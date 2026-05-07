from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from mcp.server.fastmcp.exceptions import ToolError

from mcp_v2 import (
    describe_v2,
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
    all_clients = find_v2("client", {}, graph=kuzu_graph)
    assert all_clients.success is True
    target = next((r for r in all_clients.results if r.fqn.strip()), None)
    if target is None:
        pytest.skip("no client rows with target metadata in fixture")
    target_service = target.fqn.split(" ", 1)[0]
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


def test_find_silent_ignore_irrelevant_filter_keys(kuzu_graph) -> None:
    out = find_v2("symbol", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert out.success is True
    assert isinstance(out.results, list)


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


def test_neighbors_route_in_exposes_returns_handler(kuzu_graph) -> None:
    route_id = _first_route_with_handler(kuzu_graph)
    out = neighbors_v2(route_id, direction="in", edge_types=["EXPOSES"], graph=kuzu_graph)
    assert out.success is True
    assert out.results


def test_neighbors_route_in_http_calls_returns_callers(kuzu_graph) -> None:
    class FakeGraph:
        def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if "MATCH (a)<-[e]-(b)" in query:
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
    mid = _method_id_with_calls(kuzu_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", edge_types=[], graph=kuzu_graph)
