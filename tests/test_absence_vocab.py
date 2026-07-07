"""Tests for absence_vocab.py VocabularyIndex and lazy-load helper."""

import json
from pathlib import Path
from unittest import mock

import pytest

# These imports will fail until absence_vocab.py is created
from absence_vocab import (
    SymbolRecord,
    VocabularyIndex,
    VocabIndexStale,
    get_vocabulary_index,
    reset_cache,
    VOCAB_INDEX_FILENAME,
)


class TestVocabularyIndexBuild:
    """Tests for VocabularyIndex.build() from a LadybugGraph."""

    def test_build_returns_index_with_symbol_count(self, ladybug_graph):
        """VocabularyIndex.build(graph, q=3) returns an index whose symbol_count >= 1."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        assert index.symbol_count >= 1
        # Should be at least the number of class symbols in bank-chat-system
        assert index.symbol_count >= 20  # bank-chat has at least 20 classes

    def test_build_records_contain_expected_fields(self, ladybug_graph):
        """Built index records contain all SymbolRecord fields."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        assert len(index.records) > 0

        record = index.records[0]
        assert isinstance(record, SymbolRecord)
        assert hasattr(record, "node_id")
        assert hasattr(record, "fqn")
        assert hasattr(record, "simple_name")
        assert hasattr(record, "normalized_name")
        assert hasattr(record, "kind")
        assert hasattr(record, "module")
        assert hasattr(record, "microservice")
        assert hasattr(record, "role")
        assert hasattr(record, "resolved")

    def test_build_ngram_index_structure(self, ladybug_graph):
        """Built index has a valid ngram index mapping grams to record lists."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        assert isinstance(index.ngram_index, dict)
        assert len(index.ngram_index) > 0

        # Each gram should map to a list of integers (record indexes)
        for gram, record_idxs in list(index.ngram_index.items())[:10]:  # Check first 10
            assert isinstance(gram, str)
            assert len(gram) <= 3  # q=3, but can be shorter for short names
            assert isinstance(record_idxs, list)
            assert all(isinstance(idx, int) for idx in record_idxs)


class TestVocabularyIndexPersistence:
    """Tests for VocabularyIndex.save() and load() round-trip."""

    def test_save_creates_json_sidecar(self, ladybug_graph, tmp_path):
        """save() creates a JSON sidecar with expected structure."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        sidecar_path = tmp_path / VOCAB_INDEX_FILENAME

        from ast_java import ONTOLOGY_VERSION
        index.save(sidecar_path, ontology_version=ONTOLOGY_VERSION)

        assert sidecar_path.exists()
        with open(sidecar_path) as f:
            data = json.load(f)

        assert "format_version" in data
        assert "ontology_version" in data
        assert "built_at" in data
        assert "symbol_count" in data
        assert "q" in data
        assert "records" in data
        assert "ngrams" in data

    def test_save_load_roundtrip(self, ladybug_graph, tmp_path):
        """save() then load() round-trips: symbol_count equal; a known symbol present."""
        from ast_java import ONTOLOGY_VERSION

        original = VocabularyIndex.build(ladybug_graph, q=3)
        sidecar_path = tmp_path / VOCAB_INDEX_FILENAME
        original.save(sidecar_path, ontology_version=ONTOLOGY_VERSION)

        loaded = VocabularyIndex.load(sidecar_path)

        assert loaded.symbol_count == original.symbol_count
        assert len(loaded.records) == len(original.records)

        # Find a known symbol from bank-chat-system (ChatAssignApplication exists in the corpus)
        found_names = {r.simple_name for r in loaded.records}
        # At least one expected symbol should be present
        assert any(name in found_names for name in ["ChatAssignApplication", "ChatManagementController", "OperatorManagementController"])

    def test_load_stale_ontology_version_raises(self, ladybug_graph, tmp_path):
        """load() on a sidecar with stale ontology_version raises VocabIndexStale."""
        from ast_java import ONTOLOGY_VERSION

        index = VocabularyIndex.build(ladybug_graph, q=3)
        sidecar_path = tmp_path / VOCAB_INDEX_FILENAME
        index.save(sidecar_path, ontology_version=ONTOLOGY_VERSION)

        # Manually corrupt the ontology_version in the sidecar
        with open(sidecar_path) as f:
            data = json.load(f)
        data["ontology_version"] = 999  # Wrong version
        with open(sidecar_path, "w") as f:
            json.dump(data, f)

        with pytest.raises(VocabIndexStale):
            VocabularyIndex.load(sidecar_path)


class TestVocabularyIndexLookup:
    """Tests for VocabularyIndex.lookup() candidate retrieval."""

    def test_lookup_exact_name_returns_record(self, ladybug_graph):
        """lookup("ChatAssignApplication", limit=5) returns that record among candidates."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        candidates = index.lookup("ChatAssignApplication", limit=5)

        assert len(candidates) > 0
        # Should find ChatAssignApplication
        assert any(r.simple_name == "ChatAssignApplication" for r in candidates)

    def test_lookup_typo_name_returns_closest_match(self, ladybug_graph):
        """lookup("ChatAssignApp") for typoed name still returns ChatAssignApplication among candidates."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        candidates = index.lookup("ChatAssignApp", limit=50)

        assert len(candidates) > 0
        # Should still find ChatAssignApplication via n-gram overlap
        assert any(r.simple_name == "ChatAssignApplication" for r in candidates)

    def test_lookup_respects_limit(self, ladybug_graph):
        """lookup() with limit=2 returns at most 2 candidates."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        candidates = index.lookup("Service", limit=2)

        assert len(candidates) <= 2


class TestVocabularyIndexIsExternal:
    """Tests for VocabularyIndex.is_external()."""

    def test_is_external_java_util_list_returns_prefix(self, ladybug_graph):
        """is_external("java.util.List") → (True, "prefix")."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        is_ext, reason = index.is_external("java.util.List")

        assert is_ext is True
        assert reason == "prefix"

    def test_is_external_phantom_symbol_returns_phantom(self, ladybug_graph):
        """is_external() on a phantom present in the corpus → (True, "phantom")."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        # Find a phantom symbol (resolved=False) if any exist in bank-chat
        phantom = next((r for r in index.records if not r.resolved), None)
        if phantom:
            is_ext, reason = index.is_external(phantom.simple_name)
            assert is_ext is True
            assert reason == "phantom"

    def test_is_external_project_symbol_returns_false(self, ladybug_graph):
        """is_external() on a real project symbol → (False, None)."""
        index = VocabularyIndex.build(ladybug_graph, q=3)
        # ChatAssignApplication is a real project symbol
        is_ext, reason = index.is_external("ChatAssignApplication")

        assert is_ext is False
        assert reason is None


class TestGetVocabularyIndex:
    """Tests for get_vocabulary_index() lazy-load helper."""

    def test_first_call_builds_and_saves(self, ladybug_graph, tmp_path, monkeypatch):
        """First call with no sidecar builds + saves the index."""
        from ast_java import ONTOLOGY_VERSION
        from java_codebase_rag.config import resolve_operator_config
        import os

        # Use tmp_path as the index dir via environment variable
        monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(tmp_path))
        monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(Path.cwd()))

        cfg = resolve_operator_config(source_root=Path.cwd())

        # Sidecar doesn't exist yet (check at ladybug_graph.db_path location)
        sidecar_path = Path(ladybug_graph.db_path).parent / VOCAB_INDEX_FILENAME
        # Delete sidecar if it exists from previous test runs
        if sidecar_path.exists():
            sidecar_path.unlink()

        index = get_vocabulary_index(ladybug_graph, cfg)

        assert index.symbol_count > 0
        assert sidecar_path.exists()  # Should have been saved

    def test_second_call_loads_from_cache(self, ladybug_graph, tmp_path, monkeypatch):
        """Second call loads from file (build runs once)."""
        from java_codebase_rag.config import resolve_operator_config
        from unittest.mock import patch

        # Use tmp_path as the index dir
        monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(tmp_path))
        monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(Path.cwd()))

        cfg = resolve_operator_config(source_root=Path.cwd())

        # First call - builds
        get_vocabulary_index(ladybug_graph, cfg)

        # Spy on VocabularyIndex.build
        with patch.object(VocabularyIndex, 'build') as mock_build:
            # Second call - should load, not build
            get_vocabulary_index(ladybug_graph, cfg)
            mock_build.assert_not_called()

    def test_get_vocabulary_index_cache_can_be_reset(self, ladybug_graph, tmp_path, monkeypatch):
        """Cache can be reset via reset_cache() for test isolation."""
        from java_codebase_rag.config import resolve_operator_config

        # Use tmp_path as the index dir
        monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(tmp_path))
        monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(Path.cwd()))

        cfg = resolve_operator_config(source_root=Path.cwd())

        # First call - builds and caches
        index1 = get_vocabulary_index(ladybug_graph, cfg)
        assert index1.symbol_count > 0

        # Verify it's cached by calling again (should return same instance)
        index2 = get_vocabulary_index(ladybug_graph, cfg)
        assert index1 is index2  # Same instance from cache

        # Reset cache
        reset_cache()

        # After reset, new call returns a new instance (loaded from sidecar)
        index3 = get_vocabulary_index(ladybug_graph, cfg)
        assert index3 is not index1  # Different instance after cache reset
        assert index3.symbol_count == index1.symbol_count  # Same data though


class TestBuildFailureResilience:
    """Tests that build failures don't break the graph build."""

    def test_write_ladybug_succeeds_when_vocab_build_fails(self, corpus_root, tmp_path):
        """Monkeypatch build to raise → write_ladybug still succeeds (graph written; sidecar absent)."""
        from build_ast_graph import write_ladybug, GraphTables

        db_path = tmp_path / "code_graph.lbug"
        tables = GraphTables()  # Empty tables for this test

        # Mock VocabularyIndex.build to raise
        with mock.patch("absence_vocab.VocabularyIndex.build", side_effect=RuntimeError("Build failed")):
            # write_ladybug should not raise
            write_ladybug(
                db_path=db_path,
                tables=tables,
                source_root=corpus_root,
                verbose=False,
            )

        # Graph should still be written
        assert db_path.exists()

        # Sidecar should not exist (build failed)
        sidecar_path = tmp_path / VOCAB_INDEX_FILENAME
        assert not sidecar_path.exists()

    def test_try_build_vocabulary_index_failure_is_logged(self, corpus_root, tmp_path, caplog):
        """_try_build_vocabulary_index logs on failure but doesn't raise."""
        from build_ast_graph import _try_build_vocabulary_index

        db_path = tmp_path / "code_graph.lbug"
        # Create a dummy db file for testing
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()

        # Mock VocabularyIndex.build to raise
        with mock.patch("absence_vocab.VocabularyIndex.build", side_effect=RuntimeError("Build failed")):
            # Should not raise
            _try_build_vocabulary_index(db_path, corpus_root, verbose=False)

        # Should have logged a warning
        assert any("Vocabulary index build failed" in record.message for record in caplog.records)
