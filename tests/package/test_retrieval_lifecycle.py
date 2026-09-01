"""CLI lifecycle under ``retrieval: bm25`` — init/increment/reprocess must never
spawn the cocoindex vectors phase; the graph phase still runs.

Mirrors the graph-only (stack-absent) skip branch these commands already have,
but driven by the ``retrieval:`` config knob instead of the platform. Each test
writes a ``.java-codebase-rag.yml`` into a temp project root, builds the
command's ``argparse.Namespace`` directly (no argv parsing), stubs the pipeline
helpers at the ``cli`` module seam, and invokes ``work(None)`` straight through
``_run_with_pipeline_progress`` (no progress renderer, no TTY).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from java_codebase_rag import cli as cli_mod
from java_codebase_rag.config import YAML_CONFIG_FILENAMES
from java_codebase_rag.pipeline import RETRIEVAL_BM25_HINT, VECTORS_SKIPPED_BM25


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot os.environ so ``cfg.apply_to_os_environ()`` can't leak between tests."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("JAVA_CODEBASE_RAG_RETRIEVAL", raising=False)


def _write_retrieval_yaml(root: Path, retrieval: str) -> None:
    (root / YAML_CONFIG_FILENAMES[0]).write_text(f"retrieval: {retrieval}\n", encoding="utf-8")


def _stub_completed() -> subprocess.CompletedProcess[str]:
    # args length > 1 so the preflight-blocker detectors never mistake this for a
    # pre-spawn stub (those carry returncode 126/127 with args length <= 1).
    return subprocess.CompletedProcess(
        args=["stub", "cmd"], returncode=0, stdout="", stderr=""
    )


def _stub_failed() -> subprocess.CompletedProcess[str]:
    # A genuine cocoindex failure: non-zero exit with a full command list, so the
    # preflight-blocker detector (returncode 126/127 + args length <= 1) classifies
    # it as a real run that failed — the branch the bm25 hint hangs off.
    return subprocess.CompletedProcess(
        args=["stub", "cmd"], returncode=1, stdout="", stderr="cocoindex boom"
    )


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, int], *, coco: str
) -> None:
    """Stub the pipeline helpers at the ``cli`` module seam.

    ``coco="forbid"`` makes any ``run_cocoindex_update`` call fail the test;
    ``coco="fake"`` counts the call and returns a successful stub (vectors mode);
    ``coco="fail"`` counts the call and returns a non-zero (genuine failure) stub.
    """

    def coco_forbidden(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("run_cocoindex_update must not be called when retrieval is bm25")

    def coco_fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        calls["coco"] += 1
        return _stub_completed()

    def coco_fail(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        calls["coco"] += 1
        return _stub_failed()

    def fake_graph(**_k: object) -> subprocess.CompletedProcess[str]:
        calls["graph"] += 1
        return _stub_completed()

    def fake_incremental_graph(**_k: object) -> subprocess.CompletedProcess[str]:
        calls["incremental_graph"] += 1
        return _stub_completed()

    coco_impl = {"forbid": coco_forbidden, "fake": coco_fake, "fail": coco_fail}[coco]
    monkeypatch.setattr(cli_mod, "run_cocoindex_update", coco_impl)
    monkeypatch.setattr(cli_mod, "run_build_ast_graph", fake_graph)
    monkeypatch.setattr(cli_mod, "run_incremental_graph", fake_incremental_graph)
    # Bypass the progress renderer: run work() directly with no PipelineProgress.
    monkeypatch.setattr(
        cli_mod,
        "_run_with_pipeline_progress",
        lambda subcommand, cfg, *, quiet, verbose=False, work: int(work(None)),
    )


def _ns(root: Path, idx: Path, **overrides: object) -> argparse.Namespace:
    # Attribute shape mirrors the argparse setup for init/increment/reprocess;
    # extra flags (vectors_only/graph_only) are simply unread on the init path.
    base: dict[str, object] = {
        "source_root": str(root),
        "index_dir": str(idx),
        "embedding_model": None,
        "embedding_device": None,
        "quiet": False,
        "verbose": False,
        "vectors_only": False,
        "graph_only": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _calls() -> dict[str, int]:
    return {"coco": 0, "graph": 0, "incremental_graph": 0}


# --- init -------------------------------------------------------------------


def test_init_bm25_skips_vectors_and_builds_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    rc = cli_mod._cmd_init(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 0
    assert calls["graph"] == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert payload["message"] == (
        "init completed (graph-only; vectors skipped — retrieval mode is bm25)"
    )
    assert VECTORS_SKIPPED_BM25 in captured.err


def test_init_vectors_mode_still_calls_cocoindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "vectors")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="fake")

    rc = cli_mod._cmd_init(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 0
    assert calls["coco"] == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"success": True, "message": "init completed"}
    assert VECTORS_SKIPPED_BM25 not in captured.err


# --- increment ---------------------------------------------------------------


def test_increment_bm25_skips_vectors_and_updates_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    rc = cli_mod._cmd_increment(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 0
    assert calls["incremental_graph"] == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "success": True,
        "message": (
            "increment completed (graph only; vectors skipped — retrieval mode is bm25)"
        ),
    }
    assert VECTORS_SKIPPED_BM25 in captured.err


def test_increment_bm25_vectors_only_is_clean_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    rc = cli_mod._cmd_increment(_ns(tmp_path, tmp_path / "idx", vectors_only=True))

    assert rc == 0
    assert calls["graph"] == 0
    assert calls["incremental_graph"] == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "success": True,
        "message": "increment skipped: retrieval mode is bm25 (no vectors phase)",
    }


def test_increment_vectors_only_ladybug_warning_gated_by_retrieval_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """--vectors-only emits the ladybug warning only when vectors actually ran.

    Under bm25 the vectors phase is skipped, so the warning's closing line
    ("Lance vector index has been updated incrementally and is current") would
    be false and would contradict the 'increment skipped' payload that follows.
    """
    _write_retrieval_yaml(tmp_path, "bm25")
    _install_stubs(monkeypatch, _calls(), coco="forbid")

    assert cli_mod._cmd_increment(_ns(tmp_path, tmp_path / "idx", vectors_only=True)) == 0
    err = capsys.readouterr().err
    assert "Lance vector index has been updated incrementally and is current." not in err

    # Control: vectors mode with --vectors-only still emits the warning. The
    # first call published JAVA_CODEBASE_RAG_RETRIEVAL (env overrides YAML), so
    # clear it before re-resolving the config from the rewritten YAML.
    monkeypatch.delenv("JAVA_CODEBASE_RAG_RETRIEVAL", raising=False)
    _write_retrieval_yaml(tmp_path, "vectors")
    _install_stubs(monkeypatch, _calls(), coco="fake")

    assert cli_mod._cmd_increment(_ns(tmp_path, tmp_path / "idx", vectors_only=True)) == 0
    err = capsys.readouterr().err
    assert "Lance vector index has been updated incrementally and is current." in err


# --- reprocess ---------------------------------------------------------------


def test_reprocess_bm25_vectors_only_is_clean_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    rc = cli_mod._cmd_reprocess(_ns(tmp_path, tmp_path / "idx", vectors_only=True))

    assert rc == 0
    assert calls["graph"] == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "success": True,
        "message": "reprocess skipped: retrieval mode is bm25 (no vectors phase)",
    }


def test_reprocess_bm25_full_is_graph_only_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    rc = cli_mod._cmd_reprocess(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 0
    assert calls["graph"] == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "success": True,
        "message": (
            "reprocess completed (graph-only; vectors skipped — retrieval mode is bm25)"
        ),
    }
    assert VECTORS_SKIPPED_BM25 in captured.err


# --- vectors-phase failure → bm25 remediation hint ----------------------------


def test_init_vectors_mode_cocoindex_failure_hints_bm25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A genuine cocoindex failure (non-zero exit, not a preflight blocker) under
    vectors mode exits 1 and prints the bm25 remediation hint on stderr right
    after the failure payload — the operator's offline escape hatch."""
    _write_retrieval_yaml(tmp_path, "vectors")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="fail")

    rc = cli_mod._cmd_init(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 1
    assert calls["coco"] == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is False
    assert payload["message"] == "cocoindex exit 1"
    assert RETRIEVAL_BM25_HINT in captured.err


def test_increment_vectors_mode_cocoindex_failure_hints_bm25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Same remediation hint on the increment cocoindex failure path."""
    _write_retrieval_yaml(tmp_path, "vectors")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="fail")

    rc = cli_mod._cmd_increment(_ns(tmp_path, tmp_path / "idx"))

    assert rc == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] is False
    assert RETRIEVAL_BM25_HINT in captured.err


def test_reprocess_vectors_only_cocoindex_failure_hints_bm25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Same remediation hint on reprocess's vectors-phase failure block (the
    ``--vectors-only`` cocoindex run — the CLI-owned cocoindex failure payload
    in reprocess)."""
    _write_retrieval_yaml(tmp_path, "vectors")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="fail")

    rc = cli_mod._cmd_reprocess(_ns(tmp_path, tmp_path / "idx", vectors_only=True))

    assert rc == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] is False
    assert RETRIEVAL_BM25_HINT in captured.err


def test_bm25_mode_never_emits_bm25_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Control: under bm25 no lifecycle command reaches cocoindex at all, so the
    bm25 remediation hint never appears on any path (telling a bm25 operator to
    switch to bm25 would be noise)."""
    _write_retrieval_yaml(tmp_path, "bm25")
    calls = _calls()
    _install_stubs(monkeypatch, calls, coco="forbid")

    for cmd, ns_overrides in (
        (cli_mod._cmd_init, {}),
        (cli_mod._cmd_increment, {}),
        (cli_mod._cmd_increment, {"vectors_only": True}),
        (cli_mod._cmd_reprocess, {"vectors_only": True}),
        (cli_mod._cmd_reprocess, {}),
    ):
        assert cmd(_ns(tmp_path, tmp_path / "idx", **ns_overrides)) == 0

    captured = capsys.readouterr()
    assert RETRIEVAL_BM25_HINT not in captured.err
