"""Payload-returning cores for the ``jrag`` read commands.

Each ``<cmd>_payload(args, cfg, graph)`` function assembles the
JSON-serializable payload the corresponding ``jrag`` handler renders, WITHOUT
rendering. This lets the ``jrag watch`` daemon (later tasks) serve these
commands over a socket by calling the payload function and serializing,
reusing the exact cold read path.

Behavior-preserving extraction (Task 5 of the jrag-watch plan): the compute
(graph calls + folds) is lifted verbatim from the handlers in ``jrag.py``;
rendering stays in the handlers. Each handler is restructured to::

    payload = <cmd>_payload(args, cfg, graph)   # may raise PayloadError
    render(payload, args)                        # unchanged render call

On resolve / kind-guard / validation failure the payload raises
:class:`PayloadError` carrying the :class:`Envelope` and ``rc`` the handler
would have rendered, so the handler can render the error byte-identically.

Payload shapes:
  * ``search_payload``  -> ``SearchOutput`` (pydantic; the raw ``search_v2`` result)
  * ``find_payload``    -> ``{"mode": "query"|"filter", ...}`` carrying either the
                           ``find_by_name_or_fqn`` post-filtered rows or a ``FindOutput``
  * ``inspect_payload`` -> ``{"describe": DescribeOutput, "node_id", "node_fqn",
                           "file_location"}`` (inspect's renderer needs resolve-derived
                           ``file_location`` which is not on ``DescribeOutput``)
  * ``callers_payload`` / ``callees_payload`` / ``flow_payload`` -> traversal dict
    ``{"root_id", "nodes", "edges", "noun", "warnings", "truncated",
    "is_external_entrypoint"}`` shaped exactly as ``_emit_traversal`` consumes.

All payloads round-trip through ``json.dumps``/``loads`` (pydantic via
``model_dump``; the rest are plain dict/list/scalar).
"""
from __future__ import annotations

import argparse
from typing import Any

# Top-level imports are safe here: ``jrag`` imports this module lazily (inside
# its handlers), so there is no import cycle -- by the time this module loads,
# ``jrag`` is already fully initialized, and these are stable module-level helpers.
from java_codebase_rag.jrag import (
    _build_node_filter_or_error,
    _check_kind_contradiction,
    _clamped_limit,
    _dedupe_traversal_edges,
    _infer_kind,
    _noderef_to_node_dict,
    _symbol_hit_to_dict,
    _warn_unapplied_scope,
)
from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, normalize_enum, resolve_query


class PayloadError(Exception):
    """Raised by a ``*_payload`` function on resolve / kind-guard / validation
    failure.

    Carries the :class:`Envelope` the handler renders and the ``rc`` it returns,
    so the handler renders the error byte-identically to before the extraction.
    """

    def __init__(self, env: Envelope, rc: int) -> None:
        self.env = env
        self.rc = rc
        super().__init__(env.message or env.status)


# ---------------------------------------------------------------------------
# Shared resolve frame (verbatim logic of jrag._resolve_traversal_node, minus
# the print — the handler renders the Envelope carried by PayloadError).
# ---------------------------------------------------------------------------


def _resolve_traversal(args: argparse.Namespace, *, cfg, graph, hint_kind, apply_scope: bool):
    """Resolve the traversal root non-printingly.

    Returns the resolved ``node`` (NodeRef). Raises :class:`PayloadError`
    carrying the resolve ``Envelope`` + ``rc`` on failure (rc=2 on error,
    0 on ambiguous/not_found — matches ``_resolve_traversal_node``).
    """
    node, env = resolve_query(
        args.query,
        hint_kind=hint_kind,
        java_kind=getattr(args, "java_kind", None),
        role=getattr(args, "role", None),
        fqn_contains=getattr(args, "fqn_contains", None),
        cfg=cfg,
        graph=graph,
        microservice=(getattr(args, "service", None) or "") if apply_scope else "",
        module=(getattr(args, "module", None) or "") if apply_scope else "",
    )
    if env.status != "ok":
        raise PayloadError(env, 2 if env.status == "error" else 0)
    return node


def _kind_guard(node, *, args: argparse.Namespace, expected: str, kinds: tuple[str, ...], hint: str = "") -> None:
    """Non-printing kind guard (logic of jrag._require_kind, minus the print).

    Raises :class:`PayloadError` with the same error message ``_require_kind``
    builds when ``node.kind`` is not in ``kinds``.
    """
    if node.kind not in kinds:
        msg = f"{expected}; resolved kind is {node.kind!r}."
        if hint:
            msg = f"{msg} {hint}"
        raise PayloadError(Envelope(status="error", message=msg), 2)


# ---------------------------------------------------------------------------
# callers_payload
# ---------------------------------------------------------------------------


def callers_payload(args: argparse.Namespace, cfg, graph) -> dict[str, Any]:
    """Assemble the traversal payload for ``jrag callers``.

    Route root -> ``find_route_callers`` (plus the external-entrypoint fold for
    a zero-caller http_endpoint). Symbol root -> ``find_callers`` plus the
    EXPOSES-inbound fold (DECLARES.EXPOSES routes surfaced as additional rows).
    Both folds are transcribed verbatim from ``_cmd_callers``.
    """
    limit = _clamped_limit(args)
    # callers opts into resolve-time --service/--module narrowing (apply_scope).
    node = _resolve_traversal(
        args, cfg=cfg, graph=graph, hint_kind=args.kind, apply_scope=True
    )

    root_dict = _noderef_to_node_dict(node)
    root_id = node.id

    # ---- Route root -> find_route_callers (+ external-entrypoint fold) ----
    if node.kind == "route":
        route_callers = graph.find_route_callers(route_id=root_id)
        warnings: list[str] = []
        # No backend limit on find_route_callers; client-side slice for truncation.
        truncated = len(route_callers) > limit
        display = route_callers[:limit]
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for rc in display:
            caller_id = rc.caller_node_id
            if rc.caller_node_kind == "client":
                edge_type = "HTTP_CALLS"
            else:
                edge_type = "ASYNC_CALLS"
            node_row = {
                "id": caller_id,
                "kind": rc.caller_node_kind,
                "fqn": rc.declaring_symbol_fqn or caller_id,
                "microservice": rc.caller_microservice,
            }
            if rc.target_service:
                node_row["target_service"] = rc.target_service
            if rc.caller_node_kind == "client" and rc.raw_uri:
                node_row["raw_uri"] = rc.raw_uri
            elif rc.caller_node_kind != "client" and rc.topic:
                node_row["topic"] = rc.topic
            nodes[caller_id] = node_row
            edges.append(
                {"other_id": caller_id, "edge_type": edge_type, "confidence": rc.confidence}
            )
        # Include the root (Route) node so the zero-callers rendering surfaces
        # the route path rather than a bare "0 callers" line.
        nodes[root_id] = root_dict
        # External-entrypoint detection (http_endpoint with an inbound EXPOSES
        # edge from a controller Symbol genuinely has zero in-repo callers).
        is_external_entrypoint = False
        if not display:
            kind_row = graph._rows(  # noqa: SLF001 - same pattern as jrag_envelope._node_file_location
                "MATCH (r:Route) WHERE r.id = $rid RETURN r.kind AS kind LIMIT 1",
                {"rid": root_id},
            )
            route_kind = str(kind_row[0].get("kind") or "") if kind_row else ""
            if route_kind == "http_endpoint" and graph.find_route_handlers(route_id=root_id):
                is_external_entrypoint = True
        return {
            "root_id": root_id,
            "nodes": nodes,
            "edges": edges,
            "noun": "callers",
            "warnings": warnings,
            "truncated": truncated,
            "is_external_entrypoint": is_external_entrypoint,
        }

    # callers accepts Symbol OR Route; anything else is a usage error.
    if node.kind != "symbol":
        raise PayloadError(
            Envelope(
                status="error",
                message=(
                    f"callers expects a Symbol or Route root; resolved node kind is "
                    f"{node.kind!r}. Use --kind to narrow resolve."
                ),
            ),
            2,
        )

    # ---- Symbol root -> find_callers (+ EXPOSES-inbound fold) ----
    depth = getattr(args, "depth", 1)
    min_conf = getattr(args, "min_confidence", 0.0)
    exclude_external = not getattr(args, "include_external", False)
    call_edges = graph.find_callers(
        node.fqn,
        depth=depth,
        limit=limit + 1,
        min_confidence=min_conf,
        exclude_external=exclude_external,
        module=args.module,
        microservice=args.service,
    )
    display, truncated = mark_truncated(call_edges, limit)
    nodes = {}
    edges = []
    for ce in display:
        nodes[ce.src.id] = _symbol_hit_to_dict(ce.src)
        edges.append(
            {"other_id": ce.src.id, "edge_type": "CALLS", "confidence": ce.confidence}
        )
    # EXPOSES-inbound fold: surface routes this type's methods EXPOSE as
    # additional rows (controllers/listeners are entry points invoked via the
    # framework dispatch, not via in-repo CALLS edges).
    expose_rows = graph._rows(  # noqa: SLF001 - one-shot aggregation, cf. _cmd_callees client path
        "MATCH (t:Symbol {id: $tid})-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->(r:Route) "
        "RETURN r.id AS rid, r.method AS rmethod, r.path AS rpath, "
        "r.path_template AS rpt, r.microservice AS rms, "
        "m.fqn AS via_fqn, e.confidence AS conf",
        {"tid": root_id},
    )
    for row in expose_rows:
        rid = str(row.get("rid") or "")
        if not rid or rid in nodes:
            continue
        rmethod = str(row.get("rmethod") or "")
        rpath = str(row.get("rpt") or row.get("rpath") or "")
        nodes[rid] = {
            "id": rid,
            "kind": "route",
            "fqn": f"{rmethod} {rpath}".strip(),
            "method": rmethod,
            "path": rpath,
            "microservice": str(row.get("rms") or ""),
        }
        edge_row: dict = {"other_id": rid, "edge_type": "EXPOSES"}
        via_fqn = str(row.get("via_fqn") or "")
        if via_fqn:
            edge_row["from_fqn"] = via_fqn
        edges.append(edge_row)
    nodes[root_id] = root_dict
    return {
        "root_id": root_id,
        "nodes": nodes,
        "edges": edges,
        "noun": "callers",
        "warnings": [],
        "truncated": truncated,
        "is_external_entrypoint": False,
    }


# ---------------------------------------------------------------------------
# callees_payload
# ---------------------------------------------------------------------------


def callees_payload(args: argparse.Namespace, cfg, graph) -> dict[str, Any]:
    """Assemble the traversal payload for ``jrag callees``.

    Client/Producer root -> ``neighbors_v2`` (HTTP_CALLS / ASYNC_CALLS out).
    CLIENT-role type Symbol -> the CLIENT-role HTTP_CALLS fold (declared Client
    nodes -> HTTP_CALLS -> Route). Symbol root -> ``find_callees``. The
    CLIENT-role fold is transcribed verbatim from ``_cmd_callees``.
    """
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag_envelope import Envelope as _Envelope  # noqa: F811 (local alias for clarity)

    limit = _clamped_limit(args)
    node = _resolve_traversal(
        args, cfg=cfg, graph=graph, hint_kind=args.kind, apply_scope=False
    )

    _kind_guard(
        node,
        args=args,
        expected="callees expects a Symbol, Client, or Producer root",
        kinds=("symbol", "client", "producer"),
        hint="Use --kind to narrow resolve.",
    )

    # ---- Client/Producer root -> neighbors_v2 (HTTP_CALLS / ASYNC_CALLS out) ----
    if node.kind in ("client", "producer"):
        edge_types = ["HTTP_CALLS"] if node.kind == "client" else ["ASYNC_CALLS"]
        out = mcp_v2.neighbors_v2(
            [node.id], direction="out", edge_types=edge_types,
            limit=limit + 1, graph=graph,
        )
        if not out.success:
            raise PayloadError(
                _Envelope(status="error", message=out.message or "neighbors_v2 failed"), 2
            )
        root_id = node.id
        nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
        edges: list[dict] = []
        for e in out.results:
            nodes[e.other.id] = _noderef_to_node_dict(e.other)
            edges.append(
                {
                    "other_id": e.other.id,
                    "edge_type": e.edge_type,
                    "confidence": e.attrs.get("confidence"),
                }
            )
        truncated = bool(out.has_more_results) or len(edges) > limit
        if len(edges) > limit:
            edges = edges[:limit]
        # --include-external is accepted but does not apply on Client/Producer roots.
        warnings: list[str] = []
        if getattr(args, "include_external", False):
            warnings.append(
                "--include-external does not apply to Client/Producer roots "
                "(HTTP_CALLS/ASYNC_CALLS reach :Route, which is always in-graph)"
            )
        edges = _dedupe_traversal_edges(edges)
        truncated = truncated or len(edges) > limit
        edges = edges[:limit]
        return {
            "root_id": root_id,
            "nodes": nodes,
            "edges": edges,
            "noun": "callees",
            "warnings": warnings,
            "truncated": truncated,
            "is_external_entrypoint": False,
        }

    # ---- CLIENT-role type Symbol -> HTTP_CALLS fold ----
    if (node.role or "") == "CLIENT":
        root_id = node.id
        client_rows = graph._rows(  # noqa: SLF001 - one-shot aggregation query
            "MATCH (iface:Symbol {id: $sid})-[:DECLARES]->(m:Symbol)"
            "-[:DECLARES_CLIENT]->(c:Client) "
            "OPTIONAL MATCH (c)-[e:HTTP_CALLS]->(r:Route) "
            "RETURN c.id AS cid, c.member_fqn AS cfqn, c.path AS cpath, "
            "c.method AS cmethod, c.microservice AS cms, "
            "r.id AS rid, r.method AS rmethod, r.path AS rpath, "
            "r.path_template AS rpt, r.microservice AS rms, "
            "e.confidence AS conf",
            {"sid": root_id},
        )
        nodes = {root_id: _noderef_to_node_dict(node)}
        edges = []
        for row in client_rows:
            rid = str(row.get("rid") or "")
            if rid:
                target_id = rid
                rmethod = str(row.get("rmethod") or "")
                rpath = str(row.get("rpt") or row.get("rpath") or "")
                nodes[target_id] = {
                    "id": target_id,
                    "kind": "route",
                    "fqn": f"{rmethod} {rpath}".strip(),
                    "microservice": str(row.get("rms") or ""),
                }
                edge_type = "HTTP_CALLS"
            else:
                # Client with no resolved HTTP_CALLS edge: surface the client
                # node + its declared path so the outbound intent is visible.
                target_id = str(row.get("cid") or "")
                if not target_id:
                    continue
                cmethod = str(row.get("cmethod") or "")
                cpath = str(row.get("cpath") or "")
                nodes[target_id] = {
                    "id": target_id,
                    "kind": "client",
                    "fqn": f"{cmethod} {cpath}".strip() or str(row.get("cfqn") or ""),
                    "microservice": str(row.get("cms") or ""),
                }
                edge_type = "HTTP_CALLS"
            edges.append({
                "other_id": target_id,
                "edge_type": edge_type,
                "confidence": float(row.get("conf") or 0.0) or None,
            })
        edges = _dedupe_traversal_edges(edges)
        truncated = len(edges) > limit
        edges = edges[:limit]
        return {
            "root_id": root_id,
            "nodes": nodes,
            "edges": edges,
            "noun": "callees",
            "warnings": [],
            "truncated": truncated,
            "is_external_entrypoint": False,
        }

    # ---- Symbol root -> find_callees ----
    depth = getattr(args, "depth", 1)
    min_conf = getattr(args, "min_confidence", 0.0)
    exclude_external = not getattr(args, "include_external", False)
    call_edges = graph.find_callees(
        node.fqn,
        depth=depth,
        limit=limit + 1,
        min_confidence=min_conf,
        exclude_external=exclude_external,
        module=args.module,
        microservice=args.service,
    )
    display, truncated = mark_truncated(call_edges, limit)
    root_id = node.id
    nodes = {root_id: _noderef_to_node_dict(node)}
    edges = []
    for ce in display:
        nodes[ce.dst.id] = _symbol_hit_to_dict(ce.dst)
        edges.append(
            {"other_id": ce.dst.id, "edge_type": "CALLS", "confidence": ce.confidence}
        )
    edges = _dedupe_traversal_edges(edges)
    truncated = truncated or len(edges) > limit
    edges = edges[:limit]
    return {
        "root_id": root_id,
        "nodes": nodes,
        "edges": edges,
        "noun": "callees",
        "warnings": [],
        "truncated": truncated,
        "is_external_entrypoint": False,
    }


# ---------------------------------------------------------------------------
# flow_payload
# ---------------------------------------------------------------------------


def flow_payload(args: argparse.Namespace, cfg, graph) -> dict[str, Any]:
    """Assemble the traversal payload for ``jrag flow``.

    Route root -> ``trace_request_flow`` plus the inbound/outbound merge +
    client-side truncation, transcribed verbatim from ``_cmd_flow``.
    """
    node = _resolve_traversal(
        args, cfg=cfg, graph=graph, hint_kind="route", apply_scope=False
    )

    _kind_guard(
        node,
        args=args,
        expected="flow requires a Route root",
        kinds=("route",),
        hint="Pass a route path (e.g. /chat/assign).",
    )

    warnings = _warn_unapplied_scope(
        args,
        reason="trace_request_flow carries no microservice predicate; intra-codebase is an index-time data property",
    )

    limit = _clamped_limit(args)
    max_hops = max(1, min(8, getattr(args, "depth", 5)))
    flow_data = graph.trace_request_flow(entry_route_id=node.id, max_hops=max_hops)

    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    # Inbound: cross-service HTTP/async callers (Client/Producer two-hop).
    for row in flow_data.get("inbound", []):
        caller_id = str(row.get("caller_node_id") or "")
        if not caller_id:
            continue
        kind = str(row.get("caller_node_kind") or "")
        nodes[caller_id] = {
            "id": caller_id,
            "kind": kind,
            "fqn": str(row.get("declaring_symbol_fqn") or ""),
            "microservice": str(row.get("microservice") or ""),
        }
        edges.append(
            {
                "other_id": caller_id,
                "edge_type": "HTTP_CALLS" if kind == "client" else "ASYNC_CALLS",
                "confidence": float(row.get("confidence") or 0.0),
            }
        )
    # Outbound: CALLS hops from the route handler (intra-service by construction).
    for row in flow_data.get("outbound", []):
        next_id = str(row.get("next_symbol_id") or "")
        if not next_id:
            continue
        nodes[next_id] = {
            "id": next_id,
            "kind": "symbol",
            "fqn": str(row.get("next_fqn") or ""),
            "microservice": str(row.get("next_microservice") or ""),
        }
        edges.append({"other_id": next_id, "edge_type": "CALLS"})

    # Client-side slice for truncation (trace_request_flow has no limit param).
    truncated = len(edges) > limit
    if truncated:
        edges = edges[:limit]
    return {
        "root_id": root_id,
        "nodes": nodes,
        "edges": edges,
        "noun": "flow",
        "warnings": warnings,
        "truncated": truncated,
        "is_external_entrypoint": False,
    }


# ---------------------------------------------------------------------------
# search_payload
# ---------------------------------------------------------------------------


def search_payload(args: argparse.Namespace, cfg, graph):
    """Return the ``SearchOutput`` for ``jrag search``.

    Builds the ``NodeFilter`` from args (the same filter set the handler builds)
    and calls ``mcp_v2.search_v2`` with ``limit+1`` for +1-fetch truncation.
    Pre-validation (``--fuzzy``, ``limit==0`` short-circuit, ``--framework``
    enum check) and post-processing (framework post-filter, ``--explain``,
    ``--min-score``, truncation, zero-result guidance) stay in the handler —
    this is the clean ``search_v2`` core.
    """
    from java_codebase_rag.mcp import mcp_v2

    # Build NodeFilter from flags (same set as the handler / `find` filter mode).
    # NOTE: --framework is intentionally NOT placed in the NodeFilter (the graph
    # stores framework only on Route nodes; the handler applies it as a
    # client-side POST-filter after the hits come back).
    filter_dict: dict = {}
    if args.service:
        filter_dict["microservice"] = args.service
    if args.module:
        filter_dict["module"] = args.module
    if args.role:
        filter_dict["role"] = normalize_enum(args.role, kind="role")
    if args.exclude_role:
        filter_dict["exclude_roles"] = [normalize_enum(args.exclude_role, kind="role")]
    if args.annotation:
        filter_dict["annotation"] = args.annotation
    if args.capability:
        filter_dict["capability"] = args.capability
    if args.fqn_contains:
        filter_dict["fqn_contains"] = args.fqn_contains
    if args.java_kind:
        filter_dict["symbol_kind"] = normalize_enum(args.java_kind, kind="java_kind")

    node_filter, err_env = _build_node_filter_or_error(filter_dict)
    if err_env is not None:
        raise PayloadError(err_env, 2)

    limit = min(args.limit if args.limit is not None else 20, 499)
    return mcp_v2.search_v2(
        args.query,
        table=args.table,
        hybrid=args.hybrid,
        limit=limit + 1,  # +1 for truncated detection
        offset=args.offset,
        path_contains=args.path_contains,
        filter=node_filter,
        explain=args.explain,
        graph=graph,
        dedup=not getattr(args, "chunks", False),
    )


# ---------------------------------------------------------------------------
# find_payload
# ---------------------------------------------------------------------------


def find_payload(args: argparse.Namespace, cfg, graph) -> dict[str, Any]:
    """Return the find payload, selecting query vs filter mode exactly as
    ``_cmd_find`` / ``_cmd_find_*`` do today.

    * Query mode (positional ``<query>``): ``graph.find_by_name_or_fqn`` plus
      the existing client-side post-filters (role/annotation/capability). Returns
      ``{"mode": "query", "rows", "raw_truncated", "post_filter_active", "limit",
      "query", "kinds", "matched_mode", "identifier_matched"}``. With ``--fuzzy``,
      an empty exact result widens to prefix then substring (issue #375);
      ``matched_mode`` is the tier that hit (``exact``/``prefix``/``contains``).
    * Filter mode: ``mcp_v2.find_v2``. Returns
      ``{"mode": "filter", "kind", "out" (FindOutput), "limit"}``.

    Kind-contradiction / query-mode-kind errors raise :class:`PayloadError`.
    Rendering (nodes/warnings/empty-result hint/offset) stays in the handler.
    """
    from java_codebase_rag.mcp import mcp_v2

    inferred = _infer_kind(args)
    is_contradiction, error_msg = _check_kind_contradiction(args, inferred)
    if is_contradiction:
        raise PayloadError(
            Envelope(status="error", message=error_msg or "kind contradiction"), 2
        )

    # Cap at 499 so limit+1 <= 500 (backend clamp); default 20.
    raw_limit = args.limit if args.limit is not None else 20
    limit = min(raw_limit, 499)

    # ---- Query mode: positional <query> present ----
    if args.query:
        effective_kind = inferred or "symbol"
        if effective_kind != "symbol":
            raise PayloadError(
                Envelope(
                    status="error",
                    message=(
                        f"query mode (positional <query>) only searches Symbols, but kind "
                        f"'{effective_kind}' was {'inferred from domain flags' if args.kind is None else 'set via --kind'}. "
                        "Drop the positional <query> and use filter mode (the domain flags) "
                        "for route/client/producer searches."
                    ),
                ),
                2,
            )
        query = args.query
        # find_by_name_or_fqn is always Symbol; the only valid kinds filter is
        # the symbol sub-kind derived from --java-kind.
        if args.java_kind:
            java_kind_norm = normalize_enum(args.java_kind, kind="java_kind")
            kinds = [java_kind_norm.lower()]
        else:
            kinds = None

        rows = graph.find_by_name_or_fqn(
            query,
            kinds=kinds,
            module=args.module,
            microservice=args.service,
            limit=limit + 1,  # +1 for truncated detection
        )
        # --fuzzy: widen an empty exact result to prefix (STARTS WITH) then
        # substring (CONTAINS) on name/FQN (issue #375). Fuzzy modes exclude
        # file/package Symbol nodes (their fqn is a filesystem path) — enforced
        # in find_by_name_or_fqn's ``mode`` handling.
        matched_mode = "exact"
        if not rows and getattr(args, "fuzzy", False) and query:
            for fb_mode in ("prefix", "contains"):
                rows = graph.find_by_name_or_fqn(
                    query, kinds=kinds, module=args.module, microservice=args.service,
                    limit=limit + 1, mode=fb_mode,
                )
                if rows:
                    matched_mode = fb_mode
                    break
        # Did any tier (exact or fallback) return rows BEFORE post-filters? Used
        # to distinguish "identifier genuinely matched nothing" from "matched but
        # --role/--annotation/--capability removed all hits" in the empty-result hint.
        identifier_matched = bool(rows)
        # Truncation is decided by the RAW name/FQN fetch (limit+1), BEFORE
        # post-filters reduce the set.
        raw_truncated = len(rows) > limit

        # Post-filter by role/annotation/capability (SymbolHit carries these).
        post_filter_active = False
        if args.role:
            post_filter_active = True
            role_norm = normalize_enum(args.role, kind="role")
            rows = [r for r in rows if (r.role or "").upper().replace("-", "_") == role_norm.upper()]
        if args.exclude_role:
            post_filter_active = True
            exclude_role_norm = normalize_enum(args.exclude_role, kind="role")
            rows = [r for r in rows if (r.role or "").upper().replace("-", "_") != exclude_role_norm.upper()]
        if args.annotation:
            post_filter_active = True
            rows = [r for r in rows if args.annotation in (r.annotations or [])]
        if args.capability:
            post_filter_active = True
            rows = [r for r in rows if args.capability in (r.capabilities or [])]

        return {
            "mode": "query",
            "rows": rows,
            "raw_truncated": raw_truncated,
            "post_filter_active": post_filter_active,
            "limit": limit,
            "query": query,
            "kinds": kinds,
            "matched_mode": matched_mode,
            "identifier_matched": identifier_matched,
        }

    # ---- Filter mode: build NodeFilter and call find_v2 ----
    kind = inferred or "symbol"
    filter_dict: dict = {}
    if args.service:
        filter_dict["microservice"] = args.service
    if args.module:
        filter_dict["module"] = args.module
    if args.role:
        filter_dict["role"] = normalize_enum(args.role, kind="role")
    if args.exclude_role:
        filter_dict["exclude_roles"] = [normalize_enum(args.exclude_role, kind="role")]
    if args.annotation:
        filter_dict["annotation"] = args.annotation
    if args.capability:
        filter_dict["capability"] = args.capability
    if args.fqn_contains:
        filter_dict["fqn_contains"] = args.fqn_contains
    if args.java_kind:
        filter_dict["symbol_kind"] = normalize_enum(args.java_kind, kind="java_kind")
    if args.framework:
        filter_dict["framework"] = normalize_enum(args.framework, kind="framework")
    if args.source_layer:
        filter_dict["source_layer"] = normalize_enum(args.source_layer, kind="source_layer")
    if args.http_method:
        filter_dict["http_method"] = args.http_method.upper()
    if args.path_contains:
        filter_dict["path_contains"] = args.path_contains
    if args.client_kind:
        filter_dict["client_kind"] = normalize_enum(args.client_kind, kind="client_kind")
    if args.calls_service:
        filter_dict["target_service"] = args.calls_service
    if args.calls_path_contains:
        filter_dict["target_path_contains"] = args.calls_path_contains
    if args.producer_kind:
        filter_dict["producer_kind"] = normalize_enum(args.producer_kind, kind="producer_kind")
    if args.topic_contains:
        filter_dict["topic_contains"] = args.topic_contains

    node_filter, err_env = _build_node_filter_or_error(filter_dict)
    if err_env is not None:
        raise PayloadError(err_env, 2)

    out = mcp_v2.find_v2(
        kind=kind,
        filter=node_filter,
        limit=limit + 1,  # +1 for has_more_results detection
        offset=args.offset,
        graph=graph,
    )
    return {"mode": "filter", "kind": kind, "out": out, "limit": limit}


# ---------------------------------------------------------------------------
# inspect_payload
# ---------------------------------------------------------------------------


def inspect_payload(args: argparse.Namespace, cfg, graph) -> dict[str, Any]:
    """Return the inspect payload: resolve + ``describe_v2``.

    inspect's renderer needs ``file_location`` from the resolve ``Envelope``
    (not present on ``DescribeOutput``), so the payload carries the resolved
    node id/fqn and file_location alongside the ``DescribeOutput``. The
    NodeRecord -> envelope-node flatten + render stays in the handler.
    """
    from java_codebase_rag.mcp import mcp_v2

    # inspect forwards --service/--module into resolve (disambiguates by scope).
    node, env = resolve_query(
        args.query,
        hint_kind=args.kind,
        java_kind=args.java_kind,
        role=args.role,
        fqn_contains=args.fqn_contains,
        cfg=cfg,
        graph=graph,
        microservice=getattr(args, "service", None) or "",
        module=getattr(args, "module", None) or "",
    )
    if env.status != "ok":
        raise PayloadError(env, 2 if env.status == "error" else 0)

    desc_out = mcp_v2.describe_v2(id=node.id, graph=graph)
    return {
        "describe": desc_out,
        "node_id": node.id,
        "node_fqn": node.fqn,
        "file_location": env.file_location,
    }
