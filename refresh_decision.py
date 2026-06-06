"""Decision engine for incremental vs full refresh of Lance + Kuzu indexes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ChangeSet:
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    renamed: tuple[str, ...] = ()
    config_changed: bool = False
    pipeline_changed: bool = False
    meta_annotation_changed: bool = False


@dataclass(frozen=True)
class RefreshDecision:
    lance_mode: Literal["incremental", "full"]
    kuzu_mode: Literal["incremental", "full"]
    reasons: tuple[str, ...] = ()
    detected_changes: ChangeSet = field(default_factory=ChangeSet)


_CONFIG_FILES = {
    ".java-codebase-rag.yml",
    ".lancedb-mcp.yml",
}

_PIPELINE_FILES = {
    "java_index_flow_lancedb.py",
    "build_ast_graph.py",
    "graph_enrich.py",
}


def _any_match(paths: tuple[str, ...], names: set[str]) -> bool:
    return any(Path(p).name in names for p in paths)


def _all_java_paths(changes: ChangeSet) -> tuple[str, ...]:
    return changes.added + changes.modified + changes.deleted + changes.renamed


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _detect_repo_changes(
    source_root: Path,
    *,
    changed_paths: list[str] | None = None,
    deps_index: dict | None = None,
) -> ChangeSet:
    """Detect repository changes via changed_paths or hash-based diff against .deps.json."""
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    all_paths: list[str] = []

    if changed_paths is not None:
        for p in changed_paths:
            all_paths.append(p)
            modified.append(p)
    elif deps_index is not None:
        files_index = deps_index.get("files", {})
        cached_paths = set(files_index.keys())

        # Walk source tree for .java files and compare hashes
        on_disk: set[str] = set()
        for java_file in source_root.rglob("*.java"):
            rel = str(java_file.relative_to(source_root))
            on_disk.add(rel)
            current_hash = _sha256(java_file)
            cached = files_index.get(rel)
            if cached is None:
                added.append(rel)
                all_paths.append(rel)
            elif cached.get("ext_hash") != current_hash:
                modified.append(rel)
                all_paths.append(rel)

        # Files in index but no longer on disk
        for rel in cached_paths - on_disk:
            deleted.append(rel)
            all_paths.append(rel)

    all_t = tuple(all_paths)
    config_changed = _any_match(all_t, _CONFIG_FILES)
    pipeline_changed = _any_match(all_t, _PIPELINE_FILES)
    meta_annotation_changed = False  # deferred to PR-T5 brownfield closure refinement

    return ChangeSet(
        added=tuple(added),
        modified=tuple(modified),
        deleted=tuple(deleted),
        renamed=(),
        config_changed=config_changed,
        pipeline_changed=pipeline_changed,
        meta_annotation_changed=meta_annotation_changed,
    )


def _read_deps_ontology_version(kuzu_path: Path) -> int | None:
    """Read ontology_version from .deps.json sidecar. Returns None if missing/stale."""
    deps_path = kuzu_path.parent / ".deps.json"
    if not deps_path.is_file():
        return None
    try:
        raw = json.loads(deps_path.read_text())
        return int(raw.get("ontology_version", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _count_deps_files(kuzu_path: Path) -> int:
    """Count total files tracked in .deps.json."""
    deps_path = kuzu_path.parent / ".deps.json"
    if not deps_path.is_file():
        return 0
    try:
        raw = json.loads(deps_path.read_text())
        return len(raw.get("files", {}))
    except (json.JSONDecodeError, OSError):
        return 0


def _read_deps_index(kuzu_path: Path) -> dict | None:
    """Read full .deps.json content. Returns None if missing or corrupt."""
    deps_path = kuzu_path.parent / ".deps.json"
    if not deps_path.is_file():
        return None
    try:
        return json.loads(deps_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _current_ontology_version() -> int:
    """Import and return the current ontology version from ast_java."""
    from ast_java import ONTOLOGY_VERSION

    return ONTOLOGY_VERSION


def _choose_refresh_mode(
    changes: ChangeSet,
    *,
    kuzu_path: Path,
    mode: Literal["auto", "incremental", "full"] = "auto",
) -> RefreshDecision:
    """Choose refresh mode for Lance and Kuzu based on detected changes."""
    reasons: list[str] = []

    # Explicit full overrides everything
    if mode == "full":
        return RefreshDecision(
            lance_mode="full",
            kuzu_mode="full",
            reasons=("explicit full mode requested",),
            detected_changes=changes,
        )

    # --- Kuzu mode ---
    kuzu_mode: Literal["incremental", "full"] = "incremental"

    if changes.deleted:
        kuzu_mode = "full"
        reasons.append(f"deleted files detected ({len(changes.deleted)})")
    elif changes.renamed:
        kuzu_mode = "full"
        reasons.append(f"renamed files detected ({len(changes.renamed)})")

    if changes.config_changed:
        kuzu_mode = "full"
        reasons.append("config file changed")

    if changes.pipeline_changed:
        kuzu_mode = "full"
        reasons.append("indexing pipeline file changed")

    if changes.meta_annotation_changed and kuzu_mode != "full":
        kuzu_mode = "full"
        reasons.append("meta-annotation file changed")

    # .deps.json checks
    deps_ov = _read_deps_ontology_version(kuzu_path)
    current_ov = _current_ontology_version()
    if deps_ov is None:
        kuzu_mode = "full"
        reasons.append(".deps.json missing or corrupt")
    elif deps_ov != current_ov:
        kuzu_mode = "full"
        reasons.append(
            f".deps.json ontology_version {deps_ov} != current {current_ov}"
        )

    # >50% dirty heuristic
    if kuzu_mode == "incremental":
        total = _count_deps_files(kuzu_path)
        dirty_count = len(changes.added) + len(changes.modified) + len(changes.deleted) + len(changes.renamed)
        if total and dirty_count > 0.5 * total:
            kuzu_mode = "full"
            reasons.append(f"dirty set {dirty_count}/{total} > 50%")

    # --- Lance mode ---
    lance_mode: Literal["incremental", "full"] = "incremental"
    if changes.config_changed:
        lance_mode = "full"
        if "config file changed" not in reasons:
            reasons.append("config file changed")
    if changes.pipeline_changed:
        lance_mode = "full"
        if "indexing pipeline file changed" not in reasons:
            reasons.append("indexing pipeline file changed")

    return RefreshDecision(
        lance_mode=lance_mode,
        kuzu_mode=kuzu_mode,
        reasons=tuple(reasons),
        detected_changes=changes,
    )


def choose_refresh_mode(
    source_root: Path,
    kuzu_path: Path,
    *,
    mode: Literal["auto", "incremental", "full"] = "auto",
    changed_paths: list[str] | None = None,
) -> RefreshDecision:
    """Public API: detect changes and choose refresh mode.

    Change detection order:
    1. Explicit ``changed_paths`` if provided.
    2. Hash-based diff against ``.deps.json`` (walk source tree, compare
       SHA-256 hashes).
    3. Empty change set if neither is available.
    """
    deps_index = _read_deps_index(kuzu_path) if changed_paths is None else None
    changes = _detect_repo_changes(
        source_root,
        changed_paths=changed_paths,
        deps_index=deps_index,
    )
    return _choose_refresh_mode(changes, kuzu_path=kuzu_path, mode=mode)
