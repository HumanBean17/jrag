"""JSONL event writer — the append discipline.

Local-only telemetry storage. Rules (spec decisions 2/4/9):

* ONE ``os.write`` of the whole line on an ``O_APPEND|O_CREAT`` fd — no locks;
  concurrent one-shot CLI processes stay safe in practice for small lines.
* Synchronous at emit time: both CLI ``_console_script_main`` and the watch
  daemon ``os._exit()`` past buffered flushes, so nothing may be deferred.
* The entire body is a swallow-everything guard — telemetry never changes
  stdout/stderr/exit codes of the host command. A debug line appears only
  under ``JAVA_CODEBASE_RAG_DEBUG_CONTEXT``.
* Bounded: day-sharded files, 30-day retention, 5 MiB/day cap (overflow is
  dropped and counted in a ``.drops`` sidecar).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from java_codebase_rag.usage import paths

#: Max serialized line size (bytes). A single ``write()`` on an O_APPEND
#: regular-file fd is positioned+written atomically by the kernel (PIPE_BUF
#: applies to pipes, not files), so 1 KiB lines stay safely single-append —
#: measured real events (cwd + facts + capped query) run 400-900 bytes.
LINE_CAP_BYTES = 1024

#: Day-file size cap; events past it are dropped and counted.
DAY_FILE_CAP_BYTES = 5 * 1024 * 1024

#: Day files older than this are pruned on write.
RETENTION_DAYS = 30

_DEBUG_ENV = "JAVA_CODEBASE_RAG_DEBUG_CONTEXT"


def record_event(
    event: dict[str, Any],
    *,
    enabled: bool,
    state_dir_override: str | None = None,
) -> bool:
    """Append one event line; True iff written. Never raises (see module doc)."""
    try:
        return _record_event_inner(event, enabled=enabled,
                                   state_dir_override=state_dir_override)
    except Exception as exc:  # noqa: BLE001 — the guard IS the contract
        _debug_drop(exc)
        return False


def record_feedback(
    label: dict[str, Any],
    *,
    project_key: str,
    enabled: bool,
    state_dir_override: str | None = None,
) -> bool:
    """Append one feedback label; no cap, no prune (labels are tiny)."""
    if not enabled:
        return False
    try:
        line = json.dumps(label, separators=(",", ":"), default=str) + "\n"
        target = paths.feedback_file(project_key, state_dir_override)
        _append_line(target, line)
        return True
    except Exception as exc:  # noqa: BLE001
        _debug_drop(exc)
        return False


def _record_event_inner(
    event: dict[str, Any], *, enabled: bool, state_dir_override: str | None
) -> bool:
    if not enabled:
        return False
    key = event.get("project_key") or "unknown"
    today = date.today()
    line = _serialize_capped(event)
    if line is None:
        bump_drops(paths.drops_file(key, today, state_dir_override))
        return False
    target = paths.day_file(key, today, state_dir_override)
    if target.exists() and target.stat().st_size >= DAY_FILE_CAP_BYTES:
        bump_drops(paths.drops_file(key, today, state_dir_override))
        return False
    _append_line(target, line)
    _prune_old(paths.project_events_dir(key, state_dir_override), today)
    return True


def _serialize_capped(event: dict[str, Any]) -> str | None:
    """Serialize under LINE_CAP; drop ``query`` first, then the event."""
    line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
    if len(line.encode()) <= LINE_CAP_BYTES:
        return line
    trimmed = dict(event, query=None)
    line = json.dumps(trimmed, separators=(",", ":"), default=str) + "\n"
    return line if len(line.encode()) <= LINE_CAP_BYTES else None


def _append_line(target: Path, line: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _prune_old(events_dir: Path, today: date) -> None:
    cutoff = today - timedelta(days=RETENTION_DAYS)
    for entry in events_dir.iterdir():
        day = _day_from_name(entry.name)
        if day is not None and day < cutoff:
            entry.unlink(missing_ok=True)


def _day_from_name(name: str) -> date | None:
    if not (name.startswith("events-") and
            (name.endswith(".jsonl") or name.endswith(".drops"))):
        return None
    stem = name[len("events-"):-len(".jsonl")] if name.endswith(".jsonl") \
        else name[len("events-"):-len(".drops")]
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def bump_drops(path: Path) -> None:
    """Best-effort increment of the day's drop counter; tolerant of garbage."""
    try:
        current = 0
        if path.exists():
            raw = path.read_text().strip()
            current = int(raw) if raw.isdigit() else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{current + 1}\n")
    except Exception:  # noqa: BLE001 — accounting must never break the host
        pass


def _debug_drop(exc: Exception) -> None:
    if os.environ.get(_DEBUG_ENV, "").strip():
        print(f"jrag: usage event dropped: {exc}", file=sys.stderr)
