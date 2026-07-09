"""Structured stderr events for brownfield extraction (PR-1 scaffolding; PR-2 wiring).

Each call emits one JSON object on a single line to sys.stderr. MCP stdio servers
must not use this module from tool handlers — graph build / AST paths only.
"""
from __future__ import annotations

import json
import sys
from typing import Any

_VALID_SEVERITIES = frozenset({"INFO", "WARN"})
_VALID_EVENTS = frozenset(
    {
        "brownfield-exclusivity-shadowing",
        "brownfield-method-string-literal",
    }
)


def emit_structured_brownfield_event(
    event_id: str,
    severity_level: str,
    **fields: Any,
) -> None:
    """Emit one JSON line to stderr with keys ``event`` and ``severity`` plus ``**fields``.

    Parameters are named ``event_id`` / ``severity_level`` so ``**fields`` may contain
    keys ``event`` or ``severity`` without a caller TypeError; those keys never override
    the canonical record (reserved keys are merged last).
    """
    if severity_level not in _VALID_SEVERITIES:
        raise ValueError(
            f"severity must be one of {_VALID_SEVERITIES}, got {severity_level!r}"
        )
    if event_id not in _VALID_EVENTS:
        raise ValueError(f"event must be one of {_VALID_EVENTS}, got {event_id!r}")
    # Fields first so JSON ``event`` / ``severity`` always match event_id / severity_level.
    payload = {**fields, "event": event_id, "severity": severity_level}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


def emit_brownfield_exclusivity_shadowing(**fields: Any) -> None:
    """INFO: brownfield HTTP annotation co-present with shadowable framework annotations."""
    emit_structured_brownfield_event(
        "brownfield-exclusivity-shadowing",
        "INFO",
        **fields,
    )


def emit_brownfield_method_string_literal(**fields: Any) -> None:
    """WARN: HTTP client/route `method` still a string literal (migration / misuse)."""
    emit_structured_brownfield_event(
        "brownfield-method-string-literal",
        "WARN",
        **fields,
    )
