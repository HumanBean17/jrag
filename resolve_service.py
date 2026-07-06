"""Resolve service for mapping identifiers to graph nodes.

Transport-agnostic resolve pipeline extracted from mcp_v2.py for reuse
by the CLI layer. Provides resolve_v2(identifier, hint_kind, graph) -> ResolveOutput.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_types import (
    NodeRef,
    StructuredHint,
    _hints_or_skip,
    _node_ref_from_row,
    _to_structured_hints,
    set_hints_enabled,
)
from java_ontology import ResolveReason
from ladybug_queries import LadybugGraph
from mcp_hints import MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION

__all__ = [
    "resolve_v2",
    "ResolveOutput",
    "ResolveCandidate",
    "ResolveStatus",
    "set_hints_enabled",
]


ResolveStatus = Literal["one", "many", "none"]

_RESOLVE_CANDIDATE_CAP = 10

_RESOLVE_REASON_PRIORITY: dict[ResolveReason, int] = {
    "exact_id": 0,
    "exact_fqn": 1,
    "route_method_path": 1,
    "client_target_path": 1,
    "producer_topic_prefix": 1,
    "fqn_suffix": 2,
    "route_template": 2,
    "route_topic": 2,
    "client_fqn": 2,
    "short_name": 3,
    "client_target": 3,
    "client_name": 3,
    "producer_topic": 3,
    "route_topic_prefix": 3,
}

_SYMBOL_RESOLVE_RETURN = (
    "s.id AS id, s.fqn AS fqn, s.microservice AS microservice, "
    "s.module AS module, s.role AS role, s.kind AS symbol_kind"
)

_ROUTE_RESOLVE_RETURN = (
    "r.id AS id, r.kind AS kind, r.framework AS framework, r.method AS method, "
    "r.path AS path, r.path_template AS path_template, r.path_regex AS path_regex, "
    "r.topic AS topic, r.broker AS broker, r.feign_name AS feign_name, r.feign_url AS feign_url, "
    "r.microservice AS microservice, r.module AS module, r.filename AS filename, "
    "r.start_line AS start_line, r.end_line AS end_line, r.resolved AS resolved"
)

_CLIENT_RESOLVE_RETURN = (
    "c.id AS id, c.client_kind AS client_kind, c.target_service AS target_service, "
    "c.method AS method, c.path AS path, c.path_template AS path_template, "
    "c.path_regex AS path_regex, c.member_fqn AS member_fqn, c.member_id AS member_id, "
    "c.microservice AS microservice, c.module AS module, c.filename AS filename, "
    "c.start_line AS start_line, c.end_line AS end_line, c.resolved AS resolved, "
    "c.source_layer AS source_layer"
)

_PRODUCER_RESOLVE_RETURN = (
    "p.id AS id, p.producer_kind AS producer_kind, p.topic AS topic, p.broker AS broker, "
    "p.direction AS direction, p.member_fqn AS member_fqn, p.member_id AS member_id, "
    "p.microservice AS microservice, p.module AS module, p.filename AS filename, "
    "p.start_line AS start_line, p.end_line AS end_line, p.resolved AS resolved, "
    "p.source_layer AS source_layer"
)

_RESOLVE_PRE_DEDUP_LIMIT = 50


def _scope_clause(
    alias: str,
    microservice: str = "",
    module: str = "",
) -> tuple[str, dict[str, str]]:
    """Build a Cypher AND-clause scoping a node alias by microservice/module.

    Returns ``(clause, params)`` where ``clause`` is ``""`` or
    ``" AND <alias>.microservice = $ms AND <alias>.module = $mod"`` and
    ``params`` carries only the bound scope values. Used by the candidate
    matchers to push ``--service``/``--module`` down into resolve so they act
    as resolve-time filters (not just traversal post-filters).
    """
    preds: list[str] = []
    params: dict[str, str] = {}
    if microservice:
        preds.append(f"{alias}.microservice = $ms")
        params["ms"] = microservice
    if module:
        preds.append(f"{alias}.module = $mod")
        params["mod"] = module
    clause = (" AND " + " AND ".join(preds)) if preds else ""
    return clause, params


class ResolveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: NodeRef
    score: float
    reason: ResolveReason


class ResolveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: ResolveStatus
    node: NodeRef | None = None
    candidates: list[ResolveCandidate] = Field(default_factory=list)
    message: str | None = None
    resolved_identifier: str | None = None
    advisories: list[str] = Field(default_factory=list, description="Pure informational text with no tool call suggestion")
    hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)


def _resolve_validate_identifier(raw: str) -> tuple[str | None, str | None]:
    trimmed = raw.strip()
    if not trimmed:
        detail = "empty string" if raw == "" else "whitespace only"
        return None, f"Invalid identifier: {detail}"
    return trimmed, None


def _resolve_kinds_to_search(
    hint_kind: Literal["symbol", "route", "client", "producer"] | None,
) -> list[Literal["symbol", "route", "client", "producer"]]:
    if hint_kind is None:
        return ["symbol", "route", "client", "producer"]
    return [hint_kind]


def _resolve_parse_route_method_path(identifier: str) -> tuple[str, str] | None:
    parts = identifier.split(None, 1)
    if len(parts) != 2:
        return None
    method, path = parts[0].upper(), parts[1].strip()
    if not method.isalpha() or not path.startswith("/"):
        return None
    return method, path


def _resolve_parse_microservice_route(identifier: str) -> tuple[str, str, str] | None:
    parts = identifier.split(None, 2)
    if len(parts) != 3:
        return None
    microservice, method, path = parts[0], parts[1].upper(), parts[2].strip()
    if not method.isalpha() or not path.startswith("/"):
        return None
    return microservice, method, path


def _resolve_symbol_candidates(
    g: LadybugGraph,
    identifier: str,
    *,
    microservice: str = "",
    module: str = "",
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT
    scope, scope_params = _scope_clause("s", microservice, module)

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.id = $id{scope} RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "exact_id", len(identifier)))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.fqn = $fqn{scope} RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"fqn": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "exact_fqn", len(identifier)))

    # Method FQN without arg signature (e.g. "pkg.Cls#method"): the stored method
    # fqn is "pkg.Cls#method(Type,Type)", so an argless identifier misses the
    # exact match above. Prefix-match on "<identifier>(" so the agent doesn't
    # have to type the exact "(Type,Type)" signature. Multiple overloads → the
    # resolve "many" path surfaces them honestly as ambiguous candidates.
    if "#" in identifier and "(" not in identifier:
        rows = g._rows(  # noqa: SLF001
            f"MATCH (s:Symbol) WHERE s.fqn STARTS WITH $mp{scope} "
            f"RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
            {"mp": identifier + "(", "lim": lim, **scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("symbol", row), "fqn_suffix", len(identifier) + 1))
        # Short "Cls#method" form (no package): the identifier is "<Class>#<method>"
        # with no dot in the class part. Match symbols whose fqn contains
        # "<Class>#<method>(" so overloads surface honestly as ambiguous.
        if "." not in identifier.split("#", 1)[0]:
            class_part, _, method_part = identifier.partition("#")
            if class_part and method_part:
                contains = f".{class_part}#{method_part}("
                rows = g._rows(  # noqa: SLF001
                    f"MATCH (s:Symbol) WHERE s.fqn CONTAINS $mp{scope} "
                    f"RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
                    {"mp": contains, "lim": lim, **scope_params},
                )
                for row in rows:
                    fqn = str(row.get("fqn") or "")
                    out.append(
                        (_node_ref_from_row("symbol", row), "fqn_suffix", len(identifier) + 1)
                    )

    suffix = f".{identifier}"
    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.fqn = $ident OR s.fqn ENDS WITH $suffix{scope} "
        f"RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"ident": identifier, "suffix": suffix, "lim": lim, **scope_params},
    )
    for row in rows:
        fqn = str(row.get("fqn") or "")
        spec = len(fqn)
        out.append((_node_ref_from_row("symbol", row), "fqn_suffix", spec))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.name = $name{scope} RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"name": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "short_name", len(identifier)))

    return out


def _resolve_route_candidates(
    g: LadybugGraph,
    identifier: str,
    *,
    microservice: str = "",
    module: str = "",
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT
    scope, scope_params = _scope_clause("r", microservice, module)

    rows = g._rows(  # noqa: SLF001
        f"MATCH (r:Route) WHERE r.id = $id{scope} RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("route", row), "exact_id", len(identifier)))

    ms_route = _resolve_parse_microservice_route(identifier)
    if ms_route is not None:
        microservice_ms, method, path = ms_route
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.microservice = $ms AND r.method = $method "
            f"AND (r.path = $path OR r.path_template = $path){scope} "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"ms": microservice_ms, "method": method, "path": path, "lim": lim, **scope_params},
        )
        for row in rows:
            spec = len(path)
            out.append((_node_ref_from_row("route", row), "route_method_path", spec))

    method_path = _resolve_parse_route_method_path(identifier)
    if method_path is not None:
        method, path = method_path
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.method = $method "
            f"AND (r.path = $path OR r.path_template = $path){scope} "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"method": method, "path": path, "lim": lim, **scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("route", row), "route_method_path", len(path)))

    if identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.path = $path OR r.path_template = $path{scope} "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"path": identifier, "lim": lim, **scope_params},
        )
        for row in rows:
            path_val = str(row.get("path_template") or row.get("path") or "")
            out.append((_node_ref_from_row("route", row), "route_template", len(path_val)))

    # Kafka/topic routes carry their name in ``topic`` (``path``/``path_template``
    # are empty), so path-based matching above cannot reach them. Match on
    # ``r.topic`` the same way ``_resolve_producer_candidates`` matches
    # ``p.topic`` — this lets ``flow``/``callers``/``overview`` resolve a
    # ``kafka_topic`` Route by topic name. ``_drop_route_mirrors`` below then
    # discards the no-EXPOSES producer phantom in favour of the server route.
    rows = g._rows(  # noqa: SLF001
        f"MATCH (r:Route) WHERE r.topic = $topic{scope} RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
        {"topic": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("route", row), "route_topic", len(identifier)))

    if not identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.topic STARTS WITH $topic{scope} "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"topic": identifier, "lim": lim, **scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("route", row), "route_topic_prefix", len(identifier)))

    return _drop_route_mirrors(g, out)


def _drop_route_mirrors(
    g: LadybugGraph,
    cands: list[tuple[NodeRef, ResolveReason, int]],
) -> list[tuple[NodeRef, ResolveReason, int]]:
    """Drop client-side Route mirrors that collide with a server-exposed route.

    A path can resolve to TWO Route nodes: the server route (exposed by a
    controller via an inbound ``EXPOSES`` edge, ``microservice`` set) and a
    client-side mirror (no ``EXPOSES``, often ``microservice=''``) created when
    a Client's HTTP call couldn't be linked to the server route. The mirror is
    an artifact — drop it when a server-exposed route shares the same
    ``(method, path_template)`` so the no-flags ``jrag callers '/path'`` flow
    resolves to the single server route instead of stalling on "ambiguous".
    GENUINE ambiguity (two server-exposed routes in different microservices
    sharing a path) is preserved — both have ``EXPOSES`` and survive.
    """
    if len(cands) < 2:
        return cands
    ids = [c[0].id for c in cands if c[0].id]
    if not ids:
        return cands
    id_list = ", ".join("'" + cid.replace("'", "''") + "'" for cid in ids)
    exposed_rows = g._rows(  # noqa: SLF001
        "MATCH (s:Symbol)-[:EXPOSES]->(r:Route) "
        f"WHERE r.id IN [{id_list}] "
        "RETURN r.id AS rid",
        {},
    )
    exposed_ids = {str(r.get("rid") or "") for r in exposed_rows}
    if not exposed_ids:
        return cands

    # Group candidates by their route fqn ("METHOD path"); within a colliding
    # group, drop non-exposed (mirror) entries only when an exposed entry exists.
    groups: dict[str, list[tuple[NodeRef, ResolveReason, int]]] = {}
    for node, reason, spec in cands:
        groups.setdefault(str(node.fqn or ""), []).append((node, reason, spec))

    keep: list[tuple[NodeRef, ResolveReason, int]] = []
    for group in groups.values():
        has_exposed = any(c[0].id in exposed_ids for c in group)
        for node, reason, spec in group:
            if has_exposed and node.id not in exposed_ids:
                continue  # mirror colliding with a server route — drop
            keep.append((node, reason, spec))
    return keep


def _resolve_client_candidates(
    g: LadybugGraph,
    identifier: str,
    *,
    microservice: str = "",
    module: str = "",
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT
    scope, scope_params = _scope_clause("c", microservice, module)

    rows = g._rows(  # noqa: SLF001
        f"MATCH (c:Client) WHERE c.id = $id{scope} RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("client", row), "exact_id", len(identifier)))

    if " " in identifier:
        target, path_prefix = identifier.split(" ", 1)
        target = target.strip()
        path_prefix = path_prefix.strip()
        if target and path_prefix:
            rows = g._rows(  # noqa: SLF001
                f"MATCH (c:Client) WHERE c.target_service = $target "
                f"AND (c.path STARTS WITH $path OR c.path_template STARTS WITH $path){scope} "
                f"RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
                {"target": target, "path": path_prefix, "lim": lim, **scope_params},
            )
            for row in rows:
                spec = len(path_prefix)
                out.append((_node_ref_from_row("client", row), "client_target_path", spec))
    elif not identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (c:Client) WHERE c.target_service = $target{scope} "
            f"RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
            {"target": identifier, "lim": lim, **scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("client", row), "client_target", len(identifier)))

    # Client-by-name/FQN: reach a Client root via its declaring Symbol (the
    # method that declares the client). Only SUFFIX/NAME matches are used — a
    # full method FQN identifier (e.g. 'pkg.Cls#method(Arg)') is intentionally
    # left to resolve to the method Symbol (exact_fqn) rather than ALSO matching
    # the Client declared by that same method, which would surface a spurious
    # "ambiguous" result. A bare class name (no '#') does not suffix-match a
    # Client's member_fqn (which carries '#method(args)'), so it resolves to the
    # type Symbol; a bare method name ('joinOperator') matches Client(s) via the
    # declaring symbol name and surfaces honest ambiguity across clients.
    if " " not in identifier and not identifier.startswith("/"):
        sym_scope, sym_scope_params = _scope_clause("s", microservice, module)
        # Declaring symbol name match (e.g. 'joinOperator').
        rows = g._rows(  # noqa: SLF001
            "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client) "
            f"WHERE s.name = $name{sym_scope} "
            f"RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
            {"name": identifier, "lim": lim, **sym_scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("client", row), "client_name", len(identifier)))
        # Declaring symbol FQN / member_fqn SUFFIX match (safe: a full method
        # FQN with args never suffix-matches because stored fqns carry the arg
        # suffix; '.<identifier>' only matches a class-level identifier).
        suffix = "." + identifier
        rows = g._rows(  # noqa: SLF001
            "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client) "
            f"WHERE s.fqn ENDS WITH $suffix OR c.member_fqn ENDS WITH $suffix{sym_scope} "
            f"RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
            {"suffix": suffix, "lim": lim, **sym_scope_params},
        )
        for row in rows:
            fqn = str(row.get("member_fqn") or "")
            out.append((_node_ref_from_row("client", row), "client_fqn", len(fqn) or len(identifier)))

    return out


def _resolve_producer_candidates(
    g: LadybugGraph,
    identifier: str,
    *,
    microservice: str = "",
    module: str = "",
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT
    scope, scope_params = _scope_clause("p", microservice, module)

    rows = g._rows(  # noqa: SLF001
        f"MATCH (p:Producer) WHERE p.id = $id{scope} RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("producer", row), "exact_id", len(identifier)))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (p:Producer) WHERE p.topic = $topic{scope} RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
        {"topic": identifier, "lim": lim, **scope_params},
    )
    for row in rows:
        out.append((_node_ref_from_row("producer", row), "producer_topic", len(identifier)))

    if not identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (p:Producer) WHERE p.topic STARTS WITH $topic{scope} "
            f"RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
            {"topic": identifier, "lim": lim, **scope_params},
        )
        for row in rows:
            out.append((_node_ref_from_row("producer", row), "producer_topic_prefix", len(identifier)))

    return out


def _resolve_dedupe_candidates(
    raw: list[tuple[NodeRef, ResolveReason, int]],
) -> list[tuple[NodeRef, ResolveReason, int]]:
    best: dict[str, tuple[NodeRef, ResolveReason, int]] = {}
    for node, reason, specificity in raw:
        prev = best.get(node.id)
        if prev is None:
            best[node.id] = (node, reason, specificity)
            continue
        prev_pri = _RESOLVE_REASON_PRIORITY[prev[1]]
        new_pri = _RESOLVE_REASON_PRIORITY[reason]
        if new_pri < prev_pri or (new_pri == prev_pri and specificity > prev[2]):
            best[node.id] = (node, reason, specificity)
    return list(best.values())


def _resolve_rank_candidates(
    deduped: list[tuple[NodeRef, ResolveReason, int]],
) -> list[ResolveCandidate]:
    ordered = sorted(
        deduped,
        key=lambda item: (_RESOLVE_REASON_PRIORITY[item[1]], -item[2], item[0].id),
    )
    total = len(ordered)
    return [
        ResolveCandidate(
            node=node,
            reason=reason,
            score=(1.0 - (idx / total)) if total else 0.0,
        )
        for idx, (node, reason, _spec) in enumerate(ordered)
    ]


def _resolve_assert_invariants(out: ResolveOutput) -> None:
    if not out.success:
        assert out.status == "none"
        assert out.node is None
        assert not out.candidates
        assert out.message
        return
    if out.status == "one":
        assert out.node is not None
        assert not out.candidates
    elif out.status == "many":
        assert out.node is None
        assert len(out.candidates) >= 2
    elif out.status == "none":
        assert out.node is None
        assert not out.candidates
        assert out.message


def _resolve_seeds_for_hints(identifier: str) -> tuple[str | None, str | None]:
    path_prefix_seed: str | None = None
    method_path = _resolve_parse_route_method_path(identifier)
    if method_path is not None:
        path_prefix_seed = method_path[1]
    else:
        ms_route = _resolve_parse_microservice_route(identifier)
        if ms_route is not None:
            path_prefix_seed = ms_route[2]
        elif identifier.startswith("/"):
            path_prefix_seed = identifier

    target_service_seed: str | None = None
    if " " in identifier:
        target, _path_prefix = identifier.split(" ", 1)
        target = target.strip()
        if target:
            target_service_seed = target
    elif not identifier.startswith("/"):
        target_service_seed = identifier

    return path_prefix_seed, target_service_seed


def _resolve_finalize_success(
    trimmed: str,
    hint_kind: Literal["symbol", "route", "client", "producer"] | None,
    matches: list[ResolveCandidate],
) -> ResolveOutput:
    if not matches:
        out = ResolveOutput(
            success=True,
            status="none",
            message=(
                "No matches for identifier; use search(query=...) for ranked fuzzy lookup."
            ),
            resolved_identifier=trimmed,
        )
    elif len(matches) == 1:
        out = ResolveOutput(
            success=True,
            status="one",
            node=matches[0].node,
            resolved_identifier=trimmed,
        )
    else:
        out = ResolveOutput(
            success=True,
            status="many",
            candidates=matches,
            resolved_identifier=trimmed,
        )

    path_prefix_seed, target_service_seed = _resolve_seeds_for_hints(trimmed)
    hint_payload = {
        "status": out.status,
        "resolved_identifier": trimmed,
        "candidates": out.candidates,
        "hint_kind": hint_kind,
        "path_prefix_seed": path_prefix_seed,
        "target_service_seed": target_service_seed,
    }
    raw_struct, raw_advisories = _hints_or_skip("resolve", hint_payload)
    out = out.model_copy(update={
        "advisories": raw_advisories,
        "hints_structured": _to_structured_hints(raw_struct),
    })
    _resolve_assert_invariants(out)
    return out


def resolve_v2(
    identifier: str,
    hint_kind: Literal["symbol", "route", "client", "producer"] | None = None,
    graph: LadybugGraph | None = None,
    *,
    microservice: str = "",
    module: str = "",
) -> ResolveOutput:
    try:
        trimmed, err = _resolve_validate_identifier(identifier)
        if err is not None:
            out = ResolveOutput(
                success=False,
                status="none",
                message=err,
                advisories=[],
                resolved_identifier=None,
            )
            _resolve_assert_invariants(out)
            return out

        assert trimmed is not None
        if "*" in trimmed or "?" in trimmed:
            out = ResolveOutput(
                success=False,
                status="none",
                message=(
                    "Wildcards (* and ?) are not supported in resolve; "
                    "use search(query=...) for ranked text search."
                ),
                advisories=[],
                resolved_identifier=trimmed,
            )
            _resolve_assert_invariants(out)
            return out

        g = graph or LadybugGraph.get()
        raw: list[tuple[NodeRef, ResolveReason, int]] = []
        for kind in _resolve_kinds_to_search(hint_kind):
            if kind == "symbol":
                raw.extend(_resolve_symbol_candidates(g, trimmed, microservice=microservice, module=module))
            elif kind == "route":
                raw.extend(_resolve_route_candidates(g, trimmed, microservice=microservice, module=module))
            elif kind == "client":
                raw.extend(_resolve_client_candidates(g, trimmed, microservice=microservice, module=module))
            else:
                raw.extend(_resolve_producer_candidates(g, trimmed, microservice=microservice, module=module))

        deduped = _resolve_dedupe_candidates(raw)
        ranked = _resolve_rank_candidates(deduped)
        capped = ranked[:_RESOLVE_CANDIDATE_CAP]
        return _resolve_finalize_success(trimmed, hint_kind, capped)
    except Exception as exc:
        out = ResolveOutput(
            success=False,
            status="none",
            message=str(exc),
            advisories=[],
            resolved_identifier=None,
        )
        _resolve_assert_invariants(out)
        return out
