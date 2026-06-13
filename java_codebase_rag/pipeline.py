"""Subprocess helpers for cocoindex + graph builder (no heavy ML imports at import time)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from java_codebase_rag.cli_format import Spinner, is_noise_line, stderr_is_tty
from java_codebase_rag.cli_progress import emit_vectors_finish, emit_vectors_start
from java_codebase_rag.config import cocoindex_subprocess_env_defaults

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
) -> tuple[str, str, int]:
    """Capture stdout/stderr; relay stderr through noise filter (or verbatim in verbose mode)."""
    out_buf = bytearray()
    err_buf = bytearray()
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
) -> subprocess.CompletedProcess[str]:
    exe = cocoindex_bin()
    if not exe.is_file():
        return subprocess.CompletedProcess(
            args=[str(exe)],
            returncode=127,
            stdout="",
            stderr=f"cocoindex not found: {exe}",
        )
    bd = bundle_dir()
    flow = bd / "java_index_flow_lancedb.py"
    if not flow.is_file():
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

    emit_progress = lance_project_root is not None
    use_spinner = emit_progress and stderr_is_tty()
    if emit_progress and not use_spinner:
        emit_vectors_start()
    spinner: Spinner | None = None
    if use_spinner:
        spinner = Spinner("[vectors] running · cocoindex update")
        spinner.start()
    t0 = time.perf_counter()
    code = -1
    out_s, err_s = "", ""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(bd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        out_s, err_s, code = _popen_capturing_stderr(proc, verbose=verbose)
    finally:
        if spinner is not None:
            spinner.stop()
        if emit_progress:
            emit_vectors_finish(elapsed_s=time.perf_counter() - t0, exit_code=code)
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
    out_s, err_s, code = _popen_capturing_stderr(proc, verbose=verbose)
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
    out_s, err_s, code = _popen_capturing_stderr(proc, verbose=verbose)
    if not verbose:
        from java_codebase_rag.cli_format import bold_cyan, styled_check, styled_cross
        marker = styled_check() if code == 0 else styled_cross()
        print(f"{marker} {bold_cyan('[increment]')} done", file=sys.stderr, flush=True)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def clip(s: str, n: int) -> str:
    return s[-n:] if len(s) > n else s
