"""Agent-CLI usage tap — per-invocation events from jrag.main (Task 5)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from java_codebase_rag import jrag

pytestmark = pytest.mark.usefixtures("mcp_env")


@pytest.fixture
def usage_state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "usage-state"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    return state


def _day_file(state: Path) -> Path:
    events = list(state.rglob(f"events-{date.today():%Y-%m-%d}.jsonl"))
    assert len(events) == 1, f"expected one day file, found {events}"
    return events[0]


def _events(state: Path) -> list[dict]:
    return [json.loads(line) for line in _day_file(state).read_text().splitlines()]


def test_ok_invocation_recorded(usage_state, capsys) -> None:
    rc = jrag.main(["find", "ProcessedEventKeyRepository", "--format", "json"])
    assert rc == 0
    (ev,) = _events(usage_state)
    assert ev["surface"] == "cli" and ev["event"] == "command"
    assert ev["verb"] == "find"
    assert ev["query"] == "ProcessedEventKeyRepository"
    assert ev["rc"] == 0
    assert ev["envelope_facts"]["status"] == "ok"
    assert ev["envelope_facts"]["result_count"] >= 1
    assert isinstance(ev["pid"], int) and isinstance(ev["ppid"], int)
    assert isinstance(ev["cwd"], str)
    assert ev["served_by"] in {"daemon", "cold"}  # cold in-process in tests
    assert ev["index_age_s"] is None  # no watch state file for the fixture
    assert ev["event_id"]


def test_disabled_writes_nothing(tmp_path: Path, monkeypatch, mcp_env, capsys) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    state = tmp_path / "should-not-exist"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    rc = jrag.main(["find", "ProcessedEventKeyRepository", "--format", "json"])
    assert rc == 0
    assert not state.exists()
    out = json.loads(capsys.readouterr().out)
    assert "event_id" not in out  # omitted-when-empty when telemetry is off


def test_error_invocation_recorded(usage_state, monkeypatch) -> None:
    def boom(_args):
        raise RuntimeError("kaput")

    monkeypatch.setattr(jrag, "_cmd_find", boom)
    rc = jrag.main(["find", "anything"])
    assert rc == 2
    (ev,) = _events(usage_state)
    assert ev["rc"] == 2
    assert ev["envelope_facts"]["status"] == "error"
    assert ev["envelope_facts"]["error_type"] == "RuntimeError"
    assert ev["event_id"]


def test_event_id_on_envelope_matches_event(usage_state, capsys) -> None:
    rc = jrag.main(["find", "ProcessedEventKeyRepository", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    (ev,) = _events(usage_state)
    assert out["event_id"] == ev["event_id"]
    assert len(out["event_id"]) == 10


def test_usage_error_recorded(usage_state) -> None:
    rc = jrag.main(["find", "--bogus-flag"])
    assert rc == 2
    (ev,) = _events(usage_state)
    assert ev["verb"] == "find"
    assert ev["envelope_facts"]["error_type"] == "usage_error"
    assert ev["rc"] == 2
