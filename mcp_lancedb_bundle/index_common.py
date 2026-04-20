"""Minimal embedding config for the MCP bundle (no cocoindex dependency)."""

from __future__ import annotations

import os

_DEFAULT_HUB = "sentence-transformers/all-MiniLM-L6-v2"
SBERT_MODEL = os.path.expandvars(
    os.path.expanduser(os.environ.get("SBERT_MODEL", _DEFAULT_HUB))
)
