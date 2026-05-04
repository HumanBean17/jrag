"""Kuzu round-trip for `tests/fixtures/call_graph_smoke` (proposal §7.1 / §7.4 gaps)."""
from __future__ import annotations

from pathlib import Path

import kuzu

from build_ast_graph import GraphTables, pass1_parse, pass2_edges, pass3_calls, write_kuzu
from kuzu_queries import KuzuGraph

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "call_graph_smoke"


def _connect(db_path: Path) -> kuzu.Connection:
    return kuzu.Connection(kuzu.Database(str(db_path), read_only=True))


def _build_smoke_db(tmp_path: Path) -> Path:
    tables = GraphTables()
    asts = pass1_parse(_FIXTURE_ROOT, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    db_path = tmp_path / "smoke_graph.kuzu"
    write_kuzu(db_path, tables, source_root=_FIXTURE_ROOT, verbose=False)
    return db_path


def _rows(conn: kuzu.Connection, q: str) -> list:
    r = conn.execute(q)
    out: list = []
    while r.has_next():
        out.append(r.get_next())
    return out


def test_smoke_fixture_root_exists() -> None:
    assert _FIXTURE_ROOT.is_dir(), _FIXTURE_ROOT


def test_scope_receivers_calls_resolved_import_map(tmp_path: Path) -> None:
    """§7.1 #4–6: field / param / local `Svc` receiver → `Svc.work` via scope + import_map."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    for method in ("byField", "byParam", "byLocal"):
        q = (
            "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
            f"WHERE src.fqn STARTS WITH 'smoke.ScopeReceivers#' AND src.name = '{method}' "
            "AND dst.fqn STARTS WITH 'smoke.Svc#' AND dst.name = 'work' "
            "AND c.resolved = true AND c.strategy = 'import_map' "
            "RETURN count(*) AS n"
        )
        n = int(_rows(conn, q)[0][0])
        assert n >= 1, f"expected import_map CALLS for {method}"


def test_local_shadows_field_same_name_resolves_receiver(tmp_path: Path) -> None:
    """Local `dup` shadows field `dup` (String): `dup.work()` must target smoke.Svc."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    n = int(
        _rows(
            conn,
            "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
            "WHERE src.fqn STARTS WITH 'smoke.ScopeReceivers#shadowLocalOverField' "
            "AND dst.fqn STARTS WITH 'smoke.Svc#' AND dst.name = 'work' "
            "AND c.resolved = true AND c.strategy = 'import_map' "
            "RETURN count(*) AS n",
        )[0][0],
    )
    assert n >= 1


def test_wildcard_static_import_strategy(tmp_path: Path) -> None:
    """§7.1 #15: `import static …*` bare call → static_import_wildcard."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.WildcardStaticImport#' "
        "AND dst.name = 'wildHelper' AND c.resolved = true "
        "RETURN c.strategy AS s LIMIT 5",
    )
    strats = {str(r[0]) for r in rows}
    assert "static_import_wildcard" in strats, strats


def test_overload_sameArity_emits_two_overload_ambiguous_edges(tmp_path: Path) -> None:
    """§7.1 #13: two one-arg overloads → two resolved edges tagged overload_ambiguous."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.OverloadPatterns#sameArity' "
        "AND dst.name = 'amb' AND c.strategy = 'overload_ambiguous' "
        "RETURN dst.fqn AS fqn",
    )
    assert len(rows) == 2, f"expected 2 overload_ambiguous targets, got {rows}"


def test_overload_distinct_arities_single_targets(tmp_path: Path) -> None:
    """§7.1 #12: arity distinguishes overloads (no overload_ambiguous on arity())."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    amb = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.OverloadPatterns#arity' "
        "AND c.strategy = 'overload_ambiguous' "
        "RETURN count(*) AS n",
    )
    assert int(amb[0][0]) == 0
    n = int(
        _rows(
            conn,
            "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
            "WHERE src.fqn STARTS WITH 'smoke.OverloadPatterns#arity' "
            "AND dst.name = 'ovl' AND c.resolved = true "
            "RETURN count(*) AS n",
        )[0][0],
    )
    assert n == 2, "ovl(1) and ovl(1,2) should each resolve"


def test_expr_qualified_method_ref_chained_receiver(tmp_path: Path) -> None:
    """§7.1 #18 (graph): expression-qualified `getX()::trim` → chained_receiver phantom."""
    db = _build_smoke_db(tmp_path)
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.NestedCalls#m' AND dst.name = 'trim' "
        "RETURN c.strategy AS s, c.resolved AS r LIMIT 5",
    )
    assert rows, "expected a trim call site from NestedCalls.m"
    assert any(str(r[0]) == "chained_receiver" and r[1] is False for r in rows), rows


def test_find_callers_external_java_util_needle_lists_internal_callers(tmp_path: Path) -> None:
    """exclude_external filters callers (src) only: JDK needle still returns in-repo callers."""
    db = _build_smoke_db(tmp_path)
    try:
        KuzuGraph._instance = None
        KuzuGraph._instance_path = None
        g = KuzuGraph.get(str(db))
        edges = g.find_callers(
            "java.util.Objects#requireNonNull(1)",
            depth=1,
            limit=20,
            exclude_external=True,
        )
        assert edges
        assert any("StaticImportTest" in e.src.fqn for e in edges), [e.src.fqn for e in edges]
        assert all(not e.src.fqn.startswith("java.") for e in edges)
    finally:
        KuzuGraph._instance = None
        KuzuGraph._instance_path = None
