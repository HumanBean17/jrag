"""jrag usage verb — rollup, zero-states, drift (Task 9)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from java_codebase_rag import jrag

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


def _write_event(state: Path, event: dict) -> None:
    ev_dir = _events_dir(state)
    ev_dir.mkdir(parents=True, exist_ok=True)
    with open(ev_dir / f"events-{date.today():%Y-%m-%d}.jsonl", "a") as fh:
        fh.write(json.dumps(event) + "\n")


def _cmd_event(verb: str, *, status: str = "ok", count: int = 2) -> dict:
    return {
        "v": 1, "ts": "2026-09-13T09:00:00.000Z", "surface": "cli",
        "event": "command", "project_key": "k", "pid": 1, "verb": verb,
        "query": "Foo", "flags": {}, "duration_ms": 12.0, "rc": 0,
        "envelope_facts": {
            "status": status, "result_count": count, "truncated": False,
            "candidates_count": 0, "absence_verdict": None,
            "absence_cause": None, "warnings_count": 0,
        },
        "index_age_s": 60.0, "served_by": "cold", "ppid": 9, "cwd": "/p",
        "event_id": "aaaa000000",
    }


def test_disabled_zero_state(tmp_path: Path, monkeypatch, env_pinned, capsys) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    state = tmp_path / "never"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_USAGE_DIR", str(state))

    def _no_reads(files, since_days):
        raise AssertionError("must not read event files when telemetry is off")

    monkeypatch.setattr("java_codebase_rag.usage.summarize.load_events", _no_reads)
    rc = jrag.main(["usage", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert "JAVA_CODEBASE_RAG_USAGE_ENABLED" in out["message"]


def test_empty_zero_state(usage_state, env_pinned, capsys) -> None:
    # Create the project events dir by making one throwaway invocation with
    # telemetry on, then wipe the day files — dir exists, no events.
    jrag.main(["find", "ProcessedEventKeyRepository"])
    capsys.readouterr()  # discard the find output
    for f in _events_dir(usage_state).glob("events-*.jsonl"):
        f.unlink()
    rc = jrag.main(["usage", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert "no usage events" in out["message"]


def test_populated_rollup(usage_state, env_pinned, capsys) -> None:
    # Seed real events via the tap itself (find ok + a not_found).
    jrag.main(["find", "ProcessedEventKeyRepository"])
    jrag.main(["find", "AbsolutelyMissingThing"])
    # Plus a synthetic watch event for the health section.
    _write_event(usage_state, {
        "v": 1, "ts": "2026-09-13T09:00:00.000Z", "surface": "watch",
        "event": "reindex", "kind": "error",
        "detail": {"phase": "graph", "returncode": 1, "stderr_tail": "boom"},
        "project_key": "k", "pid": 5,
    })
    capsys.readouterr()  # discard the seeding invocations' output
    rc = jrag.main(["usage", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    node = out["nodes"]["usage"]
    assert node["window_days"] == 7
    by_verb = {row["verb"]: row for row in node["calls"]}
    assert by_verb["find"]["calls"] >= 2
    assert by_verb["find"]["ok"] >= 1
    assert node["watch"]["recent_failures"][0]["stderr_tail"] == "boom"
    assert node["storage"]["files"] >= 1
    assert node["sessions"]["count"] >= 1


def test_agent_verbs_contains_usage() -> None:
    from java_codebase_rag.cli_dispatch import AGENT_VERBS

    assert "usage" in AGENT_VERBS
