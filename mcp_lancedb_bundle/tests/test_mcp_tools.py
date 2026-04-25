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

import pytest


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
    assert out["service_counts"]["chat-assign"] > 0


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


# ---------------- list_by_role ----------------


async def test_list_by_role_controller(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool("list_by_role", {"role": "CONTROLLER"})
    )
    assert out["success"] is True
    assert all(r["role"] == "CONTROLLER" for r in out["results"])
    assert len(out["results"]) >= 2


async def test_list_by_role_service_filter(mcp_server) -> None:
    out = _structured(
        await mcp_server.call_tool(
            "list_by_role", {"role": "SERVICE", "service": "chat-assign"}
        )
    )
    assert out["success"] is True
    services = {r["service"] for r in out["results"]}
    # Service filter must narrow to exactly the requested service (or
    # be empty, never bleed in other modules).
    assert services <= {"chat-assign"}, services


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
