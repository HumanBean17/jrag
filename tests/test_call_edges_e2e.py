from __future__ import annotations

from pathlib import Path

import kuzu

from ast_java import ONTOLOGY_VERSION
from kuzu_queries import KuzuGraph


def _scalar(db_path: Path, query: str) -> int:
    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def test_http_calls_table_built_on_bank_chat(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH (:Symbol)-[r:HTTP_CALLS]->(:Route) RETURN count(r)") >= 2


def test_async_calls_table_built_on_bank_chat(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH (:Symbol)-[r:ASYNC_CALLS]->(:Route) RETURN count(r)") >= 5


def test_pr_d1_emits_unresolved_match_for_all(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH ()-[r:HTTP_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0
    assert _scalar(kuzu_db_path, "MATCH ()-[r:ASYNC_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0


def test_phantom_routes_dedup_across_call_sites(kuzu_db_path_http_caller_smoke: Path) -> None:
    db = kuzu_db_path_http_caller_smoke
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


def test_graph_meta_call_edge_counters(kuzu_db_path: Path) -> None:
    m = KuzuGraph(str(kuzu_db_path)).meta()
    assert m["http_calls_total"] > 0
    assert m["async_calls_total"] > 0
    assert isinstance(m["http_calls_by_strategy"], dict)
    assert isinstance(m["async_calls_by_strategy"], dict)
    assert 0.0 <= m["http_calls_resolved_pct"] <= 1.0
    assert 0.0 <= m["async_calls_resolved_pct"] <= 1.0


def test_ontology_version_matches_graph_meta(kuzu_db_path: Path) -> None:
    assert KuzuGraph(str(kuzu_db_path)).meta()["ontology_version"] == ONTOLOGY_VERSION
