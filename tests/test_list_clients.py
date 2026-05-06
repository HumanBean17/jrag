from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest


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
    raise AssertionError(f"could not extract structured payload from {result!r}")


@pytest.fixture(scope="module")
def list_clients_mcp_server(tmp_path_factory):
    from build_ast_graph import (
        GraphTables,
        pass1_parse,
        pass2_edges,
        pass3_calls,
        pass4_routes,
        pass5_imperative_edges,
        pass6_match_edges,
        write_kuzu,
    )
    from kuzu_queries import KuzuGraph
    from server import create_mcp_server

    root = tmp_path_factory.mktemp("list_clients_graph")
    stubs = Path(__file__).resolve().parent / "fixtures" / "brownfield_client_stubs"
    shutil.copytree(stubs, root, dirs_exist_ok=True)
    (root / "p" / "ClientSource.java").parent.mkdir(parents=True, exist_ok=True)
    (root / "p" / "ClientSource.java").write_text(
        (
            "package p; import com.example.rag.*; class ClientSource { "
            "@CodebaseClient(clientKind=CodebaseClientKind.rest_template, targetService=\"chat-core\", path=\"/chat/joinOperator\", method=\"GET\") "
            "void callA() {} "
            "@CodebaseClient(clientKind=CodebaseClientKind.web_client, targetService=\"chat-assign\", path=\"/assign/run\", method=\"POST\") "
            "void callB() {} }"
        ),
        encoding="utf-8",
    )
    (root / "p" / "FeignApi.java").write_text(
        (
            "package p; "
            "import org.springframework.cloud.openfeign.FeignClient; "
            "import org.springframework.web.bind.annotation.GetMapping; "
            "@FeignClient(name=\"remote-users\", path=\"/users\") interface FeignApi { "
            "@GetMapping(\"/{id}\") Object getById(String id); }"
        ),
        encoding="utf-8",
    )

    db_path = root / "graph.kuzu"
    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=root, verbose=False)
    pass5_imperative_edges(tables, asts, source_root=root, verbose=False)
    pass6_match_edges(tables, verbose=False)
    write_kuzu(db_path, tables, source_root=root, verbose=False)

    saved_env = {k: os.environ.get(k) for k in (
        "KUZU_DB_PATH",
        "LANCEDB_MCP_GRAPH_ENABLED",
        "LANCEDB_URI",
        "LANCEDB_MCP_PROJECT_ROOT",
    )}
    os.environ["KUZU_DB_PATH"] = str(db_path)
    os.environ["LANCEDB_MCP_GRAPH_ENABLED"] = "1"
    os.environ["LANCEDB_URI"] = str(root / "lance")
    os.environ["LANCEDB_MCP_PROJECT_ROOT"] = str(root)
    Path(os.environ["LANCEDB_URI"]).mkdir(parents=True, exist_ok=True)
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    server = create_mcp_server()
    try:
        yield server
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        KuzuGraph._instance = None
        KuzuGraph._instance_path = None


async def test_list_clients_returns_rows(list_clients_mcp_server) -> None:
    out = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 50}))
    assert out["success"] is True
    assert out["clients"]
    assert all("source_layer" in c for c in out["clients"])


async def test_list_clients_filter_microservice(list_clients_mcp_server) -> None:
    out = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"microservice": "p", "limit": 100},
        )
    )
    assert out["success"] is True
    assert all(c["microservice"] == "p" for c in out["clients"])


async def test_list_clients_filter_client_kind(list_clients_mcp_server) -> None:
    out = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"client_kind": "feign_method", "limit": 100},
        )
    )
    assert out["success"] is True
    assert all(c["client_kind"] == "feign_method" for c in out["clients"])


async def test_list_clients_filter_target_service(list_clients_mcp_server) -> None:
    base = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 200}))
    assert base["success"] is True
    target = next((c.get("target_service") for c in base["clients"] if c.get("target_service")), "")
    assert target
    out = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"target_service": target, "limit": 200},
        )
    )
    assert out["success"] is True
    assert out["clients"]
    assert all(c.get("target_service") == target for c in out["clients"])


async def test_list_clients_filter_path_prefix(list_clients_mcp_server) -> None:
    out = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"path_prefix": "/chat", "limit": 200},
        )
    )
    assert out["success"] is True
    assert all((c.get("path") or "").startswith("/chat") for c in out["clients"])


async def test_list_clients_filter_method(list_clients_mcp_server) -> None:
    out_lower = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"method": "get", "limit": 200},
        )
    )
    out_upper = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {"method": "GET", "limit": 200},
        )
    )
    assert out_lower["success"] is True
    assert out_upper["success"] is True
    assert out_lower["clients"] == out_upper["clients"]
    assert all(c.get("method") == "GET" for c in out_lower["clients"])


async def test_list_clients_empty_result_is_success_with_empty_clients(list_clients_mcp_server) -> None:
    out = _structured(
        await list_clients_mcp_server.call_tool(
            "list_clients",
            {
                "microservice": "missing-service",
                "target_service": "missing-target",
                "path_prefix": "/definitely/missing",
            },
        )
    )
    assert out["success"] is True
    assert out["clients"] == []


async def test_list_clients_limit_bounds_and_clamping_behavior(list_clients_mcp_server) -> None:
    out_zero = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 0}))
    out_one = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 1}))
    out_500 = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 500}))
    out_501 = _structured(await list_clients_mcp_server.call_tool("list_clients", {"limit": 501}))

    assert out_zero["success"] is True
    assert out_one["success"] is True
    assert out_500["success"] is True
    assert out_501["success"] is True
    assert len(out_zero["clients"]) == len(out_one["clients"])
    assert len(out_one["clients"]) <= 1
    assert len(out_501["clients"]) == len(out_500["clients"])
    assert len(out_500["clients"]) <= 500
