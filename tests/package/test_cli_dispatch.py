"""Routing + drift-guard tests for the unified ``jrag`` CLI dispatcher.

``java_codebase_rag.cli_dispatch._console_script_main`` is the single entry point
behind the ``jrag`` console script (Task 2 of the rename). It picks one of the
two existing zero-arg console-script mains — ``cli._console_script_main``
(operator/lifecycle verbs) or ``jrag._console_script_main`` (agent verbs) —
based on the first matching verb in ``sys.argv[1:]``, falling back to an
identity default derived from ``sys.argv[0]`` basename.

These tests:

* swap the two real ``_console_script_main`` callables for recording stubs (the
  real ones call ``os._exit`` after running, so they cannot be invoked in
  process), set ``sys.argv`` per case, and assert the dispatcher routes to the
  right target with ``sys.argv`` unchanged.
* assert the exported ``OPERATOR_VERBS`` / ``AGENT_VERBS`` frozensets exactly
  match the top-level subcommand choice names registered on
  ``cli.build_parser()`` / ``jrag.build_parser()``. This is the drift guard: if
  a verb is added to a parser but not to the dispatcher, the test fails.
"""
from __future__ import annotations

import argparse
import sys

import pytest


# --- Routing helpers -------------------------------------------------------


def _install_recording_stubs(monkeypatch):
    """Replace ``cli._console_script_main`` and ``jrag._console_script_main``.

    Returns ``(cli_calls, jrag_calls)`` — the lists the stubs append each
    invocation's ``sys.argv`` snapshot to. Real mains call ``os._exit``; these
    stubs just record, so the dispatcher can be exercised in process.
    """
    from java_codebase_rag import cli as cli_mod
    from java_codebase_rag import jrag as jrag_mod

    cli_calls: list[list[str]] = []
    jrag_calls: list[list[str]] = []

    def fake_cli_main() -> None:
        cli_calls.append(list(sys.argv))

    def fake_jrag_main() -> None:
        jrag_calls.append(list(sys.argv))

    monkeypatch.setattr(cli_mod, "_console_script_main", fake_cli_main)
    monkeypatch.setattr(jrag_mod, "_console_script_main", fake_jrag_main)
    return cli_calls, jrag_calls


def _run_dispatcher(monkeypatch, argv):
    """Set ``sys.argv``, install stubs, invoke the dispatcher.

    Returns ``(cli_calls, jrag_calls)`` so the caller can assert routing.
    """
    monkeypatch.setattr(sys, "argv", list(argv))
    cli_calls, jrag_calls = _install_recording_stubs(monkeypatch)
    # Imported lazily so the module-not-found failure mode of step 2 is the
    # literal ModuleNotFoundError, not an import-time error from this test file.
    from java_codebase_rag import cli_dispatch

    cli_dispatch._console_script_main()
    return cli_calls, jrag_calls


# --- Routing contract (12 cases from the task brief) -----------------------
#
# Each tuple is (argv, expected_target) where expected_target is "cli" or
# "jrag". argv[0] is the program name (basename used for identity default);
# argv[1:] is what the dispatcher scans for the first verb token.

ROUTING_CASES: list[tuple[list[str], str]] = [
    (["jrag", "find", "ChatController"], "jrag"),  # agent verb
    (["jrag", "install"], "cli"),  # operator verb under canonical name (unification)
    (["jrag", "init"], "cli"),
    (["jrag", "--version"], "jrag"),  # no subcommand -> identity default
    (["jrag", "--help"], "jrag"),
    (["jrag"], "jrag"),  # no args
    (["jrag", "bogus-verb"], "jrag"),  # unknown -> identity default; jrag parser errors
    (["java-codebase-rag", "install"], "cli"),
    (["java-codebase-rag", "find", "X"], "jrag"),  # alias gains agent verbs
    (["java-codebase-rag", "--version"], "cli"),  # alias identity default
    (["java-codebase-rag"], "cli"),
    (["jrag", "--index-dir", "/tmp/x", "find", "Y"], "jrag"),  # global flag + value before verb
]


@pytest.mark.parametrize("argv,expected", ROUTING_CASES)
def test_routes_to_expected_target(monkeypatch, argv, expected):
    cli_calls, jrag_calls = _run_dispatcher(monkeypatch, argv)
    if expected == "cli":
        assert len(cli_calls) == 1, f"expected cli route; got cli={cli_calls} jrag={jrag_calls}"
        assert not jrag_calls, f"expected no jrag route; got jrag={jrag_calls}"
        assert cli_calls[0] == argv, f"sys.argv changed en route: {cli_calls[0]!r} != {argv!r}"
    else:
        assert len(jrag_calls) == 1, f"expected jrag route; got jrag={jrag_calls} cli={cli_calls}"
        assert not cli_calls, f"expected no cli route; got cli={cli_calls}"
        assert jrag_calls[0] == argv, f"sys.argv changed en route: {jrag_calls[0]!r} != {argv!r}"


# --- Drift guard: verb frozensets must match the parsers' subcommands ------


def _top_level_subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    """Collect the top-level subcommand choice names registered on ``parser``.

    Walks the parser's actions for the ``_SubParsersAction`` and returns its
    ``choices`` keys. Returns an empty set if the parser has no subparsers
    (which would itself be a regression worth failing on).
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    return set()


def test_operator_verbs_match_cli_parser_subcommands():
    """OPERATOR_VERBS must equal cli.build_parser()'s registered subcommand names.

    Drift guard: if a verb is added/removed/renamed in ``cli.build_parser()``
    but not in ``cli_dispatch.OPERATOR_VERBS``, this assertion fails and the
    dispatcher's routing table is forced back in sync with the parser.
    """
    from java_codebase_rag.cli import build_parser as cli_build_parser
    from java_codebase_rag.cli_dispatch import OPERATOR_VERBS

    actual = _top_level_subcommand_names(cli_build_parser())
    assert OPERATOR_VERBS == actual, (
        f"OPERATOR_VERBS drift: dispatcher={sorted(OPERATOR_VERBS)!r} "
        f"parser={sorted(actual)!r}"
    )


def test_agent_verbs_match_jrag_parser_subcommands():
    """AGENT_VERBS must equal jrag.build_parser()'s registered subcommand names.

    Drift guard for the agent side; see the operator test above.
    """
    from java_codebase_rag.jrag import build_parser as jrag_build_parser
    from java_codebase_rag.cli_dispatch import AGENT_VERBS

    actual = _top_level_subcommand_names(jrag_build_parser())
    assert AGENT_VERBS == actual, (
        f"AGENT_VERBS drift: dispatcher={sorted(AGENT_VERBS)!r} "
        f"parser={sorted(actual)!r}"
    )


def test_operator_and_agent_verbs_are_disjoint():
    """Routing by set-membership requires the two verb sets to be disjoint.

    A non-disjoint overlap would make the operator-vs-agent decision ambiguous
    for the colliding verb. This is a hard contract from the rename design.
    """
    from java_codebase_rag.cli_dispatch import AGENT_VERBS, OPERATOR_VERBS

    overlap = OPERATOR_VERBS & AGENT_VERBS
    assert not overlap, f"verb sets overlap: {sorted(overlap)!r}"


def test_verb_frozensets_are_frozen():
    """The exported sets must be ``frozenset`` so they cannot be mutated."""
    from java_codebase_rag.cli_dispatch import AGENT_VERBS, OPERATOR_VERBS

    assert isinstance(OPERATOR_VERBS, frozenset), type(OPERATOR_VERBS)
    assert isinstance(AGENT_VERBS, frozenset), type(AGENT_VERBS)
