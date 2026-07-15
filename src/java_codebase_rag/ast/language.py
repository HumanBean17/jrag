"""Language-dispatch seam for AST extraction.

A registry of ``LanguageBackend`` objects keyed by ``language_id``; each backend
owns the source suffixes it claims (e.g. ``.java``) and a ``parse`` entry point
returning a ``JavaFileAst`` (the single AST shape today; a Kotlin backend lands
in later tasks and will reuse this surface).

Single-language era: only Java is registered, so ``FileAst`` is a direct alias
for ``JavaFileAst``. When a second language arrives the alias is revisited.

Import cycle note: ``ast_java`` defines ``JavaFileAst`` whose ``__post_init__``
validates against ``KNOWN_LANGUAGE_IDS`` (defined here). To keep the cycle
one-directional, ``ast_java`` does NOT import this module at top level — it
imports ``KNOWN_LANGUAGE_IDS`` lazily inside ``__post_init__``. This module
freely imports from ``ast_java``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from java_codebase_rag.ast.ast_java import JavaFileAst, parse_java

__all__ = [
    "LanguageBackend",
    "JavaBackend",
    "LANG_BACKENDS",
    "KNOWN_LANGUAGE_IDS",
    "backend_for",
    "FileAst",
]


@runtime_checkable
class LanguageBackend(Protocol):
    """A pluggable parser backend for one source language."""

    language_id: str
    suffixes: tuple[str, ...]

    def parse(
        self,
        source: bytes | str,
        *,
        filename: str,
        verbose: bool = False,
    ) -> JavaFileAst: ...


class JavaBackend:
    """The Java backend — delegates to the existing tree-sitter ``parse_java``."""

    language_id: str = "java"
    suffixes: tuple[str, ...] = (".java",)

    def parse(
        self, source: bytes | str, *, filename: str = "", verbose: bool = False
    ) -> JavaFileAst:
        return parse_java(source, filename=filename, verbose=verbose)


# Registry: language_id -> backend. Later tasks append a Kotlin entry here.
LANG_BACKENDS: dict[str, LanguageBackend] = {
    "java": JavaBackend(),
}

# Derived from the registry so the two never drift apart.
KNOWN_LANGUAGE_IDS: frozenset[str] = frozenset(LANG_BACKENDS.keys())


def backend_for(path: Path | str) -> LanguageBackend | None:
    """Return the first backend whose ``suffixes`` contain ``path``'s suffix.

    Suffix matching is case-sensitive against ``Path(path).suffix``. Returns
    ``None`` when no backend claims the file (e.g. ``.kt`` today, or ``.md``).
    """
    suffix = Path(path).suffix
    for backend in LANG_BACKENDS.values():
        if suffix in backend.suffixes:
            return backend
    return None


# Single-language alias. Re-exported so downstream call sites can target the
# generic name and stay source-stable when a Kotlin AST shape is introduced.
FileAst = JavaFileAst
