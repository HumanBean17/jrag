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

    def test_discover_project_root_finds_nonempty_index_dir(self, tmp_path):
        """Non-empty .java-codebase-rag/ directory acts as project anchor."""
        subdir = tmp_path / "microservice"
        subdir.mkdir()
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00" * 16)

        result = discover_project_root(subdir)
        assert result == tmp_path

    def test_discover_project_root_skips_empty_index_dir(self, tmp_path):
        """Empty .java-codebase-rag/ directory does not anchor the project."""
        subdir = tmp_path / "microservice"
        subdir.mkdir()
        # Empty index dir at subdir level
        empty_idx = subdir / ".java-codebase-rag"
        empty_idx.mkdir()
        # Real index at parent level
        real_idx = tmp_path / ".java-codebase-rag"
        real_idx.mkdir()
        (real_idx / "code_graph.lbug").write_bytes(b"\x00" * 16)

        result = discover_project_root(subdir)
        assert result == tmp_path

    def test_discover_project_root_config_wins_over_index_dir(self, tmp_path):
        """Config file takes priority over index dir at the same level."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        # Index dir at tmp_path level
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00" * 16)
        # Config at subdir level
        config_file = subdir / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# child config")

        deep = subdir / "deep"
        deep.mkdir()
        result = discover_project_root(deep)
        # Config at subdir is closer and wins
        assert result == subdir

    def test_discover_project_root_both_markers_same_level(self, tmp_path):
        """When both config and index dir exist at same dir, both resolve correctly."""
        # Both markers in the same directory
        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        config_file.write_text("# config")
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00" * 16)

        result = discover_project_root(tmp_path)
        assert result == tmp_path


class TestSourceRootFromYaml:
    """Tests for source_root YAML field parsing and resolution."""

    def test_source_root_from_yaml_relative(self, tmp_path, monkeypatch):
        """source_root: ../ resolves to parent of config dir."""
        # Clean environment from conftest.py session fixture
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

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
        # Clean environment from conftest.py session fixture
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_file = tmp_path / YAML_CONFIG_FILENAMES[0]
        absolute_path = "/some/absolute/path"
        config_file.write_text(f"source_root: {absolute_path}")

        # Change cwd to tmp_path so walk-up finds this config
        monkeypatch.chdir(tmp_path)

        # source_root=None triggers walk-up discovery + YAML parsing
        result = resolve_operator_config(source_root=None)
        assert result.source_root == Path(absolute_path)


class TestIndexDirRelativeToConfigDir:
    """YAML ``index_dir`` must resolve against the config file's directory.

    ``source_root`` already resolves against the config dir (see
    ``TestSourceRootFromYaml``). ``index_dir`` must use the SAME base so a
    user can express both keys relative to the config file — otherwise a
    ``../`` in ``index_dir`` gets re-applied on top of the already-resolved
    ``source_root`` and overshoots by one level (the "init indexes ~/"
    symptom when the config lives in a subdirectory of the Java tree).
    """

    def test_yaml_index_dir_double_dot_resolves_against_config_dir(self, tmp_path, monkeypatch):
        """``index_dir: ../x`` is relative to the config file's directory, not source_root."""
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_dir = tmp_path / "my-project-context"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "source_root: ../\nindex_dir: ../.java-codebase-rag\n"
        )
        monkeypatch.chdir(config_dir)

        result = resolve_operator_config(source_root=None)
        # source_root ../  -> tmp_path (one level above the config file)
        assert result.source_root == tmp_path
        # index_dir ../    -> tmp_path/.java-codebase-rag (one level above the config file),
        # NOT tmp_path.parent/.java-codebase-rag (which is what resolving against
        # the already-resolved source_root would produce).
        assert result.index_dir == (tmp_path / ".java-codebase-rag").resolve()

    def test_yaml_index_dir_bare_resolves_against_config_dir(self, tmp_path, monkeypatch):
        """``index_dir: x`` (no ``../``) sits next to the config file."""
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_dir = tmp_path / "my-project-context"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "source_root: ../\nindex_dir: .java-codebase-rag\n"
        )
        monkeypatch.chdir(config_dir)

        result = resolve_operator_config(source_root=None)
        assert result.source_root == tmp_path
        # Bare path resolves against the config dir, so the index sits beside
        # the config file — NOT beside source_root.
        assert result.index_dir == (config_dir / ".java-codebase-rag").resolve()
        assert result.index_dir_source == "yaml"


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
        # Clean environment from conftest.py session fixture
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

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
        # Clean environment from conftest.py session fixture
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

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


def test_cocoindex_subprocess_env_defaults_uses_real_inflight_env_var() -> None:
    """The throttle must use CocoIndex's REAL env var name.

    The earlier #293 "fix" set ``COCOINDEX_SOURCE_MAX_INFLIGHT_ROWS``, an env
    var CocoIndex never reads (it reads ``COCOINDEX_MAX_INFLIGHT_COMPONENTS``,
    default 1024), so it was a no-op and the EMFILE error recurred (#306).
    """
    from java_codebase_rag.config import cocoindex_subprocess_env_defaults

    defaults = cocoindex_subprocess_env_defaults()

    assert defaults["COCOINDEX_MAX_INFLIGHT_COMPONENTS"] == "256"
    # The bogus name from the broken #293 fix must NOT leak back in.
    assert "COCOINDEX_SOURCE_MAX_INFLIGHT_ROWS" not in defaults
