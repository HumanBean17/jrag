"""Subprocess helpers for cocoindex + graph builder (no heavy ML imports at import time)."""
from __future__ import annotations

import asyncio
import importlib.util
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

# Operator-facing line printed when an indexing command skips the vectors phase
# because the vector stack is absent (graph-only install, e.g. macOS Intel where
# cocoindex[lancedb]/lancedb/sentence-transformers are gated off by PEP 508 markers).
# Single source of truth — imported by cli.py (init/increment) and server.py
# (reprocess) so the wording can't drift between the two paths.
VECTORS_SKIPPED_GRAPH_ONLY = (
    "jrag: vectors skipped — vector stack not installed on this platform "
    "(graph-only mode). The graph is built/refreshed; semantic search is unavailable."
)


# Package-internal locations of the cocoindex flow definition and the graph
# builder. Both are executed *by file path* — cocoindex loads the flow via a
# ``file:Class`` target (COCOINDEX_TARGET below) and the builder runs as
# ``python <path>`` — so we must resolve their paths without importing them
# (this module deliberately stays free of heavy ML imports at import time).
# Derived from this file's location so they resolve under both editable and
# wheel installs.
_PKG_DIR = Path(__file__).resolve().parent
_FLOW_DIR = _PKG_DIR / "index"
_FLOW_FILE = _FLOW_DIR / "java_index_flow_lancedb.py"
_BUILDER_FILE = _PKG_DIR / "graph" / "build_ast_graph.py"


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
    # Wait on the CHILD before joining the drain threads. ``Popen.wait()`` is
    # interruptible by Ctrl+C — the underlying ``os.waitpid`` returns EINTR and
    # CPython raises ``KeyboardInterrupt`` — whereas ``Thread.join()`` blocks on
    # an internal lock whose infinite-timeout acquire CPython never polls for
    # signals. Joining *first* therefore made the whole indexing step ignore
    # Ctrl+C until the child happened to close its pipes; cocoindex's teardown
    # (and any flow-server grandchild it spawned) can hold them open for a long
    # time, so an install/reprocess could not be aborted. The daemon drain
    # threads keep the child's stdout/stderr pipes empty while we wait, so the
    # child never blocks on a full pipe.
    try:
        code = proc.wait()
    except BaseException:
        # Ctrl+C or any other abort: best-effort, NON-BLOCKING child teardown
        # so a process we spawned does not outlive us, then re-raise WITHOUT
        # joining the drain threads. They may be blocked on a pipe the child
        # still owns, and a join here would re-introduce the very hang this
        # reordering fixes. The threads are daemons, so they vanish at exit.
        _abort_child(proc)
        raise
    # Normal exit: the child is gone, its pipes hit EOF, and the drain threads
    # return promptly — safe to join here. (If a pipe-inheriting grandchild
    # lingered past the child's exit, these joins would block until it too
    # closed its write ends — on Ctrl+C the shared-process-group SIGINT reaches
    # it and closes them. This window is the short teardown-after-indexing phase,
    # not the long indexing phase the wait-first reorder already made
    # interruptible.)
    t_out.join()
    t_err.join()
    if filt is not None:
        filt.flush()
    return out_buf.decode(errors="replace"), err_buf.decode(errors="replace"), code


def _abort_child(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort, NON-BLOCKING teardown of a spawned child on an abort path.

    Runs when ``proc.wait()`` raised (Ctrl+C, or any other exception). Sends
    SIGTERM and returns immediately — this path exists to let the operator exit
    *promptly*, and waiting for the child would defeat that. On Ctrl+C the child
    already received SIGINT (same process group); this just guarantees teardown
    for non-signal aborts, after which the child finishes shutting down on its
    own. ``OSError`` (incl. ``ProcessLookupError`` — already-dead / zombie /
    reaped) is swallowed — the only caller re-raises regardless.
    """
    fn = getattr(proc, "terminate", None)
    if fn is None:
        return
    try:
        fn()
    except OSError:
        pass


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
    if full_reprocess:
        # A full reprocess rebuilds every row, so DROP the Lance target tables
        # first and let cocoindex recreate them via the fast INSERT path. The
        # in-place alternative (cocoindex's bulk-update merge_insert) emits
        # ~one deletion-vector + version commit PER matched row — O(rows) of
        # tiny file IO that scales to multi-minute hangs on large repos
        # (measured ~83s sys time / 3474 deletion files for 3475 chunks on
        # Shopizer; drop+recreate is ~3.6s sys / 0 deletions, ~3.7x faster and
        # hang-free). Output is identical either way (full recompute); only the
        # write path differs. Drop failure is non-fatal — if it somehow fails,
        # the update falls back to the slow in-place path. The same fix is
        # applied on the async server path (``server.run_refresh_pipeline``).
        drop = run_cocoindex_drop(env, quiet=quiet)
        if drop.returncode != 0 and not is_cocoindex_preflight_blocker(drop):
            print(
                "jrag: drop-before-reprocess failed "
                f"(exit {drop.returncode}); falling back to in-place update: "
                f"{(drop.stderr or '').strip()[:200]}",
                file=sys.stderr,
            )
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
            "jrag: optimize skipped — JAVA_CODEBASE_RAG_INDEX_DIR "
            "not set in subprocess env",
            file=sys.stderr,
        )
        return
    try:
        from java_codebase_rag.lance_optimize import optimize_lance_tables

        asyncio.run(optimize_lance_tables(Path(idx_raw), quiet=quiet, on_progress=on_progress))
    except Exception as exc:
        # Never crash the CLI on an optimize failure — surface on stderr only.
        print(f"jrag: optimize failed: {exc}", file=sys.stderr)


def is_cocoindex_preflight_blocker(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when ``run_cocoindex_update`` returned a pre-spawn stub, not a real cocoindex run.

    The stubs are emitted by ``_run_cocoindex_update_impl`` just below:
      * returncode 127, args=[exe] -> cocoindex binary not installed (graph-only install,
        e.g. macOS Intel where the vector extra is gated off).
      * returncode 126, args=[]     -> ``java_index_flow_lancedb.py`` missing from the bundle.
    A real cocoindex run has ``args`` = the full command list (length > 1), so the
    ``len(args) <= 1`` guard distinguishes a stub from a genuine non-zero exit. This is the
    single authoritative detector — ``cli.py`` and ``installer.py`` both call it so the
    "treat as skip, not failure" decision stays aligned with the stub shapes here.
    """
    return bool(proc.returncode in (126, 127) and len(getattr(proc, "args", ()) or ()) <= 1)


def is_graph_preflight_blocker(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when ``run_build_ast_graph`` returned a pre-spawn stub (builder missing)."""
    return bool(proc.returncode in (126, 127) and len(getattr(proc, "args", ()) or ()) <= 1)


def vector_stack_installed() -> bool:
    """True when the optional vector stack (cocoindex/lancedb/sentence-transformers) is importable.

    False on graph-only installs (macOS Intel), where PEP 508 markers exclude the trio.
    Used to skip vector-only wizard steps (e.g. embedding-model selection) and to preflight
    branching without spawning cocoindex. Probes all three since they are gated together.
    """
    return all(
        importlib.util.find_spec(m) is not None
        for m in ("cocoindex", "lancedb", "sentence_transformers")
    )


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
    bd = _FLOW_DIR
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
    bd = _FLOW_DIR
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
    builder = _BUILDER_FILE
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
    t0 = time.perf_counter()
    code = -1
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(source_root),
            env=env or os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        out_s, err_s, code = _popen_capturing_stderr(
            proc,
            verbose=verbose,
            on_progress=on_progress,
            on_progress_console=on_progress_console,
        )
    finally:
        # On SUCCESS the child (build_ast_graph._graph_pass_progress) already
        # emitted its own terminal ``kind=graph pass=6/6 status=done`` line in
        # its finally, which the relay routes to on_progress — so the renderer
        # and programmatic consumers have already seen the one terminal graph
        # event. Emitting a second here would violate the "one terminal event
        # per kind" invariant (duplicate non-TTY line, two events for MCP). The
        # parent therefore emits ONLY on the failure/interrupt path (code != 0,
        # incl. spawn failure where code stays -1), where the child did NOT
        # reach its finally with a healthy exit.
        if on_progress is not None and code != 0:
            on_progress(
                ProgressEvent(
                    kind="graph",
                    phase=None,
                    pass_=None,
                    done=None,
                    total=None,
                    status="failed",
                    elapsed_s=time.perf_counter() - t0,
                )
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
    builder = _BUILDER_FILE
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
    t0 = time.perf_counter()
    code = -1
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(source_root),
            env=env or os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        out_s, err_s, code = _popen_capturing_stderr(
            proc,
            verbose=verbose,
            on_progress=on_progress,
            on_progress_console=on_progress_console,
        )
    finally:
        # See run_build_ast_graph: on success the child already emitted its own
        # terminal graph event (pass=6/6 status=done); the parent emits ONLY on
        # the failure/interrupt path to keep exactly one terminal event per kind.
        if on_progress is not None and code != 0:
            on_progress(
                ProgressEvent(
                    kind="graph",
                    phase=None,
                    pass_=None,
                    done=None,
                    total=None,
                    status="failed",
                    elapsed_s=time.perf_counter() - t0,
                )
            )
    if not verbose:
        from java_codebase_rag.cli_format import bold_cyan, styled_check, styled_cross
        marker = styled_check() if code == 0 else styled_cross()
        print(f"{marker} {bold_cyan('[increment]')} done", file=sys.stderr, flush=True)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def clip(s: str, n: int) -> str:
    return s[-n:] if len(s) > n else s
