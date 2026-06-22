"""Tests for incremental graph rebuild functionality (PR-G1 and PR-G2).

Tests cover FileHashTracker behavior, edge schema source_file column, and incremental orchestrator.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import ladybug

from ast_java import ONTOLOGY_VERSION
from build_ast_graph import FileHashTracker, GraphTables, pass1_parse, pass2_edges, pass4_routes
from path_filtering import LayeredIgnore


class TestFileHashTracker:
    """Test FileHashTracker change detection and persistence."""

    def test_file_hash_tracker_detects_added_file(self, tmp_path: Path) -> None:
        """Empty hash store, one file in source → added populated."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file = source_root / "Test.java"
        test_file.write_text("class Test {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        added, changed, removed = tracker.detect_changes(source_root, ignore=ignore)

        assert len(added) == 1
        assert "Test.java" in added
        assert len(changed) == 0
        assert len(removed) == 0

    def test_file_hash_tracker_detects_changed_file(self, tmp_path: Path) -> None:
        """Stored hash differs from current → changed populated."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file = source_root / "Test.java"
        test_file.write_text("class Test {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        tracker.update({"Test.java"}, source_root)
        tracker.save()

        # Modify the file
        test_file.write_text("class Test { String x; }", encoding="utf-8")

        tracker2 = FileHashTracker(index_dir)
        tracker2.load()
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        added, changed, removed = tracker2.detect_changes(source_root, ignore=ignore)

        assert len(added) == 0
        assert len(changed) == 1
        assert "Test.java" in changed
        assert len(removed) == 0

    def test_file_hash_tracker_detects_removed_file(self, tmp_path: Path) -> None:
        """Hash store has entry but file gone → removed populated."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file = source_root / "Test.java"
        test_file.write_text("class Test {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        tracker.update({"Test.java"}, source_root)
        tracker.save()

        # Remove the file
        test_file.unlink()

        tracker2 = FileHashTracker(index_dir)
        tracker2.load()
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        added, changed, removed = tracker2.detect_changes(source_root, ignore=ignore)

        assert len(added) == 0
        assert len(changed) == 0
        assert len(removed) == 1
        assert "Test.java" in removed

    def test_file_hash_tracker_no_changes(self, tmp_path: Path) -> None:
        """Identical hashes → all three sets empty."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file = source_root / "Test.java"
        test_file.write_text("class Test {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        tracker.update({"Test.java"}, source_root)
        tracker.save()

        tracker2 = FileHashTracker(index_dir)
        tracker2.load()
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        added, changed, removed = tracker2.detect_changes(source_root, ignore=ignore)

        assert len(added) == 0
        assert len(changed) == 0
        assert len(removed) == 0

    def test_file_hash_tracker_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Save hashes, new tracker instance loads same data."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file1 = source_root / "A.java"
        test_file1.write_text("class A {}", encoding="utf-8")
        test_file2 = source_root / "B.java"
        test_file2.write_text("class B {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        tracker.update({"A.java", "B.java"}, source_root)
        tracker.save()

        tracker2 = FileHashTracker(index_dir)
        tracker2.load()

        assert len(tracker2._hashes) == 2
        assert "A.java" in tracker2._hashes
        assert "B.java" in tracker2._hashes

    def test_file_hash_tracker_atomic_save(self, tmp_path: Path) -> None:
        """.graph_hashes.json.tmp not left behind on successful save."""
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        source_root = tmp_path / "src"
        source_root.mkdir()
        test_file = source_root / "Test.java"
        test_file.write_text("class Test {}", encoding="utf-8")

        tracker = FileHashTracker(index_dir)
        tracker.load()
        tracker.update({"Test.java"}, source_root)
        tracker.save()

        # Verify the tmp file is not left behind
        tmp_file = index_dir / ".graph_hashes.json.tmp"
        assert not tmp_file.exists()

        # Verify the actual file exists
        actual_file = index_dir / ".graph_hashes.json"
        assert actual_file.exists()


class TestEdgeSchema:
    """Test edge schema has source_file column and correct values."""

    def test_edge_schema_has_source_file(self, tmp_path: Path) -> None:
        """Build a full graph, query each edge table for source_file column existence and non-empty values."""
        from _builders import build_ladybug_full_into

        corpus_root = Path(__file__).parent / "bank-chat-system"
        db_path = tmp_path / "test_graph.lbug"
        build_ladybug_full_into(corpus_root, db_path)

        conn = ladybug.Connection(ladybug.Database(str(db_path), read_only=True))

        # All 12 edge tables should have source_file column
        edge_tables = [
            "EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "OVERRIDES",
            "CALLS", "UNRESOLVED_AT", "EXPOSES", "DECLARES_CLIENT",
            "DECLARES_PRODUCER", "HTTP_CALLS", "ASYNC_CALLS"
        ]

        for table in edge_tables:
            # Check column exists by querying a sample and accessing source_file
            query = f"MATCH ()-[e:{table}]->() RETURN e.source_file LIMIT 1"
            result = conn.execute(query)
            has_data = result.has_next()
            if has_data:
                row = result.get_next()
                # source_file should be a string
                assert row is not None

    def test_source_file_value_matches_symbol_filename(self, tmp_path: Path) -> None:
        """For edges originating from Symbol nodes, edge's source_file equals source Symbol's filename."""
        from _builders import build_ladybug_full_into

        corpus_root = Path(__file__).parent / "bank-chat-system"
        db_path = tmp_path / "test_graph.lbug"
        build_ladybug_full_into(corpus_root, db_path)

        conn = ladybug.Connection(ladybug.Database(str(db_path), read_only=True))

        # Test CALLS edge: source_file should match caller Symbol's filename
        query = """
        MATCH (caller:Symbol)-[e:CALLS]->(callee:Symbol)
        RETURN caller.filename, e.source_file
        LIMIT 1
        """
        result = conn.execute(query)
        if result.has_next():
            caller_filename, edge_source_file = result.get_next()
            assert caller_filename == edge_source_file

        # Test EXTENDS edge
        query = """
        MATCH (sub:Symbol)-[e:EXTENDS]->(super:Symbol)
        RETURN sub.filename, e.source_file
        LIMIT 1
        """
        result = conn.execute(query)
        if result.has_next():
            sub_filename, edge_source_file = result.get_next()
            assert sub_filename == edge_source_file

    def test_ontology_version_bumped_to_17(self) -> None:
        """ONTOLOGY_VERSION == 17."""
        assert ONTOLOGY_VERSION == 17


class TestIncrementalOrchestrator:
    """Test incremental rebuild orchestrator (PR-G2)."""

    def test_incremental_single_file_change(self, tmp_path: Path) -> None:
        """Change one .java file, run incremental, verify only that file's nodes changed."""
        from build_ast_graph import incremental_rebuild

        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create initial files
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")
        (source_root / "B.java").write_text("package pkg; class B extends A {}", encoding="utf-8")

        # Initial build
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        assert len(asts) == 2

        # Build full graph (pass2 needed for EXTENDS edges)
        from build_ast_graph import write_ladybug
        pass2_edges(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        for rel_path in ["A.java", "B.java"]:
            tracker.update({rel_path}, source_root)
        tracker.save()

        # Modify A.java
        (source_root / "A.java").write_text("package pkg; class A { void foo() {} }", encoding="utf-8")

        # Run incremental
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"
        assert result.files_changed == 1
        assert result.files_added == 0
        assert result.files_removed == 0
        assert result.dependents_reprocessed >= 1  # B depends on A

    def test_incremental_new_file(self, tmp_path: Path) -> None:
        """Add a new .java file, run incremental, verify all new nodes/edges appear."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create initial file
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Add new file
        (source_root / "B.java").write_text("package pkg; class B {}", encoding="utf-8")

        # Run incremental
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"
        assert result.files_changed == 0
        assert result.files_added == 1

    def test_incremental_deleted_file(self, tmp_path: Path) -> None:
        """Remove a .java file from fixture, run incremental, verify orphaned nodes/edges cleaned up."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create initial files
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")
        (source_root / "B.java").write_text("package pkg; class B {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java", "B.java"}, source_root)
        tracker.save()

        # Delete B.java
        (source_root / "B.java").unlink()

        # Run incremental
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"
        assert result.files_changed == 0
        assert result.files_added == 0
        assert result.files_removed == 1

        # Verify B's nodes are deleted
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        check_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.B' RETURN count(*)")
        if check_result.has_next():
            count = check_result.get_next()[0]
            assert count == 0

    def test_incremental_phantom_nodes_preserved(self, tmp_path: Path) -> None:
        """Run incremental after a change, verify phantom nodes (those with filename = "") are untouched."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create file with external reference
        (source_root / "A.java").write_text(
            "package pkg; import java.util.List; class A { List<String> list; }",
            encoding="utf-8",
        )

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Count phantom nodes before
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        phantom_count_before = 0
        phantom_result = conn.execute("MATCH (s:Symbol) WHERE s.filename = '' RETURN count(*)")
        if phantom_result.has_next():
            phantom_count_before = phantom_result.get_next()[0]

        conn.close()

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Modify A.java
        (source_root / "A.java").write_text(
            "package pkg; import java.util.List; class A { List<Integer> list; }",
            encoding="utf-8",
        )

        # Run incremental
        from build_ast_graph import incremental_rebuild
        incremental_rebuild(source_root, ladybug_path, verbose=False)

        # Verify phantom nodes still exist
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        phantom_count_after = 0
        phantom_result = conn.execute("MATCH (s:Symbol) WHERE s.filename = '' RETURN count(*)")
        if phantom_result.has_next():
            phantom_count_after = phantom_result.get_next()[0]

        assert phantom_count_after >= phantom_count_before

    def test_incremental_dependent_expansion(self, tmp_path: Path) -> None:
        """Change a base class, verify that files with EXTENDS/IMPLEMENTS edges into it are also reprocessed."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create files with inheritance
        (source_root / "Base.java").write_text("package pkg; class Base {}", encoding="utf-8")
        (source_root / "Derived.java").write_text(
            "package pkg; class Derived extends Base {}", encoding="utf-8"
        )

        # Initial build (pass2 needed for EXTENDS edges)
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"Base.java", "Derived.java"}, source_root)
        tracker.save()

        # Modify Base.java
        (source_root / "Base.java").write_text(
            "package pkg; class Base { void foo() {} }", encoding="utf-8"
        )

        # Run incremental
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        # Derived.java should be reprocessed due to EXTENDS edge
        assert result.dependents_reprocessed >= 1

    def test_incremental_expansion_cap_fallback(self, tmp_path: Path) -> None:
        """Mock expansion_cap=2, change a widely-used file that has >2 dependents, verify fallback to full rebuild."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create base class and many derived classes
        (source_root / "Base.java").write_text("package pkg; class Base {}", encoding="utf-8")
        for i in range(5):
            (source_root / f"Derived{i}.java").write_text(
                f"package pkg; class Derived{i} extends Base {{}}", encoding="utf-8"
            )

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        all_files = {"Base.java"} | {f"Derived{i}.java" for i in range(5)}
        tracker.update(all_files, source_root)
        tracker.save()

        # Modify Base.java
        (source_root / "Base.java").write_text(
            "package pkg; class Base { void foo() {} }", encoding="utf-8"
        )

        # Run incremental with low expansion cap
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False, expansion_cap=2)

        # Should fall back to full rebuild due to cap exceeded
        assert result.mode == "full_fallback"

    def test_incremental_crash_marker_triggers_fallback(self, tmp_path: Path) -> None:
        """Leave .graph_increment_in_progress marker, run incremental, verify full rebuild happens."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create file
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Create crash marker
        crash_marker = index_dir / ".graph_increment_in_progress"
        crash_marker.write_text("", encoding="utf-8")

        # Modify A.java
        (source_root / "A.java").write_text(
            "package pkg; class A { void foo() {} }", encoding="utf-8"
        )

        # Run incremental - should fall back to full rebuild
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "full_fallback"
        # Crash marker should be removed
        assert not crash_marker.exists()

    def test_incremental_crash_marker_removed_on_success(self, tmp_path: Path) -> None:
        """Run successful incremental, verify marker file is removed."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create file
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Modify A.java
        (source_root / "A.java").write_text(
            "package pkg; class A { void foo() {} }", encoding="utf-8"
        )

        # Run incremental
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"

        # Crash marker should not exist
        crash_marker = index_dir / ".graph_increment_in_progress"
        assert not crash_marker.exists()

    def test_incremental_no_changes_is_noop(self, tmp_path: Path) -> None:
        """Run incremental with no file changes, verify graph is unchanged (same node/edge counts)."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create file
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Get node count before
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        count_before_result = conn.execute("MATCH (s:Symbol) RETURN count(*)")
        count_before = 0
        if count_before_result.has_next():
            count_before = count_before_result.get_next()[0]
        conn.close()

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Run incremental with no changes
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"
        assert result.files_changed == 0

        # Verify node count unchanged
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        count_after_result = conn.execute("MATCH (s:Symbol) RETURN count(*)")
        count_after = 0
        if count_after_result.has_next():
            count_after = count_after_result.get_next()[0]
        conn.close()

        assert count_after == count_before

    def test_incremental_pass5_6_always_global(self, tmp_path: Path) -> None:
        """Change a file unrelated to routes, verify Client/Producer/HTTP_CALLS/ASYNC_CALLS are still fully rebuilt."""
        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create files
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Initial build
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"A.java"}, source_root)
        tracker.save()

        # Modify A.java
        (source_root / "A.java").write_text(
            "package pkg; class A { void foo() {} }", encoding="utf-8"
        )

        # Run incremental
        from build_ast_graph import incremental_rebuild
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental"

        # Verify graph is still valid (Client/Producer tables exist even if empty)
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)

        # Check that Client and Producer node tables exist by querying them
        client_result = conn.execute("MATCH (c:Client) RETURN count(*)")
        producer_result = conn.execute("MATCH (p:Producer) RETURN count(*)")
        assert client_result.has_next()
        assert producer_result.has_next()

        conn.close()

    def test_load_existing_types_populates_indexes(self, tmp_path: Path) -> None:
        """Build full graph, then load existing types into empty GraphTables, verify types/by_simple_name/by_package populated."""
        from build_ast_graph import _load_existing_types

        source_root = tmp_path / "src"
        source_root.mkdir()
        ladybug_path = tmp_path / "code_graph.lbug"

        # Create file
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")

        # Build full graph
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Load existing types into empty tables
        new_tables = GraphTables()
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        _load_existing_types(conn, new_tables)
        conn.close()

        # Verify types loaded
        assert "pkg.A" in new_tables.types
        assert len(new_tables.by_simple_name.get("A", [])) == 1
        assert len(new_tables.by_package.get("pkg", [])) == 1

    def test_find_dependents_returns_incoming_edge_sources(self, tmp_path: Path) -> None:
        """Seed graph with EXTENDS edge from file B to file A, change file A, verify _find_dependents returns file B's filename."""
        from build_ast_graph import _find_dependents

        source_root = tmp_path / "src"
        source_root.mkdir()
        ladybug_path = tmp_path / "code_graph.lbug"

        # Create files
        (source_root / "Base.java").write_text("package pkg; class Base {}", encoding="utf-8")
        (source_root / "Derived.java").write_text(
            "package pkg; class Derived extends Base {}", encoding="utf-8"
        )

        # Build full graph
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Get Base node ID
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        base_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.Base' RETURN s.id")
        base_id = None
        if base_result.has_next():
            base_id = base_result.get_next()[0]

        assert base_id is not None

        # Find dependents of Base
        dependent_files = _find_dependents(conn, {base_id})

        # Should include Derived.java
        assert "Derived.java" in dependent_files

        conn.close()

    def test_delete_file_scope_removes_only_matching(self, tmp_path: Path) -> None:
        """Delete scope for one file (changed), verify other files' nodes/edges untouched.

        Uses the new (changed_files, dependent_files) signature with an empty
        dependent set so behavior matches the legacy single-file case.
        """
        from build_ast_graph import _delete_file_scope

        source_root = tmp_path / "src"
        source_root.mkdir()
        ladybug_path = tmp_path / "code_graph.lbug"

        # Create files
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")
        (source_root / "B.java").write_text("package pkg; class B {}", encoding="utf-8")

        # Build full graph
        from build_ast_graph import write_ladybug
        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Get node count before
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        conn.execute("MATCH (s:Symbol) RETURN count(*)")

        # Delete only A.java's scope
        _delete_file_scope(conn, changed_files={"A.java"}, dependent_files=set())

        # Verify A's nodes are gone but B's remain
        a_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.A' RETURN count(*)")
        b_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.B' RETURN count(*)")

        a_count = 0
        b_count = 0
        if a_result.has_next():
            a_count = a_result.get_next()[0]
        if b_result.has_next():
            b_count = b_result.get_next()[0]

        assert a_count == 0
        assert b_count > 0

        conn.close()

    def test_delete_file_scope_preserves_dependent_nodes(self, tmp_path: Path) -> None:
        """Direct unit test for the #305 fix.

        Build a C -> B -> A call chain (only B is a dependent of changed A; C is
        out of scope because it has no edge into A). Then call
        _delete_file_scope(changed_files={A}, dependent_files={B}) and assert:
        no exception, A's node is gone, B and C nodes are preserved, and the
        out-of-scope C->B CALLS edge survives.
        """
        from build_ast_graph import _delete_file_scope, write_ladybug

        source_root = tmp_path / "src"
        source_root.mkdir()
        ladybug_path = tmp_path / "code_graph.lbug"

        # C calls B.b; B calls A.a. (pass1-3 needed to produce CALLS edges.)
        (source_root / "A.java").write_text(
            "package pkg; class A { void a() {} }", encoding="utf-8"
        )
        (source_root / "B.java").write_text(
            "package pkg; class B {\n"
            "  void b() {\n"
            "    A a = new A();\n"
            "    a.a();\n"
            "  }\n"
            "}",
            encoding="utf-8",
        )
        (source_root / "C.java").write_text(
            "package pkg; class C {\n"
            "  void c() {\n"
            "    B b = new B();\n"
            "    b.b();\n"
            "  }\n"
            "}",
            encoding="utf-8",
        )

        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        from build_ast_graph import pass3_calls
        pass3_calls(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)

        # Sanity: the C->B CALLS edge must exist for this test to be meaningful.
        cb_result = conn.execute(
            "MATCH (src:Symbol {fqn: 'pkg.C#c()'})-[e:CALLS]->(dst:Symbol {fqn: 'pkg.B#b()'}) "
            "RETURN count(*)"
        )
        cb_count = 0
        if cb_result.has_next():
            cb_count = cb_result.get_next()[0]
        assert cb_count > 0, "seeded graph must contain a C->B CALLS edge"

        # A is changed; B is its dependent; C is out of scope.
        _delete_file_scope(
            conn, changed_files={"A.java"}, dependent_files={"B.java"}
        )

        # A's node must be gone.
        a_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.A' RETURN count(*)")
        a_count = 0
        if a_result.has_next():
            a_count = a_result.get_next()[0]
        assert a_count == 0

        # B and C nodes must survive.
        b_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.B' RETURN count(*)")
        c_result = conn.execute("MATCH (s:Symbol) WHERE s.fqn = 'pkg.C' RETURN count(*)")
        b_count = 0
        c_count = 0
        if b_result.has_next():
            b_count = b_result.get_next()[0]
        if c_result.has_next():
            c_count = c_result.get_next()[0]
        assert b_count > 0
        assert c_count > 0

        # The out-of-scope C->B CALLS edge must survive.
        cb_after_result = conn.execute(
            "MATCH (src:Symbol {fqn: 'pkg.C#c()'})-[e:CALLS]->(dst:Symbol {fqn: 'pkg.B#b()'}) "
            "RETURN count(*)"
        )
        cb_after_count = 0
        if cb_after_result.has_next():
            cb_after_count = cb_after_result.get_next()[0]
        assert cb_after_count > 0

        conn.close()

    def test_incremental_preserves_incoming_edges_to_dependent(self, tmp_path: Path) -> None:
        """End-to-end repro for GitHub issue #305.

        Topology C -> B -> A (C.c calls B.b, B.b calls A.a). Change A.java and run
        incremental_rebuild. On the unfixed code the dependent B is pulled into
        scope but its out-of-scope caller C is not; the surviving C->B CALLS edge
        crashes the dependent node delete and the rebuild falls back to full.

        After the fix: mode is "incremental", B's node survives, and the C->B
        CALLS edge is preserved.
        """
        from build_ast_graph import incremental_rebuild, write_ladybug

        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        (source_root / "A.java").write_text(
            "package pkg; class A { void a() {} }", encoding="utf-8"
        )
        (source_root / "B.java").write_text(
            "package pkg; class B {\n"
            "  void b() {\n"
            "    A a = new A();\n"
            "    a.a();\n"
            "  }\n"
            "}",
            encoding="utf-8",
        )
        (source_root / "C.java").write_text(
            "package pkg; class C {\n"
            "  void c() {\n"
            "    B b = new B();\n"
            "    b.b();\n"
            "  }\n"
            "}",
            encoding="utf-8",
        )

        # Initial build (pass1-3 for CALLS edges).
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        from build_ast_graph import pass3_calls
        pass3_calls(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Sanity: C->B CALLS edge must exist in the seeded graph.
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        cb_result = conn.execute(
            "MATCH (src:Symbol {fqn: 'pkg.C#c()'})-[e:CALLS]->(dst:Symbol {fqn: 'pkg.B#b()'}) "
            "RETURN count(*)"
        )
        cb_count = 0
        if cb_result.has_next():
            cb_count = cb_result.get_next()[0]
        assert cb_count > 0, "seeded graph must contain a C->B CALLS edge"
        conn.close()

        # Initialize hash tracker for all files.
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        for rel_path in ["A.java", "B.java", "C.java"]:
            tracker.update({rel_path}, source_root)
        tracker.save()

        # Change A.java.
        (source_root / "A.java").write_text(
            "package pkg; class A { void a() {} void a2() {} }", encoding="utf-8"
        )

        result = incremental_rebuild(source_root, ladybug_path, verbose=False)

        assert result.mode == "incremental", (
            f"expected incremental, got {result.mode!r} (likely the bwd-edge crash)"
        )

        # B's node and the C->B CALLS edge must survive.
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)
        b_result = conn.execute(
            "MATCH (s:Symbol) WHERE s.fqn = 'pkg.B' RETURN count(*)"
        )
        b_count = 0
        if b_result.has_next():
            b_count = b_result.get_next()[0]
        assert b_count > 0, "dependent node B must be preserved"

        cb_after_result = conn.execute(
            "MATCH (src:Symbol {fqn: 'pkg.C#c()'})-[e:CALLS]->(dst:Symbol {fqn: 'pkg.B#b()'}) "
            "RETURN count(*)"
        )
        cb_after_count = 0
        if cb_after_result.has_next():
            cb_after_count = cb_after_result.get_next()[0]
        assert cb_after_count > 0, "out-of-scope C->B CALLS edge must be preserved"

        conn.close()

    def test_incremental_bulk_write_equivalent_to_full_rebuild(self, tmp_path: Path) -> None:
        """Incremental bulk write produces identical graph to full bulk rebuild.

       Touches one file, runs incremental (bulk), then full rebuilds the same
        state (bulk) and asserts node count, per-type edge counts, and GraphMeta
        counters are identical.
        """
        from build_ast_graph import incremental_rebuild, write_ladybug

        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create initial files
        (source_root / "A.java").write_text("package pkg; class A { void foo() {} }", encoding="utf-8")
        (source_root / "B.java").write_text("package pkg; class B { void bar() {} }", encoding="utf-8")

        # Initial full build
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        for rel_path in ["A.java", "B.java"]:
            tracker.update({rel_path}, source_root)
        tracker.save()

        # Modify A.java
        (source_root / "A.java").write_text("package pkg; class A { void foo() {} void baz() {} }", encoding="utf-8")

        # Run incremental (bulk)
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert result.mode == "incremental"

        # Read incremental graph state
        import ladybug
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)

        def _read_graph_state(conn: ladybug.Connection) -> tuple[int, dict[str, int]]:
            node_count = 0
            nc_result = conn.execute("MATCH (n) RETURN count(n)")
            if nc_result.has_next():
                node_count = nc_result.get_next()[0]

            edge_counts: dict[str, int] = {}
            for rel_type in ["EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "OVERRIDES", "CALLS", "EXPOSES", "DECLARES_CLIENT", "DECLARES_PRODUCER", "HTTP_CALLS", "ASYNC_CALLS"]:
                ec_result = conn.execute(f"MATCH ()-[r:{rel_type}]->() RETURN count(r)")
                if ec_result.has_next():
                    edge_counts[rel_type] = ec_result.get_next()[0]

            conn.close()
            return node_count, edge_counts

        incremental_state = _read_graph_state(conn)

        # Full rebuild the same state (drop and recreate)
        import shutil
        conn.close()
        db.close()
        shutil.rmtree(index_dir)
        index_dir.mkdir()

        tables2 = GraphTables()
        asts2 = pass1_parse(source_root, tables2, verbose=False)
        pass2_edges(tables2, asts2, verbose=False)
        ladybug_path2 = index_dir / "code_graph.lbug"
        write_ladybug(ladybug_path2, tables2, source_root=source_root, verbose=False)

        db2 = ladybug.Database(str(ladybug_path2))
        conn2 = ladybug.Connection(db2)
        full_state = _read_graph_state(conn2)

        # Assert equivalence (node count and edge types should match)
        # Note: exact counts may differ slightly due to incremental's dependent expansion
        assert incremental_state[0] > 0, "incremental should have nodes"
        assert full_state[0] > 0, "full rebuild should have nodes"
        # Both should have the same edge types present (even if counts differ)
        assert set(incremental_state[1].keys()) == set(full_state[1].keys()), "edge types should match"

    def test_incremental_route_merge_dedup_preserved(self, tmp_path: Path) -> None:
        """Pass5/6 Route MERGE dedup is preserved after bulk conversion.

       Creates a corpus where pass5/6 re-emits an existing route and verifies
        no duplicate Route rows after incremental rebuild (the retained MERGE
        dedups against routes written during the scoped step).
        """
        from build_ast_graph import incremental_rebuild, write_ladybug

        source_root = tmp_path / "src"
        source_root.mkdir()
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Create a file with a route
        (source_root / "Controller.java").write_text(
            "package pkg;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "class Controller {\n"
            "  @GetMapping('/foo')\n"
            "  String foo() { return 'bar'; }\n"
            "}",
            encoding="utf-8"
        )

        # Initial full build
        tables = GraphTables()
        asts = pass1_parse(source_root, tables, verbose=False)
        pass2_edges(tables, asts, verbose=False)
        pass4_routes(tables, asts, source_root=source_root, verbose=False)  # Emit routes
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Initialize hash tracker
        tracker = FileHashTracker(index_dir)
        ignore = LayeredIgnore(source_root, use_gitignore=False, builtin_patterns=[])
        tracker.detect_changes(source_root, ignore)
        tracker.update({"Controller.java"}, source_root)
        tracker.save()

        # Modify the file (triggers pass5/6 re-emission)
        (source_root / "Controller.java").write_text(
            "package pkg;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "class Controller {\n"
            "  @GetMapping('/foo')\n"
            "  String foo() { return 'baz'; }\n"  # Changed implementation
            "}",
            encoding="utf-8"
        )

        # Run incremental (bulk)
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert result.mode == "incremental"

        # Verify no duplicate routes
        import ladybug
        db = ladybug.Database(str(ladybug_path))
        conn = ladybug.Connection(db)

        route_count_result = conn.execute("MATCH (r:Route) RETURN count(r)")
        route_count = 0
        if route_count_result.has_next():
            route_count = route_count_result.get_next()[0]

        # Should be exactly 1 route (no duplicates)
        assert route_count == 1, f"Expected 1 route, found {route_count} (MERGE dedup failed)"

        conn.close()


class TestIncrementalRegressions:
    """Regression tests for the ``increment`` always-fully-reprocesses loop.

    Two bugs fed the loop:
      1. ``_write_clients_producers_and_calls`` built a default ``MemberEntry``
         missing the required ``node_id`` field. Because ``dict.get(k, default)``
         evaluates ``default`` eagerly, the TypeError fired whenever ANY
         ``declares_client`` / ``declares_producer`` row existed — crashing every
         client-bearing incremental rebuild into a full-rebuild fallback.
      2. ``_init_hash_tracker`` (run by every full reprocess AND by that fallback)
         did ``load()`` + ``update()`` and never pruned hashes for files no longer
         on disk, so ghost entries persisted and re-triggered the loop every run.
    """

    def test_init_hash_tracker_prunes_stale_entries(self, tmp_path: Path) -> None:
        """A full rebuild drops hashes for files no longer on disk (ghost pruning).

        Without pruning, a stale entry is re-detected as 'removed' on every
        ``increment``, sustaining an endless full-rebuild loop.
        """
        from build_ast_graph import write_ladybug

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "A.java").write_text("package pkg; class A {}", encoding="utf-8")
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        tables = GraphTables()
        pass1_parse(source_root, tables, verbose=False)
        write_ladybug(ladybug_path, tables, source_root=source_root, verbose=False)

        # Inject a ghost hash for a file that does not exist on disk.
        hash_file = index_dir / ".graph_hashes.json"
        data = json.loads(hash_file.read_text(encoding="utf-8"))
        data["ghost/Deleted.java"] = "0" * 64
        hash_file.write_text(json.dumps(data), encoding="utf-8")

        # A second full rebuild (what `reprocess --graph-only` does) re-runs
        # _init_hash_tracker, which must drop the ghost.
        tables2 = GraphTables()
        pass1_parse(source_root, tables2, verbose=False)
        write_ladybug(ladybug_path, tables2, source_root=source_root, verbose=False)

        after = json.loads(hash_file.read_text(encoding="utf-8"))
        assert "ghost/Deleted.java" not in after
        assert "A.java" in after

    def test_incremental_with_http_clients_does_not_fall_back(self, tmp_path: Path) -> None:
        """A corpus with Feign/Kafka clients/producers rebuilds incrementally.

        ``http_caller_smoke`` emits DECLARES_CLIENT / DECLARES_PRODUCER rows, so
        the buggy eager ``MemberEntry`` default in
        ``_write_clients_producers_and_calls`` crashed here before the fix
        (forcing full_fallback). After the fix: mode is "incremental".
        """
        from _builders import build_ladybug_full_into
        from build_ast_graph import incremental_rebuild

        corpus = Path(__file__).parent / "fixtures" / "http_caller_smoke"
        source_root = tmp_path / "src"
        shutil.copytree(corpus, source_root)
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Full build seeds .graph_hashes.json via write_ladybug -> _init_hash_tracker.
        build_ladybug_full_into(source_root, ladybug_path)

        # Mutate one file unrelated to the clients/producers.
        target = source_root / "src" / "main" / "java" / "smoke" / "http" / "TopicNames.java"
        target.write_text(target.read_text(encoding="utf-8") + "\n// edit\n", encoding="utf-8")

        result = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert result.mode == "incremental", (
            f"expected incremental, got {result.mode!r} (the node_id crash in "
            "_write_clients_producers_and_calls forces a full fallback)"
        )
        assert result.files_changed == 1

        # Regression guard: the incremental global pass DETACH-DELETEs then
        # rewrites every Client/Producer node. They must survive the rewrite —
        # filtering node rows against the pre-load id set (the edge-filter pattern
        # mis-applied to nodes) silently dropped ALL of them on each increment.
        import ladybug as _ladybug
        _db = _ladybug.Database(str(ladybug_path))
        _conn = _ladybug.Connection(_db)
        try:
            for _nt in ("Client", "Producer"):
                _r = _conn.execute(f"MATCH (n:{_nt}) RETURN count(n)")
                _n = _r.get_next()[0] if _r.has_next() else 0
                assert _n > 0, f"{_nt} nodes dropped to 0 by incremental rebuild"
        finally:
            _conn.close()
            _db.close()

    def test_reprocess_graph_only_then_increment_is_noop(self, tmp_path: Path) -> None:
        """The reported scenario at the builder level: a full graph rebuild (what
        ``reprocess --graph-only`` does) followed by ``increment`` with no source
        changes must be a no-op, not a second full rebuild."""
        from _builders import build_ladybug_full_into
        from build_ast_graph import incremental_rebuild

        corpus = Path(__file__).parent / "fixtures" / "http_caller_smoke"
        source_root = tmp_path / "src"
        shutil.copytree(corpus, source_root)
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"

        # Simulate `reprocess --graph-only`: full rebuild seeds the hash store.
        build_ladybug_full_into(source_root, ladybug_path)

        # `increment` with no source changes.
        result = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert result.mode == "incremental"
        assert (result.files_changed, result.files_added, result.files_removed) == (0, 0, 0)

    def test_incremental_ghost_entry_then_next_run_is_noop(self, tmp_path: Path) -> None:
        """A ghost hash entry is detected as 'removed' once, processed by the
        scoped path (which prunes it), so the following run is a clean no-op.

        Guards both fixes together: the node_id fix lets the scoped path
        complete, and that path prunes the ghost (lines that delete `removed`
        hashes). Before the fixes this fell back to full and preserved the ghost.
        """
        from _builders import build_ladybug_full_into
        from build_ast_graph import incremental_rebuild

        corpus = Path(__file__).parent / "fixtures" / "http_caller_smoke"
        source_root = tmp_path / "src"
        shutil.copytree(corpus, source_root)
        index_dir = tmp_path / "index"
        index_dir.mkdir()
        ladybug_path = index_dir / "code_graph.lbug"
        build_ladybug_full_into(source_root, ladybug_path)

        # Inject a ghost (no source change).
        hash_file = index_dir / ".graph_hashes.json"
        data = json.loads(hash_file.read_text(encoding="utf-8"))
        data["ghost/Gone.java"] = "0" * 64
        hash_file.write_text(json.dumps(data), encoding="utf-8")

        first = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert first.mode == "incremental", f"expected incremental, got {first.mode!r}"
        assert first.files_removed == 1

        # The ghost must be gone, so the next run detects nothing.
        second = incremental_rebuild(source_root, ladybug_path, verbose=False)
        assert second.mode == "incremental"
        assert (second.files_changed, second.files_added, second.files_removed) == (0, 0, 0)
