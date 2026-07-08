"""Tests for Task 3: Tag graph Symbol nodes with generated/generated_by.

Tests verify that:
1. Generated types are tagged with generated=True and generated_by set
2. Hand-written types have generated=False
3. Incremental rebuild preserves generated/generated_by on unchanged types
"""
from __future__ import annotations

from pathlib import Path

import ladybug
import pytest

from _builders import build_ladybug_into


def _connect(db_path: Path) -> ladybug.Connection:
    db = ladybug.Database(str(db_path), read_only=True)
    return ladybug.Connection(db)


def _scalar(conn: ladybug.Connection, query: str):
    r = conn.execute(query)
    if not r.has_next():
        return None
    return r.get_next()[0]


def test_generated_type_has_generated_fields(
    tmp_path: Path,
) -> None:
    """Build a graph from a fixture with a generated type → assert generated == True, generated_by set."""
    # Create a simple fixture with a generated type (OpenAPI @Generated with value)
    root = tmp_path / "proj"
    java_dir = root / "src/main/java/com/example"
    java_dir.mkdir(parents=True)

    # Generated type: OpenAPI @Generated class
    generated_java = java_dir / "GeneratedUser.java"
    generated_java.write_text(
        "package com.example;\n"
        "\n"
        "@Generated(value = \"org.openapitools.codegen.DefaultCodegen\", date = \"2025-01-15T10:30:00Z\")\n"
        "public class GeneratedUser {\n"
        "    private String name;\n"
        "    private int age;\n"
        "}\n"
    )

    # Hand-written type (no generation annotation)
    hand_written_java = java_dir / "ManualUser.java"
    hand_written_java.write_text(
        "package com.example;\n"
        "\n"
        "public class ManualUser {\n"
        "    private String name;\n"
        "    private int age;\n"
        "}\n"
    )

    # Build the graph
    db_path = tmp_path / "test.lbug"
    build_ladybug_into(root, db_path)

    # Query the generated type
    conn = _connect(db_path)
    generated_result = conn.execute(
        "MATCH (n:Symbol {fqn: 'com.example.GeneratedUser'}) "
        "RETURN n.generated, n.generated_by"
    )
    assert generated_result.has_next(), "GeneratedUser type should exist in graph"
    gen_generated, gen_generated_by = generated_result.get_next()
    assert gen_generated is True, f"GeneratedUser should have generated=True, got {gen_generated}"
    assert gen_generated_by == "openapi", f"GeneratedUser should have generated_by='openapi', got {gen_generated_by}"

    # Query the hand-written type
    manual_result = conn.execute(
        "MATCH (n:Symbol {fqn: 'com.example.ManualUser'}) "
        "RETURN n.generated, n.generated_by"
    )
    assert manual_result.has_next(), "ManualUser type should exist in graph"
    manual_generated, manual_generated_by = manual_result.get_next()
    assert manual_generated is False, f"ManualUser should have generated=False, got {manual_generated}"
    assert manual_generated_by is None, f"ManualUser should have generated_by=None, got {manual_generated_by}"


def test_incremental_rebuild_preserves_generated_fields(
    tmp_path: Path,
) -> None:
    """Incremental rebuild: a preserved (unchanged) generated type retains its generated/generated_by."""
    from build_ast_graph import FileHashTracker, GraphTables, incremental_rebuild, pass1_parse, write_ladybug
    from graph_enrich import load_generated_detection
    from path_filtering import LayeredIgnore

    load_generated_detection.cache_clear()

    # Create fixture with both generated and hand-written types
    source_root = tmp_path / "src"
    source_root.mkdir()
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    ladybug_path = index_dir / "code_graph.lbug"

    java_dir = source_root / "com/example"
    java_dir.mkdir(parents=True)

    # Generated type (OpenAPI style)
    generated_java = java_dir / "GeneratedUser.java"
    generated_java.write_text(
        "package com.example;\n"
        "\n"
        "@Generated(value = \"org.openapitools.codegen.DefaultCodegen\", date = \"2025-01-15T10:30:00Z\")\n"
        "public class GeneratedUser {\n"
        "    private String name;\n"
        "    private int age;\n"
        "}\n"
    )

    # Hand-written type that we'll modify later
    hand_written_java = java_dir / "ManualUser.java"
    hand_written_java.write_text(
        "package com.example;\n"
        "\n"
        "public class ManualUser {\n"
        "    private String name;\n"
        "}\n"
    )

    # Initial build
    tables = GraphTables()
    asts = pass1_parse(source_root, tables, verbose=False)
    from build_ast_graph import pass2_edges
    pass2_edges(tables, asts, verbose=False)
    write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

    # Initialize hash tracker
    tracker = FileHashTracker(index_dir)
    ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
    tracker.detect_changes(source_root, ignore)
    for rel_path in ["com/example/GeneratedUser.java", "com/example/ManualUser.java"]:
        tracker.update({rel_path}, source_root)
    tracker.save()

    # Verify initial state
    conn = _connect(ladybug_path)
    initial_gen_result = conn.execute(
        "MATCH (n:Symbol {fqn: 'com.example.GeneratedUser'}) "
        "RETURN n.generated, n.generated_by, n.id"
    )
    assert initial_gen_result.has_next()
    initial_gen_generated, initial_gen_by, gen_node_id = initial_gen_result.get_next()
    assert initial_gen_generated is True
    assert initial_gen_by == "openapi"

    # Now modify ONLY the hand-written file (not the generated one)
    hand_written_java.write_text(
        "package com.example;\n"
        "\n"
        "public class ManualUser {\n"
        "    private String name;\n"
        "    private int age;  // Added field\n"
        "}\n"
    )

    # Incremental rebuild (should preserve GeneratedUser as unchanged)
    result = incremental_rebuild(source_root, ladybug_path, verbose=False)
    assert result.mode == "incremental"

    # Verify that the preserved generated type still has its original values
    conn = _connect(ladybug_path)
    final_gen_result = conn.execute(
        "MATCH (n:Symbol {fqn: 'com.example.GeneratedUser'}) "
        "RETURN n.generated, n.generated_by, n.id"
    )
    assert final_gen_result.has_next()
    final_gen_generated, final_gen_by, final_gen_node_id = final_gen_result.get_next()
    assert final_gen_generated is True, f"Preserved GeneratedUser should still have generated=True, got {final_gen_generated}"
    assert final_gen_by == "openapi", f"Preserved GeneratedUser should still have generated_by='openapi', got {final_gen_by}"
    assert final_gen_node_id == gen_node_id, "Node ID should be the same after incremental rebuild"


def test_schema_has_generated_columns(
    tmp_path: Path,
) -> None:
    """Verify the Symbol table has generated and generated_by columns."""
    root = tmp_path / "proj"
    java_dir = root / "src/main/java/com/example"
    java_dir.mkdir(parents=True)
    (java_dir / "Test.java").write_text("package com.example; public class Test {}")

    db_path = tmp_path / "test.lbug"
    build_ladybug_into(root, db_path)

    # Simply try to query the columns - if they don't exist, we'll get an error
    conn = _connect(db_path)
    try:
        result = conn.execute("MATCH (n:Symbol) RETURN n.generated, n.generated_by LIMIT 1;")
        # If we get here without exception, columns exist (query succeeded)
        # The try/except is the real check - no assertion needed
    except RuntimeError as e:
        if "Cannot find property" in str(e):
            pytest.fail(f"Schema missing generated columns: {e}")
        raise
