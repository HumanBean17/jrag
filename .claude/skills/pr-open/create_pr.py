#!/usr/bin/env python3
"""
Generate a comprehensive PR body and optionally open a PR.

Usage:
  .venv/bin/python .claude/skills/pr-open/create_pr.py --input .claude/skills/pr-open/pr-input.example.json --print-only
  .venv/bin/python .claude/skills/pr-open/create_pr.py --input /path/to/pr-input.json --create
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL_KEYS = {
    "title",
    "scope",
    "what_changed",
    "semantics_non_goals",
    "validation",
    "sentinel_checks",
    "manual_evidence",
    "out_of_scope_confirmed",
}


DEFAULT_DOD = [
    "All listed deliverables for this PR are shipped.",
    "Required lint/tests pass locally with recorded command output.",
    "Sentinel checks produce expected results.",
    "Only in-scope files are modified.",
    "PR description includes scope, validation, and manual evidence.",
    "PR targets `master` with agreed title and branch naming.",
]


def _as_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError(f"Field '{key}' must be a non-empty list of non-empty strings.")
    return value


def _validate_validation_block(payload: dict) -> dict:
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("Field 'validation' must be an object.")

    lint = _as_list(validation.get("lint"), "validation.lint")
    tests = _as_list(validation.get("tests"), "validation.tests")
    additional = validation.get("additional_checks", [])
    if additional:
        additional = _as_list(additional, "validation.additional_checks")
    else:
        additional = []
    return {"lint": lint, "tests": tests, "additional_checks": additional}


def _load_payload(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Input JSON must be an object.")

    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(data.keys()))
    if missing:
        raise ValueError(f"Missing required key(s): {', '.join(missing)}")

    for key in ("title", "scope"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Field '{key}' must be a non-empty string.")

    data["what_changed"] = _as_list(data.get("what_changed"), "what_changed")
    data["semantics_non_goals"] = _as_list(data.get("semantics_non_goals"), "semantics_non_goals")
    data["validation"] = _validate_validation_block(data)
    data["sentinel_checks"] = _as_list(data.get("sentinel_checks"), "sentinel_checks")
    data["manual_evidence"] = _as_list(data.get("manual_evidence"), "manual_evidence")
    data["out_of_scope_confirmed"] = _as_list(
        data.get("out_of_scope_confirmed"), "out_of_scope_confirmed"
    )

    dod = data.get("definition_of_done", DEFAULT_DOD)
    data["definition_of_done"] = _as_list(dod, "definition_of_done")
    base = data.get("base", "master")
    if not isinstance(base, str) or not base.strip():
        raise ValueError("Field 'base' must be a non-empty string when provided.")
    data["base"] = base.strip()
    data["draft"] = bool(data.get("draft", False))
    return data


def _bullet_lines(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _build_body(data: dict) -> str:
    lines: list[str] = []
    lines.append("## Scope")
    lines.append(data["scope"].strip())
    lines.append("")

    lines.append("## What Changed")
    lines.extend(_bullet_lines(data["what_changed"]))
    lines.append("")

    lines.append("## Semantics / Non-Goals")
    lines.extend(_bullet_lines(data["semantics_non_goals"]))
    lines.append("")

    lines.append("## Validation")
    lines.append("### Lint")
    lines.extend(_bullet_lines(data["validation"]["lint"]))
    lines.append("")
    lines.append("### Tests")
    lines.extend(_bullet_lines(data["validation"]["tests"]))
    if data["validation"]["additional_checks"]:
        lines.append("")
        lines.append("### Additional checks")
        lines.extend(_bullet_lines(data["validation"]["additional_checks"]))
    lines.append("")

    lines.append("## Sentinel checks")
    lines.extend(_bullet_lines(data["sentinel_checks"]))
    lines.append("")

    lines.append("## Manual evidence")
    lines.extend(_bullet_lines(data["manual_evidence"]))
    lines.append("")

    lines.append("## Out of Scope Confirmed")
    lines.append("Did not implement:")
    lines.extend(_bullet_lines(data["out_of_scope_confirmed"]))
    lines.append("")

    lines.append("## Definition of Done")
    lines.extend([f"- [ ] {item}" for item in data["definition_of_done"]])
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _open_pr(title: str, body: str, base: str, draft: bool) -> None:
    cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
    if draft:
        cmd.append("--draft")
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and open a comprehensive PR body.")
    parser.add_argument("--input", required=True, help="Path to JSON input payload.")
    parser.add_argument("--print-only", action="store_true", help="Print generated body only.")
    parser.add_argument("--create", action="store_true", help="Create PR with gh after generation.")
    args = parser.parse_args()

    if not args.print_only and not args.create:
        parser.error("Choose at least one action: --print-only and/or --create.")

    if args.print_only and args.create:
        parser.error("Use either --print-only or --create, not both.")

    try:
        payload = _load_payload(Path(args.input))
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    body = _build_body(payload)
    if args.print_only:
        print(body)
        return 0

    try:
        _open_pr(payload["title"], body, payload["base"], payload["draft"])
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI error path
        print(f"Failed to create PR (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
