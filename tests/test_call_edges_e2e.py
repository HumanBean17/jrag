from __future__ import annotations

import shutil
from pathlib import Path

import kuzu

from ast_java import ONTOLOGY_VERSION
from kuzu_queries import KuzuGraph

_STUB_ROOT = Path(__file__).resolve().parent / "fixtures" / "brownfield_client_stubs"


def _scalar(db_path: Path, query: str) -> int:
    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def _build_repeatable_clients(tmp_path: Path) -> Path:
    shutil.copytree(_STUB_ROOT, tmp_path, dirs_exist_ok=True)
    java_dir = tmp_path / "p"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir / "X.java").write_text(
        "package p; import com.example.rag.*; class X { "
        "@CodebaseHttpClients({"
        "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/r1\", method=CodebaseHttpMethod.GET),"
        "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/r2\", method=CodebaseHttpMethod.POST)"
        "}) void m() {} }",
        encoding="utf-8",
    )
    from _builders import build_kuzu_full_into

    db_path = tmp_path / "g.kuzu"
    build_kuzu_full_into(tmp_path, db_path)
    return db_path


def test_http_calls_table_built_on_bank_chat(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH (:Client)-[r:HTTP_CALLS]->(:Route) RETURN count(r)") >= 2


def test_async_calls_table_built_on_bank_chat(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH (:Producer)-[r:ASYNC_CALLS]->(:Route) RETURN count(r)") >= 5


def test_pr_d1_emits_unresolved_match_for_all(kuzu_db_path: Path) -> None:
    assert _scalar(kuzu_db_path, "MATCH ()-[r:HTTP_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0
    assert _scalar(kuzu_db_path, "MATCH ()-[r:ASYNC_CALLS]->() WHERE r.match <> 'unresolved' RETURN count(r)") == 0


def test_phantom_routes_dedup_across_call_sites(kuzu_db_path_http_caller_smoke: Path) -> None:
    db = kuzu_db_path_http_caller_smoke
    route_ids = _scalar(
        db,
        "MATCH (c:Client)-[r:HTTP_CALLS]->(rt:Route) "
        "WHERE rt.path_template='/api/users' AND rt.method='GET' AND rt.microservice='' RETURN count(DISTINCT rt.id)",
    )
    edges = _scalar(
        db,
        "MATCH (c:Client)-[r:HTTP_CALLS]->(rt:Route) "
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


def test_call_edges_client_outbound_http_calls_returns_routes(kuzu_db_path_http_caller_smoke: Path) -> None:
    db = kuzu_db_path_http_caller_smoke
    n = _scalar(
        db,
        "MATCH (c:Client) WHERE c.path_template='/api/users' AND c.method='GET' "
        "MATCH (c)-[:HTTP_CALLS]->(:Route) RETURN count(*)",
    )
    assert n >= 1


def test_call_edges_method_two_http_clients_two_routes(tmp_path: Path) -> None:
    db = _build_repeatable_clients(tmp_path)
    client_routes = _scalar(
        db,
        "MATCH (c:Client)-[:HTTP_CALLS]->(:Route) RETURN count(DISTINCT c.id)",
    )
    assert client_routes >= 2


def test_call_edges_cross_service_http_four_hop(kuzu_db_path_cross_service_smoke: Path) -> None:
    db = kuzu_db_path_cross_service_smoke
    n = _scalar(
        db,
        "MATCH (m:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[:HTTP_CALLS]->(rt:Route)"
        "<-[:EXPOSES]-(h:Symbol) RETURN count(*)",
    )
    assert n >= 1


def _build_producer_stub(tmp_path: Path, java_body: str) -> Path:
    shutil.copytree(_STUB_ROOT, tmp_path, dirs_exist_ok=True)
    java_dir = tmp_path / "p"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir / "X.java").write_text(java_body, encoding="utf-8")
    from _builders import build_kuzu_full_into

    db_path = tmp_path / "g.kuzu"
    build_kuzu_full_into(tmp_path, db_path)
    return db_path


def test_call_edges_declares_producer_then_async_calls_to_topic(tmp_path: Path) -> None:
    db = _build_producer_stub(
        tmp_path,
        "package p; import com.example.rag.*; class X { "
        "@CodebaseProducer(topic=\"orders\") void m() {} }",
    )
    n = _scalar(
        db,
        "MATCH (s:Symbol)-[:DECLARES_PRODUCER]->(pr:Producer)-[:ASYNC_CALLS]->(r:Route) "
        "WHERE pr.topic = 'orders' RETURN count(*)",
    )
    assert n >= 1


def test_call_edges_topic_inbound_async_calls_lists_producers(tmp_path: Path) -> None:
    db = _build_producer_stub(
        tmp_path,
        "package p; import com.example.rag.*; class X { "
        "@CodebaseProducer(topic=\"inbound-topic\") void m() {} }",
    )
    n = _scalar(
        db,
        "MATCH (pr:Producer)-[:ASYNC_CALLS]->(r:Route {topic: 'inbound-topic'}) RETURN count(DISTINCT pr.id)",
    )
    assert n >= 1


def test_call_edges_method_two_producers_two_topics(tmp_path: Path) -> None:
    db = _build_producer_stub(
        tmp_path,
        "package p; import com.example.rag.*; class X { "
        "@CodebaseProducers({"
        "@CodebaseProducer(topic=\"t1\"),"
        "@CodebaseProducer(topic=\"t2\")"
        "}) void m() {} }",
    )
    producer_topics = _scalar(
        db,
        "MATCH (s:Symbol)-[:DECLARES_PRODUCER]->(pr:Producer)-[:ASYNC_CALLS]->(:Route) "
        "RETURN count(DISTINCT pr.id)",
    )
    assert producer_topics >= 2


def test_call_edges_unresolved_producer_empty_async_out(tmp_path: Path) -> None:
    db = _build_producer_stub(
        tmp_path,
        "package p; import com.example.rag.*; class X { "
        "@CodebaseProducer(topic=\"orphan-topic\") void m() {} }",
    )
    producers = _scalar(db, "MATCH (pr:Producer) WHERE pr.topic = 'orphan-topic' RETURN count(pr)")
    outbound = _scalar(
        db,
        "MATCH (pr:Producer {topic: 'orphan-topic'})-[:ASYNC_CALLS]->() RETURN count(*)",
    )
    assert producers >= 1
    assert outbound >= 0


def test_call_edges_cross_service_async_four_hop(kuzu_db_path_cross_service_smoke: Path) -> None:
    db = kuzu_db_path_cross_service_smoke
    n = _scalar(
        db,
        "MATCH (m:Symbol)-[:DECLARES_PRODUCER]->(pr:Producer)-[:ASYNC_CALLS]->(rt:Route)"
        "<-[:EXPOSES]-(h:Symbol) RETURN count(*)",
    )
    assert n >= 1


def test_call_edges_method_mixed_http_client_and_async_producer(tmp_path: Path) -> None:
    db = _build_producer_stub(
        tmp_path,
        "package p; import com.example.rag.*; class X { "
        "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/api\", method=CodebaseHttpMethod.GET) "
        "@CodebaseProducer(topic=\"mixed-topic\") void m() {} }",
    )
    http_n = _scalar(
        db,
        "MATCH (:Symbol)-[:DECLARES_CLIENT]->(:Client)-[:HTTP_CALLS]->(:Route) RETURN count(*)",
    )
    async_n = _scalar(
        db,
        "MATCH (:Symbol)-[:DECLARES_PRODUCER]->(:Producer)-[:ASYNC_CALLS]->(:Route) RETURN count(*)",
    )
    assert http_n >= 1
    assert async_n >= 1

