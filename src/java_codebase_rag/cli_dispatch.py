"""Unified ``jrag`` CLI dispatcher (Task 2 of the rename).

The tool exposes two disjoint CLI surfaces that this module unifies behind a
single ``jrag`` console-script entry point:

* :mod:`java_codebase_rag.cli` — operator / lifecycle verbs (``init``,
  ``install``, ``update``, ``increment``, ``reprocess``, ``erase``, ``meta``,
  ``tables``, ``diagnose-ignore``, ``analyze-pr``, ``unresolved-calls``).
* :mod:`java_codebase_rag.jrag` — agent verbs (``find``, ``search``,
  ``inspect``, ``callers``, ``callees``, ``hierarchy``, ``watch``, ``status``,
  … — 32 verbs total).

Both modules already expose a zero-argument ``_console_script_main`` that
reads ``sys.argv`` itself, performs its own startup (``raise_fd_limit``, UTF-8
stdio, error handling), and calls ``sys.exit`` (via ``os._exit``). This
dispatcher does NOT reimplement argparse, fd-limit, or error handling: it picks
the target module and forwards. Letting the chosen ``_console_script_main`` run
unwinds as normal, including its ``SystemExit`` / ``os._exit``.

Routing contract
----------------
For an invocation ``[argv0, *args]``:

* If any token in ``args`` is a member of :data:`OPERATOR_VERBS`, route to
  ``cli._console_script_main``.
* Else if any token is a member of :data:`AGENT_VERBS`, route to
  ``jrag._console_script_main``.
* Otherwise fall back to the identity default: the basename of ``argv0``.
  ``jrag`` → ``jrag._console_script_main``; anything else (including the
  legacy ``java-codebase-rag`` alias) → ``cli._console_script_main``.

The "first matching verb" rule scans left-to-right and routes by *that* verb's
set. Operator and agent verb sets are disjoint, so the scan is unambiguous.

Known non-goal: if a global flag's value token happens to equal a verb name
(e.g. a hypothetical ``--config find``), routing may follow the verb heuristic.
Acceptable edge case — the alternative (a flag-aware parser) would duplicate
argparse here, which the design explicitly forbids.

Unified help contract
---------------------
For the canonical ``jrag`` identity, a top-level help request (``jrag``,
``jrag --help``, ``jrag -h`` — i.e. ``-h``/``--help`` appears before any
recognized verb, or no tokens at all) is served by :func:`_print_unified_help`:
it prints the agent parser's full help (agent verbs + global flags) and
appends a clearly labeled "Operator commands" section listing the operator
verbs with one-line descriptions sourced from :func:`cli.build_parser`'s
subparser choices. This makes ``jrag --help`` the single discovery surface for
all 11+32 verbs, satisfying the unification design contract.

The legacy ``java-codebase-rag`` alias does NOT get unified help: it keeps its
pre-rename behavior (operator-only parser) for backward compatibility, since
operators with shell scripts that parse ``java-codebase-rag --help`` output
must not see new verbs appear under that alias.

Before routing, :func:`maybe_warn_legacy_alias` runs once. It is a no-op
unless the tool was invoked through a legacy alias in an interactive context
(see :mod:`java_codebase_rag._deprecation`); in tests ``sys.stderr`` is not a
TTY so it stays silent.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable

from java_codebase_rag import cli as _cli_mod
from java_codebase_rag import jrag as _jrag_mod
from java_codebase_rag._deprecation import maybe_warn_legacy_alias

__all__ = [
    "OPERATOR_VERBS",
    "AGENT_VERBS",
    "_console_script_main",
]

#: Tokens that request top-level help. argparse's default help action accepts
#: both ``-h`` and ``--help`` (neither parser disables ``add_help``).
_HELP_TOKENS: frozenset[str] = frozenset({"-h", "--help"})


#: Operator / lifecycle verbs. Must match ``cli.build_parser()``'s registered
#: top-level subcommand choice names (drift-guarded by the test suite).
#:
#: Note: the rename design spec lists ``diagnose`` and ``unresolved`` here, but
#: the parser actually registers ``diagnose-ignore`` and ``unresolved-calls``
#: (the spec abbreviated the names). The drift-guard test pins the dispatcher
#: to the parser's truth, so the hyphenated names are what we ship.
OPERATOR_VERBS: frozenset[str] = frozenset(
    {
        "init",
        "install",
        "update",
        "increment",
        "reprocess",
        "erase",
        "meta",
        "tables",
        "diagnose-ignore",
        "analyze-pr",
        "unresolved-calls",
    }
)

#: Agent verbs. Must match ``jrag.build_parser()``'s registered top-level
#: subcommand choice names (drift-guarded by the test suite).
AGENT_VERBS: frozenset[str] = frozenset(
    {
        "find",
        "search",
        "inspect",
        "callers",
        "callees",
        "hierarchy",
        "implementations",
        "subclasses",
        "overrides",
        "overridden-by",
        "dependents",
        "impact",
        "decompose",
        "flow",
        "dependencies",
        "connection",
        "outline",
        "imports",
        "microservices",
        "map",
        "conventions",
        "overview",
        "vocab-index",
        "watch",
        "status",
        "http-routes",
        "http-clients",
        "producers",
        "topics",
        "jobs",
        "listeners",
        "entities",
    }
)


def _invoked_program_basename() -> str:
    """Basename of ``sys.argv[0]``, or empty string if argv is empty."""
    argv = sys.argv
    if not argv:
        return ""
    return os.path.basename(argv[0])


def _is_unified_help_request() -> bool:
    """True iff argv is a top-level help/no-args request.

    Returns True when ``sys.argv[1:]`` is empty (no-args) OR contains
    ``-h``/``--help`` as the first recognized token (i.e. before any verb).
    Returns False as soon as a verb token is seen, so verb-specific help
    (``jrag install --help``, ``jrag find --help``) still routes to the
    verb's own parser. Also returns False for non-help requests such as
    ``jrag --version`` (no help token, no verb — falls through to the
    identity default).
    """
    rest = sys.argv[1:] if len(sys.argv) > 1 else []
    if not rest:
        return True
    for token in rest:
        if token in OPERATOR_VERBS or token in AGENT_VERBS:
            return False
        if token in _HELP_TOKENS:
            return True
    return False


def _operator_subcommand_helps() -> list[tuple[str, str]]:
    """Return ``[(verb, one_line_help), ...]`` for operator verbs, parser order.

    Pulls the ``(metavar, help)`` of each ``_ChoicesPseudoAction`` registered
    on ``cli.build_parser()``'s top-level subparsers action. Parser order
    (lifecycle flow: ``init``, ``install``, ``update``, ...) is preserved
    rather than sorting alphabetically.
    """
    parser = _cli_mod.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return [(ca.metavar, ca.help or "") for ca in action._choices_actions]
    return []


def _print_unified_help(stream=None) -> None:
    """Print the canonical ``jrag`` unified help: agent verbs + operator verbs.

    Prints the agent parser's full help (which already lists the 32 agent
    verbs with one-line descriptions plus the global ``-h``/``--version``
    options and the descriptive epilog), then appends a clearly labeled
    "Operator commands (indexing & maintenance)" section listing the 11
    operator verbs with their one-line descriptions sourced from
    :func:`cli.build_parser`'s subparser choices.

    Writes to ``stream`` (default ``sys.stdout``) and returns without exiting
    — the caller (``_console_script_main``) returns to the pip wrapper, which
    exits 0.
    """
    target = stream if stream is not None else sys.stdout
    agent_parser = _jrag_mod.build_parser()
    agent_parser.print_help(target)
    target.write("\n")
    target.write(
        "Operator commands (indexing & maintenance; run `jrag <command> --help` "
        "for details):\n"
    )
    for name, help_text in _operator_subcommand_helps():
        target.write(f"    {name:<20} {help_text}\n")


def _choose_target() -> Callable[[], None]:
    """Pick the ``_console_script_main`` to delegate to.

    Scans ``sys.argv[1:]`` left-to-right for the first token that is a member
    of ``OPERATOR_VERBS`` or ``AGENT_VERBS``; routes by that set. If no token
    matches, routes by identity default (``jrag`` basename → jrag; anything
    else → cli, including the legacy ``java-codebase-rag`` alias).
    """
    rest = sys.argv[1:] if len(sys.argv) > 1 else []
    for token in rest:
        if token in OPERATOR_VERBS:
            return _cli_mod._console_script_main
        if token in AGENT_VERBS:
            return _jrag_mod._console_script_main

    # No verb token: identity default by argv[0] basename.
    if _invoked_program_basename() == "jrag":
        return _jrag_mod._console_script_main
    return _cli_mod._console_script_main


def _console_script_main() -> None:
    """Unified ``jrag`` entry point: warn, maybe serve unified help, delegate.

    For the canonical ``jrag`` identity with a top-level help/no-args request,
    print the unified help (agent verbs + operator verbs) and return — the pip
    wrapper then exits 0. This is the discovery surface for all verbs.

    Otherwise: pick the target module and forward. The chosen target's
    ``_console_script_main`` does its own startup and terminates the process
    (via ``os._exit``); we let ``SystemExit`` propagate rather than
    reimplementing startup here.
    """
    maybe_warn_legacy_alias()
    if _invoked_program_basename() == "jrag" and _is_unified_help_request():
        _print_unified_help()
        return
    target = _choose_target()
    target()


if __name__ == "__main__":  # pragma: no cover - direct invocation
    _console_script_main()
