from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, validate_call
from sentence_transformers import SentenceTransformer

from index_common import SBERT_MODEL
from kuzu_queries import KuzuGraph
from search_lancedb import TABLES, run_search

_NEIGHBOR_EDGE_TYPES_ADAPTER = TypeAdapter(
    Annotated[list[str], Field(min_length=1, description="At least one graph edge label")]
)

_st_lock = threading.Lock()
_st_model: SentenceTransformer | None = None


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
    microservice: str | None = None
    module: str | None = None
    source_layer: str | None = None
    role: str | None = None
    exclude_roles: list[str] | None = None
    annotation: str | None = None
    capability: str | None = None
    fqn_prefix: str | None = None
    http_method: str | None = None
    path_prefix: str | None = None
    framework: str | None = None
    client_kind: str | None = None
    target_service: str | None = None
    target_path_prefix: str | None = None
    client_method: str | None = None


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
    kind: Literal["symbol", "route", "client"]
    fqn: str
    microservice: str | None = None
    module: str | None = None
    role: str | None = None


class NodeRecord(BaseModel):
    id: str
    kind: Literal["symbol", "route", "client"]
    fqn: str
    data: dict[str, Any] = Field(default_factory=dict)
    edge_summary: dict[str, dict[str, int]] | None = None


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


class FindOutput(BaseModel):
    success: bool
    results: list[NodeRef] = Field(default_factory=list)
    message: str | None = None


class DescribeOutput(BaseModel):
    success: bool
    record: NodeRecord | None = None
    message: str | None = None


class NeighborsOutput(BaseModel):
    success: bool
    results: list[Edge] = Field(default_factory=list)
    message: str | None = None


def _node_kind_from_id(id_str: str) -> Literal["symbol", "route", "client"]:
    if id_str.startswith("sym:"):
        return "symbol"
    if id_str.startswith("route:") or id_str.startswith("r:"):
        return "route"
    if id_str.startswith("client:") or id_str.startswith("c:"):
        return "client"
    raise ValueError(f"Unknown id prefix for `{id_str}`")


def _resolve_node_kind(graph: KuzuGraph, node_id: str) -> Literal["symbol", "route", "client"]:
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
    where = f"WHERE {' AND '.join(preds)}" if preds else ""
    return where, params


def _node_ref_from_row(kind: Literal["symbol", "route", "client"], row: dict[str, Any]) -> NodeRef:
    if kind == "symbol":
        fqn = str(row.get("fqn") or "")
        role = str(row.get("role") or "") or None
    elif kind == "route":
        method = str(row.get("method") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{method} {path}".strip()
        role = None
    else:
        method = str(row.get("method") or "")
        target = str(row.get("target_service") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{target} {method} {path}".strip()
        role = None
    return NodeRef(
        id=str(row.get("id") or ""),
        kind=kind,
        fqn=fqn,
        microservice=str(row.get("microservice") or "") or None,
        module=str(row.get("module") or "") or None,
        role=role,
    )


def _load_node_record(graph: KuzuGraph, node_id: str, kind: Literal["symbol", "route", "client"]) -> dict[str, Any] | None:
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
    else:
        projection = (
            "n.id AS id, n.client_kind AS client_kind, n.target_service AS target_service, "
            "n.method AS method, n.path AS path, n.path_template AS path_template, "
            "n.path_regex AS path_regex, n.member_fqn AS member_fqn, n.member_id AS member_id, "
            "n.microservice AS microservice, n.module AS module, n.filename AS filename, "
            "n.start_line AS start_line, n.end_line AS end_line, n.resolved AS resolved, "
            "n.source_layer AS source_layer"
        )
        label = "Client"
    rows = graph._rows(f"MATCH (n:{label}) WHERE n.id = $id RETURN {projection}", {"id": node_id})  # noqa: SLF001
    if not rows:
        return None
    return rows[0]


def _edge_summary_for_node(graph: KuzuGraph, node_id: str) -> dict[str, dict[str, int]]:
    return graph.edge_counts_for(node_id)


def _node_matches_filter(kind: Literal["symbol", "route", "client"], row: dict[str, Any], f: NodeFilter | None) -> bool:
    if f is None:
        return True
    if f.microservice and str(row.get("microservice") or "") != f.microservice:
        return False
    if f.module and str(row.get("module") or "") != f.module:
        return False
    if kind == "client" and f.source_layer and str(row.get("source_layer") or "") != f.source_layer:
        return False
    if kind == "symbol":
        role = str(row.get("role") or "")
        fqn_val = str(row.get("fqn") or row.get("primary_type_fqn") or "")
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
    elif kind == "route":
        if f.http_method and str(row.get("method") or "") != f.http_method:
            return False
        if f.path_prefix:
            path = str(row.get("path") or "")
            if not path.startswith(f.path_prefix):
                return False
        if f.framework and str(row.get("framework") or "") != f.framework:
            return False
    else:
        if f.client_kind and str(row.get("client_kind") or "") != f.client_kind:
            return False
        if f.target_service and str(row.get("target_service") or "") != f.target_service:
            return False
        if f.target_path_prefix:
            path = str(row.get("path") or "")
            if not path.startswith(f.target_path_prefix):
                return False
        if f.client_method and str(row.get("method") or "") != f.client_method:
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
        model_name = os.environ.get("SBERT_MODEL", SBERT_MODEL)
        device = os.environ.get("SBERT_DEVICE") or None
        model = _get_sentence_transformer(model_name, device)
        uri = os.environ.get("LANCEDB_URI", "./lancedb_data")
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
        raw_filter = _coerce_filter(filter)
        nf = (
            NodeFilter.model_validate(raw_filter)
            if raw_filter is not None and not isinstance(raw_filter, NodeFilter)
            else raw_filter
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
        return SearchOutput(success=True, results=hits)
    except Exception as exc:
        return SearchOutput(success=False, message=str(exc))


def find_v2(
    kind: Literal["symbol", "route", "client"],
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
        nf = NodeFilter.model_validate(raw_filter) if not isinstance(raw_filter, NodeFilter) else raw_filter
        if kind == "symbol":
            where, params = _symbol_where_from_filter(nf)
            params["lim"] = int(limit) + int(offset)
            rows = g._rows(  # noqa: SLF001
                f"MATCH (s:Symbol) {where} RETURN s.id AS id, s.fqn AS fqn, s.microservice AS microservice, "
                "s.module AS module, s.role AS role ORDER BY s.fqn LIMIT $lim",
                params,
            )
            rows = rows[offset : offset + limit]
        elif kind == "route":
            rows = g.list_routes(
                microservice=nf.microservice,
                framework=nf.framework,
                path_prefix=nf.path_prefix,
                method=nf.http_method,
                limit=max(500, limit + offset),
            )
            rows = [r for r in rows if _node_matches_filter("route", r, nf)]
            rows = rows[offset : offset + limit]
        else:
            rows = g.list_clients(
                microservice=nf.microservice,
                client_kind=nf.client_kind,
                target_service=nf.target_service,
                path_prefix=nf.target_path_prefix,
                method=nf.client_method,
                limit=max(500, limit + offset),
            )
            rows = [r for r in rows if _node_matches_filter("client", r, nf)]
            rows = rows[offset : offset + limit]
        return FindOutput(success=True, results=[_node_ref_from_row(kind, r) for r in rows])
    except Exception as exc:
        return FindOutput(success=False, message=str(exc))


def describe_v2(id: str, graph: KuzuGraph | None = None) -> DescribeOutput:
    try:
        g = graph or KuzuGraph.get()
        kind = _resolve_node_kind(g, id)
        row = _load_node_record(g, id, kind)
        if row is None:
            return DescribeOutput(success=False, message=f"No node found for `{id}`")
        ref = _node_ref_from_row(kind, row)
        edge_summary = _edge_summary_for_node(g, id)
        return DescribeOutput(
            success=True,
            record=NodeRecord(id=ref.id, kind=kind, fqn=ref.fqn, data=row, edge_summary=edge_summary),
        )
    except ValueError as exc:
        return DescribeOutput(success=False, message=str(exc))
    except Exception as exc:
        return DescribeOutput(success=False, message=str(exc))


@validate_call(config={"arbitrary_types_allowed": True})
def neighbors_v2(
    ids: str | list[str],
    # Required fields are intentional: direct Python calls and MCP-bound calls
    # share the same validation contract through @validate_call.
    direction: Literal["in", "out"] = Field(...),
    edge_types: list[str] = Field(...),
    limit: int = 25,
    offset: int = 0,
    filter: NodeFilter | dict[str, Any] | str | None = None,
    graph: Any | None = None,
) -> NeighborsOutput:
    try:
        _NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(edge_types)
        g = graph or KuzuGraph.get()
        raw_filter = _coerce_filter(filter)
        nf = (
            NodeFilter.model_validate(raw_filter)
            if raw_filter is not None and not isinstance(raw_filter, NodeFilter)
            else raw_filter
        )
        origins = [ids] if isinstance(ids, str) else list(ids)
        results: list[Edge] = []
        for origin_id in origins:
            _resolve_node_kind(g, origin_id)
            if direction == "out":
                rows = g._rows(  # noqa: SLF001
                    "MATCH (a)-[e]->(b) WHERE a.id = $id AND label(e) IN $edge_types "
                    "RETURN b.id AS other_id, label(e) AS edge_type, e.confidence AS confidence, "
                    "e.strategy AS strategy, e.match AS match, e.mechanism AS mechanism, "
                    "e.annotation AS annotation, e.field_or_param AS field_or_param, "
                    "e.source AS source, e.call_site_line AS call_site_line, "
                    "e.call_site_byte AS call_site_byte, e.arg_count AS arg_count, "
                    "e.resolved AS resolved",
                    {"id": origin_id, "edge_types": edge_types},
                )
            else:
                rows = g._rows(  # noqa: SLF001
                    "MATCH (a)<-[e]-(b) WHERE a.id = $id AND label(e) IN $edge_types "
                    "RETURN b.id AS other_id, label(e) AS edge_type, e.confidence AS confidence, "
                    "e.strategy AS strategy, e.match AS match, e.mechanism AS mechanism, "
                    "e.annotation AS annotation, e.field_or_param AS field_or_param, "
                    "e.source AS source, e.call_site_line AS call_site_line, "
                    "e.call_site_byte AS call_site_byte, e.arg_count AS arg_count, "
                    "e.resolved AS resolved",
                    {"id": origin_id, "edge_types": edge_types},
                )
            for row in rows:
                other_id = str(row.get("other_id") or "")
                other_kind = _resolve_node_kind(g, other_id)
                other_rec = _load_node_record(g, other_id, other_kind)
                if other_rec is None:
                    continue
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
        return NeighborsOutput(success=True, results=results[offset : offset + limit])
    except ValidationError:
        raise
    except Exception as exc:
        return NeighborsOutput(success=False, message=str(exc))
