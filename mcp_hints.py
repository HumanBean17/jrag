"""Pure MCP v2 road-sign hint generation (no graph I/O, no search, no LLM).

Locked v1 catalog: ``propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`` Appendix A.
Priority cap: same propose §7.12 / ``plans/completed/PLAN-HINTS.md`` principles.
"""

from __future__ import annotations

from typing import Any, Literal

# Normative schema description (propose §3.1) — imported by ``mcp_v2`` for Field(description=...).
MCP_HINTS_FIELD_DESCRIPTION = (
    "Road-sign hints pointing to likely next calls. Each hint is a short string "
    "referencing one MCP V2 tool call. Hints are advisory and may be safely ignored. "
    "Maximum 5 hints per output. Hints never recommend dot-key edge labels (composed "
    "rollups) as neighbors() arguments."
)

# --- Appendix A verbatim templates (substitute {id}, {kind}, {limit}) ---

TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS = (
    "clients via members: neighbors(['{id}'],'out',['DECLARES']) "
    "then neighbors(member_ids,'out',['DECLARES_CLIENT'])"
)
TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS = (
    "routes via members: neighbors(['{id}'],'out',['DECLARES']) "
    "then neighbors(member_ids,'out',['EXPOSES'])"
)
TPL_DESCRIBE_METHOD_OVERRIDERS = "overriders: neighbors(['{id}'],'in',['OVERRIDES'])"
TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS = (
    "clients in overriders: neighbors(['{id}'],'in',['OVERRIDES']) "
    "then neighbors(overrider_ids,'out',['DECLARES_CLIENT'])"
)
TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT = "outbound client: neighbors(['{id}'],'out',['DECLARES_CLIENT'])"
TPL_DESCRIBE_METHOD_INBOUND_ROUTE = "inbound route: neighbors(['{id}'],'out',['EXPOSES'])"
TPL_DESCRIBE_METHOD_MANY_CALLS = "many CALLS — consider filtering by target microservice"
TPL_DESCRIBE_ROUTE_DECLARING = "declaring method: neighbors(['{id}'],'in',['EXPOSES'])"
TPL_DESCRIBE_CLIENT_DECLARING = "declaring method: neighbors(['{id}'],'in',['DECLARES_CLIENT'])"

TPL_FIND_EMPTY_RESOLVE = "no matches — try resolve(identifier, hint_kind='{kind}') for canonical lookup"
TPL_FIND_PAGE_FULL = "result page full at {limit} — narrow filter or paginate"

TPL_NEIGHBORS_EMPTY_KIND_CHECK = (
    "0 results — check if the requested edge_types apply to this kind"
)

TPL_SEARCH_WEAK = "results look weak — narrow the query or try find(role=…)"

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


def _symbol_declaration_kind(record: dict[str, Any]) -> str | None:
    data = record.get("data")
    if isinstance(data, dict):
        k = data.get("kind")
        if k is not None:
            return str(k).strip() or None
    return None


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


def generate_hints(
    output_kind: Literal["search", "find", "describe", "neighbors"],
    payload: dict[str, Any],
) -> list[str]:
    """Return up to 5 road-sign hint strings for a success-only MCP v2 payload dict.

    Callers must pass ``success: True`` payloads only for hint rows; this function
    returns ``[]`` when ``success`` is false or missing.
    """
    if not payload.get("success"):
        return []

    pairs: list[tuple[int, str]] = []

    if output_kind == "search":
        results: list[dict[str, Any]] = list(payload.get("results") or [])
        lim = payload.get("limit")
        if lim is not None and len(results) == int(lim) and results:
            scores = [float(r.get("score", 0.0) or 0.0) for r in results]
            mx = max(scores)
            mn = min(scores)
            if mx > 0.0 and (mx - mn) < 0.1 * mx:
                pairs.append((PRIORITY_META, TPL_SEARCH_WEAK))
        return finalize_hint_list(pairs)

    if output_kind == "find":
        kind = str(payload.get("kind") or "")
        results = list(payload.get("results") or [])
        flt = payload.get("filter") if isinstance(payload.get("filter"), dict) else {}
        lim = payload.get("limit")
        if not results and _find_has_identifier_shaped_filter(kind, flt):
            pairs.append((PRIORITY_META, TPL_FIND_EMPTY_RESOLVE.format(kind=kind)))
        if (
            lim is not None
            and len(results) >= int(lim)
            and payload.get("has_more_results") is True
        ):
            pairs.append((PRIORITY_META, TPL_FIND_PAGE_FULL.format(limit=int(lim))))
        return finalize_hint_list(pairs)

    if output_kind == "neighbors":
        results = list(payload.get("results") or [])
        req_types = payload.get("requested_edge_types")
        if not isinstance(req_types, list):
            req_types = []
        n_types = len([x for x in req_types if str(x).strip()])
        if not results and n_types > 0:
            pairs.append((PRIORITY_META, TPL_NEIGHBORS_EMPTY_KIND_CHECK))
        return finalize_hint_list(pairs)

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
            pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_ROUTE_DECLARING.format(id=node_id)))
            return finalize_hint_list(pairs)
        if kind == "client":
            pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_CLIENT_DECLARING.format(id=node_id)))
            return finalize_hint_list(pairs)

        if kind != "symbol":
            return finalize_hint_list(pairs)

        decl_kind = _symbol_declaration_kind(rec)
        is_type = decl_kind in _TYPE_SYMBOL_KINDS
        is_method = decl_kind in _METHOD_SYMBOL_KINDS

        if is_type:
            if _out_count(edge_summary, "DECLARES.DECLARES_CLIENT") > 0:
                pairs.append(
                    (PRIORITY_DECLARES_TYPE_ROLLUP, TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS.format(id=node_id))
                )
            if _out_count(edge_summary, "DECLARES.EXPOSES") > 0:
                pairs.append(
                    (PRIORITY_DECLARES_TYPE_ROLLUP, TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS.format(id=node_id))
                )
            return finalize_hint_list(pairs)

        if is_method:
            if _out_count(edge_summary, "OVERRIDDEN_BY") > 0:
                pairs.append((PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_OVERRIDERS.format(id=node_id)))
            if _out_count(edge_summary, "OVERRIDDEN_BY.DECLARES_CLIENT") > 0:
                pairs.append(
                    (PRIORITY_OVERRIDDEN_AXIS, TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS.format(id=node_id))
                )
            if _out_count(edge_summary, "DECLARES_CLIENT") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT.format(id=node_id)))
            if _out_count(edge_summary, "EXPOSES") > 0:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_INBOUND_ROUTE.format(id=node_id)))
            if _out_count(edge_summary, "CALLS") >= 10:
                pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_MANY_CALLS))
            return finalize_hint_list(pairs)

        return finalize_hint_list(pairs)

    return []
