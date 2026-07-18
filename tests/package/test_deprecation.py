"""Legacy-alias deprecation helper.

``maybe_warn_legacy_alias`` emits a single-line notice when the tool is invoked
through one of the legacy command aliases (``java-codebase-rag`` or
``java-codebase-rag-mcp``), gated so it never fires in non-interactive or
automation contexts.

Suppression rule (see module docstring of :mod:`java_codebase_rag._deprecation`):
the notice is suppressed when ``JRAG_NO_DEPRECATION`` is present and non-empty
(any non-empty value — including ``"0"``, ``"1"``, ``"false"`` — suppresses), or
when ``sys.stderr`` is not a TTY.
"""
from __future__ import annotations

import sys

from java_codebase_rag._deprecation import maybe_warn_legacy_alias


EXPECTED_LINE = (
    "jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. "
    "Set JRAG_NO_DEPRECATION=1 to silence.\n"
)


class _FakeStderr:
    """Minimal stderr stand-in: records writes and reports a chosen TTY state."""

    def __init__(self, *, isatty: bool) -> None:
        self._isatty = isatty
        self.writes: list[str] = []

    def isatty(self) -> bool:
        return self._isatty

    def write(self, s: str) -> int:
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:  # pragma: no cover - parity with real stderr
        pass


def _setup(monkeypatch, argv0, *, isatty, env_value=None):
    """Configure ``sys.argv``, ``sys.stderr``, and ``JRAG_NO_DEPRECATION``."""
    monkeypatch.setattr(sys, "argv", [argv0])
    fake = _FakeStderr(isatty=isatty)
    monkeypatch.setattr(sys, "stderr", fake)
    if env_value is None:
        monkeypatch.delenv("JRAG_NO_DEPRECATION", raising=False)
    else:
        monkeypatch.setenv("JRAG_NO_DEPRECATION", env_value)
    return fake


# --- Scenarios that EMIT the deprecation line ------------------------------


def test_emits_for_java_codebase_rag_alias(monkeypatch):
    fake = _setup(monkeypatch, "java-codebase-rag", isatty=True)
    maybe_warn_legacy_alias()
    assert "".join(fake.writes) == EXPECTED_LINE


def test_emits_for_java_codebase_rag_mcp_alias(monkeypatch):
    fake = _setup(monkeypatch, "java-codebase-rag-mcp", isatty=True)
    maybe_warn_legacy_alias()
    assert "".join(fake.writes) == EXPECTED_LINE


# --- Scenarios that emit NOTHING -------------------------------------------


def test_silent_for_canonical_jrag(monkeypatch):
    fake = _setup(monkeypatch, "jrag", isatty=True)
    maybe_warn_legacy_alias()
    assert fake.writes == []


def test_silent_for_canonical_jrag_mcp(monkeypatch):
    fake = _setup(monkeypatch, "jrag-mcp", isatty=True)
    maybe_warn_legacy_alias()
    assert fake.writes == []


def test_silent_when_env_suppresses(monkeypatch):
    fake = _setup(monkeypatch, "java-codebase-rag", isatty=True, env_value="1")
    maybe_warn_legacy_alias()
    assert fake.writes == []


def test_silent_when_not_a_tty(monkeypatch):
    fake = _setup(monkeypatch, "java-codebase-rag", isatty=False)
    maybe_warn_legacy_alias()
    assert fake.writes == []


def test_any_nonempty_env_value_suppresses(monkeypatch):
    """Rule: any non-empty ``JRAG_NO_DEPRECATION`` value suppresses.

    ``"0"``, ``"1"``, and ``"false"`` all suppress (no truthy-value parsing).
    """
    for val in ("0", "1", "false"):
        fake = _setup(monkeypatch, "java-codebase-rag", isatty=True, env_value=val)
        maybe_warn_legacy_alias()
        assert fake.writes == [], f"value {val!r} should suppress"
