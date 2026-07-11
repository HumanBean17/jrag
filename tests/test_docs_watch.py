"""Docs-accuracy tests for the `jrag watch` operator surface.

Pins the operator-facing documentation against drift: the CLI playbook must
mention `jrag watch` and its lifecycle verbs, and the configuration reference
must document the `watch:` YAML block. These are grep-style assertions on the
doc file contents — no runtime behavior is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# tests/test_docs_watch.py → parent = tests/ → parent = repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DOC = REPO_ROOT / "docs" / "JAVA-CODEBASE-RAG-CLI.md"
CONFIG_DOC = REPO_ROOT / "docs" / "CONFIGURATION.md"


@pytest.fixture(scope="module")
def cli_text() -> str:
    return CLI_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def config_text() -> str:
    return CONFIG_DOC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI playbook — operator surface
# ---------------------------------------------------------------------------


class TestCliDocWatchSurface:
    """`JAVA-CODEBASE-RAG-CLI.md` must document the `jrag watch` lifecycle."""

    def test_mentions_jrag_watch(self, cli_text: str) -> None:
        assert "jrag watch" in cli_text, "CLI doc does not mention `jrag watch`"

    @pytest.mark.parametrize("flag", ["--detach", "--stop", "--status"])
    def test_documents_lifecycle_flag(self, cli_text: str, flag: str) -> None:
        assert flag in cli_text, f"CLI doc does not document the `{flag}` verb"

    def test_documents_cold_fallback_guarantee(self, cli_text: str) -> None:
        """The doc must state that reads behave identically with no daemon."""
        lower = cli_text.lower()
        assert "fallback" in lower and "cold" in lower, (
            "CLI doc does not state the cold-fallback guarantee "
            "(reads work byte-identically when no daemon is running)"
        )

    def test_documents_unix_only(self, cli_text: str) -> None:
        """`jrag watch` is Unix-only (macOS/Linux) — the doc must say so."""
        lower = cli_text.lower()
        assert "unix" in lower or ("macos" in lower and "linux" in lower), (
            "CLI doc does not note the Unix-only (macOS/Linux) constraint"
        )


# ---------------------------------------------------------------------------
# Configuration reference — the `watch:` YAML block
# ---------------------------------------------------------------------------


class TestConfigDocWatchBlock:
    """`CONFIGURATION.md` must document the three `watch:` YAML keys."""

    @pytest.mark.parametrize(
        "key", ["watch.debounce_ms", "watch.backend", "watch.poll_interval_ms"]
    )
    def test_documents_watch_key(self, config_text: str, key: str) -> None:
        assert key in config_text, (
            f"CONFIGURATION.md does not document `{key}`"
        )
