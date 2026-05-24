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

MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION = (
    "Machine-parseable next-action objects. Each element has "
    "label (short semantic name, e.g. 'routes via members', 'implementors'), "
    "tool (MCP tool name), args (ready-to-use parameters), actionable "
    "(True = direct call with complete args; False = advisory/partial — agent "
    "fills missing values or uses as guidance), and reason (human-readable "
    "advisory text, empty for actionable hints). Maximum 5 per output."
)

# --- Internal structured hint representation (no mcp_v2 import) ---


class _StructuredHint(NamedTuple):
    tool: str
    args: dict[str, Any]
    actionable: bool
    priority: int
    label: str = ""
    reason: str = ""


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

_FIRST_TRAVERSAL_RE = re.compile(
    r"neighbors\(\[.*?\],'(in|out)',\['([^']+)'\]\)"
)


def _parse_first_traversal(template: str) -> tuple[str, list[str]] | None:
    m = _FIRST_TRAVERSAL_RE.search(template)
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


_RESOLVE_WILDCARDS = ("*", "?")
_CALLS_HIGH_FANOUT_THRESHOLD = 10
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


def _find_is_page_full(payload: dict[str, Any], results: list[dict[str, Any]]) -> bool:
    lim = payload.get("limit")
    return (
        lim is not None
        and len(results) >= int(lim)
        and payload.get("has_more_results") is True
    )


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


def _neighbors_empty_structured_hints(
    *,
    subject_record: dict[str, Any],
    requested_edge_types: list[str],
    requested_direction: Literal["in", "out"],
) -> list[_StructuredHint]:
    """Structured empty-neighbors hints from ``EDGE_SCHEMA`` (Rows 1–3)."""
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
                        reason=f"'{edge}' connects {spec.src} → {spec.dst}; this is a {subject_label}",
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
                reason=f"'{edge}' is {spec.src} → {spec.dst}; try direction='{correct_dir}'",
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
                    reason=f"'{edge}' lives on methods, not on {subject_label}; try dot-key traversal",
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
                        reason=f"'{edge}' lives on methods, not on {subject_label}",
                    ))
    return out


def _neighbors_success_structured_hints(payload: dict[str, Any]) -> list[_StructuredHint]:
    """Structured non-empty neighbors follow-ups (N1a–N7)."""
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
            out.append(_StructuredHint(
                "neighbors",
                {"ids": [origin_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]},
                True,
                PRIORITY_LEAF_FOLLOWUP,
                LABEL_CLIENTS_VIA_MEMBERS,
            ))
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
) -> list[_StructuredHint]:
    """Return up to 5 structured hints for a success-only MCP v2 payload dict.

    For ``search`` / ``find`` / ``describe`` / ``neighbors``, callers must pass
    ``success: True``; this function returns ``[]`` when ``success`` is false or
    missing. The ``resolve`` branch is **status-driven** (``status``,
    ``resolved_identifier``, ``candidates``, optional seeds) and does not require
    ``success`` in the payload; an explicit ``success: False`` still suppresses
    hints (defense in depth).
    """
    struct_pairs: list[_StructuredHint] = []

    if output_kind == "resolve":
        if payload.get("success") is False:
            return []
        status = str(payload.get("status") or "")
        if status == "one":
            return []
        if status == "many":
            n = len(payload.get("candidates") or [])
            if n > 1:
                struct_pairs.append(_StructuredHint(
                    "resolve",
                    {"identifier": str(payload.get("resolved_identifier") or ""), "hint_kind": str(payload.get("hint_kind") or "")},
                    False, PRIORITY_META,
                    LABEL_TIGHTEN_IDENTIFIER,
                    reason=f"{n} candidates — tighten identifier or pick a candidate by id",
                ))
            return finalize_structured_hints(struct_pairs)
        if status == "none":
            identifier = payload.get("resolved_identifier")
            hint_kind = payload.get("hint_kind")
            if not isinstance(identifier, str) or not identifier.strip():
                return []
            if any(w in identifier for w in _RESOLVE_WILDCARDS):
                return []
            if hint_kind == "route":
                seed = payload.get("path_prefix_seed")
                if isinstance(seed, str) and seed.strip():
                    struct_pairs.append(_StructuredHint(
                        "find", {"kind": "route", "filter": {"path_prefix": seed}}, True, PRIORITY_META,
                        LABEL_TRY_FIND_ROUTE,
                    ))
            elif hint_kind == "client":
                seed = payload.get("target_service_seed")
                if isinstance(seed, str) and seed.strip():
                    struct_pairs.append(_StructuredHint(
                        "find", {"kind": "client", "filter": {"target_service": seed}}, True, PRIORITY_META,
                        LABEL_TRY_FIND_CLIENT,
                    ))
            else:
                struct_pairs.append(_StructuredHint(
                    "search", {"query": identifier}, True, PRIORITY_META,
                    LABEL_TRY_SEARCH,
                ))
            return finalize_structured_hints(struct_pairs)
        return []

    if not payload.get("success"):
        return []

    if output_kind == "search":
        results: list[dict[str, Any]] = list(payload.get("results") or [])
        lim = payload.get("limit")
        if lim is not None and len(results) == int(lim) and results:
            scores = [float(r.get("score", 0.0) or 0.0) for r in results]
            mx = max(scores)
            mn = min(scores)
            if mx > 0.0 and (mx - mn) < 0.1 * mx:
                struct_pairs.append(_StructuredHint(
                    "find", {"kind": "symbol", "filter": {"role": "SERVICE"}}, False, PRIORITY_META,
                    LABEL_WEAK_RESULTS,
                    reason="results look weak — narrow the query or try find with a role filter",
                ))
        return finalize_structured_hints(struct_pairs)

    if output_kind == "find":
        kind = str(payload.get("kind") or "")
        results = list(payload.get("results") or [])
        flt = payload.get("filter") if isinstance(payload.get("filter"), dict) else {}
        lim = payload.get("limit")
        if not results and _find_has_identifier_shaped_filter(kind, flt):
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
            struct_pairs.append(_StructuredHint(
                "find", {"kind": kind, "filter": flt, "limit": int(lim)}, False, PRIORITY_META,
                LABEL_PAGE_FULL,
                reason=f"result page full at {int(lim)} — narrow filter or paginate",
            ))
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
        return finalize_structured_hints(struct_pairs)

    if output_kind == "neighbors":
        results = list(payload.get("results") or [])
        req_types = payload.get("requested_edge_types")
        if not isinstance(req_types, list):
            req_types = []
        edge_labels = [str(x).strip() for x in req_types if str(x).strip()]
        offset = int(payload.get("offset") or 0)
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
                struct_empty.extend(
                    _neighbors_empty_structured_hints(
                        subject_record=subject_record,
                        requested_edge_types=edge_labels,
                        requested_direction=requested_direction,
                    )
                )
        elif results and offset == 0:
            struct_success = _neighbors_success_structured_hints(payload)

        # CALLS meta hints
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
                            reason=f"{calls_n} CALLS on this method; try edge_filter to narrow",
                        ))
        if results and _any_fuzzy_strategy(results):
            struct_meta.append(_StructuredHint(
                "neighbors", {}, False, PRIORITY_META, LABEL_FUZZY_STRATEGY,
                reason="some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row",
            ))
        return finalize_structured_hints(struct_empty + struct_success + struct_meta)

    if output_kind == "describe":
        rec = payload.get("record")
        if not isinstance(rec, dict):
            return []
        node_id = str(rec.get("id") or "")
        if not node_id:
            return []
        kind = str(rec.get("kind") or "")
        es = rec.get("edge_summary")
        edge_summary = es if isinstance(es, dict) else None

        if kind == "route":
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["EXPOSES"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            return finalize_structured_hints(struct_pairs)
        if kind == "client":
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["DECLARES_CLIENT"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            if _out_count(edge_summary, "HTTP_CALLS") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["HTTP_CALLS"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_HTTP_TARGETS,
                ))
            return finalize_structured_hints(struct_pairs)
        if kind == "producer":
            struct_pairs.append(_StructuredHint(
                "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["DECLARES_PRODUCER"]},
                True, PRIORITY_LEAF_FOLLOWUP,
                LABEL_DECLARING_METHOD,
            ))
            if _out_count(edge_summary, "ASYNC_CALLS") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_ASYNC_TARGETS,
                ))
            return finalize_structured_hints(struct_pairs)

        if kind != "symbol":
            return finalize_structured_hints(struct_pairs)

        decl_kind = _symbol_declaration_kind(rec)
        is_type = decl_kind in _TYPE_SYMBOL_KINDS
        is_method = decl_kind in _METHOD_SYMBOL_KINDS

        if is_type:
            if _out_count(edge_summary, "DECLARES.DECLARES_CLIENT") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_CLIENTS_VIA_MEMBERS,
                ))
            if _out_count(edge_summary, "DECLARES.EXPOSES") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.EXPOSES"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_ROUTES_VIA_MEMBERS,
                ))
            if _out_count(edge_summary, "DECLARES.DECLARES_PRODUCER") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_PRODUCER"]},
                    True, PRIORITY_DECLARES_TYPE_ROLLUP,
                    LABEL_PRODUCERS_VIA_MEMBERS,
                ))

            if not _type_rollup_would_emit(edge_summary):
                if decl_kind == "interface" and _in_count(edge_summary, "IMPLEMENTS") > 0:
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["IMPLEMENTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_IMPLEMENTORS,
                    ))
                if decl_kind == "class" and _out_count(edge_summary, "IMPLEMENTS") > 0:
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["IMPLEMENTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_IMPLEMENTS,
                    ))
                if decl_kind == "class" and _record_role(rec) == "SERVICE" and _out_count(edge_summary, "INJECTS") > 0:
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["INJECTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_DEPENDENCIES,
                    ))
                if decl_kind in {"interface", "class"} and _in_count(edge_summary, "INJECTS") > 0:
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "in", "edge_types": ["INJECTS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_INJECTORS,
                    ))

            return finalize_structured_hints(struct_pairs)

        if is_method:
            if _out_count(edge_summary, "OVERRIDDEN_BY") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.DECLARES_CLIENT") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_CLIENT"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_CLIENTS_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.DECLARES_PRODUCER") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_PRODUCER"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_PRODUCERS_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "OVERRIDDEN_BY.EXPOSES") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY.EXPOSES"]},
                    True, PRIORITY_OVERRIDDEN_AXIS,
                    LABEL_ROUTES_IN_OVERRIDERS,
                ))
            if _out_count(edge_summary, "DECLARES_CLIENT") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES_CLIENT"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_OUTBOUND_CLIENT,
                ))
            if _out_count(edge_summary, "DECLARES_PRODUCER") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["DECLARES_PRODUCER"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_OUTBOUND_PRODUCER,
                ))
            if _out_count(edge_summary, "EXPOSES") > 0:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["EXPOSES"]},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_INBOUND_ROUTE,
                ))
            calls_out = _out_count(edge_summary, "CALLS")
            if 1 <= calls_out <= 9:
                method_role = _record_role(rec)
                if method_role != "OTHER" or calls_out >= 3:
                    struct_pairs.append(_StructuredHint(
                        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]},
                        True, PRIORITY_LEAF_FOLLOWUP,
                        LABEL_OUTBOUND_CALLS,
                    ))
            if _out_count(edge_summary, "OVERRIDES") > 0:
                if _out_count(edge_summary, "OVERRIDDEN_BY") == 0:
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
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"], "include_unresolved": True},
                    True, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_UNRESOLVED,
                ))
            if _out_count(edge_summary, "CALLS") >= 10:
                struct_pairs.append(_StructuredHint(
                    "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]},
                    False, PRIORITY_LEAF_FOLLOWUP,
                    LABEL_HIGH_FANOUT,
                    reason="many CALLS — consider filtering by target microservice",
                ))
            return finalize_structured_hints(struct_pairs)

        return finalize_structured_hints(struct_pairs)

    return []
