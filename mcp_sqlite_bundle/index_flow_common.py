"""Chunking and position helpers for CocoIndex Java/SQL/YAML flows (inline, no external dep)."""

from __future__ import annotations

from typing import Any

from cocoindex.resources.chunk import Chunk, TextPosition

from index_common import SBERT_MODEL, coerce_json_dict

# Skip VCS, build outputs, and dependency trees during walks (see mcp_lancedb_bundle/java_index_v1_common.py).
COMMON_EXCLUDED_PATH_PATTERNS: list[str] = [
    "**/.*",
    "**/node_modules/**",
    "**/target/**",
    "**/build/**",
    "**/.git/**",
]

# Aligned with mcp_pgvector_bundle/java_index_flow_postgres.py
JAVA_CHUNK = (1000, 300, 300)
SQL_CHUNK = (2000, 100, 200)
YAML_CHUNK = (2000, 100, 200)

__all__ = [
    "COMMON_EXCLUDED_PATH_PATTERNS",
    "JAVA_CHUNK",
    "SQL_CHUNK",
    "YAML_CHUNK",
    "SBERT_MODEL",
    "chunk_key_range",
    "position_to_json",
    "coerce_json_dict",
]


def position_to_json(pos: TextPosition) -> dict[str, Any]:
    return {
        "byte_offset": pos.byte_offset,
        "char_offset": pos.char_offset,
        "line": pos.line,
        "column": pos.column,
    }


def chunk_key_range(ch: Chunk) -> tuple[int, int]:
    """Stable line range for chunk rows (matches Lance/PG row semantics)."""
    return (ch.start.line, ch.end.line)
