"""TTY-aware ANSI formatting for CLI stderr progress."""
from __future__ import annotations

import sys

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_CYAN = "\033[36m"

CHECK = "✓"
CROSS = "✗"

_NOISE_CONTAINS: tuple[bytes, ...] = (
    b"lance::",
    b"FutureWarning",
    b"Loading weights:",
    b'"event": "brownfield-',
    b"unknown producer source strategy",
    b"unknown client source strategy",
    # Builder verbose heartbeats / pass banners: in default mode the renderer's
    # bar subsumes these, so they must NOT also appear as raw lines above the
    # Live region. --verbose raw-relay bypasses this filter and still shows them.
    b"[graph] pass ",
    b"[graph] scoped write ",
    b"[graph] writing ",
    b"[graph] done ",
    b"[increment] ",
)


def is_noise_line(line: bytes) -> bool:
    return any(p in line for p in _NOISE_CONTAINS)


def stderr_is_tty() -> bool:
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _styled(text: str, *codes: str) -> str:
    if not stderr_is_tty():
        return text
    return "".join(codes) + text + _RESET


def bold(text: str) -> str:
    return _styled(text, _BOLD)


def dim(text: str) -> str:
    return _styled(text, _DIM)


def green(text: str) -> str:
    return _styled(text, _GREEN)


def red(text: str) -> str:
    return _styled(text, _RED)


def cyan(text: str) -> str:
    return _styled(text, _CYAN)


def bold_green(text: str) -> str:
    return _styled(text, _BOLD, _GREEN)


def bold_red(text: str) -> str:
    return _styled(text, _BOLD, _RED)


def bold_cyan(text: str) -> str:
    return _styled(text, _BOLD, _CYAN)


def styled_check() -> str:
    return green(CHECK) if stderr_is_tty() else CHECK


def styled_cross() -> str:
    return red(CROSS) if stderr_is_tty() else CROSS
