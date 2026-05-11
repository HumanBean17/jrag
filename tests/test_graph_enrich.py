"""Unit tests for `module_for_path` / `microservice_for_path` inference.

These tests construct a synthetic on-disk monorepo to exercise both
single-module and multi-module shapes, plus the override mechanisms
(YAML-only: `.java-codebase-rag.yml`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from graph_enrich import (
    _load_config_microservice_roots,
    microservice_for_path,
    module_for_path,
)


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def monorepo(tmp_path: Path) -> Path:
    """Layout:

        <root>/
          single-svc/
            pom.xml                            <- module + microservice = single-svc
            src/main/java/Foo.java
          multi-svc/
            pom.xml                            <- microservice = multi-svc
            mod-a/
              pom.xml                          <- module = mod-a
              src/main/java/A.java
            mod-b/
              build.gradle                     <- module = mod-b
              src/main/java/B.java
          loose/
            src/main/java/Loose.java           <- no build marker; falls back to "loose"
    """
    root = tmp_path / "monorepo"
    _touch(root / "single-svc/pom.xml")
    _touch(root / "single-svc/src/main/java/Foo.java", "class Foo {}")
    _touch(root / "multi-svc/pom.xml")
    _touch(root / "multi-svc/mod-a/pom.xml")
    _touch(root / "multi-svc/mod-a/src/main/java/A.java", "class A {}")
    _touch(root / "multi-svc/mod-b/build.gradle")
    _touch(root / "multi-svc/mod-b/src/main/java/B.java", "class B {}")
    _touch(root / "loose/src/main/java/Loose.java", "class Loose {}")
    return root


def test_single_module_module_equals_microservice(monorepo: Path) -> None:
    f = monorepo / "single-svc/src/main/java/Foo.java"
    assert module_for_path(str(f), monorepo) == "single-svc"
    assert microservice_for_path(str(f), monorepo) == "single-svc"


def test_multi_module_module_is_innermost(monorepo: Path) -> None:
    f = monorepo / "multi-svc/mod-a/src/main/java/A.java"
    assert module_for_path(str(f), monorepo) == "mod-a"


def test_multi_module_microservice_is_outermost(monorepo: Path) -> None:
    """The bug we set out to fix: chat-core/chat-app should report
    microservice='chat-core', not 'chat-app'."""
    f_a = monorepo / "multi-svc/mod-a/src/main/java/A.java"
    f_b = monorepo / "multi-svc/mod-b/src/main/java/B.java"
    assert microservice_for_path(str(f_a), monorepo) == "multi-svc"
    assert microservice_for_path(str(f_b), monorepo) == "multi-svc"


def test_no_build_marker_falls_back_to_top_level_directory(monorepo: Path) -> None:
    f = monorepo / "loose/src/main/java/Loose.java"
    # No build marker anywhere → module is empty, microservice falls back
    # to the top-level directory under project_root.
    assert module_for_path(str(f), monorepo) == ""
    assert microservice_for_path(str(f), monorepo) == "loose"


def test_yaml_mod_a_override(monorepo: Path) -> None:
    """`microservice_roots:` in `.java-codebase-rag.yml` can promote a module path."""
    _load_config_microservice_roots.cache_clear()
    (monorepo / ".java-codebase-rag.yml").write_text(
        "microservice_roots:\n  - mod-a\n",
        encoding="utf-8",
    )
    f = monorepo / "multi-svc/mod-a/src/main/java/A.java"
    assert microservice_for_path(str(f), monorepo) == "mod-a"
    f_b = monorepo / "multi-svc/mod-b/src/main/java/B.java"
    assert microservice_for_path(str(f_b), monorepo) == "multi-svc"
    _load_config_microservice_roots.cache_clear()


def test_yaml_config_override(monorepo: Path) -> None:
    """`.java-codebase-rag.yml` at project_root must be honoured."""
    _load_config_microservice_roots.cache_clear()
    (monorepo / ".java-codebase-rag.yml").write_text(
        "microservice_roots:\n  - mod-b\n",
        encoding="utf-8",
    )
    f_b = monorepo / "multi-svc/mod-b/src/main/java/B.java"
    assert microservice_for_path(str(f_b), monorepo) == "mod-b"
    # Cleanup the cache so other tests aren't poisoned.
    _load_config_microservice_roots.cache_clear()


def test_microservice_returns_empty_when_no_root_and_no_marker(tmp_path: Path) -> None:
    """When project_root is None and there's no build marker, both
    inference functions should return ""."""
    f = tmp_path / "Standalone.java"
    f.write_text("class Standalone {}", encoding="utf-8")
    assert module_for_path(str(f), None) == ""
    assert microservice_for_path(str(f), None) == ""
