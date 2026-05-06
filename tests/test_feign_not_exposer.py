from __future__ import annotations

from pathlib import Path

import kuzu

from build_ast_graph import GraphTables, pass1_parse, pass2_edges, pass3_calls, pass4_routes
from build_ast_graph import pass5_imperative_edges, pass6_match_edges, write_kuzu
from kuzu_queries import KuzuGraph

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cross_service_smoke"


def _build_tables(project_root: Path) -> GraphTables:
    tables = GraphTables()
    asts = pass1_parse(project_root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=project_root, verbose=False)
    pass5_imperative_edges(tables, asts, source_root=project_root, verbose=False)
    pass6_match_edges(tables, verbose=False)
    return tables


def _member_id(tables: GraphTables, *, parent_fqn: str, method_name: str) -> str:
    for member in tables.members:
        if member.parent_fqn == parent_fqn and member.decl.name == method_name:
            return member.node_id
    raise AssertionError(f"member not found: {parent_fqn}#{method_name}")


def test_feign_client_does_not_emit_exposes() -> None:
    tables = _build_tables(_FIXTURE)
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


def test_feign_caller_resolves_to_target_endpoint() -> None:
    tables = _build_tables(_FIXTURE)
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    row = next((r for r in tables.http_call_rows if r.symbol_id == caller_id), None)
    assert row is not None
    route = next((r for r in tables.routes_rows if r.id == row.route_id), None)
    assert route is not None
    assert row.match == "cross_service"
    assert route.kind == "http_endpoint"
    assert route.microservice == "svc-b"


def test_feign_route_node_still_present() -> None:
    tables = _build_tables(_FIXTURE)
    assert any(
        r.kind == "http_consumer"
        and r.framework == "feign"
        and r.path_template == "/chat/joinOperator"
        and r.microservice == "svc-a"
        for r in tables.routes_rows
    )


def test_meta_reports_exposes_suppressed_feign_count(tmp_path: Path) -> None:
    db_path = tmp_path / "feign_meta.kuzu"
    tables = _build_tables(_FIXTURE)
    write_kuzu(db_path, tables, source_root=_FIXTURE, verbose=False)
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    assert KuzuGraph(str(db_path)).meta()["pass4_exposes_suppressed_feign"] == 1


def test_meta_returns_none_for_old_graphs(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_meta.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
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
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    assert KuzuGraph(str(db_path)).meta()["pass4_exposes_suppressed_feign"] is None


def test_no_change_to_async_routes() -> None:
    tables = _build_tables(_FIXTURE)
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
