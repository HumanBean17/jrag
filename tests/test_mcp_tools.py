"""Tool-surface assertions for the v2 MCP API."""
from __future__ import annotations


async def test_registered_tool_surface_is_v2_navigation_only(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    names = {tool.name for tool in tools}
    expected = {
        "search",
        "find",
        "describe",
        "neighbors",
    }
    assert names == expected
    assert len(names) == 4


async def test_all_tools_have_non_empty_description(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    missing = [
        tool.name
        for tool in tools
        if not isinstance(tool.description, str) or not tool.description.strip()
    ]
    assert missing == [], f"tools missing description: {missing}"


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
