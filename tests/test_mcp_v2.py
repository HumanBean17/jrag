from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from mcp.server.fastmcp.exceptions import ToolError

from mcp_v2 import (
    NodeFilter,
    _NODEFILTER_APPLICABLE_FIELDS,
    describe_v2,
    filter_frame_counters,
    find_v2,
    neighbors_v2,
    search_v2,
)


def _method_id_with_calls(kuzu_graph, direction: str) -> str:
    if direction == "in":
        rows = kuzu_graph._rows(  # noqa: SLF001
            "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) RETURN dst.id AS id LIMIT 1"
        )
    else:
        rows = kuzu_graph._rows(  # noqa: SLF001
            "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) RETURN src.id AS id LIMIT 1"
        )
    assert rows
    return str(rows[0]["id"])


def _method_id_declares_client_and_other_out_edge(kuzu_graph) -> str | None:
    """A method with DECLARES_CLIENT plus another out-label (Kuzu #119 strict-subset case)."""
    for pattern in (
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:CALLS]->() RETURN m.id AS id LIMIT 1",
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:HTTP_CALLS]->() RETURN m.id AS id LIMIT 1",
    ):
        rows = kuzu_graph._rows(pattern)  # noqa: SLF001
        if rows:
            return str(rows[0]["id"])
    return None


def _first_route_with_handler(kuzu_graph) -> str:
    for route in kuzu_graph.list_routes(limit=200):
        if kuzu_graph.find_route_handlers(route_id=route["id"]):
            return route["id"]
    raise AssertionError("expected a route with at least one handler")


def _first_route_with_callers(kuzu_graph) -> str:
    for route in kuzu_graph.list_routes(limit=200):
        if kuzu_graph.find_route_callers(route["id"]):
            return route["id"]
    raise AssertionError("expected a route with at least one caller")


def _fake_search_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "chunk:1",
            "symbol_id": "sym:1",
            "primary_type_fqn": "com.example.ChatService",
            "_rrf_score": 0.9,
            "text": "ChatService sample",
            "microservice": "chat-assign",
            "module": "chat-assign",
            "role": "SERVICE",
            "filename": "chat-assign/src/main/java/com/example/ChatAssignService.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 30},
        },
        {
            "id": "chunk:2",
            "symbol_id": "sym:2",
            "primary_type_fqn": "com.example.ChatController",
            "_rrf_score": 0.8,
            "text": "ChatController sample",
            "microservice": "chat-core",
            "module": "chat-app",
            "role": "CONTROLLER",
            "filename": "chat-core/chat-app/src/main/java/com/example/ChatController.java",
            "start": {"byte_offset": 40},
            "end": {"byte_offset": 80},
        },
    ]


def test_search_basic_returns_hits_with_symbol_id(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert out.results[0].symbol_id is not None


def test_search_filter_microservice(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"microservice": "chat-assign"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert {h.microservice for h in out.results} == {"chat-assign"}


def test_search_path_contains_filter(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatAssign", path_contains="ChatAssign", graph=kuzu_graph)
    assert out.success is True
    assert len(out.results) == 1


def test_find_symbol_by_role(kuzu_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert all(r.role == "CONTROLLER" for r in out.results if r.role is not None)


def test_find_symbol_empty_filter_returns_results(kuzu_graph) -> None:
    out = find_v2("symbol", {}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    # Regression guard: Symbol rows can include non-declaration kinds (e.g., package/file).
    assert all(isinstance(r.symbol_kind, str) and r.symbol_kind for r in out.results)


def test_find_symbol_by_symbol_kind_method(kuzu_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "method"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind == "method" for r in out.results)


def test_find_symbol_by_symbol_kind_interface(kuzu_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "interface"}, graph=kuzu_graph)
    assert out.success is True
    if not out.results:
        pytest.skip("fixture has no interface symbols")
    assert all(r.symbol_kind == "interface" for r in out.results)


def test_find_symbol_by_symbol_kinds_type_level(kuzu_graph) -> None:
    type_level_kinds = ["class", "interface", "enum", "record", "annotation"]
    out = find_v2("symbol", {"symbol_kinds": type_level_kinds}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind in set(type_level_kinds) for r in out.results)


def test_find_symbol_projection_includes_symbol_kind(kuzu_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "method"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert all(isinstance(r.symbol_kind, str) and r.symbol_kind for r in out.results)


def test_find_route_by_path_prefix(kuzu_graph) -> None:
    out = find_v2("route", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_find_client_by_client_kind(kuzu_graph) -> None:
    out = find_v2("client", {"client_kind": "feign_method"}, graph=kuzu_graph)
    assert out.success is True
    if not out.results:
        pytest.skip("fixture has no feign_method client rows")
    assert all("feign_method" in r.fqn for r in out.results)


def test_find_client_by_target_service(kuzu_graph) -> None:
    rows = kuzu_graph.list_clients(limit=500)
    seed = next((r for r in rows if str(r.get("target_service") or "").strip()), None)
    if seed is None:
        pytest.skip("no client rows with target_service in fixture")
    target_service = str(seed["target_service"])
    out = find_v2("client", {"target_service": target_service}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    assert all(r.fqn.startswith(f"{target_service} ") for r in out.results)


def test_find_client_by_path_prefix(kuzu_graph) -> None:
    all_clients = find_v2("client", {}, graph=kuzu_graph)
    assert all_clients.success is True
    sample = next((r for r in all_clients.results if "/" in r.fqn), None)
    if sample is None:
        pytest.skip("no client rows with path metadata in fixture")
    parts = sample.fqn.split(" ")
    path = parts[-1] if parts else ""
    if not path.startswith("/"):
        pytest.skip("sample client path is unavailable")
    prefix = path[: min(len(path), 5)]
    out = find_v2("client", {"target_path_prefix": prefix}, graph=kuzu_graph)
    assert out.success is True
    assert out.results
    for ref in out.results:
        bits = ref.fqn.split(" ")
        assert bits
        assert bits[-1].startswith(prefix)


def test_find_cross_kind_filter_fields_return_failure(kuzu_graph) -> None:
    out = find_v2("symbol", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_prefix" in out.message
    assert "kind='symbol'" in out.message


def test_find_unknown_filter_key_returns_failure(kuzu_graph) -> None:
    out = find_v2("symbol", {"typo_key": "x"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_find_symbol_only_field_with_kind_client_returns_failure(kuzu_graph) -> None:
    out = find_v2("client", {"fqn_prefix": "com.example"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "fqn_prefix" in out.message
    assert "kind='client'" in out.message


def test_find_client_only_field_with_kind_symbol_returns_failure(kuzu_graph) -> None:
    out = find_v2("symbol", {"client_kind": "feign_method"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "client_kind" in out.message
    assert "kind='symbol'" in out.message


def test_nodefilter_applicability_table_covers_all_fields() -> None:
    declared = set(NodeFilter.model_fields.keys())
    covered = set().union(*_NODEFILTER_APPLICABLE_FIELDS.values())
    assert declared == covered


def test_http_method_field_applies_to_route_kind(kuzu_graph) -> None:
    routes = kuzu_graph.list_routes(limit=2000)
    post_ids = {str(r["id"]) for r in routes if str(r.get("method") or "").upper() == "POST"}
    if not post_ids:
        pytest.skip("fixture has no POST routes")
    out = find_v2("route", {"http_method": "POST"}, graph=kuzu_graph, limit=500)
    assert out.success is True
    assert out.results
    assert {r.id for r in out.results} <= post_ids
    assert all(r.fqn == "POST" or r.fqn.startswith("POST ") for r in out.results)


def test_http_method_field_applies_to_client_kind(kuzu_graph) -> None:
    clients = kuzu_graph.list_clients(limit=2000)
    post_ids = {str(r["id"]) for r in clients if str(r.get("method") or "").upper() == "POST"}
    if not post_ids:
        pytest.skip("fixture has no POST clients")
    out = find_v2("client", {"http_method": "POST"}, graph=kuzu_graph, limit=500)
    assert out.success is True
    assert out.results
    assert {r.id for r in out.results} <= post_ids


def test_http_method_field_inapplicable_to_symbol(kuzu_graph) -> None:
    out = find_v2("symbol", {"http_method": "POST"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "http_method" in out.message
    assert "kind='symbol'" in out.message


def test_nodefilter_rejects_old_client_method_field() -> None:
    with pytest.raises(ValidationError) as excinfo:
        NodeFilter.model_validate({"client_method": "POST"})
    assert any("client_method" in str(e.get("loc", ())) for e in excinfo.value.errors())


async def test_find_missing_filter_rejected(mcp_server) -> None:
    with pytest.raises(ToolError, match="Field required"):
        await mcp_server.call_tool("find", {"kind": "symbol"})


def test_describe_symbol_returns_record(kuzu_graph) -> None:
    symbol = kuzu_graph.list_by_role("SERVICE", limit=1)[0]
    out = describe_v2(symbol.id, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "symbol"


def test_describe_route_returns_record(kuzu_graph) -> None:
    route = kuzu_graph.list_routes(limit=1)[0]
    out = describe_v2(route["id"], graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "route"


def test_describe_client_returns_record(kuzu_graph) -> None:
    class FakeGraph:
        def edge_counts_for(self, node_id: str) -> dict[str, dict[str, int]]:
            return {}

        def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if "MATCH (n:Client)" in query:
                return [
                    {
                        "id": "client:1",
                        "client_kind": "feign_method",
                        "target_service": "chat-core",
                        "method": "GET",
                        "path": "/api/chats",
                        "path_template": "/api/chats",
                        "path_regex": "",
                        "member_fqn": "com.example.Client#get()",
                        "member_id": "sym:client_member",
                        "microservice": "chat-assign",
                        "module": "chat-assign",
                        "filename": "Client.java",
                        "start_line": 1,
                        "end_line": 2,
                        "resolved": True,
                        "source_layer": "builtin",
                    }
                ]
            return []

    out = describe_v2("client:1", graph=FakeGraph())  # type: ignore[arg-type]
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "client"


def test_describe_unknown_id_returns_error(kuzu_graph) -> None:
    out = describe_v2("bogus:1", graph=kuzu_graph)
    assert out.success is False
    assert out.message


def test_describe_package_or_file_symbol_succeeds(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind IN ['package', 'file'] RETURN s.id AS id LIMIT 1"
    )
    if not rows:
        pytest.skip("fixture has no package/file symbol rows")
    out = describe_v2(str(rows[0]["id"]), graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "symbol"
    assert out.record.data.get("kind") in {"package", "file"}


def test_neighbors_in_calls(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "in")
    out = neighbors_v2(mid, direction="in", edge_types=["CALLS"], graph=kuzu_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_neighbors_out_calls(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], graph=kuzu_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_neighbors_edge_types_strict_subset_respects_label_filter(kuzu_graph) -> None:
    """Regression (#119): Kuzu can drop `label(e) IN $list`; use OR of `label(e) = $p` instead."""
    mid = _method_id_declares_client_and_other_out_edge(kuzu_graph)
    if mid is None:
        pytest.skip("no method with DECLARES_CLIENT and CALLS or HTTP_CALLS out-edges")
    dc_rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[e:DECLARES_CLIENT]->() WHERE m.id = $id RETURN count(e) AS n",
        {"id": mid},
    )
    assert dc_rows
    want_dc = int(dc_rows[0]["n"])
    assert want_dc >= 1
    out = neighbors_v2(mid, direction="out", edge_types=["DECLARES_CLIENT"], graph=kuzu_graph)
    assert out.success is True
    assert all(e.edge_type == "DECLARES_CLIENT" for e in out.results)
    assert len(out.results) == want_dc


def test_neighbors_route_in_exposes_returns_handler(kuzu_graph) -> None:
    route_id = _first_route_with_handler(kuzu_graph)
    out = neighbors_v2(route_id, direction="in", edge_types=["EXPOSES"], graph=kuzu_graph)
    assert out.success is True
    assert out.results


def test_neighbors_route_in_http_calls_returns_callers(kuzu_graph) -> None:
    class FakeGraph:
        def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if (
                "MATCH (a)<-[e]-(b)" in query
                and "WHERE a.id" in query
                and "RETURN b.id AS other_id" in query
            ):
                return [{"other_id": "sym:caller", "edge_type": "HTTP_CALLS", "confidence": 0.8, "match": "cross_service"}]
            if "MATCH (n:Symbol)" in query:
                return [
                    {
                        "id": "sym:caller",
                        "kind": "method",
                        "name": "call",
                        "fqn": "com.example.Caller#call()",
                        "package": "com.example",
                        "module": "chat-app",
                        "microservice": "chat-core",
                        "filename": "Caller.java",
                        "start_line": 1,
                        "end_line": 2,
                        "start_byte": 0,
                        "end_byte": 1,
                        "modifiers": [],
                        "annotations": [],
                        "capabilities": [],
                        "role": "SERVICE",
                        "signature": "call()",
                        "parent_id": "sym:parent",
                        "resolved": True,
                    }
                ]
            return []

    out = neighbors_v2(
        "route:one",
        direction="in",
        edge_types=["HTTP_CALLS", "ASYNC_CALLS"],
        graph=FakeGraph(),  # type: ignore[arg-type]
    )
    assert out.success is True
    assert len(out.results) == 1


def test_neighbors_batch_ids_carries_origin_id(kuzu_graph) -> None:
    one = _method_id_with_calls(kuzu_graph, "out")
    two = _method_id_with_calls(kuzu_graph, "in")
    out = neighbors_v2([one, two], direction="out", edge_types=["CALLS"], graph=kuzu_graph)
    assert out.success is True
    assert {e.origin_id for e in out.results} <= {one, two}


def test_neighbors_missing_direction_rejected(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, edge_types=["CALLS"], graph=kuzu_graph)


def test_neighbors_missing_edge_types_rejected(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", graph=kuzu_graph)


def test_neighbors_empty_edge_types_rejected(kuzu_graph) -> None:
    # Exercises TypeAdapter(min_length=1), distinct from missing edge_types (validate_call / Field(...)).
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", edge_types=[], graph=kuzu_graph)


def test_neighbors_invalid_direction_rejected(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="upstream", edge_types=["CALLS"], graph=kuzu_graph)


def test_neighbors_invalid_edge_type_rejected(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", edge_types=["calls"], graph=kuzu_graph)


def test_neighbors_rejects_composed_edge_summary_key(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(
            mid,
            direction="out",
            edge_types=["DECLARES.DECLARES_CLIENT"],
            graph=kuzu_graph,
        )


async def test_find_invalid_kind_rejected(mcp_server) -> None:
    with pytest.raises(ToolError, match="Input should be"):
        await mcp_server.call_tool("find", {"kind": "method", "filter": {}})


async def test_search_invalid_table_rejected(mcp_server) -> None:
    with pytest.raises(ToolError, match="Input should be"):
        await mcp_server.call_tool("search", {"query": "foo", "table": "code"})


def test_search_filter_accepts_json_string(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    want = {"microservice": "chat-assign"}
    out_dict = search_v2("ChatService", filter=want, graph=kuzu_graph)
    out_str = search_v2("ChatService", filter='{"microservice":"chat-assign"}', graph=kuzu_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_search_unknown_filter_key_returns_failure(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"typo_key": "x"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_search_cross_kind_filter_returns_failure(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_prefix" in out.message
    assert "kind='symbol'" in out.message


def test_search_filter_empty_string_treated_as_none(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    baseline = search_v2("ChatService", graph=kuzu_graph)
    empty = search_v2("ChatService", filter="", graph=kuzu_graph)
    whitespace = search_v2("ChatService", filter="   ", graph=kuzu_graph)
    assert baseline.success is True
    assert empty.success is True
    assert whitespace.success is True
    assert baseline.results == empty.results == whitespace.results


def test_search_filter_json_null_treated_as_none(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    baseline = search_v2("ChatService", graph=kuzu_graph)
    out = search_v2("ChatService", filter="null", graph=kuzu_graph)
    assert baseline.success is True
    assert out.success is True
    assert baseline.results == out.results


def test_find_filter_json_null_treated_as_empty_filter(kuzu_graph) -> None:
    empty = find_v2("symbol", {}, graph=kuzu_graph)
    out = find_v2("symbol", "null", graph=kuzu_graph)
    assert empty.success is True
    assert out.success is True
    assert empty.results == out.results


def test_find_filter_accepts_json_string(kuzu_graph) -> None:
    out_dict = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph)
    out_str = find_v2("symbol", '{"role":"CONTROLLER"}', graph=kuzu_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_find_symbol_kind_filter_accepts_json_string(kuzu_graph) -> None:
    out_dict = find_v2("symbol", {"symbol_kind": "method"}, graph=kuzu_graph)
    out_str = find_v2("symbol", '{"symbol_kind":"method"}', graph=kuzu_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_neighbors_filter_accepts_json_string(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    flt = {"role": "SERVICE"}
    out_dict = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter=flt, graph=kuzu_graph)
    out_str = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter='{"role":"SERVICE"}', graph=kuzu_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_neighbors_filter_unknown_key_returns_failure(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter={"typo_key": "x"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_neighbors_filter_cross_kind_on_neighbor_returns_failure(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter={"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_prefix" in out.message
    assert "kind='symbol'" in out.message


def test_neighbors_validate_call_still_raises(kuzu_graph) -> None:
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="upstream", edge_types=["CALLS"], graph=kuzu_graph)


def test_filter_invalid_json_returns_failure(monkeypatch, kuzu_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter="{not json", graph=kuzu_graph)
    assert out.success is False
    assert out.message is not None
    assert "JSON" in out.message


def test_wildcard_in_fqn_prefix_rejected(kuzu_graph) -> None:
    out = find_v2("symbol", {"fqn_prefix": "com.foo.*"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message
    assert "fqn_prefix" in out.message
    assert "search(query=..." in out.message


def test_wildcard_in_path_prefix_rejected(kuzu_graph) -> None:
    out = find_v2("route", {"path_prefix": "/api/*"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message
    assert "path_prefix" in out.message
    assert "search(query=..." in out.message


def test_wildcard_in_target_path_prefix_rejected(kuzu_graph) -> None:
    out = find_v2("client", {"target_path_prefix": "/api/*"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message
    assert "target_path_prefix" in out.message
    assert "search(query=..." in out.message


def test_wildcard_question_mark_in_fqn_prefix_rejected(kuzu_graph) -> None:
    out = find_v2("symbol", {"fqn_prefix": "com.foo.?"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message
    assert "fqn_prefix" in out.message


def test_search_wildcard_in_fqn_prefix_rejected_without_run_search(monkeypatch, kuzu_graph) -> None:
    calls: list[int] = []

    def boom(*_a, **_k):
        calls.append(1)
        return _fake_search_rows()

    monkeypatch.setattr("mcp_v2.run_search", boom)
    out = search_v2("anything", filter={"fqn_prefix": "com.*"}, graph=kuzu_graph)
    assert out.success is False
    assert out.message
    assert "fqn_prefix" in out.message
    assert calls == []


def test_neighbors_wildcard_in_filter_rejected_before_graph_query(kuzu_graph) -> None:
    class ExplodeGraph:
        def _rows(self, *_a, **_k) -> list:
            raise AssertionError("graph must not be queried when wildcard rejects filter")

    out = neighbors_v2(
        "sym:unused",
        direction="out",
        edge_types=["CALLS"],
        filter={"fqn_prefix": "com.*"},
        graph=ExplodeGraph(),  # type: ignore[arg-type]
    )
    assert out.success is False
    assert out.message
    assert "fqn_prefix" in out.message


def test_describe_by_fqn_returns_symbol(kuzu_graph) -> None:
    symbol = kuzu_graph.list_by_role("SERVICE", limit=1)[0]
    out = describe_v2(fqn=symbol.fqn, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.id == symbol.id
    assert out.record.kind == "symbol"
    assert out.message is None


def test_describe_by_fqn_unknown_returns_error(kuzu_graph) -> None:
    out = describe_v2(fqn="com.nonexistent.Foo", graph=kuzu_graph)
    assert out.success is False
    assert out.message == "No Symbol found for fqn='com.nonexistent.Foo'"


def test_describe_by_fqn_id_takes_precedence(kuzu_graph) -> None:
    svc = kuzu_graph.list_by_role("SERVICE", limit=1)[0]
    ctrl = kuzu_graph.list_by_role("CONTROLLER", limit=1)[0]
    out = describe_v2(id=svc.id, fqn=ctrl.fqn, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.id == svc.id
    assert str(out.record.data.get("role") or "") == "SERVICE"


def test_describe_by_fqn_duplicate_returns_first_with_disambiguation_hint() -> None:
    class DupFqnGraph:
        def _rows(self, query: str, params: dict | None = None) -> list:
            p = params or {}
            if "WHERE s.fqn = $fqn" in query:
                if p.get("fqn") == "com.fixture.DupeName":
                    return [{"id": "sym:dupe-a"}, {"id": "sym:dupe-b"}]
            if "MATCH (n:Symbol)" in query and "WHERE n.id = $id" in query:
                if p.get("id") == "sym:dupe-a":
                    return [
                        {
                            "id": "sym:dupe-a",
                            "kind": "file",
                            "name": "DupeName",
                            "fqn": "com.fixture.DupeName",
                            "package": "com.fixture",
                            "module": "fixture",
                            "microservice": "svc-a",
                            "filename": "DupeName.java",
                            "start_line": 1,
                            "end_line": 1,
                            "start_byte": 0,
                            "end_byte": 0,
                            "modifiers": [],
                            "annotations": [],
                            "capabilities": [],
                            "role": "",
                            "signature": "",
                            "parent_id": "",
                            "resolved": True,
                        }
                    ]
            return []

        def edge_counts_for(self, node_id: str) -> dict[str, dict[str, int]]:
            return {}

    out = describe_v2(fqn="com.fixture.DupeName", graph=DupFqnGraph())  # type: ignore[arg-type]
    assert out.success is True
    assert out.record is not None
    assert out.record.id == "sym:dupe-a"
    assert out.message
    assert "multiple symbols share this FQN" in out.message
    assert "find(kind='symbol'" in out.message
    assert "describe(id=..." in out.message
    assert "search(query=..." in out.message


def test_describe_by_fqn_requires_id_or_fqn(kuzu_graph) -> None:
    out = describe_v2(graph=kuzu_graph)
    assert out.success is False
    assert out.message == "id or fqn required"


def test_multi_value_symbol_kinds_or_semantics(kuzu_graph) -> None:
    out = find_v2("symbol", {"symbol_kinds": ["class", "interface"]}, graph=kuzu_graph, limit=200)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind in {"class", "interface"} for r in out.results)


def test_cross_field_and_semantics(kuzu_graph) -> None:
    controllers = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=50)
    assert controllers.success is True
    assert controllers.results
    ms = next((r.microservice for r in controllers.results if r.microservice), None)
    if not ms:
        pytest.skip("no controller with microservice in fixture")
    out = find_v2(
        "symbol",
        {"microservice": ms, "role": "CONTROLLER"},
        graph=kuzu_graph,
        limit=200,
    )
    assert out.success is True
    assert out.results
    assert all((r.microservice or "") == ms for r in out.results)
    assert all((r.role or "") == "CONTROLLER" for r in out.results)


def test_exclude_roles_negation_predicate(kuzu_graph) -> None:
    out = find_v2("symbol", {"exclude_roles": ["CONTROLLER"]}, graph=kuzu_graph, limit=500)
    assert out.success is True
    assert out.results
    assert not any(r.role == "CONTROLLER" for r in out.results)


def test_empty_filter_returns_full_result_set(kuzu_graph) -> None:
    out = find_v2("client", {}, graph=kuzu_graph)
    assert out.success is True
    assert out.results


def test_fail_loud_counter_increments_on_applicability_error(kuzu_graph) -> None:
    before = filter_frame_counters().get("applicability", 0)
    out = find_v2("symbol", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is False
    assert filter_frame_counters().get("applicability", 0) == before + 1


def test_fail_loud_counter_increments_on_wildcard_rejection(kuzu_graph) -> None:
    before = filter_frame_counters().get("wildcard", 0)
    out = find_v2("symbol", {"fqn_prefix": "com.foo.*"}, graph=kuzu_graph)
    assert out.success is False
    assert filter_frame_counters().get("wildcard", 0) == before + 1


def test_fail_loud_counter_categories_are_distinct(kuzu_graph) -> None:
    b_app = filter_frame_counters().get("applicability", 0)
    b_wild = filter_frame_counters().get("wildcard", 0)
    find_v2("symbol", {"path_prefix": "/x"}, graph=kuzu_graph)
    find_v2("symbol", {"fqn_prefix": "com.*"}, graph=kuzu_graph)
    assert filter_frame_counters().get("applicability", 0) == b_app + 1
    assert filter_frame_counters().get("wildcard", 0) == b_wild + 1


def test_fail_loud_counter_survives_multiple_calls(kuzu_graph) -> None:
    before = filter_frame_counters().get("applicability", 0)
    find_v2("symbol", {"http_method": "GET"}, graph=kuzu_graph)
    find_v2("symbol", {"http_method": "GET"}, graph=kuzu_graph)
    assert filter_frame_counters().get("applicability", 0) >= before + 2
