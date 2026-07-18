"""PR-3: quiet stderr parity + graph builder pass start / heartbeat + pipeline banner."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from java_codebase_rag import cli as cli_mod

REPO = Path(__file__).resolve().parent.parent.parent
BUILDER = REPO / "src" / "java_codebase_rag" / "graph" / "build_ast_graph.py"
FIXTURE_ROOT = REPO / "tests" / "fixtures" / "call_graph_smoke"
_PASS1_START = "[graph] pass 1 · parsing Java files"


def _cocoindex_available() -> bool:
    return (Path(sys.executable).parent / "cocoindex").is_file()


def _assert_quiet_stderr_no_progress_markers(stderr: str) -> None:
    assert "[vectors]" not in stderr
    assert "] starting ·" not in stderr
    assert "] running …" not in stderr
    assert " · source=" not in stderr, stderr


def test_pass_heartbeat_fires_when_pass_slowed(tmp_path: Path) -> None:
    kuzu = tmp_path / "g.lbug"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_TEST_GRAPH_SLOW_SEC"] = "6"
    proc = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--source-root",
            str(FIXTURE_ROOT),
            "--ladybug-path",
            str(kuzu),
            "--verbose",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    err = proc.stderr
    assert "[graph] pass 1 ·" in err and "elapsed" in err
    hb_pos = err.index("[graph] pass 1 ·")
    summary_pos = err.index("[graph] pass 1 · parsed")
    assert hb_pos < summary_pos


def test_pass_start_before_pass_body(tmp_path: Path) -> None:
    kuzu = tmp_path / "g2.lbug"
    proc = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--source-root",
            str(FIXTURE_ROOT),
            "--ladybug-path",
            str(kuzu),
            "--verbose",
        ],
        cwd=str(REPO),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    err = proc.stderr
    assert err.find(_PASS1_START) < err.find("[graph] pass 1 · parsed")


def test_pipeline_header_footer_present(tmp_path: Path) -> None:
    exe = shutil.which("java-codebase-rag")
    if exe is None:
        pytest.skip("java-codebase-rag entrypoint not on PATH")
    idx = tmp_path / "idx_pf"
    idx.mkdir()
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [exe, "erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    err = proc.stderr
    assert re.search(r"jrag erase · source=.* · index=", err)
    assert re.search(r"jrag erase · finished in [0-9]+\.[0-9]{2}s", err)


def test_cli_quiet_stderr_baseline_per_subcommand(tmp_path: Path, corpus_root: Path) -> None:
    exe = shutil.which("java-codebase-rag")
    if exe is None:
        pytest.skip("java-codebase-rag entrypoint not on PATH")

    idx_erase = tmp_path / "idx_qe"
    idx_erase.mkdir()
    env_erase = os.environ.copy()
    env_erase["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx_erase)
    env_erase["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    r_erase = subprocess.run(
        [exe, "erase", "--source-root", str(tmp_path), "--index-dir", str(idx_erase), "--yes", "--quiet"],
        capture_output=True,
        text=True,
        env=env_erase,
        check=False,
    )
    assert r_erase.returncode == 0, r_erase.stderr + r_erase.stdout
    _assert_quiet_stderr_no_progress_markers(r_erase.stderr)

    if os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip() != "1":
        pytest.skip("cocoindex init/increment/reprocess quiet checks; set JAVA_CODEBASE_RAG_RUN_HEAVY=1")
    if not _cocoindex_available():
        pytest.skip("cocoindex CLI missing — skip init/increment/reprocess quiet checks")

    idx_life = tmp_path / "idx_lf"
    env_life = os.environ.copy()
    env_life["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx_life)
    env_life["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())

    subprocess.run(
        [exe, "erase", "--source-root", str(corpus_root), "--index-dir", str(idx_life), "--yes", "--quiet"],
        capture_output=True,
        text=True,
        env=env_life,
        check=False,
    )
    r_init = subprocess.run(
        [exe, "init", "--source-root", str(corpus_root), "--index-dir", str(idx_life), "--quiet"],
        capture_output=True,
        text=True,
        env=env_life,
        check=False,
    )
    assert r_init.returncode == 0, r_init.stderr + r_init.stdout
    _assert_quiet_stderr_no_progress_markers(r_init.stderr)

    r_inc = subprocess.run(
        [exe, "increment", "--source-root", str(corpus_root), "--index-dir", str(idx_life), "--quiet"],
        capture_output=True,
        text=True,
        env=env_life,
        check=False,
    )
    assert r_inc.returncode == 0, r_inc.stderr + r_inc.stdout
    _assert_quiet_stderr_no_progress_markers(r_inc.stderr)
    assert r_inc.stderr == "\n".join(cli_mod._INCREMENT_WARNING_LINES) + "\n"

    r_rep = subprocess.run(
        [exe, "reprocess", "--source-root", str(corpus_root), "--index-dir", str(idx_life), "--quiet"],
        capture_output=True,
        text=True,
        env=env_life,
        check=False,
    )
    assert r_rep.returncode == 0, r_rep.stderr + r_rep.stdout
    _assert_quiet_stderr_no_progress_markers(r_rep.stderr)
