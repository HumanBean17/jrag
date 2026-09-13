"""Import lint — the usage package is local-only, network-free by construction.

The repo's observability stance is "strictly local, no network, ever" (spec
2026-09-13). This guard makes the promise enforceable: no module anywhere
under ``java_codebase_rag.usage`` (including future subpackages) may import
anything outside a fixed allowlist of stdlib non-network roots plus the
package's own siblings. Threat model: accidental drift by a future
contributor — dynamic ``__import__`` calls are out of scope (this is a lint,
not tamper-proofing).
"""

from __future__ import annotations

import ast
from pathlib import Path

USAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "java_codebase_rag" / "usage"

ALLOWED_STDLIB = {
    "__future__", "json", "math", "os", "sys", "time", "hashlib", "pathlib",
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


def _import_roots(tree: ast.AST) -> set[tuple[str, int, str]]:
    """(module_or_name, lineno, kind) for absolute imports and relatives.

    The package convention is absolute imports; any relative import
    (``from . import x`` records ``""``, ``from .paths import x`` records
    ``"paths"``) is flagged so a future subpackage reaching sideways gets a
    human look instead of silently bypassing the absolute-name allowlist.
    """
    found: set[tuple[str, int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno, "import"))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                found.add((node.module or "", node.lineno, "relative"))
            elif node.module:
                found.add((node.module, node.lineno, "from"))
    return found


def test_usage_package_imports_nothing_network_capable() -> None:
    files = sorted(USAGE_DIR.rglob("*.py"))
    assert files, f"usage package missing at {USAGE_DIR}"
    violations: list[str] = []
    for path in files:
        rel = path.relative_to(USAGE_DIR)
        tree = ast.parse(path.read_text())
        for root, lineno, kind in _import_roots(tree):
            top = root.split(".")[0]
            if root in ALLOWED_LOCAL:
                continue
            if kind != "relative" and top in ALLOWED_STDLIB:
                continue
            violations.append(f"{rel}:{lineno}: unexpected {kind} import {root!r}")
    assert not violations, (
        "usage/ is local-only by design (network-free, stdlib non-network "
        "only, absolute imports):\n" + "\n".join(violations)
    )


def test_no_forbidden_root_appears_in_allowlist() -> None:
    # Guard the guard: the allowlist itself must never grow a network root.
    for marker in FORBIDDEN_MARKERS:
        assert marker not in ALLOWED_STDLIB
