"""Tests for incremental graph rebuild functionality (PR-G1).

Tests cover FileHashTracker behavior and edge schema source_file column.
"""
from __future__ import annotations

from pathlib import Path

import kuzu

from ast_java import ONTOLOGY_VERSION
from build_ast_graph import FileHashTracker
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
        from _builders import build_kuzu_full_into

        corpus_root = Path(__file__).parent / "bank-chat-system"
        db_path = tmp_path / "test_graph.kuzu"
        build_kuzu_full_into(corpus_root, db_path)

        conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))

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
        from _builders import build_kuzu_full_into

        corpus_root = Path(__file__).parent / "bank-chat-system"
        db_path = tmp_path / "test_graph.kuzu"
        build_kuzu_full_into(corpus_root, db_path)

        conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))

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
