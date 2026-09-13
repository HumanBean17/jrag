"""Operator-CLI usage mirror — one event per operator verb (Task 6)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from java_codebase_rag import cli


@pytest.fixture
def usage_state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "usage-state"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    return state


def _events(state: Path) -> list[dict]:
    files = list(state.rglob(f"events-{date.today():%Y-%m-%d}.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def test_operator_invocation_recorded(usage_state, mcp_env) -> None:
    rc = cli.main(["meta"])
    assert rc == 0
    (ev,) = _events(usage_state)
    assert ev["surface"] == "cli" and ev["event"] == "command"
    assert ev["verb"] == "meta"
    assert ev["rc"] == 0
    assert ev["envelope_facts"]["status"] == "ok"
    assert ev["query"] is None


def test_operator_disabled(mcp_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    state = tmp_path / "nope"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    rc = cli.main(["meta"])
    assert rc == 0
    assert not state.exists()
