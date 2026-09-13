"""Health surfacing — watch --status last_error, status daemon node, prime note (Task 11)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from java_codebase_rag import jrag

pytestmark = pytest.mark.usefixtures("env_pinned")


@pytest.fixture
def cfg_for_fixture_index(env_pinned):
    from java_codebase_rag.config import resolve_operator_config

    return resolve_operator_config(source_root=None)


def _write_state(cfg, **state) -> None:
    from java_codebase_rag.watch.paths import state_path

    p = state_path(cfg.index_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    base = {"pid": None, "socket": "x", "reindex_count": 1,
            "last_reindex_at": time.time()}
    base.update(state)
    p.write_text(json.dumps(base))
    return p


def _cleanup_state(cfg) -> None:
    from java_codebase_rag.watch.paths import state_path

    try:
        state_path(cfg.index_dir).unlink()
    except FileNotFoundError:
        pass


def test_watch_status_shows_last_error_ungated(cfg_for_fixture_index, capsys) -> None:
    cfg = cfg_for_fixture_index
    _write_state(cfg, last_error={
        "phase": "vectors", "at": time.time() - 120,
        "detail": {"phase": "vectors", "returncode": 1, "stderr_tail": "boom"},
    })
    try:
        # Daemon is not alive in tests → down path; last_error renders only on
        # the up path, so assert via the alive branch by checking the down rc
        # and instead drive the render directly for the up case.
        from java_codebase_rag.watch.client import is_daemon_alive

        if is_daemon_alive(cfg.index_dir):
            pytest.skip("unexpected live daemon")
        # Simulate the alive branch of _cmd_watch_status by calling it with a
        # monkeypatched liveness probe.
        import java_codebase_rag.jrag as jrag_mod
        import java_codebase_rag.watch.client as client_mod

        real = client_mod.is_daemon_alive
        client_mod.is_daemon_alive = lambda _idx: True
        try:
            rc = jrag_mod._cmd_watch_status(cfg)
        finally:
            client_mod.is_daemon_alive = real
        assert rc == 0
        out = capsys.readouterr().out
        assert "last error" in out
        assert "vectors" in out
    finally:
        _cleanup_state(cfg)


def test_status_daemon_health_gated(tmp_path: Path, monkeypatch,
                                    cfg_for_fixture_index, capsys) -> None:
    cfg = cfg_for_fixture_index
    state_file = _write_state(cfg, consecutive_errors=5)
    try:
        # Telemetry ON: daemon node + warning (daemon not alive in tests).
        monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
        monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(tmp_path / "s"))
        rc = jrag.main(["status", "--format", "json"])
        assert rc == 0
        capsys.readouterr()
        # The status envelope renders via _emit inside main; re-run and parse.
        rc = jrag.main(["status", "--format", "json"])
        out = json.loads(capsys.readouterr().out)
        daemon = out["nodes"]["index"]["daemon"]
        assert daemon["running"] is False
        assert daemon["consecutive_errors"] == 5
        assert out.get("warnings")

        # Telemetry OFF: node absent, no warnings.
        monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED")
        rc = jrag.main(["status", "--format", "json"])
        out = json.loads(capsys.readouterr().out)
        assert "daemon" not in out["nodes"]["index"]
        assert not out.get("warnings")
    finally:
        _cleanup_state(cfg)


def test_prime_enrichment_gated(tmp_path: Path, monkeypatch,
                                cfg_for_fixture_index) -> None:
    cfg = cfg_for_fixture_index
    from java_codebase_rag import prime as prime_mod

    graph = jrag._load_graph(cfg)
    meta = graph.meta()
    _write_state(cfg, consecutive_errors=2, last_error={
        "phase": "graph", "at": time.time() - 300, "detail": {},
    })
    try:
        monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
        monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(tmp_path / "s"))
        # The note applies only to a RUNNING-but-failing daemon; fake liveness
        # (no real daemon exists in tests).
        monkeypatch.setattr(
            "java_codebase_rag.watch.client.is_daemon_alive", lambda _idx: True
        )
        from java_codebase_rag.config import resolve_operator_config

        cfg_on = resolve_operator_config(source_root=None)
        state = jrag._prime_state(cfg_on, graph, meta)
        assert state.daemon_note is not None
        assert "reindex failing" in prime_mod.render(state)

        monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED")
        cfg_off = resolve_operator_config(source_root=None)
        state_off = jrag._prime_state(cfg_off, graph, meta)
        assert state_off.daemon_note is None
        assert "reindex failing" not in prime_mod.render(state_off)
    finally:
        _cleanup_state(cfg)
