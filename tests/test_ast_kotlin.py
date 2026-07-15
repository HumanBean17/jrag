"""Smoke test for the ``tree-sitter-kotlin`` grammar dependency.

Later Kotlin-extractor tasks (parse_kotlin, etc.) extend this file; for now it
only gates that the grammar wheel installs and imports on every supported
platform (including Intel-Mac graph-only installs).
"""
from __future__ import annotations

import tree_sitter_kotlin


def test_grammar_imports_and_exposes_language() -> None:
    """``tree_sitter_kotlin.language()`` must return a truthy language capsule."""
    language = tree_sitter_kotlin.language()
    assert language, "tree_sitter_kotlin.language() returned a falsy value"
