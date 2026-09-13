"""Event record contract for local observability (schema v1).

Builds dicts only — no I/O. The common core is versioned (``v``) and
surface-neutral (``surface`` is an open enum: ``"mcp"`` joins later without a
format migration); readers ignore unknown fields. Payload discipline:
identifiers and outcomes, never file contents or snippets — ``cap_query``
enforces the query cap because agent queries may contain pasted code.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

#: Open enums — ``"mcp"`` is a planned future surface, not a v1 writer.
SURFACES: tuple[str, ...] = ("cli", "watch")
EVENT_KINDS: tuple[str, ...] = ("command", "reindex", "daemon")

_QUERY_CAP = 200


def rfc3339_now() -> str:
    """UTC RFC3339 timestamp with millisecond precision, ``Z``-suffixed."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def cap_query(q: str | None) -> str | None:
    """Cut at the first newline, then truncate to the query cap."""
    if q is None:
        return None
    first_line = q.split("\n", 1)[0]
    return first_line[:_QUERY_CAP]


def derive_event_id(*stable: object) -> str:
    """First 10 hex chars of SHA256 over the stable event fields."""
    digest = hashlib.sha256(repr(stable).encode()).hexdigest()
    return digest[:10]


def _common_core(surface: str, event: str, project_key: str) -> dict[str, Any]:
    return {
        "v": 1,
        "ts": rfc3339_now(),
        "surface": surface,
        "event": event,
        "project_key": project_key,
        "pid": os.getpid(),
    }


def build_command_event(
    *,
    verb: str | None,
    query: str | None,
    flags: dict[str, Any],
    duration_ms: float,
    rc: int,
    envelope_facts: dict[str, Any],
    index_age_s: float | None,
    served_by: str | None,
    ppid: int | None,
    cwd: str | None,
    project_key: str,
) -> dict[str, Any]:
    """One agent/operator CLI invocation. Optional facts stay explicit None."""
    ev = _common_core("cli", "command", project_key)
    ev.update(
        {
            "verb": verb,
            "query": cap_query(query),
            "flags": flags,
            "duration_ms": duration_ms,
            "rc": rc,
            "envelope_facts": envelope_facts,
            "index_age_s": index_age_s,
            "served_by": served_by,
            "ppid": ppid,
            "cwd": cwd,
        }
    )
    return ev


def build_reindex_event(
    kind: str, detail: dict[str, Any], project_key: str
) -> dict[str, Any]:
    """Watcher lifecycle event; error details carry a pre-trimmed stderr_tail."""
    ev = _common_core("watch", "reindex", project_key)
    ev.update({"kind": kind, "detail": detail})
    return ev


def build_daemon_event(
    lifecycle: str, detail: dict[str, Any], project_key: str
) -> dict[str, Any]:
    """Daemon start/stop lifecycle."""
    ev = _common_core("watch", "daemon", project_key)
    ev.update({"lifecycle": lifecycle, "detail": detail})
    return ev
