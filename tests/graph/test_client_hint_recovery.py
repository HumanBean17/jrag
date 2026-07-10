from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from java_codebase_rag.graph.build_ast_graph import GraphTables, _match_call_edge, pass6_match_edges, write_ladybug
from java_codebase_rag.graph.ladybug_queries import LadybugGraph

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "cross_service_smoke"
_HTTP_CALLER = Path(__file__).resolve().parent.parent / "fixtures" / "http_caller_smoke"


def _build_tables() -> GraphTables:
    from _builders import build_graph_tables_to

    return build_graph_tables_to(_FIXTURE, max_pass=5)


def _member_id(tables: GraphTables, *, parent_fqn: str, method_name: str) -> str:
    for member in tables.members:
        if member.parent_fqn == parent_fqn and member.decl.name == method_name:
            return member.node_id
    raise AssertionError(f"member not found: {parent_fqn}#{method_name}")


def _first_http_call_for_member(tables: GraphTables, member_id: str):
    client_ids = {
        e.client_id for e in tables.declares_client_rows if e.symbol_id == member_id
    }
    row = next((r for r in tables.http_call_rows if r.client_id in client_ids), None)
    assert row is not None
    return row


def test_pass6_uses_client_hints_for_feign_resolution() -> None:
    tables = _build_tables()
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    row = _first_http_call_for_member(tables, caller_id)
    row.route_id = "missing:route:id"
    row.match = "unresolved"

    pass6_match_edges(tables, verbose=False)

    route_by_id = {r.id: r for r in tables.routes_rows}
    resolved = _first_http_call_for_member(tables, caller_id)
    assert resolved.match == "cross_service"
    assert route_by_id[resolved.route_id].microservice == "svc-b"


def test_cross_service_match_outcome_unchanged_after_client_migration() -> None:
    tables = _build_tables()
    pass6_match_edges(tables, verbose=False)
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    row = _first_http_call_for_member(tables, caller_id)
    assert row.match == "cross_service"


def test_find_route_callers_still_returns_expected_feign_caller(tmp_path: Path) -> None:
    tables = _build_tables()
    pass6_match_edges(tables, verbose=False)
    db_path = tmp_path / "client_hints.lbug"
    write_ladybug(db_path, tables, source_root=_FIXTURE, verbose=False)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    g = LadybugGraph(str(db_path))
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    callers = g.find_route_callers(
        None,
        microservice="svc-b",
        path_template="/chat/joinOperator",
        method="POST",
    )
    assert any(c.declaring_symbol_id == caller_id for c in callers)
    assert all(c.caller_node_kind == "client" for c in callers)


def test_pass6_async_rematch_uses_producer_row_kind() -> None:
    from _builders import build_graph_tables_to

    tables = build_graph_tables_to(_HTTP_CALLER, max_pass=5)
    producer = next(p for p in tables.producer_rows if p.producer_kind == "stream_bridge_send")
    row = next(r for r in tables.async_call_rows if r.producer_id == producer.id)
    assert row.match == "unresolved"
    row.route_id = "missing:route:id"

    seen_kinds: list[str] = []

    def capture(call, routes, caller_microservice):
        seen_kinds.append(call.client_kind)
        return _match_call_edge(call, routes, caller_microservice)

    with patch("java_codebase_rag.graph.build_ast_graph._match_call_edge", capture):
        pass6_match_edges(tables, verbose=False)

    assert "stream_bridge_send" in seen_kinds


def test_missing_client_hint_falls_back_to_existing_unresolved_or_phantom_flow() -> None:
    tables = _build_tables()
    caller_id = _member_id(
        tables,
        parent_fqn="smoke.a.BFeignClient",
        method_name="joinOperator",
    )
    row = _first_http_call_for_member(tables, caller_id)
    tables.declares_client_rows = [r for r in tables.declares_client_rows if r.symbol_id != caller_id]
    tables.client_rows = [c for c in tables.client_rows if c.member_id != caller_id]
    row.route_id = "missing:route:id"
    row.match = "unresolved"

    pass6_match_edges(tables, verbose=False)

    assert row.match in {"unresolved", "phantom"}
    assert row.match != "cross_service"
