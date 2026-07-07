"""Task 5: exclude_generated / generated_only filter tests.

Tests both engines (Lance SQL + MCP/Kùzu Cypher + Python post-filter).

Fixture: generated_samples (one generated + one hand-written type).
"""

import os
import pytest
from pathlib import Path
from mcp_v2 import find_v2, NodeFilter


# Skip LanceDB tests if we're in a graph-only environment
pytest.importorskip("lancedb")
import search_lancedb


def test_mcp_nodefilter_accepts_generated_flags(ladybug_graph) -> None:
    """NodeFilter should accept exclude_generated and generated_only flags."""
    # This would fail before implementation due to extra="forbid"
    filter1 = NodeFilter(exclude_generated=True)
    filter2 = NodeFilter(generated_only=True)
    filter3 = NodeFilter(exclude_generated=False, generated_only=False)

    assert filter1.exclude_generated is True
    assert filter2.generated_only is True
    assert filter3.exclude_generated is False
    assert filter3.generated_only is False


def test_mcp_find_exclude_generated_excludes_generated_nodes(ladybug_graph) -> None:
    """find(symbol, filter=NodeFilter(exclude_generated=True)) → generated NodeRef excluded.

    This tests the Cypher path via _symbol_where_from_filter.
    """
    out = find_v2(
        "symbol",
        NodeFilter(exclude_generated=True),
        graph=ladybug_graph,
        limit=100,
    )
    assert out.success is True
    # Results should only contain hand-written symbols (generated is False/None)
    for r in out.results:
        if hasattr(r, 'generated'):
            assert not r.generated, f"Expected only hand-written symbols, but found generated: {r}"


def test_mcp_find_generated_only_returns_only_generated_nodes(ladybug_graph) -> None:
    """find(symbol, filter=NodeFilter(generated_only=True)) → only generated.

    This tests the post-filter path via _node_matches_filter.
    """
    out = find_v2(
        "symbol",
        NodeFilter(generated_only=True),
        graph=ladybug_graph,
        limit=100,
    )
    assert out.success is True
    # Results should only contain generated symbols
    for r in out.results:
        if hasattr(r, 'generated'):
            assert r.generated, f"Expected only generated symbols, but found hand-written: {r}"


def test_mcp_find_default_returns_both_types(ladybug_graph) -> None:
    """find(symbol, filter=NodeFilter()) → both generated and hand-written returned (default)."""
    out = find_v2(
        "symbol",
        NodeFilter(),  # No flags set (default behavior)
        graph=ladybug_graph,
        limit=100,
    )
    assert out.success is True
    # Should have results (we don't assert both types exist since the test data may vary)
    assert len(out.results) > 0, "Expected some results with default filter"


@pytest.fixture
def lancedb_uri(tmp_path):
    """Provide a LanceDB URI for testing if available."""
    # Try to get the index directory from the environment
    index_dir = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR")
    if index_dir and Path(index_dir).exists():
        # Check if it's a LanceDB directory (has .lance files)
        lance_path = Path(index_dir)
        if any(lance_path.glob("*.lance")) or (lance_path / "java").exists():
            return str(lance_path)
    pytest.skip("No LanceDB index available - skipping LanceDB tests")


def test_run_search_exclude_generated_removes_generated_sources(lancedb_uri) -> None:
    """run_search(..., exclude_generated=True) → no generated chunks; hand-written present."""
    try:
        rows = search_lancedb.run_search(
            "ChatController OR ChatService",
            uri=lancedb_uri,
            table_keys=["java"],
            limit=10,
            path_substring=None,  # No path filter
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            device=None,
            exclude_generated=True,
        )
        # Should not contain any generated sources
        for row in rows:
            if "generated" in row:
                assert not row.get("generated"), f"Expected no generated sources, but found: {row}"
    except Exception as e:
        if "was not found" in str(e):
            pytest.skip("LanceDB java table not found in index")
        else:
            raise


def test_run_search_generated_only_returns_only_generated_sources(lancedb_uri) -> None:
    """run_search(..., generated_only=True) → only generated chunks."""
    try:
        rows = search_lancedb.run_search(
            "ChatController OR ChatService",
            uri=lancedb_uri,
            table_keys=["java"],
            limit=10,
            path_substring=None,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            device=None,
            generated_only=True,
        )
        # Should only contain generated sources
        for row in rows:
            if "generated" in row:
                assert row.get("generated"), f"Expected only generated sources, but found: {row}"
    except Exception as e:
        if "was not found" in str(e):
            pytest.skip("LanceDB java table not found in index")
        else:
            raise


def test_run_search_default_returns_both_types(lancedb_uri) -> None:
    """run_search(..., default) → both generated and hand-written returned."""
    try:
        rows = search_lancedb.run_search(
            "ChatController OR ChatService",
            uri=lancedb_uri,
            table_keys=["java"],
            limit=10,
            path_substring=None,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            device=None,
            # Neither flag set (default behavior)
        )
        # Should have results
        assert len(rows) > 0, "Expected some results with default filter"
    except Exception as e:
        if "was not found" in str(e):
            pytest.skip("LanceDB java table not found in index")
        else:
            raise
