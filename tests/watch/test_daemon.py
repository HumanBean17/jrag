"""Lifecycle + golden-IPC tests for ``WatchDaemon`` and the ``jrag watch`` verbs.

Two test populations:

1. **Lifecycle** (always run, lightweight): a stubbed daemon process exercises
   the REAL ``WatchDaemon`` shutdown discipline + the ``jrag watch``
   ``--status``/``--stop``/``--detach``/foreground verbs + SIGINT clean
   shutdown. The stub process swaps lightweight fakes in for
   ``WarmResources``/``SourceWatcher`` (sanctioned by the brief — "can stub
   WarmResources/WatchServer/SourceWatcher to avoid real model/index") so no
   real model or index is needed, while the REAL ``ProjectLock``/``WatchServer``/
   signal/shutdown/``os._exit`` path is exercised end-to-end. Because
   ``run_foreground`` terminates via ``os._exit(0)`` on the serving path, every
   test that drives a running daemon does so in a SUBPROCESS (never in the
   pytest process).

2. **Golden IPC** (heavy-gated on ``JAVA_CODEBASE_RAG_RUN_HEAVY``): builds a
   real Lance + Ladybug index, starts the REAL daemon, runs ``jrag search``
   cold (no daemon) and hot (daemon alive) in-process, and asserts the rendered
   output is BYTE-IDENTICAL. A spy on ``client._load_graph`` proves the hot path
   was actually served (cold fallback would re-load the graph), so the test does
   not degenerate into cold==cold.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import java_codebase_rag.jrag as jrag_mod
import java_codebase_rag.watch.client as client_mod
from java_codebase_rag.jrag import main as jrag_main
from java_codebase_rag.watch import paths
from java_codebase_rag.watch.client import is_daemon_alive
from java_codebase_rag.watch.lock import ProjectLock

# --- heavy gate (only the golden IPC test is heavy) ----------------------------
HEAVY = (
    os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip().lower()
    in ("1", "true", "yes")
)

_PY = sys.executable
_TESTS_DIR = Path(__file__).resolve().parent.parent
_FIXTURE_CORPUS = _TESTS_DIR / "fixtures" / "call_graph_smoke"

# A daemon shutdown (watcher.stop ≤10s join + server.shutdown ≤2s join) is well
# under this on an idle watcher; generous for CI scheduling.
_SHUTDOWN_WAIT_S = 15.0
# First-run model load dominates; 90s is generous, the cache makes repeats fast.
_REAL_READY_S = 90.0
_STUB_READY_S = 15.0


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _index_source(tmp_path: Path, tag: str = "idx") -> tuple[Path, Path]:
    """Create a fresh (index_dir, source_root) pair under ``tmp_path``.

    The index dir is created but empty — lifecycle tests never touch a real
    index (the stub daemon fakes the warm model + watcher; the real server only
    binds a socket). A unique index_dir per test yields unique socket/pid/state
    paths (keyed on the resolved index_dir hash) so tests never collide.
    """
    index_dir = tmp_path / f"{tag}.java-codebase-rag"
    index_dir.mkdir(parents=True, exist_ok=True)
    source_root = tmp_path / f"{tag}_src"
    source_root.mkdir(parents=True, exist_ok=True)
    return index_dir, source_root


def _anchor_env(monkeypatch, index_dir: Path, source_root: Path) -> None:
    """Anchor cfg resolution at (index_dir, source_root) for the test process.

    Also exports ``JRAG_WATCH_TEST_INDEX`` so spawned stub daemons resolve the
    same index without argv parsing.
    """
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("JRAG_WATCH_TEST_INDEX", str(index_dir))


def _cleanup_runtime(index_dir: Path) -> None:
    """Idempotently remove this index's socket/pid/state runtime files."""
    for p in (
        paths.socket_path(index_dir),
        paths.pid_path(index_dir),
        paths.state_path(index_dir),
    ):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _wait_alive(index_dir: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_daemon_alive(index_dir):
            return True
        time.sleep(0.1)
    return False


def _wait_dead(proc: subprocess.Popen, timeout: float = _SHUTDOWN_WAIT_S) -> int:
    """Wait for a daemon subprocess to exit (it ends via ``os._exit(0)``)."""
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


def _stop_proc(proc: subprocess.Popen, index_dir: Path) -> None:
    """Best-effort: SIGTERM a daemon subprocess and reap it + its files."""
    if proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        _wait_dead(proc)
    _cleanup_runtime(index_dir)


@pytest.fixture
def daemon_stub(tmp_path_factory) -> Path:
    """Write the stub-daemon script once and return its path.

    The stub fakes ``WarmResources`` (no model load) and ``SourceWatcher`` (no
    file watching) by monkeypatching ``daemon.WarmResources``/``SourceWatcher``
    before constructing ``WatchDaemon`` — then runs the REAL ``run_foreground``
    (real ``ProjectLock`` + real ``WatchServer`` socket bind + real signal
    handling + real ``os._exit(0)`` shutdown). This is the lifecycle faithfulness
    boundary: everything except the model and the watcher is production code.
    """
    script = tmp_path_factory.mktemp("stub") / "daemon_stub.py"
    script.write_text(_STUB_DAEMON_SCRIPT)
    return script


_STUB_DAEMON_SCRIPT = '''\
"""Stub watch daemon: fake warm model + watcher, REAL lock/server/shutdown.

Spawned by tests/watch/test_daemon.py as a subprocess so the os._exit(0)
shutdown path runs outside the pytest process. Cfg is resolved from the env
exported by the test (JRAG_WATCH_TEST_INDEX / JAVA_CODEBASE_RAG_SOURCE_ROOT).
"""
import os
import sys

from java_codebase_rag.config import resolve_operator_config
from java_codebase_rag.watch import daemon


class _FakeWarm:
    def __init__(self, cfg):
        self.cfg = cfg

    def model(self):  # eagerly-warmed by run_foreground; must not raise
        return None

    def graph(self):  # never called (no queries issued against the stub)
        return None

    def begin_graph_snapshot(self):
        pass

    def commit_graph_snapshot(self):
        pass


class _FakeWatcher:
    def __init__(self, cfg, warm, *, debounce_ms, backend, poll_interval_ms, on_event=None):
        pass

    def start(self):
        pass

    def stop(self):
        pass


daemon.WarmResources = _FakeWarm
daemon.SourceWatcher = _FakeWatcher

cfg = resolve_operator_config(source_root=None, cli_index_dir=os.environ["JRAG_WATCH_TEST_INDEX"])
cfg.apply_to_os_environ()
daemon.WatchDaemon(cfg).run_foreground()
sys.exit(0)  # pragma: no cover - run_foreground ends with os._exit(0)
'''


def _spawn_stub(daemon_stub: Path, index_dir: Path, source_root: Path,
                log_path: Path | None = None) -> subprocess.Popen:
    """Spawn the stub daemon process inheriting the anchored env."""
    env = dict(os.environ)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(index_dir)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(source_root)
    env["JRAG_WATCH_TEST_INDEX"] = str(index_dir)
    out = open(log_path, "ab") if log_path is not None else subprocess.DEVNULL
    own_fh = log_path is not None
    try:
        return subprocess.Popen(
            [_PY, str(daemon_stub)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            env=env,
            close_fds=True,
        )
    finally:
        if own_fh:
            out.close()


# ===========================================================================
# (a) foreground lock-held -> rc 2 with "in use by PID <test pid>"
# ===========================================================================


def test_foreground_lock_held_returns_2(tmp_path, monkeypatch, capsys):
    """A held project lock makes the foreground daemon refuse with rc 2.

    The refusal happens at lock.acquire() — before server.start(), so no lance
    worker threads exist and run_foreground returns 2 normally (no os._exit).
    """
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    holder = ProjectLock(index_dir)
    holder.acquire()
    try:
        rc = jrag_main(["watch", "--index-dir", str(index_dir)])
    finally:
        holder.release()
    captured = capsys.readouterr()
    assert rc == 2, f"expected rc 2 (lock held), got {rc}; stderr={captured.err!r}"
    assert f"in use by PID {os.getpid()}" in captured.err, (
        f"expected 'in use by PID {os.getpid()}' on stderr; got {captured.err!r}"
    )
    _cleanup_runtime(index_dir)


def test_foreground_releases_lock_on_refusal(tmp_path, monkeypatch):
    """After the lock-held refusal, the test's own lock is still releasable
    (the refused daemon never stole it) — and a fresh acquire succeeds, proving
    the refused daemon did not leave a half-held lock."""
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    holder = ProjectLock(index_dir)
    holder.acquire()
    jrag_main(["watch", "--index-dir", str(index_dir)])
    holder.release()
    # A fresh acquire must now succeed (lock is fully free).
    again = ProjectLock(index_dir)
    again.acquire()
    again.release()
    _cleanup_runtime(index_dir)


# ===========================================================================
# (b) --status up vs down (rc 0 vs 1)
# ===========================================================================


def test_status_down_when_no_daemon(tmp_path, monkeypatch, capsys):
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    rc = jrag_main(["watch", "--status", "--index-dir", str(index_dir)])
    captured = capsys.readouterr()
    assert rc == 1, f"expected rc 1 (down), got {rc}"
    assert "down" in captured.out
    _cleanup_runtime(index_dir)


def test_status_and_stop_skip_heavy_daemon_import(tmp_path, monkeypatch):
    """``--status`` and ``--stop`` must NOT import the heavy daemon module.

    Importing ``java_codebase_rag.watch.daemon`` eagerly pulls
    torch/sentence_transformers/lancedb/pyarrow (~2.5s + ~1GB), defeating the
    lightweight probe verbs. Order-independent: evicts the daemon module first
    (a prior foreground-verb test may have imported it in-process) and asserts
    neither probe verb re-adds it to ``sys.modules``.
    """
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    mod_key = "java_codebase_rag.watch.daemon"
    held = sys.modules.pop(mod_key, None)
    try:
        jrag_main(["watch", "--status", "--index-dir", str(index_dir)])
        assert mod_key not in sys.modules, (
            "--status imported the heavy daemon module (torch/lancedb chain)"
        )
        jrag_main(["watch", "--stop", "--index-dir", str(index_dir)])
        assert mod_key not in sys.modules, (
            "--stop imported the heavy daemon module (torch/lancedb chain)"
        )
    finally:
        if held is not None and mod_key not in sys.modules:
            sys.modules[mod_key] = held
        _cleanup_runtime(index_dir)


def test_status_up_when_daemon_running(tmp_path, monkeypatch, capsys, daemon_stub):
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    proc = _spawn_stub(daemon_stub, index_dir, source_root)
    try:
        assert _wait_alive(index_dir, _STUB_READY_S), "stub daemon did not come up"
        rc = jrag_main(["watch", "--status", "--index-dir", str(index_dir)])
        captured = capsys.readouterr()
        assert rc == 0, f"expected rc 0 (up), got {rc}"
        assert "up" in captured.out
        # read_holder reports the daemon's own pid (== proc.pid).
        assert f"pid {proc.pid}" in captured.out, (
            f"status did not report the live pid {proc.pid}: {captured.out!r}"
        )
    finally:
        _stop_proc(proc, index_dir)


# ===========================================================================
# (c) --stop: SIGTERMs a running daemon (rc 0) / "not running" (rc 1)
# ===========================================================================


def test_stop_not_running(tmp_path, monkeypatch, capsys):
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    rc = jrag_main(["watch", "--stop", "--index-dir", str(index_dir)])
    captured = capsys.readouterr()
    assert rc == 1, f"expected rc 1 (not running), got {rc}"
    assert "not running" in captured.out
    assert not paths.socket_path(index_dir).exists()
    _cleanup_runtime(index_dir)


def test_stop_running_daemon_removes_socket(tmp_path, monkeypatch, capsys, daemon_stub):
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    proc = _spawn_stub(daemon_stub, index_dir, source_root)
    try:
        assert _wait_alive(index_dir, _STUB_READY_S), "stub daemon did not come up"
        sock = paths.socket_path(index_dir)
        assert sock.exists(), "socket not bound before --stop"
        rc = jrag_main(["watch", "--stop", "--index-dir", str(index_dir)])
        captured = capsys.readouterr()
        assert rc == 0, f"expected rc 0 (stopped), got {rc}"
        assert "stopped" in captured.out
        # The socket is removed within the timeout (daemon's own shutdown).
        assert not sock.exists(), "socket still present after --stop"
        # The daemon process has exited.
        assert _wait_dead(proc) == 0
    finally:
        _stop_proc(proc, index_dir)


# ===========================================================================
# (d) --detach: returns 0 after the child is is_daemon_alive (child stubbed)
# ===========================================================================


def test_detach_spawns_and_returns(tmp_path, monkeypatch, capsys, daemon_stub):
    """``--detach`` spawns the child (stubbed via _watch_child_argv), waits until
    it is alive, prints the socket path + pid, and returns 0."""
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)

    # Swap the child command for the stub so no real model/index is needed.
    monkeypatch.setattr(
        jrag_mod, "_watch_child_argv", lambda extra: [_PY, str(daemon_stub)]
    )
    try:
        rc = jrag_main(["watch", "--detach", "--index-dir", str(index_dir)])
        captured = capsys.readouterr()
        assert rc == 0, f"expected rc 0 (detached), got {rc}; stderr={captured.err!r}"
        assert "detached" in captured.out
        assert is_daemon_alive(index_dir), "daemon not alive after --detach returned"
        # The detached child inherited JRAG_WATCH_TEST_INDEX and resolved the lock.
        holder_pid = ProjectLock.read_holder(index_dir)
        assert holder_pid is not None
    finally:
        # Tear down the detached daemon via --stop (also exercised in (c)).
        jrag_main(["watch", "--stop", "--index-dir", str(index_dir)])
        _cleanup_runtime(index_dir)


def test_detach_stale_socket_cleaned_before_bind(tmp_path, monkeypatch, capsys, daemon_stub):
    """A leftover socket from a crashed daemon is unlinked so --detach can start.

    The server's start() unlinks a stale socket only when read_holder is None; a
    stale socket with no live holder must not block a fresh detach."""
    index_dir, source_root = _index_source(tmp_path)
    _anchor_env(monkeypatch, index_dir, source_root)
    monkeypatch.setattr(
        jrag_mod, "_watch_child_argv", lambda extra: [_PY, str(daemon_stub)]
    )
    # Plant a stale socket + state with NO live holder.
    stale_sock = paths.socket_path(index_dir)
    stale_sock.parent.mkdir(parents=True, exist_ok=True)
    stale_sock.write_bytes(b"")
    try:
        rc = jrag_main(["watch", "--detach", "--index-dir", str(index_dir)])
        assert rc == 0, f"expected rc 0, got {rc}"
        assert is_daemon_alive(index_dir)
    finally:
        jrag_main(["watch", "--stop", "--index-dir", str(index_dir)])
        _cleanup_runtime(index_dir)


# ===========================================================================
# (e) SIGINT -> clean shutdown (runtime files gone, lock released)
# ===========================================================================


def test_sigint_clean_shutdown(tmp_path, daemon_stub):
    """SIGINT to a running daemon triggers a clean shutdown: watcher stopped,
    socket + pid + state removed, and the lock released (a fresh acquire
    succeeds)."""
    index_dir, source_root = _index_source(tmp_path)
    proc = _spawn_stub(daemon_stub, index_dir, source_root)
    try:
        assert _wait_alive(index_dir, _STUB_READY_S), "stub daemon did not come up"
        sock = paths.socket_path(index_dir)
        pid_file = paths.pid_path(index_dir)
        state_file = paths.state_path(index_dir)
        assert sock.exists() and pid_file.exists() and state_file.exists()

        os.kill(proc.pid, signal.SIGINT)
        assert _wait_dead(proc) == 0, "daemon did not exit after SIGINT"

        # Runtime files removed by the daemon's own shutdown.
        assert not sock.exists(), "socket not removed on shutdown"
        assert not pid_file.exists(), "pid file not removed on shutdown"
        assert not state_file.exists(), "state file not removed on shutdown"
        # Lock released: a fresh ProjectLock acquires cleanly.
        fresh = ProjectLock(index_dir)
        fresh.acquire()
        fresh.release()
    finally:
        _stop_proc(proc, index_dir)


def test_state_file_written_on_start(tmp_path, daemon_stub):
    """The state file is written at startup with the documented keys so that a
    concurrent ``--status`` from another process sees current truth."""
    index_dir, source_root = _index_source(tmp_path)
    proc = _spawn_stub(daemon_stub, index_dir, source_root)
    try:
        assert _wait_alive(index_dir, _STUB_READY_S)
        import json

        state = json.loads(paths.state_path(index_dir).read_text())
        assert state["pid"] == proc.pid
        assert state["socket"] == str(paths.socket_path(index_dir))
        assert state["started_at"] is not None
        assert state["reindex_count"] == 0
        assert state["queries_served"] == 0
    finally:
        _stop_proc(proc, index_dir)


# ===========================================================================
# (f) graph-only startup (macOS Intel): skip model load, serve lexical mode
# ===========================================================================


_GRAPH_ONLY_STUB_SCRIPT = '''\
"""Graph-only stub watch daemon: simulates macOS Intel (no vector stack).

Patches ``daemon.vector_stack_installed`` -> False, swaps in a WarmResources whose
``model()`` RAISES (proving ``run_foreground`` never calls it in lexical mode),
fakes SourceWatcher, then runs the REAL ``run_foreground``. If the gating
regresses (model() called), the AssertionError surfaces as "failed to load
embedding model" -> exit 2 -> the daemon never comes up -> the test fails fast.
"""
import os
import sys

from java_codebase_rag.config import resolve_operator_config
from java_codebase_rag.watch import daemon


class _RaisingModelWarm:
    def __init__(self, cfg):
        self.cfg = cfg

    def model(self):
        raise AssertionError("warm.model() must not be called in lexical mode")

    def graph(self):
        return None

    def begin_graph_snapshot(self):
        pass

    def commit_graph_snapshot(self):
        pass


class _FakeWatcher:
    def __init__(self, cfg, warm, *, debounce_ms, backend, poll_interval_ms, on_event=None):
        pass

    def start(self):
        pass

    def stop(self):
        pass


# Simulate a graph-only install (macOS Intel): vector stack absent.
daemon.vector_stack_installed = lambda: False
daemon.WarmResources = _RaisingModelWarm
daemon.SourceWatcher = _FakeWatcher

cfg = resolve_operator_config(source_root=None, cli_index_dir=os.environ["JRAG_WATCH_TEST_INDEX"])
cfg.apply_to_os_environ()
daemon.WatchDaemon(cfg).run_foreground()
sys.exit(0)  # pragma: no cover - run_foreground ends with os._exit(0)
'''


def test_foreground_graph_only_starts_without_vectors(tmp_path, monkeypatch):
    """Lexical-mode startup (macOS Intel): with the vector stack absent, the daemon
    must NOT call ``warm.model()`` (it would import sentence_transformers and exit 2)
    and must reach serving — the state file carries ``mode='lexical'``.

    The stub's ``WarmResources.model()`` raises AssertionError if called, so a gating
    regression surfaces as a non-alive daemon (exit 2) rather than a silent pass.
    """
    index_dir, source_root = _index_source(tmp_path, tag="goidx")
    _anchor_env(monkeypatch, index_dir, source_root)

    script = tmp_path / "graph_only_stub.py"
    script.write_text(_GRAPH_ONLY_STUB_SCRIPT)
    proc = _spawn_stub(script, index_dir, source_root)
    try:
        assert _wait_alive(index_dir, _STUB_READY_S), (
            "graph-only daemon did not come up — warm.model() was not skipped (exit 2?)"
        )
        import json

        state = json.loads(paths.state_path(index_dir).read_text())
        assert state.get("mode") == "lexical", f"expected mode='lexical', got {state.get('mode')!r}"
        assert state["pid"] == proc.pid
    finally:
        _stop_proc(proc, index_dir)


# ===========================================================================
# GOLDEN IPC TEST (heavy) — jrag search over the socket == cold path, byte for byte
# ===========================================================================


def _require_pipeline_deps() -> None:
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            f"Heavy golden test needs project deps (pip install -e .[dev]): {exc}"
        )
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})")


def _build_real_index(tmp_path: Path) -> tuple[Path, Path]:
    """Build a small real Lance + Ladybug index; return (index_dir, corpus)."""
    _require_pipeline_deps()
    assert _FIXTURE_CORPUS.is_dir(), f"fixture corpus missing: {_FIXTURE_CORPUS}"
    from java_codebase_rag.pipeline import run_build_ast_graph, run_cocoindex_update

    corpus = tmp_path / "corpus"
    shutil.copytree(_FIXTURE_CORPUS, corpus)
    index_dir = tmp_path / "index" / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
    }
    vproc = run_cocoindex_update(env, full_reprocess=False, quiet=True, verbose=False)
    assert vproc.returncode == 0, f"cocoindex vectors build failed: {vproc.stderr}"
    gproc = run_build_ast_graph(
        source_root=corpus,
        ladybug_path=index_dir / "code_graph.lbug",
        verbose=False,
        quiet=True,
        env=env,
    )
    assert gproc.returncode == 0, f"graph build failed: {gproc.stderr}"
    return index_dir, corpus


def _spawn_real_daemon(index_dir: Path, corpus: Path, log_path: Path) -> subprocess.Popen:
    """Spawn the FULL ``jrag watch`` daemon (real model + server + watcher).

    Uses ``python -m java_codebase_rag.jrag`` (not the file path) so the stdlib
    ``ast`` module is not shadowed by the project's ``java_codebase_rag.ast``
    package — see ``jrag._watch_child_argv`` for the same rationale.
    """
    env = dict(os.environ)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(index_dir.resolve())
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus.resolve())
    log_fh = open(log_path, "ab")
    try:
        return subprocess.Popen(
            [_PY, "-m", "java_codebase_rag.jrag", "watch", "--index-dir", str(index_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            env=env,
            close_fds=True,
        )
    finally:
        log_fh.close()


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_search_ipc_byte_identical(tmp_path, monkeypatch, capsys):
    """The capstone: a real ``jrag search`` over the daemon socket renders
    BYTE-IDENTICALLY to the cold path (no daemon).

    Pipeline under test: cold ``search_payload`` -> rendered envelope, vs
    daemon-served ``search_payload`` -> serialized -> reconstructed -> SAME
    rendered envelope. A spy on ``client._load_graph`` proves the hot path was
    actually served: the cold call loads the graph (counter increments); the hot
    call does NOT (counter unchanged) — so the test cannot pass by silently
    falling back to cold.
    """
    index_dir, corpus = _build_real_index(tmp_path)
    # Anchor the test process's cfg resolution at the real index.
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus))

    query = "call"  # a term that hits many code chunks in the fixture corpus

    # --- spy on the cold-path graph load to distinguish hot-served from cold-fallback ---
    load_calls = {"n": 0}
    real_load_graph = client_mod._load_graph

    def _counting_load_graph(cfg):
        load_calls["n"] += 1
        return real_load_graph(cfg)

    monkeypatch.setattr(client_mod, "_load_graph", _counting_load_graph)

    # --- COLD path: no daemon alive -> get_payload runs the cold core ---
    assert not is_daemon_alive(index_dir), "precondition: no daemon before cold run"
    rc_cold = jrag_main(["search", query, "--index-dir", str(index_dir)])
    cold_out = capsys.readouterr().out
    assert rc_cold == 0, f"cold search failed rc={rc_cold}"
    assert cold_out.strip(), "cold search produced no output"
    cold_loads = load_calls["n"]
    assert cold_loads >= 1, "cold path did not load the graph (spy misconfigured?)"

    # --- start the REAL daemon and wait until it serves ---
    log_path = tmp_path / "daemon.log"
    proc = _spawn_real_daemon(index_dir, corpus, log_path)
    try:
        came_up = _wait_alive(index_dir, _REAL_READY_S)
        if not came_up:
            tail = log_path.read_bytes()[-4000:] if log_path.exists() else b"<no log>"
            pytest.fail(f"real daemon did not come up; log tail:\n{tail!r}")

        # --- HOT path: daemon alive -> get_payload serves over the socket ---
        loads_before_hot = load_calls["n"]
        rc_hot = jrag_main(["search", query, "--index-dir", str(index_dir)])
        hot_out = capsys.readouterr().out
        assert rc_hot == 0, f"hot search failed rc={rc_hot}"
        # The hot path must NOT have loaded the graph in the test process — that
        # is the proof the daemon served (cold fallback would load it).
        assert load_calls["n"] == loads_before_hot, (
            "hot search fell back to cold (graph was loaded in the test process); "
            f"loads {loads_before_hot} -> {load_calls['n']}"
        )

        # --- THE byte-identity assertion ---
        assert hot_out == cold_out, (
            "IPC search output diverged from the cold path:\n"
            f"--- cold ({len(cold_out)} bytes) ---\n{cold_out}\n"
            f"--- hot ({len(hot_out)} bytes) ---\n{hot_out}\n"
        )
    finally:
        # Stop the real daemon (SIGTERM -> clean shutdown -> os._exit(0)).
        _stop_proc(proc, index_dir)
        # Reset the warm-graph singleton cache so it doesn't leak across tests.
        try:
            from java_codebase_rag.graph.ladybug_queries import LadybugGraph

            LadybugGraph.reset_for_path(str(index_dir / "code_graph.lbug"))
        except Exception:
            pass


# ===========================================================================
# GOLDEN IPC TESTS (heavy) — the other 5 read commands over the socket
# == cold path, byte for byte (find / inspect / callers / callees / flow)
# ===========================================================================
#
# The search golden above is the only read command that previously had an
# end-to-end byte-identity proof over the real socket. The other 5 read
# commands had only unit-level reconstruction tests (tests/watch/test_client.py)
# — no proof that a real ``jrag <cmd>`` over the socket renders byte-identically
# to cold. These 5 goldens close that gap, each mirroring the search golden's
# structure (real index, real daemon, _load_graph spy). Fixture queries come
# from the bank-chat corpus (the same queries the Task-5 goldens use) so each
# command is exercised meaningfully (callers/callees resolve real CALLS edges;
# flow resolves a real Route; inspect describes a real Service class).

_BANK_CHAT_CORPUS = _TESTS_DIR / "bank-chat-system"


def _canonical_json(s: str) -> str:
    """Parse + re-dump with sorted keys: order-insensitive value identity.

    ``inspect``'s ``edge_summary`` sub-dict key order (DECLARES/INJECTS) is a
    PRE-EXISTING non-determinism of ``describe_v2`` across processes (verified in
    tests/jrag/test_read_payloads.py: 6 runs produced DECLARES-first 3x and
    INJECTS-first 3x, identical values). Since the daemon is a SEPARATE process
    from the test process, cold-vs-hot can legitimately flip that order, so
    ``inspect`` is pinned by canonicalized value identity (same approach as
    Task-5's ``_BYTE_STABLE`` exclusion). The other 4 commands are byte-stable.
    """
    import json

    return json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False)


def _build_bank_chat_index(tmp_path: Path) -> tuple[Path, Path]:
    """Build a small real Lance + Ladybug index from the bank-chat fixture.

    The bank-chat corpus has routes, clients, services, and real CALLS/HTTP_CALLS
    edges, so every read command returns a meaningful (non-empty) result. Vectors
    are built even though only ``search`` needs them, so the daemon (which eagerly
    warms the model and may touch Lance) starts cleanly against a complete index.
    """
    _require_pipeline_deps()
    assert _BANK_CHAT_CORPUS.is_dir(), f"bank-chat corpus missing: {_BANK_CHAT_CORPUS}"
    from java_codebase_rag.pipeline import run_build_ast_graph, run_cocoindex_update

    corpus = tmp_path / "bc_corpus"
    shutil.copytree(_BANK_CHAT_CORPUS, corpus)
    index_dir = tmp_path / "bc_index" / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
    }
    vproc = run_cocoindex_update(env, full_reprocess=False, quiet=True, verbose=False)
    assert vproc.returncode == 0, f"cocoindex vectors build failed: {vproc.stderr}"
    gproc = run_build_ast_graph(
        source_root=corpus,
        ladybug_path=index_dir / "code_graph.lbug",
        verbose=False,
        quiet=True,
        env=env,
    )
    assert gproc.returncode == 0, f"graph build failed: {gproc.stderr}"
    return index_dir, corpus


def _assert_golden_read_byte_identical(
    tmp_path: Path, monkeypatch, capsys, cmd_argv: list[str], *, canonical: bool = False
) -> None:
    """Cold-vs-hot byte-identity for one read command over the real daemon socket.

    Mirrors ``test_golden_search_ipc_byte_identical``: builds a bank-chat index,
    runs the command cold (no daemon) then hot (daemon alive) in-process, and
    asserts the rendered stdout matches. A spy on ``client._load_graph`` proves the
    hot path was daemon-served (cold fallback would re-load the graph in the test
    process). ``canonical=True`` compares order-insensitive canonicalized JSON
    (used only for ``inspect`` — see ``_canonical_json``).
    """
    index_dir, corpus = _build_bank_chat_index(tmp_path)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus))

    # spy on the cold-path graph load to distinguish hot-served from cold-fallback.
    load_calls = {"n": 0}
    real_load_graph = client_mod._load_graph

    def _counting_load_graph(cfg):
        load_calls["n"] += 1
        return real_load_graph(cfg)

    monkeypatch.setattr(client_mod, "_load_graph", _counting_load_graph)

    full_argv = [*cmd_argv, "--index-dir", str(index_dir)]
    cmd_name = cmd_argv[0]

    # --- COLD path: no daemon alive -> get_payload runs the cold core ---
    assert not is_daemon_alive(index_dir), "precondition: no daemon before cold run"
    rc_cold = jrag_main(full_argv)
    cold_out = capsys.readouterr().out
    assert rc_cold == 0, f"cold {cmd_name} failed rc={rc_cold}"
    assert cold_out.strip(), f"cold {cmd_name} produced no output"
    cold_loads = load_calls["n"]
    assert cold_loads >= 1, "cold path did not load the graph (spy misconfigured?)"

    # --- start the REAL daemon and wait until it serves ---
    log_path = tmp_path / "daemon.log"
    proc = _spawn_real_daemon(index_dir, corpus, log_path)
    try:
        came_up = _wait_alive(index_dir, _REAL_READY_S)
        if not came_up:
            tail = log_path.read_bytes()[-4000:] if log_path.exists() else b"<no log>"
            pytest.fail(f"real daemon did not come up for {cmd_name}; log tail:\n{tail!r}")

        # --- HOT path: daemon alive -> get_payload serves over the socket ---
        loads_before_hot = load_calls["n"]
        rc_hot = jrag_main(full_argv)
        hot_out = capsys.readouterr().out
        assert rc_hot == 0, f"hot {cmd_name} failed rc={rc_hot}"
        # The hot path must NOT have loaded the graph in the test process — that
        # is the proof the daemon served (cold fallback would load it).
        assert load_calls["n"] == loads_before_hot, (
            f"hot {cmd_name} fell back to cold (graph was loaded in the test process); "
            f"loads {loads_before_hot} -> {load_calls['n']}"
        )

        # --- THE byte-identity assertion ---
        if canonical:
            assert _canonical_json(hot_out) == _canonical_json(cold_out), (
                f"{cmd_name}: canonicalized JSON diverged from the cold path "
                f"(values changed, not just key order):\n"
                f"--- cold ({len(cold_out)} bytes) ---\n{_canonical_json(cold_out)}\n"
                f"--- hot ({len(hot_out)} bytes) ---\n{_canonical_json(hot_out)}\n"
            )
        else:
            assert hot_out == cold_out, (
                f"{cmd_name}: IPC output diverged from the cold path:\n"
                f"--- cold ({len(cold_out)} bytes) ---\n{cold_out}\n"
                f"--- hot ({len(hot_out)} bytes) ---\n{hot_out}\n"
            )
    finally:
        _stop_proc(proc, index_dir)
        try:
            from java_codebase_rag.graph.ladybug_queries import LadybugGraph

            LadybugGraph.reset_for_path(str(index_dir / "code_graph.lbug"))
        except Exception:
            pass


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_find_ipc_byte_identical(tmp_path, monkeypatch, capsys):
    """Real ``jrag find`` over the daemon socket == cold path, byte for byte.

    Query ``find ChatManagementService`` resolves real Symbol nodes (the class +
    its constructor) in the bank-chat corpus — a meaningful name/FQN lookup, not
    an empty result.
    """
    _assert_golden_read_byte_identical(
        tmp_path, monkeypatch, capsys, ["find", "ChatManagementService"]
    )


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_inspect_ipc_canonical_identical(tmp_path, monkeypatch, capsys):
    """Real ``jrag inspect`` over the daemon socket == cold path (canonicalized).

    Query ``inspect com.bank.chat.assign.service.ChatManagementService`` describes
    a real Service class (annotations, edge_summary, file location). Uses
    ``--format json`` so the output is parseable, and canonicalized (sorted-key)
    comparison — NOT raw byte — because ``edge_summary`` key order (DECLARES vs
    INJECTS first) is a pre-existing cross-process non-determinism of
    ``describe_v2`` (see ``_canonical_json``); the cold run (test process) and the
    hot run (daemon subprocess) can legitimately flip that order with identical
    values. The other 4 read goldens use the default human-readable format and are
    byte-stable, so the default render path is covered there.
    """
    _assert_golden_read_byte_identical(
        tmp_path, monkeypatch, capsys,
        ["inspect", "com.bank.chat.assign.service.ChatManagementService", "--format", "json"],
        canonical=True,
    )


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_callers_ipc_byte_identical(tmp_path, monkeypatch, capsys):
    """Real ``jrag callers`` over the daemon socket == cold path, byte for byte.

    Query ``callers …ChatManagementService#assign(AssignmentRequest)`` traverses
    real inbound CALLS edges (the controller method that calls ``assign``) — a
    meaningful call-graph traversal, not an empty result.
    """
    _assert_golden_read_byte_identical(
        tmp_path, monkeypatch, capsys,
        ["callers", "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"],
    )


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_callees_ipc_byte_identical(tmp_path, monkeypatch, capsys):
    """Real ``jrag callees`` over the daemon socket == cold path, byte for byte.

    Query ``callees …ChatManagementService#assign(AssignmentRequest)`` traverses
    real outbound CALLS edges (publishers, resolvers, entity setters, repository
    lookups) — a meaningful call-graph traversal that exercises truncation.
    """
    _assert_golden_read_byte_identical(
        tmp_path, monkeypatch, capsys,
        ["callees", "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"],
    )


@pytest.mark.skipif(not HEAVY, reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the golden IPC test")
def test_golden_flow_ipc_byte_identical(tmp_path, monkeypatch, capsys):
    """Real ``jrag flow`` over the daemon socket == cold path, byte for byte.

    Query ``flow /chat/joinOperator --service chat-core`` resolves a real Route
    and traces its request flow (inbound Feign callers + outbound CALLS hops) —
    a meaningful route-flow traversal, not an empty result.
    """
    _assert_golden_read_byte_identical(
        tmp_path, monkeypatch, capsys,
        ["flow", "/chat/joinOperator", "--service", "chat-core"],
    )
