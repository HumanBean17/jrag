"""Pure MCP v2 road-sign hint generation (no graph I/O, no search, no LLM).

Locked v1 catalog: ``propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`` Appendix A
(issue #161 producer/override-route amendments in that appendix).
v2 resolve + neighbors fuzzy-strategy catalog: ``propose/completed/HINTS-V2-PROPOSE.md`` Appendix A.
v3 empty-neighbors structural catalog: ``propose/completed/HINTS-V3-PROPOSE.md`` §3.1–3.3.
v4 success-path catalog: ``propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md``.
Priority cap: same propose §7.12 / ``plans/completed/PLAN-HINTS.md`` principles.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, NamedTuple

from java_ontology import EDGE_SCHEMA, FUZZY_STRATEGY_SET

# Normative schema description (propose §3.1) — imported by ``mcp_v2`` for Field(description=...).
MCP_HINTS_FIELD_DESCRIPTION = (
    "Road-sign hints pointing to likely next calls. Each hint is a short string "
    "referencing one MCP V2 tool call. Hints are advisory and may be safely ignored. "
    "Maximum 5 hints per output. Describe-time type rollup hints may recommend "
    "DECLARES.* and OVERRIDDEN_BY.* dot-keys for neighbors() on matching Symbol origins; "
    "empty neighbors structural hints never use "
    "dot-key edge labels. For neighbors with multiple origin ids, empty-result "
    "structural hints describe the first origin only. On neighbors with "
    "edge_types=['CALLS'] only, optional edge_filter projects the ordered CALLS stream "
    "(min_confidence, strategies, callee_declaring_role axes); fail-loud with composed "
    "dot-keys or additional stored labels. include_unresolved interleaves "
    "UnresolvedCallSite rows (mutually exclusive with edge_filter). dedup_calls collapses "
    "identical (origin, callee) CALLS rows."
)

MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION = (
    "Machine-parseable next-action objects alongside hints. Each element has "
    "label (short semantic name, e.g. 'routes via members', 'implementors'), "
    "tool (MCP tool name), args (ready-to-use parameters), and actionable "
    "(True = direct call with complete args; False = advisory/partial — agent "
    "fills missing values or uses as guidance). Same trigger logic, priority, "
    "dedup, and cap as hints. See hints for human-readable versions."
)

# --- Internal structured hint representation (no mcp_v2 import) ---


class _StructuredHint(NamedTuple):
    tool: str
    args: dict[str, Any]
    actionable: bool
    priority: int
    label: str = ""


def finalize_structured_hints(scored: list[_StructuredHint]) -> list[_StructuredHint]:
    """Dedupe by ``(tool, json.dumps(args, sort_keys=True))``, keep highest priority, cap to 5."""
    best: dict[tuple[str, str], tuple[int, int]] = {}
    hints: dict[tuple[str, str], _StructuredHint] = {}
    for idx, h in enumerate(scored):
        key = (h.tool, json.dumps(h.args, sort_keys=True))
        prev = best.get(key)
        if prev is None or h.priority > prev[0]:
            best[key] = (h.priority, idx)
            hints[key] = h
        elif h.priority == prev[0]:
            best[key] = (h.priority, min(prev[1], idx))
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    return [hints[k] for k, _ in ordered[:5]]


_MEMBER_ONLY_DOT_KEY: dict[str, str] = {
    "DECLARES_CLIENT": "DECLARES.DECLARES_CLIENT",
    "DECLARES_PRODUCER": "DECLARES.DECLARES_PRODUCER",
    "EXPOSES": "DECLARES.EXPOSES",
}

_FIRST_NEIGHBORS_CALL_RE = re.compile(
    r"neighbors\(\[.*?\],'(in|out)',\['([^']+)'\]\)"
)


def _parse_first_traversal(template: str) -> tuple[str, list[str]] | None:
    m = _FIRST_NEIGHBORS_CALL_RE.search(template)
    if m:
        return m.group(1), [m.group(2)]
    return None


def _extract_other_ids(results: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for r in results:
        other = r.get("other")
        if isinstance(other, dict):
            oid = other.get("id")
            if isinstance(oid, str) and oid:
                ids.append(oid)
    return ids


# --- Appendix A verbatim templates (substitute {id}, {kind}, {limit}) ---

TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS = (
    "clients via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])"
)
TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS = (
    "routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])"
)
TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS = (
    "producers via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_PRODUCER'])"
)
TPL_DESCRIBE_METHOD_OVERRIDERS = "overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY'])"
TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS = (
    "clients in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.DECLARES_CLIENT'])"
)
TPL_DESCRIBE_METHOD_PRODUCERS_IN_OVERRIDERS = (
    "producers in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.DECLARES_PRODUCER'])"
)
TPL_DESCRIBE_METHOD_ROUTES_IN_OVERRIDERS = (
    "routes in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.EXPOSES'])"
)
TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT = "outbound client: neighbors(['{id}'],'out',['DECLARES_CLIENT'])"
TPL_DESCRIBE_METHOD_OUTBOUND_PRODUCER = "outbound producer: neighbors(['{id}'],'out',['DECLARES_PRODUCER'])"
TPL_DESCRIBE_METHOD_INBOUND_ROUTE = "inbound route: neighbors(['{id}'],'out',['EXPOSES'])"
TPL_DESCRIBE_METHOD_MANY_CALLS = "many CALLS — consider filtering by target microservice"
TPL_DESCRIBE_ROUTE_DECLARING = "declaring method: neighbors(['{id}'],'in',['EXPOSES'])"
TPL_DESCRIBE_CLIENT_DECLARING = "declaring method: neighbors(['{id}'],'in',['DECLARES_CLIENT'])"
TPL_DESCRIBE_PRODUCER_DECLARING = "declaring method: neighbors(['{id}'],'in',['DECLARES_PRODUCER'])"
TPL_DESCRIBE_TYPE_IMPLEMENTORS = "implementors: neighbors(['{id}'],'in',['IMPLEMENTS'])"
TPL_DESCRIBE_TYPE_IMPLEMENTS = "implements: neighbors(['{id}'],'out',['IMPLEMENTS'])"
TPL_DESCRIBE_TYPE_DEPENDENCIES = "dependencies: neighbors(['{id}'],'out',['INJECTS'])"
TPL_DESCRIBE_TYPE_INJECTORS = "injectors: neighbors(['{id}'],'in',['INJECTS'])"
TPL_DESCRIBE_METHOD_OUTBOUND_CALLS = "outbound calls: neighbors(['{id}'],'out',['CALLS'])"
TPL_DESCRIBE_METHOD_SUPER_DECL = "super declaration: neighbors(['{id}'],'out',['OVERRIDES'])"
TPL_DESCRIBE_METHOD_UNRESOLVED = "unresolved: neighbors(['{id}'],'out',['CALLS'],include_unresolved=True)"

TPL_FIND_EMPTY_RESOLVE = "no matches — try resolve(identifier, hint_kind='{kind}') for canonical lookup"
TPL_FIND_PAGE_FULL = "result page full at {limit} — narrow filter or paginate"
TPL_FIND_SUCCESS_HANDLER = "handler: neighbors(['{id}'],'in',['EXPOSES'])"
TPL_FIND_SUCCESS_HTTP_TARGETS = "HTTP targets: neighbors(['{id}'],'out',['HTTP_CALLS'])"
TPL_FIND_SUCCESS_ASYNC_TARGETS = "async targets: neighbors(['{id}'],'out',['ASYNC_CALLS'])"

_FIND_SUCCESS_MAX_CHARS = 120

TPL_NEIGHBORS_WRONG_SUBJECT_KIND = (
    "0 results — '{edge}' connects {src_kind} → {dst_kind}; "
    "this is a {subject_kind}. Try: {canonical_traversal}"
)

TPL_NEIGHBORS_WRONG_DIRECTION = (
    "0 results — '{edge}' is {src_kind} → {dst_kind}; "
    "you requested direction='{requested_dir}'. Try direction='{correct_dir}'."
)

TPL_NEIGHBORS_TYPE_LEVEL_REQUERY = (
    "0 results — '{edge}' lives on methods, not on {subject_kind}. "
    "Try: {canonical_traversal}"
)

TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED = (
    "edges on '{edge}' are emitted by the brownfield resolver — "
    "absence here may mean unresolved (no matching annotation/target), "
    "not absent from the codebase"
)

TPL_SEARCH_WEAK = "results look weak — narrow the query or try find(role=…)"

# --- v2: resolve templates (propose/HINTS-V2-PROPOSE.md Appendix A) ---

TPL_RESOLVE_NONE_TRY_SEARCH = (
    "no match — try search(query='{identifier}') for ranked fuzzy lookup"
)
TPL_RESOLVE_NONE_TRY_FIND_ROUTE = (
    "no match — try find(kind='route', filter={{path_prefix: '{seed}'}})"
)
TPL_RESOLVE_NONE_TRY_FIND_CLIENT = (
    "no match — try find(kind='client', filter={{target_service: '{seed}'}})"
)
TPL_RESOLVE_MANY_TIGHTEN = (
    "{n} candidates — tighten identifier or pick a candidate by id"
)

_RESOLVE_HINT_MAX_CHARS = 120
_RESOLVE_WILDCARDS = ("*", "?")

TPL_NEIGHBORS_FUZZY_STRATEGY = (
    "some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"
)

TPL_NEIGHBORS_CALLS_ROLE_FILTER_OTHER_FALLBACK = (
    "0 CALLS matched callee_declaring_role filter but method has many callees — "
    "targets may be OTHER (interface/JDK); try "
    "edge_filter={{exclude_callee_declaring_roles: ['ENTITY','DTO']}} instead of role exact match"
)

TPL_NEIGHBORS_CALLS_NODEFILTER_ROLE_COLLISION = (
    "NodeFilter.role filters the neighbor method's role (usually OTHER), not the callee's "
    "declaring type — use edge_filter={{callee_declaring_role: 'SERVICE'}} (or REPOSITORY) "
    "for CALLS stereotype projection"
)

_CALLS_HIGH_FANOUT_THRESHOLD = 10

TPL_NEIGHBORS_CALLS_HIGH_FANOUT = (
    "{n} CALLS on this method; the noisy axes are callee_declaring_role "
    "and per-call-site multiplicity. Try edge_filter={{callee_declaring_role: 'SERVICE'}} "
    "for delegation hops, edge_filter={{exclude_callee_declaring_roles: ['ENTITY','DTO']}} "
    "to drop accessor noise, edge_filter={{min_confidence: 0.5}} to trim low-confidence rows "
    "(exclude_external is find_callers-only, not neighbors), or dedup_calls=True to collapse "
    "identical callees."
)

TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED = (
    "{n} CALLS shown; this method also has {k} unresolved call sites "
    "(see describe(method_id).unresolved_call_sites, or call neighbors with "
    "include_unresolved=True for a source-ordered interleaved view — note "
    "include_unresolved is mutually exclusive with edge_filter)."
)

# v4 neighbors success-path (propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md); N1a/N1b alias describe templates.
TPL_NEIGHBORS_SUCCESS_HTTP_TARGETS = "HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])"
TPL_NEIGHBORS_SUCCESS_ASYNC_TARGETS = "async targets: neighbors(producer_ids,'out',['ASYNC_CALLS'])"
TPL_NEIGHBORS_SUCCESS_CALLERS = "callers: neighbors(handler_ids,'in',['CALLS'])"
TPL_NEIGHBORS_SUCCESS_DECLARING_CLIENT = (
    "declaring method: neighbors(client_ids,'in',['DECLARES_CLIENT'])"
)
TPL_NEIGHBORS_SUCCESS_DECLARING_PRODUCER = (
    "declaring method: neighbors(producer_ids,'in',['DECLARES_PRODUCER'])"
)
TPL_NEIGHBORS_SUCCESS_HANDLER = "handler: neighbors(route_ids,'in',['EXPOSES'])"

_NEIGHBORS_SUCCESS_MAX_CHARS = 120
_EDGE_DECLARES_CLIENT = frozenset({"DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT"})
_EDGE_DECLARES_PRODUCER = frozenset({"DECLARES_PRODUCER", "DECLARES.DECLARES_PRODUCER"})

# --- Labels for structured hints (semantic name of each hint) ---

LABEL_CLIENTS_VIA_MEMBERS = "clients via members"
LABEL_ROUTES_VIA_MEMBERS = "routes via members"
LABEL_PRODUCERS_VIA_MEMBERS = "producers via members"
LABEL_OVERRIDERS = "overriders"
LABEL_CLIENTS_IN_OVERRIDERS = "clients in overriders"
LABEL_PRODUCERS_IN_OVERRIDERS = "producers in overriders"
LABEL_ROUTES_IN_OVERRIDERS = "routes in overriders"
LABEL_OUTBOUND_CLIENT = "outbound client"
LABEL_OUTBOUND_PRODUCER = "outbound producer"
LABEL_INBOUND_ROUTE = "inbound route"
LABEL_HIGH_FANOUT = "high fanout"
LABEL_DECLARING_METHOD = "declaring method"
LABEL_IMPLEMENTORS = "implementors"
LABEL_IMPLEMENTS = "implements"
LABEL_DEPENDENCIES = "dependencies"
LABEL_INJECTORS = "injectors"
LABEL_OUTBOUND_CALLS = "outbound calls"
LABEL_SUPER_DECLARATION = "super declaration"
LABEL_UNRESOLVED = "unresolved"
LABEL_TRY_RESOLVE = "try resolve"
LABEL_PAGE_FULL = "page full"
LABEL_HANDLER = "handler"
LABEL_HTTP_TARGETS = "HTTP targets"
LABEL_ASYNC_TARGETS = "async targets"
LABEL_WEAK_RESULTS = "weak results"
LABEL_TRY_SEARCH = "try search"
LABEL_TRY_FIND_ROUTE = "try find route"
LABEL_TRY_FIND_CLIENT = "try find client"
LABEL_TIGHTEN_IDENTIFIER = "tighten identifier"
LABEL_FUZZY_STRATEGY = "fuzzy strategy"
LABEL_ROLE_FILTER_FALLBACK = "role filter fallback"
LABEL_ROLE_COLLISION = "role collision"
LABEL_HAS_UNRESOLVED = "has unresolved"
LABEL_CALLERS = "callers"
LABEL_WRONG_SUBJECT_KIND = "wrong subject kind"
LABEL_WRONG_DIRECTION = "wrong direction"
LABEL_TYPE_LEVEL_REQUERY = "type-level requery"

# §7.12 priority: DECLARES.* type rollups > OVERRIDDEN_BY.* > leaf follow-ups > meta.
PRIORITY_DECLARES_TYPE_ROLLUP = 4
PRIORITY_OVERRIDDEN_AXIS = 3
PRIORITY_LEAF_FOLLOWUP = 2
PRIORITY_META = 1

_TYPE_SYMBOL_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})
_METHOD_SYMBOL_KINDS = frozenset({"method", "constructor"})

_COMPOSED_DOT_KEY_PREFIXES = ("DECLARES.", "OVERRIDDEN_BY.")
# Row 4 (brownfield absence): only when the subject is a resolver endpoint node, not a
# structurally valid Symbol query that happens to be empty (DECLARES_CLIENT, EXPOSES, …).
_BROWNFIELD_ABSENCE_SUBJECT_LABELS = frozenset({"Client", "Producer", "Route"})
_REQUIRED_TRAVERSAL_ROLE_KEYS = frozenset({"type_subject", "member_subject", "alien_subject"})

_IDENTIFIER_FILTER_FIELDS: dict[str, tuple[str, ...]] = {
    "symbol": ("fqn_prefix",),
    "route": ("path_prefix",),
    "client": ("target_service", "target_path_prefix"),
}


def _out_count(edge_summary: dict[str, Any] | None, key: str) -> int:
    if not edge_summary or key not in edge_summary:
        return 0
    cell = edge_summary[key]
    if not isinstance(cell, dict):
        return 0
    return int(cell.get("out", 0) or 0)


def _in_count(edge_summary: dict[str, Any] | None, key: str) -> int:
    if not edge_summary or key not in edge_summary:
        return 0
    cell = edge_summary[key]
    if not isinstance(cell, dict):
        return 0
    return int(cell.get("in", 0) or 0)


def _record_role(rec: dict[str, Any]) -> str:
    return str((rec.get("data") or {}).get("role") or rec.get("role") or "")


def _type_rollup_would_emit(edge_summary: dict[str, Any] | None) -> bool:
    return (
        _out_count(edge_summary, "DECLARES.DECLARES_CLIENT") > 0
        or _out_count(edge_summary, "DECLARES.EXPOSES") > 0
        or _out_count(edge_summary, "DECLARES.DECLARES_PRODUCER") > 0
    )


def _symbol_declaration_kind(record: dict[str, Any]) -> str | None:
    data = record.get("data")
    if isinstance(data, dict):
        k = data.get("kind")
        if k is not None:
            return str(k).strip() or None
    return None


def _subject_node_label(subject_record: dict[str, Any]) -> str:
    if "producer_kind" in subject_record:
        return "Producer"
    if "client_kind" in subject_record:
        return "Client"
    if "framework" in subject_record:
        return "Route"
    return "Symbol"


def _traversal_role_for_wrong_kind(subject_label: str, subject_record: dict[str, Any]) -> str:
    if subject_label == "Symbol":
        sk = str(subject_record.get("kind") or "")
        if sk in _METHOD_SYMBOL_KINDS:
            return "member_subject"
        if sk in _TYPE_SYMBOL_KINDS:
            return "alien_subject"
    return "alien_subject"


def typical_traversal_for(
    edge: str,
    role_key: str,
    *,
    subject_id: str,
    direction: str,
) -> str:
    template = EDGE_SCHEMA[edge].typical_traversals[role_key]
    return template.format(id=subject_id, direction=direction, edge=edge)


def neighbors_empty_hints(
    *,
    subject_record: dict[str, Any],
    requested_edge_types: list[str],
    requested_direction: Literal["in", "out"],
) -> list[tuple[int, str]]:
    """Structural empty-neighbors hints from ``EDGE_SCHEMA`` (at most one row 1–3 per edge)."""
    pairs: list[tuple[int, str]] = []
    subject_label = _subject_node_label(subject_record)
    subject_id = str(subject_record.get("id") or "")

    for edge in requested_edge_types:
        spec = EDGE_SCHEMA.get(edge)
        if spec is None:
            continue

        if subject_label != spec.src and subject_label != spec.dst:
            role = _traversal_role_for_wrong_kind(subject_label, subject_record)
            trav = typical_traversal_for(
                edge, role, subject_id=subject_id, direction=requested_direction,
            )
            pairs.append(
                (
                    PRIORITY_META,
                    TPL_NEIGHBORS_WRONG_SUBJECT_KIND.format(
                        edge=edge,
                        src_kind=spec.src,
                        dst_kind=spec.dst,
                        subject_kind=subject_label,
                        canonical_traversal=trav,
                    ),
                )
            )
            continue

        wrong_direction = spec.src != spec.dst and (
            (requested_direction == "out" and subject_label == spec.dst)
            or (requested_direction == "in" and subject_label == spec.src)
        )
        if wrong_direction:
            correct_dir = "in" if requested_direction == "out" else "out"
            pairs.append(
                (
                    PRIORITY_META,
                    TPL_NEIGHBORS_WRONG_DIRECTION.format(
                        edge=edge,
                        src_kind=spec.src,
                        dst_kind=spec.dst,
                        requested_dir=requested_direction,
                        correct_dir=correct_dir,
                    ),
                )
            )
            continue

        if (
            subject_label == "Symbol"
            and str(subject_record.get("kind") or "") in _TYPE_SYMBOL_KINDS
            and spec.member_only
        ):
            trav = typical_traversal_for(
                edge, "type_subject", subject_id=subject_id, direction=requested_direction,
            )
            pairs.append(
                (
                    PRIORITY_META,
                    TPL_NEIGHBORS_TYPE_LEVEL_REQUERY.format(
                        edge=edge,
                        subject_kind=subject_label,
                        canonical_traversal=trav,
                    ),
                )
            )

    if subject_label in _BROWNFIELD_ABSENCE_SUBJECT_LABELS:
        for edge in requested_edge_types:
            spec = EDGE_SCHEMA.get(edge)
            if spec is not None and spec.brownfield_resolver_sourced:
                pairs.append(
                    (
                        PRIORITY_META,
                        TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED.format(edge=edge),
                    )
                )
                break

    return pairs


def _hint_contains_composed_dotkey(hint: str) -> bool:
    return any(prefix in hint for prefix in _COMPOSED_DOT_KEY_PREFIXES)


def _filter_neighbors_dotkey_hints(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(pri, text) for pri, text in pairs if not _hint_contains_composed_dotkey(text)]


def _neighbors_success_subject_is_type(subject_record: dict[str, Any]) -> bool:
    return (
        _subject_node_label(subject_record) == "Symbol"
        and str(subject_record.get("kind") or "") in _TYPE_SYMBOL_KINDS
    )


def _neighbors_results_homogeneous(
    results: list[dict[str, Any]],
    *,
    endpoint_kind: str | None = None,
    symbol_kinds: frozenset[str] | None = None,
) -> bool:
    if not results:
        return False
    for row in results:
        other = row.get("other")
        if not isinstance(other, dict):
            return False
        ok = str(other.get("kind") or "")
        if endpoint_kind is not None and ok != endpoint_kind:
            return False
        if symbol_kinds is not None:
            if ok != "symbol":
                return False
            if str(other.get("symbol_kind") or "") not in symbol_kinds:
                return False
    return True


def _append_neighbors_success_hint(pairs: list[tuple[int, str]], text: str) -> None:
    # v4 neighbors cap only (describe uses the same N1a/N1b templates without this gate).
    if text and len(text) <= _NEIGHBORS_SUCCESS_MAX_CHARS:
        pairs.append((PRIORITY_LEAF_FOLLOWUP, text))


def neighbors_calls_fanout_hints(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """High-fanout and unresolved-site nudges for CALLS-on-method neighbors (PR-3)."""
    pairs: list[tuple[int, str]] = []
    req_types = payload.get("requested_edge_types")
    if not isinstance(req_types, list) or req_types != ["CALLS"]:
        return pairs
    if payload.get("include_unresolved"):
        return pairs
    page_n = len(list(payload.get("results") or []))
    calls_n = int(payload.get("calls_row_count") or 0) or page_n
    unresolved = int(payload.get("unresolved_count") or 0)
    if not payload.get("edge_filter_provided") and calls_n >= _CALLS_HIGH_FANOUT_THRESHOLD:
        pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_NEIGHBORS_CALLS_HIGH_FANOUT.format(n=calls_n)))
    if unresolved > 0:
        pairs.append(
            (PRIORITY_LEAF_FOLLOWUP, TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED.format(n=page_n, k=unresolved))
        )
    return pairs


def neighbors_calls_meta_hints(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """CALLS-specific hints: role-filter OTHER fallback (Decision 20) and NodeFilter.role trap (30)."""
    pairs: list[tuple[int, str]] = []
    req_types = payload.get("requested_edge_types")
    if not isinstance(req_types, list) or req_types != ["CALLS"]:
        return pairs
    results = list(payload.get("results") or [])
    edge_flt = payload.get("edge_filter") if isinstance(payload.get("edge_filter"), dict) else {}
    node_flt = payload.get("node_filter") if isinstance(payload.get("node_filter"), dict) else {}
    role_exact = edge_flt.get("callee_declaring_role")
    if (
        role_exact in ("SERVICE", "REPOSITORY")
        and not results
        and int(payload.get("unfiltered_calls_count") or 0) >= 5
    ):
        pairs.append((PRIORITY_META, TPL_NEIGHBORS_CALLS_ROLE_FILTER_OTHER_FALLBACK))
    node_role = node_flt.get("role")
    if node_role and results:
        method_rows = [
            r
            for r in results
            if str(((r.get("other") or {}) if isinstance(r.get("other"), dict) else {}).get("symbol_kind") or "")
            == "method"
        ]
        if method_rows:
            other_roles = [
                str(
                    ((r.get("other") or {}) if isinstance(r.get("other"), dict) else {}).get("role")
                    or ""
                )
                for r in method_rows
            ]
            if other_roles and sum(1 for role in other_roles if role == "OTHER") >= max(
                1, (len(other_roles) * 3) // 4
            ):
                pairs.append((PRIORITY_META, TPL_NEIGHBORS_CALLS_NODEFILTER_ROLE_COLLISION))
    return pairs


def neighbors_success_hints(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """v4 non-empty neighbors follow-ups (N1a–N7); no graph I/O."""
    if not payload.get("success"):
        return []
    results = list(payload.get("results") or [])
    if not results or int(payload.get("offset") or 0) != 0:
        return []
    req_types = payload.get("requested_edge_types")
    if not isinstance(req_types, list) or len(req_types) != 1:
        return []
    edge = str(req_types[0]).strip()
    if not edge:
        return []
    direction = payload.get("requested_direction")
    if direction not in ("in", "out"):
        return []

    pairs: list[tuple[int, str]] = []
    origin_id = str(payload.get("origin_id") or "")
    if not origin_id:
        origin_id = str(results[0].get("origin_id") or "")
    subject_record = payload.get("subject_record")
    is_type_subject = (
        isinstance(subject_record, dict) and _neighbors_success_subject_is_type(subject_record)
    )

    if (
        edge == "DECLARES"
        and direction == "out"
        and is_type_subject
        and _neighbors_results_homogeneous(results, symbol_kinds=_METHOD_SYMBOL_KINDS)
    ):
        if origin_id:
            _append_neighbors_success_hint(
                pairs, TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS.format(id=origin_id),
            )
            _append_neighbors_success_hint(
                pairs, TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS.format(id=origin_id),
            )

    if edge in _EDGE_DECLARES_CLIENT and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="client"):
            _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_HTTP_TARGETS)

    if edge in _EDGE_DECLARES_PRODUCER and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="producer"):
            _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_ASYNC_TARGETS)

    if (
        edge == "EXPOSES"
        and direction == "in"
        and _neighbors_results_homogeneous(results, symbol_kinds=_METHOD_SYMBOL_KINDS)
    ):
        _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_CALLERS)

    if edge == "HTTP_CALLS" and direction == "in":
        if _neighbors_results_homogeneous(results, endpoint_kind="client"):
            _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_DECLARING_CLIENT)

    if edge == "ASYNC_CALLS" and direction == "in":
        if _neighbors_results_homogeneous(results, endpoint_kind="producer"):
            _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_DECLARING_PRODUCER)

    if edge == "DECLARES.EXPOSES" and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="route"):
            _append_neighbors_success_hint(pairs, TPL_NEIGHBORS_SUCCESS_HANDLER)

    return pairs


def _find_is_page_full(payload: dict[str, Any], results: list[dict[str, Any]]) -> bool:
    lim = payload.get("limit")
    return (
        lim is not None
        and len(results) >= int(lim)
        and payload.get("has_more_results") is True
    )


def _append_find_success_hint(pairs: list[tuple[int, str]], text: str) -> None:
    if text and len(text) <= _FIND_SUCCESS_MAX_CHARS:
        pairs.append((PRIORITY_LEAF_FOLLOWUP, text))


def find_success_hints(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """v4 non-empty find follow-ups (F1–F3); no graph I/O."""
    if not payload.get("success"):
        return []
    results = list(payload.get("results") or [])
    if not results or _find_is_page_full(payload, results):
        return []
    node_id = str(results[0].get("id") or "")
    if not node_id:
        return []
    kind = str(payload.get("kind") or "")
    pairs: list[tuple[int, str]] = []
    if kind == "route":
        _append_find_success_hint(pairs, TPL_FIND_SUCCESS_HANDLER.format(id=node_id))
    elif kind == "client":
        _append_find_success_hint(pairs, TPL_FIND_SUCCESS_HTTP_TARGETS.format(id=node_id))
    elif kind == "producer":
        _append_find_success_hint(pairs, TPL_FIND_SUCCESS_ASYNC_TARGETS.format(id=node_id))
    return pairs


def _any_fuzzy_strategy(edges: list[dict[str, Any]]) -> bool:
    for e in edges:
        attrs = e.get("attrs") if isinstance(e.get("attrs"), dict) else {}
        s = attrs.get("strategy") if isinstance(attrs, dict) else None
        if isinstance(s, str) and s in FUZZY_STRATEGY_SET:
            return True
    return False


def _find_has_identifier_shaped_filter(kind: str, flt: dict[str, Any]) -> bool:
    for name in _IDENTIFIER_FILTER_FIELDS.get(kind, ()):
        val = flt.get(name)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return True
        if not isinstance(val, str):
            return True
    return False


def finalize_hint_list(scored: list[tuple[int, str]]) -> list[str]:
    """Dedupe identical rendered strings keeping the highest priority; cap to 5 (drop lowest).

    Within the same priority tier, keep hints in emission order (first scored wins the cap).
    """
    best: dict[str, tuple[int, int]] = {}
    for idx, (pri, text) in enumerate(scored):
        if not text:
            continue
        prev = best.get(text)
        if prev is None or pri > prev[0]:
            best[text] = (pri, idx)
        elif pri == prev[0]:
            best[text] = (pri, min(prev[1], idx))
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    return [text for text, _pri in ordered[:5]]


def _neighbors_empty_structured_hints(
    *,
    subject_record: dict[str, Any],
    requested_edge_types: list[str],
    requested_direction: Literal["in", "out"],
) -> list[_StructuredHint]:
    """Structured counterparts to ``neighbors_empty_hints`` (Rows 1–3)."""
    out: list[_StructuredHint] = []
    subject_label = _subject_node_label(subject_record)
    subject_id = str(subject_record.get("id") or "")

    for edge in requested_edge_types:
        spec = EDGE_SCHEMA.get(edge)
        if spec is None:
            continue

        # Row 1: wrong subject kind
        if subject_label != spec.src and subject_label != spec.dst:
            role = _traversal_role_for_wrong_kind(subject_label, subject_record)
            if role != "alien_subject":
                template = spec.typical_traversals.get(role, "")
                parsed = _parse_first_traversal(template)
                if parsed:
                    direction, edge_types = parsed
                    out.append(_StructuredHint(
                        "neighbors",
                        {"ids": [subject_id], "direction": direction, "edge_types": edge_types},
                        False,
                        PRIORITY_META,
                        LABEL_WRONG_SUBJECT_KIND,
                    ))
            continue

        # Row 2: wrong direction
        wrong_direction = spec.src != spec.dst and (
            (requested_direction == "out" and subject_label == spec.dst)
            or (requested_direction == "in" and subject_label == spec.src)
        )
        if wrong_direction:
            correct_dir = "in" if requested_direction == "out" else "out"
            out.append(_StructuredHint(
                "neighbors",
                {"ids": [subject_id], "direction": correct_dir, "edge_types": [edge]},
                False,
                PRIORITY_META,
                LABEL_WRONG_DIRECTION,
            ))
            continue

        # Row 3: type-level requery
        if (
            subject_label == "Symbol"
            and str(subject_record.get("kind") or "") in _TYPE_SYMBOL_KINDS
            and spec.member_only
        ):
            dot_key = _MEMBER_ONLY_DOT_KEY.get(edge)
            if dot_key:
                out.append(_StructuredHint(
                    "neighbors",
                    {"ids": [subject_id], "direction": requested_direction, "edge_types": [dot_key]},
                    False,
                    PRIORITY_META,
                    LABEL_TYPE_LEVEL_REQUERY,
                ))
            else:
                template = spec.typical_traversals.get("type_subject", "")
                parsed = _parse_first_traversal(template)
                if parsed:
                    direction, edge_types = parsed
                    out.append(_StructuredHint(
                        "neighbors",
                        {"ids": [subject_id], "direction": direction, "edge_types": edge_types},
                        False,
                        PRIORITY_META,
                        LABEL_TYPE_LEVEL_REQUERY,
                    ))
    return out


def _neighbors_success_structured_hints(payload: dict[str, Any]) -> list[_StructuredHint]:
    """Structured counterparts to ``neighbors_success_hints`` (N1a–N7)."""
    if not payload.get("success"):
        return []
    results = list(payload.get("results") or [])
    if not results or int(payload.get("offset") or 0) != 0:
        return []
    req_types = payload.get("requested_edge_types")
    if not isinstance(req_types, list) or len(req_types) != 1:
        return []
    edge = str(req_types[0]).strip()
    if not edge:
        return []
    direction = payload.get("requested_direction")
    if direction not in ("in", "out"):
        return []

    out: list[_StructuredHint] = []
    origin_id = str(payload.get("origin_id") or "")
    if not origin_id:
        origin_id = str(results[0].get("origin_id") or "")
    subject_record = payload.get("subject_record")
    is_type_subject = (
        isinstance(subject_record, dict) and _neighbors_success_subject_is_type(subject_record)
    )

    # N1a/N1b: DECLARES out from type → dot-key clients/routes
    if (
        edge == "DECLARES"
        and direction == "out"
        and is_type_subject
        and _neighbors_results_homogeneous(results, symbol_kinds=_METHOD_SYMBOL_KINDS)
    ):
        if origin_id:
            n1a = TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS.format(id=origin_id)
            n1b = TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS.format(id=origin_id)
            if len(n1a) <= _NEIGHBORS_SUCCESS_MAX_CHARS:
                out.append(_StructuredHint(
                    "neighbors",
                    {"ids": [origin_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]},
                    True,
                    PRIORITY_LEAF_FOLLOWUP,
                    LABEL_CLIENTS_VIA_MEMBERS,
                ))
            if len(n1b) <= _NEIGHBORS_SUCCESS_MAX_CHARS:
                out.append(_StructuredHint(
                    "neighbors",
                    {"ids": [origin_id], "direction": "out", "edge_types": ["DECLARES.EXPOSES"]},
                    True,
                    PRIORITY_LEAF_FOLLOWUP,
                    LABEL_ROUTES_VIA_MEMBERS,
                ))

    # N2: DECLARES_CLIENT / DECLARES.DECLARES_CLIENT out → HTTP_CALLS
    if edge in _EDGE_DECLARES_CLIENT and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="client"):
            other_ids = _extract_other_ids(results)
            out.append(_StructuredHint(
                "neighbors",
                {"ids": other_ids, "direction": "out", "edge_types": ["HTTP_CALLS"]},
                bool(other_ids),
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_HTTP_TARGETS,
            ))

    # N3: DECLARES_PRODUCER / DECLARES.DECLARES_PRODUCER out → ASYNC_CALLS
    if edge in _EDGE_DECLARES_PRODUCER and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="producer"):
            other_ids = _extract_other_ids(results)
            out.append(_StructuredHint(
                "neighbors",
                {"ids": other_ids, "direction": "out", "edge_types": ["ASYNC_CALLS"]},
                bool(other_ids),
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_ASYNC_TARGETS,
            ))

    # N4: EXPOSES in → CALLS (callers)
    if (
        edge == "EXPOSES"
        and direction == "in"
        and _neighbors_results_homogeneous(results, symbol_kinds=_METHOD_SYMBOL_KINDS)
    ):
        other_ids = _extract_other_ids(results)
        out.append(_StructuredHint(
            "neighbors",
            {"ids": other_ids, "direction": "in", "edge_types": ["CALLS"]},
            bool(other_ids),
            PRIORITY_LEAF_FOLLOWUP,
            LABEL_CALLERS,
        ))

    # N5: HTTP_CALLS in → DECLARES_CLIENT
    if edge == "HTTP_CALLS" and direction == "in":
        if _neighbors_results_homogeneous(results, endpoint_kind="client"):
            other_ids = _extract_other_ids(results)
            out.append(_StructuredHint(
                "neighbors",
                {"ids": other_ids, "direction": "in", "edge_types": ["DECLARES_CLIENT"]},
                bool(other_ids),
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))

    # N6: ASYNC_CALLS in → DECLARES_PRODUCER
    if edge == "ASYNC_CALLS" and direction == "in":
        if _neighbors_results_homogeneous(results, endpoint_kind="producer"):
            other_ids = _extract_other_ids(results)
            out.append(_StructuredHint(
                "neighbors",
                {"ids": other_ids, "direction": "in", "edge_types": ["DECLARES_PRODUCER"]},
                bool(other_ids),
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))

    # N7: DECLARES.EXPOSES out → EXPOSES (handler)
    if edge == "DECLARES.EXPOSES" and direction == "out":
        if _neighbors_results_homogeneous(results, endpoint_kind="route"):
            other_ids = _extract_other_ids(results)
            out.append(_StructuredHint(
                "neighbors",
                {"ids": other_ids, "direction": "in", "edge_types": ["EXPOSES"]},
                bool(other_ids),
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_HANDLER,
            ))

    return out


def generate_hints(
    output_kind: Literal["search", "find", "describe", "neighbors", "resolve"],
    payload: dict[str, Any],
) -> tuple[list[str], list[_StructuredHint]]:
    """Return up to 5 road-sign hint strings and structured hints for a success-only MCP v2 payload dict.

    For ``search`` / ``find`` / ``describe`` / ``neighbors``, callers must pass
    ``success: True``; this function returns ``[]`` when ``success`` is false or
    missing. The ``resolve`` branch is **status-driven** (``status``,
    ``resolved_identifier``, ``candidates``, optional seeds) and does not require
    ``success`` in the payload; an explicit ``success: False`` still suppresses
    hints (defense in depth).
    """
    pairs: list[tuple[int, str]] = []
    struct_pairs: list[_StructuredHint] = []

    if output_kind == "resolve":
        if payload.get("success") is False:
            return ([], [])
        status = str(payload.get("status") or "")
        if status == "one":
            return ([], [])
        if status == "many":
            n = len(payload.get("candidates") or [])
            if n > 1:
                pairs.append((PRIORITY_META, TPL_RESOLVE_MANY_TIGHTEN.format(n=n)))
                struct_pairs.append(_StructuredHint(
                    "resolve",
                    {"identifier": str(payload.get("resolved_identifier") or ""), "hint_kind": str(payload.get("hint_kind") or "")},
                    False, PRIORITY_META,
                    LABEL_TIGHTEN_IDENTIFIER,
                ))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
        if status == "none":
            identifier = payload.get("resolved_identifier")
            hint_kind = payload.get("hint_kind")
            if not isinstance(identifier, str) or not identifier.strip():
                return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
            if any(w in identifier for w in _RESOLVE_WILDCARDS):
                return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
            rendered: str | None = None
            if hint_kind == "route":
                seed = payload.get("path_prefix_seed")
                if isinstance(seed, str) and seed.strip():
                    rendered = TPL_RESOLVE_NONE_TRY_FIND_ROUTE.format(seed=seed)
                    struct_pairs.append(_StructuredHint(
                        "find", {"kind": "route", "filter": {"path_prefix": seed}}, True, PRIORITY_META,
                        LABEL_TRY_FIND_ROUTE,
                    ))
            elif hint_kind == "client":
                seed = payload.get("target_service_seed")
                if isinstance(seed, str) and seed.strip():
                    rendered = TPL_RESOLVE_NONE_TRY_FIND_CLIENT.format(seed=seed)
                    struct_pairs.append(_StructuredHint(
                        "find", {"kind": "client", "filter": {"target_service": seed}}, True, PRIORITY_META,
                        LABEL_TRY_FIND_CLIENT,
                    ))
            else:
                rendered = TPL_RESOLVE_NONE_TRY_SEARCH.format(identifier=identifier)
                struct_pairs.append(_StructuredHint(
                    "search", {"query": identifier}, True, PRIORITY_META,
                    LABEL_TRY_SEARCH,
                ))
            if rendered is not None and len(rendered) <= _RESOLVE_HINT_MAX_CHARS:
                pairs.append((PRIORITY_META, rendered))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
        return ([], [])

    if not payload.get("success"):
        return ([], [])

    if output_kind == "search":
        results: list[dict[str, Any]] = list(payload.get("results") or [])
        lim = payload.get("limit")
        if lim is not None and len(results) == int(lim) and results:
            scores = [float(r.get("score", 0.0) or 0.0) for r in results]
            mx = max(scores)
            mn = min(scores)
            if mx > 0.0 and (mx - mn) < 0.1 * mx:
                pairs.append((PRIORITY_META, TPL_SEARCH_WEAK))
                struct_pairs.append(_StructuredHint(
                    "find", {"kind": "symbol", "filter": {"role": "SERVICE"}}, False, PRIORITY_META,
                    LABEL_WEAK_RESULTS,
                ))
        return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

    if output_kind == "find":
        kind = str(payload.get("kind") or "")
        results = list(payload.get("results") or [])
        flt = payload.get("filter") if isinstance(payload.get("filter"), dict) else {}
        lim = payload.get("limit")
        if not results and _find_has_identifier_shaped_filter(kind, flt):
            pairs.append((PRIORITY_META, TPL_FIND_EMPTY_RESOLVE.format(kind=kind)))
            identifier = ""
            for fname in _IDENTIFIER_FILTER_FIELDS.get(kind, ()):
                val = flt.get(fname)
                if isinstance(val, str) and val.strip():
                    identifier = val.strip()
                    break
            struct_pairs.append(_StructuredHint(
                "resolve", {"identifier": identifier, "hint_kind": kind}, True, PRIORITY_META,
                LABEL_TRY_RESOLVE,
            ))
        if _find_is_page_full(payload, results) and lim is not None:
            pairs.append((PRIORITY_META, TPL_FIND_PAGE_FULL.format(limit=int(lim))))
            struct_pairs.append(_StructuredHint(
                "find", {"kind": kind, "filter": flt, "limit": int(lim)}, False, PRIORITY_META,
                LABEL_PAGE_FULL,
            ))
        pairs.extend(find_success_hints(payload))
        if results and not _find_is_page_full(payload, results):
            node_id = str(results[0].get("id") or "")
            if node_id:
                if kind == "route":
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["EXPOSES"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_HANDLER,
                    ))
                elif kind == "client":
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["HTTP_CALLS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_HTTP_TARGETS,
                    ))
                elif kind == "producer":
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_ASYNC_TARGETS,
                    ))
        return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

    if output_kind == "neighbors":
        results = list(payload.get("results") or [])
        req_types = payload.get("requested_edge_types")
        if not isinstance(req_types, list):
            req_types = []
        edge_labels = [str(x).strip() for x in req_types if str(x).strip()]
        offset = int(payload.get("offset") or 0)
        empty_pairs: list[tuple[int, str]] = []
        success_pairs: list[tuple[int, str]] = []
        meta_pairs: list[tuple[int, str]] = []
        struct_empty: list[_StructuredHint] = []
        struct_success: list[_StructuredHint] = []
        struct_meta: list[_StructuredHint] = []
        if not results and edge_labels and offset == 0:
            subject_record = payload.get("subject_record")
            requested_direction = payload.get("requested_direction")
            if (
                isinstance(subject_record, dict)
                and subject_record
                and requested_direction in ("in", "out")
            ):
                empty_pairs.extend(
                    neighbors_empty_hints(
                        subject_record=subject_record,
                        requested_edge_types=edge_labels,
                        requested_direction=requested_direction,
                    )
                )
                struct_empty.extend(
                    _neighbors_empty_structured_hints(
                        subject_record=subject_record,
                        requested_edge_types=edge_labels,
                        requested_direction=requested_direction,
                    )
                )
        elif results and offset == 0:
            success_pairs = neighbors_success_hints(payload)
            struct_success = _neighbors_success_structured_hints(payload)
        meta_pairs.extend(neighbors_calls_meta_hints(payload))
        meta_pairs.extend(neighbors_calls_fanout_hints(payload))
        # Structured meta hints for CALLS fanout
        if isinstance(req_types, list) and req_types == ["CALLS"]:
            if not payload.get("edge_filter_provided") and not payload.get("include_unresolved"):
                calls_n = int(payload.get("calls_row_count") or 0) or len(results)
                if calls_n >= _CALLS_HIGH_FANOUT_THRESHOLD:
                    origin_id = str(payload.get("origin_id") or "")
                    if origin_id:
                        struct_meta.append(_StructuredHint(
                            "neighbors",
                            {"ids": [origin_id], "direction": "out", "edge_types": ["CALLS"], "edge_filter": {}},
                            False, PRIORITY_LEAF_FOLLOWUP,
                            LABEL_HIGH_FANOUT,
                        ))
        if results and _any_fuzzy_strategy(results):
            meta_pairs.append((PRIORITY_META, TPL_NEIGHBORS_FUZZY_STRATEGY))
            struct_meta.append(_StructuredHint("neighbors", {}, False, PRIORITY_META, LABEL_FUZZY_STRATEGY))
        return (
            finalize_hint_list(
                _filter_neighbors_dotkey_hints(empty_pairs) + success_pairs + meta_pairs,
            ),
            finalize_structured_hints(struct_empty + struct_success + struct_meta),
        )

    if output_kind == "describe":
        rec = payload.get("record")
        if not isinstance(rec, dict):
            return ([], [])
        node_id = str(rec.get("id") or "")
        if not node_id:
            return ([], [])
        kind = str(rec.get("kind") or "")
        es = rec.get("edge_summary")
        edge_summary = es if isinstance(es, dict) else None

        if kind == "route":
            pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_ROUTE_DECLARING.format(id=node_id)))
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["EXPOSES"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
        if kind == "client":
            pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_CLIENT_DECLARING.format(id=node_id)))
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["DECLARES_CLIENT"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            if _out_count(edge_summary, "HTTP_CALLS") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_FIND_SUCCESS_HTTP_TARGETS.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["HTTP_CALLS"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_HTTP_TARGETS,
                ))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
        if kind == "producer":
            pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_PRODUCER_DECLARING.format(id=node_id)))
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["DECLARES_PRODUCER"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            if _out_count(edge_summary, "ASYNC_CALLS") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_FIND_SUCCESS_ASYNC_TARGETS.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_ASYNC_TARGETS,
                ))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

        if kind != "symbol":
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

        decl_kind = _symbol_declaration_kind(rec)
        is_type = decl_kind in _TYPE_SYMBOL_KINDS
        is_method = decl_kind in _METHOD_SYMBOL_KINDS

        if is_type:
            if _out_count(edge_summary, "DECLARES.DECLARES_CLIENT") > 0:
                pairs.append(
                    (PRIORITY_DECLARES_TYPE_ROLLUP, TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_CLIENTS_VIA_MEMBERS,
                ))
            if _out_count(edge_summary, "DECLARES.EXPOSES") > 0:
                pairs.append(
                    (PRIORITY_DECLARES_TYPE_ROLLUP, TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.EXPOSES"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_ROUTES_VIA_MEMBERS,
                ))
            if _out_count(edge_summary, "DECLARES.DECLARES_PRODUCER") > 0:
                pairs.append(
                    (PRIORITY_DECLARES_TYPE_ROLLUP, TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_PRODUCER"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_PRODUCERS_VIA_MEMBERS,
                ))

            if not _type_rollup_would_emit(edge_summary):
                if decl_kind == "interface" and _in_count(edge_summary, "IMPLEMENTS") > 0:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_TYPE_IMPLEMENTORS.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["IMPLEMENTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_IMPLEMENTORS,
                    ))
                if decl_kind == "class" and _out_count(edge_summary, "IMPLEMENTS") > 0:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_TYPE_IMPLEMENTS.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["IMPLEMENTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_IMPLEMENTS,
                    ))
                if decl_kind == "class" and _record_role(rec) == "SERVICE" and _out_count(edge_summary, "INJECTS") > 0:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_TYPE_DEPENDENCIES.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["INJECTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_DEPENDENCIES,
                    ))
                if decl_kind in {"interface", "class"} and _in_count(edge_summary, "INJECTS") > 0:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_TYPE_INJECTORS.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["INJECTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_INJECTORS,
                    ))

            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

        if is_method:
            if _out_count(edge_summary, "OVERRIDDEN_BY") > 0:
                pairs.append((PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_OVERRIDERS.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.DECLARES_CLIENT") > 0:
                pairs.append(
                    (PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_CLIENT"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_CLIENTS_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.DECLARES_PRODUCER") > 0:
                pairs.append(
                    (PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_PRODUCERS_IN_OVERRIDERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_PRODUCER"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_PRODUCERS_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.EXPOSES") > 0:
                pairs.append(
                    (PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_ROUTES_IN_OVERRIDERS.format(id=node_id))
                )
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.EXPOSES"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_ROUTES_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "DECLARES_CLIENT") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES_CLIENT"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_OUTBOUND_CLIENT,
                ))
            if _out_count(edge_summary, "DECLARES_PRODUCER") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_OUTBOUND_PRODUCER.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES_PRODUCER"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_OUTBOUND_PRODUCER,
                ))
            if _out_count(edge_summary, "EXPOSES") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_INBOUND_ROUTE.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["EXPOSES"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_INBOUND_ROUTE,
                ))
            calls_out = _out_count(edge_summary, "CALLS")
            if 1 <= calls_out <= 9:
                method_role = _record_role(rec)
                if method_role != "OTHER" or calls_out >= 3:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_OUTBOUND_CALLS.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_OUTBOUND_CALLS,
                    ))
            if _out_count(edge_summary, "OVERRIDES") > 0:
                if _out_count(edge_summary, "OVERRIDDEN_BY") == 0:
                    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_SUPER_DECL.format(id=node_id)))
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDES"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_SUPER_DECLARATION,
                    ))
            data = rec.get("data")
            unresolved = 0
            if isinstance(data, dict):
                unresolved = int(data.get("unresolved_call_sites_total") or 0)
            if unresolved > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_UNRESOLVED.format(id=node_id)))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"], "include_unresolved": True},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_UNRESOLVED,
                ))
            if _out_count(edge_summary, "CALLS") >= 10:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_MANY_CALLS))
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]},
                    False, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_HIGH_FANOUT,
                ))
            return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

        return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))

    return ([], [])
