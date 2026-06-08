"""Shared pytest fixtures for the mcp_lancedb_bundle test suite.

Session-scoped graphs are built once per static corpus (see ``tests/README.md``).
The bank-chat chain ``corpus_root → kuzu_db_path → mcp_env → kuzu_graph → mcp_server``
runs pass1–5 + ``write_kuzu`` (no pass6) so Tier-1 caller-edge tests match the
pre-refactor bank pipeline while avoiding a second full parse for MCP tests.

⚠️  Do not bake fixture-specific assumptions into the production code under
test. See ``tests/README.md`` for the project's anti-overfitting rules.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from build_ast_graph import GraphTables


BUNDLE_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
CORPUS_ROOT = TESTS_DIR / "bank-chat-system"

# Make the bundle importable when running `pytest` from the repo root.
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "lance_e2e: end-to-end cocoindex + Lance (optional; also gate with JAVA_CODEBASE_RAG_RUN_HEAVY).",
    )


@pytest.fixture(scope="session")
def corpus_root() -> Path:
    assert CORPUS_ROOT.is_dir(), f"corpus missing: {CORPUS_ROOT}"
    return CORPUS_ROOT


def _session_db_path(tmp_path_factory: pytest.TempPathFactory, name: str) -> Path:
    base = tmp_path_factory.mktemp(f"kuzu_{name}")
    return base / "code_graph.kuzu"


@pytest.fixture(scope="session")
def kuzu_db_path(tmp_path_factory, corpus_root: Path) -> Path:
    """Bank-chat Kuzu DB: pass1–5 + ``write_kuzu`` (no pass6)."""
    import kuzu

    from _builders import build_kuzu_to

    db_path = _session_db_path(tmp_path_factory, "bank_chat")
    build_kuzu_to(corpus_root, db_path, max_pass=5)

    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    n_types = 0
    r = conn.execute("MATCH (s:Symbol) WHERE s.kind = 'class' RETURN count(*) AS n")
    if r.has_next():
        n_types = int(r.get_next()[0] or 0)
    assert n_types >= 1, "expected class symbols in session bank graph"
    r = conn.execute("MATCH ()-[e:INJECTS]->() RETURN count(e) AS n")
    n_injects = int(r.get_next()[0] or 0) if r.has_next() else 0
    assert n_injects >= 1, "build produced no INJECTS edges"
    return db_path


@pytest.fixture(scope="session")
def mcp_env(kuzu_db_path: Path, tmp_path_factory) -> dict[str, str]:
    """Configure env vars the MCP server reads on startup.

    ``JAVA_CODEBASE_RAG_INDEX_DIR`` is the parent of ``code_graph.kuzu`` so
    ``resolve_kuzu_path()`` matches the session graph fixture. Lance tables
    are not required for graph-only tools.
    """
    idx_dir = kuzu_db_path.parent
    env = {
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(idx_dir),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(CORPUS_ROOT),
    }
    for k, v in env.items():
        os.environ[k] = v
    return env


@pytest.fixture(scope="session")
def kuzu_graph(mcp_env, kuzu_db_path: Path):
    """Read-only KuzuGraph singleton bound to the session DB."""
    from kuzu_queries import KuzuGraph

    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    graph = KuzuGraph.get(str(kuzu_db_path))
    yield graph
    graph.close()
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None


@pytest.fixture(scope="session")
def mcp_server(mcp_env, kuzu_graph):
    """A FastMCP server instance with all tools registered."""
    from server import create_mcp_server

    return create_mcp_server()


# --- Session graphs for small static corpora under tests/fixtures/ ---


@pytest.fixture(scope="session")
def kuzu_db_path_call_graph_smoke(tmp_path_factory) -> Path:
    from _builders import build_kuzu_to

    root = TESTS_DIR / "fixtures" / "call_graph_smoke"
    assert root.is_dir(), root
    db_path = _session_db_path(tmp_path_factory, "call_graph_smoke")
    return build_kuzu_to(root, db_path, max_pass=3)


@pytest.fixture(scope="session")
def kuzu_db_path_route_extraction_smoke(tmp_path_factory) -> Path:
    from _builders import build_kuzu_to

    root = TESTS_DIR / "fixtures" / "route_extraction_smoke"
    assert root.is_dir(), root
    db_path = _session_db_path(tmp_path_factory, "route_extraction_smoke")
    return build_kuzu_to(root, db_path, max_pass=4)


@pytest.fixture(scope="session")
def kuzu_graph_route_extraction_smoke(kuzu_db_path_route_extraction_smoke: Path):
    """Read-only ``KuzuGraph`` for ``route_extraction_smoke`` (own DB path; not ``KuzuGraph.get``)."""
    from kuzu_queries import KuzuGraph

    graph = KuzuGraph(str(kuzu_db_path_route_extraction_smoke))
    yield graph
    graph.close()


@pytest.fixture(scope="session")
def kuzu_db_path_cross_service_smoke(tmp_path_factory) -> Path:
    from _builders import build_kuzu_to

    root = TESTS_DIR / "fixtures" / "cross_service_smoke"
    assert root.is_dir(), root
    db_path = _session_db_path(tmp_path_factory, "cross_service_smoke")
    return build_kuzu_to(root, db_path, max_pass=6)


@pytest.fixture(scope="session")
def kuzu_db_path_fqn_collision_smoke(tmp_path_factory) -> Path:
    from _builders import build_kuzu_to

    root = TESTS_DIR / "fixtures" / "fqn_collision_smoke"
    assert root.is_dir(), root
    db_path = _session_db_path(tmp_path_factory, "fqn_collision_smoke")
    return build_kuzu_to(root, db_path, max_pass=3)


@pytest.fixture(scope="session")
def kuzu_graph_fqn_collision_smoke(kuzu_db_path_fqn_collision_smoke: Path):
    from kuzu_queries import KuzuGraph

    graph = KuzuGraph(str(kuzu_db_path_fqn_collision_smoke))
    yield graph
    graph.close()


@pytest.fixture(scope="session")
def kuzu_db_path_http_caller_smoke(tmp_path_factory) -> Path:
    from _builders import build_kuzu_to

    root = TESTS_DIR / "fixtures" / "http_caller_smoke"
    assert root.is_dir(), root
    db_path = _session_db_path(tmp_path_factory, "http_caller_smoke")
    return build_kuzu_to(root, db_path, max_pass=5)


@pytest.fixture(scope="session")
def graph_tables_cross_service_smoke() -> "GraphTables":
    """In-memory tables for ``tests/fixtures/cross_service_smoke`` through pass6 (read-only tests)."""
    from _builders import build_graph_tables_to

    root = TESTS_DIR / "fixtures" / "cross_service_smoke"
    assert root.is_dir(), root
    return build_graph_tables_to(root, max_pass=6)
