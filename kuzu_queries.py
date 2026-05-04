"""Read-only Cypher helpers over the Kuzu AST graph built by `build_ast_graph.py`.

Each function opens a Kuzu connection on demand and returns plain JSON-ish dicts
so the MCP server can serialize them without further mapping.

The Kuzu database is opened read-only and cached per-process. This module is
intentionally dependency-light: nothing here imports LanceDB or sentence-transformers.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import kuzu

from ast_java import ONTOLOGY_VERSION as _ONTOLOGY_VERSION

__all__ = [
    "KuzuGraph",
    "resolve_kuzu_path",
    "SymbolHit",
    "EdgeHit",
    "CallEdge",
    "ViaEdge",
    "StageSymbol",
]


def resolve_kuzu_path(explicit: str | None = None) -> str:
    """Resolve the Kuzu DB path the same way the builder does."""
    if explicit:
        return str(Path(explicit).expanduser())
    env = os.environ.get("KUZU_DB_PATH", "").strip()
    if env:
        return str(Path(os.path.expanduser(env)))
    lance = os.environ.get("LANCEDB_URI", "").strip()
    if lance and not lance.startswith(("s3://", "gs://", "az://")):
        return str(Path(os.path.expanduser(lance.rstrip("/"))) / "code_graph.kuzu")
    return "./lancedb_data/code_graph.kuzu"


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
    edge_type: str  # INJECTS | EXTENDS | IMPLEMENTS | CALLS
    from_fqn: str
    hop: int  # 1 = direct neighbour of previous-stage frontier


@dataclass
class StageSymbol:
    """A trace_flow stage entry: the symbol plus the edges that pulled it in.

    Stage 0 (seeds) has `via=[]`. Later stages list every first-time path
    from the previous frontier to `symbol`.
    """
    symbol: SymbolHit
    via: list[ViaEdge]


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
                        "Run: LANCEDB_MCP_ALLOW_REFRESH=1 refresh_code_index(confirm=true) "
                        "or: python build_ast_graph.py --source-root <repo>"
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
        try:
            rows = self._rows(
                "MATCH (m:GraphMeta) RETURN m.key AS key, m.ontology_version AS ontology_version, "
                "m.built_at AS built_at, m.source_root AS source_root, "
                "m.counts_json AS counts_json, m.parse_errors AS parse_errors"
            )
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
        return {
            "ontology_version": int(row.get("ontology_version") or 0),
            "built_at": int(row.get("built_at") or 0),
            "source_root": row.get("source_root") or "",
            "parse_errors": int(row.get("parse_errors") or 0),
            "counts": counts,
            "db_path": self.db_path,
        }

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
        ("FEIGN_CLIENT", "REPOSITORY", "MAPPER"),
    )

    # Stage-0 accepts any entrypoint-like role. COMPONENT is included because
    # Kafka listeners / @Scheduled orchestrators are frequently plain
    # @Component, not @Controller; SERVICE is included so we don't drop
    # orchestrator seeds when the caller already narrowed the vector search
    # to services.
    _ENTRYPOINT_ROLES: tuple[str, ...] = (
        "CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT",
    )

    def trace_flow(self, seed_fqns: list[str], *,
                   module: str | None = None,
                   microservice: str | None = None,
                   depth: int = 2, stage_limit: int = 20,
                   follow_calls: bool = True,
                   min_call_confidence: float = 0.0,
                   exclude_external: bool = True) -> list[list[StageSymbol]]:
        """Walk stages `CONTROLLER -> SERVICE/COMPONENT -> FEIGN_CLIENT/REPOSITORY/MAPPER`.

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

                # Structural-first budget: CALLS only tops up the slots
                # structural didn't already claim. Skip the round-trip when
                # the bucket is already full at this hop.
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
                        "AND n.resolved AND n.kind IN ['class','interface','enum','record','annotation'] "
                        f"AND c.confidence >= $mc{sccf} "
                        f"RETURN {_symbol_return_for('n')}, 'CALLS' AS edge_type, root.fqn AS from_fqn "
                        f"LIMIT {max(1, remaining * 4)}"
                    )
                    for row in self._rows(qcf, params_cf):
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
