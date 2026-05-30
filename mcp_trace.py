"""MCP trace tool — multi-hop BFS traversal with pruning.

Imports stable types from mcp_v2.py but does not modify them:
- NodeFilter, EdgeFilter, NodeRef, _node_ref_from_row, _node_kind_from_id

This module is additive (PR-TRACE-1a: core BFS engine + budget + paths).
Pruning features (prune_roles, fan_out_cap, collapse_trivial, cross-service)
land in PR-TRACE-1b.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, validate_call

from java_ontology import EDGE_SCHEMA
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


def _role_priority(role: str | None) -> int:
    """Return numeric priority for role ranking (higher = better)."""
    if role is None:
        return 0
    return _ROLE_PRIORITY.get(role, 1)


class TraceEdge(BaseModel):
    """A single edge in the trace result with BFS metadata."""
    model_config = ConfigDict(extra="forbid")

    from_id: str
    to_id: str
    edge_type: str
    hop: int
    parent_edge_id: str | None = None
    collapsed: bool = False
    collapsed_intermediates: list[str] = Field(default_factory=list)
    cross_service_boundary: bool = False
    attrs: dict[str, Any] = Field(default_factory=dict)


class TracePath(BaseModel):
    """A root-to-leaf path through the traced DAG."""
    model_config = ConfigDict(extra="forbid")

    edges: list[TraceEdge]
    leaf: NodeRef


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
    edges: list[TraceEdge] = Field(default_factory=list)
    paths: list[TracePath] = Field(default_factory=list)
    stats: TraceStats = Field(default_factory=TraceStats)
    message: str | None = None
    advisories: list[str] = Field(default_factory=list)


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
    """Issue a single Cypher query for all frontier nodes at one BFS hop.

    Returns rows with: source_id, other_id, edge_type, and edge attribute columns.
    Each row represents one edge from a source node to a target node.
    """
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
    """Load a node record from Kuzu (copied from mcp_v2.py)."""
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


def _enumerate_paths(
    nodes: dict[str, NodeRef],
    edges: list[TraceEdge],
    max_paths: int,
) -> list[TracePath]:
    """Enumerate root-to-leaf paths through the DAG, capped and ranked."""
    if not edges:
        return []

    # Build adjacency list: from_id -> list of outgoing edges.
    out_edges_by_src: dict[str, list[TraceEdge]] = defaultdict(list)
    for e in edges:
        out_edges_by_src[e.from_id].append(e)

    # Find seeds (edges with hop 0).
    seeds = {e.from_id for e in edges if e.hop == 0}

    # Find leaves (node IDs that have no outgoing edges in the result).
    all_targets = {e.to_id for e in edges}
    leaves = all_targets - set(out_edges_by_src.keys())

    if not leaves:
        return []

    # DFS from each seed to enumerate paths.
    candidates: list[tuple[int, float, int, list[TraceEdge]]] = []

    def dfs(current_id: str, path_edges: list[TraceEdge], min_conf: float) -> None:
        """Depth-first search accumulating path confidence."""
        if current_id in leaves:
            leaf_role = nodes.get(current_id, NodeRef(id=current_id, kind="symbol", fqn="")).role
            candidates.append(
                (_role_priority(leaf_role), min_conf, -len(path_edges), list(path_edges))
            )
            return

        for e in out_edges_by_src.get(current_id, []):
            edge_conf = float(e.attrs.get("confidence", 1.0))
            dfs(e.to_id, path_edges + [e], min(min_conf, edge_conf))

    for seed in seeds:
        dfs(seed, [], 1.0)

    # Cap enumeration to avoid exponential blowup.
    if len(candidates) > 10 * max_paths:
        # Sort and keep top candidates.
        candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        candidates = candidates[: 10 * max_paths]

    # Rank and cap at max_paths.
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    paths: list[TracePath] = []

    for role_prio, _min_conf, _neg_len, edge_list in candidates[:max_paths]:
        leaf_id = edge_list[-1].to_id if edge_list else ""
        leaf_node = nodes.get(leaf_id, NodeRef(id=leaf_id, kind="symbol", fqn=""))
        paths.append(TracePath(edges=edge_list, leaf=leaf_node))

    return paths


@validate_call(config={"arbitrary_types_allowed": True})
def trace_v2(
    ids: str | list[str],
    direction: Literal["in", "out"] = Field(...),
    edge_types: list[str] = Field(...),
    max_depth: int = 3,
    max_paths: int = 20,
    max_nodes_discovered: int = 500,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    edge_filter: EdgeFilter | dict[str, Any] | str | None = None,
    prune_roles: list[str] | None = None,
    fan_out_cap: int = 5,
    collapse_trivial: bool = True,
    include_unresolved: bool = False,
    graph: KuzuGraph | None = None,
) -> TraceOutput:
    """Multi-hop BFS traversal with pruning.

    This is PR-TRACE-1a (core BFS engine). Pruning features (prune_roles, fan_out_cap,
    collapse_trivial, include_unresolved) are wired but treated as no-ops until
    PR-TRACE-1b implementation.
    """
    # Validate required parameters.
    if not direction:
        return TraceOutput(
            success=False,
            seed_ids=[],
            direction="",
            edge_types=[],
            message="direction is required (in or out)",
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
            edges=[],
            paths=[],
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

    # BFS state.
    visited: set[str] = set(seed_ids)
    frontier: list[str] = list(seed_ids)
    edges: list[TraceEdge] = []
    nodes: dict[str, NodeRef] = {}
    edge_id_map: dict[str, TraceEdge] = {}
    total_discovered = len(seed_ids)  # Count seeds as discovered
    actual_depth = 0
    budget_hit = False

    # Track incoming edge ID for each node (for parent_edge_id).
    node_to_incoming_edge_id: dict[str, str] = {}

    # For seed nodes, record them in nodes dict (always include seeds, filter doesn't apply).
    for sid in seed_ids:
        try:
            kind = _resolve_node_kind(g, sid)
            row = _load_node_record(g, sid, kind)
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

        # Batch query for all frontier nodes.
        rows = _neighbors_batched(
            g,
            node_ids=frontier,
            direction=direction,
            edge_types=edge_types,
            edge_filter=ef,
        )

        # Group by source node.
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            src_id = str(row.get("source_id") or "")
            if not src_id:
                # Infer source from frontier context (same pattern as neighbors_v2).
                continue
            by_source[src_id].append(row)

        # Process discovered edges.
        new_frontier: set[str] = set()
        edges_by_parent: dict[str, list[TraceEdge]] = defaultdict(list)

        for src_id, src_rows in by_source.items():
            parent_edge_id = node_to_incoming_edge_id.get(src_id)

            for row in src_rows:
                other_id = str(row.get("other_id") or "")
                if not other_id:
                    continue

                if other_id in visited:
                    continue

                # Check budget BEFORE counting (only counts newly discovered nodes).
                if total_discovered >= max_nodes_discovered:
                    budget_hit = True
                    break

                total_discovered += 1

                # Load target node.
                try:
                    other_kind = _resolve_node_kind(g, other_id)
                    other_rec = _load_node_record(g, other_id, other_kind)
                    if other_rec is None:
                        continue
                except Exception:
                    continue

                # Apply NodeFilter (hard gate).
                if not _node_matches_filter(other_kind, other_rec, nf):
                    continue

                # Record target node.
                if other_id not in nodes:
                    nodes[other_id] = _node_ref_from_row(other_kind, other_rec)

                # Record edge.
                edge_type = str(row.get("edge_type") or "")
                edge_id = f"{src_id}:{other_id}:{edge_type}:{hop}"
                edge = TraceEdge(
                    from_id=src_id,
                    to_id=other_id,
                    edge_type=edge_type,
                    hop=hop,
                    parent_edge_id=parent_edge_id,
                    attrs=_edge_attrs_for_row(row),
                )
                edges.append(edge)
                edge_id_map[edge_id] = edge
                edges_by_parent[src_id].append(edge)

                # Track incoming edge ID for this node (for parent_edge_id of children).
                if other_id not in node_to_incoming_edge_id:
                    node_to_incoming_edge_id[other_id] = edge_id

                # PR-TRACE-1b: prune_roles, fan_out_cap, cross_service are no-ops here.
                # For now, add all discovered nodes to frontier.
                new_frontier.add(other_id)

        visited.update(new_frontier)
        frontier = list(new_frontier)

        if budget_hit:
            break

    # Build stats.
    stats = TraceStats(
        total_nodes_discovered=total_discovered,
        total_edges_discovered=len(edges),
        budget_hit=budget_hit,
        budget_limit=max_nodes_discovered,
        nodes_after_pruning=len(nodes),
        edges_after_pruning=len(edges),
    )

    # Enumerate paths.
    paths = _enumerate_paths(nodes, edges, max_paths)

    advisories = []
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
        nodes=nodes,
        edges=edges,
        paths=paths,
        stats=stats,
        advisories=advisories,
    )


__all__ = [
    "TraceEdge",
    "TracePath",
    "TraceStats",
    "TraceOutput",
    "trace_v2",
]
