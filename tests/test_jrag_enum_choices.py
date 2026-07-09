"""Cross-check jrag's hardcoded enum ``choices=`` constants against the
canonical ontology literals (``mcp_v2`` / ``java_ontology``), so the constants
in ``java_codebase_rag/jrag.py`` can't silently drift (they're hardcoded to
keep ``jrag --help`` fast — see the comment above ``_ROLE_CHOICES`` there).

Also covers the ``choices=`` wiring and the case-normalizing ``type=`` on
``--role`` / ``--exclude-role`` / ``--java-kind`` / ``--framework`` /
``--capability``: flexible casing must normalize to the stored literal, and
typos must be rejected with the valid set.
"""
from __future__ import annotations

import argparse
from typing import get_args

import pytest

from java_codebase_rag.graph import java_ontology
from java_codebase_rag.mcp import mcp_v2

from java_codebase_rag.jrag import (
    _CAPABILITY_CHOICES,
    _FRAMEWORK_CHOICES,
    _JAVA_KIND_CHOICES,
    _ROLE_CHOICES,
    build_parser,
)


def test_role_choices_match_canonical_literal() -> None:
    assert set(_ROLE_CHOICES) == set(get_args(mcp_v2.Role))


def test_java_kind_choices_match_canonical_literal() -> None:
    assert set(_JAVA_KIND_CHOICES) == set(get_args(mcp_v2.DeclarationSymbolKind))


def test_framework_choices_match_canonical_literal() -> None:
    assert set(_FRAMEWORK_CHOICES) == {f for f in get_args(mcp_v2.Framework) if f}


def test_capability_choices_match_canonical_set() -> None:
    assert set(_CAPABILITY_CHOICES) == set(java_ontology.VALID_CAPABILITIES)


@pytest.mark.parametrize(
    "argv,attr,expected",
    [
        (["find", "--role", "controller"], "role", "CONTROLLER"),
        (["find", "--role", "CONTROLLER"], "role", "CONTROLLER"),
        (["find", "--exclude-role", "dto"], "exclude_role", "DTO"),
        (["find", "--capability", "scheduled-task"], "capability", "SCHEDULED_TASK"),
        (["find", "--java-kind", "Interface"], "java_kind", "interface"),
        (["http-routes", "--framework", "Spring-MVC"], "framework", "spring_mvc"),
        (["search", "payment", "--role", "service"], "role", "SERVICE"),
    ],
)
def test_enum_flags_normalize_flexible_casing(argv, attr, expected) -> None:
    ns = build_parser().parse_args(argv)
    assert getattr(ns, attr) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["find", "--role", "CONROLER"],
        ["find", "--capability", "nope"],
        ["find", "--java-kind", "klass"],
        ["http-routes", "--framework", "django"],
        ["search", "--exclude-role", "WIDGET"],
    ],
)
def test_enum_flags_reject_invalid_values(argv) -> None:
    # build_parser's error() raises argparse.ArgumentError (not SystemExit).
    with pytest.raises(argparse.ArgumentError):
        build_parser().parse_args(argv)
