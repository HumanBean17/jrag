"""Subprocess helpers for cocoindex + graph builder (no heavy ML imports at import time)."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from java_codebase_rag.cli_format import is_noise_line
from java_codebase_rag.config import cocoindex_subprocess_env_defaults
from java_codebase_rag.progress import ProgressEvent, ProgressRelay, make_relay

COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"


def bundle_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def cocoindex_bin() -> Path:
    candidate = Path(sys.executable).parent / "cocoindex"
    if candidate.is_file():
        return candidate
    found = shutil.which("cocoindex")
    if found:
        return Path(found)
    return candidate


class _LineFilter:
    """Buffer byte chunks and relay only non-noise lines to stderr."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._suppress_next = False

    def feed(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line += b"\n"
            noise = is_noise_line(line)
            if noise:
                self._suppress_next = True
                continue
            if self._suppress_next and line[:1] in (b" ", b"\t"):
                continue
            self._suppress_next = False
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    def flush(self) -> None:
        if self._buf:
            if not is_noise_line(self._buf):
                sys.stderr.buffer.write(bytes(self._buf))
                sys.stderr.buffer.flush()
            self._buf.clear()
        self._suppress_next = False


def _popen_capturing_stderr(
    proc: subprocess.Popen[bytes],
    *,
    verbose: bool = True,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> tuple[str, str, int]:
    """Capture stdout/stderr; relay stderr through noise filter (or verbatim in verbose mode).

    When ``on_progress`` is set, stderr is drained through a :class:`ProgressRelay`
    instead of the bare ``_LineFilter``: progress lines are parsed first, routed to
    ``on_progress``, and suppressed from the relay; non-progress lines follow the
    relay's routing (``console.print`` while a Live region is up via
    ``on_progress_console``, raw ``buffer.write`` in verbose mode).
    """
    out_buf = bytearray()
    err_buf = bytearray()
    if on_progress is not None:
        relay = make_relay(
            on_progress, console=on_progress_console, verbose=verbose
        )
        filt: _LineFilter | ProgressRelay | None = relay
    else:
        filt = _LineFilter() if not verbose else None

    def drain_out() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            out_buf.extend(chunk)

    def drain_err() -> None:
        assert proc.stderr is not None
        while True:
            chunk = proc.stderr.read(65536)
            if not chunk:
                break
            err_buf.extend(chunk)
            if filt is not None:
                filt.feed(chunk)
            else:
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()

    t_out = threading.Thread(target=drain_out, name="stream-stdout", daemon=True)
    t_err = threading.Thread(target=drain_err, name="stream-stderr", daemon=True)
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()
    if filt is not None:
        filt.flush()
    code = proc.wait()
    return out_buf.decode(errors="replace"), err_buf.decode(errors="replace"), code


def run_cocoindex_update(
    env: dict[str, str],
    *,
    full_reprocess: bool,
    quiet: bool,
    verbose: bool = True,
    lance_project_root: Path | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run_cocoindex_update_impl(
        env,
        full_reprocess=full_reprocess,
        quiet=quiet,
        verbose=verbose,
        lance_project_root=lance_project_root,
        on_progress=on_progress,
        on_progress_console=on_progress_console,
    )
    # After cocoindex returns exit 0 there are no concurrent writers, so this
    # is the safe window to compact the Lance tables. The flow disabled its
    # in-flight background optimize (see java_index_flow_lancedb.py), making
    # this serialized pass the sole optimizer. Optimize failure does not flip
    # the cocoindex CompletedProcess (a successful index is still usable, just
    # not compacted); the outcome is logged to stderr only. Thread the
    # in-process on_progress so the optimize phase renders via the same
    # renderer (the flow cannot emit it — it runs in the child).
    if result.returncode == 0:
        _maybe_run_serialized_optimize(env, quiet=quiet, on_progress=on_progress)
    return result


def _maybe_run_serialized_optimize(
    env: dict[str, str], *, quiet: bool, on_progress: Callable | None = None
) -> None:
    """Resolve the index dir from *env* and run the serialized Lance optimize.

    The flow's lifespan reads ``JAVA_CODEBASE_RAG_INDEX_DIR`` (set by the CLI /
    config.subprocess_env), so it is guaranteed present when cocoindex ran.
    If it is somehow absent we skip optimize with a stderr warning rather than
    crash — a successful index is still searchable un-compacted.
    """
    idx_raw = env.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if not idx_raw:
        print(
            "java-codebase-rag: optimize skipped — JAVA_CODEBASE_RAG_INDEX_DIR "
            "not set in subprocess env",
            file=sys.stderr,
        )
        return
    try:
        from java_codebase_rag.lance_optimize import optimize_lance_tables

        asyncio.run(optimize_lance_tables(Path(idx_raw), quiet=quiet, on_progress=on_progress))
    except Exception as exc:
        # Never crash the CLI on an optimize failure — surface on stderr only.
        print(f"java-codebase-rag: optimize failed: {exc}", file=sys.stderr)


def _run_cocoindex_update_impl(
    env: dict[str, str],
    *,
    full_reprocess: bool,
    quiet: bool,
    verbose: bool = True,
    lance_project_root: Path | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> subprocess.CompletedProcess[str]:
    exe = cocoindex_bin()
    if not exe.is_file():
        # 127 pre-spawn stub: never mark the vectors task running — emit a
        # terminal failed event so the renderer doesn't leave it hung.
        if on_progress is not None:
            on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
        return subprocess.CompletedProcess(
            args=[str(exe)],
            returncode=127,
            stdout="",
            stderr=f"cocoindex not found: {exe}",
        )
    bd = bundle_dir()
    flow = bd / "java_index_flow_lancedb.py"
    if not flow.is_file():
        if on_progress is not None:
            on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr=f"java_index_flow_lancedb.py not found under {bd}",
        )
    # Cap CocoIndex concurrency to avoid EMFILE ("too many open files") under
    # default OS fd limits. See: https://github.com/HumanBean17/java-codebase-rag/issues/306
    env = env.copy()
    for _k, _v in cocoindex_subprocess_env_defaults().items():
        env.setdefault(_k, _v)
    cmd: list[str] = [str(exe), "update", COCOINDEX_TARGET]
    if full_reprocess:
        cmd.extend(["--full-reprocess", "-f"])
    else:
        cmd.append("-f")
    if quiet:
        cmd.append("-q")
        return subprocess.run(
            cmd,
            cwd=str(bd),
            env=env,
            capture_output=True,
            text=True,
        )

    t0 = time.perf_counter()
    code = -1
    out_s, err_s = "", ""
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(bd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        # Vectors task is marked running only AFTER Popen succeeds — the flow's
        # per-file ticks + approximate total stream in from the child via the
        # relay (parsed by ProgressRelay, routed to on_progress).
        out_s, err_s, code = _popen_capturing_stderr(
            proc, verbose=verbose, on_progress=on_progress, on_progress_console=on_progress_console
        )
    finally:
        # The flow cannot emit the terminal vectors event (no "all files done"
        # hook in cocoindex flows), so the PARENT emits it here based on the
        # cocoindex exit code. This drives clamp-on-completion + the phase
        # transition to Optimize. Emitted even on a spawn failure (code stays
        # -1 → failed) so the renderer's task never hangs at running.
        if on_progress is not None:
            elapsed = time.perf_counter() - t0
            status = "done" if code == 0 else "failed"
            on_progress(
                ProgressEvent(
                    kind="vectors", phase=None, pass_=None, done=None, total=None,
                    status=status, elapsed_s=elapsed,
                )
            )
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def run_cocoindex_drop(env: dict[str, str], *, quiet: bool) -> subprocess.CompletedProcess[str]:
    exe = cocoindex_bin()
    if not exe.is_file():
        return subprocess.CompletedProcess(
            args=[str(exe)],
            returncode=127,
            stdout="",
            stderr=f"cocoindex not found: {exe}",
        )
    bd = bundle_dir()
    cmd = [str(exe), "drop", COCOINDEX_TARGET, "-f"]
    if quiet:
        cmd.append("-q")
    return subprocess.run(
        cmd,
        cwd=str(bd),
        env=env,
        capture_output=True,
        text=True,
    )


def run_build_ast_graph(
    *,
    source_root: Path,
    ladybug_path: Path,
    verbose: bool,
    quiet: bool = False,
    env: dict[str, str] | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> subprocess.CompletedProcess[str]:
    builder = bundle_dir() / "build_ast_graph.py"
    if not builder.is_file():
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr=f"build_ast_graph.py not found under {builder.parent}",
        )
    cmd: list[str] = [
        sys.executable,
        str(builder),
        "--source-root",
        str(source_root),
        "--ladybug-path",
        str(ladybug_path),
    ]
    # Three-tier: --quiet (silent) / default (filtered progress) / --verbose (raw).
    # Default passes --verbose so the builder emits per-pass progress lines,
    # which the parent filters via _LineFilter.  --verbose bypasses the filter.
    if verbose or not quiet:
        cmd.append("--verbose")
    if quiet:
        return subprocess.run(
            cmd,
            cwd=str(source_root),
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
        )
    proc = subprocess.Popen(
        cmd,
        cwd=str(source_root),
        env=env or os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    out_s, err_s, code = _popen_capturing_stderr(
        proc, verbose=verbose, on_progress=on_progress, on_progress_console=on_progress_console
    )
    if not verbose:
        from java_codebase_rag.cli_format import bold_cyan, styled_check, styled_cross
        marker = styled_check() if code == 0 else styled_cross()
        print(f"{marker} {bold_cyan('[graph]')} done", file=sys.stderr, flush=True)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def run_incremental_graph(
    *,
    source_root: Path,
    ladybug_path: Path,
    verbose: bool,
    quiet: bool = False,
    env: dict[str, str] | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run incremental graph rebuild by passing --incremental flag to build_ast_graph.py."""
    builder = bundle_dir() / "build_ast_graph.py"
    if not builder.is_file():
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr=f"build_ast_graph.py not found under {builder.parent}",
        )
    cmd: list[str] = [
        sys.executable,
        str(builder),
        "--source-root",
        str(source_root),
        "--ladybug-path",
        str(ladybug_path),
        "--incremental",
    ]
    # Three-tier: --quiet (silent) / default (filtered progress) / --verbose (raw).
    # Default passes --verbose so the builder emits per-pass progress lines,
    # which the parent filters via _LineFilter.  --verbose bypasses the filter.
    if verbose or not quiet:
        cmd.append("--verbose")
    if quiet:
        return subprocess.run(
            cmd,
            cwd=str(source_root),
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
        )
    proc = subprocess.Popen(
        cmd,
        cwd=str(source_root),
        env=env or os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    out_s, err_s, code = _popen_capturing_stderr(
        proc, verbose=verbose, on_progress=on_progress, on_progress_console=on_progress_console
    )
    if not verbose:
        from java_codebase_rag.cli_format import bold_cyan, styled_check, styled_cross
        marker = styled_check() if code == 0 else styled_cross()
        print(f"{marker} {bold_cyan('[increment]')} done", file=sys.stderr, flush=True)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def clip(s: str, n: int) -> str:
    return s[-n:] if len(s) > n else s
