"""Tests for the MCP tool surface in `server.py`.

We invoke each tool through `FastMCP.call_tool()` so we exercise the full
JSON-schema validation + `asyncio.to_thread` plumbing the way a real MCP
client (Claude, Cursor, etc.) would.

⚠️  These tests assert on the *contract* of each tool (success flag, the
shape of the structured output, sensible error messages on bad input). They
do **not** pin specific result counts. See `tests/README.md`.
"""
from __future__ import annotations

import json
from typing import Any


def _structured(result: Any) -> dict[str, Any]:
    """Pull the structured payload out of a `FastMCP.call_tool()` return.

    FastMCP can return either `Sequence[ContentBlock]` (legacy) or a plain
    `dict` (newer). We accept both and always hand back a dict so test
    assertions stay simple.
    """
    if isinstance(result, dict):
        return result
    # tuple form: (content_blocks, structured_dict)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    # Sequence of ContentBlocks: try to find a TextContent with a JSON
    # payload (FastMCP typically serialises pydantic outputs that way).
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


# ---------------- tool metadata contract ----------------


async def test_all_tools_have_non_empty_description(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    missing = [
        tool.name for tool in tools
        if not isinstance(tool.description, str) or not tool.description.strip()
    ]
    assert missing == [], f"Tools missing description: {missing}"


# ---------------- list_code_index_tables ----------------


async def test_list_code_index_tables(mcp_server) -> None:
    out = _structured(await mcp_server.call_tool("list_code_index_tables", {}))
    assert "tables" in out and set(out["tables"]) >= {"java", "sql", "yaml"}
    assert out["graph"]["enabled"] is True
    assert out["graph"]["counts"]["types"] > 0


# ---------------- graph_meta ----------------


async def test_graph_meta(mcp_server) -> None:
    out = _structured(await mcp_server.call_tool("graph_meta", {}))
    assert out["success"] is True
    assert out["enabled"] is True
    assert out["ontology_version"] >= 1
    # Builder writes counts inside the GraphMeta row, then service_counts is
    # derived from the live Kuzu DB. Both should be populated.
    assert out["counts"]["types"] > 0
    assert out["module_counts"]["chat-assign"] > 0
    assert out["microservice_counts"]["chat-assign"] > 0
    assert out["microservice_counts"]["chat-core"] > 0
    assert out["counts"].get("calls", 0) > 0
    assert out["counts"].get("declares", 0) > 0
    assert out.get("routes_total", 0) >= 1
    assert isinstance(out.get("routes_by_framework"), dict)


# ---------------- find_implementors ----------------


async def test_find_implementors(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool("find_implementors", {"name": "EventProcessor"})
    )
    assert out["success"] is True
    names = {r["name"] for r in out["results"]}
    assert len(names) >= 5, names
    assert "ClientMessageProcessor" in names


# ---------------- find_subclasses ----------------


async def test_find_subclasses_phantom_target(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool("find_subclasses", {"name": "JpaRepository"})
    )
    assert out["success"] is True
    assert len(out["results"]) >= 2, out


# ---------------- find_injectors ----------------


async def test_find_injectors(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "find_injectors", {"name": "AssignChatRepository"}
        )
    )
    assert out["success"] is True
    consumers = {edge["consumer"]["name"] for edge in out["results"]}
    assert "ChatManagementService" in consumers


# ---------------- route graph tools (B2a) ----------------


async def test_route_graph_mcp_tools_smoke(mcp_server) -> None:
    listed = _structured(await mcp_server.call_tool("list_routes", {"limit": 20}))
    assert listed["success"] is True
    assert listed.get("routes")
    rid = ""
    handlers = {"success": True, "results": []}
    for route in listed["routes"]:
        rid = route["id"]
        handlers = _structured(await mcp_server.call_tool("find_route_handlers", {"route_id": rid}))
        if handlers.get("results"):
            break
    assert handlers["success"] is True
    assert handlers.get("results") is not None
    assert len(handlers["results"]) >= 1
    r0 = next((r for r in listed["routes"] if r.get("path_template")), listed["routes"][0])
    byp = _structured(
        await mcp_server.call_tool(
            "get_route_by_path",
            {
                "microservice": r0["microservice"],
                "path_template": r0["path_template"],
                "method": r0.get("method") or "",
            },
        )
    )
    assert byp["success"] is True
    assert byp.get("route") is not None


# ---------------- find_callers / find_callees ----------------


async def test_find_callers_tool(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "find_callers",
            {
                "fqn_or_signature": (
                    "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
                ),
                "limit": 30,
            },
        )
    )
    assert out["success"] is True
    assert any(
        "ChatManagementController" in e["caller"]["fqn"] for e in out["results"]
    ), out["results"]


async def test_find_callees_tool(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "find_callees",
            {
                "fqn_or_signature": (
                    "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
                ),
                "limit": 40,
            },
        )
    )
    assert out["success"] is True
    assert out["results"], out


# ---------------- list_by_role ----------------


async def test_list_by_role_controller(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool("list_by_role", {"role": "CONTROLLER"})
    )
    assert out["success"] is True
    assert all(r["role"] == "CONTROLLER" for r in out["results"])
    assert len(out["results"]) >= 2


async def test_list_by_role_module_filter(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "list_by_role", {"role": "SERVICE", "module": "chat-assign"}
        )
    )
    assert out["success"] is True
    modules = {r["module"] for r in out["results"]}
    # Module filter must narrow to exactly the requested module (or be
    # empty, never bleed in other modules).
    assert modules <= {"chat-assign"}, modules


async def test_list_by_role_microservice_filter(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "list_by_role", {"role": "SERVICE", "microservice": "chat-core"}
        )
    )
    assert out["success"] is True
    microservices = {r["microservice"] for r in out["results"]}
    # Microservice scoping must isolate chat-core's services from chat-assign's.
    assert microservices <= {"chat-core"}, microservices


# ---------------- list_by_annotation ----------------


async def test_list_by_annotation(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "list_by_annotation", {"annotation": "Transactional"}
        )
    )
    assert out["success"] is True
    assert len(out["results"]) >= 1


# ---------------- graph_neighbors ----------------


async def test_graph_neighbors_validation(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "graph_neighbors",
            {"name": "ChatManagementService", "direction": "garbage"},
        )
    )
    assert out["success"] is False
    assert "direction" in (out["message"] or "")


async def test_graph_neighbors(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "graph_neighbors",
            {
                "name": "ChatManagementService",
                "edge_types": ["INJECTS"],
                "direction": "out",
                "depth": 1,
            },
        )
    )
    assert out["success"] is True
    assert len(out["results"]) >= 3


# ---------------- impact_analysis ----------------


async def test_impact_analysis(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "impact_analysis", {"name": "AssignChatRepository", "depth": 2}
        )
    )
    assert out["success"] is True
    names = {r["name"] for r in out["results"]}
    assert "ChatManagementService" in names


# ---------------- analyze_pr (B4) ----------------


async def test_diagnose_ignore_smoke(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "diagnose_ignore",
            {"path": "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"},
        )
    )
    assert out["success"] is True
    assert "ignored" in out
    assert isinstance(out["ignored"], bool)
    assert "layer" in out
    assert "explanation" in out


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
    assert isinstance(out["changed_symbols"], list)
    assert any(
        "assign(AssignmentRequest)" in str(s.get("fqn", "")) for s in out["changed_symbols"]
    ), out


# ---------------- codebase_search (validation paths only) ----------------


async def test_codebase_search_rejects_bad_table(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "codebase_search", {"query": "anything", "table": "wat"}
        )
    )
    assert out["success"] is False
    assert "table" in (out["message"] or "").lower()


async def test_codebase_search_rejects_hybrid_with_all(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "codebase_search",
            {"query": "anything", "table": "all", "hybrid": True},
        )
    )
    assert out["success"] is False
    assert "hybrid" in (out["message"] or "").lower()


async def test_codebase_search_missing_lance_uri_message(
    mcp_server, monkeypatch, tmp_path
) -> None:
    """When LANCEDB_URI points at a non-existent path the tool must error
    cleanly rather than crash deep inside lance / pyarrow."""
    bogus = tmp_path / "definitely-not-here"
    monkeypatch.setenv("LANCEDB_URI", str(bogus))
    out = _structured(
        await mcp_server.call_tool("codebase_search", {"query": "anything"})
    )
    assert out["success"] is False
    assert "LanceDB" in (out["message"] or "") or "lancedb" in (out["message"] or "")


# ---------------- trace_flow (also Lance-dependent for seeds) ----------------


async def test_trace_flow_lance_required(mcp_server, monkeypatch, tmp_path) -> None:
    """`trace_flow` seeds via vector search, so it should also surface a
    helpful message when LanceDB is missing. We only check it does *not*
    crash and returns the structured failure shape."""
    bogus = tmp_path / "nope"
    monkeypatch.setenv("LANCEDB_URI", str(bogus))
    out = _structured(
        await mcp_server.call_tool("trace_flow", {"query": "what happens on chat assign"})
    )
    assert out["success"] is False


async def test_find_route_callers_by_route_id(mcp_server) -> None:
    routes = _structured(await mcp_server.call_tool("list_routes", {"limit": 100}))
    target = next((r for r in routes["routes"] if r.get("path_template") == "/chat/joinOperator"), None)
    assert target is not None
    out = _structured(await mcp_server.call_tool("find_route_callers", {"route_id": target["id"]}))
    assert out["success"] is True
    assert isinstance(out["results"], list)


async def test_find_route_callers_by_path_method(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "find_route_callers",
            {"microservice": "chat-core", "path_template": "/chat/joinOperator", "method": "POST"},
        )
    )
    assert out["success"] is True
    assert isinstance(out["results"], list)


async def test_find_route_callers_no_match_returns_empty(mcp_server) -> None:
    out = _structured(await mcp_server.call_tool("find_route_callers", {"route_id": "r:missing"}))
    assert out["success"] is True
    assert out["results"] == []


async def test_trace_request_flow_two_hop(mcp_server) -> None:
    routes = _structured(await mcp_server.call_tool("list_routes", {"limit": 100}))
    target = next((r for r in routes["routes"] if r.get("path_template") == "/chat/joinOperator"), None)
    assert target is not None
    out = _structured(
        await mcp_server.call_tool("trace_request_flow", {"entry_route_id": target["id"], "max_hops": 3})
    )
    assert out["success"] is True
    assert "inbound" in out["flow"]
    assert "outbound" in out["flow"]


async def test_trace_request_flow_max_hops_respected(mcp_server) -> None:
    routes = _structured(await mcp_server.call_tool("list_routes", {"limit": 100}))
    target = next((r for r in routes["routes"] if r.get("path_template") == "/chat/joinOperator"), None)
    assert target is not None
    out = _structured(
        await mcp_server.call_tool("trace_request_flow", {"entry_route_id": target["id"], "max_hops": 1})
    )
    assert out["success"] is True
    assert int(out["flow"]["max_hops"]) == 1


async def test_impact_analysis_includes_cross_service_callers(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "impact_analysis", {"name": "com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)", "depth": 2}
        )
    )
    assert out["success"] is True
    assert "cross_service_callers" in out


async def test_analyze_pr_surfaces_cross_service_count(mcp_server) -> None:
    diff = """diff --git a/chat-core/chat-app/src/main/java/com/bank/chat/core/api/ChatController.java b/chat-core/chat-app/src/main/java/com/bank/chat/core/api/ChatController.java
--- a/chat-core/chat-app/src/main/java/com/bank/chat/core/api/ChatController.java
+++ b/chat-core/chat-app/src/main/java/com/bank/chat/core/api/ChatController.java
@@ -31,6 +31,6 @@
     @PostMapping("/chat/joinOperator")
     public JoinOperatorResponse joinOperator(@RequestBody JoinOperatorRequest request) {
-        return chatApplicationService.joinOperator(request);
+        return chatApplicationService.joinOperator(request);
     }
 }
"""
    out = _structured(await mcp_server.call_tool("analyze_pr", {"diff_unified": diff}))
    assert out["success"] is True
    for sym in out.get("changed_symbols", []):
        assert "cross_service_callers_count" in sym


async def test_trace_flow_follows_http_calls(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "trace_flow",
            {"query": "join operator flow", "seed_limit": 5, "stage_limit": 10, "follow_calls": True},
        )
    )
    assert "success" in out


# ---------------- refresh_code_index gating ----------------


async def test_refresh_code_index_disabled_by_default(mcp_server, monkeypatch) -> None:
    monkeypatch.delenv("LANCEDB_MCP_ALLOW_REFRESH", raising=False)
    out = _structured(
        await mcp_server.call_tool("refresh_code_index", {"confirm": True})
    )
    assert out["success"] is False
    assert "LANCEDB_MCP_ALLOW_REFRESH" in (out["message"] or "")


async def test_refresh_code_index_requires_confirm(mcp_server, monkeypatch) -> None:
    monkeypatch.setenv("LANCEDB_MCP_ALLOW_REFRESH", "1")
    out = _structured(
        await mcp_server.call_tool("refresh_code_index", {"confirm": False})
    )
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
