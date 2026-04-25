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
class ViaEdge:
    """Labelled edge from a previous-stage node to a stage symbol.

    Populated by `trace_flow` so callers can see *why* two types ended up
    in the same chain (e.g. `INJECTS` vs `IMPLEMENTS`) and at what hop
    from the frontier they were reached.
    """
    edge_type: str  # INJECTS | EXTENDS | IMPLEMENTS
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
                     limit: int = 100) -> list[SymbolHit]:
        filters = ["s.role = $role"]
        params: dict[str, Any] = {"role": role}
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    def list_by_annotation(self, annotation: str, *, module: str | None = None,
                           microservice: str | None = None,
                           limit: int = 100) -> list[SymbolHit]:
        # Kuzu supports `list_contains` for STRING[].
        filters = ["list_contains(s.annotations, $ann)"]
        params: dict[str, Any] = {"ann": annotation}
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        q = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
        return [_row_to_symbol(r) for r in self._rows(q, params)]

    # ---- edge traversals ----

    def find_implementors(self, interface_name_or_fqn: str, *,
                          module: str | None = None,
                          microservice: str | None = None,
                          limit: int = 100) -> list[SymbolHit]:
        filters = ["(i.name = $needle OR i.fqn = $needle)"]
        params: dict[str, Any] = {"needle": interface_name_or_fqn}
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
                        limit: int = 100) -> list[SymbolHit]:
        filters = ["(b.name = $needle OR b.fqn = $needle)"]
        params: dict[str, Any] = {"needle": class_name_or_fqn}
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
                       limit: int = 100) -> list[EdgeHit]:
        filters = ["(t.name = $needle OR t.fqn = $needle)"]
        params: dict[str, Any] = {"needle": target_name_or_fqn}
        filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
        where = " AND ".join(filters)
        # Project both sides of the edge with prefixed aliases (`s_*` / `t_*`)
        # so we can split rows back into source / target SymbolHits without
        # column-name collisions.
        s_proj = ", ".join(
            f"s.{c} AS s_{c}" for c in (
                "id", "kind", "name", "fqn", "package", "module", "microservice",
                "filename", "start_line", "end_line", "start_byte", "end_byte",
                "modifiers", "annotations", "role", "signature", "parent_id", "resolved",
            )
        )
        t_proj = ", ".join(
            f"t.{c} AS t_{c}" for c in (
                "id", "kind", "name", "fqn", "package", "module", "microservice",
                "filename", "start_line", "end_line", "start_byte", "end_byte",
                "modifiers", "annotations", "role", "signature", "parent_id", "resolved",
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
                   depth: int = 2, stage_limit: int = 20) -> list[list[StageSymbol]]:
        """Walk stages `CONTROLLER -> SERVICE/COMPONENT -> FEIGN_CLIENT/REPOSITORY/MAPPER`.

        Returns a list of stages; each stage is a list of SymbolHit. The first
        stage is the seed set (entrypoints matched by FQN, filtered to
        orchestrator-like roles — see `_ENTRYPOINT_ROLES`). If role-filtered
        seeds come back empty we fall back to unfiltered seeds so a caller
        with no CONTROLLER coverage still gets *something* back.
        Each subsequent stage is the neighbor-set (INJECTS+EXTENDS+IMPLEMENTS)
        of the previous stage, restricted to the stage's role allow-list.

        `depth` bounds the neighbor hop count per stage (default 2, max 3).
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
                filters.append("s.role IN $entry_roles")
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
                for row in self._rows(q, params):
                    sym = _row_to_symbol(row)
                    if not sym.fqn or sym.fqn in visited_fqns:
                        continue
                    edge = ViaEdge(
                        edge_type=str(row.get("edge_type") or ""),
                        from_fqn=str(row.get("from_fqn") or ""),
                        hop=hop,
                    )
                    existing = stage_results.get(sym.fqn)
                    if existing is None:
                        stage_results[sym.fqn] = StageSymbol(symbol=sym, via=[edge])
                        next_frontier.append(sym.fqn)
                        if len(stage_results) >= stage_limit:
                            break
                    else:
                        # Same symbol can be reached via multiple edges (e.g.
                        # both INJECTS and IMPLEMENTS); record up to a few.
                        if len(existing.via) < 4 and not any(
                            v.edge_type == edge.edge_type and v.from_fqn == edge.from_fqn
                            for v in existing.via
                        ):
                            existing.via.append(edge)
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
