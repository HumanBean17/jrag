"""Envelope event_id + jrag feedback verb (Task 10)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from java_codebase_rag import jrag
from java_codebase_rag.jrag_envelope import Envelope

pytestmark = pytest.mark.usefixtures("env_pinned")


@pytest.fixture
def usage_state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "usage-state"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", "1")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))
    return state


def _events_dir(state: Path) -> Path:
    dirs = list((state / "events").iterdir())
    assert len(dirs) == 1
    return dirs[0]


def _known_event_id(usage_state) -> str:
    day = _events_dir(usage_state) / f"events-{date.today():%Y-%m-%d}.jsonl"
    lines = [json.loads(l) for l in day.read_text().splitlines()]
    ids = [e["event_id"] for e in lines if e.get("event_id")]
    assert ids
    return ids[0]


def test_envelope_omits_event_id_when_none() -> None:
    assert "event_id" not in Envelope(status="ok").to_dict()
    assert "event_id" not in json.loads(Envelope(status="ok").to_json())


def test_feedback_roundtrip(usage_state, capsys) -> None:
    jrag.main(["find", "ProcessedEventKeyRepository"])
    capsys.readouterr()
    eid = _known_event_id(usage_state)
    rc = jrag.main(["feedback", eid, "--good", "--note", "helpful", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    fb = _events_dir(usage_state) / "feedback.jsonl"
    (label,) = [json.loads(l) for l in fb.read_text().splitlines()]
    assert label["event_id"] == eid
    assert label["rating"] == "good"
    assert label["note"] == "helpful"


def test_feedback_unknown_id_not_found(usage_state, capsys) -> None:
    jrag.main(["find", "ProcessedEventKeyRepository"])
    capsys.readouterr()
    rc = jrag.main(["feedback", "missing99", "--bad", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "not_found"


def test_feedback_disabled_zero_state(tmp_path: Path, monkeypatch, env_pinned,
                                      capsys) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(tmp_path / "x"))
    rc = jrag.main(["feedback", "abc", "--good", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert "JAVA_CODEBASE_RAG_USAGE_ENABLED" in out["message"]


def test_note_capped_500(usage_state) -> None:
    jrag.main(["find", "ProcessedEventKeyRepository"])
    eid = _known_event_id(usage_state)
    jrag.main(["feedback", eid, "--bad", "--note", "x" * 600, "--format", "json"])
    fb = _events_dir(usage_state) / "feedback.jsonl"
    (label,) = [json.loads(l) for l in fb.read_text().splitlines()]
    assert len(label["note"]) == 500
