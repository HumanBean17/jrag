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

Before routing, :func:`maybe_warn_legacy_alias` runs once. It is a no-op
unless the tool was invoked through a legacy alias in an interactive context
(see :mod:`java_codebase_rag._deprecation`); in tests ``sys.stderr`` is not a
TTY so it stays silent.
"""
from __future__ import annotations

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
    """Unified ``jrag`` entry point: warn, pick target, delegate.

    The chosen target's ``_console_script_main`` does its own startup and
    terminates the process (via ``os._exit``); we let ``SystemExit`` propagate
    rather than reimplementing startup here.
    """
    maybe_warn_legacy_alias()
    target = _choose_target()
    target()


if __name__ == "__main__":  # pragma: no cover - direct invocation
    _console_script_main()
