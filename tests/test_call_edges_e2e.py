from __future__ import annotations

from pathlib import Path

import kuzu

from ast_java import ONTOLOGY_VERSION
from build_ast_graph import GraphTables, pass1_parse, pass2_edges, pass3_calls, pass4_routes, pass5_imperative_edges, write_kuzu
from kuzu_queries import KuzuGraph


_HTTP_CALLER_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "http_caller_smoke"


def _build(root: Path, db_path: Path) -> None:
    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=root, verbose=False)
    pass5_imperative_edges(tables, asts, source_root=root, verbose=False)
    write_kuzu(db_path, tables, source_root=root, verbose=False)


def _scalar(db_path: Path, query: str) -> int:
    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def test_http_calls_table_built_on_bank_chat(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "bank_http.kuzu"
    _build(corpus_root, db)
    assert _scalar(db, "MATCH (:Symbol)-[r:HTTP_CALLS]->(:Route) RETURN count(r)") >= 2


def test_async_calls_table_built_on_bank_chat(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "bank_async.kuzu"
    _build(corpus_root, db)
    assert _scalar(db, "MATCH (:Symbol)-[r:ASYNC_CALLS]->(:Route) RETURN count(r)") >= 5


def test_pr_d1_emits_unresolved_match_for_all(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "bank_match.kuzu"
    _build(corpus_root, db)
    assert _scalar(db, "MATCH ()-[r:HTTP_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0
    assert _scalar(db, "MATCH ()-[r:ASYNC_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0


def test_phantom_routes_dedup_across_call_sites(tmp_path: Path) -> None:
    db = tmp_path / "dedup.kuzu"
    _build(_HTTP_CALLER_FIXTURE, db)
    route_ids = _scalar(
        db,
        "MATCH (s:Symbol)-[r:HTTP_CALLS]->(rt:Route) "
        "WHERE rt.path_template='/api/users' AND rt.method='GET' AND rt.microservice='' RETURN count(DISTINCT rt.id)",
    )
    edges = _scalar(
        db,
        "MATCH (s:Symbol)-[r:HTTP_CALLS]->(rt:Route) "
        "WHERE rt.path_template='/api/users' AND rt.method='GET' AND rt.microservice='' RETURN count(r)",
    )
    assert route_ids == 1
    assert edges >= 2


def test_graph_meta_call_edge_counters(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "meta.kuzu"
    _build(corpus_root, db)
    m = KuzuGraph(str(db)).meta()
    assert m["http_calls_total"] > 0
    assert m["async_calls_total"] > 0
    assert isinstance(m["http_calls_by_strategy"], dict)
    assert isinstance(m["async_calls_by_strategy"], dict)
    assert 0.0 <= m["http_calls_resolved_pct"] <= 1.0
    assert 0.0 <= m["async_calls_resolved_pct"] <= 1.0


def test_ontology_version_matches_graph_meta(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "ontology.kuzu"
    _build(corpus_root, db)
    assert KuzuGraph(str(db)).meta()["ontology_version"] == ONTOLOGY_VERSION
