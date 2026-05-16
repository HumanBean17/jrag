"""MCP V2 graph query surface (``search`` / ``find`` / ``describe`` / ``neighbors`` / ``resolve``).

Strict frame contract
---------------------
NodeFilter is a typed predicate bag: each populated field maps to one stored graph
attribute for the selected kind; inapplicable fields fail loud with a teaching message.
The ``search`` tool's ``query`` parameter is the ranked-text carve-out; structured
prefix fields (``fqn_prefix``, ``path_prefix``, ``target_path_prefix``) reject ``*``
and ``?`` — see ``_validate_no_wildcards``.

Revisit trigger (``propose/completed/MCP-FILTER-FRAME-PROPOSE.md`` section 3.4.6)
--------------------------------------------------------------
If **three** legitimate issue-tracker workflows appear within **six months** of frame
lock where the strict frame has no clean analog under ``search``, deferred
``resolve``, or documented multi-call patterns, reopen the frame for revision.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import threading
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, validate_call
from sentence_transformers import SentenceTransformer

from index_common import SBERT_MODEL
from java_codebase_rag.config import resolved_sbert_model_for_process_env
from java_ontology import ResolveReason
from kuzu_queries import KuzuGraph
from mcp_hints import MCP_HINTS_FIELD_DESCRIPTION, generate_hints
from search_lancedb import TABLES, run_search

DeclarationSymbolKind = Literal["class", "interface", "enum", "record", "annotation", "method", "constructor"]

# Composed describe-time keys in edge_summary (e.g. DECLARES.DECLARES_CLIENT) are
# intentionally not EdgeType literals — neighbors(edge_types=...) rejects them.
# Virtual override-axis keys (OVERRIDDEN_BY, …) are also rejected; stored OVERRIDES is an EdgeType.
EdgeType = Literal[
    "EXTENDS",
    "IMPLEMENTS",
    "INJECTS",
    "OVERRIDES",
    "DECLARES",
    "DECLARES_CLIENT",
    "DECLARES_PRODUCER",
    "CALLS",
    "EXPOSES",
    "HTTP_CALLS",
    "ASYNC_CALLS",
]

_NEIGHBOR_EDGE_TYPES_ADAPTER = TypeAdapter(
    Annotated[list[EdgeType], Field(min_length=1, description="At least one graph edge label")]
)

_st_lock = threading.Lock()
_st_model: SentenceTransformer | None = None

_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP = frozenset(
    {"class", "interface", "enum", "record", "annotation"}
)

_METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP = frozenset({"method"})

_fail_loud_counts: dict[str, int] = {}
_fail_loud_lock = threading.Lock()


def _log_fail_loud(category: str) -> None:
    """Increment process-local fail-loud counter and emit one stderr line (PR-FRAME-3)."""
    with _fail_loud_lock:
        _fail_loud_counts[category] = _fail_loud_counts.get(category, 0) + 1
        n = _fail_loud_counts[category]
    print(f"[filter-frame] fail-loud category={category} count={n}", file=sys.stderr, flush=True)


def filter_frame_counters() -> dict[str, int]:
    """Snapshot of fail-loud counts (tests / local diagnostics; not an MCP tool)."""
    with _fail_loud_lock:
        return dict(_fail_loud_counts)


def _get_sentence_transformer(model_name: str, device: str | None) -> SentenceTransformer:
    global _st_model
    with _st_lock:
        if _st_model is None:
            _st_model = SentenceTransformer(
                model_name,
                device=device,
                trust_remote_code=True,
            )
        return _st_model


class NodeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    microservice: str | None = None
    module: str | None = None
    source_layer: str | None = None
    role: str | None = None
    exclude_roles: list[str] | None = None
    annotation: str | None = None
    capability: str | None = None
    fqn_prefix: str | None = None
    symbol_kind: DeclarationSymbolKind | None = None
    symbol_kinds: list[DeclarationSymbolKind] | None = None
    http_method: str | None = None
    path_prefix: str | None = None
    framework: str | None = None
    client_kind: str | None = None
    target_service: str | None = None
    target_path_prefix: str | None = None
    producer_kind: str | None = None
    topic_prefix: str | None = None


_NODEFILTER_FIELD_ORDER: tuple[str, ...] = tuple(NodeFilter.model_fields.keys())

_NODEFILTER_APPLICABLE_FIELDS: dict[Literal["symbol", "route", "client", "producer"], tuple[str, ...]] = {
    "symbol": (
        "microservice",
        "module",
        "role",
        "exclude_roles",
        "annotation",
        "capability",
        "fqn_prefix",
        "symbol_kind",
        "symbol_kinds",
    ),
    "route": (
        "microservice",
        "module",
        "http_method",
        "path_prefix",
        "framework",
    ),
    "client": (
        "microservice",
        "module",
        "source_layer",
        "client_kind",
        "target_service",
        "target_path_prefix",
        "http_method",
    ),
    "producer": (
        "microservice",
        "module",
        "source_layer",
        "producer_kind",
        "topic_prefix",
    ),
}


def _ordered_nodefilter_fields(field_names: set[str]) -> list[str]:
    return [name for name in _NODEFILTER_FIELD_ORDER if name in field_names]


def _populated_nodefilter_fields(nf: NodeFilter) -> set[str]:
    populated: set[str] = set()
    for field_name in _NODEFILTER_FIELD_ORDER:
        value = getattr(nf, field_name)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        populated.add(field_name)
    return populated


def _nodefilter_inapplicable_fields(
    kind: Literal["symbol", "route", "client", "producer"], nf: NodeFilter,
) -> list[str]:
    populated = _populated_nodefilter_fields(nf)
    applicable = set(_NODEFILTER_APPLICABLE_FIELDS[kind])
    return _ordered_nodefilter_fields(populated - applicable)


def _nodefilter_applicability_error(
    kind: Literal["symbol", "route", "client", "producer"], nf: NodeFilter,
) -> str | None:
    inapplicable = _nodefilter_inapplicable_fields(kind, nf)
    if not inapplicable:
        return None
    applicable = ", ".join(_NODEFILTER_APPLICABLE_FIELDS[kind])
    bad = ", ".join(inapplicable)
    return (
        f"Invalid filter for kind='{kind}': populated field(s) not applicable: [{bad}]. "
        f"Applicable field(s): [{applicable}]"
    )


def _validate_no_wildcards(nf: NodeFilter) -> str | None:
    """Reject ``*`` / ``?`` in prefix-match fields; wildcards belong in ``search(query=…)``."""
    for field_name in ("fqn_prefix", "path_prefix", "target_path_prefix"):
        val = getattr(nf, field_name)
        if val is None:
            continue
        if "*" in val or "?" in val:
            return (
                f"Wildcards (* and ?) are not supported in structured filter field `{field_name}`; "
                "use search(query=...) for ranked text match instead."
            )
    return None


def _filter_validation_error_message(exc: ValidationError) -> str:
    items: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = str(err.get("msg") or "invalid value")
        if loc:
            items.append(f"{loc}: {msg}")
        else:
            items.append(msg)
    details = "; ".join(items) if items else str(exc)
    return f"Invalid filter: {details}"


def _coerce_filter(
    value: NodeFilter | dict[str, Any] | str | None,
) -> NodeFilter | dict[str, Any] | None:
    """Normalize MCP tool input: weak clients sometimes pass JSON-encoded strings."""
    if value is None or isinstance(value, NodeFilter):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            decoded = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"filter must be a JSON object; invalid JSON: {exc.msg}") from exc
        if decoded is None:
            return None
        if not isinstance(decoded, dict):
            raise ValueError(f"filter must decode to a JSON object, got {type(decoded).__name__}")
        return decoded
    return value


class SearchHit(BaseModel):
    chunk_id: str
    symbol_id: str | None = None
    fqn: str | None = None
    score: float
    snippet: str
    microservice: str | None = None
    module: str | None = None
    role: str | None = None


class NodeRef(BaseModel):
    id: str
    kind: Literal["symbol", "route", "client", "producer"]
    fqn: str
    symbol_kind: str | None = None
    microservice: str | None = None
    module: str | None = None
    role: str | None = None


class NodeRecord(BaseModel):
    id: str
    kind: Literal["symbol", "route", "client", "producer"]
    fqn: str
    data: dict[str, Any] = Field(default_factory=dict)
    edge_summary: dict[str, dict[str, int]] | None = Field(
        default=None,
        description=(
            "Per graph edge label, in/out incident counts. For type Symbols (class, interface, "
            "enum, record, annotation), may also include composed dot-keys "
            "`DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, and `DECLARES.EXPOSES`: 2-hop summaries "
            "(DECLARES to member, then that edge) — edge-row counts, not EdgeType literals; "
            "do not pass them to neighbors(edge_types=…). For method Symbols, may include "
            "override-axis virtual keys `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, "
            "`OVERRIDDEN_BY.DECLARES_PRODUCER`, `OVERRIDDEN_BY.EXPOSES`, plus an `OVERRIDES` map entry "
            "that **merges** stored "
            "`[:OVERRIDES]` in/out counts with the describe-time dispatch-up rollup (per "
            "direction `max`, so inbound stored overrides are not dropped). Those virtual / "
            "dot-keys are not valid neighbors(edge_types=…) arguments. The stored relationship "
            "label `OVERRIDES` **is** a valid EdgeType for neighbors."
        ),
    )


class Edge(BaseModel):
    origin_id: str
    edge_type: str
    direction: Literal["in", "out"]
    other: NodeRef
    attrs: dict[str, Any] = Field(default_factory=dict)


class SearchOutput(BaseModel):
    success: bool
    results: list[SearchHit] = Field(default_factory=list)
    message: str | None = None
    limit: int | None = Field(
        default=None,
        description="Echoed from the request — the page size the server applied. None on success=False.",
    )
    offset: int | None = Field(
        default=None,
        description="Echoed from the request — the page offset the server applied. None on success=False.",
    )
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)


class FindOutput(BaseModel):
    success: bool
    results: list[NodeRef] = Field(default_factory=list)
    message: str | None = None
    limit: int | None = Field(
        default=None,
        description="Echoed from the request — the page size the server applied. None on success=False.",
    )
    offset: int | None = Field(
        default=None,
        description="Echoed from the request — the page offset the server applied. None on success=False.",
    )
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)


class DescribeOutput(BaseModel):
    success: bool
    record: NodeRecord | None = None
    message: str | None = None
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)


class NeighborsOutput(BaseModel):
    success: bool
    results: list[Edge] = Field(default_factory=list)
    message: str | None = None
    requested_edge_types: list[str] = Field(
        default_factory=list,
        description="Echo of neighbors(edge_types=...) from the request; empty when success=False.",
    )
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)


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
    "short_name": 3,
    "client_target": 3,
    "producer_topic": 3,
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
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)


def _node_kind_from_id(id_str: str) -> Literal["symbol", "route", "client", "producer"]:
    if id_str.startswith("sym:"):
        return "symbol"
    if id_str.startswith("route:") or id_str.startswith("r:"):
        return "route"
    if id_str.startswith("client:") or id_str.startswith("c:"):
        return "client"
    if id_str.startswith("producer:") or id_str.startswith("p:"):
        return "producer"
    raise ValueError(f"Unknown id prefix for `{id_str}`")


def _resolve_node_kind(graph: KuzuGraph, node_id: str) -> Literal["symbol", "route", "client", "producer"]:
    try:
        return _node_kind_from_id(node_id)
    except ValueError:
        pass
    if graph._rows("MATCH (n:Symbol) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "symbol"
    if graph._rows("MATCH (n:Route) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "route"
    if graph._rows("MATCH (n:Client) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "client"
    if graph._rows("MATCH (n:Producer) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "producer"
    raise ValueError(f"Unknown id prefix for `{node_id}`")


def _chunk_id_from_row(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or "")
    start = row.get("start") or {}
    end = row.get("end") or {}
    sb = int(start.get("byte_offset") or 0) if isinstance(start, dict) else 0
    eb = int(end.get("byte_offset") or 0) if isinstance(end, dict) else 0
    return f"{filename}:{sb}:{eb}"


def _row_to_search_hit(row: dict[str, Any]) -> SearchHit:
    score = float(row.get("_rrf_score") or row.get("_score") or 0.0)
    return SearchHit(
        chunk_id=_chunk_id_from_row(row),
        symbol_id=_chunk_to_symbol_id(row),
        fqn=str(row.get("primary_type_fqn")) if row.get("primary_type_fqn") else None,
        score=score,
        snippet=str(row.get("text") or ""),
        microservice=str(row.get("microservice")) if row.get("microservice") else None,
        module=str(row.get("module")) if row.get("module") else None,
        role=str(row.get("role")) if row.get("role") else None,
    )


def _chunk_to_symbol_id(chunk_row: dict[str, Any]) -> str | None:
    symbol_id = chunk_row.get("symbol_id")
    if symbol_id:
        return str(symbol_id)
    meta = chunk_row.get("metadata")
    if isinstance(meta, str):
        try:
            parsed = json.loads(meta)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            meta = None
    if isinstance(meta, dict):
        nested = meta.get("symbol_id")
        if nested:
            return str(nested)
    return None


def _symbol_where_from_filter(f: NodeFilter) -> tuple[str, dict[str, Any]]:
    preds: list[str] = []
    params: dict[str, Any] = {}
    if f.microservice:
        preds.append("s.microservice = $microservice")
        params["microservice"] = f.microservice
    if f.module:
        preds.append("s.module = $module")
        params["module"] = f.module
    if f.role:
        preds.append("s.role = $role")
        params["role"] = f.role
    if f.exclude_roles:
        preds.append("NOT s.role IN $exclude_roles")
        params["exclude_roles"] = list(f.exclude_roles)
    if f.annotation:
        preds.append("list_contains(s.annotations, $annotation)")
        params["annotation"] = f.annotation
    if f.capability:
        preds.append("$capability IN s.capabilities")
        params["capability"] = f.capability
    if f.fqn_prefix:
        preds.append("s.fqn STARTS WITH $fqn_prefix")
        params["fqn_prefix"] = f.fqn_prefix
    if f.symbol_kind:
        preds.append("s.kind = $symbol_kind")
        params["symbol_kind"] = f.symbol_kind
    if f.symbol_kinds:
        preds.append("s.kind IN $symbol_kinds")
        params["symbol_kinds"] = list(f.symbol_kinds)
    where = f"WHERE {' AND '.join(preds)}" if preds else ""
    return where, params


def _node_ref_from_row(kind: Literal["symbol", "route", "client", "producer"], row: dict[str, Any]) -> NodeRef:
    symbol_kind: str | None = None
    if kind == "symbol":
        fqn = str(row.get("fqn") or "")
        role = str(row.get("role") or "") or None
        symbol_kind_val = str(row.get("symbol_kind") or row.get("kind") or "").strip()
        symbol_kind = symbol_kind_val or None
    elif kind == "route":
        method = str(row.get("method") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{method} {path}".strip()
        role = None
    elif kind == "client":
        method = str(row.get("method") or "")
        target = str(row.get("target_service") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{target} {method} {path}".strip()
        role = None
    else:
        topic = str(row.get("topic") or "")
        broker = str(row.get("broker") or "")
        fqn = f"{topic} {broker}".strip()
        role = None
    return NodeRef(
        id=str(row.get("id") or ""),
        kind=kind,
        fqn=fqn,
        symbol_kind=symbol_kind,
        microservice=str(row.get("microservice") or "") or None,
        module=str(row.get("module") or "") or None,
        role=role,
    )


def _load_node_record(
    graph: KuzuGraph, node_id: str, kind: Literal["symbol", "route", "client", "producer"],
) -> dict[str, Any] | None:
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
            "n.id AS id, n.kind AS kind, n.framework AS framework, n.method AS method, n.path AS path, "
            "n.path_template AS path_template, n.path_regex AS path_regex, n.topic AS topic, "
            "n.broker AS broker, n.feign_name AS feign_name, n.feign_url AS feign_url, "
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


def _incident_counts(cell: dict[str, int] | None) -> dict[str, int]:
    if not cell:
        return {"in": 0, "out": 0}
    return {"in": int(cell.get("in", 0)), "out": int(cell.get("out", 0))}


def _merge_overrides_edge_summary(
    stored_before_rollups: dict[str, int],
    summary_after_rollups: dict[str, dict[str, int]],
) -> None:
    """Reconcile `OVERRIDES` with `override_axis_rollup_for` without clobbering stored `in`.

    Rollup rows reuse the ``OVERRIDES`` key for dispatch-up counts only (``in`` is always
    zero there). Stored ``[:OVERRIDES]`` edges contribute real ``in``/``out`` from Kuzu;
    merge per direction with ``max`` so inbound override edges stay visible.
    """
    roll = _incident_counts(summary_after_rollups.get("OVERRIDES"))
    if "OVERRIDES" not in summary_after_rollups and not any(stored_before_rollups.values()):
        return
    merged_in = max(stored_before_rollups["in"], roll["in"])
    merged_out = max(stored_before_rollups["out"], roll["out"])
    if merged_in == 0 and merged_out == 0:
        summary_after_rollups.pop("OVERRIDES", None)
    else:
        summary_after_rollups["OVERRIDES"] = {"in": merged_in, "out": merged_out}


def _edge_summary_for_node(
    graph: KuzuGraph, node_id: str, *, kind: str, row: dict[str, Any]
) -> dict[str, dict[str, int]]:
    summary = dict(graph.edge_counts_for(node_id))
    sym_kind = str(row.get("kind") or "")
    if kind == "symbol" and sym_kind in _TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP:
        summary.update(graph.member_edge_rollup_for(node_id))
    elif kind == "symbol" and sym_kind in _METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP:
        stored_overrides = _incident_counts(summary.get("OVERRIDES"))
        summary.update(graph.override_axis_rollup_for(node_id))
        _merge_overrides_edge_summary(stored_overrides, summary)
    return summary


def _node_matches_filter(
    kind: Literal["symbol", "route", "client", "producer"], row: dict[str, Any], f: NodeFilter | None,
) -> bool:
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
        fqn_val = str(row.get("fqn") or row.get("primary_type_fqn") or "")
        symbol_kind_val = str(row.get("kind") or row.get("symbol_kind") or "")
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


def search_v2(
    query: str,
    table: str = "java",
    hybrid: bool = False,
    limit: int = 5,
    offset: int = 0,
    path_contains: str | None = None,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    graph: KuzuGraph | None = None,
) -> SearchOutput:
    try:
        raw_filter = _coerce_filter(filter)
        try:
            nf = (
                NodeFilter.model_validate(raw_filter)
                if raw_filter is not None and not isinstance(raw_filter, NodeFilter)
                else raw_filter
            )
        except ValidationError as exc:
            _log_fail_loud("unknown_key")
            return SearchOutput(
                success=False,
                message=_filter_validation_error_message(exc),
                hints=[],
                limit=None,
                offset=None,
            )
        if nf and (err := _nodefilter_applicability_error("symbol", nf)):
            _log_fail_loud("applicability")
            return SearchOutput(success=False, message=err, hints=[], limit=None, offset=None)
        if nf and (err := _validate_no_wildcards(nf)):
            _log_fail_loud("wildcard")
            return SearchOutput(success=False, message=err, hints=[], limit=None, offset=None)
        model_name = resolved_sbert_model_for_process_env(SBERT_MODEL)
        device = os.environ.get("SBERT_DEVICE") or None
        model = _get_sentence_transformer(model_name, device)
        uri = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip() or str(
            (Path.cwd() / ".java-codebase-rag").resolve()
        )
        uri_path = Path(uri)
        if not uri.startswith(("s3://", "gs://", "az://")) and uri_path.exists():
            uri = str(uri_path.resolve())
        table_keys = list(TABLES) if table == "all" else [table]
        rows = run_search(
            query,
            uri=uri,
            table_keys=table_keys,
            hybrid=hybrid,
            limit=limit,
            offset=offset,
            path_substring=path_contains,
            model_name=model_name,
            device=device,
            model=model,
        )
        hits: list[SearchHit] = []
        for row in rows:
            if path_contains and path_contains not in str(row.get("filename") or ""):
                continue
            if nf:
                row_kind = "symbol"
                if not _node_matches_filter(row_kind, row, nf):
                    continue
            hits.append(_row_to_search_hit(row))
        hint_payload = {
            "success": True,
            "results": [h.model_dump() for h in hits],
            "limit": limit,
            "offset": offset,
        }
        return SearchOutput(
            success=True,
            results=hits,
            limit=limit,
            offset=offset,
            hints=generate_hints("search", hint_payload),
        )
    except Exception as exc:
        return SearchOutput(success=False, message=str(exc), hints=[], limit=None, offset=None)


def find_v2(
    kind: Literal["symbol", "route", "client", "producer"],
    filter: NodeFilter | dict[str, Any] | str,
    limit: int = 25,
    offset: int = 0,
    graph: KuzuGraph | None = None,
) -> FindOutput:
    try:
        g = graph or KuzuGraph.get()
        raw_filter = _coerce_filter(filter)
        if raw_filter is None:
            raw_filter = {}
        try:
            nf = NodeFilter.model_validate(raw_filter) if not isinstance(raw_filter, NodeFilter) else raw_filter
        except ValidationError as exc:
            _log_fail_loud("unknown_key")
            return FindOutput(
                success=False,
                message=_filter_validation_error_message(exc),
                hints=[],
                limit=None,
                offset=None,
            )
        if err := _nodefilter_applicability_error(kind, nf):
            _log_fail_loud("applicability")
            return FindOutput(success=False, message=err, hints=[], limit=None, offset=None)
        if err := _validate_no_wildcards(nf):
            _log_fail_loud("wildcard")
            return FindOutput(success=False, message=err, hints=[], limit=None, offset=None)
        fetch_cap = int(limit) + int(offset) + 1
        if kind == "symbol":
            where, params = _symbol_where_from_filter(nf)
            params["lim"] = fetch_cap
            rows = g._rows(  # noqa: SLF001
                f"MATCH (s:Symbol) {where} RETURN s.id AS id, s.fqn AS fqn, s.microservice AS microservice, "
                "s.module AS module, s.role AS role, s.kind AS symbol_kind ORDER BY s.fqn LIMIT $lim",
                params,
            )
        elif kind == "route":
            rows = g.list_routes(
                microservice=nf.microservice,
                framework=nf.framework,
                path_prefix=nf.path_prefix,
                method=nf.http_method,
                limit=max(500, fetch_cap),
            )
            rows = [r for r in rows if _node_matches_filter("route", r, nf)]
        elif kind == "client":
            rows = g.list_clients(
                microservice=nf.microservice,
                client_kind=nf.client_kind,
                target_service=nf.target_service,
                path_prefix=nf.target_path_prefix,
                method=nf.http_method,
                limit=max(500, fetch_cap),
            )
            rows = [r for r in rows if _node_matches_filter("client", r, nf)]
        else:
            rows = g.list_producers(
                microservice=nf.microservice,
                producer_kind=nf.producer_kind,
                topic_prefix=nf.topic_prefix,
                limit=max(500, fetch_cap),
            )
            rows = [r for r in rows if _node_matches_filter("producer", r, nf)]
        has_more_results = len(rows) > int(offset) + int(limit)
        rows = rows[offset : offset + limit]
        refs = [_node_ref_from_row(kind, r) for r in rows]
        filter_dump = nf.model_dump(exclude_none=True)
        hint_payload: dict[str, Any] = {
            "success": True,
            "kind": kind,
            "results": [r.model_dump() for r in refs],
            "limit": limit,
            "offset": offset,
            "filter": filter_dump,
            "has_more_results": has_more_results,
        }
        return FindOutput(
            success=True,
            results=refs,
            limit=limit,
            offset=offset,
            hints=generate_hints("find", hint_payload),
        )
    except Exception as exc:
        return FindOutput(success=False, message=str(exc), hints=[], limit=None, offset=None)


def describe_v2(
    id: str | None = None,
    fqn: str | None = None,
    graph: KuzuGraph | None = None,
) -> DescribeOutput:
    try:
        g = graph or KuzuGraph.get()
        has_id = bool(id and str(id).strip())
        has_fqn = bool(fqn and str(fqn).strip())
        if not has_id and not has_fqn:
            return DescribeOutput(success=False, message="id or fqn required", hints=[])
        hint_message: str | None = None
        node_id: str
        if has_id:
            node_id = str(id).strip()
        else:
            fqn_val = str(fqn).strip()
            rows = g._rows(  # noqa: SLF001
                "MATCH (s:Symbol) WHERE s.fqn = $fqn RETURN s.id AS id LIMIT 2",
                {"fqn": fqn_val},
            )
            if not rows:
                return DescribeOutput(success=False, message=f"No Symbol found for fqn='{fqn_val}'", hints=[])
            node_id = str(rows[0]["id"] or "")
            if len(rows) > 1:
                hint_message = (
                    "multiple symbols share this FQN; use "
                    f"resolve(identifier={fqn_val!r}, hint_kind='symbol') to list candidates with reasons, "
                    "then describe(id=...) on the chosen node"
                )
        kind = _resolve_node_kind(g, node_id)
        row = _load_node_record(g, node_id, kind)
        if row is None:
            return DescribeOutput(success=False, message=f"No node found for `{node_id}`", hints=[])
        ref = _node_ref_from_row(kind, row)
        edge_summary = _edge_summary_for_node(g, node_id, kind=kind, row=row)
        record = NodeRecord(id=ref.id, kind=kind, fqn=ref.fqn, data=row, edge_summary=edge_summary)
        return DescribeOutput(
            success=True,
            record=record,
            message=hint_message,
            hints=generate_hints("describe", {"success": True, "record": record.model_dump()}),
        )
    except ValueError as exc:
        return DescribeOutput(success=False, message=str(exc), hints=[])
    except Exception as exc:
        return DescribeOutput(success=False, message=str(exc), hints=[])


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
    g: KuzuGraph,
    identifier: str,
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.id = $id RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "exact_id", len(identifier)))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.fqn = $fqn RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"fqn": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "exact_fqn", len(identifier)))

    suffix = f".{identifier}"
    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.fqn = $ident OR s.fqn ENDS WITH $suffix "
        f"RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"ident": identifier, "suffix": suffix, "lim": lim},
    )
    for row in rows:
        fqn = str(row.get("fqn") or "")
        spec = len(fqn)
        out.append((_node_ref_from_row("symbol", row), "fqn_suffix", spec))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (s:Symbol) WHERE s.name = $name RETURN {_SYMBOL_RESOLVE_RETURN} LIMIT $lim",
        {"name": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("symbol", row), "short_name", len(identifier)))

    return out


def _resolve_route_candidates(
    g: KuzuGraph,
    identifier: str,
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT

    rows = g._rows(  # noqa: SLF001
        f"MATCH (r:Route) WHERE r.id = $id RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("route", row), "exact_id", len(identifier)))

    ms_route = _resolve_parse_microservice_route(identifier)
    if ms_route is not None:
        microservice, method, path = ms_route
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.microservice = $ms AND r.method = $method "
            f"AND (r.path = $path OR r.path_template = $path) "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"ms": microservice, "method": method, "path": path, "lim": lim},
        )
        for row in rows:
            spec = len(path)
            out.append((_node_ref_from_row("route", row), "route_method_path", spec))

    method_path = _resolve_parse_route_method_path(identifier)
    if method_path is not None:
        method, path = method_path
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.method = $method "
            f"AND (r.path = $path OR r.path_template = $path) "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"method": method, "path": path, "lim": lim},
        )
        for row in rows:
            out.append((_node_ref_from_row("route", row), "route_method_path", len(path)))

    if identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (r:Route) WHERE r.path = $path OR r.path_template = $path "
            f"RETURN {_ROUTE_RESOLVE_RETURN} LIMIT $lim",
            {"path": identifier, "lim": lim},
        )
        for row in rows:
            path_val = str(row.get("path_template") or row.get("path") or "")
            out.append((_node_ref_from_row("route", row), "route_template", len(path_val)))

    return out


def _resolve_client_candidates(
    g: KuzuGraph,
    identifier: str,
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT

    rows = g._rows(  # noqa: SLF001
        f"MATCH (c:Client) WHERE c.id = $id RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim},
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
                f"AND (c.path STARTS WITH $path OR c.path_template STARTS WITH $path) "
                f"RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
                {"target": target, "path": path_prefix, "lim": lim},
            )
            for row in rows:
                spec = len(path_prefix)
                out.append((_node_ref_from_row("client", row), "client_target_path", spec))
    elif not identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (c:Client) WHERE c.target_service = $target RETURN {_CLIENT_RESOLVE_RETURN} LIMIT $lim",
            {"target": identifier, "lim": lim},
        )
        for row in rows:
            out.append((_node_ref_from_row("client", row), "client_target", len(identifier)))

    return out


def _resolve_producer_candidates(
    g: KuzuGraph,
    identifier: str,
) -> list[tuple[NodeRef, ResolveReason, int]]:
    out: list[tuple[NodeRef, ResolveReason, int]] = []
    lim = _RESOLVE_PRE_DEDUP_LIMIT

    rows = g._rows(  # noqa: SLF001
        f"MATCH (p:Producer) WHERE p.id = $id RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
        {"id": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("producer", row), "exact_id", len(identifier)))

    rows = g._rows(  # noqa: SLF001
        f"MATCH (p:Producer) WHERE p.topic = $topic RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
        {"topic": identifier, "lim": lim},
    )
    for row in rows:
        out.append((_node_ref_from_row("producer", row), "producer_topic", len(identifier)))

    if not identifier.startswith("/"):
        rows = g._rows(  # noqa: SLF001
            f"MATCH (p:Producer) WHERE p.topic STARTS WITH $topic RETURN {_PRODUCER_RESOLVE_RETURN} LIMIT $lim",
            {"topic": identifier, "lim": lim},
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
    out = out.model_copy(update={"hints": generate_hints("resolve", hint_payload)})
    _resolve_assert_invariants(out)
    return out


def resolve_v2(
    identifier: str,
    hint_kind: Literal["symbol", "route", "client", "producer"] | None = None,
    graph: KuzuGraph | None = None,
) -> ResolveOutput:
    try:
        trimmed, err = _resolve_validate_identifier(identifier)
        if err is not None:
            out = ResolveOutput(
                success=False,
                status="none",
                message=err,
                hints=[],
                resolved_identifier=None,
            )
            _resolve_assert_invariants(out)
            return out

        assert trimmed is not None
        if "*" in trimmed or "?" in trimmed:
            return _resolve_finalize_success(trimmed, hint_kind, [])

        g = graph or KuzuGraph.get()
        raw: list[tuple[NodeRef, ResolveReason, int]] = []
        for kind in _resolve_kinds_to_search(hint_kind):
            if kind == "symbol":
                raw.extend(_resolve_symbol_candidates(g, trimmed))
            elif kind == "route":
                raw.extend(_resolve_route_candidates(g, trimmed))
            elif kind == "client":
                raw.extend(_resolve_client_candidates(g, trimmed))
            else:
                raw.extend(_resolve_producer_candidates(g, trimmed))

        deduped = _resolve_dedupe_candidates(raw)
        ranked = _resolve_rank_candidates(deduped)
        capped = ranked[:_RESOLVE_CANDIDATE_CAP]
        return _resolve_finalize_success(trimmed, hint_kind, capped)
    except Exception as exc:
        out = ResolveOutput(
            success=False,
            status="none",
            message=str(exc),
            hints=[],
            resolved_identifier=None,
        )
        _resolve_assert_invariants(out)
        return out


@validate_call(config={"arbitrary_types_allowed": True})
def neighbors_v2(
    ids: str | list[str],
    # Required fields are intentional: direct Python calls and MCP-bound calls
    # share the same validation contract through @validate_call.
    direction: Literal["in", "out"] = Field(...),
    edge_types: list[EdgeType] = Field(...),
    limit: int = 25,
    offset: int = 0,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    graph: Any | None = None,
) -> NeighborsOutput:
    try:
        _NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(edge_types)
        # Kuzu 0.11.x can drop `label(e) IN $list` in WHERE; use OR of scalar equalities instead.
        # Typed unions like `[e:CALLS|HTTP_CALLS]` fail the binder when RETURN references rel
        # columns that exist on only some of the union members.
        labels = list(dict.fromkeys(edge_types))
        label_params = [f"l{i}" for i in range(len(labels))]
        label_predicate = "(" + " OR ".join(f"label(e) = ${name}" for name in label_params) + ")"
        g = graph or KuzuGraph.get()
        try:
            raw_filter = _coerce_filter(filter)
            nf = (
                NodeFilter.model_validate(raw_filter)
                if raw_filter is not None and not isinstance(raw_filter, NodeFilter)
                else raw_filter
            )
        except ValidationError as exc:
            _log_fail_loud("unknown_key")
            return NeighborsOutput(
                success=False,
                message=_filter_validation_error_message(exc),
                hints=[],
                requested_edge_types=[],
            )
        if nf and (err := _validate_no_wildcards(nf)):
            _log_fail_loud("wildcard")
            return NeighborsOutput(success=False, message=err, hints=[], requested_edge_types=[])
        origins = [ids] if isinstance(ids, str) else list(ids)
        results: list[Edge] = []
        for origin_id in origins:
            _resolve_node_kind(g, origin_id)
            q_params = {"id": origin_id, **dict(zip(label_params, labels, strict=True))}
            if direction == "out":
                rows = g._rows(  # noqa: SLF001
                    "MATCH (a)-[e]->(b) WHERE a.id = $id AND "
                    f"{label_predicate} "
                    "RETURN b.id AS other_id, label(e) AS edge_type, e.confidence AS confidence, "
                    "e.strategy AS strategy, e.match AS match, e.mechanism AS mechanism, "
                    "e.annotation AS annotation, e.field_or_param AS field_or_param, "
                    "e.source AS source, e.call_site_line AS call_site_line, "
                    "e.call_site_byte AS call_site_byte, e.arg_count AS arg_count, "
                    "e.resolved AS resolved",
                    q_params,
                )
            else:
                rows = g._rows(  # noqa: SLF001
                    "MATCH (a)<-[e]-(b) WHERE a.id = $id AND "
                    f"{label_predicate} "
                    "RETURN b.id AS other_id, label(e) AS edge_type, e.confidence AS confidence, "
                    "e.strategy AS strategy, e.match AS match, e.mechanism AS mechanism, "
                    "e.annotation AS annotation, e.field_or_param AS field_or_param, "
                    "e.source AS source, e.call_site_line AS call_site_line, "
                    "e.call_site_byte AS call_site_byte, e.arg_count AS arg_count, "
                    "e.resolved AS resolved",
                    q_params,
                )
            for row in rows:
                other_id = str(row.get("other_id") or "")
                other_kind = _resolve_node_kind(g, other_id)
                other_rec = _load_node_record(g, other_id, other_kind)
                if other_rec is None:
                    continue
                if nf and (err := _nodefilter_applicability_error(other_kind, nf)):
                    _log_fail_loud("applicability")
                    return NeighborsOutput(success=False, message=err, hints=[], requested_edge_types=[])
                if not _node_matches_filter(other_kind, other_rec, nf):
                    continue
                attrs = {
                    k: v
                    for k, v in row.items()
                    if k
                    not in {
                        "other_id",
                        "edge_type",
                    }
                    and v not in (None, "")
                }
                results.append(
                    Edge(
                        origin_id=origin_id,
                        edge_type=str(row.get("edge_type") or ""),
                        direction=direction,
                        other=_node_ref_from_row(other_kind, other_rec),
                        attrs=attrs,
                    )
                )
        sliced = results[offset : offset + limit]
        first_origin = origins[0]
        origin_kind = _resolve_node_kind(g, first_origin)
        subject_record = _load_node_record(g, first_origin, origin_kind)
        # Empty-result hints use the sliced page only; offset>0 or strict filters can
        # yield [] while hops exist — skip structural hints in that case.
        neigh_payload = {
            "success": True,
            "results": [e.model_dump() for e in sliced],
            "requested_edge_types": list(labels),
            "requested_direction": direction,
            "offset": offset,
            "origin_id": first_origin,
            "subject_record": subject_record,
        }
        return NeighborsOutput(
            success=True,
            results=sliced,
            requested_edge_types=list(labels),
            hints=generate_hints("neighbors", neigh_payload),
        )
    except ValidationError:
        raise
    except Exception as exc:
        return NeighborsOutput(success=False, message=str(exc), hints=[], requested_edge_types=[])
