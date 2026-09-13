"""Durable state dir + event file derivation for local observability.

Pure-path logic plus ``mkdir`` — no other I/O, no imports outside the stdlib.
The state dir is deliberately NOT the index dir: ``jrag erase`` rebuilds the
index, it must not wipe usage history. Project sharding reuses the 12-hex
``project_key`` derivation owned by :mod:`java_codebase_rag.watch.paths`
(callers pass the key; this module stays key-agnostic).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

_APP_DIR = "jrag"


def state_dir(override: str | None = None) -> Path:
    """Return the per-user durable state directory, created if missing.

    Resolution order:
    1. explicit ``override`` (resolved config ``usage.dir``)
    2. env ``XDG_STATE_HOME`` / ``<XDG_STATE_HOME>/jrag``
    3. platform default: ``~/.local/state/jrag`` on Linux,
       ``~/Library/Application Support/jrag`` on macOS
    """
    if override:
        target = Path(override).expanduser()
    else:
        target = _default_base()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _default_base() -> Path:
    home = Path(os.environ.get("HOME") or Path.home())
    darwin = sys.platform == "darwin"
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    # XDG spec: relative values are ignored (a cwd-dependent state dir would
    # break event discovery). The env var applies on every platform — an
    # operator who set it made a deliberate choice.
    if xdg and Path(xdg).expanduser().is_absolute():
        return Path(xdg).expanduser() / _APP_DIR
    if darwin:
        return home / "Library" / "Application Support" / _APP_DIR
    return home / ".local" / "state" / _APP_DIR


def project_events_dir(project_key: str, override: str | None = None) -> Path:
    """Return ``<state>/events/<project_key>``, created if missing."""
    events = state_dir(override) / "events" / project_key
    events.mkdir(parents=True, exist_ok=True)
    return events


def day_file(project_key: str, day: date, override: str | None = None) -> Path:
    """Day-sharded event journal: ``events-YYYY-MM-DD.jsonl``."""
    return project_events_dir(project_key, override) / f"events-{day:%Y-%m-%d}.jsonl"


def feedback_file(project_key: str, override: str | None = None) -> Path:
    """Owner feedback labels: ``feedback.jsonl`` (no cap, no prune)."""
    return project_events_dir(project_key, override) / "feedback.jsonl"


def drops_file(project_key: str, day: date, override: str | None = None) -> Path:
    """Overflow accounting: integer count of events dropped past the day cap."""
    return project_events_dir(project_key, override) / f"events-{day:%Y-%m-%d}.drops"
