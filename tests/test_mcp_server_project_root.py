"""Tests for server.py _project_root() function in the MCP server context."""

from java_codebase_rag.config import YAML_CONFIG_FILENAMES


class TestProjectRoot:
    """Tests for _project_root() walk-up behavior."""

    def test_project_root_uses_discover_when_env_unset(self, tmp_path, monkeypatch):
        """_project_root() returns discovered config dir when JAVA_CODEBASE_RAG_SOURCE_ROOT is unset."""
        # Ensure env var is unset
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        # Create a config file
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# test config")

        # Change cwd to tmp_path
        monkeypatch.chdir(tmp_path)

        # Import _project_root after setting up the environment
        from server import _project_root

        result = _project_root()
        assert result == tmp_path
