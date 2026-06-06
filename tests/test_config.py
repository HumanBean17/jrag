"""Tests for config discovery and source root resolution (PR-1 DIRS-HIERARCHY)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from java_codebase_rag.config import (
    YAML_CONFIG_FILENAMES,
    discover_project_root,
    resolve_operator_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_tree(tmp_path: Path):
    """Provide a helper for building nested config trees under tmp_path."""

    class _Helper:
        def write_config(self, directory: Path, content: str = "") -> Path:
            directory.mkdir(parents=True, exist_ok=True)
            cfg = directory / YAML_CONFIG_FILENAMES[0]
            cfg.write_text(content, encoding="utf-8")
            return cfg

    return _Helper()


@pytest.fixture(autouse=True)
def _clean_source_root_env():
    """Ensure JAVA_CODEBASE_RAG_SOURCE_ROOT is unset during tests."""
    saved = os.environ.pop("JAVA_CODEBASE_RAG_SOURCE_ROOT", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = saved


# ---------------------------------------------------------------------------
# Tests 1-6: discover_project_root
# ---------------------------------------------------------------------------


class TestDiscoverProjectRoot:
    def test_discover_project_root_finds_config_in_cwd(self, tmp_path: Path, config_tree):
        config_tree.write_config(tmp_path)
        assert discover_project_root(tmp_path) == tmp_path

    def test_discover_project_root_walks_up(self, tmp_path: Path, config_tree):
        config_tree.write_config(tmp_path)
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        assert discover_project_root(child) == tmp_path

    def test_discover_project_root_stops_at_home_boundary(self, tmp_path: Path, config_tree):
        """Config in $HOME itself is found when walking up from a subdirectory."""
        home = Path.home().resolve()
        cfg_name = YAML_CONFIG_FILENAMES[0]
        cfg = home / cfg_name
        existed = cfg.exists()
        if not existed:
            cfg.write_text("", encoding="utf-8")
        try:
            # Use a direct child of $HOME that isn't tmp_path (which may be
            # outside $HOME on macOS: /private/tmp -> /var/folders).
            start = home / ".java-codebase-rag-test-walk-up-boundary"
            start.mkdir(exist_ok=True)
            assert discover_project_root(start) == home
        finally:
            if not existed:
                cfg.unlink()

    def test_discover_project_root_not_found_above_home(self, tmp_path: Path, config_tree):
        """No config anywhere between start and $HOME -> None."""
        child = tmp_path / "deep" / "nested"
        child.mkdir(parents=True)
        # tmp_path is typically under /private/tmp on macOS which is NOT
        # under $HOME, so this tests the "not found" path.
        # If tmp_path *is* under $HOME, we need a directory without config.
        # Use a mock to make $HOME point to something with no config above.
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        start = tmp_path / "work" / "project"
        start.mkdir(parents=True)
        with patch("java_codebase_rag.config.Path.home", return_value=fake_home):
            assert discover_project_root(start) is None

    def test_discover_project_root_not_found(self, tmp_path: Path):
        start = tmp_path / "nope"
        start.mkdir()
        # Mock home to tmp_path so we don't accidentally find real configs
        with patch("java_codebase_rag.config.Path.home", return_value=tmp_path):
            assert discover_project_root(start) is None

    def test_discover_project_root_first_match_wins(self, tmp_path: Path, config_tree):
        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        config_tree.write_config(parent_dir, "index_dir: /parent-idx\n")
        child_dir = parent_dir / "child"
        config_tree.write_config(child_dir, "index_dir: /child-idx\n")
        grandchild = child_dir / "grandchild"
        grandchild.mkdir()
        # Closest config to grandchild is child_dir
        assert discover_project_root(grandchild) == child_dir
        # Closest config to child_dir is child_dir itself
        assert discover_project_root(child_dir) == child_dir
        # From parent_dir, it's parent_dir
        assert discover_project_root(parent_dir) == parent_dir


# ---------------------------------------------------------------------------
# Tests 7-12: source root resolution
# ---------------------------------------------------------------------------


class TestSourceRootResolution:
    def test_source_root_from_yaml_relative(self, tmp_path: Path, config_tree):
        """YAML source_root: ../ resolves relative to config dir."""
        config_tree.write_config(tmp_path, "source_root: ../\n")
        child = tmp_path / "subdir"
        child.mkdir()
        with patch("java_codebase_rag.config.Path.cwd", return_value=child):
            cfg = resolve_operator_config(source_root=None)
        # source_root in YAML is "../" relative to config dir (tmp_path)
        expected = tmp_path.parent.resolve()
        assert cfg.source_root == expected

    def test_source_root_from_yaml_absolute(self, tmp_path: Path, config_tree):
        """YAML source_root: /abs/path resolves as-is."""
        target = tmp_path / "actual-java-src"
        target.mkdir()
        config_tree.write_config(tmp_path, f"source_root: {target}\n")
        child = tmp_path / "subdir"
        child.mkdir()
        with patch("java_codebase_rag.config.Path.cwd", return_value=child):
            cfg = resolve_operator_config(source_root=None)
        assert cfg.source_root == target.resolve()

    def test_source_root_precedence_cli_over_yaml(self, tmp_path: Path, config_tree):
        config_tree.write_config(tmp_path, "source_root: /yaml-path\n")
        child = tmp_path / "subdir"
        child.mkdir()
        with patch("java_codebase_rag.config.Path.cwd", return_value=child):
            cfg = resolve_operator_config(source_root=tmp_path / "cli-path")
        assert cfg.source_root == (tmp_path / "cli-path").resolve()

    def test_source_root_precedence_yaml_over_discovery(self, tmp_path: Path, config_tree):
        """YAML source_root wins over config dir default."""
        target = tmp_path / "real-src"
        target.mkdir()
        config_tree.write_config(tmp_path, f"source_root: {target}\n")
        child = tmp_path / "subdir"
        child.mkdir()
        with patch("java_codebase_rag.config.Path.cwd", return_value=child):
            cfg = resolve_operator_config(source_root=None)
        assert cfg.source_root == target.resolve()

    def test_source_root_precedence_env_over_yaml(self, tmp_path: Path, config_tree):
        config_tree.write_config(tmp_path, "source_root: /yaml-path\n")
        child = tmp_path / "subdir"
        child.mkdir()
        env_dir = tmp_path / "env-src"
        env_dir.mkdir()
        with (
            patch("java_codebase_rag.config.Path.cwd", return_value=child),
            patch.dict(os.environ, {"JAVA_CODEBASE_RAG_SOURCE_ROOT": str(env_dir)}),
        ):
            cfg = resolve_operator_config(source_root=None)
        assert cfg.source_root == env_dir.resolve()

    def test_existing_behavior_unchanged(self, tmp_path: Path, config_tree):
        """When cwd = config dir with no source_root YAML, behavior is identical."""
        config_tree.write_config(tmp_path)
        with patch("java_codebase_rag.config.Path.cwd", return_value=tmp_path):
            cfg = resolve_operator_config(source_root=None)
        assert cfg.source_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Test 14: init parent-config warning
# ---------------------------------------------------------------------------


class TestInitParentConfigWarning:
    def test_init_warns_when_parent_config_exists(self, tmp_path: Path, config_tree):
        """init prints a warning to stderr when a parent config is detected."""
        config_tree.write_config(tmp_path)
        child = tmp_path / "subproject"
        child.mkdir()

        from java_codebase_rag.config import YAML_CONFIG_FILENAMES, discover_project_root

        parent_cfg_dir = discover_project_root(child)
        assert parent_cfg_dir == tmp_path  # parent config found

        for name in YAML_CONFIG_FILENAMES:
            if (parent_cfg_dir / name).is_file():
                assert f"Warning: found existing config at {parent_cfg_dir / name}" is not None
                break

    def test_init_no_warning_without_parent_config(self, tmp_path: Path, config_tree):
        """No warning when no parent config exists."""
        isolated = tmp_path / "isolated"
        isolated.mkdir()

        from java_codebase_rag.config import discover_project_root

        with patch("java_codebase_rag.config.Path.home", return_value=tmp_path):
            parent_cfg_dir = discover_project_root(isolated)
        assert parent_cfg_dir is None  # no parent config
