"""Tests for `jrag status` and the PR-JRAG-1a CLI shell (PR-JRAG-1a).

Tests:
20. ``test_status_reports_ontology_version_and_counts`` - real index -> exit 0,
    output mentions ontology 17 and counts.
21. ``test_missing_index_returns_actionable_error`` - empty index dir ->
    ``status: error`` envelope mentioning ``java-codebase-rag init``, exit 2
    (NOT a traceback crash).
22. ``test_offset_is_not_a_global_flag`` - ``jrag callers --offset 5`` is a
    usage error (offset is never registered globally; traversal commands don't
    take it in 1a).

Plus a subprocess smoke test for ``jrag --help``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _jrag_exe() -> str:
    """Locate the installed ``jrag`` entry point next to the venv interpreter."""
    candidate = Path(sys.executable).parent / "jrag"
    if candidate.is_file():
        return str(candidate)
    exe = shutil.which("jrag")
    assert exe is not None, "expected installed jrag entrypoint (run: pip install -e .)"
    return exe


def _run_jrag(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_jrag_exe(), *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        check=False,
    )


# ----- Test 20: status reports ontology version + counts -----


def test_status_reports_ontology_version_and_counts(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """`jrag status` against a real index reports ontology 17 + non-empty counts."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    # The entry point runs in a fresh subprocess; no in-process LadybugGraph
    # singleton state leaks across.
    proc = _run_jrag(["status", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"status failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    index = payload["nodes"]["index"]
    assert index["ontology_version"] == 17
    # Counts is nested under edge_summary (the inspect-shape nesting key).
    counts = index["edge_summary"]["counts"]
    # Counts is non-empty and has at least one positive counter (the fixture
    # has real Symbols / EXTENDS / INJECTS — see conftest ladybug_db_path).
    assert counts, f"counts dict empty: {payload}"
    assert any(int(v or 0) > 0 for v in counts.values()), f"all counts zero: {counts}"


# ----- Test 21: missing index -> actionable error envelope -----


def test_missing_index_returns_actionable_error(tmp_path: Path) -> None:
    """Pointing `jrag status` at an empty dir -> status: error envelope, NOT a crash."""
    empty_idx = tmp_path / "does-not-exist"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(empty_idx)
    proc = _run_jrag(["status", "--format", "json"], env=env)

    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}\nstdout={proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    msg = payload.get("message") or ""
    # Actionable message must reference the operator init command.
    assert "java-codebase-rag init" in msg, f"missing init hint: {msg!r}"
    # The hint must include the literal `--source-root` flag form.
    assert "--source-root" in msg
    # No traceback leaked to stdout (would break JSON parse); stderr may carry
    # nothing because we route errors through the envelope, not tracebacks.
    assert "Traceback" not in proc.stdout


def test_missing_index_text_format_emits_actionable_envelope(tmp_path: Path) -> None:
    """Same path, default text format - error envelope must still be parseable."""
    empty_idx = tmp_path / "missing"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(empty_idx)
    proc = _run_jrag(["status"], env=env)
    assert proc.returncode == 2
    assert proc.stdout.startswith("error:")
    assert "java-codebase-rag init" in proc.stdout


# ----- Test 22: --offset is NOT a global flag -----


def test_offset_is_not_a_global_flag() -> None:
    """``jrag callers --offset 5`` is a usage error.

    ``--offset`` is intentionally NOT a global flag (PR-JRAG-1a contract): in
    1a no subparser has it, and in later PRs it is added ONLY to ``find`` /
    ``search`` (PR-JRAG-1b / PR-JRAG-4). ``callers`` is not yet registered, so
    argparse rejects it as an invalid choice; either way ``--offset`` is never
    silently accepted as a global.
    """
    env = os.environ.copy()
    proc = _run_jrag(["callers", "--offset", "5"], env=env)
    assert proc.returncode != 0, f"expected usage error, got rc=0\nstdout={proc.stdout}"
    # No traceback leak: argparse surfaces a clean message via our handler.
    assert "Traceback" not in proc.stderr
    # The rejection message names the offending input.
    assert "callers" in proc.stderr or "--offset" in proc.stderr


def test_offset_not_accepted_on_status_subparser() -> None:
    """`jrag status --offset 5` is a usage error: status has no --offset."""
    env = os.environ.copy()
    proc = _run_jrag(["status", "--offset", "5"], env=env)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "--offset" in proc.stderr or "unrecognized arguments" in proc.stderr.lower()


def test_offset_not_accepted_before_subcommand() -> None:
    """`jrag --offset 5 status` is a usage error: --offset is not a top-level flag.

    argparse sees the unknown ``--offset`` and then treats ``5`` as the
    subcommand choice, which is invalid - either way the command is rejected
    with a clean message (no traceback) and non-zero exit.
    """
    env = os.environ.copy()
    proc = _run_jrag(["--offset", "5", "status"], env=env)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    # Some helpful rejection text appears (specific message varies by parse path).
    assert proc.stderr.strip() != ""


# ----- Smoke: jrag --help -----


def test_jrag_help_lists_status_subcommand() -> None:
    """`jrag --help` exits 0 and lists `status` under subcommands."""
    env = os.environ.copy()
    proc = _run_jrag(["--help"], env=env)
    assert proc.returncode == 0
    assert "status" in proc.stdout
    # The --offset flag must NOT appear in the top-level help.
    assert "--offset" not in proc.stdout
