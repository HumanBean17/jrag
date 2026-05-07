"""Tool-surface assertions for the post-v1 MCP API."""
from __future__ import annotations

import json
from typing import Any


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


async def test_registered_tool_surface_is_v2_plus_operational(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    names = {tool.name for tool in tools}
    expected = {
        "search",
        "find",
        "describe",
        "neighbors",
        "graph_meta",
        "analyze_pr",
        "diagnose_ignore",
        "list_code_index_tables",
        "refresh_code_index",
    }
    removed_v1 = {
        "codebase_search",
        "find_implementors",
        "find_subclasses",
        "find_injectors",
        "find_callers",
        "find_callees",
        "list_routes",
        "list_clients",
        "find_route_handlers",
        "get_route_by_path",
        "find_route_callers",
        "trace_request_flow",
        "list_by_role",
        "list_by_annotation",
        "list_by_capability",
        "graph_neighbors",
        "impact_analysis",
        "trace_flow",
    }
    assert names == expected
    assert len(names) == 9
    assert names.isdisjoint(removed_v1)


async def test_all_tools_have_non_empty_description(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    missing = [
        tool.name
        for tool in tools
        if not isinstance(tool.description, str) or not tool.description.strip()
    ]
    assert missing == [], f"tools missing description: {missing}"


async def test_list_code_index_tables(mcp_server) -> None:
    out = _structured(await mcp_server.call_tool("list_code_index_tables", {}))
    assert "tables" in out and set(out["tables"]) >= {"java", "sql", "yaml"}
    assert out["graph"]["enabled"] is True


async def test_graph_meta(mcp_server) -> None:
    out = _structured(await mcp_server.call_tool("graph_meta", {}))
    assert out["success"] is True
    assert out["enabled"] is True
    assert out["ontology_version"] >= 1
    assert out["counts"]["types"] > 0
    assert out["module_counts"]["chat-assign"] > 0
    assert out["microservice_counts"]["chat-assign"] > 0
    assert out["microservice_counts"]["chat-core"] > 0
    assert out["counts"].get("calls", 0) > 0
    assert out["counts"].get("declares", 0) > 0
    assert out.get("routes_total", 0) >= 1
    assert isinstance(out.get("routes_by_framework"), dict)


async def test_diagnose_ignore_smoke(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "diagnose_ignore",
            {"path": "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"},
        )
    )
    assert out["success"] is True
    assert isinstance(out.get("ignored"), bool)


async def test_analyze_pr_smoke(mcp_server) -> None:
    diff = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -48,5 +48,5 @@
     @Transactional
     public void assign(AssignmentRequest request) {
-        if (request.getConversationId() == null || request.getConversationId().isBlank()) {
+        if (request.getConversationId() == null || request.getConversationId().isBlank() ) {
             throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "conversationId required");
         }
 """
    out = _structured(await mcp_server.call_tool("analyze_pr", {"diff_unified": diff}))
    assert out["success"] is True
    assert "risk_score" in out and "risk_band" in out
    assert isinstance(out.get("changed_symbols"), list)


async def test_refresh_code_index_disabled_by_default(mcp_server, monkeypatch) -> None:
    monkeypatch.delenv("LANCEDB_MCP_ALLOW_REFRESH", raising=False)
    out = _structured(await mcp_server.call_tool("refresh_code_index", {"confirm": True}))
    assert out["success"] is False
    assert "LANCEDB_MCP_ALLOW_REFRESH" in (out["message"] or "")


async def test_refresh_code_index_requires_confirm(mcp_server, monkeypatch) -> None:
    monkeypatch.setenv("LANCEDB_MCP_ALLOW_REFRESH", "1")
    out = _structured(await mcp_server.call_tool("refresh_code_index", {"confirm": False}))
    assert out["success"] is False
    assert "confirm" in (out["message"] or "").lower()


def test_cocoindex_subprocess_env_sets_project_root(monkeypatch, tmp_path) -> None:
    import server

    monkeypatch.setenv("LANCEDB_MCP_PROJECT_ROOT", "/should/be/overwritten")
    monkeypatch.setenv("PRESERVE_ME_FOR_SUBPROCESS", "ok")
    proj = tmp_path / "external-java-repo"
    proj.mkdir()
    resolved = proj.resolve()
    env = server._cocoindex_subprocess_env(resolved)
    assert env["LANCEDB_MCP_PROJECT_ROOT"] == str(resolved)
    assert env["PRESERVE_ME_FOR_SUBPROCESS"] == "ok"
