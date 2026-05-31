"""MCP trace tool — multi-hop BFS traversal with pruning.

Imports stable types from mcp_v2.py but does not modify them:
- NodeFilter, EdgeFilter, NodeRef, _node_ref_from_row, _node_kind_from_id

This module implements PR-TRACE-V2 (tree output, configurable collapse,
source-relative ranking, bidirectional traversal, min_result_nodes retry).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, validate_call

from java_ontology import EDGE_SCHEMA, VALID_ROLES
from kuzu_queries import KuzuGraph
from mcp_v2 import (
    EdgeFilter,
    NodeFilter,
    NodeRef,
    _node_ref_from_row,
    _resolve_node_kind,
)


# Valid stored edge labels (from java_ontology.py, excluding composed keys).
_TRACE_EDGE_TYPES = frozenset(EDGE_SCHEMA.keys())

# Role priority for path ranking (higher is better).
_ROLE_PRIORITY: dict[str, int] = {
    "CONTROLLER": 5,
    "SERVICE": 4,
    "REPOSITORY": 3,
    "CLIENT": 2,
    "OTHER": 1,
}

# Source-relative fan-out priority: source role → target role → priority.
# All role strings are validated against VALID_ROLES at startup.
_SOURCE_RELATIVE_PRIORITY: dict[str, dict[str, int]] = {
    "SERVICE": {
        "REPOSITORY": 5,
        "SERVICE": 4,
        "CONTROLLER": 3,
        "CLIENT": 2,
        "OTHER": 1,
    },
    "CONTROLLER": {
        "SERVICE": 5,
        "REPOSITORY": 4,
        "CLIENT": 3,
        "CONTROLLER": 2,
        "OTHER": 1,
    },
    "REPOSITORY": {
        "SERVICE": 5,
        "REPOSITORY": 4,
        "CONTROLLER": 3,
        "CLIENT": 2,
        "OTHER": 1,
    },
}

# Validate source-relative priority table against known roles.
_VALID_PRIORITY_ROLES = VALID_ROLES | frozenset({"OTHER"})
for _src_role, _target_map in _SOURCE_RELATIVE_PRIORITY.items():
    assert _src_role in _VALID_PRIORITY_ROLES, f"_SOURCE_RELATIVE_PRIORITY key {_src_role!r} not in known roles"
    for _tgt_role in _target_map:
        assert _tgt_role in _VALID_PRIORITY_ROLES, f"_SOURCE_RELATIVE_PRIORITY[{_src_role!r}] key {_tgt_role!r} not in known roles"

# Scaffolding edges exempt from fan_out_cap.
_SCAFFOLDING_EDGE_TYPES = frozenset({"DECLARES_CLIENT", "DECLARES_PRODUCER"})

# Cross-service edge types that trigger scaffolding follow.
_CROSS_SERVICE_EDGE_TYPES = frozenset({"HTTP_CALLS", "ASYNC_CALLS"})


def _role_priority(role: str | None) -> int:
    """Return numeric priority for role ranking (higher = better)."""
    if role is None:
        return 0
    return _ROLE_PRIORITY.get(role, 1)


# --- Models ---


class EdgeFromParent(BaseModel):
    """Edge metadata linking a TreeNode to its parent."""
    model_config = ConfigDict(extra="forbid")

    direction: Literal["in", "out"]
    edge_type: str
    hop: int
    confidence: float | None = None
    cross_service_boundary: bool = False
    attrs: dict[str, Any] = Field(default_factory=dict)


class TreeNode(BaseModel):
    """A node in the nested trace tree output."""
    model_config = ConfigDict(extra="forbid")

    id: str
    edge_from_parent: EdgeFromParent | None = None
    children: list[TreeNode] = Field(default_factory=list)
    collapsed: bool = False
    collapsed_intermediates: list[str] = Field(default_factory=list)


class RankedLeaf(BaseModel):
    """A ranked leaf node from the trace tree."""
    model_config = ConfigDict(extra="forbid")

    node_id: str
    depth: int
    leaf_role: str | None = None
    score: float


class TraceStats(BaseModel):
    """Traversal statistics."""
    model_config = ConfigDict(extra="forbid")

    total_nodes_discovered: int = 0
    total_edges_discovered: int = 0
    budget_hit: bool = False
    budget_limit: int = 500
    nodes_pruned_role: int = 0
    nodes_pruned_fan_out: int = 0
    edges_collapsed_trivial: int = 0
    nodes_after_pruning: int = 0
    edges_after_pruning: int = 0


class TraceOutput(BaseModel):
    """Result of a trace call."""
    model_config = ConfigDict(extra="forbid")

    success: bool
    seed_ids: list[str]
    direction: str
    edge_types: list[str]
    actual_depth: int = 0
    nodes: dict[str, NodeRef] = Field(default_factory=dict)
    tree: list[TreeNode] = Field(default_factory=list)
    ranked_leaves: list[RankedLeaf] = Field(default_factory=list)
    stats: TraceStats = Field(default_factory=TraceStats)
    message: str | None = None
    advisories: list[str] = Field(default_factory=list)


# --- Internal flat edge representation used during BFS ---


class _FlatEdge:
    """Internal flat edge during BFS (not exported)."""

    __slots__ = (
        "from_id", "to_id", "edge_type", "hop", "direction",
        "confidence", "cross_service_boundary", "attrs",
        "collapsed", "collapsed_intermediates",
    )

    def __init__(
        self,
        *,
        from_id: str,
        to_id: str,
        edge_type: str,
        hop: int,
        direction: Literal["in", "out"],
        confidence: float | None = None,
        cross_service_boundary: bool = False,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self.from_id = from_id
        self.to_id = to_id
        self.edge_type = edge_type
        self.hop = hop
        self.direction = direction
        self.confidence = confidence
        self.cross_service_boundary = cross_service_boundary
        self.attrs = attrs or {}
        self.collapsed = False
        self.collapsed_intermediates: list[str] = []


def _edge_attrs_for_row(row: dict[str, Any]) -> dict[str, Any]:
    """Extract edge attributes from a query row, excluding structural fields."""
    attrs = {
        k: v
        for k, v in row.items()
        if k not in {"source_id", "other_id", "edge_type", "stored_edge_type"}
        and v not in (None, "")
    }
    return attrs


def _neighbors_batched(
    graph: KuzuGraph,
    *,
    node_ids: list[str],
    direction: Literal["in", "out"],
    edge_types: list[str],
    edge_filter: EdgeFilter | None = None,
) -> list[dict[str, Any]]:
    """Issue a single Cypher query for all frontier nodes at one BFS hop."""
    if not node_ids:
        return []

    # Edge type expansion: OR of scalar equalities (same pattern as neighbors_v2).
    label_params = [f"l{i}" for i in range(len(edge_types))]
    label_predicate = "(" + " OR ".join(f"label(e) = ${name}" for name in label_params) + ")"
    q_params = {"ids": node_ids, **dict(zip(label_params, edge_types, strict=True))}

    # Build WHERE clause for edge_filter pushdown (CALLS only).
    wh_parts: list[str] = []
    if edge_filter is not None and edge_types == ["CALLS"]:
        if edge_filter.min_confidence is not None:
            wh_parts.append("e.confidence >= $min_confidence")
            q_params["min_confidence"] = edge_filter.min_confidence
        if edge_filter.include_strategies:
            wh_parts.append("e.strategy IN $include_strategies")
            q_params["include_strategies"] = edge_filter.include_strategies
        if edge_filter.exclude_strategies:
            wh_parts.append("NOT (e.strategy IN $exclude_strategies)")
            q_params["exclude_strategies"] = edge_filter.exclude_strategies
        if edge_filter.callee_declaring_role is not None:
            wh_parts.append("e.callee_declaring_role = $callee_declaring_role")
            q_params["callee_declaring_role"] = edge_filter.callee_declaring_role
        if edge_filter.callee_declaring_roles:
            wh_parts.append("e.callee_declaring_role IN $callee_declaring_roles")
            q_params["callee_declaring_roles"] = edge_filter.callee_declaring_roles
        if edge_filter.exclude_callee_declaring_roles:
            wh_parts.append("NOT (e.callee_declaring_role IN $exclude_callee_declaring_roles)")
            q_params["exclude_callee_declaring_roles"] = edge_filter.exclude_callee_declaring_roles

    where_clause = " AND " + " AND ".join(wh_parts) if wh_parts else ""

    if direction == "out":
        q = f"""
        MATCH (a)-[e]->(b)
        WHERE a.id IN $ids AND {label_predicate}{where_clause}
        RETURN a.id AS source_id, b.id AS other_id, label(e) AS edge_type,
               e.confidence AS confidence, e.strategy AS strategy, e.match AS match,
               e.mechanism AS mechanism, e.annotation AS annotation,
               e.field_or_param AS field_or_param, e.source AS source,
               e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte,
               e.arg_count AS arg_count, e.resolved AS resolved,
               e.callee_declaring_role AS callee_declaring_role
        """
    else:
        q = f"""
        MATCH (a)<-[e]-(b)
        WHERE a.id IN $ids AND {label_predicate}{where_clause}
        RETURN a.id AS source_id, b.id AS other_id, label(e) AS edge_type,
               e.confidence AS confidence, e.strategy AS strategy, e.match AS match,
               e.mechanism AS mechanism, e.annotation AS annotation,
               e.field_or_param AS field_or_param, e.source AS source,
               e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte,
               e.arg_count AS arg_count, e.resolved AS resolved,
               e.callee_declaring_role AS callee_declaring_role
        """

    return graph._rows(q, q_params)  # noqa: SLF001


def _load_node_record(
    graph: KuzuGraph, node_id: str, kind: Literal["symbol", "route", "client", "producer"],
) -> dict[str, Any] | None:
    """Load a node record from Kuzu."""
    if kind == "symbol":
        projection = (
            "n.id AS id, n.kind AS kind, n.name AS name, n.fqn AS fqn, n.package AS package, "
            "n.module AS module, n.microservice AS microservice, n.filename AS filename, "
            "n.start_line AS start_line, n.end_line AS end_line, n.start_byte AS start_byte, "
            "n.end_byte AS end_byte, n.modifiers AS modifiers, n.annotations AS annotations, "
            "n.capabilities AS capabilities, n.role AS role, n.signature AS signature, "
            "n.parent_id AS parent_id, n.resolved AS resolved"
        )
        label = "Symbol"
    elif kind == "route":
        projection = (
            "n.id AS id, n.kind AS kind, n.framework AS framework, n.method AS method, "
            "n.path AS path, n.path_template AS path_template, n.path_regex AS path_regex, "
            "n.topic AS topic, n.broker AS broker, n.feign_name AS feign_name, n.feign_url AS feign_url, "
            "n.microservice AS microservice, n.module AS module, n.filename AS filename, "
            "n.start_line AS start_line, n.end_line AS end_line, n.resolved AS resolved"
        )
        label = "Route"
    elif kind == "client":
        projection = (
            "n.id AS id, n.client_kind AS client_kind, n.target_service AS target_service, "
            "n.method AS method, n.path AS path, n.path_template AS path_template, "
            "n.path_regex AS path_regex, n.member_fqn AS member_fqn, n.member_id AS member_id, "
            "n.microservice AS microservice, n.module AS module, n.filename AS filename, "
            "n.start_line AS start_line, n.end_line AS end_line, n.resolved AS resolved, "
            "n.source_layer AS source_layer"
        )
        label = "Client"
    else:
        projection = (
            "n.id AS id, n.producer_kind AS producer_kind, n.topic AS topic, n.broker AS broker, "
            "n.direction AS direction, n.member_fqn AS member_fqn, n.member_id AS member_id, "
            "n.microservice AS microservice, n.module AS module, n.filename AS filename, "
            "n.start_line AS start_line, n.end_line AS end_line, n.resolved AS resolved, "
            "n.source_layer AS source_layer"
        )
        label = "Producer"
    rows = graph._rows(f"MATCH (n:{label}) WHERE n.id = $id RETURN {projection}", {"id": node_id})  # noqa: SLF001
    if not rows:
        return None
    return rows[0]


def _node_matches_filter(
    kind: Literal["symbol", "route", "client", "producer"], row: dict[str, Any], f: NodeFilter | None,
) -> bool:
    """Check if a node row matches the NodeFilter (hard gate)."""
    if f is None:
        return True
    if f.microservice and str(row.get("microservice") or "") != f.microservice:
        return False
    if f.module and str(row.get("module") or "") != f.module:
        return False
    if kind in ("client", "producer") and f.source_layer and str(row.get("source_layer") or "") != f.source_layer:
        return False
    if kind == "symbol":
        role = str(row.get("role") or "")
        fqn_val = str(row.get("fqn") or "")
        symbol_kind_val = str(row.get("kind") or "")
        if f.role and role != f.role:
            return False
        if f.exclude_roles and role in set(f.exclude_roles):
            return False
        if f.annotation and f.annotation not in list(row.get("annotations") or []):
            return False
        if f.capability and f.capability not in list(row.get("capabilities") or []):
            return False
        if f.fqn_prefix and not fqn_val.startswith(f.fqn_prefix):
            return False
        if f.symbol_kind and symbol_kind_val != f.symbol_kind:
            return False
        if f.symbol_kinds and symbol_kind_val not in set(f.symbol_kinds):
            return False
    elif kind == "route":
        if f.http_method and str(row.get("method") or "") != f.http_method:
            return False
        if f.path_prefix:
            path = str(row.get("path") or "")
            if not path.startswith(f.path_prefix):
                return False
        if f.framework and str(row.get("framework") or "") != f.framework:
            return False
    elif kind == "client":
        if f.client_kind and str(row.get("client_kind") or "") != f.client_kind:
            return False
        if f.target_service and str(row.get("target_service") or "") != f.target_service:
            return False
        if f.target_path_prefix:
            path = str(row.get("path") or "")
            if not path.startswith(f.target_path_prefix):
                return False
        if f.http_method and str(row.get("method") or "") != f.http_method:
            return False
    else:
        if f.producer_kind and str(row.get("producer_kind") or "") != f.producer_kind:
            return False
        if f.topic_prefix:
            topic = str(row.get("topic") or "")
            if not topic.startswith(f.topic_prefix):
                return False
    return True


def _fan_out_sort_key(
    row: dict[str, Any],
    nodes: dict[str, NodeRef],
    source_role: str | None = None,
) -> tuple[float, int, str]:
    """Sort key for fan_out_cap ranking: confidence desc, role priority desc, fqn asc."""
    conf = float(row.get("confidence") or 0.0)
    other_id = str(row.get("other_id") or "")
    node_ref = nodes.get(other_id)
    target_role = node_ref.role if node_ref else None

    if source_role and source_role in _SOURCE_RELATIVE_PRIORITY:
        target_prio_map = _SOURCE_RELATIVE_PRIORITY[source_role]
        role_prio = target_prio_map.get(target_role, 1) if target_role else 0
    else:
        role_prio = _role_priority(target_role)

    fqn = node_ref.fqn if node_ref else other_id
    return (-conf, -role_prio, fqn)


def _collapse_trivial_chains(
    nodes: dict[str, NodeRef],
    edges: list[_FlatEdge],
    collapse_roles: set[str] | None = None,
    collapse_min_chain_length: int = 1,
) -> int:
    """Post-BFS pass: collapse trivial chains (degree-1 intermediates).

    Collapsed intermediates are retained in the ``nodes`` dict (accessible
    standalone but not nested in the tree).

    Returns the number of edges collapsed.
    """
    if not edges:
        return 0

    if collapse_roles is None:
        collapse_roles = {"OTHER", None}

    # Build adjacency for degree counting (only CALLS edges).
    in_edges: dict[str, list[_FlatEdge]] = defaultdict(list)
    out_edges: dict[str, list[_FlatEdge]] = defaultdict(list)
    for e in edges:
        if e.collapsed:
            continue
        if e.edge_type == "CALLS":
            in_edges[e.to_id].append(e)
            out_edges[e.from_id].append(e)

    collapsed_count = 0
    edges_to_remove: set[int] = set()
    edges_to_add: list[_FlatEdge] = []

    all_node_ids = set(nodes.keys())

    # Count chain length for each candidate intermediate.
    def _chain_length(node_id: str, seen: frozenset[str] | None = None) -> int:
        if seen is None:
            seen = frozenset()
        if node_id in seen:
            return 0
        seen = seen | {node_id}
        node_out = [e for e in out_edges.get(node_id, []) if id(e) not in edges_to_remove]
        if len(node_out) != 1:
            return 1
        target_id = node_out[0].to_id
        node_in = [e for e in in_edges.get(node_id, []) if id(e) not in edges_to_remove]
        if len(node_in) != 1:
            return 1
        target_in = [e for e in in_edges.get(target_id, []) if id(e) not in edges_to_remove and not e.collapsed]
        target_out = [e for e in out_edges.get(target_id, []) if id(e) not in edges_to_remove and not e.collapsed]
        if len(target_in) == 1 and len(target_out) == 1:
            target_ref = nodes.get(target_id)
            if target_ref and target_ref.role in collapse_roles:
                return 1 + _chain_length(target_id, seen)
        return 1

    for node_id in all_node_ids:
        node_in = [e for e in in_edges.get(node_id, []) if not e.collapsed and id(e) not in edges_to_remove]
        node_out = [e for e in out_edges.get(node_id, []) if not e.collapsed and id(e) not in edges_to_remove]
        if len(node_in) != 1 or len(node_out) != 1:
            continue

        node_ref = nodes.get(node_id)
        if node_ref is None:
            continue

        # Check collapse_roles.
        role = node_ref.role
        if role not in collapse_roles:
            continue

        # Check minimum chain length.
        chain_len = _chain_length(node_id)
        if chain_len < collapse_min_chain_length:
            continue

        in_edge = node_in[0]
        out_edge = node_out[0]

        # Skip if already consumed.
        if id(in_edge) in edges_to_remove or id(out_edge) in edges_to_remove:
            continue

        # Collect intermediates from existing collapsed edges.
        intermediates = [node_id]
        final_to_id = out_edge.to_id
        # Walk the chain to collapse all intermediates.
        current_out = out_edge
        while True:
            next_id = current_out.to_id
            next_ref = nodes.get(next_id)
            if next_ref is None or next_ref.role not in collapse_roles:
                break
            next_in = [e for e in in_edges.get(next_id, []) if not e.collapsed and id(e) not in edges_to_remove]
            next_out = [e for e in out_edges.get(next_id, []) if not e.collapsed and id(e) not in edges_to_remove]
            if len(next_in) != 1 or len(next_out) != 1:
                break
            intermediates.append(next_id)
            edges_to_remove.add(id(next_in[0]))
            edges_to_remove.add(id(next_out[0]))
            current_out = next_out[0]

        final_to_id = current_out.to_id

        # Merge attrs (prefer lower confidence edge's attrs).
        merged_attrs = in_edge.attrs if (
            float(in_edge.attrs.get("confidence", 1.0))
            <= float(current_out.attrs.get("confidence", 1.0))
        ) else current_out.attrs

        merged_edge = _FlatEdge(
            from_id=in_edge.from_id,
            to_id=final_to_id,
            edge_type="CALLS",
            hop=in_edge.hop,
            direction=in_edge.direction,
            confidence=in_edge.confidence,
            attrs=merged_attrs,
        )
        merged_edge.collapsed = True
        merged_edge.collapsed_intermediates = intermediates

        edges_to_remove.add(id(in_edge))
        edges_to_remove.add(id(id(out_edge)))
        # Also remove the original out_edge.
        edges_to_remove.add(id(out_edge))

        edges_to_add.append(merged_edge)
        collapsed_count += len(intermediates)

    if collapsed_count == 0:
        return 0

    # Rebuild edges list: remove collapsed, add merged.
    new_edges = [e for e in edges if id(e) not in edges_to_remove]
    new_edges.extend(edges_to_add)
    # Retain intermediates in nodes dict (v2 change from v1).

    edges.clear()
    edges.extend(new_edges)

    return collapsed_count


def _build_tree(
    seed_ids: list[str],
    nodes: dict[str, NodeRef],
    edges: list[_FlatEdge],
) -> list[TreeNode]:
    """Convert flat edge list to nested TreeNode structure.

    Multi-seed roots: top-level tree list has one TreeNode per seed ID.
    Collapsed intermediates are NOT in the tree but retained in nodes dict.
    """
    if not edges and not seed_ids:
        return []

    # Build adjacency: from_id -> list of edges from that node.
    adj: dict[str, list[_FlatEdge]] = defaultdict(list)
    for e in edges:
        adj[e.from_id].append(e)

    # Track which nodes are already placed (seed nodes get placed first).
    placed: set[str] = set()

    def _make_tree_node(edge: _FlatEdge, target_id: str) -> TreeNode:
        """Create a TreeNode from an edge targeting target_id."""
        child = TreeNode(
            id=target_id,
            edge_from_parent=EdgeFromParent(
                direction=edge.direction,
                edge_type=edge.edge_type,
                hop=edge.hop,
                confidence=edge.confidence,
                cross_service_boundary=edge.cross_service_boundary,
                attrs=edge.attrs,
            ),
            collapsed=edge.collapsed,
            collapsed_intermediates=list(edge.collapsed_intermediates),
        )
        placed.add(target_id)
        # Recurse into children of this target.
        child_edges = adj.get(target_id, [])
        for ce in child_edges:
            if ce.to_id not in placed:
                child.children.append(_make_tree_node(ce, ce.to_id))
        return child

    result: list[TreeNode] = []
    for sid in seed_ids:
        placed.add(sid)
        seed_node = TreeNode(id=sid, edge_from_parent=None)
        for e in adj.get(sid, []):
            if e.to_id not in placed:
                seed_node.children.append(_make_tree_node(e, e.to_id))
        result.append(seed_node)

    return result


def _build_ranked_leaves(
    tree: list[TreeNode],
    nodes: dict[str, NodeRef],
    max_paths: int,
) -> list[RankedLeaf]:
    """Walk tree to find leaf nodes, score and rank them."""
    if not tree:
        return []

    candidates: list[RankedLeaf] = []

    def _walk(node: TreeNode, depth: int) -> None:
        if not node.children:
            # This is a leaf.
            node_ref = nodes.get(node.id)
            leaf_role = node_ref.role if node_ref else None
            role_score = _role_priority(leaf_role)
            # Confidence from edge_from_parent (if any).
            conf = 1.0
            if node.edge_from_parent and node.edge_from_parent.confidence is not None:
                conf = node.edge_from_parent.confidence
            score = role_score + conf
            candidates.append(RankedLeaf(
                node_id=node.id,
                depth=depth,
                leaf_role=leaf_role,
                score=score,
            ))
        else:
            for child in node.children:
                _walk(child, depth + 1)

    for seed in tree:
        _walk(seed, 0)

    candidates.sort(key=lambda r: -r.score)
    return candidates[:max_paths]


def _run_bfs(
    *,
    graph: KuzuGraph,
    seed_ids: list[str],
    direction: Literal["in", "out"],
    edge_types: list[str],
    max_depth: int,
    max_nodes_discovered: int,
    nf: NodeFilter | None,
    ef: EdgeFilter | None,
    prune_role_set: set[str],
    fan_out_cap: int,
    cross_service: bool,
    include_unresolved: bool,
    visited: set[str],
) -> tuple[dict[str, NodeRef], list[_FlatEdge], int, int, bool, int, int]:
    """Run a single-direction BFS pass.

    Returns (nodes, edges, total_discovered, actual_depth, budget_hit,
             nodes_pruned_role, nodes_pruned_fan_out).
    """
    # Determine if cross-service detection is active.
    cross_service_active = bool(set(edge_types) & _CROSS_SERVICE_EDGE_TYPES)

    # Effective scaffolding set.
    effective_scaffolding = _SCAFFOLDING_EDGE_TYPES
    if cross_service:
        effective_scaffolding = _SCAFFOLDING_EDGE_TYPES | frozenset({"EXPOSES"})

    # BFS state.
    frontier: list[str] = [sid for sid in seed_ids if sid not in visited]
    for sid in seed_ids:
        visited.add(sid)

    edges: list[_FlatEdge] = []
    nodes: dict[str, NodeRef] = {}
    total_discovered = len(seed_ids)
    actual_depth = 0
    budget_hit = False
    nodes_pruned_role = 0
    nodes_pruned_fan_out = 0

    # Record seed nodes.
    for sid in seed_ids:
        try:
            kind = _resolve_node_kind(graph, sid)
            row = _load_node_record(graph, sid, kind)
            if row is not None:
                nodes[sid] = _node_ref_from_row(kind, row)
        except Exception:
            pass

    # BFS loop.
    for hop in range(max_depth):
        if not frontier or total_discovered >= max_nodes_discovered:
            if total_discovered >= max_nodes_discovered:
                budget_hit = True
            break

        actual_depth = hop + 1

        # Determine edge types to query.
        query_edge_types = list(edge_types)
        if cross_service_active:
            for scaffold_et in effective_scaffolding:
                if scaffold_et not in query_edge_types:
                    query_edge_types.append(scaffold_et)

        rows = _neighbors_batched(
            graph,
            node_ids=frontier,
            direction=direction,
            edge_types=query_edge_types,
            edge_filter=ef,
        )

        # Group by source node.
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            src_id = str(row.get("source_id") or "")
            if not src_id:
                continue
            by_source[src_id].append(row)

        new_frontier: set[str] = set()

        for src_id, src_rows in by_source.items():
            # Source role for source-relative ranking.
            src_ref = nodes.get(src_id)
            source_role = src_ref.role if src_ref else None

            # --- Fan-out cap: separate scaffolding from signal edges ---
            scaffolding_rows: list[dict[str, Any]] = []
            signal_rows: list[dict[str, Any]] = []

            for row in src_rows:
                et = str(row.get("edge_type") or "")
                if et in effective_scaffolding:
                    scaffolding_rows.append(row)
                else:
                    signal_rows.append(row)

            # Sort signal rows by ranking key for fan-out cap.
            signal_rows.sort(key=lambda r: _fan_out_sort_key(r, nodes, source_role))

            # Apply fan_out_cap to signal edges only.
            if fan_out_cap > 0 and len(signal_rows) > fan_out_cap:
                for dropped_row in signal_rows[fan_out_cap:]:
                    dropped_id = str(dropped_row.get("other_id") or "")
                    if dropped_id and dropped_id not in visited:
                        nodes_pruned_fan_out += 1
                signal_rows = signal_rows[:fan_out_cap]

            capped_rows = scaffolding_rows + signal_rows

            for row in capped_rows:
                other_id = str(row.get("other_id") or "")
                if not other_id:
                    continue

                if other_id in visited:
                    continue

                edge_type = str(row.get("edge_type") or "")

                # --- Cross-service boundary detection ---
                if edge_type in effective_scaffolding and cross_service_active:
                    try:
                        other_kind = _resolve_node_kind(graph, other_id)
                        other_rec = _load_node_record(graph, other_id, other_kind)
                        if other_rec is None:
                            continue
                    except Exception:
                        print(f"[trace] cross-service: failed to resolve {other_id}", file=sys.stderr)
                        continue

                    if total_discovered >= max_nodes_discovered:
                        budget_hit = True
                        break

                    total_discovered += 1
                    if other_id not in nodes:
                        nodes[other_id] = _node_ref_from_row(other_kind, other_rec)

                    conf = row.get("confidence")
                    confidence = float(conf) if conf is not None else None
                    edge = _FlatEdge(
                        from_id=src_id,
                        to_id=other_id,
                        edge_type=edge_type,
                        hop=hop,
                        direction=direction,
                        confidence=confidence,
                        attrs=_edge_attrs_for_row(row),
                    )
                    edges.append(edge)
                    visited.add(other_id)

                    # Follow HTTP_CALLS/ASYNC_CALLS from Client/Producer.
                    active_cross_types = list(set(edge_types) & _CROSS_SERVICE_EDGE_TYPES)
                    if not active_cross_types:
                        continue

                    cross_rows = _neighbors_batched(
                        graph,
                        node_ids=[other_id],
                        direction=direction,
                        edge_types=active_cross_types,
                        edge_filter=None,
                    )

                    for cross_row in cross_rows:
                        cross_target_id = str(cross_row.get("other_id") or "")
                        if not cross_target_id or cross_target_id in visited:
                            continue

                        cross_et = str(cross_row.get("edge_type") or "")
                        if cross_et not in _CROSS_SERVICE_EDGE_TYPES:
                            continue

                        if total_discovered >= max_nodes_discovered:
                            budget_hit = True
                            break

                        total_discovered += 1

                        try:
                            cross_kind = _resolve_node_kind(graph, cross_target_id)
                            cross_rec = _load_node_record(graph, cross_target_id, cross_kind)
                            if cross_rec is None:
                                continue
                        except Exception:
                            print(f"[trace] cross-service: failed to resolve {cross_target_id}", file=sys.stderr)
                            continue
                        if cross_target_id not in nodes:
                            nodes[cross_target_id] = _node_ref_from_row(cross_kind, cross_rec)

                        cross_conf = cross_row.get("confidence")
                        cross_confidence = float(cross_conf) if cross_conf is not None else None
                        cross_edge = _FlatEdge(
                            from_id=other_id,
                            to_id=cross_target_id,
                            edge_type=cross_et,
                            hop=hop + 1,
                            direction=direction,
                            confidence=cross_confidence,
                            cross_service_boundary=True,
                            attrs=_edge_attrs_for_row(cross_row),
                        )
                        edges.append(cross_edge)
                        visited.add(cross_target_id)

                        if cross_service:
                            new_frontier.add(cross_target_id)

                    continue

                # --- Standard edge processing ---
                if total_discovered >= max_nodes_discovered:
                    budget_hit = True
                    break

                total_discovered += 1

                try:
                    other_kind = _resolve_node_kind(graph, other_id)
                    other_rec = _load_node_record(graph, other_id, other_kind)
                    if other_rec is None:
                        continue
                except Exception:
                    continue

                # Apply NodeFilter (hard gate).
                if not _node_matches_filter(other_kind, other_rec, nf):
                    continue

                if other_id not in nodes:
                    nodes[other_id] = _node_ref_from_row(other_kind, other_rec)

                # Check prune_roles (soft gate).
                is_pruned = False
                if prune_role_set:
                    node_ref = nodes.get(other_id)
                    if node_ref and node_ref.role in prune_role_set:
                        is_pruned = True
                        nodes_pruned_role += 1

                conf = row.get("confidence")
                confidence = float(conf) if conf is not None else None
                edge = _FlatEdge(
                    from_id=src_id,
                    to_id=other_id,
                    edge_type=edge_type,
                    hop=hop,
                    direction=direction,
                    confidence=confidence,
                    attrs=_edge_attrs_for_row(row),
                )
                edges.append(edge)

                if not is_pruned:
                    new_frontier.add(other_id)

        visited.update(new_frontier)
        frontier = list(new_frontier)

        if budget_hit:
            break

    return nodes, edges, total_discovered, actual_depth, budget_hit, nodes_pruned_role, nodes_pruned_fan_out


@validate_call(config={"arbitrary_types_allowed": True})
def trace_v2(
    ids: str | list[str],
    direction: Literal["in", "out", "both"] = Field(...),
    edge_types: list[str] = Field(...),
    max_depth: int = 3,
    max_paths: int = 20,
    max_nodes_discovered: int = 500,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    edge_filter: EdgeFilter | dict[str, Any] | str | None = None,
    prune_roles: list[str] | None = None,
    fan_out_cap: int = 5,
    collapse_trivial: bool = True,
    collapse_roles: list[str] | None = None,
    collapse_min_chain_length: int = 1,
    include_unresolved: bool = False,
    cross_service: bool = False,
    min_result_nodes: int = 0,
    graph: KuzuGraph | None = None,
) -> TraceOutput:
    """Multi-hop BFS traversal with pruning."""
    # Validate required parameters.
    if not direction:
        return TraceOutput(
            success=False,
            seed_ids=[],
            direction="",
            edge_types=[],
            message="direction is required (in, out, or both)",
        )

    if not edge_types:
        return TraceOutput(
            success=False,
            seed_ids=[],
            direction=direction,
            edge_types=[],
            message="edge_types is required and non-empty",
        )

    # Validate edge types.
    unknown = [et for et in edge_types if et not in _TRACE_EDGE_TYPES]
    if unknown:
        return TraceOutput(
            success=False,
            seed_ids=[],
            direction=direction,
            edge_types=edge_types,
            message=(
                f"Unknown edge type(s): {unknown}. "
                f"Valid types: {sorted(_TRACE_EDGE_TYPES)}. "
                "Composed keys (e.g., DECLARES.DECLARES_CLIENT) are not supported."
            ),
        )

    # Clamp max_depth.
    max_depth = max(1, min(5, int(max_depth)))

    # Clamp max_nodes_discovered.
    max_nodes_discovered = max(100, min(2000, int(max_nodes_discovered)))

    # Normalize seed IDs.
    seed_ids = [ids] if isinstance(ids, str) else list(ids)

    if not seed_ids:
        return TraceOutput(
            success=True,
            seed_ids=[],
            direction=direction,
            edge_types=edge_types,
            nodes={},
            tree=[],
            ranked_leaves=[],
            stats=TraceStats(budget_limit=max_nodes_discovered),
        )

    # Validate NodeFilter.
    try:
        if isinstance(filter, str):
            import json

            filter = json.loads(filter) if filter.strip() else None
        nf = NodeFilter.model_validate(filter) if filter is not None and not isinstance(filter, NodeFilter) else filter
    except Exception as exc:
        return TraceOutput(
            success=False,
            seed_ids=seed_ids,
            direction=direction,
            edge_types=edge_types,
            message=f"Invalid filter: {exc}",
        )

    # Validate EdgeFilter.
    try:
        if isinstance(edge_filter, str):
            import json

            edge_filter = json.loads(edge_filter) if edge_filter.strip() else None
        ef = (
            EdgeFilter.model_validate(edge_filter)
            if edge_filter is not None and not isinstance(edge_filter, EdgeFilter)
            else edge_filter
        )
    except Exception as exc:
        return TraceOutput(
            success=False,
            seed_ids=seed_ids,
            direction=direction,
            edge_types=edge_types,
            message=f"Invalid edge_filter: {exc}",
        )

    # Get graph instance.
    g = graph or KuzuGraph.get()

    # Normalized prune_roles set.
    prune_role_set = set(prune_roles) if prune_roles else set()

    # Collapse roles configuration.
    collapse_role_set: set[str | None] | None = None
    if collapse_trivial:
        if collapse_roles is not None:
            collapse_role_set = set(collapse_roles)
        else:
            collapse_role_set = {"OTHER", None}

    # Determine directions to run.
    directions: list[Literal["in", "out"]]
    if direction == "both":
        directions = ["out", "in"]
    else:
        directions = [direction]  # type: ignore[assignment]

    # Shared visited set for bidirectional.
    shared_visited: set[str] = set()
    all_nodes: dict[str, NodeRef] = {}
    all_edges: list[_FlatEdge] = []
    total_discovered = 0
    actual_depth = 0
    budget_hit = False
    total_pruned_role = 0
    total_pruned_fan_out = 0
    for pass_idx, pass_dir in enumerate(directions):
        pass_nodes, pass_edges, pass_discovered, pass_depth, pass_budget, pass_pruned_role, pass_pruned_fan_out = _run_bfs(
            graph=g,
            seed_ids=seed_ids,
            direction=pass_dir,
            edge_types=edge_types,
            max_depth=max_depth,
            max_nodes_discovered=max_nodes_discovered,
            nf=nf,
            ef=ef,
            prune_role_set=prune_role_set,
            fan_out_cap=fan_out_cap,
            cross_service=cross_service,
            include_unresolved=include_unresolved,
            visited=shared_visited,
        )

        # Merge results.
        for nid, nref in pass_nodes.items():
            if nid not in all_nodes:
                all_nodes[nid] = nref

        all_edges.extend(pass_edges)
        total_discovered += pass_discovered - len(seed_ids)  # Don't double-count seeds
        actual_depth = max(actual_depth, pass_depth)
        budget_hit = budget_hit or pass_budget
        total_pruned_role += pass_pruned_role
        total_pruned_fan_out += pass_pruned_fan_out

        # Bidirectional advisory: count nodes suppressed by shared visited set.
        if direction == "both" and pass_idx == 0:
            pass
        if direction == "both" and pass_idx == 1:
            # Nodes discovered in pass 0 that would also be discovered in pass 1
            # are implicitly counted as "suppressed" by the shared visited set.
            # We approximate: nodes in all_nodes from pass 0 minus what pass 1 added.
            pass

    # Re-count seeds once.
    total_discovered = len(all_nodes)

    # min_result_nodes retry.
    advisories: list[str] = []
    effective_cap = fan_out_cap
    if min_result_nodes > 0 and len(all_nodes) < min_result_nodes:
        # One retry with doubled fan_out_cap (clamped by max_nodes_discovered).
        effective_cap = min(fan_out_cap * 2, max_nodes_discovered)
        if effective_cap > fan_out_cap:
            # Re-run with higher cap.
            retry_visited: set[str] = set()
            retry_nodes: dict[str, NodeRef] = {}
            retry_edges: list[_FlatEdge] = []
            retry_total = 0
            retry_depth = 0
            retry_budget = False
            retry_pruned_role = 0
            retry_pruned_fan_out = 0

            for pass_idx, pass_dir in enumerate(directions):
                pn, pe, pd, pdep, pb, ppr, pf = _run_bfs(
                    graph=g,
                    seed_ids=seed_ids,
                    direction=pass_dir,
                    edge_types=edge_types,
                    max_depth=max_depth,
                    max_nodes_discovered=max_nodes_discovered,
                    nf=nf,
                    ef=ef,
                    prune_role_set=prune_role_set,
                    fan_out_cap=effective_cap,
                    cross_service=cross_service,
                    include_unresolved=include_unresolved,
                    visited=retry_visited,
                )
                for nid, nref in pn.items():
                    if nid not in retry_nodes:
                        retry_nodes[nid] = nref
                retry_edges.extend(pe)
                retry_total += pd - len(seed_ids)
                retry_depth = max(retry_depth, pdep)
                retry_budget = retry_budget or pb
                retry_pruned_role += ppr
                retry_pruned_fan_out += pf

            retry_total = len(retry_nodes)
            if retry_total >= min_result_nodes or retry_total > len(all_nodes):
                # Accept retry result.
                all_nodes = retry_nodes
                all_edges = retry_edges
                total_discovered = retry_total
                actual_depth = retry_depth
                budget_hit = retry_budget
                total_pruned_role = retry_pruned_role
                total_pruned_fan_out = retry_pruned_fan_out
            else:
                advisories.append(
                    f"min_result_nodes retry with fan_out_cap={effective_cap} still below target "
                    f"({retry_total} < {min_result_nodes}). Returning available results."
                )
                # Still accept retry if it's better.
                if retry_total > len(all_nodes):
                    all_nodes = retry_nodes
                    all_edges = retry_edges
                    total_discovered = retry_total
                    actual_depth = retry_depth
                    budget_hit = retry_budget
                    total_pruned_role = retry_pruned_role
                    total_pruned_fan_out = retry_pruned_fan_out

    # Post-BFS: collapse trivial chains.
    edges_collapsed = 0
    if collapse_trivial and collapse_role_set is not None:
        edges_collapsed = _collapse_trivial_chains(
            all_nodes, all_edges, collapse_role_set, collapse_min_chain_length,
        )

    # Build tree from flat edges.
    tree = _build_tree(seed_ids, all_nodes, all_edges)

    # Build ranked leaves.
    ranked_leaves = _build_ranked_leaves(tree, all_nodes, max_paths)

    # Build stats.
    stats = TraceStats(
        total_nodes_discovered=total_discovered,
        total_edges_discovered=len(all_edges),
        budget_hit=budget_hit,
        budget_limit=max_nodes_discovered,
        nodes_pruned_role=total_pruned_role,
        nodes_pruned_fan_out=total_pruned_fan_out,
        edges_collapsed_trivial=edges_collapsed,
        nodes_after_pruning=len(all_nodes),
        edges_after_pruning=len(all_edges),
    )

    if budget_hit:
        advisories.append(
            f"trace stopped early: discovered {total_discovered} nodes before budget. "
            f"Reduce max_depth or add prune_roles to focus."
        )

    return TraceOutput(
        success=True,
        seed_ids=seed_ids,
        direction=direction,
        edge_types=edge_types,
        actual_depth=actual_depth,
        nodes=all_nodes,
        tree=tree,
        ranked_leaves=ranked_leaves,
        stats=stats,
        advisories=advisories,
    )


__all__ = [
    "EdgeFromParent",
    "TreeNode",
    "RankedLeaf",
    "TraceStats",
    "TraceOutput",
    "trace_v2",
]
