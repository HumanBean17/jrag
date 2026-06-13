"""Kuzu round-trip for `tests/fixtures/call_graph_smoke` (proposal §7.1 / §7.4 gaps)."""
from __future__ import annotations

from pathlib import Path

import ladybug

from ladybug_queries import LadybugGraph

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "call_graph_smoke"


def _connect(db_path: Path) -> ladybug.Connection:
    return ladybug.Connection(ladybug.Database(str(db_path), read_only=True))


def _rows(conn: ladybug.Connection, q: str) -> list:
    r = conn.execute(q)
    out: list = []
    while r.has_next():
        out.append(r.get_next())
    return out


def test_smoke_fixture_root_exists() -> None:
    assert _FIXTURE_ROOT.is_dir(), _FIXTURE_ROOT


def test_this_super_field_chain_resolves_receiver_d6(ladybug_db_path_call_graph_smoke: Path) -> None:
    """D6: `this.root.mid.inner.target()` / `super.root.mid.inner.target()` — field chain, no calls in receiver."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    for type_prefix, method_name in (
        ("smoke.FieldChainReceivers#", "byThisChain"),
        ("smoke.FieldChainSub#", "bySuperChain"),
    ):
        rows = _rows(
            conn,
            "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
            f"WHERE src.fqn STARTS WITH '{type_prefix}' AND src.name = '{method_name}' "
            "AND dst.fqn STARTS WITH 'smoke.FieldChainLeaf#' AND dst.name = 'target' "
            "AND c.resolved = true AND c.strategy = 'import_map' "
            "RETURN c.confidence AS conf LIMIT 5",
        )
        assert rows, (
            f"expected import_map CALLS to FieldChainLeaf#target from {type_prefix}{method_name}"
        )
        assert all(float(r[0]) >= 0.94 for r in rows), rows


def test_scope_receivers_calls_resolved_import_map(ladybug_db_path_call_graph_smoke: Path) -> None:
    """§7.1 #4–6: field / param / local `Svc` receiver → `Svc.work` via scope + import_map."""
    db = ladybug_db_path_call_graph_smoke
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


def test_local_shadows_field_same_name_resolves_receiver(ladybug_db_path_call_graph_smoke: Path) -> None:
    """Local `dup` shadows field `dup` (String): `dup.work()` must target smoke.Svc."""
    db = ladybug_db_path_call_graph_smoke
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


def test_wildcard_static_import_strategy(ladybug_db_path_call_graph_smoke: Path) -> None:
    """§7.1 #15: `import static …*` bare call → static_import_wildcard."""
    db = ladybug_db_path_call_graph_smoke
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


def test_pass3_supertype_dedup_jpa_repository_save_one_row(
    ladybug_db_path_call_graph_smoke: Path,
) -> None:
    """SupertypeDedupPatterns: interface + concrete save → one CALLS row, REPOSITORY role."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.SupertypeDedupPatterns#persist' "
        "AND dst.name = 'save' "
        "RETURN count(*) AS n, c.callee_declaring_role AS role",
    )
    assert rows, "expected save call from SupertypeDedupPatterns#persist"
    assert int(rows[0][0]) == 1, rows
    assert str(rows[0][1]) == "REPOSITORY", rows


def test_pass3_overload_ambiguous_still_n_rows(ladybug_db_path_call_graph_smoke: Path) -> None:
    """overload_ambiguous sites keep N rows after supertype dedup (OverloadPatterns#sameArity)."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.OverloadPatterns#sameArity' "
        "AND dst.name = 'amb' AND c.strategy = 'overload_ambiguous' "
        "RETURN dst.fqn AS fqn",
    )
    assert len(rows) == 2, f"expected 2 overload_ambiguous targets, got {rows}"


def test_overload_sameArity_emits_two_overload_ambiguous_edges(ladybug_db_path_call_graph_smoke: Path) -> None:
    """§7.1 #13: two one-arg overloads → two resolved edges tagged overload_ambiguous."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.OverloadPatterns#sameArity' "
        "AND dst.name = 'amb' AND c.strategy = 'overload_ambiguous' "
        "RETURN dst.fqn AS fqn",
    )
    assert len(rows) == 2, f"expected 2 overload_ambiguous targets, got {rows}"


def test_overload_distinct_arities_single_targets(ladybug_db_path_call_graph_smoke: Path) -> None:
    """§7.1 #12: arity distinguishes overloads (no overload_ambiguous on arity())."""
    db = ladybug_db_path_call_graph_smoke
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


def test_expr_qualified_method_ref_chained_receiver(ladybug_db_path_call_graph_smoke: Path) -> None:
    """§7.1 #18 (graph): expression-qualified `getX()::trim` → chained_receiver UnresolvedCallSite."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    calls = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.NestedCalls#m' AND dst.name = 'trim' "
        "RETURN count(*) AS n",
    )
    assert int(calls[0][0]) == 0, "trim chained-receiver site must not be a CALLS row"
    ucs = _rows(
        conn,
        "MATCH (src:Symbol)-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
        "WHERE src.fqn STARTS WITH 'smoke.NestedCalls#m' AND u.callee_simple = 'trim' "
        "RETURN u.reason AS reason LIMIT 5",
    )
    assert ucs, "expected trim unresolved site from NestedCalls.m"
    assert any(str(r[0]) == "chained_receiver" for r in ucs), ucs


def test_anonymous_class_calls_attributed_to_synthetic_member(ladybug_db_path_call_graph_smoke: Path) -> None:
    """D3: `pingFromAnon()` inside `new Runnable(){ run(){...}}` → CALLS from synthetic `run()`, not NestedCalls#m."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    outer = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.NestedCalls#m' AND dst.name = 'pingFromAnon' "
        "RETURN count(*) AS n",
    )
    assert int(outer[0][0]) == 0, "pingFromAnon must not be called from NestedCalls#m"
    inner = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.NestedCalls.<anon:' AND src.name = 'run' "
        "AND dst.fqn STARTS WITH 'smoke.NestedCalls#pingFromAnon' "
        "RETURN count(*) AS n",
    )
    assert int(inner[0][0]) >= 1, "expected CALLS from synthetic anonymous run() to pingFromAnon"


def test_find_callers_external_java_util_needle_lists_internal_callers(ladybug_db_path_call_graph_smoke: Path) -> None:
    """exclude_external filters callers (src) only: JDK needle still returns in-repo callers."""
    db = ladybug_db_path_call_graph_smoke
    try:
        LadybugGraph._instance = None
        LadybugGraph._instance_path = None
        g = LadybugGraph(str(db))
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
        LadybugGraph._instance = None
        LadybugGraph._instance_path = None


# ---- B1: implicit default constructor resolution ----

def test_implicit_default_ctor_is_resolved(ladybug_db_path_call_graph_smoke: Path) -> None:
    """B1: `new Svc()` (Svc has no explicit ctor) resolves to Svc#<init>() with
    strategy='constructor' and resolved=true, not a phantom."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    for caller_method in ("byLocal", "shadowLocalOverField"):
        rows = _rows(
            conn,
            "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
            f"WHERE src.fqn STARTS WITH 'smoke.ScopeReceivers#{caller_method}' "
            "AND dst.fqn STARTS WITH 'smoke.Svc#<init>' "
            "AND c.resolved = true AND c.strategy = 'constructor' "
            "RETURN c.confidence AS conf LIMIT 5",
        )
        assert rows, (
            f"expected resolved constructor CALLS edge from ScopeReceivers#{caller_method} "
            f"to Svc#<init>; got none (B1 bug)"
        )
        assert all(float(r[0]) >= 0.90 for r in rows), rows


# ---- B2: implicit super to java.lang.Object ----

def test_implicit_super_to_object_uses_implicit_super_strategy(ladybug_db_path_call_graph_smoke: Path) -> None:
    """B2: WildUtils() has no extends clause; its synthesized implicit-super call must
    use strategy='implicit_super' and confidence=0.90, not phantom/0.0."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.WildUtils#WildUtils' "
        "AND dst.name = '<init>' "
        "RETURN c.strategy AS s, c.confidence AS conf, c.resolved AS r LIMIT 10",
    )
    assert rows, "expected an <init> call edge from WildUtils constructor (B2 bug)"
    phantom_rows = [r for r in rows if str(r[0]) == "phantom"]
    assert not phantom_rows, (
        f"WildUtils implicit-super should not be strategy='phantom'; got {rows}"
    )
    implicit_rows = [r for r in rows if str(r[0]) == "implicit_super"]
    assert implicit_rows, f"expected strategy='implicit_super', got {rows}"
    assert all(abs(float(r[1]) - 0.90) < 1e-9 for r in implicit_rows), implicit_rows
    assert all(r[2] is False for r in implicit_rows), implicit_rows


# ---- B3: static-import to JDK keeps high confidence ----

def test_static_import_to_jdk_keeps_high_confidence(ladybug_db_path_call_graph_smoke: Path) -> None:
    """B3: StaticImportTest.m calls requireNonNull via explicit static import.
    The edge must carry strategy='static_import', confidence>=0.95, resolved=false
    (callee is JDK phantom), not phantom/0.0."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.StaticImportTest#m' "
        "AND dst.name = 'requireNonNull' "
        "RETURN c.strategy AS s, c.confidence AS conf, c.resolved AS r LIMIT 10",
    )
    assert rows, "expected a requireNonNull call edge from StaticImportTest#m (B3 bug)"
    assert any(
        str(r[0]) == "static_import" and float(r[1]) >= 0.95 and r[2] is False
        for r in rows
    ), f"expected static_import edge with conf>=0.95 and resolved=false; got {rows}"


def test_min_confidence_filter_keeps_high_confidence_static_import_callers(
    ladybug_db_path_call_graph_smoke: Path,
) -> None:
    """B3: find_callers with min_confidence=0.9 must still return StaticImportTest
    for the JDK requireNonNull needle (previously returned empty because edge was 0.0)."""
    db = ladybug_db_path_call_graph_smoke
    try:
        LadybugGraph._instance = None
        LadybugGraph._instance_path = None
        g = LadybugGraph(str(db))
        edges = g.find_callers(
            "java.util.Objects#requireNonNull(1)",
            depth=1,
            limit=20,
            min_confidence=0.9,
            exclude_external=True,
        )
        assert edges, (
            "find_callers with min_confidence=0.9 returned no edges for requireNonNull; "
            "B3 fix not applied"
        )
        assert any("StaticImportTest" in e.src.fqn for e in edges), [e.src.fqn for e in edges]
        assert all(e.dst.fqn == "java.util.Objects#requireNonNull(?)" for e in edges), [
            e.dst.fqn for e in edges
        ]
    finally:
        LadybugGraph._instance = None
        LadybugGraph._instance_path = None


def test_d1_phantom_method_ref_and_invocation_share_symbol(ladybug_db_path_call_graph_smoke: Path) -> None:
    """D1: method ref (arg_count=-1) and normal call to same unindexed callee share one dst Symbol."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.PhantomMergeD1#m' AND dst.name = 'toString' "
        "RETURN count(DISTINCT dst.id) AS nids, "
        "collect(DISTINCT dst.fqn) AS fqns, "
        "collect(c.arg_count) AS arities",
    )
    assert rows, "expected CALLS edges to toString from PhantomMergeD1#m"
    nids = int(rows[0][0])
    fqns = rows[0][1]
    arities = rows[0][2]
    assert nids == 1, f"expected one phantom Symbol for toString, got nids={nids} fqns={fqns}"
    assert set(fqns) == {"smoke.Svc#toString(?)"}, fqns
    assert set(arities) == {-1, 0}, f"edges should keep site arities on CALLS; got {arities}"


def test_d2_method_ref_unambiguous_emits_resolved_arity_on_calls_edge(ladybug_db_path_call_graph_smoke: Path) -> None:
    """D2: single matching method for a :: ref — CALLS.arg_count is the method arity, not -1."""
    db = ladybug_db_path_call_graph_smoke
    conn = _connect(db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'smoke.MethodRefArity#caller' "
        "AND dst.name = 'onlyMethod' AND c.resolved = true "
        "RETURN c.arg_count AS ac, c.strategy AS s",
    )
    assert rows, "expected resolved CALLS from MethodRefArity#caller to onlyMethod"
    assert int(rows[0][0]) == 0, rows
    assert str(rows[0][1]) == "method_reference", rows
