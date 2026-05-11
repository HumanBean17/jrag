"""Tests for layered ignore rules (PR-C B5)."""
from __future__ import annotations

import warnings
from pathlib import Path

from path_filtering import (
    COMMON_EXCLUDED_PATH_PATTERNS,
    LayeredIgnore,
    compile_excluded_glob_patterns,
    is_relative_path_excluded,
    iter_java_source_files,
)


def _legacy_java_file_count(root: Path) -> int:
    """Pre-B5 file walk: same prunes + fnmatch excludes as ``java_index_v1_common`` had."""
    import os

    globs = compile_excluded_glob_patterns(COMMON_EXCLUDED_PATH_PATTERNS)
    n = 0
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in (
                ".git",
                "target",
                "build",
                "out",
                "node_modules",
                ".venv",
                ".idea",
            )
        ]
        for fn in filenames:
            if not fn.endswith(".java"):
                continue
            p = Path(dirpath) / fn
            try:
                rel = p.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = p.as_posix()
            if is_relative_path_excluded(rel, globs):
                continue
            n += 1
    return n


def test_39_builtin_default_ignores_class_file(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    f = root / "Foo.class"
    f.write_text("", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    ign, layer = li.is_ignored(f)
    assert ign is True
    assert layer is not None
    assert layer.source == "builtin_default"


def test_40_project_root_negation_unignores(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    ig = root / ".java-codebase-rag" / "ignore"
    ig.parent.mkdir(parents=True)
    ig.write_text("!**/Foo.class\n", encoding="utf-8")
    f = root / "Foo.class"
    f.write_text("", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    assert li.is_ignored(f)[0] is False


def test_41_nested_ignore_only_under_subtree(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "svc" / ".java-codebase-rag").mkdir(parents=True)
    (root / "svc" / ".java-codebase-rag" / "ignore").write_text("**/Generated*.java\n", encoding="utf-8")
    hit = root / "svc" / "src" / "GeneratedFoo.java"
    hit.parent.mkdir(parents=True)
    hit.write_text("class GeneratedFoo {}\n", encoding="utf-8")
    sibling = root / "other" / "src" / "GeneratedBar.java"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("class GeneratedBar {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    assert li.is_ignored(hit)[0] is True
    assert li.is_ignored(sibling)[0] is False


def test_42_innermost_nested_reincludes(tmp_path: Path) -> None:
    root = tmp_path / "p"
    pr = root / ".java-codebase-rag" / "ignore"
    pr.parent.mkdir(parents=True)
    pr.write_text("**/Generated*.java\n", encoding="utf-8")
    nested = root / "svc" / ".java-codebase-rag" / "ignore"
    nested.parent.mkdir(parents=True)
    nested.write_text("!**/Generated*.java\n", encoding="utf-8")
    f = root / "svc" / "GeneratedX.java"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("class GeneratedX {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    assert li.is_ignored(f)[0] is False


def test_43_gitignore_layer(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / ".gitignore").write_text("**/customout/**\n", encoding="utf-8")
    f = root / "src" / "customout" / "X.java"
    f.parent.mkdir(parents=True)
    f.write_text("class X {}\n", encoding="utf-8")
    li_on = LayeredIgnore(root, use_gitignore=True)
    assert li_on.is_ignored(f)[0] is True
    assert li_on.is_ignored(f)[1] is not None
    assert li_on.is_ignored(f)[1].source == "gitignore"


def test_44_gitignore_disabled(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / ".gitignore").write_text("**/customout/**\n", encoding="utf-8")
    f = root / "src" / "customout" / "X.java"
    f.parent.mkdir(parents=True)
    f.write_text("class X {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    assert li.is_ignored(f)[0] is False


def test_45_diagnose_nested_cites_line(tmp_path: Path) -> None:
    root = tmp_path / "p"
    nested = root / "svc" / ".java-codebase-rag" / "ignore"
    nested.parent.mkdir(parents=True)
    nested.write_text("# header\n**/Generated*.java\n", encoding="utf-8")
    f = root / "svc" / "GeneratedZ.java"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("class Z {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    d = li.diagnose_dict(f)
    assert d["ignored"] is True
    assert d["layer"] == "nested"
    expl = str(d["explanation"])
    assert "svc/.java-codebase-rag/ignore" in expl
    assert "line 2" in expl


def test_46_outside_project_not_ignored(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    li = LayeredIgnore(root, use_gitignore=False)
    outside = tmp_path / "outside" / "Foo.java"
    outside.parent.mkdir(parents=True)
    outside.write_text("class Foo {}\n", encoding="utf-8")
    assert li.is_ignored(outside) == (False, None)


def test_bank_chat_java_count_no_lancedb_ignore_gitignore_off_matches_legacy(
    corpus_root: Path,
) -> None:
    """Behavioural compatibility: no ``.java-codebase-rag/ignore`` and no git layer → same count."""
    assert not (corpus_root / ".java-codebase-rag" / "ignore").is_file()
    legacy = _legacy_java_file_count(corpus_root)
    li = LayeredIgnore(corpus_root, use_gitignore=False)
    layered = len(list(iter_java_source_files(corpus_root, ignore=li)))
    assert layered == legacy


def test_iter_java_source_files_deprecation_warns(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "A.java").write_text("class A {}\n", encoding="utf-8")
    globs = compile_excluded_glob_patterns(COMMON_EXCLUDED_PATH_PATTERNS)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        files = list(iter_java_source_files(root, globs))
    assert any(x.category is DeprecationWarning for x in w)
    assert len(files) == 1


def test_out_as_java_package_dir_is_walked_when_no_build_indicator_sibling(
    tmp_path: Path,
) -> None:
    """Regression: a Java package literally named ``out`` under ``src/main/java`` is
    NOT pruned, because its parent directory has no Maven/Gradle indicator file.

    Prior to this fix, the unconditional ``**/out/**`` glob and unconditional
    ``os.walk`` prune dropped real source under packages like
    ``com.example.out.api.AssignEndpoint``.
    """
    root = tmp_path / "proj"
    pkg = root / "src" / "main" / "java" / "com" / "example" / "out" / "api"
    pkg.mkdir(parents=True)
    f = pkg / "AssignEndpoint.java"
    f.write_text("package com.example.out.api; interface AssignEndpoint {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    files = list(iter_java_source_files(root, ignore=li))
    assert f in files
    ign, _ = li.is_ignored(f)
    assert ign is False


def test_out_as_build_output_dir_is_pruned_when_pom_xml_sibling_present(
    tmp_path: Path,
) -> None:
    """A real Maven build output directory ``out/`` (sibling to ``pom.xml``) is
    pruned and its contents skipped."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    out = root / "out" / "production"
    out.mkdir(parents=True)
    bogus = out / "Bogus.java"
    bogus.write_text("package out.production; class Bogus {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    files = list(iter_java_source_files(root, ignore=li))
    assert bogus not in files


def test_build_output_dir_pruned_when_gradle_kts_sibling_present(tmp_path: Path) -> None:
    """Gradle Kotlin DSL (``build.gradle.kts``) also marks a directory as a JVM
    module, so a sibling ``build/`` directory is treated as build output."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    build_out = root / "build" / "classes"
    build_out.mkdir(parents=True)
    bogus = build_out / "Bogus.java"
    bogus.write_text("package build.classes; class Bogus {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    files = list(iter_java_source_files(root, ignore=li))
    assert bogus not in files


def test_target_as_package_dir_is_walked_without_pom_sibling(tmp_path: Path) -> None:
    """Symmetric to the ``out`` case: ``target`` may also be a legal Java package
    name (e.g. ``com.example.target.spec``) and must NOT be pruned when the
    parent directory lacks a build-tool indicator."""
    root = tmp_path / "proj"
    pkg = root / "src" / "main" / "java" / "com" / "example" / "target" / "spec"
    pkg.mkdir(parents=True)
    f = pkg / "Spec.java"
    f.write_text("package com.example.target.spec; class Spec {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    files = list(iter_java_source_files(root, ignore=li))
    assert f in files


def test_unconditional_prune_dirs_remain_pruned_anywhere(tmp_path: Path) -> None:
    """``.git``, ``.idea``, ``.venv``, ``node_modules`` are pruned regardless of
    siblings. They are not legal package names so this stays unconditional."""
    root = tmp_path / "proj"
    root.mkdir()
    for nuisance in (".git", ".idea", ".venv", "node_modules"):
        nuis = root / "src" / "main" / nuisance
        nuis.mkdir(parents=True)
        f = nuis / "X.java"
        f.write_text("class X {}\n", encoding="utf-8")
    li = LayeredIgnore(root, use_gitignore=False)
    files = list(iter_java_source_files(root, ignore=li))
    assert files == []
