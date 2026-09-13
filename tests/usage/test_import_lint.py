"""Import lint — the usage package is local-only, network-free by construction.

The repo's observability stance is "strictly local, no network, ever" (spec
2026-09-13). This guard makes the promise enforceable: no module under
``java_codebase_rag.usage`` may import anything outside a fixed allowlist of
stdlib non-network roots. Third-party and java_codebase_rag.* imports are
fine only for the allowed internal siblings listed below (paths/events/
writer/summarize — none of which import anything else).
"""

from __future__ import annotations

import ast
from pathlib import Path

USAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "java_codebase_rag" / "usage"

ALLOWED_STDLIB = {
    "__future__", "json", "os", "sys", "time", "hashlib", "pathlib",
    "datetime", "collections", "statistics", "typing", "dataclasses",
}
ALLOWED_LOCAL = {
    "java_codebase_rag.usage",  # the package itself (sibling imports)
    "java_codebase_rag.usage.paths",
    "java_codebase_rag.usage.events",
    "java_codebase_rag.usage.writer",
    "java_codebase_rag.usage.summarize",
}

FORBIDDEN_MARKERS = ("socket", "http", "urllib", "requests", "subprocess",
                     "asyncio", "ftplib", "smtplib", "xmlrpc")


def _import_roots(tree: ast.Ast) -> set[tuple[str, int]]:  # type: ignore[name-defined]
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add((node.module, node.lineno))
    return found


def test_usage_package_imports_nothing_network_capable() -> None:
    files = sorted(USAGE_DIR.glob("*.py"))
    assert files, f"usage package missing at {USAGE_DIR}"
    violations: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text())
        for root, lineno in _import_roots(tree):
            top = root.split(".")[0]
            if root in ALLOWED_LOCAL:
                continue
            if top in ALLOWED_STDLIB:
                continue
            violations.append(f"{path.name}:{lineno}: unexpected import {root!r}")
    assert not violations, (
        "usage/ is local-only by design (network-free, stdlib non-network "
        "only):\n" + "\n".join(violations)
    )


def test_no_forbidden_root_appears_in_allowlist() -> None:
    # Guard the guard: the allowlist itself must never grow a network root.
    for marker in FORBIDDEN_MARKERS:
        assert marker not in ALLOWED_STDLIB
