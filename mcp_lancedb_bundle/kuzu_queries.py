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

__all__ = [
    "KuzuGraph",
    "resolve_kuzu_path",
    "SymbolHit",
    "EdgeHit",
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
    service: str
    filename: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    modifiers: list[str]
    annotations: list[str]
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


def _symbol_return_for(alias: str) -> str:
    """Kuzu RETURN projection for Symbol properties, using the given node alias.

    Centralised so queries that bind Symbol under a non-`s` alias (e.g. `n` in
    graph-expansion / flow-tracing) don't emit `s.*` references that Kuzu
    rejects with `Variable s is not in scope`.
    """
    return (
        f"{alias}.id AS id, {alias}.kind AS kind, {alias}.name AS name, {alias}.fqn AS fqn, "
        f"{alias}.package AS package, {alias}.service AS service, {alias}.filename AS filename, "
        f"{alias}.start_line AS start_line, {alias}.end_line AS end_line, "
        f"{alias}.start_byte AS start_byte, {alias}.end_byte AS end_byte, "
        f"{alias}.modifiers AS modifiers, {alias}.annotations AS annotations, "
        f"{alias}.role AS role, {alias}.signature AS signature, "
        f"{alias}.parent_id AS parent_id, {alias}.resolved AS resolved"
    )


_SYMBOL_RETURN = _symbol_return_for("s")


def _row_to_symbol(row: dict[str, Any]) -> SymbolHit:
    return SymbolHit(
        id=row.get("id", "") or "",
        kind=row.get("kind", "") or "",
        name=row.get("name", "") or "",
        fqn=row.get("fqn", "") or "",
        package=row.get("package", "") or "",
        service=row.get("service", "") or "",
        filename=row.get("filename", "") or "",
        start_line=int(row.get("start_line") or 0),
        end_line=int(row.get("end_line") or 0),
        start_byte=int(row.get("start_byte") or 0),
        end_byte=int(row.get("end_byte") or 0),
        modifiers=list(row.get("modifiers") or []),
        annotations=list(row.get("annotations") or []),
        role=row.get("role", "") or "",
        signature=row.get("signature", "") or "",
        parent_id=row.get("parent_id", "") or "",
        resolved=bool(row.get("resolved", True)),
    )


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
                cls._instance = cls(resolved)
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

    # ---- symbol-level lookups ----

    def find_by_name_or_fqn(self, name_or_fqn: str, *, kinds: list[str] | None = None,
                            service: str | None = None, limit: int = 50) -> list[SymbolHit]:
        filters = ["(s.name = $needle OR s.fqn = $needle)"]
        params: dict[str, Any] = {"needle": name_or_fqn}
        if kinds:
            params["kinds"] = kinds
            filters.append("s.kind IN $kinds")
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_role(self, role: str, *, service: str | None = None, limit: int = 100) -> list[SymbolHit]:
        filters = ["s.role = $role"]
        params: dict[str, Any] = {"role": role}
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_annotation(self, annotation: str, *, service: str | None = None, limit: int = 100) -> list[SymbolHit]:
        # Kuzu supports `list_contains` for STRING[].
        filters = ["list_contains(s.annotations, $ann)"]
        params: dict[str, Any] = {"ann": annotation}
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    # ---- edge traversals ----

    def find_implementors(self, interface_name_or_fqn: str, *, service: str | None = None,
                          limit: int = 100) -> list[SymbolHit]:
        filters = ["(i.name = $needle OR i.fqn = $needle)"]
        params: dict[str, Any] = {"needle": interface_name_or_fqn}
        if service:
            params["service"] = service
            filters.append("c.service = $service")
        where = " AND ".join(filters)
        q = (
            f"MATCH (c:Symbol)-[:IMPLEMENTS]->(i:Symbol) WHERE {where} "
            f"RETURN DISTINCT c.id AS id, c.kind AS kind, c.name AS name, c.fqn AS fqn, "
            f"c.package AS package, c.service AS service, c.filename AS filename, "
            f"c.start_line AS start_line, c.end_line AS end_line, "
            f"c.start_byte AS start_byte, c.end_byte AS end_byte, "
            f"c.modifiers AS modifiers, c.annotations AS annotations, "
            f"c.role AS role, c.signature AS signature, c.parent_id AS parent_id, c.resolved AS resolved "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def find_subclasses(self, class_name_or_fqn: str, *, service: str | None = None,
                        limit: int = 100) -> list[SymbolHit]:
        filters = ["(b.name = $needle OR b.fqn = $needle)"]
        params: dict[str, Any] = {"needle": class_name_or_fqn}
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q = (
            f"MATCH (s:Symbol)-[:EXTENDS]->(b:Symbol) WHERE {where} "
            f"RETURN DISTINCT s.id AS id, s.kind AS kind, s.name AS name, s.fqn AS fqn, "
            f"s.package AS package, s.service AS service, s.filename AS filename, "
            f"s.start_line AS start_line, s.end_line AS end_line, "
            f"s.start_byte AS start_byte, s.end_byte AS end_byte, "
            f"s.modifiers AS modifiers, s.annotations AS annotations, "
            f"s.role AS role, s.signature AS signature, s.parent_id AS parent_id, s.resolved AS resolved "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def find_injectors(self, target_name_or_fqn: str, *, service: str | None = None,
                       limit: int = 100) -> list[EdgeHit]:
        filters = ["(t.name = $needle OR t.fqn = $needle)"]
        params: dict[str, Any] = {"needle": target_name_or_fqn}
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q = (
            f"MATCH (s:Symbol)-[e:INJECTS]->(t:Symbol) WHERE {where} "
            f"RETURN "
            f"s.id AS s_id, s.kind AS s_kind, s.name AS s_name, s.fqn AS s_fqn, "
            f"s.package AS s_package, s.service AS s_service, s.filename AS s_filename, "
            f"s.start_line AS s_start_line, s.end_line AS s_end_line, "
            f"s.start_byte AS s_start_byte, s.end_byte AS s_end_byte, "
            f"s.modifiers AS s_modifiers, s.annotations AS s_annotations, "
            f"s.role AS s_role, s.signature AS s_signature, s.parent_id AS s_parent_id, "
            f"s.resolved AS s_resolved, "
            f"t.id AS t_id, t.kind AS t_kind, t.name AS t_name, t.fqn AS t_fqn, "
            f"t.package AS t_package, t.service AS t_service, t.filename AS t_filename, "
            f"t.start_line AS t_start_line, t.end_line AS t_end_line, "
            f"t.start_byte AS t_start_byte, t.end_byte AS t_end_byte, "
            f"t.modifiers AS t_modifiers, t.annotations AS t_annotations, "
            f"t.role AS t_role, t.signature AS t_signature, t.parent_id AS t_parent_id, "
            f"t.resolved AS t_resolved, "
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

    def neighbors(self, fqn_or_name: str, *, depth: int = 1,
                  edge_types: list[str] | None = None,
                  direction: str = "both", limit: int = 200) -> list[SymbolHit]:
        """BFS over `edge_types` up to `depth` hops. `direction` in {out, in, both}."""
        if depth < 1:
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
            f"MATCH (root:Symbol) WHERE root.name = $needle OR root.fqn = $needle "
            f"MATCH path = (root){arrow_l}[:{edge_pattern}*1..{int(depth)}]{arrow_r}(n:Symbol) "
            f"RETURN DISTINCT n.id AS id, n.kind AS kind, n.name AS name, n.fqn AS fqn, "
            f"n.package AS package, n.service AS service, n.filename AS filename, "
            f"n.start_line AS start_line, n.end_line AS end_line, "
            f"n.start_byte AS start_byte, n.end_byte AS end_byte, "
            f"n.modifiers AS modifiers, n.annotations AS annotations, "
            f"n.role AS role, n.signature AS signature, "
            f"n.parent_id AS parent_id, n.resolved AS resolved "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_symbol(r) for r in self._rows(q, {"needle": fqn_or_name})]

    def impact_analysis(self, fqn_or_name: str, *, depth: int = 2,
                        limit: int = 300) -> list[SymbolHit]:
        """Reverse closure over INJECTS + IMPLEMENTS (who breaks if `fqn` changes)."""
        q = (
            f"MATCH (target:Symbol) WHERE target.name = $needle OR target.fqn = $needle "
            f"MATCH (n:Symbol)-[:INJECTS|IMPLEMENTS|EXTENDS*1..{int(depth)}]->(target) "
            f"RETURN DISTINCT n.id AS id, n.kind AS kind, n.name AS name, n.fqn AS fqn, "
            f"n.package AS package, n.service AS service, n.filename AS filename, "
            f"n.start_line AS start_line, n.end_line AS end_line, "
            f"n.start_byte AS start_byte, n.end_byte AS end_byte, "
            f"n.modifiers AS modifiers, n.annotations AS annotations, "
            f"n.role AS role, n.signature AS signature, "
            f"n.parent_id AS parent_id, n.resolved AS resolved "
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

    def trace_flow(self, seed_fqns: list[str], *, service: str | None = None,
                   depth: int = 2, stage_limit: int = 20) -> list[list[SymbolHit]]:
        """Walk stages `CONTROLLER -> SERVICE/COMPONENT -> FEIGN_CLIENT/REPOSITORY/MAPPER`.

        Returns a list of stages; each stage is a list of SymbolHit. The first
        stage is the seed set (entrypoints matched by FQN, filtered by role).
        Each subsequent stage is the neighbor-set (INJECTS+EXTENDS+IMPLEMENTS)
        of the previous stage, restricted to the stage's role allow-list.

        `depth` bounds the neighbor hop count per stage (default 2, max 3).
        """
        if not seed_fqns:
            return []
        depth = max(1, min(3, int(depth)))

        stages: list[list[SymbolHit]] = []
        visited_fqns: set[str] = set()

        # Stage 0: resolve seeds.
        filters = ["s.fqn IN $fqns"]
        params: dict[str, Any] = {"fqns": list(seed_fqns)}
        if service:
            params["service"] = service
            filters.append("s.service = $service")
        where = " AND ".join(filters)
        q0 = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(stage_limit)}"
        seed_rows = [_row_to_symbol(r) for r in self._rows(q0, params)]
        if not seed_rows:
            return []
        stages.append(seed_rows)
        for h in seed_rows:
            if h.fqn:
                visited_fqns.add(h.fqn)

        frontier_fqns = [h.fqn for h in seed_rows if h.fqn]
        for stage_roles in self._FLOW_STAGES[1:]:
            if not frontier_fqns:
                break
            params = {
                "fqns": frontier_fqns,
                "roles": list(stage_roles),
            }
            svc_filter = ""
            if service:
                params["service"] = service
                svc_filter = " AND n.service = $service"
            q = (
                f"MATCH (root:Symbol) WHERE root.fqn IN $fqns "
                f"MATCH (root)-[:INJECTS|EXTENDS|IMPLEMENTS*1..{depth}]-(n:Symbol) "
                f"WHERE n.role IN $roles AND n.resolved{svc_filter} "
                f"RETURN DISTINCT {_symbol_return_for('n')} LIMIT {int(stage_limit)}"
            )
            rows = [_row_to_symbol(r) for r in self._rows(q, params)]
            rows = [r for r in rows if r.fqn and r.fqn not in visited_fqns]
            if not rows:
                break
            stages.append(rows)
            for r in rows:
                visited_fqns.add(r.fqn)
            frontier_fqns = [r.fqn for r in rows if r.fqn]
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
