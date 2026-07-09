"""Task 5: exclude_generated / generated_only filter tests.

Tests both engines (Lance SQL + MCP/Kùzu Cypher + Python post-filter).

Fixture: generated_samples (one generated + one hand-written type).
"""

import subprocess
from pathlib import Path

import pytest

from java_codebase_rag.mcp.mcp_v2 import find_v2, NodeFilter

# Skip LanceDB tests if we're in a graph-only environment
pytest.importorskip("lancedb")
from java_codebase_rag.search import search_lancedb


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "generated_samples"


def _require_cocoindex_runtime_deps() -> None:
    """cocoindex loads java_index_flow_lancedb.py with the same Python as the CLI."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Test needs project deps in the current env (e.g. ``pip install -r requirements*``"
            f" in the venv you use to run pytest): {exc}"
        )


def _cocoindex_flow_specifier(bundle_dir: Path, index_cwd: Path) -> str:
    """Return the coco index flow specifier for the java_index_flow_lancedb app."""
    import os
    flow_file = (bundle_dir / "java_index_flow_lancedb.py").resolve()
    if not flow_file.is_file():
        raise FileNotFoundError(f"missing index flow: {flow_file}")
    start = index_cwd.resolve()
    relp = os.path.relpath(str(flow_file), start=str(start))
    relp = Path(relp).as_posix()
    return f"{relp}:JavaCodeIndexLance"


@pytest.fixture
def lancedb_with_generated_index(tmp_path):
    """Build a real Lance index over generated_samples and return the URI."""
    _require_cocoindex_runtime_deps()

    # Locate the bundle dir (repo root)
    bundle_dir = Path(__file__).resolve().parent.parent

    # Get the flow specifier
    app_spec = _cocoindex_flow_specifier(bundle_dir / "src" / "java_codebase_rag" / "index", FIXTURE_ROOT)

    # Locate cocoindex binary
    import sys
    import os
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter; install cocoindex in this "
            f"venv and run: `.venv/bin/python -m pytest ...` ({cocoindex_bin})"
        )

    # Set up the index directory in tmp_path
    index_dir = tmp_path / ".java-codebase-rag"
    index_dir.mkdir(parents=True)

    # Set up environment
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(FIXTURE_ROOT.resolve()),
    }

    # Run cocoindex update from the fixture directory
    result = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            app_spec,
            "-f",
        ],
        cwd=str(FIXTURE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)

    return str(index_dir)


def test_mcp_nodefilter_accepts_generated_flags(ladybug_graph_generated_smoke) -> None:
    """NodeFilter should accept exclude_generated and generated_only flags."""
    # This would fail before implementation due to extra="forbid"
    filter1 = NodeFilter(exclude_generated=True)
    filter2 = NodeFilter(generated_only=True)
    filter3 = NodeFilter(exclude_generated=False, generated_only=False)

    assert filter1.exclude_generated is True
    assert filter2.generated_only is True
    assert filter3.exclude_generated is False
    assert filter3.generated_only is False


def test_mcp_find_exclude_generated_excludes_generated_nodes(ladybug_graph_generated_smoke) -> None:
    """find(symbol, filter=NodeFilter(exclude_generated=True)) → generated NodeRef excluded.

    This tests the Cypher path via _symbol_where_from_filter.
    """
    out = find_v2(
        "symbol",
        NodeFilter(exclude_generated=True),
        graph=ladybug_graph_generated_smoke,
        limit=100,
    )
    assert out.success is True
    # Results should only contain hand-written symbols (generated is False/None)
    for r in out.results:
        if hasattr(r, 'generated'):
            assert not r.generated, f"Expected only hand-written symbols, but found generated: {r}"


def test_mcp_find_generated_only_returns_only_generated_nodes(ladybug_graph_generated_smoke) -> None:
    """find(symbol, filter=NodeFilter(generated_only=True)) → only generated.

    This tests the post-filter path via _node_matches_filter.
    """
    out = find_v2(
        "symbol",
        NodeFilter(generated_only=True),
        graph=ladybug_graph_generated_smoke,
        limit=100,
    )
    assert out.success is True
    # Results should only contain generated symbols
    for r in out.results:
        if hasattr(r, 'generated'):
            assert r.generated, f"Expected only generated symbols, but found hand-written: {r}"


def test_mcp_find_default_returns_both_types(ladybug_graph_generated_smoke) -> None:
    """find(symbol, filter=NodeFilter()) → both generated and hand-written returned (default)."""
    out = find_v2(
        "symbol",
        NodeFilter(),  # No flags set (default behavior)
        graph=ladybug_graph_generated_smoke,
        limit=100,
    )
    assert out.success is True
    # Should have results from both generated and hand-written types
    assert len(out.results) > 0, "Expected some results with default filter"
    # Verify we have both types
    has_generated = any(hasattr(r, 'generated') and r.generated for r in out.results)
    has_handwritten = any(hasattr(r, 'generated') and not r.generated for r in out.results)
    assert has_generated, "Expected at least one generated symbol in default results"
    assert has_handwritten, "Expected at least one hand-written symbol in default results"


def test_run_search_exclude_generated_removes_generated_sources(lancedb_with_generated_index) -> None:
    """run_search(..., exclude_generated=True) → no generated chunks; hand-written present."""
    rows = search_lancedb.run_search(
        "HandWritten OR Model",  # Query that matches both files
        uri=lancedb_with_generated_index,
        table_keys=["java"],
        limit=10,
        path_substring=None,  # No path filter
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        device=None,
        exclude_generated=True,
    )

    # Should have results (hand-written file should match)
    assert len(rows) > 0, "Expected results from hand-written file when exclude_generated=True"

    # Should not contain any generated sources
    for row in rows:
        if "generated" in row:
            assert not row.get("generated"), f"Expected no generated sources, but found: {row}"

    # Verify HandWritten.java is present
    filenames = {row.get("filename", "") for row in rows}
    assert any("HandWritten.java" in f for f in filenames), "Expected HandWritten.java in results"

    # Verify OpenAPIModel.java is NOT present
    assert not any("OpenAPIModel.java" in f for f in filenames), "OpenAPIModel.java should be filtered out"


def test_run_search_generated_only_returns_only_generated_sources(lancedb_with_generated_index) -> None:
    """run_search(..., generated_only=True) → only generated chunks."""
    rows = search_lancedb.run_search(
        "HandWritten OR Model",  # Query that matches both files
        uri=lancedb_with_generated_index,
        table_keys=["java"],
        limit=10,
        path_substring=None,
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        device=None,
        generated_only=True,
    )

    # Should have results (generated file should match)
    assert len(rows) > 0, "Expected results from generated file when generated_only=True"

    # Should only contain generated sources
    for row in rows:
        if "generated" in row:
            assert row.get("generated"), f"Expected only generated sources, but found: {row}"

    # Verify OpenAPIModel.java is present
    filenames = {row.get("filename", "") for row in rows}
    assert any("OpenAPIModel.java" in f for f in filenames), "Expected OpenAPIModel.java in results"

    # Verify HandWritten.java is NOT present
    assert not any("HandWritten.java" in f for f in filenames), "HandWritten.java should be filtered out"


def test_run_search_default_returns_both_types(lancedb_with_generated_index) -> None:
    """run_search(..., default) → both generated and hand-written returned."""
    rows = search_lancedb.run_search(
        "HandWritten OR Model",  # Query that matches both files
        uri=lancedb_with_generated_index,
        table_keys=["java"],
        limit=10,
        path_substring=None,
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        device=None,
        # Neither flag set (default behavior)
    )

    # Should have results from both files
    assert len(rows) > 0, "Expected results with default filter"

    # Verify both files are present
    filenames = {row.get("filename", "") for row in rows}
    has_generated = any("OpenAPIModel.java" in f for f in filenames)
    has_handwritten = any("HandWritten.java" in f for f in filenames)

    assert has_generated, "Expected OpenAPIModel.java (generated) in default results"
    assert has_handwritten, "Expected HandWritten.java (hand-written) in default results"
