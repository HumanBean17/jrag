"""Minimal embedding config for the MCP bundle (no cocoindex dependency)."""

from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_HUB = "sentence-transformers/all-MiniLM-L6-v2"
SBERT_MODEL = os.path.expandvars(
    os.path.expanduser(os.environ.get("SBERT_MODEL", _DEFAULT_HUB))
)


def coerce_json_dict(val: object) -> dict[str, Any]:
    """Normalize JSON TEXT columns from SQLite into dicts."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
