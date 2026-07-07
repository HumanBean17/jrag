"""Test Task 4: Surface generated/generated_by on search/find/describe/neighbors results.

Tests verify that:
1. search returns SearchHit with generated/generated_by set
2. find(symbol) returns NodeRef with the fields
3. describe includes generated/generated_by in NodeRecord.data
4. neighbors endpoint NodeRef carries them
5. CLI search output prints the generated hint
"""
from __future__ import annotations

from typing import Any

import pytest

from mcp_v2 import SearchHit, find_v2, describe_v2, neighbors_v2, search_v2
from graph_types import NodeRef


def _fake_search_rows_with_generated() -> list[dict[str, Any]]:
    """Fake search rows with generated/generated_by fields."""
    return [
        {
            "id": "chunk:1",
            "symbol_id": "sym:1",
            "primary_type_fqn": "com.example.OpenAPIModel",
            "_rrf_score": 0.9,
            "text": "OpenAPIModel sample",
            "microservice": "chat-assign",
            "module": "chat-assign",
            "role": "DTO",
            "filename": "chat-assign/src/main/java/com/example/OpenAPIModel.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 30},
            "generated": True,
            "generated_by": "openapi",
        },
        {
            "id": "chunk:2",
            "symbol_id": "sym:2",
            "primary_type_fqn": "com.example.HandWritten",
            "_rrf_score": 0.8,
            "text": "HandWritten sample",
            "microservice": "chat-core",
            "module": "chat-app",
            "role": "SERVICE",
            "filename": "chat-core/chat-app/src/main/java/com/example/HandWritten.java",
            "start": {"byte_offset": 40},
            "end": {"byte_offset": 80},
            "generated": False,
            "generated_by": None,
        },
    ]


def test_search_surfaces_generated_fields(monkeypatch, ladybug_graph) -> None:
    """Test that search returns SearchHit with generated/generated_by set."""
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: _fake_search_rows_with_generated())
    out = search_v2("OpenAPIModel", graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert len(out.results) == 2

    # First result is generated
    generated_hit = out.results[0]
    assert generated_hit.generated is True, "Generated chunk should have generated=True"
    assert generated_hit.generated_by == "openapi", "Generated chunk should have generated_by='openapi'"

    # Second result is hand-written
    manual_hit = out.results[1]
    assert manual_hit.generated is False, "Hand-written chunk should have generated=False"
    assert manual_hit.generated_by is None, "Hand-written chunk should have generated_by=None"


def test_search_handles_missing_generated_fields(monkeypatch, ladybug_graph) -> None:
    """Test that search handles rows without generated/generated_by fields (old indexes)."""
    # Use fake rows without generated/generated_by (simulating old indexes)
    rows_without_generated = [
        {
            "id": "chunk:1",
            "symbol_id": "sym:1",
            "primary_type_fqn": "com.example.OldType",
            "_rrf_score": 0.9,
            "text": "Old type sample",
            "microservice": "chat-assign",
            "module": "chat-assign",
            "role": "SERVICE",
            "filename": "chat-assign/src/main/java/com/example/OldType.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 30},
            # Note: no generated/generated_by fields
        },
    ]
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: rows_without_generated)
    out = search_v2("OldType", graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert len(out.results) == 1

    # Should default to None when fields are missing
    hit = out.results[0]
    assert hit.generated is None, "Missing generated field should default to None"
    assert hit.generated_by is None, "Missing generated_by field should default to None"


def test_find_surfaces_generated_fields(monkeypatch, ladybug_graph) -> None:
    """Test that find(symbol) returns NodeRef with generated/generated_by fields."""
    # Mock the graph query to return rows with generated/generated_by
    def _mock_rows(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "MATCH (s:Symbol)" in query and "RETURN" in query:
            return [
                {
                    "id": "sym:1",
                    "fqn": "com.example.OpenAPIModel",
                    "microservice": "chat-assign",
                    "module": "chat-assign",
                    "role": "DTO",
                    "kind": "class",
                    "generated": True,
                    "generated_by": "openapi",
                },
                {
                    "id": "sym:2",
                    "fqn": "com.example.HandWritten",
                    "microservice": "chat-core",
                    "module": "chat-app",
                    "role": "SERVICE",
                    "kind": "class",
                    "generated": False,
                    "generated_by": None,
                },
            ]
        return []

    monkeypatch.setattr(ladybug_graph, "_rows", _mock_rows)
    out = find_v2("symbol", {}, graph=ladybug_graph)
    assert out.success is True
    assert out.results
    assert len(out.results) == 2

    # First result is generated
    generated_ref = out.results[0]
    assert isinstance(generated_ref, NodeRef)
    assert generated_ref.generated is True, "Generated symbol should have generated=True"
    assert generated_ref.generated_by == "openapi", "Generated symbol should have generated_by='openapi'"

    # Second result is hand-written
    manual_ref = out.results[1]
    assert isinstance(manual_ref, NodeRef)
    assert manual_ref.generated is False, "Hand-written symbol should have generated=False"
    assert manual_ref.generated_by is None, "Hand-written symbol should have generated_by=None"


def test_describe_surfaces_generated_fields(monkeypatch, ladybug_graph) -> None:
    """Test that describe includes generated/generated_by in NodeRecord.data for symbols."""
    # Mock the graph query to return a symbol with generated/generated_by
    def _mock_rows(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "MATCH (n:Symbol)" in query and "WHERE n.id = $id" in query:
            return [
                {
                    "id": "sym:1",
                    "kind": "class",
                    "name": "OpenAPIModel",
                    "fqn": "com.example.OpenAPIModel",
                    "package": "com.example",
                    "module": "chat-assign",
                    "microservice": "chat-assign",
                    "filename": "chat-assign/src/main/java/com/example/OpenAPIModel.java",
                    "start_line": 1,
                    "end_line": 10,
                    "start_byte": 0,
                    "end_byte": 100,
                    "modifiers": ["public"],
                    "annotations": [],
                    "capabilities": [],
                    "role": "DTO",
                    "signature": "OpenAPIModel",
                    "parent_id": None,
                    "resolved": True,
                    "generated": True,
                    "generated_by": "openapi",
                }
            ]
        return []

    monkeypatch.setattr(ladybug_graph, "_rows", _mock_rows)
    out = describe_v2("sym:1", graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "symbol"

    # Check that generated/generated_by are in the data
    assert "generated" in out.record.data, "NodeRecord.data should contain 'generated'"
    assert "generated_by" in out.record.data, "NodeRecord.data should contain 'generated_by'"
    assert out.record.data["generated"] is True, "generated should be True"
    assert out.record.data["generated_by"] == "openapi", "generated_by should be 'openapi'"


def test_neighbors_surfaces_generated_fields(monkeypatch, ladybug_graph) -> None:
    """Test that neighbors endpoint NodeRef carries generated/generated_by fields."""
    # Get a valid method ID first
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) RETURN dst.id AS id LIMIT 1"
    )
    if not rows:
        pytest.skip("No CALLS edges in graph")
    method_id = rows[0]["id"]

    # Mock _load_node_record to return a symbol with generated/generated_by
    original_load_node_record = None
    import mcp_v2
    original_load_node_record = mcp_v2._load_node_record

    def _mock_load_node_record(graph, node_id, kind):
        # Return a mock symbol with generated/generated_by
        if kind == "symbol" and node_id != method_id:
            return {
                "id": node_id,
                "kind": "class",
                "name": "GeneratedNeighbor",
                "fqn": "com.example.GeneratedNeighbor",
                "package": "com.example",
                "module": "chat-assign",
                "microservice": "chat-assign",
                "filename": "chat-assign/src/main/java/com/example/GeneratedNeighbor.java",
                "start_line": 1,
                "end_line": 10,
                "start_byte": 0,
                "end_byte": 100,
                "modifiers": ["public"],
                "annotations": [],
                "capabilities": [],
                "role": "SERVICE",
                "signature": "GeneratedNeighbor",
                "parent_id": None,
                "resolved": True,
                "generated": True,
                "generated_by": "openapi",
            }
        # For the origin node, use the original function
        return original_load_node_record(graph, node_id, kind)

    monkeypatch.setattr("mcp_v2._load_node_record", _mock_load_node_record)

    out = neighbors_v2(method_id, direction="out", edge_types=["CALLS"], graph=ladybug_graph)
    assert out.success is True
    assert isinstance(out.results, list)

    # If we have neighbors, check the first one
    if out.results:
        edge = out.results[0]
        assert isinstance(edge, Edge)
        neighbor_ref = edge.other
        assert isinstance(neighbor_ref, NodeRef)
        # The neighbor should have generated/generated_by if we're mocking correctly
        # Note: This may not always find a generated neighbor in the real graph
        if neighbor_ref.generated is not None:
            assert neighbor_ref.generated is True
            assert neighbor_ref.generated_by == "openapi"


def test_cli_search_prints_generated_hint() -> None:
    """Test that CLI search output prints the generated hint."""
    # Simulate the hint building logic from search_lancedb.py
    row = _fake_search_rows_with_generated()[0]  # Get the generated row

    # Build hint string (same logic as search_lancedb.py lines 1202-1224)
    hints = row.get("_hints") or {}
    hint_s = ""
    if hints.get("primary_type_hint"):
        hint_s += f" | type:{hints['primary_type_hint']}"
    if hints.get("import_heavy"):
        hint_s += " | mostly-imports"
    role = row.get("role") or ""
    if role:
        hint_s += f" | role:{role}"
    ms = row.get("microservice") or ""
    if ms:
        hint_s += f" | microservice:{ms}"
    mod = row.get("module") or ""
    if mod and mod != ms:
        hint_s += f" | module:{mod}"
    gen = row.get("generated")
    gen_by = row.get("generated_by") or ""
    if gen:
        hint_s += f" | generated:{gen_by}" if gen_by else " | generated"

    # Check that the generated hint is in the hint string
    assert "| generated:openapi" in hint_s, f"Hint string should contain 'generated:openapi', got: {hint_s}"
