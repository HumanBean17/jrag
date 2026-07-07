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

import pytest


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
    """`jrag status` against a real index reports ontology 18 + non-empty counts."""
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
    assert index["ontology_version"] == 18
    # Counts is a top-level nested dict on the index node (the generic
    # nested-sections dispatch signal - any dict-typed value renders as an
    # indented alphabetical section; edge_summary is NOT used as the dispatch
    # key, it is reserved for real edge data in PR-JRAG-3 inspect).
    counts = index["counts"]
    # Counts is non-empty and has at least one positive counter (the fixture
    # has real Symbols / EXTENDS / INJECTS — see conftest ladybug_db_path).
    assert counts, f"counts dict empty: {payload}"
    assert any(int(v or 0) > 0 for v in counts.values()), f"all counts zero: {counts}"
    # edge_summary is NOT populated by status (reserved for real inspect edge
    # data in PR-JRAG-3).
    assert "edge_summary" not in index


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
#
# The original brief listed `test_offset_is_not_a_global_flag` as
# `jrag callers --offset 5`, but `callers` is not a registered subcommand in
# 1a, so argparse rejects the *subcommand* (invalid choice) before it ever
# sees `--offset`. That test would pass for the WRONG reason and would not
# catch a regression that added `--offset` to the `common` parent parser.
#
# The contract ("offset is not global") is honestly covered by the three
# siblings below plus `test_jrag_help_lists_status_subcommand` (which asserts
# `--offset` is absent from the rendered `--help`).


def test_offset_not_accepted_on_status_subparser() -> None:
    """`jrag status --offset 5` is a usage error: status has no --offset.

    `status` IS a registered subcommand in 1a, so this is the honest test that
    `--offset` is not on the per-command common parser.
    """
    env = os.environ.copy()
    proc = _run_jrag(["status", "--offset", "5"], env=env)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "--offset" in proc.stderr or "unrecognized arguments" in proc.stderr.lower()


def test_offset_not_accepted_before_subcommand() -> None:
    """`jrag --offset 5 status` is a usage error: --offset is not a top-level flag.

    This is the key "not on the parent parser" test. argparse sees the unknown
    ``--offset`` and then treats ``5`` as the subcommand choice, which is
    invalid - either way the command is rejected with a clean message (no
    traceback) and non-zero exit.
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


# ----- Config resolution: env var beats a stray ancestor marker -----


def test_resolve_cfg_honors_source_root_env_over_stray_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JAVA_CODEBASE_RAG_SOURCE_ROOT must win over a stray ``.java-codebase-rag``
    marker in an ancestor of cwd.

    Regression: ``jrag._resolve_cfg`` used to pass ``discover_project_root(cwd)``
    as the explicit ``source_root``, which silently overrode the env var whenever
    any ancestor dir had a non-empty ``.java-codebase-rag/`` index — the
    documented subprocess source-root mechanism ``pipeline.subprocess_env`` sets
    for the cocoindex child (and that the traversal tests rely on). It now passes
    ``source_root=None`` so ``resolve_operator_config`` honors the env var first
    (mirroring ``cli._resolved_from_ns``).
    """
    from java_codebase_rag.jrag import _resolve_cfg

    real_root = tmp_path / "real-source"
    real_root.mkdir()
    # cwd sits inside a dir whose PARENT has a stray non-empty .java-codebase-rag/
    # index dir — the hijack condition that used to override the env var.
    # (_has_index_dir requires the marker dir to be non-empty: any(iterdir()).)
    workdir = tmp_path / "has-marker" / "sub"
    workdir.mkdir(parents=True)
    stray_idx = tmp_path / "has-marker" / ".java-codebase-rag"
    stray_idx.mkdir()
    (stray_idx / "cocoindex.db").write_bytes(b"")  # non-empty → recognized as marker
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(real_root))
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

    class _Args:
        index_dir = None

    cfg = _resolve_cfg(_Args())  # type: ignore[arg-type]
    assert cfg.source_root == real_root.resolve(), (
        "JAVA_CODEBASE_RAG_SOURCE_ROOT must win over a stray ancestor marker"
    )
