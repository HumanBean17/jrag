"""usage.writer — append discipline: caps, retention, swallow-guard (Task 4)."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from java_codebase_rag.usage import writer


def _ev(project_key: str = "k1", **over) -> dict:
    base = {
        "v": 1, "ts": "2026-09-13T10:00:00.000Z", "surface": "cli",
        "event": "command", "project_key": project_key, "pid": 1,
        "verb": "search", "query": "Foo", "flags": {},
        "duration_ms": 5.0, "rc": 0, "envelope_facts": {},
        "index_age_s": None, "served_by": "cold", "ppid": 2, "cwd": "/tmp",
    }
    base.update(over)
    return base


def _events_dir(state: Path) -> Path:
    return state / "events" / "k1"


def _today_file(state: Path) -> Path:
    return _events_dir(state) / f"events-{date.today():%Y-%m-%d}.jsonl"


def test_disabled_writes_nothing(tmp_path: Path) -> None:
    assert writer.record_event(_ev(), enabled=False, state_dir_override=str(tmp_path)) is False
    assert not (tmp_path / "events").exists()


def test_appends_one_line(tmp_path: Path) -> None:
    assert writer.record_event(_ev(), enabled=True, state_dir_override=str(tmp_path))
    assert writer.record_event(_ev(), enabled=True, state_dir_override=str(tmp_path))
    lines = _today_file(tmp_path).read_text().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["verb"] == "search" and parsed["project_key"] == "k1"


def test_oversize_line_drops_query_then_event(tmp_path: Path) -> None:
    long_q = "q" * 600
    ev = _ev(query=long_q)
    assert writer.record_event(ev, enabled=True, state_dir_override=str(tmp_path))
    line = _today_file(tmp_path).read_text().splitlines()[0]
    assert len(line.encode()) <= 512
    assert json.loads(line)["query"] is None
    # Still oversize without the query (huge flags) -> dropped entirely.
    fat = _ev(query=None, flags={"blob": "x" * 600})
    assert writer.record_event(fat, enabled=True, state_dir_override=str(tmp_path)) is False
    drops = _events_dir(tmp_path) / f"events-{date.today():%Y-%m-%d}.drops"
    assert drops.read_text().strip() == "1"


def test_size_cap_drop(tmp_path: Path) -> None:
    target = _today_file(tmp_path)
    target.parent.mkdir(parents=True)
    with open(target, "wb") as fh:
        fh.write(b"0" * writer.DAY_FILE_CAP_BYTES)
    assert writer.record_event(_ev(), enabled=True, state_dir_override=str(tmp_path)) is False
    assert target.read_bytes() == b"0" * writer.DAY_FILE_CAP_BYTES  # untouched
    drops = _events_dir(tmp_path) / f"events-{date.today():%Y-%m-%d}.drops"
    assert drops.read_text().strip() == "1"


def test_retention_prune(tmp_path: Path) -> None:
    ev_dir = _events_dir(tmp_path)
    ev_dir.mkdir(parents=True)
    old = ev_dir / "events-2020-01-01.jsonl"
    old_drops = ev_dir / "events-2020-01-01.drops"
    old.write_text("{}\n")
    old_drops.write_text("7\n")
    keep = ev_dir / f"events-{date.today():%Y-%m-%d}.jsonl"
    near = ev_dir / f"events-{date.today() - timedelta(days=29):%Y-%m-%d}.jsonl"
    notes = ev_dir / "notes.txt"
    near.write_text("{}\n")
    notes.write_text("keep me\n")
    assert writer.record_event(_ev(), enabled=True, state_dir_override=str(tmp_path))
    assert not old.exists() and not old_drops.exists()
    assert keep.exists() and near.exists() and notes.exists()


def test_never_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_DEBUG_CONTEXT", raising=False)

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(os, "open", boom)
    assert writer.record_event(_ev(), enabled=True, state_dir_override=str(tmp_path)) is False


def test_record_feedback_appends(tmp_path: Path) -> None:
    label = {"ts": "2026-09-13T10:00:00.000Z", "event_id": "abc123", "rating": "good", "note": ""}
    assert writer.record_feedback(label, project_key="k1",
                                  enabled=True, state_dir_override=str(tmp_path))
    fb = _events_dir(tmp_path) / "feedback.jsonl"
    assert json.loads(fb.read_text().splitlines()[0])["event_id"] == "abc123"
