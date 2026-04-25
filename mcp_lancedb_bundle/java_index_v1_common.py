"""Shared helpers for Java/SQL/YAML CocoIndex 1.0 apps (no ContextKeys here)."""

from __future__ import annotations

import os
from typing import Any

from cocoindex.resources.chunk import Chunk, TextPosition

# Hub id or absolute path to a local model dir (config.json + weights). Override with env SBERT_MODEL.
_DEFAULT_HUB = "sentence-transformers/all-MiniLM-L6-v2"
SBERT_MODEL = os.path.expandvars(os.path.expanduser(os.environ.get("SBERT_MODEL", _DEFAULT_HUB)))

# Pruning for LocalFile sources: skip VCS, build outputs, dependency trees, and
# test sources (we currently index prod Java only to keep the semantic index clean).
# Also avoids EMFILE under default ulimits when the engine traverses in parallel.
COMMON_EXCLUDED_PATH_PATTERNS: list[str] = [
    "**/.*",
    "**/.git/**",
    "**/.idea/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/target/**",
    "**/build/**",
    "**/*.class",
    "**/src/test/java/**",
    "**/src/test/resources/**",
]

# Larger window + overlap so chunks carry more behavioural context (method bodies
# rarely split mid-statement, fewer "orphan" import-only hits at chunk edges).
# Requires re-index to apply.
JAVA_CHUNK = (1500, 350, 220)
SQL_CHUNK = (800, 100, 80)
YAML_CHUNK = (600, 100, 60)


def position_to_json(pos: TextPosition) -> dict[str, Any]:
    return {
        "byte_offset": pos.byte_offset,
        "char_offset": pos.char_offset,
        "line": pos.line,
        "column": pos.column,
    }


def chunk_key_range(chunk: Chunk) -> tuple[int, int]:
    """Byte range for stable primary keys (start inclusive, end exclusive)."""
    return chunk.start.byte_offset, chunk.end.byte_offset
