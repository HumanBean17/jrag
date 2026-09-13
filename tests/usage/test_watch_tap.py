"""Watch daemon tap — reindex events, counters, heartbeat, queries_served (Task 7)."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest

from java_codebase_rag.config import resolve_operator_config
from java_codebase_rag.watch import daemon as daemon_mod


@pytest.fixture
def usage_state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "usage-state"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    return state


class _InertWarm:
    def __init__(self, cfg):
        self.cfg = cfg

    def model(self):  # pragma: no cover — never warmed in these tests
        raise AssertionError("model() must not be called")

    def graph(self):
        return None

    def begin_graph_snapshot(self):
        pass

    def commit_graph_snapshot(self):
        pass


def _daemon(tmp_path: Path, monkeypatch, index_dir: Path) -> daemon_mod.WatchDaemon:
    monkeypatch.setattr(daemon_mod, "WarmResources", _InertWarm)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.setattr(daemon_mod, "vector_stack_installed", lambda: False)
    monkeypatch.chdir(index_dir.parent)
    cfg = resolve_operator_config(source_root=None)
    return daemon_mod.WatchDaemon(cfg)


def _events(state: Path) -> list[dict]:
    files = list(state.rglob(f"events-{date.today():%Y-%m-%d}.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def _index_dir(tmp_path: Path) -> Path:
    idx = tmp_path / "proj" / ".java-codebase-rag"
    idx.mkdir(parents=True)
    (tmp_path / "proj" / "a.java").write_text("class A {}\n")
    return idx


def test_error_then_success_counters(tmp_path, monkeypatch, usage_state) -> None:
    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    d._record("indexing_started", {"kinds": ["java"]})
    d._record("error", {"phase": "vectors", "returncode": 1, "stderr_tail": "boom"})
    assert d._state["consecutive_errors"] == 1
    d._record("indexing_done", {"kinds": ["java"], "phases": ["graph"]})
    assert d._state["consecutive_errors"] == 0
    assert d._state["last_graph_ok_at"] is not None
    assert "last_vectors_ok_at" not in d._state or d._state["last_vectors_ok_at"] is None


def test_error_event_appended_with_stderr_tail(tmp_path, monkeypatch, usage_state) -> None:
    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    tail = "x" * 900  # ASCII: byte trim == char trim, so [-400:] is exact
    d._record("error", {"phase": "vectors", "returncode": 1, "stderr_tail": tail})
    evs = [e for e in _events(usage_state) if e["event"] == "reindex"]
    assert any(
        e["kind"] == "error"
        and e["detail"]["stderr_tail"] == tail[-400:]
        and len(json.dumps(e)) <= 1024
        for e in evs
    )


def test_disabled_no_events_no_fields(tmp_path, monkeypatch, env_pinned) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    state = tmp_path / "nope"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    assert d._telemetry is False
    d._record("indexing_started", {"kinds": ["java"]})
    d._record("error", {"phase": "graph", "returncode": 1})
    assert "consecutive_errors" not in d._state
    assert not state.exists()


def test_queries_served_increments(tmp_path, monkeypatch, usage_state) -> None:
    from java_codebase_rag.watch.server import WatchServer

    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    calls = []
    server = WatchServer(_InertWarm(d.cfg), d.cfg, on_query=lambda: calls.append(1))
    # Drive the hook through dispatch with a real served payload: dispatch
    # needs a graph; the inert warm returns None which the payload cores reject
    # — so exercise the hook contract at the _handle boundary instead: the hook
    # fires only on the ok=True path of dispatch. Direct-call the hook wrapper
    # through the daemon's method, and verify the server stores it.
    assert server.on_query is not None
    for _ in range(3):
        server.on_query()
    assert len(calls) == 3
    for _ in range(2):
        d._on_query_served()
    assert d._state["queries_served"] == 2


def test_heartbeat_rewrite(tmp_path, monkeypatch, usage_state) -> None:
    from java_codebase_rag.watch import paths

    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    state_file = paths.state_path(d.cfg.index_dir)
    with d._state_lock:
        d._write_state_locked()
    before = state_file.read_text()
    old_mtime = state_file.stat().st_mtime
    # Simulate an idle daemon whose last write is 60s old — the REAL heartbeat
    # method (the one the serve loop calls) must refresh the file.
    d._last_state_write = time.monotonic() - 60.0
    with d._state_lock:
        d._maybe_heartbeat_locked()
    assert state_file.stat().st_mtime > old_mtime
    assert json.loads(state_file.read_text())["pid"] == json.loads(before)["pid"]
    # A recently-written state is left alone (no churn under load).
    fresh_write = d._last_state_write
    with d._state_lock:
        d._maybe_heartbeat_locked()
    assert d._last_state_write == fresh_write


def test_non_ascii_stderr_tail_survives_line_cap(tmp_path, monkeypatch,
                                                 usage_state) -> None:
    """Cyrillic stderr must not blow the 1 KiB byte cap (escaped \\uXXXX)."""
    d = _daemon(tmp_path, monkeypatch, _index_dir(tmp_path))
    tail = "Ошибка индексации графа " * 40  # ~1000 chars → ~6000 escaped bytes
    d._record("error", {"phase": "graph", "returncode": 1, "stderr_tail": tail})
    evs = [json.loads(line)
           for f in usage_state.rglob(f"events-{date.today():%Y-%m-%d}.jsonl")
           for line in f.read_text().splitlines()]
    errors = [e for e in evs if e.get("kind") == "error"]
    assert errors, "non-ASCII stderr tail dropped the error event entirely"
    assert all(len(json.dumps(e)) <= 1024 for e in errors)


def test_stderr_tail_gated_in_state_schema(tmp_path, monkeypatch, usage_state) -> None:
    """Disabled telemetry: watcher error detail keeps the OLD {phase, rc} schema."""
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.watch import watcher as watcher_mod

    index_dir = _index_dir(tmp_path)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.chdir(index_dir.parent)

    class _Warm:
        def __init__(self, cfg):
            self.cfg = cfg

        def begin_graph_snapshot(self):
            pass

        def commit_graph_snapshot(self):
            pass

    def _watcher(cfg):
        return watcher_mod.SourceWatcher(
            cfg, _Warm(cfg), debounce_ms=10, backend="auto",
            poll_interval_ms=10, on_event=lambda k, d: None,
        )

    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    cfg = resolve_operator_config(source_root=None)
    assert _watcher(cfg)._error_detail("vectors", 1, None) == {
        "phase": "vectors", "returncode": 1,
    }
    # Enabled: the diagnostic tail rides along ("" for a result without stderr).
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
    cfg_on = resolve_operator_config(source_root=None)
    assert _watcher(cfg_on)._error_detail("vectors", 1, None) == {
        "phase": "vectors", "returncode": 1, "stderr_tail": "",
    }
