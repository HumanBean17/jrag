"""Tests for config discovery and resolution logic."""

from pathlib import Path
from java_codebase_rag.config import (
    discover_project_root,
    YAML_CONFIG_FILENAMES,
    resolve_operator_config,
)


class TestDiscoverProjectRoot:
    """Tests for discover_project_root walk-up behavior."""

    def test_discover_project_root_finds_config_in_cwd(self, tmp_path):
        """Config in cwd returns cwd."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# test config")

        result = discover_project_root(tmp_path)
        assert result == tmp_path

    def test_discover_project_root_walks_up(self, tmp_path):
        """Config in parent returns parent."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# test config")

        result = discover_project_root(subdir)
        assert result == tmp_path

    def test_discover_project_root_stops_at_home_boundary(self, tmp_path, monkeypatch):
        """Config at $HOME itself is found when walking up from subdirectory."""
        # Create a fake home under tmp_path
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        project_dir = fake_home / "project"
        project_dir.mkdir()

        config_file = fake_home / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# test config at home")

        # Mock HOME to point to our fake home
        monkeypatch.setenv("HOME", str(fake_home))

        result = discover_project_root(project_dir)
        assert result == fake_home

    def test_discover_project_root_not_found_above_home(self, tmp_path, monkeypatch):
        """No config anywhere under $HOME returns None."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        project_dir = fake_home / "project"
        project_dir.mkdir()

        monkeypatch.setenv("HOME", str(fake_home))

        result = discover_project_root(project_dir)
        assert result is None

    def test_discover_project_root_not_found(self, tmp_path):
        """No config anywhere returns None."""
        result = discover_project_root(tmp_path)
        assert result is None

    def test_discover_project_root_first_match_wins(self, tmp_path):
        """Configs at two levels - closest to cwd wins."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        subsubdir = subdir / "subsub"
        subsubdir.mkdir()

        # Config at both levels
        parent_config = tmp_path / YAML_CONFIG_FILENAMES[0]
        parent_config.write_text("# parent config")
        child_config = subdir / YAML_CONFIG_FILENAMES[1]  # Use .yaml variant
        child_config.write_text("# child config")

        result = discover_project_root(subsubdir)
        # Should find the closest config (subdir), not the parent (tmp_path)
        assert result == subdir


class TestSourceRootFromYaml:
    """Tests for source_root YAML field parsing and resolution."""

    def test_source_root_from_yaml_relative(self, tmp_path, monkeypatch):
        """source_root: ../ resolves to parent of config dir."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("source_root: ../")

        # Change cwd to tmp_path so walk-up finds this config
        monkeypatch.chdir(tmp_path)

        # source_root=None triggers walk-up discovery + YAML parsing
        result = resolve_operator_config(source_root=None)
        # source_root should be the parent of tmp_path
        assert result.source_root == tmp_path.parent

    def test_source_root_from_yaml_absolute(self, tmp_path, monkeypatch):
        """source_root: /abs/path resolves to absolute path."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        absolute_path = "/some/absolute/path"
        config_file.write_text(f"source_root: {absolute_path}")

        # Change cwd to tmp_path so walk-up finds this config
        monkeypatch.chdir(tmp_path)

        # source_root=None triggers walk-up discovery + YAML parsing
        result = resolve_operator_config(source_root=None)
        assert result.source_root == Path(absolute_path)


class TestSourceRootPrecedence:
    """Tests for source_root precedence chain."""

    def test_source_root_precedence_cli_over_yaml(self, tmp_path, monkeypatch):
        """CLI flag wins over YAML source_root."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("source_root: /yaml/path")

        cli_root = tmp_path / "cli_root"
        cli_root.mkdir()

        result = resolve_operator_config(source_root=cli_root)
        # CLI flag should win
        assert result.source_root == cli_root

    def test_source_root_precedence_yaml_over_discovery(self, tmp_path, monkeypatch):
        """YAML source_root wins over config dir default."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("source_root: /yaml/root")

        # Change cwd to tmp_path so walk-up finds this config
        monkeypatch.chdir(tmp_path)

        # source_root=None triggers walk-up discovery
        result = resolve_operator_config(source_root=None)
        # YAML should override the discovered config dir
        assert result.source_root == Path("/yaml/root")

    def test_source_root_precedence_env_over_yaml(self, tmp_path, monkeypatch):
        """env var wins over YAML source_root."""
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("source_root: /yaml/path")

        env_root = tmp_path / "env_root"
        env_root.mkdir()
        monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(env_root))

        result = resolve_operator_config(source_root=None)
        # Env var should win
        assert result.source_root == env_root

    def test_existing_behavior_unchanged(self, tmp_path, monkeypatch):
        """No walk-up, cwd = config dir → identical behavior to today."""
        # Create a config at cwd
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# test config")

        # Set cwd to tmp_path
        monkeypatch.chdir(tmp_path)

        # Call with source_root=tmp_path (old behavior: explicit root)
        result = resolve_operator_config(source_root=tmp_path)
        assert result.source_root == tmp_path

        # Also test that index_dir derives from source_root
        assert result.index_dir == tmp_path / ".java-codebase-rag"
