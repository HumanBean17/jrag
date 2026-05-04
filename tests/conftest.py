"""Shared pytest fixtures for the mcp_lancedb_bundle test suite.

The session-scoped `kuzu_graph` fixture builds the AST graph from the
`bank-chat-system` corpus exactly once and points the MCP environment
variables at the resulting Kuzu DB so every other test can rely on it
without paying the parse cost again.

⚠️  Do not bake fixture-specific assumptions into the production code under
test. See `tests/README.md` for the project's anti-overfitting rules.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BUNDLE_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
CORPUS_ROOT = TESTS_DIR / "bank-chat-system"

# Make the bundle importable when running `pytest` from the repo root.
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "lance_e2e: end-to-end cocoindex + Lance (optional; also gate with LANCEDB_MCP_RUN_HEAVY).",
    )


@pytest.fixture(scope="session")
def corpus_root() -> Path:
    assert CORPUS_ROOT.is_dir(), f"corpus missing: {CORPUS_ROOT}"
    return CORPUS_ROOT


@pytest.fixture(scope="session")
def kuzu_db_path(tmp_path_factory, corpus_root: Path) -> Path:
    """Build the Kuzu graph once per session against the bank-chat-system corpus."""
    from build_ast_graph import (
        GraphTables,
        pass1_parse,
        pass2_edges,
        pass3_calls,
        write_kuzu,
    )

    db_dir = tmp_path_factory.mktemp("kuzu_db")
    db_path = db_dir / "code_graph.kuzu"

    tables = GraphTables()
    asts = pass1_parse(corpus_root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    write_kuzu(db_path, tables, source_root=corpus_root, verbose=False)

    # Sanity: builder must have produced *some* nodes & edges. We don't
    # assert exact counts here — that's the job of test_ast_graph_build.
    assert tables.types, "build produced no type nodes"
    assert tables.injects_rows, "build produced no INJECTS edges"
    return db_path


@pytest.fixture(scope="session")
def mcp_env(kuzu_db_path: Path, tmp_path_factory) -> dict[str, str]:
    """Configure env vars the MCP server reads on startup.

    LANCEDB_URI points at an *empty* directory: it must exist (server
    validates with `Path(uri).exists()`) but does not need to contain a
    real index, because graph-only tools don't touch LanceDB.
    """
    fake_lance = tmp_path_factory.mktemp("fake_lancedb_data")
    env = {
        "KUZU_DB_PATH": str(kuzu_db_path),
        "LANCEDB_URI": str(fake_lance),
        "LANCEDB_MCP_GRAPH_ENABLED": "1",
        "LANCEDB_MCP_PROJECT_ROOT": str(CORPUS_ROOT),
    }
    for k, v in env.items():
        os.environ[k] = v
    return env


@pytest.fixture(scope="session")
def kuzu_graph(mcp_env, kuzu_db_path: Path):
    """Read-only KuzuGraph singleton bound to the session DB."""
    from kuzu_queries import KuzuGraph

    # Reset the cached singleton so tests don't see a stale path from
    # an earlier session / interactive run.
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    return KuzuGraph.get(str(kuzu_db_path))


@pytest.fixture(scope="session")
def mcp_server(mcp_env, kuzu_graph):
    """A FastMCP server instance with all tools registered."""
    from server import create_mcp_server

    return create_mcp_server()
