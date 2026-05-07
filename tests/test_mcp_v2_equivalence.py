from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import mcp_v2
import server


def _structured(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if hasattr(result, "__iter__"):
        for block in result:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue
    raise AssertionError(f"cannot parse MCP payload: {result!r}")


def _ids_from_neighbors(out: mcp_v2.NeighborsOutput) -> set[str]:
    return {edge.other.id for edge in out.results}


def _symbol_id(kuzu_graph, needle: str) -> str:
    rows = kuzu_graph.find_by_name_or_fqn(needle, limit=1)
    assert rows
    return rows[0].id


def _fake_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "chunk:1",
            "_rrf_score": 0.9,
            "text": "x",
            "filename": "a.java",
            "start": {"byte_offset": 1},
            "end": {"byte_offset": 5},
            "symbol_id": "sym:1",
            "primary_type_fqn": "com.example.A",
        },
        {
            "id": "chunk:2",
            "_rrf_score": 0.8,
            "text": "y",
            "filename": "b.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 20},
            "symbol_id": "sym:2",
            "primary_type_fqn": "com.example.B",
        },
    ]


async def test_eq_codebase_search(monkeypatch, mcp_server) -> None:
    monkeypatch.setattr(server, "run_search", lambda *args, **kwargs: _fake_rows())
    monkeypatch.setattr(server, "_get_sentence_transformer", lambda *args, **kwargs: object())
    monkeypatch.setattr(mcp_v2, "run_search", lambda *args, **kwargs: _fake_rows())
    v1 = _structured(await mcp_server.call_tool("codebase_search", {"query": "x"}))
    v2 = mcp_v2.search_v2("x")
    v1_ids = {f"{r['file_path']}:{r['start_byte']}:{r['end_byte']}" for r in v1["results"]}
    v2_ids = {r.chunk_id for r in v2.results}
    assert v1_ids == v2_ids


def test_eq_find_implementors(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.find_implementors("EventProcessor", limit=200)}
    iid = _symbol_id(kuzu_graph, "EventProcessor")
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(iid, direction="in", edge_types=["IMPLEMENTS"], limit=200, graph=kuzu_graph))
    assert v1 == v2


def test_eq_find_subclasses(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.find_subclasses("JpaRepository", limit=200)}
    pid = _symbol_id(kuzu_graph, "JpaRepository")
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(pid, direction="in", edge_types=["EXTENDS"], limit=200, graph=kuzu_graph))
    assert v1 == v2


def test_eq_find_injectors(kuzu_graph) -> None:
    v1 = {r.src.id for r in kuzu_graph.find_injectors("AssignChatRepository", limit=200)}
    tid = _symbol_id(kuzu_graph, "AssignChatRepository")
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(tid, direction="in", edge_types=["INJECTS"], limit=200, graph=kuzu_graph))
    assert v1 == v2


def test_eq_find_callers(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    v1 = {e.src.id for e in kuzu_graph.find_callers(needle, limit=200)}
    sid = _symbol_id(kuzu_graph, needle)
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(sid, direction="in", edge_types=["CALLS"], limit=200, graph=kuzu_graph))
    assert v1 == v2


def test_eq_find_callees(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    v1 = {e.dst.id for e in kuzu_graph.find_callees(needle, limit=200, exclude_external=False)}
    sid = _symbol_id(kuzu_graph, needle)
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(sid, direction="out", edge_types=["CALLS"], limit=200, graph=kuzu_graph))
    assert v1 == v2


def test_eq_list_routes(kuzu_graph) -> None:
    v1 = {r["id"] for r in kuzu_graph.list_routes(limit=200)}
    v2 = {r.id for r in mcp_v2.find_v2("route", {}, limit=200, graph=kuzu_graph).results}
    assert v1 == v2


def test_eq_list_clients(kuzu_graph) -> None:
    v1 = {r["id"] for r in kuzu_graph.list_clients(limit=200)}
    v2 = {r.id for r in mcp_v2.find_v2("client", {}, limit=200, graph=kuzu_graph).results}
    assert v1 == v2


def test_eq_find_route_handlers(kuzu_graph) -> None:
    route_id = next(r["id"] for r in kuzu_graph.list_routes(limit=200) if kuzu_graph.find_route_handlers(route_id=r["id"]))
    v1 = {r["symbol"]["id"] for r in kuzu_graph.find_route_handlers(route_id=route_id)}
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(route_id, direction="in", edge_types=["EXPOSES"], graph=kuzu_graph))
    assert v1 == v2


def test_eq_find_route_callers(kuzu_graph) -> None:
    @dataclass
    class CallerInfo:
        caller_symbol_id: str
        caller_microservice: str
        confidence: float
        match: str

    class FakeGraph:
        def find_route_callers(self, route_id: str) -> list[CallerInfo]:
            return [CallerInfo("sym:caller", "chat-core", 0.8, "cross_service")]

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

    fake = FakeGraph()
    v1 = {r.caller_symbol_id for r in fake.find_route_callers("route:one")}
    v2 = _ids_from_neighbors(
        mcp_v2.neighbors_v2("route:one", direction="in", edge_types=["HTTP_CALLS", "ASYNC_CALLS"], graph=fake)  # type: ignore[arg-type]
    )
    assert v1 == v2


def test_eq_list_by_role(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.list_by_role("CONTROLLER", limit=200)}
    v2 = {r.id for r in mcp_v2.find_v2("symbol", {"role": "CONTROLLER"}, limit=200, graph=kuzu_graph).results}
    assert v1 == v2


def test_eq_list_by_annotation(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.list_by_annotation("Transactional", limit=200)}
    v2 = {r.id for r in mcp_v2.find_v2("symbol", {"annotation": "Transactional"}, limit=200, graph=kuzu_graph).results}
    assert v1 == v2


def test_eq_list_by_capability(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.list_by_capability("MESSAGE_LISTENER", limit=200)}
    v2 = {r.id for r in mcp_v2.find_v2("symbol", {"capability": "MESSAGE_LISTENER"}, limit=200, graph=kuzu_graph).results}
    assert v1 == v2


def test_eq_graph_neighbors(kuzu_graph) -> None:
    v1 = {r.id for r in kuzu_graph.neighbors("ChatManagementService", depth=1, edge_types=["INJECTS"], direction="out", limit=200)}
    sid = _symbol_id(kuzu_graph, "ChatManagementService")
    v2 = _ids_from_neighbors(mcp_v2.neighbors_v2(sid, direction="out", edge_types=["INJECTS"], limit=200, graph=kuzu_graph))
    assert v1 == v2
