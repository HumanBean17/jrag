"""Tests for server.py _project_root() function in the MCP server context."""

from java_codebase_rag.config import (
    YAML_CONFIG_FILENAMES,
    resolve_operator_config,
    write_config_source_pointer,
)


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
        from java_codebase_rag.mcp.server import _project_root

        result = _project_root()
        assert result == tmp_path


class TestSourceRootForOperatorConfig:
    """The MCP server must honor the YAML ``source_root`` field like the CLI.

    ``main()`` passes ``_source_root_for_operator_config()`` (not the
    walk-up-discovered dir) as the ``source_root`` arg to
    ``resolve_operator_config``. When the env override is unset that is
    ``None``, which routes through the walk-up branch that APPLIES the YAML
    ``source_root`` field. Passing the discovered dir instead would route into
    the "explicit source root" branch and silently ignore the YAML field,
    diverging the MCP server from ``init``/``increment``/``reprocess``.
    """

    def test_returns_none_when_env_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        from java_codebase_rag.mcp.server import _source_root_for_operator_config

        assert _source_root_for_operator_config() is None

    def test_returns_env_path_when_set(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit-root"
        explicit.mkdir()
        monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(explicit))
        from java_codebase_rag.mcp.server import _source_root_for_operator_config

        assert _source_root_for_operator_config() == explicit.resolve()

    def test_mcp_and_init_resolve_identically_for_nested_config(self, tmp_path, monkeypatch):
        """Regression for the init-vs-MCP index_dir divergence.

        Config lives in a subdirectory of the Java tree (``my-project-context/``)
        and points both ``source_root`` and ``index_dir`` one level up. The MCP
        server (env unset) and the CLI must resolve the SAME source_root and
        index_dir, landing on the real index at ``tmp_path/.java-codebase-rag``.
        """
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        config_dir = tmp_path / "my-project-context"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "source_root: ../\nindex_dir: ../.java-codebase-rag\n"
        )
        monkeypatch.chdir(config_dir)

        from java_codebase_rag.mcp.server import _source_root_for_operator_config

        mcp = resolve_operator_config(source_root=_source_root_for_operator_config())
        cli = resolve_operator_config(source_root=None)

        assert mcp.source_root == tmp_path
        assert mcp.index_dir == (tmp_path / ".java-codebase-rag").resolve()
        assert mcp.source_root == cli.source_root
        assert mcp.index_dir == cli.index_dir

    def test_mcp_and_init_resolve_identically_via_pointer(self, tmp_path, monkeypatch):
        """MCP/CLI parity when the config is reached via the index-dir pointer.

        Config lives in a SIBLING dir (``my-project-context/``) of the Java tree;
        the agent's cwd is inside a microservice. Walk-up anchors on the index dir
        at ``tmp_path``; the ``config_source`` pointer rebases config_dir to the
        sibling YAML. The MCP server (env unset) and the CLI must still resolve the
        SAME source_root and index_dir — and that index_dir is the real tree index,
        not ``tmp_path.parent/.java-codebase-rag`` (the overshoot the rebase fixes).
        """
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        config_dir = tmp_path / "my-project-context"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "source_root: ../\nindex_dir: ../.java-codebase-rag\n"
        )
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00" * 16)
        write_config_source_pointer(
            index_dir=idx, yaml_config_path=config_dir / YAML_CONFIG_FILENAMES[0]
        )
        microservice = tmp_path / "microservice-a" / "src"
        microservice.mkdir(parents=True)
        monkeypatch.chdir(microservice)

        from java_codebase_rag.mcp.server import _source_root_for_operator_config

        mcp = resolve_operator_config(source_root=_source_root_for_operator_config())
        cli = resolve_operator_config(source_root=None)

        assert mcp.source_root == tmp_path.resolve()
        assert mcp.index_dir == idx.resolve()
        # The rebase prevents overshooting to tmp_path.parent.
        assert mcp.index_dir != (tmp_path.parent / ".java-codebase-rag").resolve()
        assert mcp.source_root == cli.source_root
        assert mcp.index_dir == cli.index_dir
        assert mcp.yaml_config_path == (config_dir / YAML_CONFIG_FILENAMES[0]).resolve()

