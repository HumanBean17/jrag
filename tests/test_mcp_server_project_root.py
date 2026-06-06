"""Tests for _project_root() walk-up discovery in server.py (PR-1 DIRS-HIERARCHY)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def _clean_source_root_env():
    """Ensure JAVA_CODEBASE_RAG_SOURCE_ROOT is unset during the test."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JAVA_CODEBASE_RAG_SOURCE_ROOT", None)
        yield


@pytest.mark.usefixtures("_clean_source_root_env")
class TestProjectRootDiscovery:
    def test_project_root_uses_discover_when_env_unset(self, tmp_path: Path):
        """_project_root() returns discovered config dir when env var is unset."""
        from java_codebase_rag.config import YAML_CONFIG_FILENAMES

        # Write a config in tmp_path
        cfg = tmp_path / YAML_CONFIG_FILENAMES[0]
        cfg.write_text("", encoding="utf-8")
        child = tmp_path / "subdir"
        child.mkdir()

        import server

        with patch("server.Path.cwd", return_value=child):
            result = server._project_root()
        assert result == tmp_path

    def test_resolve_operator_config_honors_yaml_source_root_from_server_path(
        self, tmp_path: Path
    ):
        """resolve_operator_config(source_root=None) resolves YAML source_root.

        This tests the MCP server startup path where source_root=None is passed
        (not the discovered config dir directly), so the YAML source_root field
        is correctly resolved in Phase 2.
        """
        from java_codebase_rag.config import YAML_CONFIG_FILENAMES, resolve_operator_config

        target = tmp_path / "actual-java-src"
        target.mkdir()
        cfg = tmp_path / YAML_CONFIG_FILENAMES[0]
        cfg.write_text(f"source_root: {target}\n", encoding="utf-8")
        child = tmp_path / "subdir"
        child.mkdir()

        with patch("java_codebase_rag.config.Path.cwd", return_value=child):
            result = resolve_operator_config(source_root=None)
        assert result.source_root == target.resolve()
