"""Decision engine for incremental vs full refresh of Lance + Kuzu indexes."""
from __future__ import annotations

import json
import subprocess
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


def _detect_repo_changes(
    source_root: Path,
    *,
    git_ref_base: str = "HEAD",
    changed_paths: list[str] | None = None,
) -> ChangeSet:
    """Detect repository changes via git diff or changed_paths fallback."""
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    renamed: list[str] = []
    all_paths: list[str] = []

    # Try git first
    git_ok = False
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", git_ref_base],
            cwd=str(source_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            git_ok = True
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status = parts[0][0]
                path = parts[-1]
                all_paths.append(path)
                if status == "A":
                    added.append(path)
                elif status == "M":
                    modified.append(path)
                elif status == "D":
                    deleted.append(path)
                elif status == "R":
                    renamed.append(path)
                else:
                    modified.append(path)
        # Also check working tree + staged
        result2 = subprocess.run(
            ["git", "diff", "--name-status"],
            cwd=str(source_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result2.returncode == 0:
            for line in result2.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status = parts[0][0]
                path = parts[-1]
                if path not in all_paths:
                    all_paths.append(path)
                    if status == "D":
                        deleted.append(path)
                    elif status == "R":
                        renamed.append(path)
                    else:
                        modified.append(path)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if not git_ok and changed_paths is not None:
        for p in changed_paths:
            all_paths.append(p)
            modified.append(p)

    all_t = tuple(all_paths)
    config_changed = _any_match(all_t, _CONFIG_FILES)
    pipeline_changed = _any_match(all_t, _PIPELINE_FILES)

    # Heuristic: if any changed file is an @interface, flag meta_annotation
    meta_annotation_changed = False  # deferred to PR-T5 brownfield closure refinement

    return ChangeSet(
        added=tuple(added),
        modified=tuple(modified),
        deleted=tuple(deleted),
        renamed=tuple(renamed),
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


def _current_ontology_version() -> int:
    """Import and return the current ontology version from ast_java."""
    # Avoid heavy import at module level
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

    # Detection failure — if no changes detected at all and mode is auto,
    # be conservative unless we had explicit changed_paths
    if (
        kuzu_mode == "incremental"
        and mode == "auto"
        and not changes.added
        and not changes.modified
        and not changes.deleted
        and not changes.renamed
    ):
        # No changes detected — this is fine for incremental (nothing to do)
        pass

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
    git_ref_base: str = "HEAD",
) -> RefreshDecision:
    """Public API: detect changes and choose refresh mode."""
    changes = _detect_repo_changes(
        source_root,
        git_ref_base=git_ref_base,
        changed_paths=changed_paths,
    )
    return _choose_refresh_mode(changes, kuzu_path=kuzu_path, mode=mode)
