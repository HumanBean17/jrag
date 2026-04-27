"""Shared helpers for Java/SQL/YAML CocoIndex 1.0 apps (no ContextKeys here)."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
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
    "**/out/**",
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


# ---------- shared Java source tree walk (graph index + meta-annotation pass) ----------

def compile_excluded_glob_patterns(
    patterns: Iterable[str] | tuple[str, ...],
) -> list[str]:
    """Store exclude patterns in list form; same as ast-graph `index` compile step."""
    return list(patterns)


def is_relative_path_excluded(
    rel_posix: str, exclude_globs: list[str],
) -> bool:
    """True if a project-relative path matches an exclude glob (incl. `**/<path>`)."""
    for pat in exclude_globs:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        if fnmatch.fnmatch(f"**/{rel_posix}", pat):
            return True
    return False


def iter_java_source_files(
    root: Path, exclude_globs: list[str],
) -> Iterator[Path]:
    """Walk `root` for `*.java`, honouring the same prunes and globs as `build_ast_graph`."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in (
                ".git",
                "target",
                "build",
                "out",
                "node_modules",
                ".venv",
                ".idea",
            )
        ]
        for fn in filenames:
            if not fn.endswith(".java"):
                continue
            p = Path(dirpath) / fn
            try:
                rel = p.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = p.as_posix()
            if is_relative_path_excluded(rel, exclude_globs):
                continue
            yield p