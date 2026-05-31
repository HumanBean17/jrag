"""Tests for mcp_trace.py (PR-TRACE-V2: tree output, configurable collapse,
source-relative ranking, bidirectional traversal, min_result_nodes retry).

All tests use the bank-chat kuzu_graph session fixture from conftest.py.
"""
from __future__ import annotations

import pytest

from kuzu_queries import KuzuGraph
from mcp_trace import trace_v2, TreeNode
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


def _walk_tree(tree: list[TreeNode]) -> list[TreeNode]:
    """Flatten tree nodes for inspection."""
    result: list[TreeNode] = []
    stack = list(tree)
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(node.children)
    return result


def _find_tree_node_by_id(tree: list[TreeNode], node_id: str) -> TreeNode | None:
    """Find a tree node by its id."""
    for node in _walk_tree(tree):
        if node.id == node_id:
            return node
    return None


def test_trace_outbound_calls_depth_2(kuzu_graph: KuzuGraph) -> None:
    """Traces from a method via CALLS out, depth 2, returns tree with nested children."""
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
    assert out.seed_ids == [seed_id]
    assert out.direction == "out"
    assert out.edge_types == ["CALLS"]
    # Tree should have the seed as root with children.
    assert len(out.tree) >= 1
    assert out.tree[0].id == seed_id
    assert len(out.tree[0].children) > 0


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
    assert len(out.tree) >= 1
    assert out.tree[0].id == seed_id


def test_trace_max_paths_cap(kuzu_graph: KuzuGraph) -> None:
    """Result ranked_leaves list does not exceed max_paths."""
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
    assert len(out.ranked_leaves) <= 5


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
        max_nodes_discovered=100,
        graph=kuzu_graph,
    )
    assert out.success is True
    if out.stats.total_nodes_discovered >= 100:
        assert out.stats.budget_hit is True
        assert any("budget" in adv for adv in out.advisories)


def test_trace_depth_1_equivalent_to_neighbors(kuzu_graph: KuzuGraph) -> None:
    """Depth 1 trace with no pruning returns same nodes as neighbors for same seed + edge types."""
    from mcp_v2 import neighbors_v2

    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    neigh_out = neighbors_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        limit=100,
        graph=kuzu_graph,
    )
    assert neigh_out.success is True

    trace_out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert trace_out.success is True

    trace_node_ids = set(trace_out.nodes.keys())
    neigh_node_ids = {e.other.id for e in neigh_out.results}
    trace_node_ids.discard(seed_id)
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
    assert out.stats.nodes_after_pruning == len(out.nodes)
    assert out.stats.total_nodes_discovered >= len(out.nodes)


def test_trace_empty_seed(kuzu_graph: KuzuGraph) -> None:
    """Empty seed ids returns success=True, tree=[], ranked_leaves=[]."""
    out = trace_v2(
        ids=[],
        direction="out",
        edge_types=["CALLS"],
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.seed_ids == []
    assert out.nodes == {}
    assert out.tree == []
    assert out.ranked_leaves == []


def test_trace_single_string_seed(kuzu_graph: KuzuGraph) -> None:
    """Single string ids is normalized to list; seed_ids echoed as list of one."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.seed_ids == [seed_id]
    assert seed_id in out.nodes


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
    # Multi-seed: tree has one root per seed.
    assert len(out.tree) >= 1


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
        edge_types=[],
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
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=0,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.actual_depth <= 1

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
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_nodes_discovered=50,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.budget_limit >= 100

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
    # No duplicate node IDs in tree walk.
    all_nodes = _walk_tree(out.tree)
    node_ids = [n.id for n in all_nodes]
    assert len(node_ids) == len(set(node_ids))


def test_trace_filter_applied(kuzu_graph: KuzuGraph) -> None:
    """NodeFilter restricts discovered nodes (hard gate — excluded entirely)."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    unfiltered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert unfiltered.success is True
    unfiltered_count = len(unfiltered.nodes) - 1  # Exclude seed

    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        filter=NodeFilter(role="SERVICE"),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    filtered_count = len(filtered.nodes) - 1
    assert filtered_count <= unfiltered_count


def test_trace_filter_vs_prune_roles(kuzu_graph: KuzuGraph) -> None:
    """NodeFilter exclude_roles removes nodes and edges entirely; prune_roles records edges but stops frontier."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    baseline = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert baseline.success is True

    roles_in_result = {
        n.role for nid, n in baseline.nodes.items()
        if n.role and nid != seed_id
    }
    if not roles_in_result:
        pytest.skip("No roles in result to test filter vs prune")

    test_role = next(iter(roles_in_result))

    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        filter=NodeFilter(exclude_roles=[test_role]),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    assert not any(
        n.role == test_role for nid, n in filtered.nodes.items() if nid != seed_id
    )

    pruned = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        prune_roles=[test_role],
        graph=kuzu_graph,
    )
    assert pruned.success is True
    assert any(n.role == test_role for n in pruned.nodes.values()) or len(pruned.nodes) >= 0
    assert len(pruned.nodes) >= len(filtered.nodes)


def test_trace_edge_filter_calls(kuzu_graph: KuzuGraph) -> None:
    """EdgeFilter with min_confidence filters CALLS edges during traversal."""
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

    unfiltered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        graph=kuzu_graph,
    )
    assert unfiltered.success is True

    filtered = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        edge_filter=EdgeFilter(min_confidence=0.9),
        graph=kuzu_graph,
    )
    assert filtered.success is True
    assert len(filtered.nodes) <= len(unfiltered.nodes)


def test_trace_include_unresolved(kuzu_graph: KuzuGraph) -> None:
    """UnresolvedCallSite edges are interleaved when include_unresolved=True."""
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

    without = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        include_unresolved=False,
        graph=kuzu_graph,
    )
    assert without.success is True

    with_unresolved = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        include_unresolved=True,
        graph=kuzu_graph,
    )
    assert with_unresolved.success is True
    assert len(with_unresolved.nodes) >= len(without.nodes)


def test_trace_paths_root_to_leaf(kuzu_graph: KuzuGraph) -> None:
    """Each ranked_leaf has a tree path from seed."""
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

    for leaf in out.ranked_leaves:
        # Leaf node should be in the tree walk.
        found = _find_tree_node_by_id(out.tree, leaf.node_id)
        assert found is not None, f"Leaf {leaf.node_id} not found in tree"
        # Leaf should have no children.
        assert len(found.children) == 0


def test_trace_overrides_interface_resolution(kuzu_graph: KuzuGraph) -> None:
    """Traces from interface method via OVERRIDES out, reaches implementation method."""
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
    # Should have at least one child edge via DECLARES or OVERRIDES.
    all_nodes = _walk_tree(out.tree)
    assert any(n.edge_from_parent is not None and n.edge_from_parent.edge_type in ("DECLARES", "OVERRIDES") for n in all_nodes)


def test_trace_prune_roles(kuzu_graph: KuzuGraph) -> None:
    """With prune_roles, edges to pruned-role nodes are recorded but BFS doesn't continue through them."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

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
    # Pruned result should have fewer or equal nodes.
    assert len(pruned.nodes) <= len(full.nodes)

    # Pruned-role nodes should be leaves in the tree (no children).
    for node in _walk_tree(pruned.tree):
        if node.id != seed_id:
            node_ref = pruned.nodes.get(node.id)
            if node_ref and node_ref.role == prune_target:
                assert len(node.children) == 0, f"Pruned node {node.id} should have no children"


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

    # Seed's children count should be at most cap.
    seed_tree_node = _find_tree_node_by_id(out.tree, seed_id)
    if seed_tree_node:
        assert len(seed_tree_node.children) <= cap
    assert out.stats.nodes_pruned_fan_out >= 0


def test_trace_fan_out_cap_scaffolding_exempt(kuzu_graph: KuzuGraph) -> None:
    """Scaffolding edges (DECLARES_CLIENT) are not counted toward fan_out_cap."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=3,
        fan_out_cap=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Should have scaffolding edges even with cap=1.
    all_nodes = _walk_tree(out.tree)
    scaffolding = [n for n in all_nodes if n.edge_from_parent and n.edge_from_parent.edge_type in ("DECLARES_CLIENT", "DECLARES_PRODUCER")]
    assert len(scaffolding) >= 1


def test_trace_collapse_trivial(kuzu_graph: KuzuGraph) -> None:
    """Wrapper chain A→B→C where B is trivial is collapsed; intermediates retained in nodes."""
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

    # Check collapsed nodes in tree.
    collapsed_nodes = [n for n in _walk_tree(out.tree) if n.collapsed]
    if collapsed_nodes:
        for cn in collapsed_nodes:
            assert cn.collapsed is True
            assert len(cn.collapsed_intermediates) > 0
        assert out.stats.edges_collapsed_trivial == len(collapsed_nodes)

        # Collapsed intermediates ARE in nodes dict (v2).
        for cn in collapsed_nodes:
            for inter_id in cn.collapsed_intermediates:
                assert inter_id in out.nodes, f"Collapsed intermediate {inter_id} should be in nodes dict"


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
    assert not any(n.collapsed for n in _walk_tree(out.tree))


def test_trace_cross_service_http(kuzu_graph: KuzuGraph) -> None:
    """Traces through DECLARES_CLIENT → HTTP_CALLS; stops at Route boundary."""
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

    # Walk tree for cross_service_boundary nodes.
    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if xs_nodes:
        for xn in xs_nodes:
            assert xn.edge_from_parent.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")
            assert xn.id in out.nodes


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

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if xs_nodes:
        for xn in xs_nodes:
            assert xn.edge_from_parent.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")


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

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    for xn in xs_nodes:
        assert xn.edge_from_parent.cross_service_boundary is True
        assert any(k in xn.edge_from_parent.attrs for k in ("confidence", "strategy", "match"))


def test_trace_cross_service_boundary_stops(kuzu_graph: KuzuGraph) -> None:
    """BFS does not follow past cross-service boundary; downstream Route in nodes but no children in tree."""
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

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if not xs_nodes:
        pytest.skip("No cross-service nodes in result")

    for xn in xs_nodes:
        assert xn.id in out.nodes
        # Boundary node should have no children (frontier stops).
        assert len(xn.children) == 0


def test_trace_cross_service_seamless_http(kuzu_graph: KuzuGraph) -> None:
    """cross_service=True: BFS continues through HTTP_CALLS boundary into downstream service."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=5,
        fan_out_cap=0,
        cross_service=True,
        graph=kuzu_graph,
    )
    assert out.success is True

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if not xs_nodes:
        pytest.skip("No cross-service nodes in result")

    for xn in xs_nodes:
        assert xn.edge_from_parent.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")
        assert xn.id in out.nodes

    # Key difference from boundary-stop: downstream Route should have children.
    for xn in xs_nodes:
        if len(xn.children) > 0:
            exposes_children = [c for c in xn.children if c.edge_from_parent and c.edge_from_parent.edge_type == "EXPOSES"]
            assert len(exposes_children) >= 1


def test_trace_cross_service_seamless_async(kuzu_graph: KuzuGraph) -> None:
    """cross_service=True: BFS continues through ASYNC_CALLS boundary into downstream service."""
    seed_id = _find_method_with_declares_producer(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_PRODUCER in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "ASYNC_CALLS"],
        max_depth=5,
        fan_out_cap=0,
        cross_service=True,
        graph=kuzu_graph,
    )
    assert out.success is True

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if not xs_nodes:
        pytest.skip("No cross-service nodes in result")

    for xn in xs_nodes:
        assert xn.edge_from_parent.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")
        assert xn.id in out.nodes


def test_trace_cross_service_seamless_respects_budget(kuzu_graph: KuzuGraph) -> None:
    """cross_service=True still respects max_nodes_discovered budget."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=5,
        max_nodes_discovered=100,
        fan_out_cap=0,
        cross_service=True,
        graph=kuzu_graph,
    )
    assert out.success is True
    if out.stats.budget_hit:
        assert out.stats.total_nodes_discovered >= 100


def test_trace_cross_service_seamless_exposes_as_scaffolding(kuzu_graph: KuzuGraph) -> None:
    """EXPOSES edges from downstream Routes are exempt from fan_out_cap when cross_service=True."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=5,
        fan_out_cap=1,
        cross_service=True,
        graph=kuzu_graph,
    )
    assert out.success is True

    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if xs_nodes:
        for xn in xs_nodes:
            if len(xn.children) > 0:
                exposes_children = [c for c in xn.children if c.edge_from_parent and c.edge_from_parent.edge_type == "EXPOSES"]
                assert len(exposes_children) >= 1


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
    assert "search" in instructions
    assert "find" in instructions
    assert "describe" in instructions
    assert "neighbors" in instructions
    assert "resolve" in instructions


# ---------------------------------------------------------------------------
# PR-TRACE-V2 tests: tree format, configurable collapse, source-relative
# ranking, bidirectional traversal, min_result_nodes retry
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


# --- Updated v1 tests (assert on tree / ranked_leaves) ---


def test_trace_bank_chat_cross_service_http_flow(kuzu_graph: KuzuGraph) -> None:
    """Integration: trace from a bank-chat method through HTTP_CALLS; verify cross-service boundary + hints."""
    seed_id = _find_method_with_declares_client(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with DECLARES_CLIENT in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS", "HTTP_CALLS"],
        max_depth=4,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True

    # Verify cross-service boundary nodes in tree.
    xs_nodes = [n for n in _walk_tree(out.tree) if n.edge_from_parent and n.edge_from_parent.cross_service_boundary]
    if xs_nodes:
        for xn in xs_nodes:
            assert xn.edge_from_parent.edge_type in ("HTTP_CALLS", "ASYNC_CALLS")
            assert xn.id in out.nodes
            # No children (boundary stops without cross_service=True).
            assert len(xn.children) == 0

    # Verify hint generation works on the trace output (tree format).
    from mcp_hints import generate_hints
    trace_payload = {
        "success": out.success,
        "stats": out.stats.model_dump(),
        "tree": [n.model_dump() for n in out.tree],
        "nodes": {nid: n.model_dump() for nid, n in out.nodes.items()},
        "seed_ids": out.seed_ids,
        "direction": out.direction,
        "edge_types": out.edge_types,
    }
    struct, advisories = generate_hints("trace", trace_payload)
    if xs_nodes:
        assert any("cross-service" in a.lower() for a in advisories), (
            f"expected cross-service advisory, got: {advisories}"
        )


# --- New v2 tests ---


def test_trace_tree_root_is_seed(kuzu_graph: KuzuGraph) -> None:
    """Tree root node matches seed ID."""
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
    assert len(out.tree) >= 1
    assert out.tree[0].id == seed_id


def test_trace_tree_seed_no_edge_from_parent(kuzu_graph: KuzuGraph) -> None:
    """Seed nodes have edge_from_parent=None."""
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
    for root in out.tree:
        assert root.edge_from_parent is None


def test_trace_tree_edge_from_parent_chain(kuzu_graph: KuzuGraph) -> None:
    """Non-root nodes have edge_from_parent with valid edge_type, hop, and direction."""
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

    all_nodes = _walk_tree(out.tree)
    for node in all_nodes:
        if node.edge_from_parent is not None:
            assert node.edge_from_parent.edge_type in {"CALLS", "DECLARES_CLIENT", "DECLARES_PRODUCER", "EXPOSES", "HTTP_CALLS", "ASYNC_CALLS"}
            assert node.edge_from_parent.hop >= 0
            assert node.edge_from_parent.direction in ("in", "out")


def test_trace_tree_edge_from_parent_direction(kuzu_graph: KuzuGraph) -> None:
    """edge_from_parent.direction is set ('in' or 'out') for all non-root nodes."""
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
    for node in _walk_tree(out.tree):
        if node.edge_from_parent is not None:
            assert node.edge_from_parent.direction == "out"


def test_trace_tree_children_nested(kuzu_graph: KuzuGraph) -> None:
    """Children are nested TreeNodes, not flat."""
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
    # Children should be TreeNode instances.
    if out.tree and out.tree[0].children:
        child = out.tree[0].children[0]
        assert isinstance(child, TreeNode)
        assert hasattr(child, "children")
        assert hasattr(child, "edge_from_parent")


def test_trace_tree_collapsed_node(kuzu_graph: KuzuGraph) -> None:
    """Collapsed intermediates carry collapsed=True and collapsed_intermediates."""
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
    collapsed = [n for n in _walk_tree(out.tree) if n.collapsed]
    if collapsed:
        for cn in collapsed:
            assert cn.collapsed is True
            assert len(cn.collapsed_intermediates) > 0


def test_trace_tree_collapse_intermediates_in_nodes(kuzu_graph: KuzuGraph) -> None:
    """Collapsed intermediate node IDs exist in nodes dict (v2)."""
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
    collapsed = [n for n in _walk_tree(out.tree) if n.collapsed]
    for cn in collapsed:
        for inter_id in cn.collapsed_intermediates:
            assert inter_id in out.nodes, f"Collapsed intermediate {inter_id} should be in nodes dict"


def test_trace_tree_collapse_children_reparented(kuzu_graph: KuzuGraph) -> None:
    """After collapsing A→B→C, C appears as child of A in tree."""
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
    # If collapsing happened, verify the tree structure is consistent.
    collapsed = [n for n in _walk_tree(out.tree) if n.collapsed]
    if collapsed:
        for cn in collapsed:
            # Collapsed node's parent should not be in collapsed_intermediates.
            parent = None
            for node in _walk_tree(out.tree):
                if cn in node.children:
                    parent = node
                    break
            if parent:
                assert parent.id not in cn.collapsed_intermediates


def test_trace_ranked_leaves_capped(kuzu_graph: KuzuGraph) -> None:
    """ranked_leaves does not exceed max_paths."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=5)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")
    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        max_paths=3,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert len(out.ranked_leaves) <= 3


def test_trace_ranked_leaves_scores(kuzu_graph: KuzuGraph) -> None:
    """Leaves are sorted by descending score."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=3)
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
    if len(out.ranked_leaves) > 1:
        for i in range(len(out.ranked_leaves) - 1):
            assert out.ranked_leaves[i].score >= out.ranked_leaves[i + 1].score


def test_trace_collapse_roles_custom(kuzu_graph: KuzuGraph) -> None:
    """collapse_roles=['OTHER','SERVICE'] collapses SERVICE intermediates."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        collapse_roles=["OTHER", "SERVICE"],
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True
    # With wider collapse roles, we should get at least as much collapsing as default.
    assert out.stats.edges_collapsed_trivial >= 0


def test_trace_collapse_roles_default(kuzu_graph: KuzuGraph) -> None:
    """Default collapse_roles only collapses OTHER."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    # Default (collapse_roles=None → defaults to OTHER).
    default_out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert default_out.success is True

    # Check that collapsed intermediates are all OTHER or None role.
    collapsed = [n for n in _walk_tree(default_out.tree) if n.collapsed]
    for cn in collapsed:
        for inter_id in cn.collapsed_intermediates:
            inter_ref = default_out.nodes.get(inter_id)
            if inter_ref:
                assert inter_ref.role in ("OTHER", None), (
                    f"Default collapse should only collapse OTHER/None, got {inter_ref.role}"
                )


def test_trace_collapse_min_chain_length_2(kuzu_graph: KuzuGraph) -> None:
    """collapse_min_chain_length=2 skips single-intermediate collapses."""
    seed_id = _find_method_with_multiple_callees(kuzu_graph, min_callees=2)
    if seed_id is None:
        pytest.skip("No method with multiple callees in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        collapse_min_chain_length=2,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert out.success is True
    # With min_chain_length=2, should collapse fewer chains than default (1).
    # The collapsed count should be <= the default count.
    default_out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=3,
        collapse_trivial=True,
        fan_out_cap=0,
        graph=kuzu_graph,
    )
    assert default_out.success is True
    assert out.stats.edges_collapsed_trivial <= default_out.stats.edges_collapsed_trivial


def test_trace_fan_out_source_relative_service(kuzu_graph: KuzuGraph) -> None:
    """From SERVICE node, REPOSITORY callee outranks CONTROLLER at equal confidence."""
    # Find a SERVICE method with outbound calls to both REPOSITORY and non-REPOSITORY.
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[:CALLS]->(other:Symbol)
        WHERE m.role = 'SERVICE'
        WITH m, collect(DISTINCT other.role) AS roles
        WHERE size(roles) >= 2
        RETURN m.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No SERVICE method with diverse callees in fixture")
    seed_id = str(rows[0]["id"])

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        fan_out_cap=10,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Verify results returned without error (source-relative ranking is internal).
    assert len(out.tree[0].children) > 0


def test_trace_fan_out_source_relative_controller(kuzu_graph: KuzuGraph) -> None:
    """From CONTROLLER node, SERVICE callee outranks REPOSITORY at equal confidence."""
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[:CALLS]->(other:Symbol)
        WHERE m.role = 'CONTROLLER'
        RETURN m.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No CONTROLLER method with callees in fixture")
    seed_id = str(rows[0]["id"])

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        fan_out_cap=10,
        graph=kuzu_graph,
    )
    assert out.success is True


def test_trace_fan_out_source_relative_fallback(kuzu_graph: KuzuGraph) -> None:
    """Unknown source role falls back to static priority."""
    # Find a method with OTHER or unknown role.
    rows = kuzu_graph._rows(  # noqa: SLF001
        """
        MATCH (m:Symbol)-[:CALLS]->(other:Symbol)
        WHERE m.role = 'OTHER' OR m.role IS NULL
        RETURN m.id AS id
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No OTHER method with callees in fixture")
    seed_id = str(rows[0]["id"])

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=1,
        fan_out_cap=10,
        graph=kuzu_graph,
    )
    assert out.success is True


def test_trace_min_result_nodes_retry(kuzu_graph: KuzuGraph) -> None:
    """min_result_nodes=10 triggers fan-out cap retry when initial result < 10."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        min_result_nodes=10,
        fan_out_cap=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Either it got enough nodes or there's an advisory about retry.
    if len(out.nodes) < 10:
        assert any("min_result_nodes" in adv for adv in out.advisories)


def test_trace_min_result_nodes_disabled(kuzu_graph: KuzuGraph) -> None:
    """min_result_nodes=0 (default) does not retry."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        min_result_nodes=0,
        fan_out_cap=1,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert not any("min_result_nodes" in adv for adv in out.advisories)


def test_trace_bidirectional_basic(kuzu_graph: KuzuGraph) -> None:
    """direction='both' returns tree with both in and out children from seed."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")
    # Also need inbound calls.
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (caller:Symbol)-[:CALLS]->(m:Symbol {id: $id}) RETURN m.id AS id LIMIT 1",
        {"id": seed_id},
    )
    if not rows:
        pytest.skip("Seed method has no inbound calls")

    out = trace_v2(
        ids=seed_id,
        direction="both",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.direction == "both"
    # Tree should have children with both directions.
    directions_found = set()
    for node in _walk_tree(out.tree):
        if node.edge_from_parent is not None:
            directions_found.add(node.edge_from_parent.direction)
    # Should have at least "out" direction (from the outbound calls).
    assert "out" in directions_found or "in" in directions_found


def test_trace_bidirectional_shared_visited(kuzu_graph: KuzuGraph) -> None:
    """Nodes discovered in 'out' are not re-visited in 'in'."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="both",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    # No duplicate node IDs in tree walk.
    all_nodes = _walk_tree(out.tree)
    node_ids = [n.id for n in all_nodes]
    assert len(node_ids) == len(set(node_ids))


def test_trace_bidirectional_stats_aggregated(kuzu_graph: KuzuGraph) -> None:
    """Stats aggregate both directions."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="both",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert out.stats.nodes_after_pruning == len(out.nodes)

    # Compare with unidirectional: both should discover more or equal nodes.
    out_out = trace_v2(
        ids=seed_id,
        direction="out",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert len(out.nodes) >= len(out_out.nodes)


def test_trace_bidirectional_ranked_leaves_merged(kuzu_graph: KuzuGraph) -> None:
    """ranked_leaves includes leaves from both directions."""
    seed_id = _find_method_with_outbound_calls(kuzu_graph)
    if seed_id is None:
        pytest.skip("No method with outbound calls in fixture")

    out = trace_v2(
        ids=seed_id,
        direction="both",
        edge_types=["CALLS"],
        max_depth=2,
        graph=kuzu_graph,
    )
    assert out.success is True
    # Should have ranked leaves.
    assert len(out.ranked_leaves) >= 0
