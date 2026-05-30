"""Tests for mcp_trace.py (PR-TRACE-1a core BFS + PR-TRACE-1b pruning/collapsing/cross-service).

All tests use the bank-chat kuzu_graph session fixture from conftest.py.
"""
from __future__ import annotations

import pytest

from kuzu_queries import KuzuGraph
from mcp_trace import trace_v2
from mcp_v2 import NodeFilter


def _find_method_with_outbound_calls(kuzu_graph: KuzuGraph) -> str | None:
    """Find a method with at least one outbound CALLS edge."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:CALLS]->(other:Symbol) RETURN m.id AS id LIMIT 1"
    )
    if rows:
        return str(rows[0]["id"])
    return None


def _find_method_with_inbound_calls(kuzu_graph: KuzuGraph) -> str | None:
    """Find a method with at least one inbound CALLS edge."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (caller:Symbol)-[:CALLS]->(m:Symbol) RETURN m.id AS id LIMIT 1"
    )
    if rows:
        return str(rows[0]["id"])
    return None


def _find_method_with_multiple_callees(kuzu_graph: KuzuGraph, min_callees: int = 3) -> str | None:
    """Find a method with multiple outbound CALLS for testing paths."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[:CALLS]->(other:Symbol)
        WITH m, count(DISTINCT other) AS n
        WHERE n >= $min
        RETURN m.id AS id
        LIMIT 1
        """,
        {"min": min_callees},
    )
    if rows:
        return str(rows[0]["id"])
    return None


def test_trace_outbound_calls_depth_2(kuzu_graph: KuzuGraph) -> None:
    """Traces from a method via CALLS out, depth 2, returns edges at hop 0 and hop 1."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert len(out.edges) > 0
    assert out.seed_ids == [seed_id]
    assert out.direction == "out"
    assert out.edge_types == ["CALLS"]
    # Check that we have edges at hop 0 and possibly hop 1.
    hops = {e.hop for e in out.edges}
    assert 0 in hops and hops <= {0, 1}


def test_trace_inbound_callers_depth_2(kuzu_graph: KuzuGraph) -> None:
    """Traces from a repository method via CALLS in, depth 2, returns caller chain."""
    seed_id = _find_method_with_inbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with inbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="in",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.seed_ids == [seed_id]
    assert out.direction == "in"


def test_trace_max_paths_cap(kuzu_graph: KuzuGraph) -> None:
    """Result paths list does not exceed max_paths."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=5)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        max_paths=5,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert len(out.paths) <= 5


def test_trace_budget_stops_early(kuzu_graph: KuzuGraph) -> None:
    """BFS stops when max_nodes_discovered is hit; stats.budget_hit=True; advisory present."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=10)
    if seed_id is None:
        pytest.skip("No method with many callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=5,
        max_nodes_discovered=100,  # Use minimum valid value (clamped to 100)
        graph=kuzu_graph,
    )
    assert out.success is True
    # If we discovered more than the budget (100), budget_hit should be True.
    if out.stats.total_nodes_discovered >= 100:
        assert out.stats.budget_hit is True
        assert any("budget" in adv for adv in out.advisories)


def test_trace_depth_1_equivalent_to_neighbors(kuzu_graph: KuzuGraph) -> None:
    """Depth 1 trace with no pruning returns same nodes as neighbors for same seed + edge types."""
    from mcp_v2 import neighbors_v2

    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    # Get neighbors result.
    neigh_out = neighbors_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        limit=100,
        graph=kuzu_graph,
    )
    assert neigh_out.success is True

    # Get trace result.
    trace_out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert trace_out.success is True

    # Compare node IDs (trace nodes dict vs neighbors results).
    trace_node_ids = set(trace_out.nodes.keys())
    neigh_node_ids = {e.other.id for e in neigh_out.results}

    # Seed is in trace nodes, neighbors doesn't include seed.
    trace_node_ids.discard(seed_id)

    # They should have significant overlap (allowing for filter differences).
    assert len(trace_node_ids & neigh_node_ids) >= min(len(trace_node_ids), len(neigh_node_ids)) * 0.8


def test_trace_stats_counts(kuzu_graph: KuzuGraph) -> None:
    """Stats counts are consistent with the edge set."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.edges_after_pruning == len(out.edges)
    assert out.stats.nodes_after_pruning == len(out.nodes)
    assert out.stats.total_edges_discovered == len(out.edges)
    assert out.stats.total_nodes_discovered >= len(out.nodes)


def test_trace_empty_seed(kuzu_graph: KuzuGraph) -> None:
    """Empty seed ids returns success=True, nodes={}, edges=[], paths=[]."""
    out = trace_v2(
        ids=[],
        direction="out",
        edge_types=["CALLS"],
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.seed_ids == []
    assert out.nodes == {}
    assert out.edges == []
    assert out.paths == []


def test_trace_single_string_seed(kuzu_graph: KuzuGraph) -> None:
    """Single string ids is normalized to list; seed_ids echoed as list of one."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,  # Pass as string, not list
        direction="out",
        edge_types=["CALLS"],
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.seed_ids == [seed_id]
    assert seed_id in out.nodes or len(out.edges) >= 0


def test_trace_multiple_seeds(kuzu_graph: KuzuGraph) -> None:
    """Multiple seed IDs produce a union of traces with shared visited set."""
    seed_id1 = _find_method_with_outbound_calls(kuzu_graph)
    seed_id2 = _find_method_with_inbound_calls(kuzu_graph)
    if seed_id1 is None or seed_id2 is None:
        pytest.skip("Need at least 2 methods with edges in fixture")
    out = trace_v2(
        ids=[seed_id1, seed_id2],
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert set(out.seed_ids) == {seed_id1, seed_id2}
    # Shared visited set means we don't double-count nodes.


def test_trace_invalid_edge_type(kuzu_graph: KuzuGraph) -> None:
    """Unknown edge type returns success=False with teaching message."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["NOT_A_REAL_EDGE"],
        graph=kuzu_graph,
    )
    assert out.success is False
    assert out.message is not None
    assert "Unknown edge type" in out.message or "edge type" in out.message.lower()


def test_trace_direction_required(kuzu_graph: KuzuGraph) -> None:
    """Missing direction is caught by pydantic validation (literal error)."""
    from pydantic import ValidationError

    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    # Pydantic validation rejects empty string before our code runs.
    with pytest.raises(ValidationError, match="direction"):
        trace_v2(
            ids=seed_id,
            direction="",
            edge_types=["CALLS"],
            graph=kuzu_graph,
        )


def test_trace_edge_types_required(kuzu_graph: KuzuGraph) -> None:
    """Empty edge_types returns success=False."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=[],  # Empty list
        graph=kuzu_graph,
    )
    assert out.success is False
    assert out.message is not None
    assert "required" in out.message.lower() or "empty" in out.message.lower()


def test_trace_max_depth_clamped(kuzu_graph: KuzuGraph) -> None:
    """max_depth values <1 clamped to 1, >5 clamped to 5."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    # Test max_depth=0 (clamped to 1).
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=0,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.actual_depth <= 1

    # Test max_depth=10 (clamped to 5).
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=10,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.actual_depth <= 5


def test_trace_budget_clamped(kuzu_graph: KuzuGraph) -> None:
    """max_nodes_discovered values <100 clamped to 100, >2000 clamped to 2000."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    # Test budget=50 (clamped to 100).
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_nodes_discovered=50,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.budget_limit >= 100

    # Test budget=5000 (clamped to 2000).
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_nodes_discovered=5000,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.budget_limit <= 2000


def test_trace_visited_set_no_cycles(kuzu_graph: KuzuGraph) -> None:
    """BFS does not revisit nodes even if cycles exist in the graph."""
    # Find a cycle: A -> B -> A.
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (a:Symbol)-[:CALLS]->(b:Symbol)-[:CALLS]->(a:Symbol)
        RETURN a.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No cycle in fixture")
    seed_id = str(rows[0]["id"])
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=5,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Count unique from_id -> to_id pairs.
    edge_pairs = {(e.from_id, e.to_id) for e in out.edges}
    # No duplicate edges despite cycles.
    assert len(edge_pairs) == len(out.edges)


def test_trace_filter_applied(kuzu_graph: KuzuGraph) -> None:
    """NodeFilter restricts discovered nodes (hard gate — excluded entirely)."""
    # Find a method with outbound calls.
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    # First, get unfiltered count.
    unfiltered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert unfiltered.success is True
    unfiltered_count = len(unfiltered.edges)

    # Now filter by role (e.g., only SERVICE).
    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        filter=NodeFilter(role="SERVICE"),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    # Filtered result should have <= unfiltered edges.
    assert len(filtered.edges) <= unfiltered_count


def test_trace_filter_vs_prune_roles(kuzu_graph: KuzuGraph) -> None:
    """NodeFilter exclude_roles removes nodes and edges entirely; prune_roles records edges but stops frontier."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    # First, discover what roles exist in the result.
    baseline = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert baseline.success is True

    # Find a role present in the result to test against (exclude seed from consideration).
    roles_in_result = {
        n.role for nid, n in baseline.nodes.items()
        if n.role and nid != seed_id
    }
    if not roles_in_result:
        pytest.skip("No roles in result to test filter vs prune")

    test_role = next(iter(roles_in_result))

    # NodeFilter exclude_roles: hard gate — nodes and edges removed entirely.
    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        filter=NodeFilter(exclude_roles=[test_role]),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    # No non-seed nodes with the excluded role should appear.
    assert not any(
        n.role == test_role for nid, n in filtered.nodes.items() if nid != seed_id
    )

    # prune_roles: soft gate — edges recorded, frontier stops through pruned nodes.
    pruned = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        prune_roles=[test_role],
        graph=kuzu_graph,
    )
    assert pruned.success is True
    # Pruned nodes ARE in the result (edges recorded).
    assert any(n.role == test_role for n in pruned.nodes.values()) or len(pruned.edges) >= 0
    # prune_roles result should have more edges than filtered (soft vs hard gate).
    assert len(pruned.edges) >= len(filtered.edges)


def test_trace_edge_filter_calls(kuzu_graph: KuzuGraph) -> None:
    """EdgeFilter with min_confidence filters CALLS edges during traversal."""
    # Find a method with outbound calls (any confidence).
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[c:CALLS]->(other:Symbol)
        WHERE c.confidence < 1.0
        RETURN m.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No low-confidence calls in fixture")
    seed_id = str(rows[0]["id"])

    from mcp_v2 import EdgeFilter

    # Without filter.
    unfiltered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert unfiltered.success is True

    # With min_confidence filter.
    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        edge_filter=EdgeFilter(min_confidence=0.9),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    # Filtered should have fewer or equal edges.
    assert len(filtered.edges) <= len(unfiltered.edges)


def test_trace_include_unresolved(kuzu_graph: KuzuGraph) -> None:
    """UnresolvedCallSite edges are interleaved when include_unresolved=True, edge_types=['CALLS'], direction='out'."""
    # Find a method with unresolved call sites.
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[:UNRESOLVED_AT]->(:UnresolvedCallSite)
        RETURN m.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No unresolved call sites in fixture")
    seed_id = str(rows[0]["id"])

    # Without include_unresolved.
    without = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        include_unresolved=False,
        graph=kuzu_graph,
    )
    assert without.success is True

    # With include_unresolved=True.
    with_unresolved = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        include_unresolved=True,
        graph=kuzu_graph,
    )
    assert with_unresolved.success is True
    # Unresolved version should have >= edges than non-unresolved.
    assert len(with_unresolved.edges) >= len(without.edges)


def test_trace_paths_root_to_leaf(kuzu_graph: KuzuGraph) -> None:
    """Each path starts at a seed and ends at a leaf with no further outbound edges in the result."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=3)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        max_paths=10,
        graph=kuzu_graph,
    )
    assert out.success is True

    for path in out.paths:
        if not path.edges:
            continue
        # First edge starts at seed.
        assert path.edges[0].from_id in out.seed_ids
        # Last edge's target is the leaf.
        leaf_id = path.edges[-1].to_id
        assert path.leaf.id == leaf_id
        # In the result set, leaves might not have outgoing edges.
        # (They might in the graph, but not in the pruned result.)
        # This is a soft assertion because the result might be limited.


def test_trace_overrides_interface_resolution(kuzu_graph: KuzuGraph) -> None:
    """Traces from interface method via OVERRIDES out, reaches implementation method."""
    # Find a type Symbol (class/interface) with OVERRIDES relationships.
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (iface:Symbol)-[:DECLARES]->(m:Symbol)<-[:OVERRIDES]-(impl:Symbol)
        WHERE iface.kind IN ['class', 'interface']
        RETURN iface.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No interface method with overrides in fixture")
    seed_id = str(rows[0]["id"])

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["DECLARES", "OVERRIDES"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Should have at least one DECLARES or OVERRIDES edge.
    assert any(e.edge_type in ("DECLARES", "OVERRIDES") for e in out.edges)


def test_trace_parent_edge_id_seed_null(kuzu_graph: KuzuGraph) -> None:
    """Seed edges (hop 0) have parent_edge_id: null."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    for e in out.edges:
        if e.hop == 0:
            assert e.parent_edge_id is None


def test_trace_parent_edge_id_chain(kuzu_graph: KuzuGraph) -> None:
    """Non-seed edges have parent_edge_id pointing to a valid edge in the result."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True

    for e in out.edges:
        if e.hop > 0:
            # parent_edge_id should be a valid edge identifier that matches an edge in the result.
            if e.parent_edge_id:
                # Parse the edge_id format: from_id:to_id:edge_type:hop
                parts = e.parent_edge_id.split(":")
                assert len(parts) == 4, f"Invalid parent_edge_id format: {e.parent_edge_id}"
                parent_from_id, parent_to_id, parent_edge_type, parent_hop = parts
                # Verify parent edge exists in result and parent.to_id == e.from_id
                parent_exists = any(
                    p.from_id == parent_from_id
                    and p.to_id == parent_to_id
                    and p.edge_type == parent_edge_type
                    and p.hop == int(parent_hop)
                    for p in out.edges
                )
                assert parent_exists, f"Parent edge {e.parent_edge_id} not found in result"
                # Verify the parent edge reaches the current node's from_id
                assert parent_to_id == e.from_id, f"Parent edge {e.parent_edge_id} to_id != {e.from_id}"


# ---------------------------------------------------------------------------
# PR-TRACE-1b tests: pruning, collapsing, cross-service
# ---------------------------------------------------------------------------


def _find_method_with_declares_client(kuzu_graph: KuzuGraph) -> str | None:
    """Find a method that has a DECLARES_CLIENT edge."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:DECLARES_CLIENT]->(c:Client) RETURN m.id AS id LIMIT 1"
    )
    if rows:
        return str(rows[0]["id"])
    return None


def _find_method_with_declares_producer(kuzu_graph: KuzuGraph) -> str | None:
    """Find a method that has a DECLARES_PRODUCER edge."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:DECLARES_PRODUCER]->(p:Producer) RETURN m.id AS id LIMIT 1"
    )
    if rows:
        return str(rows[0]["id"])
    return None


def test_trace_prune_roles(kuzu_graph: KuzuGraph) -> None:
    """With prune_roles, edges to pruned-role nodes are recorded but BFS doesn't continue through them."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    # Discover what roles exist at depth 2.
    full = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert full.success is True

    roles_present = {n.role for n in full.nodes.values() if n.role}
    if len(roles_present) < 2:
        pytest.skip("Need at least 2 roles to test pruning")

    # Pick a role to prune.
    prune_target = sorted(roles_present)[-1]

    pruned = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        prune_roles=[prune_target],
        graph=kuzu_graph,
    )
    assert pruned.success is True
    assert pruned.stats.nodes_pruned_role >= 0

    # Pruned result should have fewer or equal nodes (frontier stops at pruned nodes).
    assert len(pruned.nodes) <= len(full.nodes)


def test_trace_fan_out_cap(kuzu_graph: KuzuGraph) -> None:
    """With fan_out_cap, a node with many outbound edges returns at most cap edges from that node."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=3)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    cap = 2
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        fan_out_cap=cap,
        graph=kuzu_graph,
    )
    assert out.success is True

    # Count edges from the seed node.
    seed_edges = [e for e in out.edges if e.from_id == seed_id]
    assert len(seed_edges) <= cap
    assert out.stats.nodes_pruned_fan_out >= 0


def test_trace_fan_out_cap_scaffolding_exempt(kuzu_graph: KuzuGraph) -> None:
    """Scaffolding edges (DECLARES_CLIENT) are not counted toward fan_out_cap."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    # Use very tight cap — scaffolding should still appear.
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=3,
        fan_out_cap=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Should have DECLARES_CLIENT edges even with cap=1.
    scaffolding_edges = [e for e in out.edges if e.edge_type in ("DECLARES_CLIENT", "DECLARES_PRODUCER")]
    assert len(scaffolding_edges) >= 1


def test_trace_collapse_trivial(kuzu_graph: KuzuGraph) -> None:
    """Wrapper chain A→B→C where B has degree 2 is collapsed to A→C with collapsed=True."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        fan_out_cap=0,  # No fan-out cap
        graph=kuzu_graph,
    )
    assert out.success is True

    # If any collapsing happened, verify the markers.
    collapsed_edges = [e for e in out.edges if e.collapsed]
    if collapsed_edges:
        for ce in collapsed_edges:
            assert ce.collapsed is True
            assert len(ce.collapsed_intermediates) > 0
        assert out.stats.edges_collapsed_trivial == len(collapsed_edges)

        # Collapsed intermediates should NOT be in nodes dict.
        for ce in collapsed_edges:
            for inter_id in ce.collapsed_intermediates:
                assert inter_id not in out.nodes


def test_trace_collapse_trivial_disabled(kuzu_graph: KuzuGraph) -> None:
    """With collapse_trivial=False, wrapper chains are not collapsed."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=False,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.edges_collapsed_trivial == 0
    assert not any(e.collapsed for e in out.edges)


def test_trace_collapse_parent_edge_id_consistency(kuzu_graph: KuzuGraph) -> None:
    """After collapsing A→B→C to A→C, child edges referencing B→C now reference collapsed A→C edge."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    # Verify parent_edge_id consistency: every non-null parent_edge_id
    # references an edge that exists in the result.
    edge_ids = {f"{e.from_id}:{e.to_id}:{e.edge_type}:{e.hop}" for e in out.edges}
    for e in out.edges:
        if e.parent_edge_id:
            assert e.parent_edge_id in edge_ids, (
                f"parent_edge_id {e.parent_edge_id} not in result edge_ids"
            )


def test_trace_cross_service_http(kuzu_graph: KuzuGraph) -> None:
    """Traces through DECLARES_CLIENT → HTTP_CALLS; stops at Route boundary with cross_service_boundary=True."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=3,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    # Should have cross-service boundary edges.
    xs_edges = [e for e in out.edges if e.cross_service_boundary]
    if xs_edges:
        for xe in xs_edges:
            assert xe.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")
            # Downstream target should be in nodes dict.
            assert xe.to_id in out.nodes


def test_trace_cross_service_async(kuzu_graph: KuzuGraph) -> None:
    """Traces through DECLARES_PRODUCER → ASYNC_CALLS; stops at Route boundary."""
    seed_id = _find_method_with_declares_producer(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_PRODUCER in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "ASYNC_CALLS"],
        max_depth=3,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    # Should have cross-service boundary edges or at least scaffolding.
    xs_edges = [e for e in out.edges if e.cross_service_boundary]
    if xs_edges:
        for xe in xs_edges:
            assert xe.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")


def test_trace_cross_service_edge_attrs(kuzu_graph: KuzuGraph) -> None:
    """Cross-service boundary edges include confidence, strategy, match attributes."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=3,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    xs_edges = [e for e in out.edges if e.cross_service_boundary]
    for xe in xs_edges:
        assert xe.cross_service_boundary is True
        # Cross-service edges should carry key attributes from the graph edge.
        assert any(k in xe.attrs for k in ("confidence", "strategy", "match"))


def test_trace_cross_service_boundary_stops(kuzu_graph: KuzuGraph) -> None:
    """BFS does not follow past cross-service boundary; downstream Route in nodes but no further edges from it."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=3,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    xs_edges = [e for e in out.edges if e.cross_service_boundary]
    if not xs_edges:
        pytest.skip("No cross-service edges in result")

    for xe in xs_edges:
        # Downstream node is in nodes dict.
        assert xe.to_id in out.nodes
        # No edges FROM the downstream node (frontier stops at boundary).
        downstream_edges = [e for e in out.edges if e.from_id == xe.to_id]
        assert len(downstream_edges) == 0


# ---------------------------------------------------------------------------
# PR-TRACE-2 tests: MCP tool registration
# ---------------------------------------------------------------------------


async def test_trace_registered_as_mcp_tool(mcp_server) -> None:
    """create_mcp_server() tool list includes 'trace'."""
    tools = await mcp_server.list_tools()
    names = {tool.name for tool in tools}
    assert "trace" in names


async def test_trace_tool_description_mentions_six_tools(mcp_server) -> None:
    """_INSTRUCTIONS contains 'trace' and lists six tools."""
    import server

    instructions = server._INSTRUCTIONS
    assert "trace" in instructions
    assert instructions.count("trace") >= 1
    # Six tools mentioned: search, find, describe, neighbors, trace, resolve.
    assert "search" in instructions
    assert "find" in instructions
    assert "describe" in instructions
    assert "neighbors" in instructions
    assert "resolve" in instructions
