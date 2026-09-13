"""Per-test env pinning for usage tests.

The full-suite ordering poisons ambient env: earlier suites (e.g. eval's
``run_eval``) write ``JAVA_CODEBASE_RAG_INDEX_DIR`` into ``os.environ``
directly and never restore it, and the session-scoped ``mcp_env`` fixture only
sets env at first instantiation. These tests must not depend on whatever ran
before them — pin INDEX_DIR/SOURCE_ROOT to the session bank-chat fixture
per-test via monkeypatch (auto-reverted, order-proof).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "bank-chat-system"


@pytest.fixture
def env_pinned(ladybug_db_path: Path, monkeypatch) -> dict[str, str]:
    idx_dir = ladybug_db_path.parent
    env = {
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(idx_dir),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(_CORPUS_ROOT),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env
