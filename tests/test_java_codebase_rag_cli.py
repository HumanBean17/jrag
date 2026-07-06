from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from java_codebase_rag import cli as cli_mod
from java_codebase_rag.config import (
    YAML_CONFIG_FILENAMES,
    emit_legacy_env_hints_if_present,
    resolve_operator_config,
)


@pytest.fixture(scope="session", autouse=True)
def _install_java_codebase_rag_entrypoint() -> None:
    """Install editable package so ``java-codebase-rag`` exists for subprocess CLI tests.

    Session-scoped: one ``pip install -e`` per pytest run (slow but matches real entrypoints).
    """
    repo_root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo_root)],
        check=True,
        capture_output=True,
        text=True,
    )


def _cocoindex_available() -> bool:
    return (Path(sys.executable).parent / "cocoindex").is_file()


def _base_env(corpus_root: Path, ladybug_db_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    if ladybug_db_path is not None:
        env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    return env


def _java_codebase_rag_exe() -> str:
    venv_exe = Path(sys.executable).parent / "java-codebase-rag"
    if venv_exe.is_file():
        return str(venv_exe)
    exe = shutil.which("java-codebase-rag")
    assert exe is not None, "expected installed java-codebase-rag entrypoint"
    return exe


def _run_cli(args: list[str], *, env: dict[str, str], stdin: str | None = None) -> subprocess.CompletedProcess:
    exe = _java_codebase_rag_exe()
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        check=False,
    )


def test_cli_init_refuses_when_index_paths_non_empty(tmp_path: Path) -> None:
    idx = tmp_path / "idx"
    idx.mkdir()
    (idx / "code_graph.lbug").mkdir()
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(["init", "--source-root", str(tmp_path), "--index-dir", str(idx)], env=env)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload.get("success") is False
    assert "non_empty_paths" in payload or "non_empty" in (payload.get("message") or "").lower()


def test_cli_erase_refuses_non_tty_without_yes(tmp_path: Path) -> None:
    idx = tmp_path / "idx2"
    idx.mkdir()
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [_java_codebase_rag_exe(), "erase", "--source-root", str(tmp_path), "--index-dir", str(idx)],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert proc.returncode == 2
    assert "non-interactive" in proc.stderr.lower() or "--yes" in proc.stderr


def test_cli_erase_succeeds_with_yes_flag(tmp_path: Path) -> None:
    idx = tmp_path / "idx3"
    idx.mkdir()
    (idx / "stub.txt").write_text("x", encoding="utf-8")
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(
        ["erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_erase_removes_graph_file_cocoindex_dir_and_hash_store(tmp_path: Path) -> None:
    """erase must delete code_graph.lbug (file), cocoindex.db (dir), .graph_hashes.json.

    Regression for issue #346: a type-blind delete left both on disk.
    shutil.rmtree is a silent no-op on a regular file (code_graph.lbug), and
    Path.unlink raises IsADirectoryError on cocoindex.db (a directory) — both
    swallowed — and .graph_hashes.json was never targeted. The follow-up init
    then refused because code_graph.lbug survived.
    """
    idx = tmp_path / "erase_artifacts"
    idx.mkdir()
    # Real on-disk layout: graph is a single FILE, cocoindex state is a DIR.
    (idx / "code_graph.lbug").write_bytes(b"fake-kuzu-db")
    (idx / "cocoindex.db").mkdir()
    (idx / "cocoindex.db" / "state.json").write_text("{}", encoding="utf-8")
    (idx / ".graph_hashes.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(
        ["erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not (idx / "code_graph.lbug").exists(), "erase left code_graph.lbug on disk"
    assert not (idx / "cocoindex.db").exists(), "erase left cocoindex.db/ on disk"
    assert not (idx / ".graph_hashes.json").exists(), "erase left .graph_hashes.json on disk"


def test_erase_removes_increment_marker_and_hash_store_tmp(tmp_path: Path) -> None:
    """erase must also clear the rest of the builder's bookkeeping files.

    Regression for issues #349 / #350: erase removed code_graph.lbug,
    cocoindex.db, and .graph_hashes.json but left the incremental crash marker
    (``.graph_increment_in_progress``) and the atomic-write temp
    (``.graph_hashes.json.tmp``) on disk. The marker surviving erase -> init then
    forced the next ``increment`` into a silent full rebuild (explained only under
    ``--verbose``); the ``.tmp`` was pure cruft that defeated erase's "clean slate".
    Both are builder-owned files (``build_ast_graph.BUILDER_OWNED_INDEX_FILES``),
    so erase clears them from the same source of truth instead of hardcoding names.
    """
    idx = tmp_path / "erase_builder_state"
    idx.mkdir()
    (idx / "code_graph.lbug").write_bytes(b"fake-kuzu-db")
    (idx / ".graph_hashes.json").write_text("{}", encoding="utf-8")
    # Simulate a crashed increment (marker left behind) + a crashed hash-store
    # save (atomic-write temp orphaned before the os.replace).
    (idx / ".graph_increment_in_progress").write_text("", encoding="utf-8")
    (idx / ".graph_hashes.json.tmp").write_text("partial", encoding="utf-8")
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(
        ["erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not (idx / ".graph_increment_in_progress").exists(), (
        "erase left .graph_increment_in_progress; next increment would silently full-rebuild (#349)"
    )
    assert not (idx / ".graph_hashes.json.tmp").exists(), (
        "erase left .graph_hashes.json.tmp orphan (#350)"
    )


def test_erase_removes_config_source_pointer(tmp_path: Path) -> None:
    """erase must clear the operator-owned ``config_source`` pointer.

    The pointer is written by init/reprocess/increment/install/update (not the
    graph builder), so erase removes it via ``OPERATOR_OWNED_INDEX_FILES`` rather
    than ``BUILDER_OWNED_INDEX_FILES``. A stale pointer surviving erase would let
    a later discovery run from a sibling cwd relocate a YAML for an index that no
    longer exists — defeating erase's "clean slate".
    """
    idx = tmp_path / "erase_pointer"
    idx.mkdir()
    (idx / "code_graph.lbug").write_bytes(b"fake-kuzu-db")
    (idx / "config_source").write_text(
        str(tmp_path / ".java-codebase-rag.yml") + "\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(
        ["erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not (idx / "config_source").exists(), (
        "erase left config_source pointer; discovery could relocate a stale YAML"
    )


def test_pipeline_progress_writes_config_source_on_success_skips_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_with_pipeline_progress records the pointer on code==0, skips on failure.

    Covers the actual CLI write hook (not just the helper). Uses the non-TTY
    (--quiet) branch so no Live region is needed; the TTY branch calls the same
    ``_maybe_record_config_source`` helper from its ``finally``.
    """
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
    (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text("source_root: .\n", encoding="utf-8")
    cfg = resolve_operator_config(source_root=tmp_path)
    pointer = cfg.index_dir / "config_source"

    # Success -> pointer written with the YAML's absolute path.
    code = cli_mod._run_with_pipeline_progress(
        "init", cfg, quiet=True, verbose=False, work=lambda progress: 0
    )
    assert code == 0
    assert pointer.exists()
    assert Path(pointer.read_text().strip()) == (tmp_path / YAML_CONFIG_FILENAMES[0]).resolve()

    # Failure -> pointer not (re)written; the success pointer is left in place
    # (erase owns removal), but a fresh failure on a clean dir writes nothing.
    pointer.unlink()
    fresh_idx = tmp_path / "fresh"
    fresh_idx.mkdir()
    fresh_cfg = resolve_operator_config(source_root=tmp_path, cli_index_dir=str(fresh_idx))
    fresh_pointer = fresh_cfg.index_dir / "config_source"
    code = cli_mod._run_with_pipeline_progress(
        "init", fresh_cfg, quiet=True, verbose=False, work=lambda progress: 1
    )
    assert code == 1
    assert not fresh_pointer.exists(), "failure must not record a config pointer"


def test_embedding_model_precedence_cli_over_env_over_yaml_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: from-yaml\n",
        encoding="utf-8",
    )
    r = resolve_operator_config(
        source_root=tmp_path,
        cli_embedding_model="from-cli",
    )
    assert r.embedding_model == "from-cli"
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    r2 = resolve_operator_config(source_root=tmp_path, cli_embedding_model=None)
    assert r2.embedding_model == "from-yaml"
    monkeypatch.setenv("SBERT_MODEL", "from-env")
    r3 = resolve_operator_config(source_root=tmp_path, cli_embedding_model=None)
    assert r3.embedding_model == "from-env"
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".java-codebase-rag.yml").unlink(missing_ok=True)
    r4 = resolve_operator_config(source_root=tmp_path, cli_embedding_model=None)
    assert "MiniLM" in r4.embedding_model


def test_embedding_model_yaml_expands_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Windows expanduser uses %USERPROFILE%
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: ~/models/minilm\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert Path(cfg.embedding_model) == tmp_path / "home" / "models" / "minilm"
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_yaml_expands_envvar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("MY_MODEL_DIR", "/abs/models")
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: $MY_MODEL_DIR/minilm\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == "/abs/models/minilm"
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_yaml_hub_id_not_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: BAAI/bge-small-en-v1.5\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == "BAAI/bge-small-en-v1.5"
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_cli_quoted_tilde_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UC10b: quoted CLI argument bypasses shell expansion; helper canonicalises."""
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Windows expanduser uses %USERPROFILE%
    cfg = resolve_operator_config(
        source_root=tmp_path,
        cli_embedding_model="~/cli/x",  # quoted in shell → arrives literal
    )
    assert Path(cfg.embedding_model) == tmp_path / "home" / "cli" / "x"
    assert cfg.embedding_model_source == "cli"


def test_embedding_model_yaml_unresolved_var_keeps_literal_stderr_hint_uc9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: $UNDEFINED_FOO/x\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == "$UNDEFINED_FOO/x"
    assert cfg.embedding_model_source == "yaml"
    err = capsys.readouterr().err
    assert "unresolved variable" in err


def test_embedding_device_precedence_cli_over_env_over_yaml_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  device: cuda\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SBERT_DEVICE", raising=False)
    r = resolve_operator_config(source_root=tmp_path, cli_embedding_device="mps")
    assert r.embedding_device == "mps"
    r2 = resolve_operator_config(source_root=tmp_path, cli_embedding_device=None)
    assert r2.embedding_device == "cuda"
    monkeypatch.setenv("SBERT_DEVICE", "cpu")
    r3 = resolve_operator_config(source_root=tmp_path, cli_embedding_device=None)
    assert r3.embedding_device == "cpu"


def test_hints_enabled_defaults_to_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_HINTS_ENABLED", raising=False)
    r = resolve_operator_config(source_root=tmp_path)
    assert r.hints_enabled is True
    assert r.hints_enabled_source == "default"


def test_hints_enabled_env_over_yaml_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_HINTS_ENABLED", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "hints:\n  enabled: false\n", encoding="utf-8",
    )
    r = resolve_operator_config(source_root=tmp_path)
    assert r.hints_enabled is False
    assert r.hints_enabled_source == "yaml"
    monkeypatch.setenv("JAVA_CODEBASE_RAG_HINTS_ENABLED", "1")
    r2 = resolve_operator_config(source_root=tmp_path)
    assert r2.hints_enabled is True
    assert r2.hints_enabled_source == "env"


def test_yaml_config_ignores_legacy_filename_reads_new_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".lancedb-mcp.yml").write_text("embedding:\n  model: legacy-yaml\n", encoding="utf-8")
    (tmp_path / ".java-codebase-rag.yml").write_text("embedding:\n  model: new-yaml\n", encoding="utf-8")
    r = resolve_operator_config(source_root=tmp_path, cli_embedding_model=None)
    assert r.embedding_model == "new-yaml"


def test_index_dir_defaults_to_dot_java_codebase_rag_under_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    r = resolve_operator_config(source_root=tmp_path, cli_index_dir=None)
    assert r.index_dir == (tmp_path / ".java-codebase-rag").resolve()
    assert r.index_dir_source == "default"


def test_index_dir_precedence_cli_over_env_over_yaml_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text("index_dir: from-yaml\n", encoding="utf-8")
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    for p in (a, b, c):
        p.mkdir()
    r = resolve_operator_config(source_root=tmp_path, cli_index_dir=str(c))
    assert r.index_dir == c.resolve()
    r2 = resolve_operator_config(source_root=tmp_path, cli_index_dir=None)
    assert r2.index_dir == (tmp_path / "from-yaml").resolve()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(b))
    r3 = resolve_operator_config(source_root=tmp_path, cli_index_dir=None)
    assert r3.index_dir == b.resolve()


def test_ladybug_path_derived_as_index_dir_code_graph_kuzu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    r = resolve_operator_config(source_root=tmp_path, cli_index_dir=str(tmp_path / "idx"))
    assert r.ladybug_path == r.index_dir / "code_graph.lbug"


def test_help_output_includes_three_group_labels() -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_mod.main(["--help"])
    assert rc == 0
    out = buf.getvalue()
    assert "Lifecycle" in out
    assert "Introspection" in out
    assert "Analysis" in out


def test_java_codebase_rag_cli_module_importable() -> None:
    import java_codebase_rag.cli  # noqa: PLC0415

    assert callable(java_codebase_rag.cli.main)


def test_refresh_hidden_alias_deprecates_on_stderr(tmp_path: Path) -> None:
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(["refresh", "--help"])
    assert rc == 0
    err = buf.getvalue()
    assert "deprecated" in err.lower()
    assert "reprocess" in err.lower()


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_increment_emits_kuzu_stale_warning_block(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment does NOT emit stale warning by default (new behavior).

    The stale warning is now only emitted with --vectors-only flag.
    This test verifies the new default behavior where graph IS updated.
    """
    idx = tmp_path / "idx_inc"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    init_rc = cli_mod.main(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
    )
    assert init_rc == 0
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        )
    assert rc == 0
    err = buf.getvalue()
    # Should NOT contain old stale warning
    assert "WARNING: AST graph (Kuzu) incremental rebuild is not yet implemented." not in err
    assert "java-codebase-rag reprocess" not in err
    assert cli_mod.LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL not in err


def test_meta_reports_embedding_setting_source(corpus_root: Path, ladybug_db_path: Path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    env["SBERT_MODEL"] = "env-model"
    proc = _run_cli(
        ["meta", "--source-root", str(corpus_root), "--embedding-model", "cli-model"],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload.get("embedding_model") == "cli-model"
    assert payload.get("embedding_model_source") == "cli"


def test_legacy_env_var_set_emits_stderr_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LANCEDB_URI", "http://ignored")
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        emit_legacy_env_hints_if_present()
        emit_legacy_env_hints_if_present()
    err = buf.getvalue()
    assert "LANCEDB_URI" in err
    assert "JAVA_CODEBASE_RAG_INDEX_DIR" in err
    assert err.count("LANCEDB_URI") == 1


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_init_after_erase_succeeds(corpus_root: Path, tmp_path: Path) -> None:
    """Build a real index, erase it, then init again from a clean slate.

    Regression for issue #346: the previous body erased an *empty* index dir and
    then inited, so it never exercised "erase a real graph -> re-init" and stayed
    green while erase silently left code_graph.lbug on disk.
    """
    idx = tmp_path / "lifecycle_idx"
    idx.mkdir(parents=True)
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())
    init1 = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert init1.returncode == 0, init1.stdout + init1.stderr
    assert (idx / "code_graph.lbug").exists(), "init did not build code_graph.lbug"
    e1 = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert e1.returncode == 0, e1.stderr
    assert not (idx / "code_graph.lbug").exists(), "erase left code_graph.lbug on disk"
    init2 = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert init2.returncode == 0, init2.stdout + init2.stderr


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_cli_lifecycle_round_trip_init_increment_meta_erase(
    corpus_root: Path, tmp_path: Path,
) -> None:
    """Test lifecycle round-trip: init -> increment -> meta -> erase.

    This test verifies that increment updates both Lance and graph (new behavior).
    """
    idx = tmp_path / "rt_idx"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())
    e0 = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert e0.returncode == 0, e0.stderr
    init = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert init.returncode == 0, init.stdout + init.stderr
    inc = _run_cli(
        ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert inc.returncode == 0, inc.stdout + inc.stderr
    # Should NOT contain old stale warning (new behavior)
    assert "WARNING: AST graph" not in inc.stderr
    # Should contain new success message
    assert "Lance + graph updated" in inc.stdout
    meta = _run_cli(["meta", "--source-root", str(corpus_root), "--index-dir", str(idx)], env=env)
    assert meta.returncode == 0, meta.stderr
    er = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert er.returncode == 0, er.stderr


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_increment_runs_graph_update(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment updates graph by default (no --vectors-only)."""
    idx = tmp_path / "idx_graph_update"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    init_rc = cli_mod.main(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
    )
    assert init_rc == 0
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        )
    assert rc == 0
    # Should NOT contain stale warning
    err = buf.getvalue()
    assert "WARNING: AST graph" not in err


def test_increment_vectors_only_skips_graph(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment --vectors-only emits stale warning and skips graph update."""
    idx = tmp_path / "idx_vectors_only"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    init_rc = cli_mod.main(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
    )
    assert init_rc == 0
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["increment", "--vectors-only", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        )
    assert rc == 0
    err = buf.getvalue()
    # Should contain stale warning
    assert "WARNING: AST graph (LadybugDB) incremental rebuild is not yet implemented." in err
    assert "java-codebase-rag reprocess" in err
    assert cli_mod.LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL in err


def test_increment_cli_help_mentions_vectors_only(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment --help mentions --vectors-only flag."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_mod.main(["increment", "--help"])
    assert rc == 0
    help_text = buf.getvalue()
    assert "--vectors-only" in help_text
    assert "Run only cocoindex catch-up" in help_text


def test_increment_cli_help_no_longer_says_lance_only(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment --help no longer says 'Lance only'."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_mod.main(["increment", "--help"])
    assert rc == 0
    help_text = buf.getvalue()
    # Should NOT say "Lance only" in help
    assert "Lance only" not in help_text
    # Should say it updates graph
    assert "graph" in help_text.lower()


def test_increment_first_run_falls_back_to_full(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that increment on fresh index (no graph hashes) falls back to full rebuild."""
    idx = tmp_path / "idx_first_run"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    # Run init first
    init_rc = cli_mod.main(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
    )
    assert init_rc == 0
    # Remove hash file to simulate first run after upgrade
    hash_file = idx / ".graph_hashes.json"
    if hash_file.exists():
        hash_file.unlink()
    buf = io.StringIO()
    buf_err = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(buf_err):
            rc = cli_mod.main(
                ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
            )
    assert rc == 0
    err = buf_err.getvalue()
    # Should fall back to full rebuild gracefully
    assert "fell back to full graph rebuild" in err
    # Should still succeed
    assert "increment completed (Lance + graph updated)" in buf.getvalue()



def test_reprocess_graph_only_then_increment_graph_is_noop(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported scenario, exercised through the real CLI wiring.

    ``reprocess --graph-only`` rebuilds the graph and seeds ``.graph_hashes.json``
    (via ``write_ladybug`` -> ``_init_hash_tracker``); the next ``increment``'s
    graph stage must be a no-op, NOT a second full rebuild.

    cocoindex is stubbed so ``increment`` runs only its real graph stage (no
    embedding model needed). ``reprocess --graph-only`` needs no cocoindex
    regardless, so this test runs in the normal (non-heavy) suite.
    """
    idx = tmp_path / "idx_reprocess_then_increment"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    rc = cli_mod.main(
        ["reprocess", "--graph-only", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
    )
    assert rc == 0, "reprocess --graph-only must succeed"
    # reprocess --graph-only must seed the hash store.
    hash_file = idx / ".graph_hashes.json"
    assert hash_file.exists(), "hash store not seeded by reprocess --graph-only"

    # Inject a ghost entry for a file that does not exist — the exact "N removed
    # files every run" symptom. On bank-chat (which has Feign/Kafka clients) the
    # scoped path this triggers reaches _write_clients_producers_and_calls, so a
    # missing-field MemberEntry default here used to crash into a full fallback.
    data = json.loads(hash_file.read_text(encoding="utf-8"))
    data["ghost/DoesNotExist.java"] = "0" * 64
    hash_file.write_text(json.dumps(data), encoding="utf-8")

    # Stub cocoindex so increment exercises ONLY its graph stage.
    def _noop_coco(env, *, full_reprocess, quiet, verbose=True, lance_project_root=None, on_progress=None, on_progress_console=None):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", _noop_coco)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc2 = cli_mod.main(
            ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        )
    assert rc2 == 0
    # The graph stage must NOT have fallen back to a full rebuild.
    assert "fell back to full graph rebuild" not in buf_err.getvalue()
    assert "increment completed (Lance + graph updated)" in buf_out.getvalue()
    # The ghost must be pruned, so the next increment is clean.
    after = json.loads(hash_file.read_text(encoding="utf-8"))
    assert "ghost/DoesNotExist.java" not in after


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_increment_updates_lance_after_touch_java_file(corpus_root: Path, tmp_path: Path) -> None:
    import lancedb  # noqa: PLC0415

    work = tmp_path / "corpus_copy"
    shutil.copytree(corpus_root, work, dirs_exist_ok=False)
    idx = tmp_path / "catchup_idx"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(work.resolve())
    _run_cli(
        ["erase", "--source-root", str(work), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    init = _run_cli(
        ["init", "--source-root", str(work), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert init.returncode == 0, init.stderr
    marker = "package com.bank.chat.assign;\n\nclass CliScenariosTouchMarker { int x; }\n"
    touch_path = (
        work
        / "chat-assign"
        / "src"
        / "main"
        / "java"
        / "com"
        / "bank"
        / "chat"
        / "assign"
        / "CliScenariosTouchMarker.java"
    )
    touch_path.parent.mkdir(parents=True, exist_ok=True)
    touch_path.write_text(marker, encoding="utf-8")
    inc = _run_cli(
        ["increment", "--source-root", str(work), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert inc.returncode == 0, inc.stderr
    db2 = lancedb.connect(str(idx))
    tbl2 = db2.open_table("javacodeindex_java_code")
    texts = tbl2.to_arrow().column("text").to_pylist()
    joined = "\n".join(str(t or "") for t in texts)
    assert "CliScenariosTouchMarker" in joined


def test_cli_meta_outputs_valid_json_when_piped(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    proc = _run_cli(["meta", "--source-root", str(corpus_root)], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "edge_counts" in payload


def test_cli_tables_lists_known_table(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    proc = _run_cli(["tables", "--source-root", str(corpus_root)], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "java" in payload["tables"]
    assert "graph" in payload


def test_cli_unresolved_calls_list_and_stats(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    stats_proc = _run_cli(
        ["unresolved-calls", "stats", "--source-root", str(corpus_root), "--by", "reason"],
        env=env,
    )
    assert stats_proc.returncode == 0, stats_proc.stderr
    stats = json.loads(stats_proc.stdout)
    assert stats.get("success") is True
    assert int(stats.get("total") or 0) >= 1
    assert stats.get("buckets")

    list_proc = _run_cli(
        [
            "unresolved-calls",
            "list",
            "--source-root",
            str(corpus_root),
            "--reason",
            "chained_receiver",
            "--limit",
            "5",
        ],
        env=env,
    )
    assert list_proc.returncode == 0, list_proc.stderr
    listed = json.loads(list_proc.stdout)
    assert listed.get("success") is True
    sites = listed.get("sites") or []
    assert sites
    assert all(str(s.get("id") or "").startswith("ucs:") for s in sites)
    assert all(s.get("reason") == "chained_receiver" for s in sites)

    bad_reason = _run_cli(
        [
            "unresolved-calls",
            "list",
            "--source-root",
            str(corpus_root),
            "--reason",
            "phantom",
        ],
        env=env,
    )
    assert bad_reason.returncode != 0


def test_cli_diagnose_ignore_walked_path(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    path = "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"
    proc = _run_cli(["diagnose-ignore", "--source-root", str(corpus_root), path], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is False


def test_cli_diagnose_ignore_unconditional_prune(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    proc = _run_cli(["diagnose-ignore", "--source-root", str(corpus_root), ".git/foo"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is True


def test_cli_analyze_pr_with_diff_file(corpus_root, ladybug_db_path, tmp_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    diff_path = tmp_path / "sample.diff"
    diff_path.write_text(
        """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -48,5 +48,5 @@
     @Transactional
     public void assign(AssignmentRequest request) {
-        if (request.getConversationId() == null || request.getConversationId().isBlank()) {
+        if (request.getConversationId() == null || request.getConversationId().isBlank() ) {
             throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "conversationId required");
         }
""",
        encoding="utf-8",
    )
    proc = _run_cli(
        ["analyze-pr", "--source-root", str(corpus_root), "--diff-file", str(diff_path)],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "risk_score" in payload
    assert "blast_radius_total" in payload


def test_cli_analyze_pr_with_diff_stdin(corpus_root, ladybug_db_path) -> None:
    env = _base_env(corpus_root, ladybug_db_path)
    diff_text = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -48,5 +48,5 @@
     @Transactional
     public void assign(AssignmentRequest request) {
-        if (request.getConversationId() == null || request.getConversationId().isBlank()) {
+        if (request.getConversationId() == null || request.getConversationId().isBlank() ) {
             throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "conversationId required");
         }
"""
    proc = _run_cli(
        ["analyze-pr", "--source-root", str(corpus_root), "--diff-stdin"],
        env=env,
        stdin=diff_text,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "risk_score" in payload


def test_reprocess_vectors_only_skips_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_vo"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_coco(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["coco", "u", "t", "f"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def graph_should_not_run(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("graph builder must not run for --vectors-only")

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", fake_coco)
    monkeypatch.setattr(cli_mod, "run_build_ast_graph", graph_should_not_run)

    class _NonTty(io.StringIO):
        def isatty(self) -> bool:
            return False

    nout = _NonTty()
    monkeypatch.setattr(cli_mod.sys, "stdout", nout)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--vectors-only"],
    )
    assert rc == 0
    payload = json.loads(nout.getvalue())
    assert payload["phases_run"] == ["vectors"]


def test_reprocess_graph_only_skips_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_go"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def coco_should_not_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("cocoindex must not run for --graph-only")

    def fake_graph(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["py", "build_ast_graph.py"],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", coco_should_not_run)
    monkeypatch.setattr(cli_mod, "run_build_ast_graph", fake_graph)
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--graph-only"],
    )
    assert rc == 0
    assert json.loads(out.getvalue())["phases_run"] == ["graph"]


def test_reprocess_mutually_exclusive_flags_rejected(tmp_path: Path) -> None:
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            [
                "reprocess",
                "--source-root",
                str(tmp_path),
                "--vectors-only",
                "--graph-only",
            ],
        )
    assert rc == 2
    err = buf.getvalue()
    assert "not allowed with argument" in err or "mutually exclusive" in err.lower()


def test_reprocess_graph_only_build_failure_returns_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_gf"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_graph(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["py", "build_ast_graph.py"],
            returncode=9,
            stdout="",
            stderr="boom",
        )

    monkeypatch.setattr(cli_mod, "run_build_ast_graph", fake_graph)
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--graph-only"],
    )
    assert rc == 1
    payload = json.loads(out.getvalue())
    assert payload["phases_run"] == ["graph"]
    assert payload["graph_exit_code"] == 9


def test_reprocess_vectors_only_emits_graph_stale_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_wv"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_coco(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["coco", "u", "t", "f"],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", fake_coco)
    monkeypatch.setattr(
        cli_mod,
        "run_build_ast_graph",
        lambda **_k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    err = io.StringIO()
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--vectors-only"],
    )
    assert rc == 0
    assert "code_graph.lbug" in err.getvalue()


def test_reprocess_graph_only_emits_vectors_stale_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_wg"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_graph(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["py", "build_ast_graph.py"],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(cli_mod, "run_build_ast_graph", fake_graph)
    err = io.StringIO()
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--graph-only"],
    )
    assert rc == 0
    assert "Lance tables under" in err.getvalue()
    assert str(idx) in err.getvalue()


def test_reprocess_vectors_only_setup_failure_returns_exit_2_without_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_vs"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_coco(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["/nonexistent/cocoindex"],
            returncode=127,
            stdout="",
            stderr="cocoindex not found",
        )

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", fake_coco)
    err = io.StringIO()
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--vectors-only"],
    )
    assert rc == 2
    assert json.loads(out.getvalue())["phases_run"] == []
    assert "rebuilt vectors only" not in err.getvalue().lower()


def test_reprocess_graph_only_setup_failure_returns_exit_2_without_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_gs"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_graph(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr="build_ast_graph.py not found",
        )

    monkeypatch.setattr(cli_mod, "run_build_ast_graph", fake_graph)
    err = io.StringIO()
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--graph-only"],
    )
    assert rc == 2
    assert json.loads(out.getvalue())["phases_run"] == []
    assert "rebuilt graph only" not in err.getvalue().lower()


def test_reprocess_no_flag_cocoindex_failure_records_vectors_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server as server_mod

    idx = tmp_path / "idx_nf"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    async def fake_refresh(*, quiet: bool = False, verbose: bool = True, on_progress=None, on_progress_console=None) -> server_mod.RefreshIndexOutput:
        return server_mod.RefreshIndexOutput(
            success=False,
            exit_code=1,
            stdout="out",
            stderr="err",
            message="cocoindex exit 1",
            graph_exit_code=None,
            graph_stdout="",
            graph_stderr="",
            phases_run=["vectors"],
        )

    monkeypatch.setattr(server_mod, "run_refresh_pipeline", fake_refresh)
    out = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stdout", out)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx)],
    )
    assert rc == 1
    payload = json.loads(out.getvalue())
    assert payload["phases_run"] == ["vectors"]


def test_reprocess_pretty_output_lists_rebuilt_and_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx_po"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))

    def fake_coco(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["coco", "u", "t", "f"],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(cli_mod, "run_cocoindex_update", fake_coco)

    class TtyOut(io.StringIO):
        def isatty(self) -> bool:
            return True

    tty = TtyOut()
    monkeypatch.setattr(cli_mod.sys, "stdout", tty)
    rc = cli_mod.main(
        ["reprocess", "--source-root", str(tmp_path), "--index-dir", str(idx), "--vectors-only"],
    )
    assert rc == 0
    text = tty.getvalue()
    assert "Rebuilt: vectors" in text
    assert "Skipped: graph" in text


def test_cli_reprocess_builds_ladybug_path(corpus_root, tmp_path) -> None:
    if not _cocoindex_available():
        pytest.skip("cocoindex CLI missing")
    idx = tmp_path / "rep_idx"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())
    _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    ladybug_path = idx / "code_graph.lbug"
    proc = _run_cli(
        [
            "reprocess",
            "--source-root",
            str(corpus_root),
            "--index-dir",
            str(idx),
            "--quiet",
        ],
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ladybug_path.exists()
    meta_proc = _run_cli(
        ["meta", "--source-root", str(corpus_root), "--index-dir", str(idx)],
        env=env,
    )
    assert meta_proc.returncode == 0, meta_proc.stderr
    payload = json.loads(meta_proc.stdout)
    assert int(payload["counts"].get("types", 0)) > 0


def test_cli_unknown_subcommand_returns_error(tmp_path, monkeypatch) -> None:
    env = _base_env(tmp_path)
    proc = _run_cli(["bogus"], env=env)
    assert proc.returncode == 2


def test_mcp_server_loads_yaml_config_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP server main() loads YAML config and applies to os.environ (issue #238).

    Verifies that main() calls resolve_operator_config with the correct source_root
    and applies the result to os.environ. Uses mocks to avoid loading real models
    or leaking env state (e.g. SBERT_DEVICE=cuda) to subsequent tests.
    """
    import server as server_mod
    from unittest.mock import MagicMock

    fake_cfg = MagicMock()
    fake_cfg.apply_to_os_environ = MagicMock()

    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(server_mod, "resolve_operator_config", MagicMock(return_value=fake_cfg))

    def fake_asyncio_run(awaitable, *, debug=None):
        return None

    monkeypatch.setattr("asyncio.run", fake_asyncio_run)

    server_mod.main()

    # resolve_operator_config should have been called with the project root
    server_mod.resolve_operator_config.assert_called_once_with(source_root=server_mod._project_root())
    # apply_to_os_environ should have been called to set env vars
    fake_cfg.apply_to_os_environ.assert_called_once()


def test_mcp_server_yaml_config_precedence_env_over_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP server passes _project_root() to resolve_operator_config (issue #238).

    Precedence (env > YAML > default) is already tested by
    test_embedding_model_precedence_cli_over_env_over_yaml_over_default.
    This test verifies that main() delegates to resolve_operator_config
    with the correct source root, which handles precedence internally.
    """
    import server as server_mod
    from unittest.mock import MagicMock

    fake_cfg = MagicMock()
    fake_cfg.apply_to_os_environ = MagicMock()

    # Set source root so _project_root() returns it
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(server_mod, "resolve_operator_config", MagicMock(return_value=fake_cfg))

    def fake_asyncio_run(awaitable, *, debug=None):
        return None

    monkeypatch.setattr("asyncio.run", fake_asyncio_run)

    server_mod.main()

    server_mod.resolve_operator_config.assert_called_once()
    assert server_mod.resolve_operator_config.call_args.kwargs["source_root"] == server_mod._project_root()


def test_console_script_main_propagates_rc_via_os_exit_after_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed CLI entry must flush streams and os._exit(rc) rather than
    return into normal interpreter finalization.

    A pyarrow/lance worker thread can outlive CPython finalization in a one-shot
    CLI subprocess and trip ``PyGILState_Release`` (SIGABRT, exit -6). Routing the
    real entry through ``_console_script_main`` skips that racy teardown; ``main()``
    itself stays return-based so in-process test callers keep working.
    """
    import os as _os

    from java_codebase_rag import cli as cli

    class _StubStream:
        def __init__(self) -> None:
            self.flushed = False

        def flush(self) -> None:
            self.flushed = True

    for fake_rc in (0, 2):
        out = _StubStream()
        err = _StubStream()
        snapshot: dict[str, object] = {}

        monkeypatch.setattr(cli, "main", lambda rc=fake_rc: rc)
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)

        def fake_exit(code: int) -> None:
            snapshot["exit_code"] = code
            snapshot["out_flushed_before_exit"] = out.flushed
            snapshot["err_flushed_before_exit"] = err.flushed

        monkeypatch.setattr(_os, "_exit", fake_exit)

        result = cli._console_script_main()

        assert snapshot["exit_code"] == fake_rc, fake_rc
        assert snapshot["out_flushed_before_exit"] is True, fake_rc
        assert snapshot["err_flushed_before_exit"] is True, fake_rc
        assert result is None, fake_rc


def test_console_script_entry_point_routes_through_wrapper() -> None:
    """``[project.scripts]`` must point ``java-codebase-rag`` at
    ``_console_script_main`` (not ``main``) so the deterministic-exit path is the
    one the installed CLI actually uses."""
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'java-codebase-rag = "java_codebase_rag.cli:_console_script_main"' in pyproject
    assert 'java-codebase-rag = "java-codebase-rag:main"' not in pyproject
    assert 'java-codebase-rag = "java_codebase_rag.cli:main"' not in pyproject


# ---------------------------------------------------------------------------
# PR-2: graph-phase progress wiring (default vs --quiet)
# ---------------------------------------------------------------------------


def _make_stub_completed(*, returncode: int = 0, stderr: str = "") -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.CompletedProcess(args=["stub"], returncode=returncode, stdout="", stderr=stderr)


def _patch_pipeline_for_graph_progress(monkeypatch: pytest.MonkeyPatch, *, emit_graph: bool) -> None:
    """Patch the cocoindex + graph pipeline helpers used by init/increment.

    When ``emit_graph`` is True the patched graph helper invokes the caller's
    ``on_progress`` callback with a synthetic ``kind=graph`` event — simulating
    what the real subprocess drain would feed the renderer in default mode.
    """
    from java_codebase_rag import cli as _cli
    from java_codebase_rag import pipeline as _pipeline

    def _fake_cocoindex_update(env, *, full_reprocess, quiet, verbose=True, lance_project_root=None, on_progress=None, on_progress_console=None):
        return _make_stub_completed(returncode=0)

    def _fake_run_build_ast_graph(*, source_root, ladybug_path, verbose, quiet=False, env=None, on_progress=None, on_progress_console=None):
        if emit_graph and on_progress is not None:
            from java_codebase_rag.progress import ProgressEvent

            on_progress(
                ProgressEvent(
                    kind="graph", phase=None, pass_="1/6", done=10, total=130,
                    status="running", elapsed_s=None,
                )
            )
        return _make_stub_completed(returncode=0)

    def _fake_run_incremental_graph(*, source_root, ladybug_path, verbose, quiet=False, env=None, on_progress=None, on_progress_console=None):
        if emit_graph and on_progress is not None:
            from java_codebase_rag.progress import ProgressEvent

            on_progress(
                ProgressEvent(
                    kind="graph", phase=None, pass_="1/6", done=3, total=130,
                    status="running", elapsed_s=None,
                )
            )
        return _make_stub_completed(returncode=0)

    # Patch where cli.py imported them (module-level names in cli).
    monkeypatch.setattr(_cli, "run_cocoindex_update", _fake_cocoindex_update)
    monkeypatch.setattr(_cli, "run_build_ast_graph", _fake_run_build_ast_graph)
    monkeypatch.setattr(_cli, "run_incremental_graph", _fake_run_incremental_graph)
    # Also patch the pipeline module attributes in case anything imports there.
    monkeypatch.setattr(_pipeline, "run_cocoindex_update", _fake_cocoindex_update)
    monkeypatch.setattr(_pipeline, "run_build_ast_graph", _fake_run_build_ast_graph)
    monkeypatch.setattr(_pipeline, "run_incremental_graph", _fake_run_incremental_graph)


def test_cli_init_default_mode_graph_phase_progress_on_stderr(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In default mode a graph-phase progress event is parsed and rendered to
    stderr; the raw ``JCIRAG_PROGRESS`` line is NOT echoed verbatim."""
    idx = tmp_path / "idx_init_prog"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    _patch_pipeline_for_graph_progress(monkeypatch, emit_graph=True)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["init", "--source-root", str(corpus_root), "--index-dir", str(idx)]
        )
    assert rc == 0
    err = buf.getvalue()
    # The raw structured line is consumed by the parser, never raw-relayed.
    assert "JCIRAG_PROGRESS kind=graph" not in err
    # But graph-phase progress IS rendered (non-TTY concise fallback prints a
    # "graph ..." line). The synthetic event had total=130, done=10.
    assert "graph" in err.lower()


def test_cli_increment_graph_phase_progress(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric: increment default mode parses and renders graph progress."""
    idx = tmp_path / "idx_inc_prog"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    # init first (quiet) to populate the index dir so increment has state.
    _patch_pipeline_for_graph_progress(monkeypatch, emit_graph=False)
    init_rc = cli_mod.main(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"]
    )
    assert init_rc == 0
    # Now increment in default mode with graph progress emitted.
    _patch_pipeline_for_graph_progress(monkeypatch, emit_graph=True)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx)]
        )
    assert rc == 0
    err = buf.getvalue()
    assert "JCIRAG_PROGRESS kind=graph" not in err
    assert "graph" in err.lower()


def test_cli_graph_progress_absent_when_quiet(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--quiet suppresses all progress stderr; no graph rendering occurs."""
    idx = tmp_path / "idx_quiet_prog"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    _patch_pipeline_for_graph_progress(monkeypatch, emit_graph=True)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"]
        )
    assert rc == 0
    err = buf.getvalue()
    assert "JCIRAG_PROGRESS kind=graph" not in err
    # In quiet mode there is no header/footer framing either.
    assert "java-codebase-rag init" not in err


# ---------------------------------------------------------------------------
# PR-4 — wire --quiet/--verbose through update / install
# ---------------------------------------------------------------------------


def test_cmd_update_forwards_quiet_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_cmd_update --quiet` forwards quiet=True to run_update.

    Until PR-4 _cmd_update ignored both --quiet and --verbose entirely.
    """
    import java_codebase_rag.installer as _installer

    captured: dict = {}

    def _fake_run_update(*, force=False, dry_run=False, cwd=None,
                         quiet=False, verbose=False, surface=None):
        captured["quiet"] = quiet
        captured["verbose"] = verbose
        captured["force"] = force
        captured["dry_run"] = dry_run
        return 0

    monkeypatch.setattr(_installer, "run_update", _fake_run_update)
    monkeypatch.chdir(tmp_path)

    rc = cli_mod.main(["update", "--quiet"])
    assert rc == 0
    assert captured["quiet"] is True


def test_cmd_update_forwards_verbose_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_cmd_update --verbose` forwards verbose=True to run_update."""
    import java_codebase_rag.installer as _installer

    captured: dict = {}

    def _fake_run_update(*, force=False, dry_run=False, cwd=None,
                         quiet=False, verbose=False, surface=None):
        captured["quiet"] = quiet
        captured["verbose"] = verbose
        return 0

    monkeypatch.setattr(_installer, "run_update", _fake_run_update)
    monkeypatch.chdir(tmp_path)

    rc = cli_mod.main(["update", "--verbose"])
    assert rc == 0
    assert captured["verbose"] is True
    # And the default path (no flag) forwards both as False.
    rc2 = cli_mod.main(["update"])
    assert rc2 == 0
    assert captured["quiet"] is False
    assert captured["verbose"] is False


def test_cmd_install_forwards_verbose_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_cmd_install --verbose` forwards verbose=True to run_install.

    Until PR-4 _cmd_install wired only --quiet through.
    """
    import java_codebase_rag.installer as _installer

    captured: dict = {}

    def _fake_run_install(*, non_interactive, agents, scope, model,
                          source_root=None, quiet=False, verbose=False, surface=None):
        captured["quiet"] = quiet
        captured["verbose"] = verbose
        captured["non_interactive"] = non_interactive
        captured["surface"] = surface
        return 0

    monkeypatch.setattr(_installer, "run_install", _fake_run_install)
    monkeypatch.chdir(tmp_path)

    rc = cli_mod.main(
        ["install", "--non-interactive", "--agent", "claude-code", "--verbose"]
    )
    assert rc == 0
    assert captured["verbose"] is True
    # Omitting --surface forwards None so the interactive select_surface wizard
    # prompts (non-interactive falls back to "cli" inside select_surface). The
    # operator never picking a surface implicitly is the bug-#1 contract.
    assert captured["surface"] is None
    # quiet still flows through too.
    rc2 = cli_mod.main(
        ["install", "--non-interactive", "--agent", "claude-code", "--quiet"]
    )
    assert rc2 == 0
    assert captured["quiet"] is True

