"""Read-only Cypher helpers over the Kuzu AST graph built by `build_ast_graph.py`.

Each function opens a Kuzu connection on demand and returns plain JSON-ish dicts
so the MCP server can serialize them without further mapping.

The Kuzu database is opened read-only and cached per-process. This module is
intentionally dependency-light: nothing here imports LanceDB or sentence-transformers.

Cypher pitfalls (see also ``AGENTS.md``): avoid ``label(e) IN $list`` in ``WHERE`` for
relationship-type filters; use OR of ``label(e) = $param`` with bound parameters.
Typed unions ``-[e:A|B]-`` require every ``RETURN`` column on ``e`` to exist on all
listed rel types, or the binder may fail.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import kuzu

from ast_java import ONTOLOGY_VERSION as _ONTOLOGY_VERSION

log = logging.getLogger(__name__)

# Composed describe / neighbors dot-keys (not stored graph edge labels).
_MEMBER_EDGE_COMPOSED_REL_MAP: tuple[tuple[str, str], ...] = (
    ("DECLARES.DECLARES_CLIENT", "DECLARES_CLIENT"),
    ("DECLARES.DECLARES_PRODUCER", "DECLARES_PRODUCER"),
    ("DECLARES.EXPOSES", "EXPOSES"),
)
_MEMBER_EDGE_COMPOSED_REL_BY_KEY: dict[str, str] = dict(_MEMBER_EDGE_COMPOSED_REL_MAP)

_OVERRIDE_AXIS_COMPOSED_REL_MAP: tuple[tuple[str, str | None], ...] = (
    ("OVERRIDDEN_BY", None),
    ("OVERRIDDEN_BY.DECLARES_CLIENT", "DECLARES_CLIENT"),
    ("OVERRIDDEN_BY.DECLARES_PRODUCER", "DECLARES_PRODUCER"),
    ("OVERRIDDEN_BY.EXPOSES", "EXPOSES"),
)
_OVERRIDE_AXIS_COMPOSED_REL_BY_KEY: dict[str, str | None] = dict(_OVERRIDE_AXIS_COMPOSED_REL_MAP)
OVERRIDE_AXIS_COMPOSED_EDGE_TYPES: frozenset[str] = frozenset(_OVERRIDE_AXIS_COMPOSED_REL_BY_KEY)


def _coerce_id_list(raw: Any) -> list[str]:
    """Normalize Kuzu ``collect(DISTINCT ...)`` list results to string ids."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None and str(x) != ""]
    s = str(raw)
    return [s] if s else []


__all__ = [
    "KuzuGraph",
    "resolve_kuzu_path",
    "SymbolHit",
    "EdgeHit",
    "CallEdge",
    "ViaEdge",
    "StageSymbol",
    "RouteCaller",
    "find_symbols_in_file_range",
]


def resolve_kuzu_path(explicit: str | None = None) -> str:
    """Resolve the Kuzu DB path the same way the builder does."""
    if explicit:
        return str(Path(explicit).expanduser())
    idx = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx and not idx.startswith(("s3://", "gs://", "az://")):
        return str(Path(os.path.expanduser(idx.rstrip("/"))) / "code_graph.kuzu")
    return str((Path.cwd() / ".java-codebase-rag" / "code_graph.kuzu").resolve())


@dataclass
class SymbolHit:
    id: str
    kind: str
    name: str
    fqn: str
    package: str
    module: str
    microservice: str
    filename: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    modifiers: list[str]
    annotations: list[str]
    capabilities: list[str]
    role: str
    signature: str
    parent_id: str
    resolved: bool


@dataclass
class EdgeHit:
    type: str  # EXTENDS | IMPLEMENTS | INJECTS
    src: SymbolHit
    dst: SymbolHit
    mechanism: str = ""
    annotation: str = ""
    field_or_param: str = ""
    resolved: bool = True


@dataclass
class CallEdge:
    src: SymbolHit
    dst: SymbolHit
    confidence: float
    strategy: str
    source: str
    call_site_line: int
    call_site_byte: int
    arg_count: int
    resolved: bool


@dataclass
class ViaEdge:
    """Labelled edge from a previous-stage node to a stage symbol.

    Populated by `trace_flow` so callers can see *why* two types ended up
    in the same chain (e.g. `INJECTS` vs `IMPLEMENTS` vs `CALLS`) and at what hop
    from the frontier they were reached.
    """
    edge_type: str  # INJECTS | EXTENDS | IMPLEMENTS | CALLS | HTTP_CALLS | ASYNC_CALLS
    from_fqn: str
    hop: int  # 1 = direct neighbour of previous-stage frontier
    caller_node_id: str = ""  # Client id when edge_type is HTTP_CALLS (SCHEMA v2)


@dataclass
class StageSymbol:
    """A trace_flow stage entry: the symbol plus the edges that pulled it in.

    Stage 0 (seeds) has `via=[]`. Later stages list every first-time path
    from the previous frontier to `symbol`.
    """
    symbol: SymbolHit
    via: list[ViaEdge]


@dataclass
class RouteCaller:
    caller_node_id: str
    caller_node_kind: Literal["client", "producer"]
    caller_microservice: str
    declaring_symbol_id: str
    confidence: float
    match: str
    target_service: str = ""
    raw_uri: str = ""
    topic: str = ""
    broker: str = ""


def _symbol_return_for(alias: str) -> str:
    """Kuzu RETURN projection for Symbol properties, using the given node alias.

    Centralised so queries that bind Symbol under a non-`s` alias (e.g. `n` in
    graph-expansion / flow-tracing) don't emit `s.*` references that Kuzu
    rejects with `Variable s is not in scope`.
    """
    return (
        f"{alias}.id AS id, {alias}.kind AS kind, {alias}.name AS name, {alias}.fqn AS fqn, "
        f"{alias}.package AS package, {alias}.module AS module, "
        f"{alias}.microservice AS microservice, {alias}.filename AS filename, "
        f"{alias}.start_line AS start_line, {alias}.end_line AS end_line, "
        f"{alias}.start_byte AS start_byte, {alias}.end_byte AS end_byte, "
        f"{alias}.modifiers AS modifiers, {alias}.annotations AS annotations, "
        f"{alias}.capabilities AS capabilities, "
        f"{alias}.role AS role, {alias}.signature AS signature, "
        f"{alias}.parent_id AS parent_id, {alias}.resolved AS resolved"
    )


_SYMBOL_RETURN = _symbol_return_for("s")


def _scope_filters(
    alias: str,
    *,
    module: str | None,
    microservice: str | None,
    params: dict[str, Any],
) -> list[str]:
    """Build module/microservice scoping predicates against a node alias.

    Mutates `params` to bind `$module` / `$microservice` only when the
    corresponding filter is set, so unused names don't leak into the
    Kuzu plan.
    """
    out: list[str] = []
    if module:
        params["module"] = module
        out.append(f"{alias}.module = $module")
    if microservice:
        params["microservice"] = microservice
        out.append(f"{alias}.microservice = $microservice")
    return out


_EXTERNAL_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "org.springframework.",
    "lombok.",
)

_EDGE_TYPES: tuple[str, ...] = (
    "EXTENDS",
    "IMPLEMENTS",
    "INJECTS",
    "OVERRIDES",
    "DECLARES",
    "CALLS",
    "EXPOSES",
    "DECLARES_CLIENT",
    "DECLARES_PRODUCER",
    "HTTP_CALLS",
    "ASYNC_CALLS",
)


def _type_part_fqn(sym_fqn: str) -> str:
    return sym_fqn.split("#", 1)[0]


def _is_external_fqn(fqn: str) -> bool:
    base = _type_part_fqn(fqn)
    return any(base.startswith(p) for p in _EXTERNAL_PREFIXES)


def _row_to_symbol(row: dict[str, Any]) -> SymbolHit:
    return SymbolHit(
        id=row.get("id", "") or "",
        kind=row.get("kind", "") or "",
        name=row.get("name", "") or "",
        fqn=row.get("fqn", "") or "",
        package=row.get("package", "") or "",
        module=row.get("module", "") or "",
        microservice=row.get("microservice", "") or "",
        filename=row.get("filename", "") or "",
        start_line=int(row.get("start_line") or 0),
        end_line=int(row.get("end_line") or 0),
        start_byte=int(row.get("start_byte") or 0),
        end_byte=int(row.get("end_byte") or 0),
        modifiers=list(row.get("modifiers") or []),
        annotations=list(row.get("annotations") or []),
        capabilities=list(row.get("capabilities") or []),
        role=row.get("role", "") or "",
        signature=row.get("signature", "") or "",
        parent_id=row.get("parent_id", "") or "",
        resolved=bool(row.get("resolved", True)),
    )


_SYM_COLS = (
    "id", "kind", "name", "fqn", "package", "module", "microservice",
    "filename", "start_line", "end_line", "start_byte", "end_byte",
    "modifiers", "annotations", "capabilities", "role", "signature", "parent_id", "resolved",
)


def find_symbols_in_file_range(
    graph: "KuzuGraph",
    *,
    filename: str,
    start_line: int,
    end_line: int,
) -> list[SymbolHit]:
    """Return `Symbol` rows overlapping `[start_line, end_line]` in `filename` (1-based, inclusive)."""
    if start_line < 1 or end_line < start_line:
        return []
    q = (
        f"MATCH (s:Symbol) WHERE s.filename = $fn "
        f"AND s.start_line <= $hmax AND s.end_line >= $hmin "
        f"RETURN {_SYMBOL_RETURN} ORDER BY s.start_line, s.end_line"
    )
    params = {"fn": filename, "hmax": int(end_line), "hmin": int(start_line)}
    return [_row_to_symbol(r) for r in graph._rows(q, params)]


def _prefixed_symbol_row(prefix: str, row: dict[str, Any]) -> dict[str, Any]:
    p = f"{prefix}_"
    return {k[len(p) :]: v for k, v in row.items() if k.startswith(p)}


def _row_to_call_edge(row: dict[str, Any]) -> CallEdge:
    return CallEdge(
        src=_row_to_symbol(_prefixed_symbol_row("caller", row)),
        dst=_row_to_symbol(_prefixed_symbol_row("callee", row)),
        confidence=float(row.get("confidence") or 0.0),
        strategy=str(row.get("strategy") or ""),
        source=str(row.get("source") or "static"),
        call_site_line=int(row.get("call_site_line") or 0),
        call_site_byte=int(row.get("call_site_byte") or 0),
        arg_count=int(row.get("arg_count") or 0),
        resolved=bool(row.get("resolved", True)),
    )


def _call_graph_needle_phantom_arity_alt(needle: str) -> str | None:
    """Map ``Type#method(123)`` → ``Type#method(?)`` for phantom callee FQNs (D1)."""
    if "#" not in needle:
        return None
    i = needle.rfind("(")
    if i <= 0 or not needle.endswith(")"):
        return None
    inner = needle[i + 1 : -1]
    if not inner.isdigit():
        return None
    return needle[:i] + "(?)"


class KuzuGraph:
    """Thin wrapper around a read-only Kuzu connection.

    Safe to share across threads: we hold a single `Connection`, guarded by a lock.
    """

    _lock = threading.Lock()
    _instance: "KuzuGraph | None" = None
    _instance_path: str | None = None

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db = kuzu.Database(db_path, read_only=True)
        self._conn = kuzu.Connection(self._db)
        self._conn_lock = threading.Lock()

    @classmethod
    def get(cls, db_path: str | None = None) -> "KuzuGraph":
        resolved = resolve_kuzu_path(db_path)
        with cls._lock:
            if cls._instance is None or cls._instance_path != resolved:
                instance = cls(resolved)
                meta = instance.meta()
                graph_version = int(meta.get("ontology_version") or 0)
                if "error" not in meta and graph_version < _ONTOLOGY_VERSION:
                    raise RuntimeError(
                        f"Graph ontology version {graph_version} is older than the "
                        f"required version {_ONTOLOGY_VERSION}. "
                        "Rebuild the graph: `python build_ast_graph.py --source-root <repo>`, "
                        "or run `java-codebase-rag reprocess --source-root <repo>` for a full "
                        "Lance+Kuzu re-index."
                    )
                cls._instance = instance
                cls._instance_path = resolved
            return cls._instance

    @classmethod
    def exists(cls, db_path: str | None = None) -> bool:
        resolved = resolve_kuzu_path(db_path)
        p = Path(resolved)
        if not p.exists():
            return False
        # Kuzu represents DB as a directory; allow file form too (single-file DBs).
        return True

    # ---- low-level ----

    def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._conn_lock:
            r = self._conn.execute(query, params or {})
            columns = r.get_column_names()
            out: list[dict[str, Any]] = []
            while r.has_next():
                vals = r.get_next()
                out.append(dict(zip(columns, vals)))
            return out

    # ---- meta ----

    def meta(self) -> dict[str, Any]:
        _META_PR_F1 = (
            "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
            "m.built_at AS built_at, m.source_root AS source_root, "
            "m.counts_json AS counts_json, m.parse_errors AS parse_errors, "
            "m.routes_total AS routes_total, m.exposes_total AS exposes_total, "
            "m.routes_by_framework AS routes_by_framework, "
            "m.routes_resolved_pct AS routes_resolved_pct, "
            "m.routes_from_brownfield_pct AS routes_from_brownfield_pct, "
            "m.routes_by_layer AS routes_by_layer, "
            "m.http_calls_total AS http_calls_total, m.async_calls_total AS async_calls_total, "
            "m.http_calls_by_strategy AS http_calls_by_strategy, m.async_calls_by_strategy AS async_calls_by_strategy, "
            "m.http_calls_resolved_pct AS http_calls_resolved_pct, m.async_calls_resolved_pct AS async_calls_resolved_pct, "
            "m.http_clients_from_brownfield_pct AS http_clients_from_brownfield_pct, "
            "m.async_producers_from_brownfield_pct AS async_producers_from_brownfield_pct, "
            "m.http_calls_match_breakdown AS http_calls_match_breakdown, "
            "m.async_calls_match_breakdown AS async_calls_match_breakdown, "
            "m.cross_service_calls_total AS cross_service_calls_total, "
            "m.pass3_skipped_cross_service AS pass3_skipped_cross_service, "
            "m.pass4_exposes_suppressed_feign AS pass4_exposes_suppressed_feign, "
            "m.cross_service_resolution AS cross_service_resolution"
        )
        _META_PR_E3 = (
            "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
            "m.built_at AS built_at, m.source_root AS source_root, "
            "m.counts_json AS counts_json, m.parse_errors AS parse_errors, "
            "m.routes_total AS routes_total, m.exposes_total AS exposes_total, "
            "m.routes_by_framework AS routes_by_framework, "
            "m.routes_resolved_pct AS routes_resolved_pct, "
            "m.routes_from_brownfield_pct AS routes_from_brownfield_pct, "
            "m.routes_by_layer AS routes_by_layer, "
            "m.http_calls_total AS http_calls_total, m.async_calls_total AS async_calls_total, "
            "m.http_calls_by_strategy AS http_calls_by_strategy, m.async_calls_by_strategy AS async_calls_by_strategy, "
            "m.http_calls_resolved_pct AS http_calls_resolved_pct, m.async_calls_resolved_pct AS async_calls_resolved_pct, "
            "m.http_clients_from_brownfield_pct AS http_clients_from_brownfield_pct, "
            "m.async_producers_from_brownfield_pct AS async_producers_from_brownfield_pct, "
            "m.http_calls_match_breakdown AS http_calls_match_breakdown, "
            "m.async_calls_match_breakdown AS async_calls_match_breakdown, "
            "m.cross_service_calls_total AS cross_service_calls_total, "
            "m.pass3_skipped_cross_service AS pass3_skipped_cross_service, "
            "m.cross_service_resolution AS cross_service_resolution"
        )
        _META_PRE_E3 = (
            "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
            "m.built_at AS built_at, m.source_root AS source_root, "
            "m.counts_json AS counts_json, m.parse_errors AS parse_errors, "
            "m.routes_total AS routes_total, m.exposes_total AS exposes_total, "
            "m.routes_by_framework AS routes_by_framework, "
            "m.routes_resolved_pct AS routes_resolved_pct, "
            "m.routes_from_brownfield_pct AS routes_from_brownfield_pct, "
            "m.routes_by_layer AS routes_by_layer, "
            "m.http_calls_total AS http_calls_total, m.async_calls_total AS async_calls_total, "
            "m.http_calls_by_strategy AS http_calls_by_strategy, m.async_calls_by_strategy AS async_calls_by_strategy, "
            "m.http_calls_resolved_pct AS http_calls_resolved_pct, m.async_calls_resolved_pct AS async_calls_resolved_pct, "
            "m.http_clients_from_brownfield_pct AS http_clients_from_brownfield_pct, "
            "m.async_producers_from_brownfield_pct AS async_producers_from_brownfield_pct, "
            "m.http_calls_match_breakdown AS http_calls_match_breakdown, "
            "m.async_calls_match_breakdown AS async_calls_match_breakdown, "
            "m.cross_service_calls_total AS cross_service_calls_total"
        )
        _META_PR_A2 = (
            "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
            "m.built_at AS built_at, m.source_root AS source_root, "
            "m.counts_json AS counts_json, m.parse_errors AS parse_errors, "
            "m.routes_total AS routes_total, m.exposes_total AS exposes_total, "
            "m.routes_by_framework AS routes_by_framework, "
            "m.routes_resolved_pct AS routes_resolved_pct"
        )
        _META_LEGACY = (
            "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
            "m.built_at AS built_at, m.source_root AS source_root, "
            "m.counts_json AS counts_json, m.parse_errors AS parse_errors"
        )
        rows: list[dict[str, Any]]
        meta_mode = "pr_f1"
        try:
            rows = self._rows(_META_PR_F1)
        except Exception:
            meta_mode = "pr_e3"
            try:
                rows = self._rows(_META_PR_E3)
            except Exception:
                meta_mode = "pre_e3"
                try:
                    rows = self._rows(_META_PRE_E3)
                except Exception:
                    meta_mode = "pr_a2"
                    try:
                        rows = self._rows(_META_PR_A2)
                    except Exception:
                        meta_mode = "legacy"
                        try:
                            rows = self._rows(_META_LEGACY)
                        except Exception as e:
                            return {"error": f"{e}"}
        if not rows:
            return {"error": "no GraphMeta node"}
        row = rows[0]
        counts: dict[str, Any]
        try:
            counts = json.loads(row.get("counts_json") or "{}")
        except Exception:
            counts = {}
        routes_total = exposes_total = 0
        routes_resolved_pct = 0.0
        routes_by_framework: dict[str, Any] = {}
        routes_from_brownfield_pct = 0.0
        routes_by_layer: dict[str, Any] = {}
        http_calls_total = 0
        async_calls_total = 0
        http_calls_by_strategy: dict[str, Any] = {}
        async_calls_by_strategy: dict[str, Any] = {}
        http_calls_resolved_pct = 0.0
        async_calls_resolved_pct = 0.0
        http_clients_from_brownfield_pct = 0.0
        async_producers_from_brownfield_pct = 0.0
        http_calls_match_breakdown: dict[str, Any] = {}
        async_calls_match_breakdown: dict[str, Any] = {}
        cross_service_calls_total = 0
        pass3_skipped_cross_service = 0
        pass4_exposes_suppressed_feign: int | None = None
        cross_service_resolution: str | None = None
        if meta_mode != "legacy":
            rfw_raw = row.get("routes_by_framework") or "{}"
            try:
                routes_by_framework = json.loads(rfw_raw) if isinstance(rfw_raw, str) else (rfw_raw or {})
            except Exception:
                routes_by_framework = {}
            if not isinstance(routes_by_framework, dict):
                routes_by_framework = {}
            routes_total = int(row.get("routes_total") or 0)
            exposes_total = int(row.get("exposes_total") or 0)
            routes_resolved_pct = float(row.get("routes_resolved_pct") or 0.0)
        if meta_mode in ("pr_f1", "pr_e3", "pre_e3"):
            routes_from_brownfield_pct = float(row.get("routes_from_brownfield_pct") or 0.0)
            rbl_raw = row.get("routes_by_layer") or "{}"
            try:
                routes_by_layer = json.loads(rbl_raw) if isinstance(rbl_raw, str) else (rbl_raw or {})
            except Exception:
                routes_by_layer = {}
            if not isinstance(routes_by_layer, dict):
                routes_by_layer = {}
            http_calls_total = int(row.get("http_calls_total") or 0)
            async_calls_total = int(row.get("async_calls_total") or 0)
            hbs_raw = row.get("http_calls_by_strategy") or "{}"
            abs_raw = row.get("async_calls_by_strategy") or "{}"
            try:
                http_calls_by_strategy = json.loads(hbs_raw) if isinstance(hbs_raw, str) else (hbs_raw or {})
            except Exception:
                http_calls_by_strategy = {}
            if not isinstance(http_calls_by_strategy, dict):
                http_calls_by_strategy = {}
            try:
                async_calls_by_strategy = json.loads(abs_raw) if isinstance(abs_raw, str) else (abs_raw or {})
            except Exception:
                async_calls_by_strategy = {}
            if not isinstance(async_calls_by_strategy, dict):
                async_calls_by_strategy = {}
            http_calls_resolved_pct = float(row.get("http_calls_resolved_pct") or 0.0)
            async_calls_resolved_pct = float(row.get("async_calls_resolved_pct") or 0.0)
            http_clients_from_brownfield_pct = float(row.get("http_clients_from_brownfield_pct") or 0.0)
            async_producers_from_brownfield_pct = float(row.get("async_producers_from_brownfield_pct") or 0.0)
            hmb_raw = row.get("http_calls_match_breakdown") or "{}"
            amb_raw = row.get("async_calls_match_breakdown") or "{}"
            try:
                http_calls_match_breakdown = json.loads(hmb_raw) if isinstance(hmb_raw, str) else (hmb_raw or {})
            except Exception:
                http_calls_match_breakdown = {}
            if not isinstance(http_calls_match_breakdown, dict):
                http_calls_match_breakdown = {}
            try:
                async_calls_match_breakdown = json.loads(amb_raw) if isinstance(amb_raw, str) else (amb_raw or {})
            except Exception:
                async_calls_match_breakdown = {}
            if not isinstance(async_calls_match_breakdown, dict):
                async_calls_match_breakdown = {}
            cross_service_calls_total = int(row.get("cross_service_calls_total") or 0)
            pass3_skipped_cross_service = int(row.get("pass3_skipped_cross_service") or 0)
            if meta_mode == "pr_f1":
                pass4_exposes_suppressed_feign = int(row.get("pass4_exposes_suppressed_feign") or 0)
                raw_csr = row.get("cross_service_resolution")
                cross_service_resolution = (
                    str(raw_csr) if raw_csr not in (None, "") else None
                )
            elif meta_mode == "pr_e3":
                raw_csr = row.get("cross_service_resolution")
                cross_service_resolution = (
                    str(raw_csr) if raw_csr not in (None, "") else None
                )
        edge_counts = {edge: 0 for edge in _EDGE_TYPES}
        failed_edges: list[str] = []
        for edge_type in _EDGE_TYPES:
            try:
                edge_rows = self._rows(
                    f"MATCH ()-[e:{edge_type}]->() RETURN count(e) AS n"
                )
                edge_counts[edge_type] = int(edge_rows[0].get("n") or 0) if edge_rows else 0
            except Exception as exc:
                failed_edges.append(edge_type)
                log.warning("edge count query failed for %s: %s", edge_type, exc)
        if len(failed_edges) == len(_EDGE_TYPES):
            log.warning("edge count queries failed for all edge types; returning zeroed edge_counts")

        return {
            "ontology_version": int(row.get("ontology_version") or 0),
            "built_at": int(row.get("built_at") or 0),
            "source_root": row.get("source_root") or "",
            "parse_errors": int(row.get("parse_errors") or 0),
            "counts": counts,
            "routes_total": routes_total,
            "exposes_total": exposes_total,
            "routes_by_framework": routes_by_framework,
            "routes_resolved_pct": routes_resolved_pct,
            "routes_from_brownfield_pct": routes_from_brownfield_pct,
            "routes_by_layer": routes_by_layer,
            "http_calls_total": http_calls_total,
            "async_calls_total": async_calls_total,
            "http_calls_by_strategy": http_calls_by_strategy,
            "async_calls_by_strategy": async_calls_by_strategy,
            "http_calls_resolved_pct": http_calls_resolved_pct,
            "async_calls_resolved_pct": async_calls_resolved_pct,
            "http_clients_from_brownfield_pct": http_clients_from_brownfield_pct,
            "async_producers_from_brownfield_pct": async_producers_from_brownfield_pct,
            "http_calls_match_breakdown": http_calls_match_breakdown,
            "async_calls_match_breakdown": async_calls_match_breakdown,
            "cross_service_calls_total": cross_service_calls_total,
            "pass3_skipped_cross_service": pass3_skipped_cross_service,
            "pass4_exposes_suppressed_feign": pass4_exposes_suppressed_feign,
            "cross_service_resolution": cross_service_resolution,
            "edge_counts": edge_counts,
            "db_path": self.db_path,
        }

    def edge_counts_for(self, node_id: str) -> dict[str, dict[str, int]]:
        rows = self._rows(
            "MATCH (n {id: $id})-[e]->() "
            "RETURN label(e) AS edge_type, 'out' AS direction, count(e) AS n "
            "UNION ALL "
            "MATCH (n {id: $id})<-[e]-() "
            "RETURN label(e) AS edge_type, 'in' AS direction, count(e) AS n",
            {"id": node_id},
        )
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            edge_type = str(row.get("edge_type") or "")
            direction = str(row.get("direction") or "")
            if edge_type == "" or direction not in ("in", "out"):
                continue
            out.setdefault(edge_type, {"in": 0, "out": 0})
            out[edge_type][direction] = int(row.get("n") or 0)
        return {
            edge_type: dirs
            for edge_type, dirs in out.items()
            if int(dirs.get("in", 0)) > 0 or int(dirs.get("out", 0)) > 0
        }

    def member_edge_rollup_for(self, type_id: str) -> dict[str, dict[str, int]]:
        """2-hop DECLARES member edge counts for a type Symbol (describe-time only).

        Keys use dot notation and are not stored graph edge labels.
        """
        params = {"id": type_id}
        rollup: dict[str, dict[str, int]] = {}
        for key, rel in _MEMBER_EDGE_COMPOSED_REL_MAP:
            rows = self._rows(
                f"MATCH (t:Symbol {{id: $id}})-[:DECLARES]->(m:Symbol)-[e:{rel}]->() "
                "RETURN count(e) AS n",
                params,
            )
            n = sum(int(r.get("n") or 0) for r in rows) if rows else 0
            if n > 0:
                rollup[key] = {"in": 0, "out": n}
        return rollup

    def member_edge_traversal_for(self, type_id: str, composed_key: str) -> list[dict[str, Any]]:
        """2-hop DECLARES member traversal for a type Symbol (neighbors dot-key path)."""
        rel = _MEMBER_EDGE_COMPOSED_REL_BY_KEY.get(composed_key)
        if rel is None:
            return []
        # Untyped [e] + label(e) filter: typed unions fail the binder when RETURN references
        # columns that exist on only some rel types (same pattern as flat neighbors_v2).
        return self._rows(
            "MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e]->(term) "
            "WHERE label(e) = $rel "
            "RETURN m.id AS via_id, label(e) AS stored_edge_type, "
            "term.id AS other_id, e.confidence AS confidence, e.strategy AS strategy, "
            "e.match AS match, e.mechanism AS mechanism, e.annotation AS annotation, "
            "e.field_or_param AS field_or_param, e.source AS source, "
            "e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte, "
            "e.arg_count AS arg_count, e.resolved AS resolved",
            {"id": type_id, "rel": rel},
        )

    def override_axis_traversal_for(self, method_id: str, composed_key: str) -> list[dict[str, Any]]:
        """Override-axis composed traversal for a method Symbol (neighbors dot-key path).

        Uses stored ``[:OVERRIDES]`` for the dispatch hop (aligned with ``override_axis_rollup_for``
        overrider ids). Base key returns overrider method ids only; composed keys return terminal
        rows with full edge attr projection plus ``via_id`` (overrider method id).
        """
        rel = _OVERRIDE_AXIS_COMPOSED_REL_BY_KEY.get(composed_key)
        if rel is None and composed_key != "OVERRIDDEN_BY":
            return []
        if rel is None:
            return self._rows(
                "MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol) "
                "RETURN mover.id AS other_id",
                {"id": method_id},
            )
        return self._rows(
            "MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol)-[e]->(term) "
            "WHERE label(e) = $rel "
            "RETURN mover.id AS via_id, label(e) AS stored_edge_type, "
            "term.id AS other_id, e.confidence AS confidence, e.strategy AS strategy, "
            "e.match AS match, e.mechanism AS mechanism, e.annotation AS annotation, "
            "e.field_or_param AS field_or_param, e.source AS source, "
            "e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte, "
            "e.arg_count AS arg_count, e.resolved AS resolved",
            {"id": method_id, "rel": rel},
        )

    def count_calls_for_symbol(self, origin_id: str, *, direction: Literal["in", "out"]) -> int:
        """Count CALLS edges incident on a Symbol (hints / diagnostics)."""
        if direction == "out":
            pattern = "MATCH (origin:Symbol {id: $id})-[e:CALLS]->() RETURN count(e) AS n"
        else:
            pattern = "MATCH (origin:Symbol {id: $id})<-[e:CALLS]-() RETURN count(e) AS n"
        rows = self._rows(pattern, {"id": origin_id})
        return int(rows[0].get("n") or 0) if rows else 0

    def neighbor_calls_for_symbol(
        self,
        origin_id: str,
        *,
        direction: Literal["in", "out"],
        offset: int = 0,
        limit: int | None = None,
        sql_pagination: bool = True,
        min_confidence: float | None = None,
        include_strategies: list[str] | None = None,
        exclude_strategies: list[str] | None = None,
        callee_declaring_role: str | None = None,
        callee_declaring_roles: list[str] | None = None,
        exclude_callee_declaring_roles: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """CALLS neighbors with source-order delivery and optional edge-attribute pushdown.

        When ``sql_pagination`` is True and ``limit`` is set, ``SKIP``/``LIMIT`` apply after
        ``ORDER BY e.call_site_line, e.call_site_byte``. Otherwise the full ordered stream is
        returned for caller-side ``NodeFilter`` / pagination.
        """
        wh_parts = ["origin.id = $id"]
        params: dict[str, Any] = {"id": origin_id}
        if min_confidence is not None:
            wh_parts.append("e.confidence >= $min_confidence")
            params["min_confidence"] = min_confidence
        if include_strategies:
            wh_parts.append("e.strategy IN $include_strategies")
            params["include_strategies"] = include_strategies
        if exclude_strategies:
            wh_parts.append("NOT (e.strategy IN $exclude_strategies)")
            params["exclude_strategies"] = exclude_strategies
        if callee_declaring_role is not None:
            wh_parts.append("e.callee_declaring_role = $callee_declaring_role")
            params["callee_declaring_role"] = callee_declaring_role
        if callee_declaring_roles:
            wh_parts.append("e.callee_declaring_role IN $callee_declaring_roles")
            params["callee_declaring_roles"] = callee_declaring_roles
        if exclude_callee_declaring_roles:
            wh_parts.append("NOT (e.callee_declaring_role IN $exclude_callee_declaring_roles)")
            params["exclude_callee_declaring_roles"] = exclude_callee_declaring_roles
        where = " AND ".join(wh_parts)
        if direction == "out":
            match = "MATCH (origin:Symbol)-[e:CALLS]->(other:Symbol)"
        else:
            match = "MATCH (origin:Symbol)<-[e:CALLS]-(other:Symbol)"
        q = (
            f"{match} WHERE {where} "
            "RETURN other.id AS other_id, 'CALLS' AS edge_type, "
            "e.confidence AS confidence, e.strategy AS strategy, e.source AS source, "
            "e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte, "
            "e.arg_count AS arg_count, e.resolved AS resolved, "
            "e.callee_declaring_role AS callee_declaring_role "
            "ORDER BY e.call_site_line, e.call_site_byte"
        )
        if sql_pagination and limit is not None:
            q += " SKIP $offset LIMIT $limit"
            params["offset"] = offset
            params["limit"] = limit
        return self._rows(q, params)

    def count_unresolved_for_caller(self, caller_id: str) -> int:
        rows = self._rows(
            "MATCH (:Symbol {id: $id})-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "RETURN count(u) AS n",
            {"id": caller_id},
        )
        return int(rows[0].get("n") or 0) if rows else 0

    def unresolved_sites_for_caller(
        self,
        caller_id: str,
        *,
        direction: Literal["in", "out"] = "out",
    ) -> list[dict[str, Any]]:
        if direction != "out":
            return []
        return self._rows(
            "MATCH (:Symbol {id: $id})-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "RETURN u.id AS id, u.caller_id AS caller_id, u.call_site_line AS call_site_line, "
            "u.call_site_byte AS call_site_byte, u.arg_count AS arg_count, "
            "u.callee_simple AS callee_simple, u.receiver_expr AS receiver_expr, "
            "u.reason AS reason "
            "ORDER BY u.call_site_line, u.call_site_byte",
            {"id": caller_id},
        )

    def unresolved_sites_for_describe(
        self,
        method_id: str,
        *,
        inline_limit: int = 5,
    ) -> tuple[list[dict[str, Any]], int]:
        total_rows = self._rows(
            "MATCH (:Symbol {id: $id})-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "RETURN count(u) AS n",
            {"id": method_id},
        )
        total = int(total_rows[0].get("n") or 0) if total_rows else 0
        if total == 0:
            return [], 0
        rows = self._rows(
            "MATCH (:Symbol {id: $id})-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "RETURN u.call_site_line AS line, u.reason AS reason, "
            "u.callee_simple AS callee_simple, u.receiver_expr AS receiver_expr "
            "ORDER BY u.call_site_line, u.call_site_byte "
            f"LIMIT {int(inline_limit)}",
            {"id": method_id},
        )
        return rows, total

    def list_unresolved_call_sites(
        self,
        *,
        method_id: str | None = None,
        reason: str | None = None,
        microservice: str | None = None,
        callee_simple: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        wh_parts: list[str] = []
        params: dict[str, Any] = {"lim": int(limit)}
        if method_id:
            wh_parts.append("caller.id = $method_id")
            params["method_id"] = method_id
        if reason:
            wh_parts.append("u.reason = $reason")
            params["reason"] = reason
        if microservice:
            wh_parts.append("caller.microservice = $microservice")
            params["microservice"] = microservice
        if callee_simple:
            wh_parts.append("u.callee_simple = $callee_simple")
            params["callee_simple"] = callee_simple
        where = ("WHERE " + " AND ".join(wh_parts)) if wh_parts else ""
        return self._rows(
            "MATCH (caller:Symbol)-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            f"{where} "
            "RETURN u.id AS id, caller.id AS caller_id, caller.fqn AS caller_fqn, "
            "caller.microservice AS microservice, u.call_site_line AS call_site_line, "
            "u.call_site_byte AS call_site_byte, u.arg_count AS arg_count, "
            "u.callee_simple AS callee_simple, u.receiver_expr AS receiver_expr, "
            "u.reason AS reason "
            "ORDER BY u.call_site_line, u.call_site_byte "
            "LIMIT $lim",
            params,
        )

    def stats_unresolved_call_sites(
        self,
        *,
        by: Literal["reason", "microservice", "caller_role"],
    ) -> list[dict[str, Any]]:
        if by == "reason":
            return self._rows(
                "MATCH (:Symbol)-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
                "RETURN u.reason AS bucket, count(*) AS n ORDER BY n DESC",
            )
        if by == "microservice":
            return self._rows(
                "MATCH (caller:Symbol)-[:UNRESOLVED_AT]->(:UnresolvedCallSite) "
                "RETURN caller.microservice AS bucket, count(*) AS n ORDER BY n DESC",
            )
        return self._rows(
            "MATCH (caller:Symbol)-[:UNRESOLVED_AT]->(:UnresolvedCallSite) "
            "MATCH (parent:Symbol)-[:DECLARES]->(caller) "
            "RETURN parent.role AS bucket, count(*) AS n ORDER BY n DESC",
        )

    def _edge_row_count_from_method_ids(self, method_ids: list[str], rel: str) -> int:
        """Count outgoing ``rel`` edges from method symbols (describe rollup helper)."""
        total = 0
        for mid in method_ids:
            rows = self._rows(
                f"MATCH (x:Symbol {{id: $mid}})-[e:{rel}]->() RETURN count(e) AS n",
                {"mid": mid},
            )
            total += int(rows[0].get("n") or 0) if rows else 0
        return total

    def _override_impl_ids_from_stored(self, method_id: str) -> list[str]:
        """Overrider method ids for a declaration method (stored ``[:OVERRIDES]`` in-hop)."""
        rows = self._rows(
            "MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol) "
            "RETURN collect(DISTINCT mover.id) AS ids",
            {"id": method_id},
        )
        return list(dict.fromkeys(_coerce_id_list(rows[0].get("ids") if rows else None)))

    def _override_decl_ids_from_stored(self, method_id: str) -> list[str]:
        """Declaration method ids overridden by a concrete method (stored ``[:OVERRIDES]`` out-hop)."""
        rows = self._rows(
            "MATCH (m:Symbol {id: $id})-[:OVERRIDES]->(decl:Symbol) "
            "RETURN collect(DISTINCT decl.id) AS ids",
            {"id": method_id},
        )
        return list(dict.fromkeys(_coerce_id_list(rows[0].get("ids") if rows else None)))

    def override_axis_rollup_for(self, method_id: str) -> dict[str, dict[str, int]]:
        """Dispatch-axis composed keys for method Symbols (describe-time only).

        Dispatch hop uses materialized ``[:OVERRIDES]`` (same as ``override_axis_traversal_for`` /
        ``neighbors`` dot-keys). Terminal composed counts sum outgoing edges from overrider
        methods. Omits keys with zero counts. Returns ``{}`` for non-methods, constructors,
        and static methods.
        """
        params = {"id": method_id}
        gate = self._rows(
            "MATCH (m:Symbol {id: $id}) "
            "WHERE m.kind = 'method' "
            "AND NOT list_contains(COALESCE(m.modifiers, []), 'static') "
            "RETURN 1 AS ok LIMIT 1",
            params,
        )
        if not gate:
            return {}

        rollup: dict[str, dict[str, int]] = {}

        impl_ids = self._override_impl_ids_from_stored(method_id)
        if impl_ids:
            rollup["OVERRIDDEN_BY"] = {"in": 0, "out": len(impl_ids)}
            n_dc = self._edge_row_count_from_method_ids(impl_ids, "DECLARES_CLIENT")
            if n_dc > 0:
                rollup["OVERRIDDEN_BY.DECLARES_CLIENT"] = {"in": 0, "out": n_dc}
            n_dp = self._edge_row_count_from_method_ids(impl_ids, "DECLARES_PRODUCER")
            if n_dp > 0:
                rollup["OVERRIDDEN_BY.DECLARES_PRODUCER"] = {"in": 0, "out": n_dp}
            n_ex = self._edge_row_count_from_method_ids(impl_ids, "EXPOSES")
            if n_ex > 0:
                rollup["OVERRIDDEN_BY.EXPOSES"] = {"in": 0, "out": n_ex}

        decl_ids = self._override_decl_ids_from_stored(method_id)
        if decl_ids:
            rollup["OVERRIDES"] = {"in": 0, "out": len(decl_ids)}

        return rollup

    def _scope_counts(self, column: str) -> dict[str, int]:
        """Generic helper: count resolved type symbols grouped by `column`.

        Empty-string keys mean the builder could not infer a value
        (no build-marker ancestor / no path segment under project_root).
        """
        try:
            rows = self._rows(
                f"MATCH (s:Symbol) WHERE s.resolved "
                f"AND s.kind IN ['class','interface','enum','record','annotation'] "
                f"RETURN s.{column} AS bucket, count(*) AS n"
            )
        except Exception:
            return {}
        out: dict[str, int] = {}
        for r in rows:
            key = r.get("bucket") or ""
            out[str(key)] = int(r.get("n") or 0)
        return out

    def module_counts(self) -> dict[str, int]:
        """Map of module name -> resolved type-symbol count."""
        return self._scope_counts("module")

    def microservice_counts(self) -> dict[str, int]:
        """Map of microservice name -> resolved type-symbol count."""
        return self._scope_counts("microservice")

    # ---- symbol-level lookups ----

    def find_by_name_or_fqn(self, name_or_fqn: str, *, kinds: list[str] | None = None,
                            module: str | None = None,
                            microservice: str | None = None,
                            limit: int = 50) -> list[SymbolHit]:
        filters = ["(s.name = $needle OR s.fqn = $needle)"]
        params: dict[str, Any] = {"needle": name_or_fqn}
        if kinds:
            params["kinds"] = kinds
            filters.append("s.kind IN $kinds")
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_role(self, role: str, *, module: str | None = None,
                     microservice: str | None = None,
                     capability: str | None = None,
                     limit: int = 100) -> list[SymbolHit]:
        filters = ["s.role = $role"]
        params: dict[str, Any] = {"role": role}
        if capability:
            filters.append("$capability IN s.capabilities")
            params["capability"] = capability
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_annotation(self, annotation: str, *, module: str | None = None,
                           microservice: str | None = None,
                           capability: str | None = None,
                           limit: int = 100) -> list[SymbolHit]:
        # Kuzu supports `list_contains` for STRING[].
        filters = ["list_contains(s.annotations, $ann)"]
        params: dict[str, Any] = {"ann": annotation}
        if capability:
            filters.append("$capability IN s.capabilities")
            params["capability"] = capability
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_capability(self, capability: str, *, module: str | None = None,
                           microservice: str | None = None,
                           limit: int = 100) -> list[SymbolHit]:
        filters = ["$capability IN s.capabilities"]
        params: dict[str, Any] = {"capability": capability}
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    # ---- edge traversals ----

    def find_implementors(self, interface_name_or_fqn: str, *,
                          module: str | None = None,
                          microservice: str | None = None,
                          capability: str | None = None,
                          limit: int = 100) -> list[SymbolHit]:
        filters = ["(i.name = $needle OR i.fqn = $needle)"]
        params: dict[str, Any] = {"needle": interface_name_or_fqn}
        if capability:
            filters.append("$capability IN c.capabilities")
            params["capability"] = capability
        filters.extend(_scope_filters("c", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = (
            f"MATCH (c:Symbol)-[:IMPLEMENTS]->(i:Symbol) WHERE {where} "
            f"RETURN DISTINCT {_symbol_return_for('c')} "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def find_subclasses(self, class_name_or_fqn: str, *,
                        module: str | None = None,
                        microservice: str | None = None,
                        capability: str | None = None,
                        limit: int = 100) -> list[SymbolHit]:
        filters = ["(b.name = $needle OR b.fqn = $needle)"]
        params: dict[str, Any] = {"needle": class_name_or_fqn}
        if capability:
            filters.append("$capability IN s.capabilities")
            params["capability"] = capability
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = (
            f"MATCH (s:Symbol)-[:EXTENDS]->(b:Symbol) WHERE {where} "
            f"RETURN DISTINCT {_SYMBOL_RETURN} "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def find_injectors(self, target_name_or_fqn: str, *,
                       module: str | None = None,
                       microservice: str | None = None,
                       capability: str | None = None,
                       limit: int = 100) -> list[EdgeHit]:
        filters = ["(t.name = $needle OR t.fqn = $needle)"]
        params: dict[str, Any] = {"needle": target_name_or_fqn}
        if capability:
            # Filter on the consumer (src) side: "which injectors carry this capability?"
            filters.append("$capability IN s.capabilities")
            params["capability"] = capability
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        # Project both sides of the edge with prefixed aliases (`s_*` / `t_*`)
        # so we can split rows back into source / target SymbolHits without
        # column-name collisions.
        s_proj = ", ".join(
            f"s.{c} AS s_{c}" for c in (
                "id", "kind", "name", "fqn", "package", "module", "microservice",
                "filename", "start_line", "end_line", "start_byte", "end_byte",
                "modifiers", "annotations", "capabilities", "role", "signature", "parent_id", "resolved",
            )
        )
        t_proj = ", ".join(
            f"t.{c} AS t_{c}" for c in (
                "id", "kind", "name", "fqn", "package", "module", "microservice",
                "filename", "start_line", "end_line", "start_byte", "end_byte",
                "modifiers", "annotations", "capabilities", "role", "signature", "parent_id", "resolved",
            )
        )
        q = (
            f"MATCH (s:Symbol)-[e:INJECTS]->(t:Symbol) WHERE {where} "
            f"RETURN {s_proj}, {t_proj}, "
            f"e.mechanism AS mechanism, e.annotation AS annotation, "
            f"e.field_or_param AS field_or_param, e.resolved AS resolved "
            f"LIMIT {int(limit)}"
        )
        out: list[EdgeHit] = []
        for r in self._rows(q, params):
            src = _row_to_symbol({k[2:]: v for k, v in r.items() if k.startswith("s_")})
            dst = _row_to_symbol({k[2:]: v for k, v in r.items() if k.startswith("t_")})
            out.append(EdgeHit(
                type="INJECTS", src=src, dst=dst,
                mechanism=r.get("mechanism") or "",
                annotation=r.get("annotation") or "",
                field_or_param=r.get("field_or_param") or "",
                resolved=bool(r.get("resolved", True)),
            ))
        return out

    def _method_ids_for_call_graph_needle(self, needle: str, *, limit: int) -> list[str]:
        rows = self._rows(
            "MATCH (s:Symbol) WHERE s.fqn = $n RETURN s.id AS id, s.kind AS kind LIMIT 1",
            {"n": needle},
        )
        if not rows:
            alt = _call_graph_needle_phantom_arity_alt(needle)
            if alt:
                rows = self._rows(
                    "MATCH (s:Symbol) WHERE s.fqn = $n RETURN s.id AS id, s.kind AS kind LIMIT 1",
                    {"n": alt},
                )
        if rows:
            kind = str(rows[0].get("kind") or "")
            sid = str(rows[0].get("id") or "")
            if kind in ("class", "interface", "enum", "record", "annotation") and sid:
                mrows = self._rows(
                    "MATCH (t:Symbol {id: $tid})-[:DECLARES]->(m:Symbol) RETURN m.id AS id "
                    f"LIMIT {int(limit)}",
                    {"tid": sid},
                )
                return [str(r["id"]) for r in mrows if r.get("id")]
            if kind in ("method", "constructor") and sid:
                return [sid]
        rows2 = self._rows(
            f"MATCH (s:Symbol) WHERE s.name = $n AND s.kind IN ['method','constructor'] "
            f"RETURN s.id AS id LIMIT {int(limit)}",
            {"n": needle},
        )
        return [str(r["id"]) for r in rows2 if r.get("id")]

    def find_callers(
        self, needle: str, *,
        depth: int = 1,
        limit: int = 100,
        min_confidence: float = 0.0,
        exclude_external: bool = True,
        module: str | None = None,
        microservice: str | None = None,
    ) -> list[CallEdge]:
        frontier = self._method_ids_for_call_graph_needle(needle, limit=max(limit, 50))
        if not frontier:
            return []
        caller_proj = ", ".join(f"caller.{c} AS caller_{c}" for c in _SYM_COLS)
        callee_proj = ", ".join(f"callee.{c} AS callee_{c}" for c in _SYM_COLS)
        out: list[CallEdge] = []
        seen: set[tuple[str, str, int, int]] = set()
        for _ in range(max(1, int(depth))):
            params: dict[str, Any] = {
                "frontier": list(frontier),
                "minc": float(min_confidence),
            }
            sc = _scope_filters("caller", module=module, microservice=microservice, params=params)
            wh_parts = ["callee.id IN $frontier", "c.confidence >= $minc"]
            wh_parts.extend(sc)
            wh = " AND ".join(wh_parts)
            q = (
                f"MATCH (caller:Symbol)-[c:CALLS]->(callee:Symbol) WHERE {wh} "
                f"RETURN {caller_proj}, {callee_proj}, "
                f"c.call_site_line AS call_site_line, c.call_site_byte AS call_site_byte, "
                f"c.arg_count AS arg_count, c.confidence AS confidence, c.strategy AS strategy, "
                f"c.source AS source, c.resolved AS resolved "
                f"LIMIT {int(limit) * 8}"
            )
            next_frontier: list[str] = []
            for row in self._rows(q, params):
                ce = _row_to_call_edge(row)
                # Filter only discovered callers (src). Needle may be external
                # (e.g. java.util.List#add) while still listing internal callers.
                if exclude_external and _is_external_fqn(ce.src.fqn):
                    continue
                key = (ce.src.id, ce.dst.id, ce.call_site_line, ce.call_site_byte)
                if key in seen:
                    continue
                seen.add(key)
                out.append(ce)
                next_frontier.append(ce.src.id)
                if len(out) >= limit:
                    return out
            frontier = list(dict.fromkeys(next_frontier))
            if not frontier:
                break
        return out

    def find_callees(
        self, needle: str, *,
        depth: int = 1,
        limit: int = 100,
        min_confidence: float = 0.0,
        exclude_external: bool = True,
        module: str | None = None,
        microservice: str | None = None,
    ) -> list[CallEdge]:
        frontier = self._method_ids_for_call_graph_needle(needle, limit=max(limit, 50))
        if not frontier:
            return []
        caller_proj = ", ".join(f"caller.{c} AS caller_{c}" for c in _SYM_COLS)
        callee_proj = ", ".join(f"callee.{c} AS callee_{c}" for c in _SYM_COLS)
        out: list[CallEdge] = []
        seen: set[tuple[str, str, int, int]] = set()
        for _ in range(max(1, int(depth))):
            params: dict[str, Any] = {
                "frontier": list(frontier),
                "minc": float(min_confidence),
            }
            sc = _scope_filters("callee", module=module, microservice=microservice, params=params)
            wh_parts = ["caller.id IN $frontier", "c.confidence >= $minc"]
            wh_parts.extend(sc)
            wh = " AND ".join(wh_parts)
            q = (
                f"MATCH (caller:Symbol)-[c:CALLS]->(callee:Symbol) WHERE {wh} "
                f"RETURN {caller_proj}, {callee_proj}, "
                f"c.call_site_line AS call_site_line, c.call_site_byte AS call_site_byte, "
                f"c.arg_count AS arg_count, c.confidence AS confidence, c.strategy AS strategy, "
                f"c.source AS source, c.resolved AS resolved "
                f"LIMIT {int(limit) * 8}"
            )
            next_frontier: list[str] = []
            for row in self._rows(q, params):
                ce = _row_to_call_edge(row)
                # Filter only discovered callees (dst). Needle may be external while
                # still listing non-external outbound calls when any exist.
                if exclude_external and _is_external_fqn(ce.dst.fqn):
                    continue
                key = (ce.src.id, ce.dst.id, ce.call_site_line, ce.call_site_byte)
                if key in seen:
                    continue
                seen.add(key)
                out.append(ce)
                next_frontier.append(ce.dst.id)
                if len(out) >= limit:
                    return out
            frontier = list(dict.fromkeys(next_frontier))
            if not frontier:
                break
        return out

    def expand_methods(
        self, fqns: list[str], *, depth: int = 1,
        min_confidence: float = 0.0, limit: int = 200,
        exclude_external: bool = True,
    ) -> list[tuple[str, float]]:
        """Reach type FQNs from seed types via DECLARES → CALLS → DECLARES (reverse).

        Each entry is ``(type_fqn, path_confidence)``. ``path_confidence`` is the
        maximum, over call paths from seed methods, of the minimum ``CALLS.confidence``
        along that path (seed methods anchor at ``1.0`` before the first hop).

        When ``exclude_external`` is true (default), types whose FQN matches the
        same JDK/Spring/Lombok prefixes as ``find_callees`` are omitted from the
        returned list (they are not indexed in LanceDB anyway). BFS still walks
        through external callees to find further project types.
        """
        if not fqns or depth < 1:
            return []
        seed_mids: list[str] = []
        for tfqn in fqns:
            r = self._rows(
                "MATCH (t:Symbol) WHERE t.fqn = $f AND t.kind IN ['class','interface','enum','record','annotation'] "
                "RETURN t.id AS id LIMIT 1",
                {"f": tfqn},
            )
            if not r or not r[0].get("id"):
                continue
            tid = str(r[0]["id"])
            mrows = self._rows(
                "MATCH (t:Symbol {id: $tid})-[:DECLARES]->(m:Symbol) RETURN m.id AS id",
                {"tid": tid},
            )
            seed_mids.extend(str(x["id"]) for x in mrows if x.get("id"))
        seed_mids = list(dict.fromkeys(seed_mids))
        if not seed_mids:
            return []
        frontier_conf: dict[str, float] = {mid: 1.0 for mid in seed_mids}
        type_best: dict[str, float] = {}
        ordered_types: list[str] = []
        seen_order: set[str] = set()
        for _ in range(int(depth)):
            if not frontier_conf:
                break
            ids = list(frontier_conf.keys())
            rows = self._rows(
                "MATCH (m:Symbol)-[c:CALLS]->(n:Symbol) WHERE m.id IN $ids AND c.confidence >= $mc "
                "RETURN m.id AS mid, n.id AS nid, c.confidence AS conf",
                {"ids": ids, "mc": float(min_confidence)},
            )
            next_conf: dict[str, float] = {}
            for r in rows:
                mid = str(r.get("mid") or "")
                nid = str(r.get("nid") or "")
                if not mid or not nid:
                    continue
                raw_conf = r.get("conf")
                try:
                    ec = float(raw_conf) if raw_conf is not None else 0.0
                except (TypeError, ValueError):
                    ec = 0.0
                parent = frontier_conf.get(mid)
                if parent is None:
                    continue
                new_c = min(parent, ec)
                next_conf[nid] = max(next_conf.get(nid, 0.0), new_c)

            if not next_conf:
                break

            for nid, path_c in next_conf.items():
                srows = self._rows(
                    "MATCH (s:Symbol {id: $id}) RETURN s.fqn AS fqn LIMIT 1",
                    {"id": nid},
                )
                if not srows:
                    continue
                mfqn = str(srows[0].get("fqn") or "")
                if "#" not in mfqn:
                    continue
                tpart = mfqn.split("#", 1)[0]
                if not tpart:
                    continue
                is_ext = _is_external_fqn(tpart)
                if exclude_external and is_ext:
                    pass
                else:
                    type_best[tpart] = max(type_best.get(tpart, 0.0), path_c)
                    if tpart not in seen_order:
                        seen_order.add(tpart)
                        ordered_types.append(tpart)
                        if len(ordered_types) >= limit:
                            return [(t, type_best[t]) for t in ordered_types[:limit]]

            frontier_conf = next_conf

        return [(t, type_best[t]) for t in ordered_types[:limit]]

    def neighbors(self, fqn_or_name: str, *, depth: int = 1,
                  edge_types: list[str] | None = None,
                  direction: str = "both", limit: int = 200) -> list[SymbolHit]:
        """BFS over `edge_types` up to `depth` hops. `direction` in {out, in, both}."""
        if depth < 1:
            return []
        edges = edge_types or ["EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "CALLS"]
        edge_pattern = "|".join(edges)
        if direction == "out":
            arrow_l, arrow_r = "-", "->"
        elif direction == "in":
            arrow_l, arrow_r = "<-", "-"
        else:
            arrow_l, arrow_r = "-", "-"
        q = (
            f"MATCH (root:Symbol) WHERE root.name = $needle OR root.fqn = $needle "
            f"MATCH path = (root){arrow_l}[:{edge_pattern}*1..{int(depth)}]{arrow_r}(n:Symbol) "
            f"RETURN DISTINCT {_symbol_return_for('n')} "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, {"needle": fqn_or_name})]

    def impact_analysis(self, fqn_or_name: str, *, depth: int = 2,
                        limit: int = 300) -> list[SymbolHit]:
        """Reverse closure over INJECTS + IMPLEMENTS (who breaks if `fqn` changes)."""
        q = (
            f"MATCH (target:Symbol) WHERE target.name = $needle OR target.fqn = $needle "
            f"MATCH (n:Symbol)-[:INJECTS|IMPLEMENTS|EXTENDS*1..{int(depth)}]->(target) "
            f"RETURN DISTINCT {_symbol_return_for('n')} "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, {"needle": fqn_or_name})]

    # ---- flow tracing (entrypoint -> service -> integration / repository) ----

    # Default ordered waterfall of role stages. Each stage collects neighbors of
    # the previous stage whose role matches the allow-list. Phantom / unresolved
    # symbols are excluded so we don't propagate noise across the boundary.
    _FLOW_STAGES: tuple[tuple[str, ...], ...] = (
        ("CONTROLLER",),
        ("SERVICE", "COMPONENT"),
        ("CLIENT", "REPOSITORY", "MAPPER"),
    )

    # Stage-0 accepts any entrypoint-like role. COMPONENT is included because
    # Kafka listeners / @Scheduled orchestrators are frequently plain
    # @Component, not @Controller; SERVICE is included so we don't drop
    # orchestrator seeds when the caller already narrowed the vector search
    # to services.
    _ENTRYPOINT_ROLES: tuple[str, ...] = (
        "CONTROLLER", "COMPONENT", "SERVICE", "CLIENT",
    )

    def trace_flow(self, seed_fqns: list[str], *,
                   module: str | None = None,
                   microservice: str | None = None,
                   depth: int = 2, stage_limit: int = 20,
                   follow_calls: bool = True,
                   min_call_confidence: float = 0.0,
                   exclude_external: bool = True) -> list[list[StageSymbol]]:
        """Walk stages `CONTROLLER -> SERVICE/COMPONENT -> CLIENT/REPOSITORY/MAPPER`.

        Returns a list of stages; each stage is a list of SymbolHit. The first
        stage is the seed set (entrypoints matched by FQN, filtered to
        orchestrator-like roles — see `_ENTRYPOINT_ROLES`). If role-filtered
        seeds come back empty we fall back to unfiltered seeds so a caller
        with no CONTROLLER coverage still gets *something* back.
        Each subsequent stage is the neighbor-set (INJECTS+EXTENDS+IMPLEMENTS,
        optionally merged with type-to-type paths through DECLARES+CALLS when
        `follow_calls` is true) of the previous stage, restricted to the
        stage's role allow-list.

        Defaults: ``depth=2`` (clamped to 1..3), ``follow_calls=True``,
        ``min_call_confidence=0.0``, ``exclude_external=True``. The latter only
        filters symbols reached via the DECLARES+CALLS hop: discovered **type**
        symbols matching external FQN prefixes (same list as ``expand_methods`` /
        the callee side of ``find_callees``), not the seed frontier. INJECTS /
        EXTENDS / IMPLEMENTS hops ignore ``exclude_external``.

        ``depth`` is the neighbour hop count per stage (not total trace depth).
        """
        if not seed_fqns:
            return []
        depth = max(1, min(3, int(depth)))

        stages: list[list[StageSymbol]] = []
        visited_fqns: set[str] = set()

        def _run_seed_query(entry_roles: tuple[str, ...] | None) -> list[SymbolHit]:
            filters = ["s.fqn IN $fqns"]
            params: dict[str, Any] = {"fqns": list(seed_fqns)}
            filters.extend(_scope_filters(
                "s", module=module, microservice=microservice, params=params,
            ))
            if entry_roles:
                params["entry_roles"] = list(entry_roles)
                # Kuzu 0.11.x does not support parameterized lists inside ANY
                # comprehensions, so we expand the fixed capability set as
                # individual list_contains predicates ORed together.
                cap_predicates = " OR ".join(
                    f"list_contains(s.capabilities, '{c}')"
                    for c in ("MESSAGE_LISTENER", "SCHEDULED_TASK")
                )
                filters.append(
                    f"(s.role IN $entry_roles OR {cap_predicates})"
                )
            where = " AND ".join(filters)
            q0 = (
                f"MATCH (s:Symbol) WHERE {where} "
                f"RETURN {_SYMBOL_RETURN} LIMIT {int(stage_limit)}"
            )
            return [_row_to_symbol(r) for r in self._rows(q0, params)]

        seed_rows = _run_seed_query(self._ENTRYPOINT_ROLES)
        if not seed_rows:
            seed_rows = _run_seed_query(None)
        if not seed_rows:
            return []
        stages.append([StageSymbol(symbol=r, via=[]) for r in seed_rows])
        for h in seed_rows:
            if h.fqn:
                visited_fqns.add(h.fqn)

        frontier_fqns: list[str] = [h.fqn for h in seed_rows if h.fqn]
        for stage_roles in self._FLOW_STAGES[1:]:
            if not frontier_fqns:
                break

            # Single-hop BFS repeated up to `depth` times. Each iteration
            # knows which edge type and parent node produced a newly-
            # discovered symbol, so we can label every stage entry.
            stage_results: dict[str, StageSymbol] = {}
            current_frontier = list(frontier_fqns)

            for hop in range(1, depth + 1):
                if not current_frontier:
                    break
                params: dict[str, Any] = {
                    "fqns": current_frontier,
                    "roles": list(stage_roles),
                }
                scope = _scope_filters(
                    "n", module=module, microservice=microservice, params=params,
                )
                scope_clause = (" AND " + " AND ".join(scope)) if scope else ""
                q = (
                    f"MATCH (root:Symbol)-[e:INJECTS|EXTENDS|IMPLEMENTS]-(n:Symbol) "
                    f"WHERE root.fqn IN $fqns AND n.role IN $roles AND n.resolved{scope_clause} "
                    f"RETURN {_symbol_return_for('n')}, "
                    f"label(e) AS edge_type, root.fqn AS from_fqn "
                    f"LIMIT {int(stage_limit) * 4}"
                )
                next_frontier: list[str] = []
                def _ingest_flow_row(
                    row: dict[str, Any], *, filter_external_fqn: bool = False,
                ) -> None:
                    sym = _row_to_symbol(row)
                    if (
                        filter_external_fqn
                        and exclude_external
                        and _is_external_fqn(sym.fqn)
                    ):
                        return
                    if not sym.fqn or sym.fqn in visited_fqns:
                        return
                    edge = ViaEdge(
                        edge_type=str(row.get("edge_type") or ""),
                        from_fqn=str(row.get("from_fqn") or ""),
                        hop=hop,
                        caller_node_id=str(row.get("caller_client_id") or ""),
                    )
                    existing = stage_results.get(sym.fqn)
                    if existing is None:
                        stage_results[sym.fqn] = StageSymbol(symbol=sym, via=[edge])
                        next_frontier.append(sym.fqn)
                    else:
                        if len(existing.via) < 4 and not any(
                            v.edge_type == edge.edge_type and v.from_fqn == edge.from_fqn
                            for v in existing.via
                        ):
                            existing.via.append(edge)

                for row in self._rows(q, params):
                    _ingest_flow_row(row)
                    if len(stage_results) >= stage_limit:
                        break

                # Structural-first budget: same-microservice CALLS top up first,
                # then cross-service HTTP/ASYNC caller edges.
                if follow_calls and len(stage_results) < stage_limit:
                    remaining = stage_limit - len(stage_results)
                    params_cf: dict[str, Any] = {
                        "fqns": current_frontier,
                        "roles": list(stage_roles),
                        "mc": float(min_call_confidence),
                    }
                    scope_cf = _scope_filters(
                        "n", module=module, microservice=microservice, params=params_cf,
                    )
                    sccf = (" AND " + " AND ".join(scope_cf)) if scope_cf else ""
                    qcf = (
                        "MATCH (root:Symbol)-[:DECLARES]->(m1:Symbol)-[c:CALLS]->(m2:Symbol)"
                        "<-[:DECLARES]-(n:Symbol) WHERE root.fqn IN $fqns AND n.role IN $roles "
                        "AND root.microservice = n.microservice "
                        "AND n.resolved AND n.kind IN ['class','interface','enum','record','annotation'] "
                        f"AND c.confidence >= $mc{sccf} "
                        f"RETURN {_symbol_return_for('n')}, 'CALLS' AS edge_type, root.fqn AS from_fqn "
                        f"LIMIT {max(1, remaining * 4)}"
                    )
                    for row in self._rows(qcf, params_cf):
                        _ingest_flow_row(row, filter_external_fqn=True)
                        if len(stage_results) >= stage_limit:
                            break
                if follow_calls and len(stage_results) < stage_limit:
                    remaining = stage_limit - len(stage_results)
                    params_rf: dict[str, Any] = {
                        "fqns": current_frontier,
                        "roles": list(stage_roles),
                        "mc": float(min_call_confidence),
                    }
                    scope_rf = _scope_filters(
                        "n", module=module, microservice=microservice, params=params_rf,
                    )
                    scrf = (" AND " + " AND ".join(scope_rf)) if scope_rf else ""
                    qrf = (
                        "MATCH (root:Symbol)-[:DECLARES]->(m1:Symbol)-[:DECLARES_CLIENT]->(c:Client)"
                        "-[e:HTTP_CALLS]->(rt:Route)<-[:EXPOSES]-(handler:Symbol)<-[:DECLARES]-(n:Symbol) "
                        "WHERE root.fqn IN $fqns AND n.role IN $roles "
                        "AND n.resolved AND n.kind IN ['class','interface','enum','record','annotation'] "
                        "AND e.confidence >= $mc AND root.microservice <> n.microservice "
                        f"{scrf} "
                        f"RETURN {_symbol_return_for('n')}, 'HTTP_CALLS' AS edge_type, "
                        f"root.fqn AS from_fqn, c.id AS caller_client_id "
                        f"LIMIT {max(1, remaining * 4)}"
                    )
                    for row in self._rows(qrf, params_rf):
                        _ingest_flow_row(row, filter_external_fqn=True)
                        if len(stage_results) >= stage_limit:
                            break
                    if len(stage_results) < stage_limit:
                        remaining = stage_limit - len(stage_results)
                        qrf_async = (
                            "MATCH (root:Symbol)-[:DECLARES]->(m1:Symbol)-[:DECLARES_PRODUCER]->(pr:Producer)"
                            "-[e:ASYNC_CALLS]->(rt:Route)<-[:EXPOSES]-(handler:Symbol)<-[:DECLARES]-(n:Symbol) "
                            "WHERE root.fqn IN $fqns AND n.role IN $roles "
                            "AND n.resolved AND n.kind IN ['class','interface','enum','record','annotation'] "
                            "AND e.confidence >= $mc AND root.microservice <> n.microservice "
                            f"{scrf} "
                            f"RETURN {_symbol_return_for('n')}, 'ASYNC_CALLS' AS edge_type, "
                            f"root.fqn AS from_fqn, pr.id AS caller_producer_id "
                            f"LIMIT {max(1, remaining * 4)}"
                        )
                        for row in self._rows(qrf_async, params_rf):
                            _ingest_flow_row(row, filter_external_fqn=True)
                            if len(stage_results) >= stage_limit:
                                break

                current_frontier = next_frontier
                if len(stage_results) >= stage_limit:
                    break

            if not stage_results:
                break
            stage_list = list(stage_results.values())
            stages.append(stage_list)
            for entry in stage_list:
                visited_fqns.add(entry.symbol.fqn)
            frontier_fqns = [entry.symbol.fqn for entry in stage_list]
        return stages

    # ---- routes (B2a) ----

    _ROUTE_RETURN = (
        "r.id AS id, r.kind AS kind, r.framework AS framework, r.method AS method, "
        "r.path AS path, r.path_template AS path_template, r.path_regex AS path_regex, "
        "r.topic AS topic, r.broker AS broker, r.feign_name AS feign_name, r.feign_url AS feign_url, "
        "r.microservice AS microservice, r.module AS module, r.filename AS filename, "
        "r.start_line AS start_line, r.end_line AS end_line, r.resolved AS resolved"
    )

    @staticmethod
    def _row_to_route_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("id") or ""),
            "kind": str(row.get("kind") or ""),
            "framework": str(row.get("framework") or ""),
            "method": str(row.get("method") or ""),
            "path": str(row.get("path") or ""),
            "path_template": str(row.get("path_template") or ""),
            "path_regex": str(row.get("path_regex") or ""),
            "topic": str(row.get("topic") or ""),
            "broker": str(row.get("broker") or ""),
            "feign_name": str(row.get("feign_name") or ""),
            "feign_url": str(row.get("feign_url") or ""),
            "microservice": str(row.get("microservice") or ""),
            "module": str(row.get("module") or ""),
            "filename": str(row.get("filename") or ""),
            "start_line": int(row.get("start_line") or 0),
            "end_line": int(row.get("end_line") or 0),
            "resolved": bool(row.get("resolved", True)),
        }

    def list_routes(
        self,
        *,
        microservice: str | None = None,
        framework: str | None = None,
        path_prefix: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        params: dict[str, Any] = {"lim": lim}
        preds: list[str] = []
        if microservice:
            params["microservice"] = microservice
            preds.append("r.microservice = $microservice")
        if framework:
            params["framework"] = framework
            preds.append("r.framework = $framework")
        if path_prefix:
            params["path_prefix"] = path_prefix
            preds.append("r.path STARTS WITH $path_prefix")
        if method is not None and method != "":
            params["method"] = method
            preds.append("r.method = $method")
        where = (" WHERE " + " AND ".join(preds)) if preds else ""
        q = (
            f"MATCH (r:Route){where} RETURN {self._ROUTE_RETURN} "
            f"ORDER BY r.framework, r.path, r.id LIMIT $lim"
        )
        return [self._row_to_route_dict(r) for r in self._rows(q, params)]

    def find_route_handlers(self, *, route_id: str) -> list[dict[str, Any]]:
        s_proj = ", ".join(f"s.{c} AS s_{c}" for c in _SYM_COLS)
        q = (
            f"MATCH (s:Symbol)-[e:EXPOSES]->(r:Route) WHERE r.id = $rid "
            f"RETURN {s_proj}, e.confidence AS confidence, e.strategy AS strategy "
            f"ORDER BY s.fqn"
        )
        out: list[dict[str, Any]] = []
        for r in self._rows(q, {"rid": route_id}):
            sym = _row_to_symbol({k[2:]: v for k, v in r.items() if k.startswith("s_")})
            out.append({
                "symbol": asdict(sym),
                "confidence": float(r.get("confidence") or 0.0),
                "strategy": str(r.get("strategy") or ""),
            })
        return out

    def get_route_by_path(
        self,
        *,
        microservice: str,
        path_template: str,
        method: str = "",
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"ms": microservice, "pt": path_template}
        meth_filter = ""
        if method != "":
            params["meth"] = method
            meth_filter = "AND r.method = $meth"
        q = (
            f"MATCH (r:Route) WHERE r.microservice = $ms AND r.path_template = $pt {meth_filter} "
            f"RETURN {self._ROUTE_RETURN} ORDER BY r.id LIMIT 1"
        )
        rows = self._rows(q, params)
        if not rows:
            return None
        return self._row_to_route_dict(rows[0])

    def find_route_callers(
        self,
        route_id: str | None = None,
        *,
        microservice: str = "",
        path_template: str = "",
        method: str = "",
    ) -> list[RouteCaller]:
        """HTTP callers via Client; async callers via Producer (two-hop each)."""
        rid = route_id or ""
        if not rid:
            params: dict[str, Any] = {
                "microservice": microservice,
                "path_template": path_template,
                "method": method,
            }
            rows = self._rows(
                "MATCH (r:Route) "
                "WHERE r.microservice = $microservice AND r.path_template = $path_template AND r.method = $method "
                "RETURN r.id AS id LIMIT 1",
                params,
            )
            if not rows:
                return []
            rid = str(rows[0].get("id") or "")
            if not rid:
                return []
        http_rows = self._rows(
            "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid}) "
            "RETURN c.id AS caller_node_id, c.microservice AS caller_microservice, "
            "s.id AS declaring_symbol_id, e.confidence AS confidence, e.match AS match, "
            "c.target_service AS target_service, e.raw_uri AS raw_uri "
            "ORDER BY e.confidence DESC, c.id",
            {"rid": rid},
        )
        async_rows = self._rows(
            "MATCH (s:Symbol)-[:DECLARES_PRODUCER]->(p:Producer)-[e:ASYNC_CALLS]->(r:Route {id: $rid}) "
            "RETURN p.id AS caller_node_id, p.microservice AS caller_microservice, "
            "s.id AS declaring_symbol_id, e.confidence AS confidence, e.match AS match, "
            "p.topic AS topic, p.broker AS broker "
            "ORDER BY e.confidence DESC, p.id",
            {"rid": rid},
        )
        out: list[RouteCaller] = []
        for row in http_rows:
            out.append(
                RouteCaller(
                    caller_node_id=str(row.get("caller_node_id") or ""),
                    caller_node_kind="client",
                    caller_microservice=str(row.get("caller_microservice") or ""),
                    declaring_symbol_id=str(row.get("declaring_symbol_id") or ""),
                    confidence=float(row.get("confidence") or 0.0),
                    match=str(row.get("match") or ""),
                    target_service=str(row.get("target_service") or ""),
                    raw_uri=str(row.get("raw_uri") or ""),
                ),
            )
        for row in async_rows:
            out.append(
                RouteCaller(
                    caller_node_id=str(row.get("caller_node_id") or ""),
                    caller_node_kind="producer",
                    caller_microservice=str(row.get("caller_microservice") or ""),
                    declaring_symbol_id=str(row.get("declaring_symbol_id") or ""),
                    confidence=float(row.get("confidence") or 0.0),
                    match=str(row.get("match") or ""),
                    topic=str(row.get("topic") or ""),
                    broker=str(row.get("broker") or ""),
                ),
            )
        return out

    def trace_request_flow(self, entry_route_id: str, max_hops: int = 5) -> dict[str, Any]:
        """Inbound HTTP via Client; async inbound via Producer (two-hop each)."""
        hops = max(1, min(int(max_hops), 8))
        inbound_http = self._rows(
            f"MATCH (entry:Route {{id: $rid}})<-[e:HTTP_CALLS]-(caller:Client)"
            "<-[:DECLARES_CLIENT]-(decl:Symbol) "
            f"OPTIONAL MATCH (origin:Symbol)-[:CALLS*0..{hops}]->(decl) "
            "RETURN DISTINCT caller.id AS caller_node_id, 'client' AS caller_node_kind, "
            "decl.id AS declaring_symbol_id, decl.fqn AS declaring_symbol_fqn, "
            "caller.microservice AS microservice, e.confidence AS confidence, "
            "e.match AS match, origin.id AS origin_symbol_id, origin.fqn AS origin_fqn "
            "ORDER BY confidence DESC, caller_node_id",
            {"rid": entry_route_id},
        )
        inbound_async = self._rows(
            f"MATCH (entry:Route {{id: $rid}})<-[e:ASYNC_CALLS]-(caller:Producer)"
            "<-[:DECLARES_PRODUCER]-(decl:Symbol) "
            f"OPTIONAL MATCH (origin:Symbol)-[:CALLS*0..{hops}]->(decl) "
            "RETURN DISTINCT caller.id AS caller_node_id, 'producer' AS caller_node_kind, "
            "decl.id AS declaring_symbol_id, decl.fqn AS declaring_symbol_fqn, "
            "caller.microservice AS microservice, e.confidence AS confidence, "
            "e.match AS match, origin.id AS origin_symbol_id, origin.fqn AS origin_fqn "
            "ORDER BY confidence DESC, caller_node_id",
            {"rid": entry_route_id},
        )
        inbound = inbound_http + inbound_async
        outbound = self._rows(
            f"MATCH (handler:Symbol)-[:EXPOSES]->(entry:Route {{id: $rid}}) "
            f"OPTIONAL MATCH (handler)-[:CALLS*0..{hops}]->(next:Symbol) "
            "RETURN DISTINCT handler.id AS handler_symbol_id, handler.fqn AS handler_fqn, "
            "handler.microservice AS handler_microservice, "
            "next.id AS next_symbol_id, next.fqn AS next_fqn, next.microservice AS next_microservice "
            "ORDER BY handler_symbol_id, next_symbol_id",
            {"rid": entry_route_id},
        )
        return {
            "entry_route_id": entry_route_id,
            "max_hops": hops,
            "inbound": inbound,
            "outbound": outbound,
        }

    # ---- outbound clients (LC3) ----

    _CLIENT_RETURN = (
        "c.id AS id, c.client_kind AS client_kind, c.target_service AS target_service, "
        "c.method AS method, c.path AS path, c.path_template AS path_template, "
        "c.path_regex AS path_regex, c.member_fqn AS member_fqn, c.member_id AS member_id, "
        "c.microservice AS microservice, c.module AS module, c.filename AS filename, "
        "c.start_line AS start_line, c.end_line AS end_line, c.resolved AS resolved, "
        "c.source_layer AS source_layer"
    )

    @staticmethod
    def _row_to_client_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("id") or ""),
            "client_kind": str(row.get("client_kind") or ""),
            "target_service": str(row.get("target_service") or ""),
            "method": str(row.get("method") or ""),
            "path": str(row.get("path") or ""),
            "path_template": str(row.get("path_template") or ""),
            "path_regex": str(row.get("path_regex") or ""),
            "member_fqn": str(row.get("member_fqn") or ""),
            "member_id": str(row.get("member_id") or ""),
            "microservice": str(row.get("microservice") or ""),
            "module": str(row.get("module") or ""),
            "filename": str(row.get("filename") or ""),
            "start_line": int(row.get("start_line") or 0),
            "end_line": int(row.get("end_line") or 0),
            "resolved": bool(row.get("resolved", True)),
            "source_layer": str(row.get("source_layer") or "builtin"),
        }

    def list_clients(
        self,
        *,
        microservice: str | None = None,
        client_kind: str | None = None,
        target_service: str | None = None,
        path_prefix: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        params: dict[str, Any] = {"lim": lim}
        preds: list[str] = []
        if microservice:
            params["microservice"] = microservice
            preds.append("c.microservice = $microservice")
        if client_kind:
            params["client_kind"] = client_kind
            preds.append("c.client_kind = $client_kind")
        if target_service:
            params["target_service"] = target_service
            preds.append("c.target_service = $target_service")
        if path_prefix:
            params["path_prefix"] = path_prefix
            preds.append("c.path STARTS WITH $path_prefix")
        if method is not None and method != "":
            params["method"] = method
            preds.append("c.method = $method")
        where = (" WHERE " + " AND ".join(preds)) if preds else ""
        q = (
            f"MATCH (c:Client){where} RETURN {self._CLIENT_RETURN} "
            f"ORDER BY c.microservice, c.client_kind, c.path, c.method, c.id LIMIT $lim"
        )
        return [self._row_to_client_dict(r) for r in self._rows(q, params)]

    _PRODUCER_RETURN = (
        "p.id AS id, p.producer_kind AS producer_kind, p.topic AS topic, p.broker AS broker, "
        "p.direction AS direction, p.member_fqn AS member_fqn, p.member_id AS member_id, "
        "p.microservice AS microservice, p.module AS module, p.filename AS filename, "
        "p.start_line AS start_line, p.end_line AS end_line, p.resolved AS resolved, "
        "p.source_layer AS source_layer"
    )

    @staticmethod
    def _row_to_producer_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("id") or ""),
            "producer_kind": str(row.get("producer_kind") or ""),
            "topic": str(row.get("topic") or ""),
            "broker": str(row.get("broker") or ""),
            "direction": str(row.get("direction") or ""),
            "member_fqn": str(row.get("member_fqn") or ""),
            "member_id": str(row.get("member_id") or ""),
            "microservice": str(row.get("microservice") or ""),
            "module": str(row.get("module") or ""),
            "filename": str(row.get("filename") or ""),
            "start_line": int(row.get("start_line") or 0),
            "end_line": int(row.get("end_line") or 0),
            "resolved": bool(row.get("resolved", True)),
            "source_layer": str(row.get("source_layer") or "builtin"),
        }

    def list_producers(
        self,
        *,
        microservice: str | None = None,
        producer_kind: str | None = None,
        topic_prefix: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        params: dict[str, Any] = {"lim": lim}
        preds: list[str] = []
        if microservice:
            params["microservice"] = microservice
            preds.append("p.microservice = $microservice")
        if producer_kind:
            params["producer_kind"] = producer_kind
            preds.append("p.producer_kind = $producer_kind")
        if topic_prefix:
            params["topic_prefix"] = topic_prefix
            preds.append("p.topic STARTS WITH $topic_prefix")
        where = (" WHERE " + " AND ".join(preds)) if preds else ""
        q = (
            f"MATCH (p:Producer){where} RETURN {self._PRODUCER_RETURN} "
            f"ORDER BY p.microservice, p.producer_kind, p.topic, p.id LIMIT $lim"
        )
        return [self._row_to_producer_dict(r) for r in self._rows(q, params)]

    # ---- used by search_lancedb.graph_expand ----

    def expand_fqns(self, fqns: list[str], *, depth: int = 1,
                    edge_types: list[str] | None = None,
                    direction: str = "both", limit: int = 200) -> list[str]:
        """Return neighbor FQNs (types only) for a batch of starting FQNs."""
        if not fqns or depth < 1:
            return []
        edges = edge_types or ["EXTENDS", "IMPLEMENTS", "INJECTS"]
        edge_pattern = "|".join(edges)
        if direction == "out":
            arrow_l, arrow_r = "-", "->"
        elif direction == "in":
            arrow_l, arrow_r = "<-", "-"
        else:
            arrow_l, arrow_r = "-", "-"
        q = (
            f"MATCH (root:Symbol) WHERE root.fqn IN $fqns "
            f"MATCH (root){arrow_l}[:{edge_pattern}*1..{int(depth)}]{arrow_r}(n:Symbol) "
            f"WHERE n.kind IN ['class','interface','enum','record','annotation'] AND n.resolved "
            f"RETURN DISTINCT n.fqn AS fqn LIMIT {int(limit)}"
        )
        return [r["fqn"] for r in self._rows(q, {"fqns": fqns}) if r.get("fqn")]
