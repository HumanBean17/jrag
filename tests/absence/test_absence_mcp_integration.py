"""Integration tests for absence diagnosis wired into MCP tools (PR-ABS-3).

These tests verify that diagnose() is called on empty paths and the result is
attached to the output's absence field. Non-empty results should have absence=None.
"""
from __future__ import annotations

import importlib.util

import pytest

from java_codebase_rag.mcp.mcp_v2 import describe_v2, find_v2, neighbors_v2, search_v2
from java_codebase_rag.analysis.resolve_service import resolve_v2
from java_codebase_rag.absence.absence_types import AbsenceVerdict


def _vector_stack_available() -> bool:
    """True when the optional vector stack (torch/sentence-transformers/lancedb) is installed.

    The ``search`` tool loads a SentenceTransformer model, so tests that monkeypatch
    ``run_search`` still need the model importable (the patch makes ``run_search is None``
    False, forcing the semantic path). Skip them on graph-only installs (macOS Intel,
    where the vector trio is gated off by PEP 508 markers). Mirrors test_mcp_v2.py.
    """
    return all(importlib.util.find_spec(m) is not None for m in ("sentence_transformers", "lancedb"))


needs_vectors = pytest.mark.skipif(
    not _vector_stack_available(),
    reason="vector stack not installed (graph-only install; macOS Intel)",
)


@needs_vectors
def test_search_empty_result_has_absence_diagnosis(ladybug_graph, monkeypatch) -> None:
    """Empty search result should have absence field populated with diagnosis."""
    # Monkeypatch run_search to return empty results
    monkeypatch.setattr("java_codebase_rag.mcp.mcp_v2.run_search", lambda *args, **kwargs: [])

    out = search_v2("zzzNoSuchClass123", graph=ladybug_graph)
    assert out.success is True
    assert out.results == []
    assert out.absence is not None, "absence should be populated on empty results"
    assert out.absence.verdict in AbsenceVerdict.__args__
    assert out.absence.message
    # Should be not_in_project for a made-up identifier
    if out.absence.verdict == "not_in_project":
        assert out.absence.proof is not None
        assert out.absence.closest_symbols is not None


@needs_vectors
def test_search_typo_has_absence_diagnosis(ladybug_graph, monkeypatch) -> None:
    """Search with a typo should have refine_query verdict with closest symbols."""
    monkeypatch.setattr("java_codebase_rag.mcp.mcp_v2.run_search", lambda *args, **kwargs: [])

    out = search_v2("ChatServic", graph=ladybug_graph)  # typo: missing 'e'
    assert out.success is True
    assert out.results == []
    assert out.absence is not None
    assert out.absence.verdict == "refine_query"
    assert out.absence.cause == "identifier_miss"
    assert out.absence.closest_symbols  # should have did-you-mean suggestions


@needs_vectors
def test_search_external_dependency_has_absence_diagnosis(ladybug_graph, monkeypatch) -> None:
    """Search for an external dependency should have external_dependency verdict."""
    monkeypatch.setattr("java_codebase_rag.mcp.mcp_v2.run_search", lambda *args, **kwargs: [])

    out = search_v2("java.util.List", graph=ladybug_graph)
    assert out.success is True
    assert out.results == []
    assert out.absence is not None
    assert out.absence.verdict == "external_dependency"
    assert out.absence.external_identity is not None
    assert "java.util" in out.absence.external_identity.fqn or "java.util.List" in out.absence.external_identity.fqn


@needs_vectors
def test_search_non_empty_result_has_no_absence(ladybug_graph, monkeypatch) -> None:
    """Non-empty search result should have absence=None."""
    # Mock search to return results
    fake_rows = [
        {
            "id": "chunk:1",
            "symbol_id": "sym:1",
            "primary_type_fqn": "com.example.ChatService",
            "_rrf_score": 0.9,
            "text": "ChatService sample",
            "microservice": "chat-assign",
            "module": "chat-assign",
            "role": "SERVICE",
            "filename": "chat-assign/src/main/java/com/example/ChatAssignService.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 30},
        },
    ]
    monkeypatch.setattr("java_codebase_rag.mcp.mcp_v2.run_search", lambda *args, **kwargs: fake_rows)

    out = search_v2("ChatService", graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) > 0
    assert out.absence is None, "absence should be None for non-empty results"


def test_find_empty_result_has_absence_diagnosis(ladybug_graph) -> None:
    """Empty find result should have absence field populated."""
    out = find_v2("symbol", {"fqn_contains": "zzzNoMatch"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results == []
    assert out.absence is not None
    assert out.absence.verdict in AbsenceVerdict.__args__
    # Could be identifier_miss or filter_miss depending on the query shape
    if out.absence.verdict == "refine_query":
        assert out.absence.cause in ("identifier_miss", "filter_miss")


def test_find_non_empty_result_has_no_absence(ladybug_graph) -> None:
    """Non-empty find result should have absence=None."""
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) > 0
    assert out.absence is None


def test_describe_fqn_not_found_has_absence_diagnosis(ladybug_graph) -> None:
    """Describe with non-existent FQN should have absence field populated."""
    out = describe_v2(fqn="com.no.such.Type", graph=ladybug_graph)
    assert out.success is False
    assert out.absence is not None
    assert out.absence.verdict in ("not_in_project", "refine_query")
    # Message should mention the FQN
    assert "com.no.such.Type" in out.message or "No Symbol found" in out.message


def test_describe_node_id_not_found_has_absence_diagnosis(ladybug_graph) -> None:
    """Describe with non-existent node_id should have absence field populated."""
    out = describe_v2(id="sym:doesnotexist12345", graph=ladybug_graph)
    assert out.success is False
    assert out.absence is not None
    assert out.absence.verdict in ("refine_query", "not_in_project")


def test_describe_non_empty_result_has_no_absence(ladybug_graph) -> None:
    """Non-empty describe result should have absence=None."""
    # First find a real symbol
    find_out = find_v2("symbol", {"symbol_kind": "class"}, limit=1, graph=ladybug_graph)
    assert find_out.success is True
    assert len(find_out.results) > 0

    # Then describe it
    real_id = find_out.results[0].id
    out = describe_v2(id=real_id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.absence is None


def test_neighbors_empty_result_has_absence_diagnosis(ladybug_graph) -> None:
    """Empty neighbors result should have absence field populated."""
    # First find a leaf node (a method with no outgoing CALLS edges)
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {kind: 'method'}) WHERE NOT (m)-[:CALLS]->() RETURN m.id AS id LIMIT 1"
    )
    if not rows:
        pytest.skip("No leaf methods found in test graph")

    leaf_id = rows[0]["id"]
    out = neighbors_v2(leaf_id, edge_types=["CALLS"], direction="out", graph=ladybug_graph)
    assert out.success is True
    assert out.results == []
    assert out.absence is not None
    # Leaf with no callers should be correct_empty
    assert out.absence.verdict in ("correct_empty", "refine_query")


def test_neighbors_non_empty_result_has_no_absence(ladybug_graph) -> None:
    """Non-empty neighbors result should have absence=None."""
    # Find a method with outgoing CALLS
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {kind: 'method'})-[:CALLS]->() RETURN m.id AS id LIMIT 1"
    )
    assert rows, "Test graph should have at least one method with CALLS"

    method_id = rows[0]["id"]
    out = neighbors_v2(method_id, edge_types=["CALLS"], direction="out", graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) > 0
    assert out.absence is None


def test_resolve_empty_result_has_absence_diagnosis(ladybug_graph) -> None:
    """Empty resolve result should have absence field populated."""
    out = resolve_v2("zzzNoSuchSymbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status == "none"
    assert out.absence is not None
    assert out.absence.verdict in ("not_in_project", "refine_query")
    # Should have did-you-mean suggestions for identifier-shaped query
    if out.absence.verdict == "refine_query":
        assert out.absence.closest_symbols is not None


def test_resolve_non_empty_result_has_no_absence(ladybug_graph) -> None:
    """Non-empty resolve result should have absence=None."""
    # Find a real symbol first
    find_out = find_v2("symbol", {"symbol_kind": "class"}, limit=1, graph=ladybug_graph)
    assert find_out.success is True
    assert len(find_out.results) > 0

    real_fqn = find_out.results[0].fqn
    assert real_fqn

    out = resolve_v2(real_fqn, hint_kind="symbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status in ("one", "many")
    assert out.absence is None


# --- Absence-config singleton isolation regression ---
#
# server.main() caches the operator cfg in a module-global singleton via
# set_absence_config() (mcp/server.py). Tests that drive server.main() with a
# MOCKED resolve_operator_config (tests/package/test_java_codebase_rag_cli.py)
# cache a MagicMock there, and monkeypatch does NOT revert it (set via a function
# call, not an attribute patch). On the graph-only macOS Intel CI leg this leaks
# into later absence/resolve tests in the same xdist worker and surfaces as
# `int < MagicMock` (best_sim < cfg.absence_absent_floor). The autouse
# `_reset_absence_config_singleton` fixture in conftest.py is the fix; the pair
# below pins the cross-test isolation contract. Runs in definition order.


def test_absence_config_singleton_leak_regression_poison() -> None:
    """Part 1: simulate server.main() caching a MagicMock cfg in the singleton."""
    from unittest.mock import MagicMock

    from java_codebase_rag.analysis import resolve_service
    from java_codebase_rag.mcp import mcp_v2

    mcp_v2._absence_config = MagicMock()
    resolve_service._absence_config = MagicMock()
    assert isinstance(mcp_v2._absence_config, MagicMock)


def test_absence_config_singleton_leak_regression_is_clean() -> None:
    """Part 2: the autouse isolation fixture must have reset the poisoned singleton
    from part 1, so absence/resolve tools build a real config (no `int < MagicMock`).
    Fails if the `_reset_absence_config_singleton` fixture is removed/broken.
    """
    from java_codebase_rag.analysis import resolve_service
    from java_codebase_rag.mcp import mcp_v2

    assert mcp_v2._absence_config is None
    assert resolve_service._absence_config is None
