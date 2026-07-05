from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from mcp.server.fastmcp.exceptions import ToolError

from java_ontology import VALID_RESOLVE_REASONS

from mcp_v2 import (
    Edge,
    NodeFilter,
    _NODEFILTER_APPLICABLE_FIELDS,
    describe_v2,
    filter_frame_counters,
    find_v2,
    neighbors_v2,
    resolve_v2,
    search_v2,
)
from pinned_ids import client_message_processor_process_id

import importlib.util


def _vector_stack_available() -> bool:
    """True when the optional vector stack (torch/sentence-transformers/lancedb) is installed.

    The ``search`` tool loads a SentenceTransformer model, so the search/filter unit tests
    need it even when ``run_search`` is monkeypatched. Skip them on graph-only installs
    (macOS Intel, where the vector trio is gated off by PEP 508 markers).
    """
    return all(importlib.util.find_spec(m) is not None for m in ("sentence_transformers", "lancedb"))


needs_vectors = pytest.mark.skipif(
    not _vector_stack_available(),
    reason="vector stack not installed (graph-only install; macOS Intel)",
)

_PR2_CHAIN_SEARCH_DESCRIBE = re.compile(r"search\(query=.*\).*describe")
_PR2_SENTINEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"per\.candidate"),
    re.compile(r"until.*resolve"),
    re.compile(r"promising candidates"),
    _PR2_CHAIN_SEARCH_DESCRIBE,
)


def _assert_no_pr2_sentinels(label: str, text: str, *, is_resolve_tool: bool) -> None:
    for pat in _PR2_SENTINEL_PATTERNS:
        if is_resolve_tool and pat is _PR2_CHAIN_SEARCH_DESCRIBE:
            continue
        match = pat.search(text)
        assert match is None, f"{label}: forbidden pattern {pat.pattern!r} matched {match.group(0)!r}"


def _method_id_with_calls(ladybug_graph, direction: str) -> str:
    if direction == "in":
        rows = ladybug_graph._rows(  # noqa: SLF001
            "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) RETURN dst.id AS id LIMIT 1"
        )
    else:
        rows = ladybug_graph._rows(  # noqa: SLF001
            "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) RETURN src.id AS id LIMIT 1"
        )
    assert rows
    return str(rows[0]["id"])


def _method_id_declares_client_and_other_out_edge(ladybug_graph) -> str | None:
    """A method with DECLARES_CLIENT plus another out-label (Kuzu #119 strict-subset case)."""
    for pattern in (
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:CALLS]->() RETURN m.id AS id LIMIT 1",
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->(:Client)-[:HTTP_CALLS]->() RETURN m.id AS id LIMIT 1",
    ):
        rows = ladybug_graph._rows(pattern)  # noqa: SLF001
        if rows:
            return str(rows[0]["id"])
    return None


def _first_route_with_handler(ladybug_graph) -> str:
    for route in ladybug_graph.list_routes(limit=200):
        if ladybug_graph.find_route_handlers(route_id=route["id"]):
            return route["id"]
    raise AssertionError("expected a route with at least one handler")


def _first_route_with_callers(ladybug_graph) -> str:
    for route in ladybug_graph.list_routes(limit=200):
        if ladybug_graph.find_route_callers(route["id"]):
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


@needs_vectors
def test_search_basic_returns_hits_with_symbol_id(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert out.results[0].symbol_id is not None


@needs_vectors
def test_search_filter_microservice(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"microservice": "chat-assign"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert {h.microservice for h in out.results} == {"chat-assign"}


@needs_vectors
def test_search_path_contains_filter(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatAssign", path_contains="ChatAssign", graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) == 1


def test_find_symbol_by_role(ladybug_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert all(r.role == "CONTROLLER" for r in out.results if r.role is not None)


def test_find_symbol_empty_filter_returns_results(ladybug_graph) -> None:
    out = find_v2("symbol", {}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    # Regression guard: Symbol rows can include non-declaration kinds (e.g., package/file).
    assert all(isinstance(r.symbol_kind, str) and r.symbol_kind for r in out.results)


def test_find_symbol_by_symbol_kind_method(ladybug_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "method"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind == "method" for r in out.results)


def test_find_symbol_by_symbol_kind_interface(ladybug_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "interface"}, graph=ladybug_graph)
    assert out.success is True
    if not out.results:
        pytest.skip("fixture has no interface symbols")
    assert all(r.symbol_kind == "interface" for r in out.results)


def test_find_symbol_by_symbol_kinds_type_level(ladybug_graph) -> None:
    type_level_kinds = ["class", "interface", "enum", "record", "annotation"]
    out = find_v2("symbol", {"symbol_kinds": type_level_kinds}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind in set(type_level_kinds) for r in out.results)


def test_find_symbol_projection_includes_symbol_kind(ladybug_graph) -> None:
    out = find_v2("symbol", {"symbol_kind": "method"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert all(isinstance(r.symbol_kind, str) and r.symbol_kind for r in out.results)


def test_find_route_by_path_contains(ladybug_graph) -> None:
    out = find_v2("route", {"path_contains": "/api"}, graph=ladybug_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_find_kind_producer_returns_producer_nodes(ladybug_graph) -> None:
    out = find_v2("producer", filter={}, graph=ladybug_graph)
    if not out.results:
        pytest.skip("no Producer nodes in session fixture")
    assert out.success is True
    assert all(r.kind == "producer" for r in out.results)


def test_resolve_hint_kind_producer(ladybug_graph) -> None:
    rows = ladybug_graph.list_producers(limit=10)
    if not rows:
        pytest.skip("no Producer nodes in session fixture")
    topic = str(rows[0].get("topic") or "")
    if not topic:
        pytest.skip("producer row missing topic")
    out = resolve_v2(topic, hint_kind="producer", graph=ladybug_graph)
    assert out.success is True
    assert out.status in {"one", "many"}
    if out.status == "one":
        assert out.node is not None
        assert out.node.kind == "producer"


def test_find_client_by_client_kind(ladybug_graph) -> None:
    rows = ladybug_graph.list_clients(client_kind="feign_method", limit=500)
    if not rows:
        pytest.skip("fixture has no feign_method client rows")
    by_id = {str(r["id"]): r for r in rows}
    out = find_v2("client", {"client_kind": "feign_method"}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    for ref in out.results:
        row = by_id.get(ref.id)
        assert row is not None, f"unexpected client id {ref.id!r}"
        assert str(row.get("client_kind") or "") == "feign_method"


def test_find_client_by_target_service(ladybug_graph) -> None:
    rows = ladybug_graph.list_clients(limit=500)
    seed = next((r for r in rows if str(r.get("target_service") or "").strip()), None)
    if seed is None:
        pytest.skip("no client rows with target_service in fixture")
    target_service = str(seed["target_service"])
    out = find_v2("client", {"target_service": target_service}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert all(r.fqn.startswith(f"{target_service} ") for r in out.results)


def test_find_client_by_path_contains(ladybug_graph) -> None:
    all_clients = find_v2("client", {}, graph=ladybug_graph)
    assert all_clients.success is True
    sample = next((r for r in all_clients.results if "/" in r.fqn), None)
    if sample is None:
        pytest.skip("no client rows with path metadata in fixture")
    parts = sample.fqn.split(" ")
    path = parts[-1] if parts else ""
    if not path.startswith("/"):
        pytest.skip("sample client path is unavailable")
    needle = path[: min(len(path), 5)]
    out = find_v2("client", {"target_path_contains": needle}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    for ref in out.results:
        bits = ref.fqn.split(" ")
        assert bits
        assert needle in bits[-1]


def test_find_cross_kind_filter_fields_return_failure(ladybug_graph) -> None:
    out = find_v2("symbol", {"path_contains": "/api"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_contains" in out.message
    assert "kind='symbol'" in out.message


def test_find_unknown_filter_key_returns_failure(ladybug_graph) -> None:
    out = find_v2("symbol", {"typo_key": "x"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_find_symbol_only_field_with_kind_client_returns_failure(ladybug_graph) -> None:
    out = find_v2("client", {"fqn_contains": "com.example"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "fqn_contains" in out.message
    assert "kind='client'" in out.message


def test_find_client_only_field_with_kind_symbol_returns_failure(ladybug_graph) -> None:
    out = find_v2("symbol", {"client_kind": "feign_method"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "client_kind" in out.message
    assert "kind='symbol'" in out.message


def test_nodefilter_applicability_table_covers_all_fields() -> None:
    declared = set(NodeFilter.model_fields.keys())
    covered = set().union(*_NODEFILTER_APPLICABLE_FIELDS.values())
    assert declared == covered


def test_http_method_field_applies_to_route_kind(ladybug_graph) -> None:
    routes = ladybug_graph.list_routes(limit=2000)
    post_ids = {str(r["id"]) for r in routes if str(r.get("method") or "").upper() == "POST"}
    if not post_ids:
        pytest.skip("fixture has no POST routes")
    out = find_v2("route", {"http_method": "POST"}, graph=ladybug_graph, limit=500)
    assert out.success is True
    assert out.results
    assert {r.id for r in out.results} <= post_ids
    assert all(r.fqn == "POST" or r.fqn.startswith("POST ") for r in out.results)


def test_http_method_field_applies_to_client_kind(ladybug_graph) -> None:
    clients = ladybug_graph.list_clients(limit=2000)
    post_ids = {str(r["id"]) for r in clients if str(r.get("method") or "").upper() == "POST"}
    if not post_ids:
        pytest.skip("fixture has no POST clients")
    out = find_v2("client", {"http_method": "POST"}, graph=ladybug_graph, limit=500)
    assert out.success is True
    assert out.results
    assert {r.id for r in out.results} <= post_ids


def test_http_method_field_inapplicable_to_symbol(ladybug_graph) -> None:
    out = find_v2("symbol", {"http_method": "POST"}, graph=ladybug_graph)
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


def test_describe_symbol_returns_record(ladybug_graph) -> None:
    symbol = ladybug_graph.list_by_role("SERVICE", limit=1)[0]
    out = describe_v2(symbol.id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "symbol"


def test_describe_route_returns_record(ladybug_graph) -> None:
    route = ladybug_graph.list_routes(limit=1)[0]
    out = describe_v2(route["id"], graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "route"


def test_describe_client_returns_record(ladybug_graph) -> None:
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


def test_describe_unknown_id_returns_error(ladybug_graph) -> None:
    out = describe_v2("bogus:1", graph=ladybug_graph)
    assert out.success is False
    assert out.message


def test_describe_package_or_file_symbol_succeeds(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind IN ['package', 'file'] RETURN s.id AS id LIMIT 1"
    )
    if not rows:
        pytest.skip("fixture has no package/file symbol rows")
    out = describe_v2(str(rows[0]["id"]), graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "symbol"
    assert out.record.data.get("kind") in {"package", "file"}


def test_neighbors_in_calls(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "in")
    out = neighbors_v2(mid, direction="in", edge_types=["CALLS"], graph=ladybug_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_neighbors_out_calls(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], graph=ladybug_graph)
    assert out.success is True
    assert isinstance(out.results, list)


def test_neighbors_edge_types_strict_subset_respects_label_filter(ladybug_graph) -> None:
    """Regression (#119): Kuzu can drop `label(e) IN $list`; use OR of `label(e) = $p` instead."""
    mid = _method_id_declares_client_and_other_out_edge(ladybug_graph)
    if mid is None:
        pytest.skip("no method with DECLARES_CLIENT and CALLS or HTTP_CALLS out-edges")
    dc_rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[e:DECLARES_CLIENT]->() WHERE m.id = $id RETURN count(e) AS n",
        {"id": mid},
    )
    assert dc_rows
    want_dc = int(dc_rows[0]["n"])
    assert want_dc >= 1
    out = neighbors_v2(mid, direction="out", edge_types=["DECLARES_CLIENT"], graph=ladybug_graph)
    assert out.success is True
    assert all(e.edge_type == "DECLARES_CLIENT" for e in out.results)
    assert len(out.results) == want_dc


def test_neighbors_route_in_exposes_returns_handler(ladybug_graph) -> None:
    route_id = _first_route_with_handler(ladybug_graph)
    out = neighbors_v2(route_id, direction="in", edge_types=["EXPOSES"], graph=ladybug_graph)
    assert out.success is True
    assert out.results


def test_neighbors_route_in_http_calls_returns_callers(ladybug_graph) -> None:
    class FakeGraph:
        def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            # The generic flat-label path now queries one edge type at a time and
            # passes label(e) = $label (issue #356); model that only HTTP_CALLS
            # callers exist in this fixture so the ASYNC_CALLS label returns none.
            if (
                "MATCH (a)<-[e]-(b)" in query
                and "WHERE a.id" in query
                and "RETURN b.id AS other_id" in query
                and (params or {}).get("label") == "HTTP_CALLS"
            ):
                return [{"other_id": "client:caller", "edge_type": "HTTP_CALLS", "confidence": 0.8, "match": "cross_service"}]
            if "MATCH (n:Client)" in query:
                return [
                    {
                        "id": "client:caller",
                        "client_kind": "feign_method",
                        "target_service": "chat-core",
                        "method": "POST",
                        "path": "/chat/joinOperator",
                        "path_template": "/chat/joinOperator",
                        "path_regex": "",
                        "member_fqn": "com.example.Caller#call()",
                        "member_id": "sym:caller",
                        "microservice": "chat-core",
                        "module": "chat-app",
                        "filename": "Caller.java",
                        "start_line": 1,
                        "end_line": 2,
                        "resolved": True,
                        "source_layer": "builtin",
                    }
                ]
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
    assert out.results[0].other.id == "client:caller"
    assert out.results[0].other.kind == "client"


def test_neighbors_batch_ids_carries_origin_id(ladybug_graph) -> None:
    one = _method_id_with_calls(ladybug_graph, "out")
    two = _method_id_with_calls(ladybug_graph, "in")
    out = neighbors_v2([one, two], direction="out", edge_types=["CALLS"], graph=ladybug_graph)
    assert out.success is True
    assert {e.origin_id for e in out.results} <= {one, two}


def test_neighbors_missing_direction_rejected(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, edge_types=["CALLS"], graph=ladybug_graph)


def test_neighbors_missing_edge_types_rejected(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", graph=ladybug_graph)


def test_neighbors_empty_edge_types_rejected(ladybug_graph) -> None:
    # Exercises TypeAdapter(min_length=1), distinct from missing edge_types (validate_call / Field(...)).
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", edge_types=[], graph=ladybug_graph)


def test_neighbors_invalid_direction_rejected(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="upstream", edge_types=["CALLS"], graph=ladybug_graph)


def test_neighbors_invalid_edge_type_rejected(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="in", edge_types=["calls"], graph=ladybug_graph)


async def test_find_invalid_kind_rejected(mcp_server) -> None:
    with pytest.raises(ToolError, match="Input should be"):
        await mcp_server.call_tool("find", {"kind": "method", "filter": {}})


async def test_search_invalid_table_rejected(mcp_server) -> None:
    with pytest.raises(ToolError, match="Input should be"):
        await mcp_server.call_tool("search", {"query": "foo", "table": "code"})


@needs_vectors
def test_search_filter_accepts_json_string(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    want = {"microservice": "chat-assign"}
    out_dict = search_v2("ChatService", filter=want, graph=ladybug_graph)
    out_str = search_v2("ChatService", filter='{"microservice":"chat-assign"}', graph=ladybug_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


@needs_vectors
def test_search_unknown_filter_key_returns_failure(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"typo_key": "x"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_search_no_lance_index_returns_failure_envelope(monkeypatch, ladybug_graph, tmp_path) -> None:
    """Real search error path (issue #358): every other search test monkeypatches
    run_search, so the genuine `except Exception` envelope in search_v2 was never
    exercised. With no Lance vector index present, search returns a structured
    failure (success=False, non-empty message, no traceback) while the graph-only
    tools still succeed against the same graph — proving the failure is
    vector-specific, not a crash."""
    empty_index = tmp_path / "no-lance-index"
    empty_index.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(empty_index))
    out = search_v2("ChatService", graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None and out.message.strip()
    # Graph-only tools still work (the failure is vector-specific, not a crash).
    found = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph)
    assert found.success is True
    assert found.results


@needs_vectors
def test_search_pushes_nodefilter_into_run_search(monkeypatch, ladybug_graph) -> None:
    """search forwards NodeFilter structural fields into run_search so the filter
    applies BEFORE pagination, not as a post-filter on the already-paginated page
    (issue #353) — previously a filtered page could shrink to 0-2 results even
    when many matches existed deeper in the ranking."""
    captured: dict[str, Any] = {}

    def fake_run_search(query, **kwargs):
        captured.update(kwargs)
        return _fake_search_rows()

    monkeypatch.setattr("mcp_v2.run_search", fake_run_search)
    out = search_v2(
        "ChatService",
        filter={
            "role": "SERVICE",
            "module": "chat-assign",
            "microservice": "chat-assign",
            "capability": "c",
            "exclude_roles": ["CONTROLLER"],
        },
        graph=ladybug_graph,
    )
    assert out.success is True
    assert captured.get("role") == "SERVICE"
    assert captured.get("module") == "chat-assign"
    assert captured.get("microservice") == "chat-assign"
    assert captured.get("capability") == "c"
    assert captured.get("exclude_roles") == ["CONTROLLER"]


def test_unresolved_call_site_noderef_carries_callee_name() -> None:
    """The unresolved-call-site NodeRef must carry the callee identifier in `name`
    (issue #354) — NodeRef previously had no `name` field, so pydantic's default
    extra='ignore' silently dropped `name=callee`, leaving the structured ref with
    fqn='' and no human-readable callee (clients had to dig into attrs)."""
    from mcp_v2 import _unresolved_site_to_edge

    edge = _unresolved_site_to_edge(
        "origin:1",
        {
            "id": "ucs:1",
            "callee_simple": "Foo.bar",
            "call_site_line": 42,
            "call_site_byte": 7,
            "arg_count": 2,
            "reason": "phantom",
            "receiver_expr": "x",
        },
    )
    assert edge.other.kind == "unresolved_call_site"
    assert edge.other.name == "Foo.bar"
    # callee is also still carried in attrs for clients that read attrs
    assert edge.attrs["callee_simple"] == "Foo.bar"


def test_find_exposes_has_more_results(ladybug_graph) -> None:
    """find surfaces has_more_results on FindOutput so a paging client can tell
    whether another page exists without a probe call (issue #355). The value was
    computed and placed in the hint payload but absent from the output model."""
    out = find_v2("symbol", {"symbol_kind": "method"}, limit=1, offset=0, graph=ladybug_graph)
    assert out.success is True
    assert out.has_more_results is True  # bank-chat has more than one method

    # Past the end: no rows remain, so has_more is False.
    out_last = find_v2("symbol", {"symbol_kind": "method"}, limit=1, offset=1_000_000, graph=ladybug_graph)
    assert out_last.success is True
    assert out_last.has_more_results is False


def test_neighbors_flat_labels_select_columns_per_edge_type() -> None:
    """The generic flat-label neighbors query issues one Cypher per edge type and
    RETURNs only that type's columns (issue #356) — never a fixed superset that
    references columns absent on some matched type (the typed-union RETURN
    anti-pattern that errors on stricter binders like Kuzu)."""
    from mcp_v2 import _FLAT_EDGE_ATTR_COLUMNS

    issued: list[tuple[str, dict[str, Any]]] = []

    class FakeGraph:
        def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if "RETURN b.id AS other_id" in query:
                issued.append((query, params or {}))
            return []

    out = neighbors_v2(
        "sym:origin",
        direction="out",
        edge_types=["CALLS", "DECLARES", "INJECTS", "EXPOSES"],
        graph=FakeGraph(),  # type: ignore[arg-type]
    )
    assert out.success is True
    # One flat-label query per edge type, each tagged with its label param.
    labels = [p.get("label") for _, p in issued]
    assert set(labels) == {"CALLS", "DECLARES", "INJECTS", "EXPOSES"}
    for query, params in issued:
        label = params["label"]
        allowed = set(_FLAT_EDGE_ATTR_COLUMNS.get(label, ()))
        referenced = set(re.findall(r"e\.(\w+) AS ", query))
        assert referenced <= allowed, (
            f"label {label}: RETURN references {referenced}, only {allowed} are valid"
        )


@needs_vectors
def test_search_cross_kind_filter_returns_failure(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter={"path_contains": "/api"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_contains" in out.message
    assert "kind='symbol'" in out.message


@needs_vectors
def test_search_filter_empty_string_treated_as_none(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    baseline = search_v2("ChatService", graph=ladybug_graph)
    empty = search_v2("ChatService", filter="", graph=ladybug_graph)
    whitespace = search_v2("ChatService", filter="   ", graph=ladybug_graph)
    assert baseline.success is True
    assert empty.success is True
    assert whitespace.success is True
    assert baseline.results == empty.results == whitespace.results


@needs_vectors
def test_search_filter_json_null_treated_as_none(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    baseline = search_v2("ChatService", graph=ladybug_graph)
    out = search_v2("ChatService", filter="null", graph=ladybug_graph)
    assert baseline.success is True
    assert out.success is True
    assert baseline.results == out.results


def test_find_filter_json_null_treated_as_empty_filter(ladybug_graph) -> None:
    empty = find_v2("symbol", {}, graph=ladybug_graph)
    out = find_v2("symbol", "null", graph=ladybug_graph)
    assert empty.success is True
    assert out.success is True
    assert empty.results == out.results


def test_find_filter_accepts_json_string(ladybug_graph) -> None:
    out_dict = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph)
    out_str = find_v2("symbol", '{"role":"CONTROLLER"}', graph=ladybug_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_find_symbol_kind_filter_accepts_json_string(ladybug_graph) -> None:
    out_dict = find_v2("symbol", {"symbol_kind": "method"}, graph=ladybug_graph)
    out_str = find_v2("symbol", '{"symbol_kind":"method"}', graph=ladybug_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_neighbors_filter_accepts_json_string(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    flt = {"role": "SERVICE"}
    out_dict = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter=flt, graph=ladybug_graph)
    out_str = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter='{"role":"SERVICE"}', graph=ladybug_graph)
    assert out_dict.success is True
    assert out_str.success is True
    assert out_dict.results == out_str.results


def test_neighbors_calls_has_more_results_reflects_pagination_mode(ladybug_graph) -> None:
    """Single-origin CALLS has_more_results depends on whether SQL paginated.

    Regression for the #355 has_more_results field on NeighborsOutput. When a
    node_filter forces the in-memory (non-SQL-paginated) path, the full filtered
    CALLS set is returned, so has_more_results must be False (the client has
    everything and need not probe) -- not None ("unknown"). With no filter the
    single-origin SQL path paginates and the row/unfiltered counts carry the
    signal, so the field stays None.
    """
    mid = _method_id_with_calls(ladybug_graph, "out")
    # node_filter set -> paginate_in_sql False -> full set returned -> has_more False
    filtered = neighbors_v2(
        mid, direction="out", edge_types=["CALLS"], filter={"role": "SERVICE"}, graph=ladybug_graph
    )
    assert filtered.success is True
    assert filtered.has_more_results is False, (
        "non-SQL-paginated CALLS returned the full set; has_more_results must be "
        "False so a paging client does not issue a redundant probe (#355)"
    )
    # No filter, single origin -> SQL-paginated -> has-more signal is the row count.
    paginated = neighbors_v2(mid, direction="out", edge_types=["CALLS"], graph=ladybug_graph)
    assert paginated.success is True
    assert paginated.has_more_results is None


def test_neighbors_filter_unknown_key_returns_failure(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter={"typo_key": "x"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "Invalid filter" in out.message
    assert "typo_key" in out.message


def test_neighbors_filter_cross_kind_on_neighbor_returns_failure(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], filter={"path_contains": "/api"}, graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "path_contains" in out.message
    assert "kind='symbol'" in out.message


def test_neighbors_validate_call_still_raises(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="upstream", edge_types=["CALLS"], graph=ladybug_graph)


@needs_vectors
def test_filter_invalid_json_returns_failure(monkeypatch, ladybug_graph) -> None:
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows())
    out = search_v2("ChatService", filter="{not json", graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert "JSON" in out.message


def test_describe_by_fqn_returns_symbol(ladybug_graph) -> None:
    symbol = ladybug_graph.list_by_role("SERVICE", limit=1)[0]
    out = describe_v2(fqn=symbol.fqn, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.id == symbol.id
    assert out.record.kind == "symbol"
    assert out.message is None


def test_describe_by_fqn_unknown_returns_error(ladybug_graph) -> None:
    out = describe_v2(fqn="com.nonexistent.Foo", graph=ladybug_graph)
    assert out.success is False
    assert out.message == "No Symbol found for fqn='com.nonexistent.Foo'"


def test_describe_by_fqn_id_takes_precedence(ladybug_graph) -> None:
    svc = ladybug_graph.list_by_role("SERVICE", limit=1)[0]
    ctrl = ladybug_graph.list_by_role("CONTROLLER", limit=1)[0]
    out = describe_v2(id=svc.id, fqn=ctrl.fqn, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.id == svc.id
    assert str(out.record.data.get("role") or "") == "SERVICE"


def test_describe_by_fqn_duplicate_hint_points_to_resolve() -> None:
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
    assert "resolve" in out.message
    assert "hint_kind" in out.message


def test_server_tool_descriptions_no_pre_resolve_fallback() -> None:
    from server import _INSTRUCTIONS, create_mcp_server

    async def _run() -> None:
        mcp = create_mcp_server()
        tools = await mcp.list_tools()
        _assert_no_pr2_sentinels("_INSTRUCTIONS", _INSTRUCTIONS, is_resolve_tool=False)
        for tool in tools:
            desc = tool.description or ""
            _assert_no_pr2_sentinels(f"tool {tool.name!r}", desc, is_resolve_tool=(tool.name == "resolve"))

    asyncio.run(_run())


def test_describe_by_fqn_requires_id_or_fqn(ladybug_graph) -> None:
    out = describe_v2(graph=ladybug_graph)
    assert out.success is False
    assert out.message == "id or fqn required"


def test_multi_value_symbol_kinds_or_semantics(ladybug_graph) -> None:
    out = find_v2("symbol", {"symbol_kinds": ["class", "interface"]}, graph=ladybug_graph, limit=200)
    assert out.success is True
    assert out.results
    assert all(r.symbol_kind in {"class", "interface"} for r in out.results)


def test_cross_field_and_semantics(ladybug_graph) -> None:
    controllers = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph, limit=50)
    assert controllers.success is True
    assert controllers.results
    ms = next((r.microservice for r in controllers.results if r.microservice), None)
    if not ms:
        pytest.skip("no controller with microservice in fixture")
    out = find_v2(
        "symbol",
        {"microservice": ms, "role": "CONTROLLER"},
        graph=ladybug_graph,
        limit=200,
    )
    assert out.success is True
    assert out.results
    assert all((r.microservice or "") == ms for r in out.results)
    assert all((r.role or "") == "CONTROLLER" for r in out.results)


def test_exclude_roles_negation_predicate(ladybug_graph) -> None:
    out = find_v2("symbol", {"exclude_roles": ["CONTROLLER"]}, graph=ladybug_graph, limit=500)
    assert out.success is True
    assert out.results
    assert not any(r.role == "CONTROLLER" for r in out.results)


def test_empty_filter_returns_full_result_set(ladybug_graph) -> None:
    out = find_v2("client", {}, graph=ladybug_graph)
    assert out.success is True
    assert out.results


def test_fail_loud_counter_increments_on_applicability_error(ladybug_graph) -> None:
    before = filter_frame_counters().get("applicability", 0)
    out = find_v2("symbol", {"path_contains": "/api"}, graph=ladybug_graph)
    assert out.success is False
    assert filter_frame_counters().get("applicability", 0) == before + 1


def test_fail_loud_counter_categories_are_distinct(ladybug_graph) -> None:
    b_app = filter_frame_counters().get("applicability", 0)
    b_unknown = filter_frame_counters().get("unknown_key", 0)
    find_v2("symbol", {"path_contains": "/x"}, graph=ladybug_graph)  # applicability
    find_v2("symbol", {"typo_key": "x"}, graph=ladybug_graph)  # unknown_key
    assert filter_frame_counters().get("applicability", 0) == b_app + 1
    assert filter_frame_counters().get("unknown_key", 0) == b_unknown + 1


def test_fail_loud_counter_survives_multiple_calls(ladybug_graph) -> None:
    before = filter_frame_counters().get("applicability", 0)
    find_v2("symbol", {"http_method": "GET"}, graph=ladybug_graph)
    find_v2("symbol", {"http_method": "GET"}, graph=ladybug_graph)
    assert filter_frame_counters().get("applicability", 0) >= before + 2


# --- resolve (PR-RESOLVE-1) ---


def test_resolve_exact_id_symbol_returns_one(ladybug_graph) -> None:
    seed = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph, limit=1)
    assert seed.success and seed.results
    sym_id = seed.results[0].id
    out = resolve_v2(sym_id, hint_kind="symbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status == "one"
    assert out.node is not None
    assert out.node.id == sym_id


def test_resolve_exact_fqn_symbol_returns_one(ladybug_graph) -> None:
    controllers = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph, limit=50)
    assert controllers.success and controllers.results
    fqn = controllers.results[0].fqn
    out = resolve_v2(fqn, hint_kind="symbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status == "one"
    assert out.node is not None
    assert out.node.fqn == fqn


def test_resolve_fqn_collision_across_microservices_returns_many(
    ladybug_graph_fqn_collision_smoke,
) -> None:
    out = resolve_v2(
        "com.example.SharedDto#process()",
        hint_kind="symbol",
        graph=ladybug_graph_fqn_collision_smoke,
    )
    assert out.success is True
    assert out.status == "many"
    assert len(out.candidates) >= 2
    microservices = {c.node.microservice for c in out.candidates}
    assert len(microservices) >= 2
    assert any(c.reason == "exact_fqn" for c in out.candidates)


def test_resolve_short_name_ambiguity_returns_many(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind = 'method' RETURN s.name AS name"
    )
    counts = Counter(str(r["name"]) for r in rows if r.get("name"))
    dup_name = next((name for name, c in counts.items() if c >= 2), None)
    if dup_name is None:
        pytest.skip("no duplicated method short names in bank-chat fixture")
    out = resolve_v2(dup_name, hint_kind="symbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status == "many"
    assert any(c.reason == "short_name" for c in out.candidates)


def test_resolve_status_none_returns_nonempty_message(ladybug_graph) -> None:
    out = resolve_v2("com.nonexistent.ZzzMissing", hint_kind="symbol", graph=ladybug_graph)
    assert out.success is True
    assert out.status == "none"
    assert out.message
    assert "search" in out.message.lower()


def test_resolve_empty_identifier_success_false(ladybug_graph) -> None:
    out = resolve_v2("", graph=ladybug_graph)
    assert out.success is False
    assert out.status == "none"
    assert out.message and out.message.startswith("Invalid identifier:")


def test_resolve_whitespace_identifier_success_false(ladybug_graph) -> None:
    out = resolve_v2("   ", graph=ladybug_graph)
    assert out.success is False
    assert out.status == "none"
    assert out.message and out.message.startswith("Invalid identifier:")


def test_resolve_cross_kind_without_hint_returns_mixed_kinds() -> None:
    class CrossKindGraph:
        def _rows(self, query: str, params: dict | None = None) -> list:
            p = params or {}
            if "WHERE s.name = $name" in query and p.get("name") == "customers":
                return [
                    {
                        "id": "sym:customers",
                        "fqn": "com.fixture.Customers",
                        "microservice": "svc-a",
                        "module": "fixture",
                        "role": "",
                        "symbol_kind": "class",
                    }
                ]
            if "WHERE c.target_service = $target" in query and p.get("target") == "customers":
                return [
                    {
                        "id": "client:customers",
                        "client_kind": "feign_method",
                        "target_service": "customers",
                        "method": "GET",
                        "path": "/api/customers",
                        "path_template": "/api/customers",
                        "path_regex": "",
                        "member_fqn": "",
                        "member_id": "",
                        "microservice": "svc-a",
                        "module": "fixture",
                        "filename": "Client.java",
                        "start_line": 1,
                        "end_line": 1,
                        "resolved": True,
                        "source_layer": "builtin",
                    }
                ]
            return []

    out = resolve_v2("customers", graph=CrossKindGraph())  # type: ignore[arg-type]
    assert out.success is True
    assert out.status == "many"
    kinds = {c.node.kind for c in out.candidates}
    assert len(kinds) >= 2


def test_resolve_dedupes_overlapping_generator_paths() -> None:
    class DedupeGraph:
        sym_row = {
            "id": "sym:com.fixture.DedupeMe",
            "fqn": "com.fixture.DedupeMe",
            "microservice": "svc-a",
            "module": "fixture",
            "role": "",
            "symbol_kind": "class",
        }

        def _rows(self, query: str, params: dict | None = None) -> list:
            p = params or {}
            if "WHERE s.fqn = $fqn" in query and p.get("fqn") == "DedupeMe":
                return [self.sym_row]
            if "WHERE s.fqn = $ident OR s.fqn ENDS WITH $suffix" in query:
                return [self.sym_row]
            if "WHERE s.name = $name" in query and p.get("name") == "DedupeMe":
                return [self.sym_row]
            return []

    out = resolve_v2("DedupeMe", hint_kind="symbol", graph=DedupeGraph())  # type: ignore[arg-type]
    assert out.success is True
    assert out.status == "one"
    assert len(out.candidates) == 0
    assert out.node is not None
    assert out.node.id == "sym:com.fixture.DedupeMe"


def test_resolve_route_method_path_returns_one(ladybug_graph_route_extraction_smoke) -> None:
    out = resolve_v2(
        "service-a GET /api/users",
        hint_kind="route",
        graph=ladybug_graph_route_extraction_smoke,
    )
    assert out.success is True
    assert out.status == "one"
    assert out.node is not None
    assert out.node.kind == "route"
    assert out.node.microservice == "service-a"


def test_resolve_route_template_returns_one_or_many(ladybug_graph_route_extraction_smoke) -> None:
    out = resolve_v2(
        "/api/users",
        hint_kind="route",
        graph=ladybug_graph_route_extraction_smoke,
    )
    assert out.success is True
    assert out.status in {"one", "many"}
    reasons = {c.reason for c in out.candidates}
    if out.status == "many":
        assert "route_template" in reasons
    else:
        assert out.node is not None


def test_resolve_client_target_service(ladybug_graph, ladybug_db_path_http_caller_smoke) -> None:
    from ladybug_queries import LadybugGraph

    graph = ladybug_graph
    rows = graph.list_clients(limit=500)
    seed = next((r for r in rows if str(r.get("target_service") or "").strip()), None)
    if seed is None:
        graph = LadybugGraph(str(ladybug_db_path_http_caller_smoke))
        rows = graph.list_clients(limit=500)
        seed = next((r for r in rows if str(r.get("target_service") or "").strip()), None)
    if seed is None:
        pytest.skip("no client rows with target_service in fixture")
    target_service = str(seed["target_service"])
    out = resolve_v2(target_service, hint_kind="client", graph=graph)
    assert out.success is True
    assert out.status in {"one", "many"}
    if out.status == "many":
        assert any(c.reason == "client_target" for c in out.candidates)
    else:
        assert out.node is not None


def test_resolve_client_target_path_pair(ladybug_graph, ladybug_db_path_http_caller_smoke) -> None:
    from ladybug_queries import LadybugGraph

    def _seed_client(g: LadybugGraph) -> dict | None:
        rows = g.list_clients(limit=500)
        return next(
            (
                r
                for r in rows
                if str(r.get("target_service") or "").strip()
                and str(r.get("path") or "").startswith("/")
            ),
            None,
        )

    graph = ladybug_graph
    seed = _seed_client(graph)
    if seed is None:
        graph = LadybugGraph(str(ladybug_db_path_http_caller_smoke))
        seed = _seed_client(graph)
    if seed is None:
        pytest.skip("no client with target_service and path in fixture")
    target = str(seed["target_service"])
    path = str(seed["path"])
    prefix = path[: min(len(path), 8)]
    out = resolve_v2(f"{target} {prefix}", hint_kind="client", graph=graph)
    assert out.success is True
    assert out.status in {"one", "many"}
    reasons = {c.reason for c in out.candidates}
    if out.status == "many":
        assert "client_target_path" in reasons
    else:
        assert out.node is not None


def test_resolve_natural_language_sentence_returns_none(ladybug_graph) -> None:
    out = resolve_v2(
        "the client that handles smartcare assignments",
        graph=ladybug_graph,
    )
    assert out.success is True
    assert out.status == "none"


def test_resolve_wildcard_identifier_rejected(ladybug_graph) -> None:
    """resolve rejects wildcards (* and ?) consistently with search/find/neighbors
    (issue #359): previously it silently returned status='none', hiding a likely
    user mistake. Now it returns success=False with a message pointing to search."""
    out = resolve_v2("com.foo.*Service", hint_kind="symbol", graph=ladybug_graph)
    assert out.success is False
    assert out.status == "none"
    assert out.message and "search" in out.message.lower()


def test_resolve_every_reason_in_closed_set_appears() -> None:
    from resolve_service import (
        _resolve_client_candidates,
        _resolve_producer_candidates,
        _resolve_route_candidates,
        _resolve_symbol_candidates,
    )

    sym_row = {
        "id": "sym:reason",
        "fqn": "com.reason.Type",
        "microservice": "svc",
        "module": "mod",
        "role": "",
        "symbol_kind": "class",
    }
    route_row = {
        "id": "route:reason",
        "kind": "http_endpoint",
        "framework": "spring_mvc",
        "method": "GET",
        "path": "/reason",
        "path_template": "/reason",
        "path_regex": "",
        "topic": "",
        "broker": "",
        "feign_name": "",
        "feign_url": "",
        "microservice": "svc",
        "module": "mod",
        "filename": "R.java",
        "start_line": 1,
        "end_line": 1,
        "resolved": True,
    }
    client_row = {
        "id": "client:reason",
        "client_kind": "feign_method",
        "target_service": "reasonsvc",
        "method": "GET",
        "path": "/reason/path",
        "path_template": "/reason/path",
        "path_regex": "",
        "member_fqn": "",
        "member_id": "",
        "microservice": "svc",
        "module": "mod",
        "filename": "C.java",
        "start_line": 1,
        "end_line": 1,
        "resolved": True,
        "source_layer": "builtin",
    }
    producer_row = {
        "id": "p:reasonhash000000",
        "producer_kind": "kafka_send",
        "topic": "orders.created",
        "broker": "",
        "direction": "produce",
        "member_fqn": "com.reason.Producer#send()",
        "member_id": "sym:reasonproducer",
        "microservice": "svc",
        "module": "mod",
        "filename": "P.java",
        "start_line": 1,
        "end_line": 1,
        "resolved": True,
        "source_layer": "builtin",
    }

    class ReasonGraph:
        def _rows(self, query: str, params: dict | None = None) -> list:
            if "WHERE s.id = $id" in query:
                return [sym_row]
            if "WHERE s.fqn = $fqn" in query:
                return [sym_row]
            if "ENDS WITH $suffix" in query:
                return [sym_row]
            if "WHERE s.name = $name" in query:
                return [sym_row]
            if "WHERE r.id = $id" in query:
                return [route_row]
            if "r.method = $method" in query:
                return [route_row]
            if "r.path = $path OR r.path_template = $path" in query:
                return [route_row]
            if "WHERE c.id = $id" in query:
                return [client_row]
            if "STARTS WITH $path" in query:
                return [client_row]
            if "WHERE c.target_service = $target" in query:
                return [client_row]
            if "WHERE p.id = $id" in query:
                return [producer_row]
            if "WHERE p.topic = $topic" in query:
                return [producer_row]
            if "p.topic STARTS WITH $topic" in query:
                return [producer_row]
            return []

    g = ReasonGraph()  # type: ignore[arg-type]
    seen: set[str] = set()
    for _node, reason, _spec in _resolve_symbol_candidates(g, "sym:reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_symbol_candidates(g, "com.reason.Type"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_symbol_candidates(g, "Type"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_route_candidates(g, "route:reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_route_candidates(g, "GET /reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_route_candidates(g, "/reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_client_candidates(g, "client:reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_client_candidates(g, "reasonsvc"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_client_candidates(g, "reasonsvc /reason"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_producer_candidates(g, "p:reasonhash000000"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_producer_candidates(g, "orders.created"):
        seen.add(reason)
    for _node, reason, _spec in _resolve_producer_candidates(g, "orders"):
        seen.add(reason)

    assert seen == set(VALID_RESOLVE_REASONS)


def test_resolve_success_output_invariants(ladybug_graph, ladybug_graph_fqn_collision_smoke) -> None:
    one = resolve_v2(
        "com.nonexistent.ZzzMissing",
        hint_kind="symbol",
        graph=ladybug_graph,
    )
    assert one.success is True
    assert one.status == "none"
    assert one.node is None
    assert one.candidates == []
    assert one.message

    many = resolve_v2(
        "com.example.SharedDto#process()",
        hint_kind="symbol",
        graph=ladybug_graph_fqn_collision_smoke,
    )
    assert many.success is True
    assert many.status == "many"
    assert many.node is None
    assert len(many.candidates) >= 2

    sym = find_v2("symbol", {"role": "CONTROLLER"}, graph=ladybug_graph, limit=1)
    assert sym.success and sym.results
    single = resolve_v2(sym.results[0].id, hint_kind="symbol", graph=ladybug_graph)
    assert single.success is True
    assert single.status == "one"
    assert single.node is not None
    assert single.candidates == []


_PERF_BASELINES_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "perf_baselines.json"
)


def test_neighbors_calls_ordered_by_call_site(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], limit=500, graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) >= 2
    sites = [
        (int(e.attrs.get("call_site_line") or 0), int(e.attrs.get("call_site_byte") or 0))
        for e in out.results
    ]
    assert sites == sorted(sites)


def test_neighbors_calls_edge_filter_callee_declaring_role(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={"callee_declaring_role": "SERVICE"},
        limit=500,
        graph=ladybug_graph,
    )
    assert out.success is True
    assert out.results
    for edge in out.results:
        assert edge.attrs.get("callee_declaring_role") == "SERVICE"


def test_neighbors_calls_edge_filter_pushdown_in_cypher(ladybug_graph, monkeypatch) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    captured: list[str] = []
    orig_rows = ladybug_graph._rows

    def _capture_rows(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        captured.append(query)
        return orig_rows(query, params)

    monkeypatch.setattr(ladybug_graph, "_rows", _capture_rows)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={"callee_declaring_role": "SERVICE", "min_confidence": 0.5},
        graph=ladybug_graph,
    )
    assert out.success is True
    calls_queries = [q for q in captured if "ORDER BY e.call_site_line" in q]
    assert calls_queries
    q = calls_queries[0]
    assert "callee_declaring_role" in q
    assert "confidence" in q


def test_neighbors_calls_edge_filter_before_limit(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    unfiltered = neighbors_v2(
        mid, direction="out", edge_types=["CALLS"], limit=500, graph=ladybug_graph
    )
    assert unfiltered.success is True
    non_other_total = sum(
        1 for e in unfiltered.results if e.attrs.get("callee_declaring_role") != "OTHER"
    )
    assert non_other_total >= 6
    unfiltered_cap = neighbors_v2(
        mid, direction="out", edge_types=["CALLS"], limit=5, graph=ladybug_graph
    )
    assert unfiltered_cap.success is True
    assert len(unfiltered_cap.results) == 5
    other_in_cap = sum(
        1 for e in unfiltered_cap.results if e.attrs.get("callee_declaring_role") == "OTHER"
    )
    filtered = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={"exclude_callee_declaring_roles": ["OTHER"]},
        limit=5,
        graph=ladybug_graph,
    )
    assert filtered.success is True
    assert len(filtered.results) == 5
    assert all(e.attrs.get("callee_declaring_role") != "OTHER" for e in filtered.results)
    assert other_in_cap >= 1


def test_neighbors_calls_edge_filter_mixed_types_fail_loud(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS", "OVERRIDES"],
        edge_filter={"callee_declaring_role": "SERVICE"},
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message
    assert "edge_types=['CALLS']" in out.message
    assert "OVERRIDES" in out.message


def test_neighbors_calls_edge_filter_composed_types_fail_loud(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->(:Route) "
        "WHERE t.role = 'CONTROLLER' AND t.kind = 'class' "
        "RETURN t.id AS id LIMIT 1",
    )
    assert rows
    tid = str(rows[0]["id"])
    out = neighbors_v2(
        tid,
        direction="out",
        edge_types=["CALLS", "DECLARES.EXPOSES"],
        edge_filter={"callee_declaring_role": "SERVICE"},
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message
    assert "edge_types=['CALLS']" in out.message
    assert "DECLARES.EXPOSES" in out.message


def test_neighbors_calls_edge_filter_role_axes_xor(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={
            "callee_declaring_role": "SERVICE",
            "exclude_callee_declaring_roles": ["OTHER"],
        },
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message
    assert "mutually exclusive" in out.message.lower()


def test_neighbors_calls_edge_filter_strategy_xor(ladybug_graph) -> None:
    mid = _method_id_with_calls(ladybug_graph, "out")
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={"include_strategies": ["exact"], "exclude_strategies": ["phantom"]},
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message
    assert "mutually exclusive" in out.message.lower()


@pytest.mark.skipif(
    os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip() != "1",
    reason="perf gate; set JAVA_CODEBASE_RAG_RUN_HEAVY=1",
)
def test_neighbors_calls_perf_empty_filter_client_message_processor(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    baseline = json.loads(_PERF_BASELINES_PATH.read_text())[
        "neighbors_calls_empty_filter_client_message_processor"
    ]["median_sec"]
    times: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], limit=500, graph=ladybug_graph)
        times.append(time.perf_counter() - t0)
        assert out.success is True
        assert out.results
    median_sec = statistics.median(times)
    assert median_sec <= float(baseline) * 1.5


def test_neighbors_include_unresolved_interleaved_order(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        include_unresolved=True,
        limit=500,
        graph=ladybug_graph,
    )
    assert out.success is True
    assert out.results
    kinds = [e.attrs.get("row_kind") for e in out.results]
    assert "unresolved_call_site" in kinds
    assert "resolved" in kinds
    ucs_edges = [e for e in out.results if (e.attrs or {}).get("row_kind") == "unresolved_call_site"]
    assert ucs_edges
    for e in ucs_edges:
        assert e.other.kind == "unresolved_call_site"
        assert e.other.id.startswith("ucs:")
        assert not e.other.id.startswith("sym:")
    keys = [
        (
            int(e.attrs.get("call_site_line") or 0),
            int(e.attrs.get("call_site_byte") or 0),
            0 if e.attrs.get("row_kind") == "resolved" else 1,
        )
        for e in out.results
    ]
    assert keys == sorted(keys)


def test_neighbors_include_unresolved_edge_filter_mutex(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        include_unresolved=True,
        edge_filter={"min_confidence": 0.0},
        graph=ladybug_graph,
    )
    assert out.success is False
    assert "incompatible" in (out.message or "").lower()


def test_neighbors_dedup_calls_collapses_identical_dst(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WITH m, dst, collect(c.call_site_line) AS lines "
        "WHERE size(lines) > 1 "
        "RETURN m.id AS mid, dst.id AS did LIMIT 1",
    )
    if not rows:
        pytest.skip("no duplicate (caller,callee) CALLS pair in bank fixture")
    mid = str(rows[0]["mid"])
    flat = neighbors_v2(
        mid, direction="out", edge_types=["CALLS"], limit=500, graph=ladybug_graph,
    )
    deduped = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        dedup_calls=True,
        limit=500,
        graph=ladybug_graph,
    )
    assert flat.success and deduped.success
    assert len(deduped.results) < len(flat.results)
    multi = [e for e in deduped.results if int((e.attrs or {}).get("call_site_count") or 0) > 1]
    assert multi, "dedup_calls should emit call_site_count on collapsed rows"


def test_describe_ucs_id_not_describable(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (u:UnresolvedCallSite) RETURN u.id AS id LIMIT 1",
    )
    assert rows
    ucs_id = str(rows[0]["id"])
    assert ucs_id.startswith("ucs:")
    out = describe_v2(ucs_id, graph=ladybug_graph)
    assert out.success is False
    assert out.record is None
    assert "not describable" in (out.message or "").lower()
    assert "unresolved-calls" in (out.message or "").lower()


def test_neighbors_dedup_calls_include_unresolved(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        include_unresolved=True,
        dedup_calls=True,
        limit=500,
        graph=ladybug_graph,
    )
    assert out.success is True
    kinds = {str((e.attrs or {}).get("row_kind") or "resolved") for e in out.results}
    assert "resolved" in kinds
    assert "unresolved_call_site" in kinds
    keys = [_calls_transcript_sort_key_from_edge(e) for e in out.results]
    assert keys == sorted(keys)
    resolved = [e for e in out.results if (e.attrs or {}).get("row_kind") == "resolved"]
    assert any(int((e.attrs or {}).get("call_site_count") or 0) > 1 for e in resolved)


def _calls_transcript_sort_key_from_edge(edge: Edge) -> tuple[int, int, int]:
    attrs = edge.attrs or {}
    line = int(attrs.get("call_site_line") or 0)
    byte = int(attrs.get("call_site_byte") or 0)
    kind_rank = 0 if str(attrs.get("row_kind") or "resolved") == "resolved" else 1
    return (line, byte, kind_rank)


def test_describe_unresolved_call_sites_rollup_cap_footer_and_total(ladybug_graph) -> None:
    mid = client_message_processor_process_id(ladybug_graph)
    out = describe_v2(mid, graph=ladybug_graph)
    assert out.success and out.record
    data = out.record.data
    total = int(data.get("unresolved_call_sites_total") or 0)
    assert total >= 6, "ClientMessageProcessor#process should have multiple unresolved sites"
    inline = data.get("unresolved_call_sites") or []
    assert 1 <= len(inline) <= 5
    if total > len(inline):
        footer = str(data.get("unresolved_call_sites_footer") or "")
        assert "unresolved-calls list" in footer
        assert mid in footer


def test_search_hit_has_score_components_field() -> None:
    """SearchHit model includes score_components field (default None)."""
    from mcp_v2 import SearchHit
    hit = SearchHit(
        chunk_id="chunk:1",
        symbol_id="sym:1",
        score=0.9,
        snippet="test",
    )
    assert hasattr(hit, "score_components")
    assert hit.score_components is None


def test_search_explain_true_includes_score_components(monkeypatch, ladybug_graph) -> None:
    """search with explain=True returns hits with score_components."""
    def fake_rows_with_components():
        return [
            {
                "id": "chunk:1",
                "symbol_id": "sym:1",
                "primary_type_fqn": "com.example.ChatService",
                "_score": 0.85,
                "_score_components": {
                    "distance": 0.3,
                    "role_weight": 0.1,
                    "symbol_bonus": 0.05,
                },
                "text": "ChatService sample",
                "microservice": "chat-assign",
                "module": "chat-assign",
                "role": "SERVICE",
                "filename": "ChatAssignService.java",
                "start": {"line": 10},
                "end": {"line": 20},
            }
        ]

    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: fake_rows_with_components())
    out = search_v2("ChatService", explain=True, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert len(out.results) == 1
    hit = out.results[0]
    assert hit.score_components is not None
    assert "distance" in hit.score_components
    assert hit.score_components["distance"] == 0.3
    assert "role_weight" in hit.score_components
    assert hit.score_components["role_weight"] == 0.1


def test_search_explain_false_omits_score_components(monkeypatch, ladybug_graph) -> None:
    """search with explain=False (or omitted) returns hits with score_components=None."""
    def fake_rows_with_components():
        return [
            {
                "id": "chunk:1",
                "symbol_id": "sym:1",
                "primary_type_fqn": "com.example.ChatService",
                "_score": 0.85,
                "_score_components": {
                    "distance": 0.3,
                    "role_weight": 0.1,
                },
                "text": "ChatService sample",
                "microservice": "chat-assign",
                "module": "chat-assign",
                "role": "SERVICE",
                "filename": "ChatAssignService.java",
                "start": {"line": 10},
                "end": {"line": 20},
            }
        ]

    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: fake_rows_with_components())
    # Test with explain=False explicitly
    out = search_v2("ChatService", explain=False, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert len(out.results) == 1
    hit = out.results[0]
    assert hit.score_components is None

    # Test with explain omitted (default False)
    out2 = search_v2("ChatService", graph=ladybug_graph)
    assert out2.success is True
    assert out2.results
    assert len(out2.results) == 1
    hit2 = out2.results[0]
    assert hit2.score_components is None




def test_search_dedup_default_is_true(monkeypatch, ladybug_graph) -> None:
    """search tool defaults to dedup=True (per-symbol dedup enabled)."""
    captured_kwargs: dict = {}

    def mock_run_search(query, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        return [
            {
                "id": "chunk:1",
                "symbol_id": "sym:1",
                "primary_type_fqn": "com.example.TypeA",
                "_score": 0.95,
                "text": "TypeA sample",
                "microservice": "ms",
                "module": "mod",
                "role": "SERVICE",
                "filename": "a.java",
                "start": {"line": 10},
                "end": {"line": 20},
            }
        ]

    monkeypatch.setattr("mcp_v2.run_search", mock_run_search)

    out = search_v2("TypeA", graph=ladybug_graph)
    assert out.success is True
    # Default should be dedup=True
    assert captured_kwargs.get("dedup_by_fqn") is True, f"expected dedup_by_fqn=True by default, got {captured_kwargs.get('dedup_by_fqn')}"


def test_search_chunks_field_present_when_deduped(monkeypatch, ladybug_graph) -> None:
    """SearchHit.chunks field is present when rows have _chunks_collapsed."""
    def mock_run_search(query, **kwargs):
        # Return a row with _chunks_collapsed (simulating dedup output)
        return [
            {
                "id": "chunk:1",
                "symbol_id": "sym:1",
                "primary_type_fqn": "com.example.TypeA",
                "_score": 0.95,
                "_chunks_collapsed": 3,  # Dedup collapsed 3 chunks into this one
                "text": "TypeA sample",
                "microservice": "ms",
                "module": "mod",
                "role": "SERVICE",
                "filename": "a.java",
                "start": {"line": 10},
                "end": {"line": 20},
            }
        ]

    monkeypatch.setattr("mcp_v2.run_search", mock_run_search)

    out = search_v2("TypeA", graph=ladybug_graph)
    assert out.success is True
    assert len(out.results) == 1
    hit = out.results[0]
    # chunks field should be present and equal to _chunks_collapsed
    assert hasattr(hit, "chunks"), "SearchHit should have chunks field"
    assert hit.chunks == 3, f"expected chunks=3, got {hit.chunks}"


def test_search_chunks_flag_sets_dedup_false(monkeypatch, ladybug_graph) -> None:
    """chunks=True parameter sets dedup=False (opt-out of per-symbol dedup)."""
    captured_kwargs: dict = {}

    def mock_run_search(query, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        return [
            {
                "id": "chunk:1",
                "symbol_id": "sym:1",
                "primary_type_fqn": "com.example.TypeA",
                "_score": 0.95,
                "text": "TypeA sample",
                "microservice": "ms",
                "module": "mod",
                "role": "SERVICE",
                "filename": "a.java",
                "start": {"line": 10},
                "end": {"line": 20},
            }
        ]

    monkeypatch.setattr("mcp_v2.run_search", mock_run_search)

    out = search_v2("TypeA", dedup=False, graph=ladybug_graph)
    assert out.success is True
    assert captured_kwargs.get("dedup_by_fqn") is False, f"expected dedup_by_fqn=False with dedup=False, got {captured_kwargs.get('dedup_by_fqn')}"
