from __future__ import annotations

import re
from pathlib import Path

from ast_java import OutgoingCallDecl
from build_ast_graph import GraphTables, RouteRow, _match_call_edge


def _mk_call(**kwargs) -> OutgoingCallDecl:
    base = dict(
        method_fqn="smoke.X#m()",
        method_sig="m()",
        client_kind="rest_template",
        channel="http",
        feign_target_name="",
        feign_target_url="",
        path_template_call="/x",
        method_call="POST",
        topic_call="",
        broker_call="",
        raw_uri="/x",
        raw_topic="",
        resolution_strategy="rest_template",
        confidence_base=1.0,
        resolved=True,
        filename="A.java",
        start_line=1,
        end_line=1,
    )
    base.update(kwargs)
    return OutgoingCallDecl(**base)


def test_match_cross_service_resttemplate(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    assert any(r.match == "cross_service" for r in tables.http_call_rows)


def test_match_intra_service_resttemplate() -> None:
    routes = [
        RouteRow("r1", "http_endpoint", "", "POST", "", "/api/users", "^/api/users/?$", "", "", "", "", "svc-a", "", "", 0, 0, True),
    ]
    outcome, _ = _match_call_edge(_mk_call(path_template_call="/api/users"), routes, "svc-a")
    assert outcome == "intra_service"


def test_match_ambiguous_two_services_same_path() -> None:
    routes = [
        RouteRow("r1", "http_endpoint", "", "POST", "", "/api/users", "^/api/users/?$", "", "", "", "", "svc-a", "", "", 0, 0, True),
        RouteRow("r2", "http_endpoint", "", "POST", "", "/api/users", "^/api/users/?$", "", "", "", "", "svc-b", "", "", 0, 0, True),
    ]
    outcome, candidates = _match_call_edge(_mk_call(path_template_call="/api/users"), routes, "svc-c")
    assert outcome == "ambiguous"
    assert len(candidates) == 2


def test_match_phantom_external_url() -> None:
    outcome, _ = _match_call_edge(
        _mk_call(path_template_call="https://external.com/api/x", raw_uri="https://external.com/api/x"),
        [],
        "svc-a",
    )
    assert outcome == "phantom"


def test_match_unresolved_short_circuits() -> None:
    outcome, _ = _match_call_edge(
        _mk_call(resolved=False, path_template_call="", topic_call="", resolution_strategy="unresolved"),
        [
            RouteRow("r1", "http_endpoint", "", "GET", "", "/dynamic", "^/dynamic/?$", "", "", "", "", "svc-a", "", "", 0, 0, True),
        ],
        "svc-a",
    )
    assert outcome == "unresolved"


def test_feign_method_cross_service_match() -> None:
    routes = [
        RouteRow("r1", "http_endpoint", "feign", "POST", "", "", "", "", "", "svc-b", "", "svc-b", "", "", 0, 0, True),
    ]
    outcome, _ = _match_call_edge(
        _mk_call(client_kind="feign_method", feign_target_name="svc-b", path_template_call="", method_call=""),
        routes,
        "svc-a",
    )
    assert outcome == "cross_service"


def test_kafka_topic_broker_disambiguation() -> None:
    routes = [
        RouteRow("r1", "kafka_topic", "kafka", "", "", "", "", "orders", "", "", "", "svc-a", "", "", 0, 0, True),
        RouteRow("r2", "kafka_topic", "kafka", "", "", "", "", "orders", "secondary", "", "", "svc-b", "", "", 0, 0, True),
    ]
    outcome, candidates = _match_call_edge(
        _mk_call(channel="async", client_kind="kafka_send", method_call="", topic_call="orders", broker_call=""),
        routes,
        "svc-x",
    )
    assert outcome == "cross_service"
    assert len(candidates) == 1


def test_confidence_recomputed_per_outcome(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    by_match = {r.match: r.confidence for r in tables.http_call_rows}
    assert by_match.get("cross_service", 0.0) >= by_match.get("intra_service", 0.0)
    assert by_match.get("phantom", 0.0) <= by_match.get("cross_service", 1.0)


def test_phantom_routes_cleaned_up_when_real_match_found(graph_tables_cross_service_smoke: GraphTables) -> None:
    tables = graph_tables_cross_service_smoke
    inbound = {r.route_id for r in tables.http_call_rows} | {r.route_id for r in tables.async_call_rows}
    assert all(not (r.id.startswith("r:phantom:") and r.id not in inbound) for r in tables.routes_rows)


def test_graph_enrich_has_single_route_strategy_ladder() -> None:
    graph_enrich = Path(__file__).resolve().parent.parent / "graph_enrich.py"
    source = graph_enrich.read_text(encoding="utf-8")
    matches = re.findall(r"annotation.*spel.*constant_ref", source)
    assert len(matches) == 1
