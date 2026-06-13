"""Tool-surface assertions for the v2 MCP API."""
from __future__ import annotations


def _enum_sets(node: object) -> list[set[str]]:
    found: list[set[str]] = []
    if isinstance(node, dict):
        enum_vals = node.get("enum")
        if isinstance(enum_vals, list) and all(isinstance(v, str) for v in enum_vals):
            found.append(set(enum_vals))
        for value in node.values():
            found.extend(_enum_sets(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_enum_sets(value))
    return found


async def test_registered_tool_surface_is_v2_navigation_only(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    names = {tool.name for tool in tools}
    expected = {
        "search",
        "find",
        "describe",
        "neighbors",
        "resolve",
    }
    assert names == expected
    assert len(names) == 5


async def test_all_tools_have_non_empty_description(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    missing = [
        tool.name
        for tool in tools
        if not isinstance(tool.description, str) or not tool.description.strip()
    ]
    assert missing == [], f"tools missing description: {missing}"


async def test_tool_input_schema_top_level_properties_have_nonempty_descriptions(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    for tool in tools:
        schema = tool.inputSchema or {}
        props = schema.get("properties") or {}
        for param_name, spec in props.items():
            desc = spec.get("description")
            assert isinstance(desc, str) and desc.strip(), (
                f"{tool.name}.{param_name}: expected non-empty description in MCP inputSchema"
            )


async def test_tool_input_schema_includes_expected_enums(mcp_server) -> None:
    tools = await mcp_server.list_tools()
    by_name = {tool.name: tool for tool in tools}

    search_schema = by_name["search"].inputSchema or {}
    find_schema = by_name["find"].inputSchema or {}
    neighbors_schema = by_name["neighbors"].inputSchema or {}

    search_table = ((search_schema.get("properties") or {}).get("table") or {})
    find_kind = ((find_schema.get("properties") or {}).get("kind") or {})
    neighbors_direction = ((neighbors_schema.get("properties") or {}).get("direction") or {})
    neighbors_edge_types = ((neighbors_schema.get("properties") or {}).get("edge_types") or {})

    assert {"java", "sql", "yaml", "all"} in _enum_sets(search_table)
    assert {"symbol", "route", "client", "producer"} in _enum_sets(find_kind)
    assert {"in", "out"} in _enum_sets(neighbors_direction)
    assert {
        "EXTENDS",
        "IMPLEMENTS",
        "INJECTS",
        "OVERRIDES",
        "DECLARES",
        "DECLARES_CLIENT",
        "DECLARES_PRODUCER",
        "CALLS",
        "EXPOSES",
        "HTTP_CALLS",
        "ASYNC_CALLS",
    } in _enum_sets(neighbors_edge_types.get("items") or {})


def test_cocoindex_subprocess_env_sets_project_root(monkeypatch, tmp_path) -> None:
    import server

    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", "/should/be/overwritten")
    monkeypatch.setenv("PRESERVE_ME_FOR_SUBPROCESS", "ok")
    proj = tmp_path / "external-java-repo"
    proj.mkdir()
    resolved = proj.resolve()
    env = server._cocoindex_subprocess_env(resolved)
    assert env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] == str(resolved)
    assert env["PRESERVE_ME_FOR_SUBPROCESS"] == "ok"


def test_cocoindex_subprocess_env_applies_inflight_default(monkeypatch, tmp_path) -> None:
    """The MCP-triggered cocoindex subprocess must carry the real inflight throttle.

    Regression guard for #306: the throttle env var must be CocoIndex's real
    ``COCOINDEX_MAX_INFLIGHT_COMPONENTS`` (default 1024 -> capped to 256), not the
    non-existent ``COCOINDEX_SOURCE_MAX_INFLIGHT_ROWS`` from the broken #293 fix.
    """
    import server

    proj = tmp_path / "external-java-repo"
    proj.mkdir()
    env = server._cocoindex_subprocess_env(proj.resolve())

    assert env["COCOINDEX_MAX_INFLIGHT_COMPONENTS"] == "256"
    assert "COCOINDEX_SOURCE_MAX_INFLIGHT_ROWS" not in env
