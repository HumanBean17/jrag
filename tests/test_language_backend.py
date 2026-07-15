"""Tests for the language-dispatch seam (`ast.language`).

Task 1 establishes the registry with Java only; Kotlin is added in later
tasks. These tests pin the contract that downstream language dispatch relies on.
"""
from __future__ import annotations

import pytest

from java_codebase_rag.ast.ast_java import JavaFileAst
from java_codebase_rag.ast.language import (
    FileAst,
    JavaBackend,
    KNOWN_LANGUAGE_IDS,
    LANG_BACKENDS,
    backend_for,
)


def test_backend_for_java_file_returns_java_backend() -> None:
    """(a) `.java` suffix resolves to the registered Java backend."""
    backend = backend_for("src/Foo.java")
    assert backend is not None
    assert backend.language_id == "java"


def test_backend_for_kotlin_file_returns_none() -> None:
    """(b) `.kt` is not registered yet — Kotlin lands in a later task."""
    assert backend_for("Foo.kt") is None


def test_backend_for_unrelated_suffix_returns_none() -> None:
    """(c) Non-source files never match a language backend."""
    assert backend_for("README.md") is None


def test_java_backend_parse_sets_language() -> None:
    """(d) `JavaBackend.parse` delegates to `parse_java` and tags language."""
    ast = JavaBackend().parse(b"package x; class F {}", filename="F.java")
    assert isinstance(ast, JavaFileAst)
    assert ast.language == "java"
    # Delegated parser actually parsed the unit, not just an empty stub.
    assert any(t.name == "F" for t in ast.all_types)


def test_javafileast_rejects_unknown_language() -> None:
    """(e) `language` must be one of the registered language ids."""
    with pytest.raises(ValueError):
        JavaFileAst(
            package="x",
            imports=[],
            wildcard_imports=[],
            explicit_imports={},
            top_level_types=[],
            all_types=[],
            language="nope",
        )


def test_javafileast_language_is_required() -> None:
    """(f) `language` has no default — omitting it is a TypeError."""
    with pytest.raises(TypeError):
        JavaFileAst(
            package="x",
            imports=[],
            wildcard_imports=[],
            explicit_imports={},
            top_level_types=[],
            all_types=[],
        )  # type: ignore[call-arg]


def test_fileast_alias_is_javafileast() -> None:
    """(g) `FileAst` is a direct alias for `JavaFileAst` (single-language era)."""
    assert FileAst is JavaFileAst


def test_registry_and_known_ids() -> None:
    """Registry invariants: only Java registered; ids derived from keys."""
    assert set(LANG_BACKENDS.keys()) == {"java"}
    assert KNOWN_LANGUAGE_IDS == frozenset({"java"})
    assert LANG_BACKENDS["java"].language_id == "java"
    assert ".java" in LANG_BACKENDS["java"].suffixes
