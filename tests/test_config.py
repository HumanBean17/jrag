"""Tests for config discovery and resolution logic."""

from pathlib import Path
from java_codebase_rag.config import (
    CONFIG_SOURCE_FILENAME,
    YAML_CONFIG_FILENAMES,
    _config_dir_from_pointer,
    _effective_config_dir,
    discover_project_root,
    resolve_operator_config,
    write_config_source_pointer,
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

    def test_discover_project_root_ignores_stray_index_dir_at_home(self, tmp_path, monkeypatch):
        """A bare .java-codebase-rag/ index dir at $HOME must not anchor project
        root (issue #357). Otherwise a command run from any $HOME subdir without
        its own marker silently resolves to $HOME and reads/writes the home-level
        index (cross-project resolution)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        project_dir = fake_home / "project"
        project_dir.mkdir()
        # Stray index dir at $HOME (e.g. an accidental `init` run from home).
        stray_idx = fake_home / ".java-codebase-rag"
        stray_idx.mkdir()
        (stray_idx / "code_graph.lbug").write_bytes(b"\x00" * 16)

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))  # Windows: Path.home() uses %USERPROFILE%

        result = discover_project_root(project_dir)
        assert result is None, "stray ~/.java-codebase-rag/ must not anchor at $HOME (#357)"

    def test_discover_project_root_config_at_home_still_anchors(self, tmp_path, monkeypatch):
        """A config file at $HOME still anchors even with a stray index dir beside
        it — the #357 fix only demotes the bare index-dir signal at $HOME, not the
        config-file anchor (a deliberate ~/.java-codebase-rag.yml is intentional)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        project_dir = fake_home / "project"
        project_dir.mkdir()
        # Stray index dir at $HOME, NON-empty: a real accidental `init` leaves
        # code_graph.lbug behind, so _has_index_dir (which requires non-empty)
        # actually sees it. An empty mkdir() would be invisible to the index-anchor
        # check and would not represent the documented "stray index dir beside it"
        # scenario -- mirrors test_discover_project_root_ignores_stray_index_dir_at_home.
        stray_idx = fake_home / ".java-codebase-rag"
        stray_idx.mkdir()
        (stray_idx / "code_graph.lbug").write_bytes(b"\x00" * 16)
        (fake_home / YAML_CONFIG_FILENAMES[0]).write_text("# home config")

        monkeypatch.setenv("HOME", str(fake_home))

        result = discover_project_root(project_dir)
        assert result == fake_home


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

        # source_root=None triggers walk-up discovery + YAML parsing.
        # .resolve() on both sides normalises drive-relative anchoring:
        # Windows sees "/some/absolute/path" as C:/some/absolute/path.
        result = resolve_operator_config(source_root=None)
        assert Path(result.source_root).resolve() == Path(absolute_path).resolve()


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
        # YAML should override the discovered config dir. .resolve() normalises
        # drive-relative anchoring on Windows ("/yaml/root" -> C:/yaml/root).
        assert Path(result.source_root).resolve() == Path("/yaml/root").resolve()

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


class TestEmbeddingModelRelativePath:
    """``embedding.model`` relative paths resolve against a base directory.

    Mirrors ``index_dir`` (see ``TestIndexDirRelativeToConfigDir``): a relative
    model path in YAML resolves against the config file's directory; a relative
    model path from CLI / env resolves against the resolved ``source_root``.
    This makes a committed ``.java-codebase-rag.yml`` portable — the model loads
    from the same absolute path for the CLI indexer and the MCP reader, instead
    of resolving against an unreliable process CWD.
    """

    def test_yaml_relative_model_resolves_against_config_dir(self, tmp_path, monkeypatch):
        """``embedding.model: ./models/minilm`` (YAML) -> <config_dir>/models/minilm."""
        monkeypatch.delenv("SBERT_MODEL", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_dir = tmp_path / "ctx"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "embedding:\n  model: ./models/minilm\n"
        )
        monkeypatch.chdir(config_dir)

        result = resolve_operator_config(source_root=None)
        assert result.embedding_model == str((config_dir / "models/minilm").resolve())
        assert result.embedding_model_source == "yaml"

    def test_yaml_double_dot_model_resolves_against_config_dir(self, tmp_path, monkeypatch):
        """``embedding.model: ../shared/minilm`` (YAML) -> <config_dir>/../shared/minilm."""
        monkeypatch.delenv("SBERT_MODEL", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_dir = tmp_path / "ctx"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text(
            "embedding:\n  model: ../shared/minilm\n"
        )
        monkeypatch.chdir(config_dir)

        result = resolve_operator_config(source_root=None)
        assert result.embedding_model == str((tmp_path / "shared/minilm").resolve())

    def test_env_relative_model_resolves_against_source_root(self, tmp_path, monkeypatch):
        """``SBERT_MODEL=./models/minilm`` (env) -> <source_root>/models/minilm.

        Config sets ``source_root: ../`` so source_root (tmp_path) differs from
        config_dir (tmp_path/ctx); the env-sourced model must anchor on
        source_root, not config_dir — matching ``index_dir``'s env base.
        """
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

        config_dir = tmp_path / "ctx"
        config_dir.mkdir()
        (config_dir / YAML_CONFIG_FILENAMES[0]).write_text("source_root: ../\n")
        monkeypatch.chdir(config_dir)
        monkeypatch.setenv("SBERT_MODEL", "./models/minilm")

        result = resolve_operator_config(source_root=None)
        assert result.source_root == tmp_path
        assert result.embedding_model == str((tmp_path / "models/minilm").resolve())
        assert result.embedding_model_source == "env"

    def test_cli_relative_model_resolves_against_source_root(self, tmp_path, monkeypatch):
        """``--embedding-model ./models/minilm`` (CLI) -> <source_root>/models/minilm."""
        monkeypatch.delenv("SBERT_MODEL", raising=False)

        result = resolve_operator_config(
            source_root=tmp_path, cli_embedding_model="./models/minilm"
        )
        assert result.embedding_model == str((tmp_path / "models/minilm").resolve())
        assert result.embedding_model_source == "cli"


class TestMaybeExpandEmbeddingModelPath:
    """Unit tests pinning the expansion/resolution helper's contract."""

    def test_no_base_leaves_relative_unchanged(self):
        """Without a base dir, relative paths are NOT resolved.

        ``resolved_sbert_model_for_process_env`` (the MCP runtime read of
        ``SBERT_MODEL``) calls this with no base; it must stay a no-op for
        relative values so MCP behavior is unchanged there. The main resolution
        path supplies a base, so the absolute path it produces is what reaches
        the lazy loader in practice.
        """
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        assert maybe_expand_embedding_model_path("./models/minilm") == "./models/minilm"
        assert maybe_expand_embedding_model_path("../shared/minilm") == "../shared/minilm"

    def test_hub_id_passthrough(self):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        assert maybe_expand_embedding_model_path("org/name") == "org/name"
        assert (
            maybe_expand_embedding_model_path("sentence-transformers/all-MiniLM-L6-v2")
            == "sentence-transformers/all-MiniLM-L6-v2"
        )

    def test_absolute_passthrough(self):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        assert maybe_expand_embedding_model_path("/opt/models/minilm") == "/opt/models/minilm"

    def test_env_var_expansion_preserved(self, monkeypatch):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        monkeypatch.setenv("MODEL_DIR", "/opt/models")
        assert maybe_expand_embedding_model_path("${MODEL_DIR}/minilm") == "/opt/models/minilm"
        assert maybe_expand_embedding_model_path("$MODEL_DIR/minilm") == "/opt/models/minilm"

    def test_tilde_expansion_preserved(self, monkeypatch):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        monkeypatch.setenv("HOME", "/home/user")
        monkeypatch.setenv("USERPROFILE", "/home/user")  # Windows expanduser uses %USERPROFILE%
        assert maybe_expand_embedding_model_path("~/models/minilm") == "/home/user/models/minilm"

    def test_yaml_base_resolves_relative(self, tmp_path):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        out = maybe_expand_embedding_model_path(
            "./models/minilm", config_dir=tmp_path, source="yaml"
        )
        assert out == str((tmp_path / "models/minilm").resolve())

    def test_cli_env_base_is_source_root(self, tmp_path):
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        for src in ("cli", "env"):
            out = maybe_expand_embedding_model_path(
                "./models/minilm", source_root=tmp_path, source=src
            )
            assert out == str((tmp_path / "models/minilm").resolve())

    def test_absolute_after_env_var_not_rebased(self, tmp_path, monkeypatch):
        """An env var that already yields an absolute path is left absolute.

        Guards the ``${HUB_ID}`` edge: only ``./`` / ``../``-prefixed results are
        re-based, so a var holding ``org/name`` or an absolute path is untouched.
        """
        from java_codebase_rag.config import maybe_expand_embedding_model_path

        monkeypatch.setenv("MODEL_DIR", "/opt/models")
        out = maybe_expand_embedding_model_path(
            "${MODEL_DIR}/minilm", config_dir=tmp_path, source="yaml"
        )
        assert out == "/opt/models/minilm"


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


class TestConfigSourcePointer:
    """Index-dir ``config_source`` pointer lets a YAML in a sibling dir be found.

    Walk-up discovery is ancestor-only, so a config in a sibling dir (e.g.
    ``project-context/`` beside the Java tree) is invisible from inside a
    microservice. The index dir remembers its YAML via ``config_source``;
    ``_effective_config_dir`` follows it and rebases ``config_dir`` so YAML-
    relative fields (``index_dir``, ``source_root``, ``embedding.model``)
    resolve against the YAML's home, not the index-dir anchor.
    """

    @staticmethod
    def _sibling_layout(tmp_path: Path) -> dict:
        """The user's monorepo shape: config beside, not inside, the Java tree."""
        root = tmp_path
        ctx = root / "project-context"
        ctx.mkdir()
        (ctx / YAML_CONFIG_FILENAMES[0]).write_text(
            "source_root: ../\nindex_dir: ../.java-codebase-rag\n"
            "microservice_roots: [microservice-a]\n"
        )
        idx = root / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00" * 16)
        micro = root / "microservice-a" / "src" / "main" / "java"
        micro.mkdir(parents=True)
        return {
            "root": root,
            "ctx": ctx,
            "yaml": ctx / YAML_CONFIG_FILENAMES[0],
            "idx": idx,
            "micro": micro,
        }

    # --- _config_dir_from_pointer unit behaviour ---

    def test_pointer_returns_yaml_dir(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00")
        sibling = tmp_path / "ctx"
        sibling.mkdir()
        yaml = sibling / YAML_CONFIG_FILENAMES[0]
        yaml.write_text("source_root: ../\n")
        write_config_source_pointer(index_dir=idx, yaml_config_path=yaml)
        assert _config_dir_from_pointer(tmp_path) == sibling.resolve()

    def test_pointer_missing_returns_none(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00")
        assert _config_dir_from_pointer(tmp_path) is None

    def test_pointer_stale_target_returns_none(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00")
        (idx / CONFIG_SOURCE_FILENAME).write_text(str(tmp_path / "gone.yml") + "\n")
        assert _config_dir_from_pointer(tmp_path) is None

    def test_pointer_wrong_target_name_returns_none(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00")
        wrong = tmp_path / "not-a-config.txt"
        wrong.write_text("nope")
        (idx / CONFIG_SOURCE_FILENAME).write_text(str(wrong) + "\n")
        assert _config_dir_from_pointer(tmp_path) is None

    # --- _effective_config_dir precedence ---

    def test_direct_yaml_wins_over_pointer(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text("source_root: .\n")
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        (idx / "code_graph.lbug").write_bytes(b"\x00")
        sibling = tmp_path / "ctx"
        sibling.mkdir()
        other = sibling / YAML_CONFIG_FILENAMES[0]
        other.write_text("source_root: .\n")
        write_config_source_pointer(index_dir=idx, yaml_config_path=other)
        # Direct YAML at tmp_path wins; the sibling pointer is ignored.
        assert _effective_config_dir(tmp_path) == tmp_path

    def test_effective_dir_falls_back_to_anchor_when_no_yaml_or_pointer(self, tmp_path):
        assert _effective_config_dir(tmp_path) == tmp_path

    # --- end-to-end via resolve_operator_config ---

    def test_resolve_via_pointer_index_dir_is_tree_index(self, tmp_path, monkeypatch):
        """REGRESSION: index_dir resolves to <tree>/.java-codebase-rag, not
        <tree>.parent/.java-codebase-rag. Without the config_dir rebase, the YAML's
        ``index_dir: ../.java-codebase-rag`` would resolve against the index-dir
        anchor and overshoot by one level — a silent wrong-store bug."""
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        lay = self._sibling_layout(tmp_path)
        write_config_source_pointer(index_dir=lay["idx"], yaml_config_path=lay["yaml"])
        monkeypatch.chdir(lay["micro"])
        # jrag path: explicit source_root = the discovered index-dir anchor.
        cfg = resolve_operator_config(source_root=discover_project_root(Path.cwd()))
        assert cfg.source_root == lay["root"].resolve()
        assert cfg.index_dir == lay["idx"].resolve()
        # The exact overshoot the rebase prevents:
        assert cfg.index_dir != (lay["root"].parent / ".java-codebase-rag").resolve()
        assert cfg.yaml_config_path == lay["yaml"].resolve()

    def test_discovery_via_pointer_from_microservice_cwd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        lay = self._sibling_layout(tmp_path)
        write_config_source_pointer(index_dir=lay["idx"], yaml_config_path=lay["yaml"])
        monkeypatch.chdir(lay["micro"])
        cfg = resolve_operator_config(source_root=None)
        assert cfg.source_root == lay["root"].resolve()
        assert cfg.index_dir == lay["idx"].resolve()
        assert cfg.yaml_config_path == lay["yaml"].resolve()

    def test_no_pointer_falls_back_to_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        lay = self._sibling_layout(tmp_path)
        monkeypatch.chdir(lay["micro"])
        cfg = resolve_operator_config(source_root=None)
        assert cfg.yaml_config_path is None
        # Default <source_root>/.java-codebase-rag still lands on the anchor's
        # index by coincidence (the pre-feature behaviour) — no crash.
        assert cfg.index_dir == lay["idx"].resolve()

    def test_round_trip_write_then_resolve(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        lay = self._sibling_layout(tmp_path)
        # init-style resolve from the config dir, then record the pointer.
        monkeypatch.chdir(lay["ctx"])
        cfg = resolve_operator_config(source_root=None)
        write_config_source_pointer(
            index_dir=cfg.index_dir, yaml_config_path=cfg.yaml_config_path
        )
        # Resolving from the microservice cwd lands identically.
        monkeypatch.chdir(lay["micro"])
        cfg2 = resolve_operator_config(source_root=discover_project_root(Path.cwd()))
        assert cfg2.index_dir == cfg.index_dir
        assert cfg2.source_root == cfg.source_root
        assert cfg2.yaml_config_path == cfg.yaml_config_path

    # --- issue #357: a stray pointer at $HOME must not hijack discovery ---

    def test_stray_pointer_at_home_does_not_hijack(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        stray_idx = fake_home / ".java-codebase-rag"
        stray_idx.mkdir()
        (stray_idx / "code_graph.lbug").write_bytes(b"\x00")
        sibling = fake_home / "ctx"
        sibling.mkdir()
        (sibling / YAML_CONFIG_FILENAMES[0]).write_text("source_root: .\n")
        write_config_source_pointer(
            index_dir=stray_idx, yaml_config_path=sibling / YAML_CONFIG_FILENAMES[0]
        )
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / YAML_CONFIG_FILENAMES[0]).write_text("source_root: .\n")
        monkeypatch.chdir(proj)
        # Discovery anchors on the real project, never on the fake $HOME index.
        assert discover_project_root(Path.cwd()) == proj.resolve()
        assert _effective_config_dir(proj.resolve()) == proj.resolve()

    # --- write helper ---

    def test_write_pointer_noop_when_no_yaml(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        idx.mkdir()
        write_config_source_pointer(index_dir=idx, yaml_config_path=None)
        assert not (idx / CONFIG_SOURCE_FILENAME).exists()

    def test_write_pointer_creates_index_dir_writes_absolute_path(self, tmp_path):
        idx = tmp_path / ".java-codebase-rag"
        yaml = tmp_path / YAML_CONFIG_FILENAMES[0]
        yaml.write_text("source_root: .\n")
        write_config_source_pointer(index_dir=idx, yaml_config_path=yaml)
        # index_dir was created; content is an absolute path + newline.
        content = (idx / CONFIG_SOURCE_FILENAME).read_text()
        assert content.endswith("\n")
        written = Path(content.strip())
        assert written.is_absolute()
        assert written == yaml.resolve()
        # Atomic write left no .tmp behind.
        assert not (idx / (CONFIG_SOURCE_FILENAME + ".tmp")).exists()
