#!/usr/bin/env python3
"""Pre-upload guard: assert every file in dist/ matches the version in pyproject.toml.

This is the safety net for ``twine upload dist/*``. PyPI uploads are permanent
(a version can be yanked, never overwritten), so a stale file left in ``dist/``
from a prior build — or a forgotten version bump — must be caught *before* the
upload, not after.

The target version is read from ``pyproject.toml`` on purpose: passing it in as
an argument would let the operator (or the publishing agent) hand in the wrong
number. Reading the single source of truth removes that failure mode.

Checked invariants (all must hold, else exit 1):
  - ``dist/`` is non-empty (i.e. a build actually ran).
  - Every file in ``dist/`` is ``java_codebase_rag-<target>-*``.
  - The wheel's METADATA ``Version:`` equals ``<target>``.

Usage:
    python scripts/check_dist_version.py [--dist dist] [--pyproject pyproject.toml]

Exit codes:
    0: dist/ is clean and matches pyproject.toml
    1: dist/ is empty, contains a foreign version, or METADATA is wrong
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from pathlib import Path


def read_pyproject_version(pyproject: Path) -> str:
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def filename_version(path: Path) -> str | None:
    """Extract the version from a dist filename.

    Examples:
        java_codebase_rag-0.10.0-py3-none-any.whl  -> 0.10.0
        java_codebase_rag-0.10.0.tar.gz            -> 0.10.0
    """
    m = re.match(r"^java_codebase_rag-(.+?)(?:-[^-]+-[^-]+-[^-]+\.whl|\.tar\.gz)$", path.name)
    return m.group(1) if m else None


def wheel_metadata_version(wheel: Path) -> str | None:
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith("METADATA"))
        for line in z.read(meta).decode().splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", type=Path, default=Path("dist"))
    ap.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = ap.parse_args()

    target = read_pyproject_version(args.pyproject)
    files = sorted(p for p in args.dist.iterdir() if p.is_file()) if args.dist.is_dir() else []

    if not files:
        print(f"✗ {args.dist}/ is empty — run `python -m build` first.", file=sys.stderr)
        return 1

    errors: list[str] = []
    wheel_seen = False
    for f in files:
        fv = filename_version(f)
        if fv is None:
            errors.append(f"{f.name}: not a java_codebase_rag dist artifact")
        elif fv != target:
            errors.append(f"{f.name}: filename version {fv!r} ≠ pyproject {target!r}")
        if f.suffix == ".whl":
            wheel_seen = True
            mv = wheel_metadata_version(f)
            if mv != target:
                errors.append(f"{f.name}: METADATA Version {mv!r} ≠ pyproject {target!r}")

    if not wheel_seen:
        errors.append("no wheel in dist/ — build incomplete?")

    if errors:
        for e in errors:
            print(f"✗ {e}", file=sys.stderr)
        print(f"\nRefusing to upload: dist/ does not match pyproject version {target!r}.", file=sys.stderr)
        return 1

    print(f"✓ dist/ clean: {len(files)} file(s), all version {target!r} "
          f"(matches pyproject.toml). Safe to `twine upload dist/*`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
