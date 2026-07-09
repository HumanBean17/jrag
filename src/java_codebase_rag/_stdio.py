"""Force stdout/stderr to UTF-8 so non-ASCII glyphs never crash the CLI.

The text renderers emit Unicode glyphs — ``↑``/``↓`` (hierarchy tree headers
in ``jrag_render``), ``✓`` (success markers in ``cli_format``), ``→``/``…``
(listing/role lines). On Windows, ``sys.stdout``/``sys.stderr`` default to the
system ANSI codepage (cp1252 on en-US Windows), which can't encode those
characters, so ``print()`` raises ``UnicodeEncodeError`` and the process exits
non-zero. Unix platforms already default to UTF-8, so this is a no-op there.

Called from the console-script entry points (``_console_script_main``), not
from in-process ``main()`` callers, so a test that drives ``main()`` directly
keeps whatever stdout the host wired up.
"""

from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Reconfigure ``sys.stdout``/``sys.stderr`` to UTF-8.

    Best-effort and silent: never raises. No-op where a stream lacks
    ``reconfigure`` (streams replaced by capture frameworks that don't expose
    it). ``errors="replace"`` is a last-resort safety net so a hostile console
    can never crash a run; under UTF-8 every codepoint encodes cleanly, so
    replacement never actually fires for the glyphs we emit.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
