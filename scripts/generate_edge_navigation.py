#!/usr/bin/env python3
"""Generate docs/EDGE-NAVIGATION.md from java_ontology.EDGE_SCHEMA."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from java_ontology import (  # noqa: E402
    EDGE_SCHEMA,
    EdgeSpec,
    _COMPOSED_MEMBER_TYPE_TRAVERSAL,
)

_COMPOSED_MEMBER_EDGE_NAMES = frozenset({"EXPOSES", "DECLARES_CLIENT", "DECLARES_PRODUCER"})

_DEFAULT_OUT = _REPO_ROOT / "docs" / "EDGE-NAVIGATION.md"
_BANNER = (
    "# Edge Navigation Schema\n\n"
    "> **Generated from `java_ontology.EDGE_SCHEMA` — do not edit by hand.**\n"
    "> Regenerate: `.venv/bin/python scripts/generate_edge_navigation.py`\n"
)


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def _render_edge(spec: EdgeSpec) -> list[str]:
    lines = [
        f"## {spec.name}",
        "",
        f"**Endpoints**: `{spec.src} → {spec.dst}`",
        f"**Cardinality**: `{spec.cardinality}`",
        f"**Brownfield-resolver-sourced**: {_yes_no(spec.brownfield_resolver_sourced)}",
        f"**Member-only** (hints): {_yes_no(spec.member_only)}",
        "",
        f"**Purpose**: {spec.purpose}",
        "",
    ]
    if spec.attrs:
        lines.append("**Attributes**:")
        lines.append("")
        for attr in spec.attrs:
            lines.append(f"- `{attr.name}` (`{attr.kuzu_type}`) — {attr.purpose}")
        lines.append("")
    else:
        lines.append("**Attributes**: _(none)_")
        lines.append("")
    if spec.typical_traversals:
        lines.append("**Typical traversals**:")
        lines.append("")
        for role, traversal in spec.typical_traversals.items():
            if role == "type_subject" and spec.name in _COMPOSED_MEMBER_EDGE_NAMES:
                composed = _COMPOSED_MEMBER_TYPE_TRAVERSAL.format(
                    id="{id}", direction="{direction}", edge=spec.name,
                )
                traversal = f"{composed} — or {traversal}"
            lines.append(f"- `{role}`: {traversal}")
        lines.append("")
    return lines


def generate_markdown() -> str:
    parts = [_BANNER, "## Summary", "", "| Edge | From | To | Cardinality | Brownfield-resolver-sourced | Member-only |", "| --- | --- | --- | --- | --- | --- |"]
    for spec in EDGE_SCHEMA.values():
        parts.append(
            f"| {spec.name} | {spec.src} | {spec.dst} | {spec.cardinality} | "
            f"{_yes_no(spec.brownfield_resolver_sourced)} | {_yes_no(spec.member_only)} |"
        )
    parts.append("")
    for spec in EDGE_SCHEMA.values():
        parts.extend(_render_edge(spec))
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output path (default: {_DEFAULT_OUT.relative_to(_REPO_ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if committed doc differs from generator output",
    )
    args = parser.parse_args()
    content = generate_markdown()
    if args.check:
        if not args.out.is_file():
            print(f"missing {args.out}", file=sys.stderr)
            return 1
        committed = args.out.read_text(encoding="utf-8")
        if committed != content:
            print(f"stale: {args.out} (run scripts/generate_edge_navigation.py)", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
