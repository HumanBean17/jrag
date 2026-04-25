"""Discover `*.java` under source roots using the same exclusions as the LanceDB index."""

from __future__ import annotations

import os
from pathlib import Path

def _path_excluded_by_prefixes(rel_posix: str) -> bool:
    """Match project `COMMON_EXCLUDED_PATH_PATTERNS` intent without full ant glob."""
    parts = rel_posix.split("/")
    if ".git" in parts:
        return True
    if "node_modules" in parts:
        return True
    if "target" in parts:
        return True
    if "build" in parts:
        return True
    for p in parts:
        if p.startswith(".") and p not in (".", ".."):
            return True
    return False


def iter_java_files(
    roots: list[tuple[str, Path]],
    *,
    excluded_patterns: list[str] | None = None,
) -> list[tuple[str, Path, Path]]:
    """
    Yield (module_label, root_path, file_path) for each `*.java` file.

    `module_label` identifies the source root (e.g. repo folder name).
    """
    _ = excluded_patterns  # reserved for stricter glob parity with CocoIndex
    out: list[tuple[str, Path, Path]] = []
    seen: set[Path] = set()
    for label, root in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        for p in root.rglob("*.java"):
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            if _path_excluded_by_prefixes(rel):
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append((label, root, rp))
    out.sort(key=lambda x: (x[0], str(x[2])))
    return out


def resolve_source_roots_from_env() -> list[tuple[str, Path]]:
    """
    Return [(label, path), ...].

    - ``GRAPH_SOURCE_ROOTS``: comma-separated absolute or relative paths (first path
      component or basename used as label).
    - Else ``LANCEDB_MCP_PROJECT_ROOT`` as a single root.
    - Else current working directory.
    """
    raw = os.environ.get("GRAPH_SOURCE_ROOTS", "").strip()
    if raw:
        roots: list[tuple[str, Path]] = []
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            path = Path(p).expanduser().resolve()
            label = path.name or path.as_posix()
            roots.append((label, path))
        return roots

    pr = os.environ.get("LANCEDB_MCP_PROJECT_ROOT", "").strip()
    if pr:
        path = Path(pr).expanduser().resolve()
        return [(path.name, path)]

    cwd = Path.cwd().resolve()
    return [(cwd.name, cwd)]


def default_excluded_patterns() -> list[str]:
    from java_index_v1_common import COMMON_EXCLUDED_PATH_PATTERNS

    return list(COMMON_EXCLUDED_PATH_PATTERNS)
