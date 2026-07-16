"""Lightweight, query-time hints from chunk text — no AST / re-index required."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ChunkHints:
    """Heuristic metadata derived from a chunk's text."""

    primary_type_hint: str | None = None
    """First top-level ``class`` / ``interface`` / ``enum`` / ``record`` name in the chunk."""

    import_heavy: bool = False
    """True when most lines are ``import`` statements (low semantic density)."""


_JAVA_TYPE = re.compile(
    r"\b(?:public\s+|private\s+|protected\s+|sealed\s+|final\s+|abstract\s+|static\s+)*"
    r"(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
)

# Kotlin type declarations. Covers ``class`` / ``interface`` / ``object`` /
# ``enum class`` plus the modifiers Kotlin uses (``internal`` / ``open`` / etc.).
# ``object`` is the Kotlin-specific kind the Java regex misses; top-level
# ``fun`` is intentionally NOT a type declaration (no name to pin a type to).
_KOTLIN_TYPE = re.compile(
    r"\b(?:public\s+|private\s+|protected\s+|internal\s+|"
    r"final\s+|open\s+|abstract\s+|sealed\s+|data\s+)*"
    r"(?:class|interface|object|enum\s+class)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def analyze_chunk(text: str | None, *, language: str, kind: str) -> ChunkHints:
    if not text or not text.strip():
        return ChunkHints()

    lines = text.strip().split("\n")
    n = len(lines)
    lang = (language or "").lower()
    # Kotlin chunks live in the java LanceDB table (``kind == "java"``), so the
    # ``language`` field — not ``kind`` — is what distinguishes them. Detect
    # Kotlin first so the Kotlin regex wins for ``object`` / Kotlin modifiers.
    is_kotlin = lang == "kotlin"
    is_java = not is_kotlin and (kind == "java" or lang == "java")

    # Both Java and Kotlin use ``import <pkg.Type>`` lines (Java ends the line
    # with ``;``, Kotlin does not), so the import-density heuristic matches on
    # the ``import `` prefix only — the trailing-``;`` difference is irrelevant.
    import_heavy = False
    if (is_java or is_kotlin) and n >= 3:
        imp = sum(1 for L in lines if L.lstrip().startswith("import "))
        import_heavy = imp / n >= 0.55

    primary: str | None = None
    head = "\n".join(lines[: min(80, n)])
    if is_kotlin:
        m = _KOTLIN_TYPE.search(head)
        if m:
            primary = m.group(1)
    elif is_java:
        m = _JAVA_TYPE.search(head)
        if m:
            primary = m.group(1)

    return ChunkHints(
        primary_type_hint=primary,
        import_heavy=import_heavy,
    )


def looks_like_code_identifier(query: str) -> bool:
    """PascalCase type name or SCREAMING_SNAKE constant — good candidates for FTS hybrid."""
    q = query.strip()
    if not q or len(q) < 2 or len(q) > 200:
        return False
    if re.fullmatch(r"[A-Z][a-zA-Z0-9_]*", q):
        return True
    if "_" in q and re.fullmatch(r"[A-Z][A-Z0-9_]*", q):
        return True
    return False
