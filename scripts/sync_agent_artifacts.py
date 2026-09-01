#!/usr/bin/env python3
"""Sync agent and skill artifacts from dev source to install_data.

Skill/agent consumer artifacts were removed: the CLI surface ships a
``jrag prime`` SessionStart hook and the MCP surface registers the server
entry — neither deploys files. ``SYNC_MAP`` is therefore empty and this
script's remaining job is the absence guard: the directories in
``GUARDED_DIRS`` must contain no files, so the artifacts cannot creep back
into a wheel or a release.

Re-adding an artifact means restoring its ``SYNC_MAP`` pair; the sync/copy
machinery below is unchanged and will pick it up, and the guard exempts the
pair's files on both sides so a restored map runs green.

Usage:
    python scripts/sync_agent_artifacts.py          # Copy dev → install_data
    python scripts/sync_agent_artifacts.py --check  # Verify only (CI mode)

Exit codes:
    0: All files in sync
    1: Files out of sync (when --check) or copy verification failed
"""

from __future__ import annotations

import argparse
import difflib
import filecmp
import shutil
import sys
from pathlib import Path


# Mapping of source (dev) paths to destination (install_data) paths.
# Empty since the artifacts were removed — see the module docstring.
SYNC_MAP: list[tuple[Path, Path]] = []

# Directories that must contain no files. Covers both halves of the old sync
# (the dev source tree and its install_data mirror) so a reintroduced artifact
# fails the release gate regardless of which side it lands on.
GUARDED_DIRS: list[Path] = [
    Path("skills"),
    Path("agents"),
    Path("src/java_codebase_rag/install_data/skills"),
    Path("src/java_codebase_rag/install_data/agents"),
]


def collect_files(src_dir: Path, dst_dir: Path) -> list[tuple[Path, Path]]:
    """Collect (source, destination) file pairs for a subtree.

    Only regular files are included (no symlinks, no directories).
    """
    if not src_dir.is_dir():
        raise RuntimeError(f"Source directory missing: {src_dir}")

    pairs: list[tuple[Path, Path]] = []
    for src_file in src_dir.rglob("*"):
        if not src_file.is_file():
            continue
        # Compute relative path from source root
        rel_path = src_file.relative_to(src_dir)
        dst_file = dst_dir / rel_path
        pairs.append((src_file, dst_file))

    return pairs


def verify_byte_equality(src_file: Path, dst_file: Path) -> bool:
    """Check if two files are byte-identical.

    Returns True if identical, False otherwise.
    """
    if not dst_file.exists():
        return False
    return filecmp.cmp(src_file, dst_file, shallow=False)


def show_diff(src_file: Path, dst_file: Path) -> str:
    """Generate a unified diff between two files."""
    src_lines = src_file.read_text(encoding="utf-8").splitlines(keepends=True)
    dst_lines = dst_file.read_text(encoding="utf-8").splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(
            dst_lines,
            src_lines,
            fromfile=str(dst_file),
            tofile=str(src_file),
            lineterm="",
        )
    )


def sync_all(check_only: bool, repo_root: Path | None = None) -> int:
    """Sync all artifacts from dev source to install_data.

    Args:
        check_only: If True, verify only without copying.
        repo_root: Repository root directory (defaults to script parent parent).

    Returns:
        Exit code (0 for success, 1 for any mismatch).
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = repo_root.resolve()

    all_pairs: list[tuple[Path, Path]] = []
    for src_rel, dst_rel in SYNC_MAP:
        src_dir = repo_root / src_rel
        dst_dir = repo_root / dst_rel
        all_pairs.extend(collect_files(src_dir, dst_dir))

    # Check for drift
    out_of_sync: list[tuple[Path, Path, str]] = []
    missing: list[tuple[Path, Path]] = []

    for src_file, dst_file in all_pairs:
        if not dst_file.exists():
            missing.append((src_file, dst_file))
            continue

        if not verify_byte_equality(src_file, dst_file):
            out_of_sync.append((src_file, dst_file, "content differs"))

    # Absence guard: no file may live in a guarded directory. Mapped files are
    # exempt on BOTH sides — restoring a SYNC_MAP pair must revive the sync
    # without its own sources tripping the guard, so only files the map does
    # not account for are flagged. The stray-file boundary is scoped to
    # GUARDED_DIRS: files elsewhere under install_data/ are out of scope.
    all_mapped = {path for pair in all_pairs for path in pair}
    guarded_roots = [repo_root / d for d in GUARDED_DIRS]
    guarded_roots += [repo_root / dst_rel for _, dst_rel in SYNC_MAP]
    for guard_dir in guarded_roots:
        if guard_dir.exists():
            for stray in guard_dir.rglob("*"):
                if stray.is_file() and stray not in all_mapped:
                    out_of_sync.append((Path(""), stray, "extra file"))

    # Guard violations cannot be repaired by syncing — an empty SYNC_MAP has
    # nothing to copy — so they fail --check and copy mode alike. release.sh
    # points operators at the sync command on failure, which cannot clear
    # this; the remedy is spelled out per file below.
    extras = [entry for entry in out_of_sync if entry[2] == "extra file"]
    if extras:
        print("Skill/agent artifacts must not ship:", file=sys.stderr)
        for _, stray, _ in extras:
            print(f"  - {stray} (unexpected artifact; remove it or restore its SYNC_MAP pair)", file=sys.stderr)
        return 1

    if check_only:
        # --check mode: report issues and exit non-zero if any
        if not (missing or out_of_sync):
            print("✓ All agent artifacts in sync")
            return 0

        print("Agent artifacts out of sync:", file=sys.stderr)
        for src_file, dst_file, reason in out_of_sync:
            if reason == "extra file":
                print(f"  - {dst_file} (extra file)", file=sys.stderr)
            else:
                print(f"  - {dst_file} (differs from source)", file=sys.stderr)
                if src_file.exists() and dst_file.exists():
                    diff = show_diff(src_file, dst_file)
                    if diff:
                        print("    Diff:", file=sys.stderr)
                        for line in diff.splitlines():
                            print(f"      {line}", file=sys.stderr)

        for src_file, dst_file in missing:
            print(f"  - {dst_file} (missing)", file=sys.stderr)

        return 1

    # Copy mode: ensure destination directories exist and copy files
    for src_rel, dst_rel in SYNC_MAP:
        dst_dir = repo_root / dst_rel
        dst_dir.mkdir(parents=True, exist_ok=True)

    for src_file, dst_file in all_pairs:
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

    # Verify after copy
    copy_errors: list[tuple[Path, Path]] = []
    for src_file, dst_file in all_pairs:
        if not verify_byte_equality(src_file, dst_file):
            copy_errors.append((src_file, dst_file))

    if copy_errors:
        print("Copy verification failed for:", file=sys.stderr)
        for src_file, dst_file in copy_errors:
            print(f"  {src_file} → {dst_file}", file=sys.stderr)
        return 1

    print(f"✓ Synced {len(all_pairs)} agent artifact(s)")
    return 0


def main() -> int:
    """CLI entry point."""
    # Success messages use '✓' (U+2713); on Windows cp1252 stdout that crashes
    # with UnicodeEncodeError. Force UTF-8 (no-op on Unix). Standalone reconfigure
    # rather than importing java_codebase_rag._stdio — this dev script is stdlib-only.
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Sync agent artifacts from dev source to install_data"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify only without copying (for CI)"
    )
    args = parser.parse_args()

    try:
        return sync_all(check_only=args.check, repo_root=Path.cwd())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
