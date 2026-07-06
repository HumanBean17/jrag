"""MCP V2 graph query surface (``search`` / ``find`` / ``describe`` / ``neighbors`` / ``resolve``).

Strict frame contract
---------------------
NodeFilter is a typed predicate bag: each populated field maps to one stored graph
attribute for the selected kind; inapplicable fields fail loud with a teaching message.
The ``search`` tool's ``query`` parameter is the ranked-text carve-out; the substring
fields (``fqn_contains``, ``path_contains``, ``target_path_contains``, ``topic_contains``)
match literally (Cypher ``CONTAINS``) — no wildcard/metacharacter handling.

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
from typing import Annotated, Any, Literal, TYPE_CHECKING, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator, validate_call

if TYPE_CHECKING:
    # Eager import would pull torch at module load. The vector stack is optional (graph-only
    # installs ship without torch/lancedb); it is imported lazily in _get_sentence_transformer.
    from sentence_transformers import SentenceTransformer

from graph_types import (
    NodeRef,
    StructuredHint,
    _hints_or_skip,
    _node_ref_from_row,
    _resolve_node_kind,
    _to_structured_hints,
    set_hints_enabled,
)
from index_common import SBERT_MODEL
from java_codebase_rag.config import resolved_sbert_model_for_process_env
from java_ontology import EDGE_SCHEMA
from ladybug_queries import LadybugGraph, OVERRIDE_AXIS_COMPOSED_EDGE_TYPES
from mcp_hints import MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION

# The vector stack (lancedb/torch, reached via search_lancedb) is optional — it is absent on
# graph-only installs (macOS Intel). Import eagerly when available so ``run_search``/``TABLES``
# exist as module attributes (tests monkeypatch ``mcp_v2.run_search``; callers use ``TABLES``);
# fall back to sentinels on ImportError so importing this module never fails and ``search_v2``
# can return a clean "vector search unavailable" envelope instead of crashing.
try:
    from search_lancedb import TABLES, run_search
except ImportError:  # graph-only install: no torch/lancedb
    TABLES = {}
    run_search = None
__all__ = [
    "search_v2",
    "find_v2",
    "describe_v2",
    "neighbors_v2",
    "resolve_v2",
    "SearchOutput",
    "FindOutput",
    "DescribeOutput",
    "NeighborsOutput",
    "ResolveOutput",
    "ResolveCandidate",
    "ResolveStatus",
    "NodeRef",
    "NodeFilter",
    "EdgeFilter",
    "StructuredHint",
    "set_hints_enabled",
]

DeclarationSymbolKind = Literal["class", "interface", "enum", "record", "annotation", "method", "constructor"]

# Closed value taxonomies surfaced to MCP consumers as enums. Sources of truth:
#   Role         — VALID_ROLES in java_ontology.py + the "OTHER" inference fallback (ast_java.infer_role)
#   Framework    — hardcoded literals across ast_java.py / build_ast_graph.py
#   SourceLayer  — exhaustive classifier build_ast_graph._client_source_layer / _producer_source_layer
#   ClientKind   — VALID_CLIENT_KINDS in java_ontology.py (every producer validated at index time)
#   ProducerKind — VALID_PRODUCER_KINDS in java_ontology.py (every producer validated at index time)
# Keep these in sync with the indexing-side taxonomies if they change.
Role = Literal[
    "CONTROLLER", "SERVICE", "REPOSITORY", "COMPONENT", "CONFIG",
    "ENTITY", "CLIENT", "MAPPER", "DTO", "OTHER",
]
Framework = Literal["spring_mvc", "webflux", "kafka", "rabbitmq", "jms", "stream", "feign", ""]
SourceLayer = Literal["builtin", "layer_a_meta", "layer_b_ann", "layer_b_fqn", "layer_c_source"]
ClientKind = Literal["feign_method", "rest_template", "web_client"]
ProducerKind = Literal["kafka_send", "stream_bridge_send"]

# Stored graph edge labels for one-hop neighbors. Composed DECLARES.* and OVERRIDDEN_BY.*
# dot-keys are separate ComposedEdgeType literals (2-hop traversal). Stored OVERRIDES is an EdgeType.
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

ComposedEdgeType = Literal[
    "DECLARES.DECLARES_CLIENT",
    "DECLARES.DECLARES_PRODUCER",
    "DECLARES.EXPOSES",
    "OVERRIDDEN_BY",
    "OVERRIDDEN_BY.DECLARES_CLIENT",
    "OVERRIDDEN_BY.DECLARES_PRODUCER",
    "OVERRIDDEN_BY.EXPOSES",
]

NeighborEdgeType = EdgeType | ComposedEdgeType

_COMPOSED_EDGE_TYPES = frozenset(get_args(ComposedEdgeType))
_MEMBER_COMPOSED_EDGE_TYPES = frozenset(
    k for k in _COMPOSED_EDGE_TYPES if k.startswith("DECLARES.")
)
_OVERRIDE_COMPOSED_EDGE_TYPES = OVERRIDE_AXIS_COMPOSED_EDGE_TYPES

_NEIGHBOR_EDGE_TYPES_ADAPTER = TypeAdapter(
    Annotated[
        list[NeighborEdgeType],
        Field(min_length=1, description="At least one graph edge label or DECLARES.* dot-key"),
    ]
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
    """Increment process-local fail-loud counter and emit one stderr line (PR-FRAME-3).

    The stderr line is gated on ``JAVA_CODEBASE_RAG_FAIL_LOUD`` (default ``"1"`` =
    emit) so the MCP server keeps its operator diagnostic while the agent-facing
    ``jrag`` CLI (which surfaces the same failure as a clean status:error
    envelope) can run it with the diagnostic silenced.
    """
    with _fail_loud_lock:
        _fail_loud_counts[category] = _fail_loud_counts.get(category, 0) + 1
        n = _fail_loud_counts[category]
    if os.environ.get("JAVA_CODEBASE_RAG_FAIL_LOUD", "1") != "0":
        print(f"[filter-frame] fail-loud category={category} count={n}", file=sys.stderr, flush=True)


def filter_frame_counters() -> dict[str, int]:
    """Snapshot of fail-loud counts (tests / local diagnostics; not an MCP tool)."""
    with _fail_loud_lock:
        return dict(_fail_loud_counts)


def _get_sentence_transformer(model_name: str, device: str | None) -> SentenceTransformer:
    global _st_model
    from sentence_transformers import SentenceTransformer

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
    source_layer: SourceLayer | None = None
    role: Role | None = None
    exclude_roles: list[Role] | None = None
    annotation: str | None = None
    capability: str | None = None
    fqn_contains: str | None = None
    symbol_kind: DeclarationSymbolKind | None = None
    symbol_kinds: list[DeclarationSymbolKind] | None = None
    http_method: str | None = Field(
        default=None,
        description="HTTP verb (commonly GET/POST/PUT/DELETE/PATCH; user route annotations may yield others).",
    )
    path_contains: str | None = None
    framework: Framework | None = None
    client_kind: ClientKind | None = Field(
        default=None,
        description="Outbound HTTP client kind: feign_method, rest_template, or web_client.",
    )
    target_service: str | None = None
    target_path_contains: str | None = None
    producer_kind: ProducerKind | None = Field(
        default=None,
        description="Outbound async producer kind: kafka_send or stream_bridge_send.",
    )
    topic_contains: str | None = None


class EdgeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_confidence: float | None = None
    exclude_strategies: list[str] | None = None
    include_strategies: list[str] | None = None
    callee_declaring_role: Role | None = None
    callee_declaring_roles: list[Role] | None = None
    exclude_callee_declaring_roles: list[Role] | None = None

    @model_validator(mode="after")
    def _strategy_axes_mutually_exclusive(self) -> EdgeFilter:
        has_include = bool(self.include_strategies)
        has_exclude = bool(self.exclude_strategies)
        if has_include and has_exclude:
            raise ValueError("include_strategies and exclude_strategies are mutually exclusive")
        return self

    @model_validator(mode="after")
    def _role_axes_mutually_exclusive(self) -> EdgeFilter:
        role_axes = (
            self.callee_declaring_role is not None,
            bool(self.callee_declaring_roles),
            bool(self.exclude_callee_declaring_roles),
        )
        if sum(role_axes) > 1:
            raise ValueError(
                "callee_declaring_role, callee_declaring_roles, and "
                "exclude_callee_declaring_roles are mutually exclusive"
            )
        return self


_NODEFILTER_FIELD_ORDER: tuple[str, ...] = tuple(NodeFilter.model_fields.keys())
_EDGEFILTER_FIELD_ORDER: tuple[str, ...] = tuple(EdgeFilter.model_fields.keys())


# StructuredHint is now defined in graph_types.py and imported above


# Populated EdgeFilter field -> EDGE_SCHEMA attribute name used in Cypher pushdown.
_EDGEFILTER_FIELD_TO_ATTR: dict[str, str] = {
    "min_confidence": "confidence",
    "exclude_strategies": "strategy",
    "include_strategies": "strategy",
    "callee_declaring_role": "callee_declaring_role",
    "callee_declaring_roles": "callee_declaring_role",
    "exclude_callee_declaring_roles": "callee_declaring_role",
}

_ROLE_FILTER_OTHER_FALLBACK_VALUES = frozenset({"SERVICE", "REPOSITORY"})

_NODEFILTER_APPLICABLE_FIELDS: dict[Literal["symbol", "route", "client", "producer"], tuple[str, ...]] = {
    "symbol": (
        "microservice",
        "module",
        "role",
        "exclude_roles",
        "annotation",
        "capability",
        "fqn_contains",
        "symbol_kind",
        "symbol_kinds",
    ),
    "route": (
        "microservice",
        "module",
        "http_method",
        "path_contains",
        "framework",
    ),
    "client": (
        "microservice",
        "module",
        "source_layer",
        "client_kind",
        "target_service",
        "target_path_contains",
        "http_method",
    ),
    "producer": (
        "microservice",
        "module",
        "source_layer",
        "producer_kind",
        "topic_contains",
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


def _populated_edgefilter_fields(ef: EdgeFilter) -> set[str]:
    populated: set[str] = set()
    for field_name in _EDGEFILTER_FIELD_ORDER:
        value = getattr(ef, field_name)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        populated.add(field_name)
    return populated


def _edge_schema_attr_names(edge_type: str) -> set[str]:
    spec = EDGE_SCHEMA.get(edge_type)
    if spec is None:
        return set()
    return {attr.name for attr in spec.attrs}


def _edgefilter_applicability_error(edge_types: list[str], ef: EdgeFilter) -> str | None:
    populated = _populated_edgefilter_fields(ef)
    if not populated:
        return None
    flat_types = [et for et in edge_types if et not in _COMPOSED_EDGE_TYPES]
    composed = [et for et in edge_types if et in _COMPOSED_EDGE_TYPES]
    if composed or flat_types != ["CALLS"]:
        parts: list[str] = []
        if flat_types != ["CALLS"]:
            parts.append(f"stored labels {flat_types!r}")
        if composed:
            parts.append(f"composed keys {composed!r}")
        detail = " and ".join(parts) if parts else "requested edge_types"
        return (
            f"edge_filter requires edge_types=['CALLS'] only; {detail} is not supported — "
            "split into separate neighbors calls"
        )
    for edge_type in flat_types:
        available = _edge_schema_attr_names(edge_type)
        for field_name in _EDGEFILTER_FIELD_ORDER:
            if field_name not in populated:
                continue
            attr = _EDGEFILTER_FIELD_TO_ATTR[field_name]
            if attr not in available:
                return (
                    f"{attr} is not on {edge_type}; restrict edge_types to ['CALLS'] "
                    "or split into two neighbors_v2 calls"
                )
    return None


# _to_structured_hints is now defined in graph_types.py and imported above


def _coerce_edge_filter(
    value: EdgeFilter | dict[str, Any] | str | None,
) -> EdgeFilter | dict[str, Any] | None:
    """Normalize MCP tool input: weak clients sometimes pass JSON-encoded strings."""
    if value is None or isinstance(value, EdgeFilter):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            decoded = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"edge_filter must be a JSON object; invalid JSON: {exc.msg}") from exc
        if decoded is None:
            return None
        if not isinstance(decoded, dict):
            raise ValueError(
                f"edge_filter must decode to a JSON object, got {type(decoded).__name__}"
            )
        return decoded
    return value


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
    filename: str | None = None
    start_line: int | None = None
    score_components: dict[str, float] | None = None
    chunks: int | None = None


# NodeRef is now defined in graph_types.py and imported above


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
            "(DECLARES to member, then that edge) — edge-row counts; navigable via neighbors for type "
            "Symbol origins (`direction=\"out\"` only). For non-static method Symbols, may include "
            "override-axis virtual keys `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, "
            "`OVERRIDDEN_BY.DECLARES_PRODUCER`, `OVERRIDDEN_BY.EXPOSES` (stored `[:OVERRIDES]` "
            "dispatch hop, then terminal edges; navigable via neighbors for method Symbol origins, "
            "`direction=\"out\"` only; composed results include `via_id` in attrs). Plus an "
            "`OVERRIDES` map entry that **merges** stored `[:OVERRIDES]` in/out counts with the "
            "describe-time dispatch-up rollup (per direction `max`, so inbound stored overrides "
            "are not dropped). The stored relationship label `OVERRIDES` **is** also a valid "
            "EdgeType for one-hop neighbors (`direction=\"in\"` from declaration toward overriders)."
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
    advisories: list[str] = Field(default_factory=list, description="Pure informational text with no tool call suggestion")
    hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)


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
    has_more_results: bool | None = Field(
        default=None,
        description="True when additional pages remain beyond offset+limit (more matches exist). "
        "None when unset (e.g. success=False).",
    )
    advisories: list[str] = Field(default_factory=list, description="Pure informational text with no tool call suggestion")
    hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)


class DescribeOutput(BaseModel):
    success: bool
    record: NodeRecord | None = None
    message: str | None = None
    advisories: list[str] = Field(default_factory=list, description="Pure informational text with no tool call suggestion")
    hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)


class NeighborsOutput(BaseModel):
    success: bool
    results: list[Edge] = Field(default_factory=list)
    message: str | None = None
    requested_edge_types: list[str] = Field(
        default_factory=list,
        description="Echo of neighbors(edge_types=...) from the request; empty when success=False.",
    )
    has_more_results: bool | None = Field(
        default=None,
        description="True when additional pages remain beyond offset+limit. None when unset or "
        "when the single-origin CALLS path paginated in SQL (use unfiltered_calls_count / "
        "calls_row_count there).",
    )
    advisories: list[str] = Field(default_factory=list, description="Pure informational text with no tool call suggestion")
    hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)


# Re-exported from resolve_service.py (imported at end of module to avoid circular import)
# resolve_v2, ResolveOutput, ResolveCandidate, ResolveStatus are imported below


# _node_kind_from_id and _resolve_node_kind are now defined in graph_types.py and imported above


def _chunk_id_from_row(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or "")
    start = row.get("start") or {}
    end = row.get("end") or {}
    sb = int(start.get("byte_offset") or 0) if isinstance(start, dict) else 0
    eb = int(end.get("byte_offset") or 0) if isinstance(end, dict) else 0
    return f"{filename}:{sb}:{eb}"


def _row_to_search_hit(row: dict[str, Any], explain: bool = False) -> SearchHit:
    score = float(row.get("_rrf_score") or row.get("_score") or 0.0)
    filename = str(row.get("filename") or "") or None
    start_line: int | None = None
    start = row.get("start")
    if isinstance(start, dict):
        ln = start.get("line")
        if ln is not None:
            try:
                start_line = int(ln)
            except (TypeError, ValueError):
                start_line = None
    chunks = row.get("_chunks_collapsed")
    chunks_int = int(chunks) if chunks is not None and int(chunks) >= 2 else None
    return SearchHit(
        chunk_id=_chunk_id_from_row(row),
        symbol_id=_chunk_to_symbol_id(row),
        fqn=str(row.get("primary_type_fqn")) if row.get("primary_type_fqn") else None,
        score=score,
        snippet=str(row.get("text") or ""),
        microservice=str(row.get("microservice")) if row.get("microservice") else None,
        module=str(row.get("module")) if row.get("module") else None,
        role=str(row.get("role")) if row.get("role") else None,
        filename=filename,
        start_line=start_line,
        score_components=row.get("_score_components") if explain else None,
        chunks=chunks_int,
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
    if f.fqn_contains:
        preds.append("s.fqn CONTAINS $fqn_contains")
        params["fqn_contains"] = f.fqn_contains
    if f.symbol_kind:
        preds.append("s.kind = $symbol_kind")
        params["symbol_kind"] = f.symbol_kind
    if f.symbol_kinds:
        preds.append("s.kind IN $symbol_kinds")
        params["symbol_kinds"] = list(f.symbol_kinds)
    where = f"WHERE {' AND '.join(preds)}" if preds else ""
    return where, params


# _node_ref_from_row is now defined in graph_types.py and imported above


def _load_node_record(
    graph: LadybugGraph, node_id: str, kind: Literal["symbol", "route", "client", "producer"],
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
    zero there). Stored ``[:OVERRIDES]`` edges contribute real ``in``/``out`` from LadybugDB;
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
    graph: LadybugGraph, node_id: str, *, kind: str, row: dict[str, Any]
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
        if f.fqn_contains and f.fqn_contains not in fqn_val:
            return False
        if f.symbol_kind and symbol_kind_val != f.symbol_kind:
            return False
        if f.symbol_kinds and symbol_kind_val not in set(f.symbol_kinds):
            return False
    elif kind == "route":
        if f.http_method and str(row.get("method") or "") != f.http_method:
            return False
        if f.path_contains:
            path = str(row.get("path") or "")
            if f.path_contains not in path:
                return False
        if f.framework and str(row.get("framework") or "") != f.framework:
            return False
    elif kind == "client":
        if f.client_kind and str(row.get("client_kind") or "") != f.client_kind:
            return False
        if f.target_service and str(row.get("target_service") or "") != f.target_service:
            return False
        if f.target_path_contains:
            path = str(row.get("path") or "")
            if f.target_path_contains not in path:
                return False
        if f.http_method and str(row.get("method") or "") != f.http_method:
            return False
    else:
        if f.producer_kind and str(row.get("producer_kind") or "") != f.producer_kind:
            return False
        if f.topic_contains:
            topic = str(row.get("topic") or "")
            if f.topic_contains not in topic:
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
    explain: bool = False,
    graph: LadybugGraph | None = None,
    dedup: bool = True,
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
                advisories=[],
                limit=None,
                offset=None,
            )
        if nf and (err := _nodefilter_applicability_error("symbol", nf)):
            _log_fail_loud("applicability")
            return SearchOutput(success=False, message=err, advisories=[], limit=None, offset=None)
        if run_search is None:
            # Graph-only install (no torch/lancedb): the vector stack is absent. Return a
            # clean failure rather than crashing so the server keeps serving graph tools.
            return SearchOutput(
                success=False,
                message="Vector search unavailable: graph-only mode (vector stack not installed).",
                advisories=[],
                limit=None,
                offset=None,
            )
        # hybrid + table='all' is unsupported (hybrid fuses vector+FTS on ONE
        # table); fail fast with a clean envelope BEFORE loading the embedding
        # model. run_search also guards this — this is the user-facing fast path.
        if hybrid and table == "all":
            return SearchOutput(
                success=False,
                message="hybrid search requires a single table; use java, sql, or yaml (not all)",
                advisories=[],
                limit=None,
                offset=None,
            )
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

        # Graceful fallback: if hybrid=True and FTS index is missing (old index),
        # retry with hybrid=False and return vector-only results with an advisory.
        advisories: list[str] = []
        try:
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
                # Push the NodeFilter structural predicates into the LanceDB query so
                # they apply BEFORE pagination (issue #353) — previously they were only
                # a post-filter on the already-paginated page, which could shrink or
                # empty filtered pages even when many matches existed deeper in the
                # ranking. _node_matches_filter below still re-checks every row (it
                # covers the non-pushdownable fields and is the contract guarantee).
                role=nf.role if nf else None,
                module=nf.module if nf else None,
                microservice=nf.microservice if nf else None,
                capability=nf.capability if nf else None,
                exclude_roles=nf.exclude_roles if nf else None,
                dedup_by_fqn=dedup,
            )
        except Exception as exc:
            # Check if this is a missing-FTS error (old index built before PR-SEARCH-3)
            exc_text = str(exc).lower()
            is_fts_missing = "full text search" in exc_text or "inverted index" in exc_text
            if hybrid and is_fts_missing:
                # Retry with vector-only search
                rows = run_search(
                    query,
                    uri=uri,
                    table_keys=table_keys,
                    hybrid=False,  # Fallback to vector-only
                    limit=limit,
                    offset=offset,
                    path_substring=path_contains,
                    model_name=model_name,
                    device=device,
                    model=model,
                    role=nf.role if nf else None,
                    module=nf.module if nf else None,
                    microservice=nf.microservice if nf else None,
                    capability=nf.capability if nf else None,
                    exclude_roles=nf.exclude_roles if nf else None,
                    dedup_by_fqn=dedup,
                )
                advisories.append(
                    f"hybrid unavailable on table '{table}' (FTS index missing on this index built before "
                    f"PR-SEARCH-3); fell back to vector-only — reindex to enable hybrid"
                )
            else:
                # Non-FTS error: surface as structured failure
                raise
        hits: list[SearchHit] = []
        for row in rows:
            if path_contains and path_contains not in str(row.get("filename") or ""):
                continue
            if nf:
                row_kind = "symbol"
                if not _node_matches_filter(row_kind, row, nf):
                    continue
            hits.append(_row_to_search_hit(row, explain=explain))
        hint_payload = {
            "success": True,
            "results": [h.model_dump() for h in hits],
            "limit": limit,
            "offset": offset,
        }
        raw_struct, raw_advisories = _hints_or_skip("search", hint_payload)
        return SearchOutput(
            success=True,
            results=hits,
            limit=limit,
            offset=offset,
            advisories=advisories + raw_advisories,  # Merge fallback + hints advisories
            hints_structured=_to_structured_hints(raw_struct),
        )
    except Exception as exc:
        return SearchOutput(success=False, message=str(exc), advisories=[], limit=None, offset=None)


def find_v2(
    kind: Literal["symbol", "route", "client", "producer"],
    filter: NodeFilter | dict[str, Any] | str,
    limit: int = 25,
    offset: int = 0,
    graph: LadybugGraph | None = None,
) -> FindOutput:
    try:
        g = graph or LadybugGraph.get()
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
                advisories=[],
                limit=None,
                offset=None,
            )
        if err := _nodefilter_applicability_error(kind, nf):
            _log_fail_loud("applicability")
            return FindOutput(success=False, message=err, advisories=[], limit=None, offset=None)
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
                path_contains=nf.path_contains,
                method=nf.http_method,
                limit=max(500, fetch_cap),
            )
            rows = [r for r in rows if _node_matches_filter("route", r, nf)]
        elif kind == "client":
            rows = g.list_clients(
                microservice=nf.microservice,
                client_kind=nf.client_kind,
                target_service=nf.target_service,
                path_contains=nf.target_path_contains,
                method=nf.http_method,
                limit=max(500, fetch_cap),
            )
            rows = [r for r in rows if _node_matches_filter("client", r, nf)]
        else:
            rows = g.list_producers(
                microservice=nf.microservice,
                producer_kind=nf.producer_kind,
                topic_contains=nf.topic_contains,
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
            # exclude_none: this dict feeds generate_hints (which reads fields
            # defensively via .get), not the tool result (FindOutput below holds the
            # pydantic objects). Drop null fields -- including the NodeRef.name field
            # that is None for every structured ref -- to match filter_dump above and
            # avoid spurious "name": null noise in the hint input.
            "results": [r.model_dump(exclude_none=True) for r in refs],
            "limit": limit,
            "offset": offset,
            "filter": filter_dump,
            "has_more_results": has_more_results,
        }
        raw_struct, raw_advisories = _hints_or_skip("find", hint_payload)
        return FindOutput(
            success=True,
            results=refs,
            limit=limit,
            offset=offset,
            has_more_results=has_more_results,
            advisories=raw_advisories,
            hints_structured=_to_structured_hints(raw_struct),
        )
    except Exception as exc:
        return FindOutput(success=False, message=str(exc), advisories=[], limit=None, offset=None)


_DESCRIBE_UCS_ID_MESSAGE = (
    "UnresolvedCallSite ids (ucs:…) are not describable — use describe(caller_method_id) "
    "for record.data.unresolved_call_sites, neighbors(..., include_unresolved=True), "
    "or java-codebase-rag unresolved-calls list --method-id <caller_id>"
)


def describe_v2(
    id: str | None = None,
    fqn: str | None = None,
    graph: LadybugGraph | None = None,
) -> DescribeOutput:
    try:
        g = graph or LadybugGraph.get()
        has_id = bool(id and str(id).strip())
        has_fqn = bool(fqn and str(fqn).strip())
        if not has_id and not has_fqn:
            return DescribeOutput(success=False, message="id or fqn required")
        if has_id and str(id).strip().startswith("ucs:"):
            return DescribeOutput(success=False, message=_DESCRIBE_UCS_ID_MESSAGE)
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
                return DescribeOutput(success=False, message=f"No Symbol found for fqn='{fqn_val}'")
            node_id = str(rows[0]["id"] or "")
            if len(rows) > 1:
                hint_message = (
                    "multiple symbols share this FQN; use "
                    f"resolve(identifier={fqn_val!r}, hint_kind='symbol') to list candidates with reasons, "
                    "then describe(id=...) on the chosen node"
                )
        kind = _resolve_node_kind(g, node_id)
        if kind == "unresolved_call_site":
            return DescribeOutput(success=False, message=_DESCRIBE_UCS_ID_MESSAGE, advisories=[])
        row = _load_node_record(g, node_id, kind)
        if row is None:
            return DescribeOutput(success=False, message=f"No node found for `{node_id}`", advisories=[])
        ref = _node_ref_from_row(kind, row)
        edge_summary = _edge_summary_for_node(g, node_id, kind=kind, row=row)
        data = dict(row)
        if kind == "symbol" and str(row.get("kind") or "") in _METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP:
            inline, total = g.unresolved_sites_for_describe(node_id)
            if total > 0:
                data["unresolved_call_sites_total"] = total
                data["unresolved_call_sites"] = [
                    {
                        "line": int(r.get("line") or 0),
                        "reason": str(r.get("reason") or ""),
                        "callee_simple": str(r.get("callee_simple") or ""),
                        "receiver_expr": str(r.get("receiver_expr") or ""),
                    }
                    for r in inline
                ]
                if total > len(inline):
                    data["unresolved_call_sites_footer"] = (
                        f"{total} unresolved call sites — see "
                        f"java-codebase-rag unresolved-calls list --method-id {node_id} for the full list"
                    )
        record = NodeRecord(id=ref.id, kind=kind, fqn=ref.fqn, data=data, edge_summary=edge_summary)
        raw_struct, raw_advisories = _hints_or_skip("describe", {"success": True, "record": record.model_dump()})
        return DescribeOutput(
            success=True,
            record=record,
            message=hint_message,
            advisories=raw_advisories,
            hints_structured=_to_structured_hints(raw_struct),
        )
    except ValueError as exc:
        return DescribeOutput(success=False, message=str(exc), advisories=[])
    except Exception as exc:
        return DescribeOutput(success=False, message=str(exc), advisories=[])




# Per-edge-type attribute columns selected by the generic (flat-label) neighbors
# query (issue #356). RETURNing a fixed superset of columns regardless of which
# edge type matched is the typed-union RETURN anti-pattern: a stricter binder
# (e.g. Kùzu) errors when a RETURNed column does not exist on the matched type.
# Selecting columns per edge type keeps the query portable; _neighbor_edge_attrs
# still drops None/"" so each edge exposes only the attrs that exist for its type.
# Aligned with the REL TABLE schemas in build_ast_graph.py.
_FLAT_EDGE_ATTR_COLUMNS: dict[str, tuple[str, ...]] = {
    "CALLS": ("confidence", "strategy", "source", "call_site_line", "call_site_byte", "arg_count", "resolved"),
    "HTTP_CALLS": ("confidence", "strategy", "match"),
    "ASYNC_CALLS": ("confidence", "strategy", "match"),
    "EXPOSES": ("confidence", "strategy"),
    "DECLARES_CLIENT": ("confidence", "strategy"),
    "DECLARES_PRODUCER": ("confidence", "strategy"),
    "INJECTS": ("mechanism", "annotation", "field_or_param", "resolved"),
    "EXTENDS": ("resolved",),
    "IMPLEMENTS": ("resolved",),
    "DECLARES": (),
    "OVERRIDES": (),
}


def _neighbor_edge_attrs(row: dict[str, Any]) -> dict[str, Any]:
    attrs = {
        k: v
        for k, v in row.items()
        if k not in {"other_id", "edge_type", "stored_edge_type"}
        and v not in (None, "")
    }
    attrs.setdefault("row_kind", "resolved")
    return attrs


def _unresolved_site_to_edge(origin_id: str, row: dict[str, Any]) -> Edge:
    ucs_id = str(row.get("id") or "")
    callee = str(row.get("callee_simple") or "")
    line = int(row.get("call_site_line") or 0)
    byte = int(row.get("call_site_byte") or 0)
    return Edge(
        origin_id=origin_id,
        edge_type="CALLS",
        direction="out",
        other=NodeRef(id=ucs_id, kind="unresolved_call_site", fqn="", name=callee),
        attrs={
            "row_kind": "unresolved_call_site",
            "unresolved_call_site_id": ucs_id,
            "reason": str(row.get("reason") or ""),
            "call_site_line": line,
            "call_site_byte": byte,
            "arg_count": int(row.get("arg_count") or 0),
            "callee_simple": callee,
            "receiver_expr": str(row.get("receiver_expr") or ""),
        },
    )


def _calls_transcript_sort_key(edge: Edge) -> tuple[int, int, int]:
    attrs = edge.attrs or {}
    line = int(attrs.get("call_site_line") or 0)
    byte = int(attrs.get("call_site_byte") or 0)
    kind_rank = 0 if str(attrs.get("row_kind") or "resolved") == "resolved" else 1
    return (line, byte, kind_rank)


def _dedup_call_edges(edges: list[Edge]) -> list[Edge]:
    """Collapse resolved CALLS rows sharing (origin_id, other.id); unresolved rows pass through."""
    resolved: list[Edge] = []
    unresolved: list[Edge] = []
    for e in edges:
        if str((e.attrs or {}).get("row_kind") or "resolved") == "unresolved_call_site":
            unresolved.append(e)
        else:
            resolved.append(e)
    groups: dict[tuple[str, str], list[Edge]] = {}
    for e in resolved:
        key = (e.origin_id, e.other.id)
        groups.setdefault(key, []).append(e)
    collapsed: list[Edge] = []
    for group in groups.values():
        ordered = sorted(group, key=_calls_transcript_sort_key)
        canonical = ordered[0]
        lines = sorted(
            {int((x.attrs or {}).get("call_site_line") or 0) for x in group},
        )
        attrs = dict(canonical.attrs or {})
        attrs["call_site_count"] = len(group)
        attrs["call_site_lines"] = lines
        collapsed.append(canonical.model_copy(update={"attrs": attrs}))
    merged = collapsed + unresolved
    merged.sort(key=_calls_transcript_sort_key)
    return merged


def _edgefilter_pushdown_kwargs(ef: EdgeFilter | None) -> dict[str, Any]:
    if ef is None:
        return {}
    return {
        "min_confidence": ef.min_confidence,
        "include_strategies": ef.include_strategies,
        "exclude_strategies": ef.exclude_strategies,
        "callee_declaring_role": ef.callee_declaring_role,
        "callee_declaring_roles": ef.callee_declaring_roles,
        "exclude_callee_declaring_roles": ef.exclude_callee_declaring_roles,
    }


def _rows_to_call_edges(
    g: Any,
    *,
    origin_id: str,
    direction: Literal["in", "out"],
    rows: list[dict[str, Any]],
    nf: NodeFilter | None,
) -> list[Edge]:
    edges: list[Edge] = []
    for row in rows:
        other_id = str(row.get("other_id") or "")
        other_kind = _resolve_node_kind(g, other_id)
        other_rec = _load_node_record(g, other_id, other_kind)
        if other_rec is None:
            continue
        if nf and (err := _nodefilter_applicability_error(other_kind, nf)):
            _log_fail_loud("applicability")
            raise ValueError(err)
        if not _node_matches_filter(other_kind, other_rec, nf):
            continue
        edges.append(
            Edge(
                origin_id=origin_id,
                edge_type=str(row.get("edge_type") or "CALLS"),
                direction=direction,
                other=_node_ref_from_row(other_kind, other_rec),
                attrs=_neighbor_edge_attrs(row),
            )
        )
    return edges


def _neighbors_calls_for_origin(
    g: Any,
    origin_id: str,
    *,
    direction: Literal["in", "out"],
    nf: NodeFilter | None,
    ef: EdgeFilter | None,
    offset: int,
    limit: int | None,
    include_unresolved: bool = False,
    dedup_calls: bool = False,
) -> list[Edge]:
    pushdown = _edgefilter_pushdown_kwargs(ef)
    needs_full_stream = (
        nf is not None
        or dedup_calls
        or include_unresolved
        or limit is None
    )
    sql_pagination = not needs_full_stream and limit is not None
    if sql_pagination:
        rows = g.neighbor_calls_for_symbol(
            origin_id,
            direction=direction,
            offset=offset,
            limit=limit,
            sql_pagination=True,
            **pushdown,
        )
        return _rows_to_call_edges(g, origin_id=origin_id, direction=direction, rows=rows, nf=nf)
    rows = g.neighbor_calls_for_symbol(
        origin_id,
        direction=direction,
        offset=0,
        limit=None,
        sql_pagination=False,
        **pushdown,
    )
    edges = _rows_to_call_edges(g, origin_id=origin_id, direction=direction, rows=rows, nf=nf)
    if include_unresolved and direction == "out":
        ucs_rows = g.unresolved_sites_for_caller(origin_id, direction=direction)
        edges.extend(_unresolved_site_to_edge(origin_id, r) for r in ucs_rows)
        edges.sort(key=_calls_transcript_sort_key)
    if dedup_calls:
        edges = _dedup_call_edges(edges)
    if limit is None:
        return edges
    return edges[offset : offset + limit]


def _composed_axis_origin_error(
    *,
    symbol_kind: str,
    modifiers: list[str] | None,
    declares_composed: list[str],
    override_composed: list[str],
) -> str | None:
    """Fail-fast origin gate for composed DECLARES.* vs OVERRIDDEN_BY.* families."""
    if declares_composed and symbol_kind not in _TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP:
        return f"Composed edge types ({declares_composed[0]}) require a type Symbol origin"
    if override_composed:
        key = override_composed[0]
        mods = modifiers or []
        if symbol_kind == "constructor":
            return (
                f"Composed edge types ({key}) require a non-static method Symbol origin "
                "(constructors are not supported)"
            )
        if symbol_kind not in _METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP:
            return f"Composed edge types ({key}) require a method Symbol origin"
        if "static" in mods:
            return (
                f"Composed edge types ({key}) require a non-static method Symbol origin "
                "(static methods are not supported)"
            )
    return None


@validate_call(config={"arbitrary_types_allowed": True})
def neighbors_v2(
    ids: str | list[str],
    # Required fields are intentional: direct Python calls and MCP-bound calls
    # share the same validation contract through @validate_call.
    direction: Literal["in", "out"] = Field(...),
    edge_types: list[NeighborEdgeType] = Field(...),
    limit: int = 25,
    offset: int = 0,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    edge_filter: EdgeFilter | dict[str, Any] | str | None = None,
    include_unresolved: bool = False,
    dedup_calls: bool = False,
    graph: Any | None = None,
) -> NeighborsOutput:
    try:
        validated_types = _NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(edge_types)
        requested_edge_types = list(dict.fromkeys(validated_types))
        flat_labels = [et for et in requested_edge_types if et not in _COMPOSED_EDGE_TYPES]
        composed_keys = [et for et in requested_edge_types if et in _COMPOSED_EDGE_TYPES]
        declares_composed = [k for k in composed_keys if k in _MEMBER_COMPOSED_EDGE_TYPES]
        override_composed = [k for k in composed_keys if k in _OVERRIDE_COMPOSED_EDGE_TYPES]
        ordered_composed = declares_composed + override_composed
        g = graph or LadybugGraph.get()
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
                advisories=[],
                requested_edge_types=[],
            )
        try:
            raw_edge_filter = _coerce_edge_filter(edge_filter)
            ef = (
                EdgeFilter.model_validate(raw_edge_filter)
                if raw_edge_filter is not None and not isinstance(raw_edge_filter, EdgeFilter)
                else raw_edge_filter
            )
        except ValidationError as exc:
            _log_fail_loud("edge_filter")
            return NeighborsOutput(
                success=False,
                message=_filter_validation_error_message(exc),
                advisories=[],
                requested_edge_types=[],
            )
        except ValueError as exc:
            _log_fail_loud("edge_filter")
            return NeighborsOutput(success=False, message=str(exc), requested_edge_types=[])
        if include_unresolved and ef is not None:
            return NeighborsOutput(
                success=False,
                message=(
                    "include_unresolved=True is incompatible with edge_filter; "
                    "UnresolvedCallSite rows have no edge attributes to filter on"
                ),
                requested_edge_types=requested_edge_types,
            )
        if include_unresolved and requested_edge_types != ["CALLS"]:
            return NeighborsOutput(
                success=False,
                message="include_unresolved requires edge_types=['CALLS']",
                requested_edge_types=requested_edge_types,
            )
        if include_unresolved and direction != "out":
            return NeighborsOutput(
                success=False,
                message='include_unresolved requires direction="out"',
                requested_edge_types=requested_edge_types,
            )
        if ef and (err := _edgefilter_applicability_error(requested_edge_types, ef)):
            _log_fail_loud("edge_filter")
            return NeighborsOutput(
                success=False,
                message=err,
                requested_edge_types=requested_edge_types,
            )
        if composed_keys and direction != "out":
            return NeighborsOutput(
                success=False,
                message='Composed edge types require direction="out"',
                requested_edge_types=requested_edge_types,
            )
        use_calls_path = flat_labels == ["CALLS"] and not composed_keys
        origins = [ids] if isinstance(ids, str) else list(ids)
        results: list[Edge] = []
        unfiltered_calls_count: int | None = None
        unresolved_count: int | None = None
        calls_row_count: int | None = None
        if use_calls_path and len(origins) == 1 and direction == "out":
            unresolved_count = g.count_unresolved_for_caller(origins[0])
            calls_row_count = g.count_calls_for_symbol(origins[0], direction=direction)
        for origin_id in origins:
            origin_kind = _resolve_node_kind(g, origin_id)
            if ordered_composed:
                if origin_kind != "symbol":
                    first_key = ordered_composed[0]
                    axis_msg = (
                        f"Composed edge types ({first_key}) require a method Symbol origin"
                        if first_key in _OVERRIDE_COMPOSED_EDGE_TYPES
                        else f"Composed edge types ({first_key}) require a type Symbol origin"
                    )
                    return NeighborsOutput(
                        success=False,
                        message=axis_msg,
                        requested_edge_types=requested_edge_types,
                    )
                origin_row = _load_node_record(g, origin_id, "symbol")
                sym_kind = str((origin_row or {}).get("kind") or "")
                mods_raw = (origin_row or {}).get("modifiers")
                mods = mods_raw if isinstance(mods_raw, list) else None
                if err := _composed_axis_origin_error(
                    symbol_kind=sym_kind,
                    modifiers=mods,
                    declares_composed=declares_composed,
                    override_composed=override_composed,
                ):
                    return NeighborsOutput(
                        success=False,
                        message=err,
                        requested_edge_types=requested_edge_types,
                    )
            if use_calls_path:
                paginate_in_sql = (
                    len(origins) == 1
                    and nf is None
                    and not include_unresolved
                    and not dedup_calls
                )
                try:
                    origin_edges = _neighbors_calls_for_origin(
                        g,
                        origin_id,
                        direction=direction,
                        nf=nf,
                        ef=ef,
                        offset=offset if paginate_in_sql else 0,
                        limit=limit if paginate_in_sql else None,
                        include_unresolved=include_unresolved,
                        dedup_calls=dedup_calls,
                    )
                except ValueError as exc:
                    return NeighborsOutput(
                        success=False,
                        message=str(exc),
                        requested_edge_types=requested_edge_types,
                    )
                if (
                    ef is not None
                    and ef.callee_declaring_role in _ROLE_FILTER_OTHER_FALLBACK_VALUES
                    and not origin_edges
                    and unfiltered_calls_count is None
                ):
                    unfiltered_calls_count = g.count_calls_for_symbol(origin_id, direction=direction)
                results.extend(origin_edges)
                continue
            if flat_labels:
                # Select attribute columns per edge type (issue #356). A single
                # multi-label query RETURNing a fixed column superset references
                # columns that don't exist on every matched type — the typed-union
                # RETURN anti-pattern, which errors on stricter binders (e.g. Kùzu).
                # Run one single-label query per type, RETURNing only that type's
                # columns, and merge the rows. `label(e) = $label` scalar equality
                # (not `label(e) IN [...]`) per the AGENTS.md Cypher note.
                rows: list[dict[str, Any]] = []
                match_clause = "MATCH (a)-[e]->(b)" if direction == "out" else "MATCH (a)<-[e]-(b)"
                for label in flat_labels:
                    cols = _FLAT_EDGE_ATTR_COLUMNS.get(label, ())
                    select = "b.id AS other_id, label(e) AS edge_type"
                    if cols:
                        select += ", " + ", ".join(f"e.{c} AS {c}" for c in cols)
                    rows.extend(
                        g._rows(  # noqa: SLF001
                            f"{match_clause} WHERE a.id = $id AND label(e) = $label RETURN {select}",
                            {"id": origin_id, "label": label},
                        )
                    )
                for row in rows:
                    other_id = str(row.get("other_id") or "")
                    other_kind = _resolve_node_kind(g, other_id)
                    other_rec = _load_node_record(g, other_id, other_kind)
                    if other_rec is None:
                        continue
                    if nf and (err := _nodefilter_applicability_error(other_kind, nf)):
                        _log_fail_loud("applicability")
                        return NeighborsOutput(
                            success=False, message=err, requested_edge_types=[]
                        )
                    if not _node_matches_filter(other_kind, other_rec, nf):
                        continue
                    results.append(
                        Edge(
                            origin_id=origin_id,
                            edge_type=str(row.get("edge_type") or ""),
                            direction=direction,
                            other=_node_ref_from_row(other_kind, other_rec),
                            attrs=_neighbor_edge_attrs(row),
                        )
                    )
            for composed_key in ordered_composed:
                if composed_key in _MEMBER_COMPOSED_EDGE_TYPES:
                    traversal_rows = g.member_edge_traversal_for(origin_id, composed_key)
                else:
                    traversal_rows = g.override_axis_traversal_for(origin_id, composed_key)
                for row in traversal_rows:
                    other_id = str(row.get("other_id") or "")
                    other_kind = _resolve_node_kind(g, other_id)
                    other_rec = _load_node_record(g, other_id, other_kind)
                    if other_rec is None:
                        continue
                    if nf and (err := _nodefilter_applicability_error(other_kind, nf)):
                        _log_fail_loud("applicability")
                        return NeighborsOutput(
                            success=False, message=err, requested_edge_types=[]
                        )
                    if not _node_matches_filter(other_kind, other_rec, nf):
                        continue
                    if composed_key == "OVERRIDDEN_BY":
                        edge_attrs: dict[str, Any] = {}
                    else:
                        edge_attrs = _neighbor_edge_attrs(row)
                    results.append(
                        Edge(
                            origin_id=origin_id,
                            edge_type=composed_key,
                            direction="out",
                            other=_node_ref_from_row(other_kind, other_rec),
                            attrs=edge_attrs,
                        )
                    )
        if use_calls_path and len(origins) > 1:
            sliced = results[offset : offset + limit]
            neighbors_has_more = len(results) > offset + limit
        elif use_calls_path:
            # Single-origin CALLS path. When paginate_in_sql is True the SQL did
            # the OFFSET/LIMIT and the row/unfiltered counts carry the has-more
            # signal, so this field stays None (unknown). When paginate_in_sql is
            # False (a node_filter is set, include_unresolved, or dedup_calls) we
            # loaded the FULL matching set with no pushdown, so the client already
            # has every edge -> False (not None), so a paging client need not probe.
            sliced = results
            neighbors_has_more = None if paginate_in_sql else False
        else:
            sliced = results[offset : offset + limit]
            neighbors_has_more = len(results) > offset + limit
        first_origin = origins[0]
        origin_kind = _resolve_node_kind(g, first_origin)
        subject_record = _load_node_record(g, first_origin, origin_kind)
        neigh_payload = {
            "success": True,
            "results": [e.model_dump(exclude_none=True) for e in sliced],
            "requested_edge_types": requested_edge_types,
            "requested_direction": direction,
            "offset": offset,
            "origin_id": first_origin,
            "subject_record": subject_record,
            "node_filter": nf.model_dump(exclude_none=True) if nf else None,
            "edge_filter": ef.model_dump(exclude_none=True) if ef else None,
            "edge_filter_provided": ef is not None,
            "include_unresolved": include_unresolved,
            "dedup_calls": dedup_calls,
            "unfiltered_calls_count": unfiltered_calls_count,
            "unresolved_count": unresolved_count,
            "calls_row_count": calls_row_count,
        }
        raw_struct, raw_advisories = _hints_or_skip("neighbors", neigh_payload)
        return NeighborsOutput(
            success=True,
            results=sliced,
            requested_edge_types=requested_edge_types,
            has_more_results=neighbors_has_more,
            advisories=raw_advisories,
            hints_structured=_to_structured_hints(raw_struct),
        )
    except ValidationError:
        raise
    except Exception as exc:
        return NeighborsOutput(success=False, message=str(exc), advisories=[], requested_edge_types=[])


# Re-export resolve symbols from resolve_service.py (imported here to avoid circular import)
from resolve_service import (  # noqa: E402
    ResolveCandidate,
    ResolveOutput,
    ResolveStatus,
    resolve_v2,
)
