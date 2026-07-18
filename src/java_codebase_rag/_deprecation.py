"""Legacy-alias deprecation notice for the ``jrag`` rename.

The tool is being renamed from ``java-codebase-rag`` to ``jrag``. The legacy
command aliases (``java-codebase-rag`` and ``java-codebase-rag-mcp``) continue
to work; when invoked through one of them in an interactive context, this
helper emits a single-line notice pointing the operator at the new name.

Suppression rule
----------------
The notice is suppressed when **either** of the following holds:

* ``$JRAG_NO_DEPRECATION`` is present and non-empty — any non-empty value
  suppresses (so ``"1"``, ``"0"``, ``"false"``, ``"yes"`` all suppress; the
  empty string and an unset variable do not). The simpler "present and
  non-empty" rule is preferred over parsing truthy values so that operators
  can shut the notice up with whatever value is easiest to reach for.
* ``sys.stderr.isatty()`` is false (or ``sys.stderr`` lacks ``isatty``) — i.e.
  non-interactive contexts: piped, redirected, or captured stderr. Under real
  MCP use stderr is not a TTY, so the call is silent in production; it exists
  for the rare human-debug case.

This module is stdlib-only and import-light: it runs at MCP-server startup and
before ``--help``, so it must not pull in any backend (cli, search, mcp) code.
"""
from __future__ import annotations

import os
import sys

_LEGACY_ALIASES = frozenset({"java-codebase-rag", "java-codebase-rag-mcp"})

_DEPRECATION_LINE = (
    "jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. "
    "Set JRAG_NO_DEPRECATION=1 to silence.\n"
)

#: Executable suffixes appended by Windows (and packaging tools like pip) to
#: console-script wrappers. All three are exactly 4 characters long (``.exe``,
#: ``.bat``, ``.cmd``) so a single ``b[:-4]`` slice strips any of them. Stripping
#: keeps routing decisions stable across POSIX and Windows installs:
#: ``C:\\Users\\foo\\Scripts\\jrag.exe`` → ``"jrag"``;
#: ``...\\java-codebase-rag.exe`` → ``"java-codebase-rag"``.
_WINDOWS_EXE_SUFFIXES: tuple[str, ...] = (".exe", ".bat", ".cmd")


def _invoked_program_name() -> str:
    """Basename of ``sys.argv[0]`` with a Windows executable suffix stripped.

    Returns the empty string when ``sys.argv`` is empty. Otherwise takes
    ``os.path.basename(sys.argv[0])`` and, if it ends (case-insensitively) with
    one of ``.exe`` / ``.bat`` / ``.cmd``, drops the 4-character suffix.

    Shared with :mod:`java_codebase_rag.cli_dispatch` for identity-default
    routing: both legacy-alias deprecation here and ``jrag``-vs-alias routing
    there rely on the same canonicalized basename. Without this, Windows builds
    would route ``jrag.exe --version`` through the wrong parser and never emit
    the deprecation notice for ``java-codebase-rag.exe``.
    """
    argv = sys.argv
    if not argv:
        return ""
    base = os.path.basename(argv[0])
    if base.lower().endswith(_WINDOWS_EXE_SUFFIXES):
        return base[:-4]
    return base


def _stderr_is_tty() -> bool:
    """True iff ``sys.stderr`` reports itself as a TTY.

    Defensive against stderr replacements that omit ``isatty``.
    """
    stderr = sys.stderr
    return hasattr(stderr, "isatty") and stderr.isatty()


def _suppressed() -> bool:
    """True iff the notice should be suppressed."""
    if os.environ.get("JRAG_NO_DEPRECATION"):
        return True
    return not _stderr_is_tty()


def maybe_warn_legacy_alias(stream=None) -> None:
    """Emit a one-line legacy-alias deprecation notice when appropriate.

    Detects a legacy invocation (``sys.argv[0]`` basename is exactly
    ``java-codebase-rag`` or ``java-codebase-rag-mcp``) and, if not suppressed,
    writes a single line to ``stream`` (defaulting to ``sys.stderr``). Never
    raises: any error inside the write is swallowed so the rename helper can
    never break tool startup.

    See module docstring for the suppression rule.
    """
    if _invoked_program_name() not in _LEGACY_ALIASES:
        return
    if _suppressed():
        return
    target = stream if stream is not None else sys.stderr
    try:
        target.write(_DEPRECATION_LINE)
    except Exception:  # pragma: no cover - defensive: never break startup
        return
