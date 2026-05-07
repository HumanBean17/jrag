from __future__ import annotations

import errno
import json
import os
import pty
import select
import subprocess
import sys
from pathlib import Path
import shutil

import pytest


@pytest.fixture(scope="session", autouse=True)
def _install_user_rag_entrypoint() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo_root)],
        check=True,
        capture_output=True,
        text=True,
    )


def _base_env(corpus_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["LANCEDB_MCP_PROJECT_ROOT"] = str(corpus_root)
    env["LANCEDB_MCP_GRAPH_ENABLED"] = "1"
    return env


def _run_cli(args: list[str], *, env: dict[str, str], stdin: str | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which("user-rag")
    assert exe is not None, "expected installed user-rag entrypoint"
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        check=False,
    )


def test_cli_meta_outputs_valid_json_when_piped(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
    proc = _run_cli(["meta"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "edge_counts" in payload


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY not available on this platform")
def test_cli_meta_pretty_when_tty(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
    exe = shutil.which("user-rag")
    assert exe is not None, "expected installed user-rag entrypoint"
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [exe, "meta"],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            env=env,
        )
        os.close(slave)
        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            data = b""
            if ready:
                try:
                    data = os.read(master, 4096)
                except OSError as exc:
                    # Some platforms report slave-closed as EIO on master fd.
                    if exc.errno == errno.EIO and proc.poll() is not None:
                        break
                    raise
                if data:
                    chunks.append(data)
            if proc.poll() is not None and (not ready or not data):
                break
        out = b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    assert proc.returncode == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_cli_tables_lists_known_table(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
    proc = _run_cli(["tables"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "java" in payload["tables"]
    assert "graph" in payload


def test_cli_diagnose_ignore_walked_path(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
    path = "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"
    proc = _run_cli(["diagnose-ignore", path], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is False


def test_cli_diagnose_ignore_unconditional_prune(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
    proc = _run_cli(["diagnose-ignore", ".git/foo"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ignored"] is True


def test_cli_analyze_pr_with_diff_file(corpus_root, kuzu_db_path, tmp_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
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
    proc = _run_cli(["analyze-pr", "--diff-file", str(diff_path)], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "risk_score" in payload
    assert "blast_radius_total" in payload


def test_cli_analyze_pr_with_diff_stdin(corpus_root, kuzu_db_path) -> None:
    env = _base_env(corpus_root)
    env["KUZU_DB_PATH"] = str(kuzu_db_path)
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
    proc = _run_cli(["analyze-pr", "--diff-stdin"], env=env, stdin=diff_text)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "risk_score" in payload
    assert "blast_radius_total" in payload


def test_cli_refresh_rebuilds_kuzu_path(corpus_root, tmp_path) -> None:
    env = _base_env(corpus_root)
    kuzu_path = tmp_path / "cli_refresh.kuzu"
    proc = _run_cli(
        [
            "refresh",
            "--source-root",
            str(corpus_root),
            "--kuzu-path",
            str(kuzu_path),
            "--quiet",
        ],
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert kuzu_path.exists()
    meta_proc = _run_cli(["meta", "--kuzu-path", str(kuzu_path)], env=env)
    assert meta_proc.returncode == 0, meta_proc.stderr
    payload = json.loads(meta_proc.stdout)
    assert int(payload["counts"].get("types", 0)) > 0


def test_cli_unknown_subcommand_exits_2(corpus_root) -> None:
    env = _base_env(corpus_root)
    proc = _run_cli(["bogus"], env=env)
    assert proc.returncode == 2
    assert "unknown" in proc.stderr.lower() or "invalid" in proc.stderr.lower()
