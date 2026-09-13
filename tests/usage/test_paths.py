"""usage.paths — durable state dir + event file derivation (Task 1)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from java_codebase_rag.usage import paths


def test_state_dir_override_wins(tmp_path: Path) -> None:
    target = tmp_path / "x"
    result = paths.state_dir(str(target))
    assert result == target
    assert target.is_dir()


def test_state_dir_xdg(tmp_path: Path, monkeypatch) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    assert paths.state_dir() == xdg / "jrag"
    assert (xdg / "jrag").is_dir()


def test_state_dir_macos_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    result = paths.state_dir()
    assert result == tmp_path / "Library" / "Application Support" / "jrag"
    assert result.is_dir()


def test_state_dir_linux_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    result = paths.state_dir()
    assert result == tmp_path / ".local" / "state" / "jrag"
    assert result.is_dir()


def test_event_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    key = "abc123def456"
    day = date(2026, 9, 13)
    ev_dir = paths.project_events_dir(key)
    assert ev_dir == tmp_path / "xdg" / "jrag" / "events" / key
    assert ev_dir.is_dir()
    assert paths.day_file(key, day).name == "events-2026-09-13.jsonl"
    assert paths.feedback_file(key).name == "feedback.jsonl"
    assert paths.drops_file(key, day).name == "events-2026-09-13.drops"
    # Override threads through every derivation.
    override = tmp_path / "elsewhere"
    assert paths.project_events_dir(key, str(override)) == override / "events" / key
