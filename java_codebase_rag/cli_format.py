"""TTY-aware ANSI formatting for CLI stderr progress."""
from __future__ import annotations

import itertools
import sys
import threading
import time

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_CYAN = "\033[36m"

CHECK = "✓"
CROSS = "✗"

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_NOISE_CONTAINS: tuple[bytes, ...] = (
    b"lance::",
    b"FutureWarning",
    b"Loading weights:",
    b'"event": "brownfield-',
    b"unknown producer source strategy",
    b"unknown client source strategy",
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


class Spinner:
    """Braille spinner that overwrites the current stderr line until stopped."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="spinner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        sys.stderr.buffer.write(b"\r\x1b[2K")
        sys.stderr.buffer.flush()

    def _run(self) -> None:
        frames = itertools.cycle(_SPINNER_FRAMES)
        t0 = time.monotonic()
        while not self._stop.wait(0.3):
            elapsed = time.monotonic() - t0
            frame = next(frames)
            line = f"\r{frame} {self._label} · {elapsed:.0f}s"
            sys.stderr.buffer.write(line.encode())
            sys.stderr.buffer.flush()
