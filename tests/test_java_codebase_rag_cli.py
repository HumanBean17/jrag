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
from java_codebase_rag.config import emit_legacy_env_hints_if_present, resolve_operator_config


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


def _base_env(corpus_root: Path, kuzu_db_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    if kuzu_db_path is not None:
        env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(kuzu_db_path.parent)
    return env


def _run_cli(args: list[str], *, env: dict[str, str], stdin: str | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which("java-codebase-rag")
    assert exe is not None, "expected installed java-codebase-rag entrypoint"
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
    (idx / "code_graph.kuzu").mkdir()
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
        [shutil.which("java-codebase-rag"), "erase", "--source-root", str(tmp_path), "--index-dir", str(idx)],
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
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: ~/models/minilm\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == str(tmp_path / "home" / "models" / "minilm")
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
    cfg = resolve_operator_config(
        source_root=tmp_path,
        cli_embedding_model="~/cli/x",  # quoted in shell → arrives literal
    )
    assert cfg.embedding_model == str(tmp_path / "home" / "cli" / "x")
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


def test_kuzu_path_derived_as_index_dir_code_graph_kuzu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    r = resolve_operator_config(source_root=tmp_path, cli_index_dir=str(tmp_path / "idx"))
    assert r.kuzu_path == r.index_dir / "code_graph.kuzu"


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
    assert "WARNING: AST graph (Kuzu) incremental rebuild is not yet implemented." in err
    assert "java-codebase-rag reprocess" in err
    assert cli_mod.KUZU_INCREMENTAL_TRACKING_ISSUE_URL in err


def test_meta_reports_embedding_setting_source(corpus_root: Path, kuzu_db_path: Path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
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
    idx = tmp_path / "lifecycle_idx"
    idx.mkdir(parents=True)
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())
    e1 = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert e1.returncode == 0, e1.stderr
    init = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert init.returncode == 0, init.stdout + init.stderr


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_cli_lifecycle_round_trip_init_increment_meta_erase(
    corpus_root: Path, tmp_path: Path,
) -> None:
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
    assert "WARNING: AST graph" in inc.stderr
    meta = _run_cli(["meta", "--source-root", str(corpus_root), "--index-dir", str(idx)], env=env)
    assert meta.returncode == 0, meta.stderr
    er = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert er.returncode == 0, er.stderr


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
    marker = "class CliScenariosTouchMarker { int x; }\n"
    touch_path = work / "cli_scenarios_touch_marker.java"
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


def test_cli_meta_outputs_valid_json_when_piped(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
    proc = _run_cli(["meta", "--source-root", str(corpus_root)], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "edge_counts" in payload


def test_cli_tables_lists_known_table(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
    proc = _run_cli(["tables", "--source-root", str(corpus_root)], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "java" in payload["tables"]
    assert "graph" in payload


def test_cli_diagnose_ignore_walked_path(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
    path = "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"
    proc = _run_cli(["diagnose-ignore", "--source-root", str(corpus_root), path], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is False


def test_cli_diagnose_ignore_unconditional_prune(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
    proc = _run_cli(["diagnose-ignore", "--source-root", str(corpus_root), ".git/foo"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is True


def test_cli_analyze_pr_with_diff_file(corpus_root, kuzu_db_path, tmp_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
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


def test_cli_analyze_pr_with_diff_stdin(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root, kuzu_db_path)
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


def test_cli_reprocess_builds_kuzu_path(corpus_root, tmp_path) -> None:
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
    kuzu_path = idx / "code_graph.kuzu"
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
    assert kuzu_path.exists()
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
