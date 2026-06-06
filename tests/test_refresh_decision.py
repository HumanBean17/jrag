"""Tests for refresh_decision.py decision engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refresh_decision import (
    ChangeSet,
    _choose_refresh_mode,
    _current_ontology_version,
    choose_refresh_mode,
)


def _make_deps_json(
    path: Path,
    ontology_version: int,
    file_count: int = 5,
    file_hashes: dict[str, str] | None = None,
) -> None:
    """Write a valid .deps.json to the kuzu_path's parent directory."""
    deps_path = path.parent / ".deps.json"
    deps_path.parent.mkdir(parents=True, exist_ok=True)
    files = {}
    for i in range(file_count):
        name = f"src/File{i}.java"
        files[name] = {
            "ext_hash": file_hashes.get(name, "sha256:abc") if file_hashes else "sha256:abc",
            "declares": [],
            "injects": [],
            "extends": [],
            "calls": [],
            "uses_anno": [],
            "overrides": [],
            "declares_clients": [],
            "declares_producers": [],
        }
    deps_path.write_text(json.dumps({
        "version": 1,
        "ontology_version": ontology_version,
        "files": files,
    }))


@pytest.fixture
def tmp_kuzu_path(tmp_path: Path) -> Path:
    """Provide a temp kuzu path with valid .deps.json."""
    kuzu = tmp_path / "code_graph.kuzu"
    _make_deps_json(kuzu, _current_ontology_version())
    return kuzu


def test_auto_modified_only_incremental(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(modified=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "incremental"
    assert decision.lance_mode == "incremental"


def test_auto_deleted_file_full_kuzu(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(deleted=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "full"
    assert decision.lance_mode == "incremental"
    assert any("deleted" in r for r in decision.reasons)


def test_auto_renamed_file_full_kuzu(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(renamed=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "full"
    assert decision.lance_mode == "incremental"
    assert any("renamed" in r for r in decision.reasons)


def test_auto_config_change_full(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(modified=(".java-codebase-rag.yml",), config_changed=True)
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "full"
    assert decision.lance_mode == "full"
    assert any("config" in r for r in decision.reasons)


def test_auto_empty_changes_incremental(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    kuzu.parent.mkdir(parents=True, exist_ok=True)
    _make_deps_json(kuzu, _current_ontology_version())
    changes = ChangeSet()
    decision = _choose_refresh_mode(changes, kuzu_path=kuzu, mode="auto")
    assert decision.kuzu_mode == "incremental"


def test_explicit_full_overrides(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(modified=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="full")
    assert decision.kuzu_mode == "full"
    assert decision.lance_mode == "full"
    assert any("explicit" in r for r in decision.reasons)


def test_deps_missing_full_kuzu(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    kuzu.parent.mkdir(parents=True, exist_ok=True)
    changes = ChangeSet(modified=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=kuzu, mode="auto")
    assert decision.kuzu_mode == "full"
    assert any("deps" in r for r in decision.reasons)


def test_deps_stale_ontology_full_kuzu(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    _make_deps_json(kuzu, ontology_version=0)
    changes = ChangeSet(modified=("src/Foo.java",))
    decision = _choose_refresh_mode(changes, kuzu_path=kuzu, mode="auto")
    assert decision.kuzu_mode == "full"
    assert any("ontology" in r for r in decision.reasons)


def test_hash_based_detects_new_file(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    _make_deps_json(kuzu, _current_ontology_version(), file_count=0)
    # Create a .java file on disk not in the index
    src = tmp_path / "src"
    src.mkdir()
    (src / "NewFile.java").write_text("class NewFile {}")
    decision = choose_refresh_mode(tmp_path, kuzu, mode="auto")
    assert "src/NewFile.java" in decision.detected_changes.added


def test_hash_based_detects_modified_file(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    src = tmp_path / "src"
    src.mkdir()
    f = src / "File0.java"
    f.write_text("original")
    _make_deps_json(kuzu, _current_ontology_version(), file_count=1)
    # Change the file content (hash will differ from cached "sha256:abc")
    f.write_text("modified")
    decision = choose_refresh_mode(tmp_path, kuzu, mode="auto")
    assert "src/File0.java" in decision.detected_changes.modified


def test_hash_based_detects_deleted_file(tmp_path: Path) -> None:
    kuzu = tmp_path / "code_graph.kuzu"
    _make_deps_json(kuzu, _current_ontology_version(), file_count=1)
    # File in index but not on disk → deleted
    decision = choose_refresh_mode(tmp_path, kuzu, mode="auto")
    assert "src/File0.java" in decision.detected_changes.deleted


def test_pipeline_changed_full(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(modified=("build_ast_graph.py",), pipeline_changed=True)
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "full"
    assert decision.lance_mode == "full"
    assert any("pipeline" in r for r in decision.reasons)


def test_meta_annotation_changed_full(tmp_kuzu_path: Path) -> None:
    changes = ChangeSet(modified=("src/CustomAnnotation.java",), meta_annotation_changed=True)
    decision = _choose_refresh_mode(changes, kuzu_path=tmp_kuzu_path, mode="auto")
    assert decision.kuzu_mode == "full"
    assert any("meta-annotation" in r for r in decision.reasons)


def test_large_dirty_set_full(tmp_path: Path) -> None:
    """When >50% files are dirty, fall back to full rebuild."""
    kuzu = tmp_path / "code_graph.kuzu"
    _make_deps_json(kuzu, _current_ontology_version(), file_count=3)
    changes = ChangeSet(modified=("src/File0.java", "src/File1.java"))
    decision = _choose_refresh_mode(changes, kuzu_path=kuzu, mode="auto")
    assert decision.kuzu_mode == "full"
    assert any("50%" in r for r in decision.reasons)
