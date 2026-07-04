from __future__ import annotations

from pathlib import Path

import ladybug

from build_ast_graph import GraphTables, write_ladybug
from ladybug_queries import LadybugGraph

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cross_service_smoke"


def _member_id(tables: GraphTables, *, parent_fqn: str, method_name: str) -> str:
    for member in tables.members:
        if member.parent_fqn == parent_fqn and member.decl.name == method_name:
            return member.node_id
    raise AssertionError(f"member not found: {parent_fqn}#{method_name}")


def test_feign_client_does_not_emit_exposes(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    feign_member_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    endpoint_member_id = _member_id(
        tables,
        parent_fqn="smoke.b.JoinControllerB",
        method_name="joinOperator",
    )
    exposes_sources = {row.symbol_id for row in tables.exposes_rows}
    assert feign_member_id not in exposes_sources
    assert endpoint_member_id in exposes_sources


def test_feign_caller_resolves_to_target_endpoint(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    client_ids = {
        e.client_id for e in tables.declares_client_rows if e.symbol_id == caller_id
    }
    row = next((r for r in tables.http_call_rows if r.client_id in client_ids), None)
    assert row is not None
    route = next((r for r in tables.routes_rows if r.id == row.route_id), None)
    assert route is not None
    assert row.match == "cross_service"
    assert route.kind == "http_endpoint"
    assert route.microservice == "svc-b"


def test_feign_route_node_is_not_emitted(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    assert not any(
        r.kind == "http_consumer"
        and r.framework == "feign"
        and r.path_template == "/chat/joinOperator"
        and r.microservice == "svc-a"
        for r in tables.routes_rows
    )


def test_meta_reports_exposes_suppressed_feign_count(tmp_path: Path, graph_tables_cross_service_smoke: GraphTables) -> None:
    db_path = tmp_path / "feign_meta.lbug"
    tables = graph_tables_cross_service_smoke
    write_ladybug(db_path, tables, source_root=_FIXTURE, verbose=False)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    assert LadybugGraph(str(db_path)).meta()["pass4_exposes_suppressed_feign"] == 0


def test_meta_returns_none_for_old_graphs(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_meta.lbug"
    db = ladybug.Database(str(db_path))
    conn = ladybug.Connection(db)
    conn.execute(
        "CREATE NODE TABLE GraphMeta("
        "key STRING PRIMARY KEY, "
        "ontology_version INT64, built_at INT64, source_root STRING, "
        "counts_json STRING, parse_errors INT64, "
        "routes_total INT64, exposes_total INT64, "
        "routes_by_framework STRING, "
        "routes_resolved_pct DOUBLE, "
        "routes_from_brownfield_pct DOUBLE, "
        "routes_by_layer STRING, "
        "http_calls_total INT64, "
        "async_calls_total INT64, "
        "http_calls_by_strategy STRING, "
        "async_calls_by_strategy STRING, "
        "http_calls_resolved_pct DOUBLE, "
        "async_calls_resolved_pct DOUBLE, "
        "http_clients_from_brownfield_pct DOUBLE, "
        "async_producers_from_brownfield_pct DOUBLE, "
        "http_calls_match_breakdown STRING, "
        "async_calls_match_breakdown STRING, "
        "cross_service_calls_total INT64, "
        "pass3_skipped_cross_service INT64, "
        "cross_service_resolution STRING)"
    )
    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: $t, "
        "source_root: $sr, counts_json: $cj, parse_errors: $pe, "
        "routes_total: $rt, exposes_total: $et, "
        "routes_by_framework: $rfw, routes_resolved_pct: $rrp, "
        "routes_from_brownfield_pct: $rfbp, routes_by_layer: $rbl, "
        "http_calls_total: $hct, async_calls_total: $act, "
        "http_calls_by_strategy: $hbs, async_calls_by_strategy: $abs, "
        "http_calls_resolved_pct: $hcrp, async_calls_resolved_pct: $acrp, "
        "http_clients_from_brownfield_pct: $hcbp, "
        "async_producers_from_brownfield_pct: $apbp, "
        "http_calls_match_breakdown: $hmb, async_calls_match_breakdown: $amb, "
        "cross_service_calls_total: $csct, pass3_skipped_cross_service: $p3, "
        "cross_service_resolution: $csr})",
        {
            "k": "graph",
            "ov": 8,
            "t": 0,
            "sr": "/tmp",
            "cj": "{}",
            "pe": 0,
            "rt": 0,
            "et": 0,
            "rfw": "{}",
            "rrp": 0.0,
            "rfbp": 0.0,
            "rbl": "{}",
            "hct": 0,
            "act": 0,
            "hbs": "{}",
            "abs": "{}",
            "hcrp": 0.0,
            "acrp": 0.0,
            "hcbp": 0.0,
            "apbp": 0.0,
            "hmb": "{}",
            "amb": "{}",
            "csct": 0,
            "p3": 0,
            "csr": "auto",
        },
    )
    conn.close()
    db.close()
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    assert LadybugGraph(str(db_path)).meta()["pass4_exposes_suppressed_feign"] is None


def test_no_change_to_async_routes(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    listener_id = _member_id(
        tables,
        parent_fqn="smoke.b.OrdersListenerB",
        method_name="onOrder",
    )
    route_by_id = {r.id: r for r in tables.routes_rows}
    assert any(
        row.symbol_id == listener_id and route_by_id[row.route_id].kind == "kafka_topic"
        for row in tables.exposes_rows
    )
