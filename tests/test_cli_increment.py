"""Tests for CLI increment command Kuzu integration."""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from java_codebase_rag import cli as cli_mod
from java_codebase_rag.config import ResolvedOperatorConfig
from refresh_decision import ChangeSet, RefreshDecision


def _make_cfg(tmp_path: Path) -> ResolvedOperatorConfig:
    return ResolvedOperatorConfig(
        source_root=tmp_path / "src",
        index_dir=tmp_path / "idx",
        kuzu_path=tmp_path / "idx" / "code_graph.kuzu",
        cocoindex_db=tmp_path / "idx" / "cocoindex.db",
        embedding_model="test-model",
        embedding_device=None,
        hints_enabled=False,
        index_dir_source="default",
        embedding_model_source="default",
        embedding_device_source="default",
        hints_enabled_source="default",
    )


def _make_args(**overrides) -> object:
    defaults = {
        "source_root": None,
        "index_dir": None,
        "embedding_model": None,
        "embedding_device": None,
        "quiet": True,
        "verbose": False,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _mock_completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    p.args = ["build_ast_graph.py"]
    return p


def test_increment_dispatches_kuzu_incremental(tmp_path: Path) -> None:
    """When decision engine returns incremental, CLI should call run_build_ast_graph_incremental."""
    cfg = _make_cfg(tmp_path)
    cfg.source_root.mkdir(parents=True, exist_ok=True)
    cfg.index_dir.mkdir(parents=True, exist_ok=True)

    decision = RefreshDecision(
        lance_mode="incremental",
        kuzu_mode="incremental",
        reasons=(),
        detected_changes=ChangeSet(modified=("src/Foo.java",)),
    )

    with (
        patch("java_codebase_rag.cli._resolved_from_ns", return_value=cfg),
        patch("java_codebase_rag.cli._startup_hints"),
        patch("java_codebase_rag.cli.run_cocoindex_update", return_value=_mock_completed_process()),
        patch("refresh_decision.choose_refresh_mode", return_value=decision),
        patch("java_codebase_rag.cli.run_build_ast_graph_incremental", return_value=_mock_completed_process()) as mock_incr,
        patch("java_codebase_rag.cli.run_build_ast_graph", return_value=_mock_completed_process()) as mock_full,
        patch("sys.stdout", new_callable=io.StringIO),
    ):
        args = _make_args()
        code = cli_mod._cmd_increment(args)
        assert code == 0
        mock_incr.assert_called_once()
        mock_full.assert_not_called()


def test_increment_dispatches_kuzu_full_fallback(tmp_path: Path) -> None:
    """When decision engine returns full, CLI should call run_build_ast_graph (full)."""
    cfg = _make_cfg(tmp_path)
    cfg.source_root.mkdir(parents=True, exist_ok=True)
    cfg.index_dir.mkdir(parents=True, exist_ok=True)

    decision = RefreshDecision(
        lance_mode="full",
        kuzu_mode="full",
        reasons=(".deps.json missing or corrupt",),
        detected_changes=ChangeSet(deleted=("src/Foo.java",)),
    )

    with (
        patch("java_codebase_rag.cli._resolved_from_ns", return_value=cfg),
        patch("java_codebase_rag.cli._startup_hints"),
        patch("java_codebase_rag.cli.run_cocoindex_update", return_value=_mock_completed_process()),
        patch("refresh_decision.choose_refresh_mode", return_value=decision),
        patch("java_codebase_rag.cli.run_build_ast_graph_incremental", return_value=_mock_completed_process()) as mock_incr,
        patch("java_codebase_rag.cli.run_build_ast_graph", return_value=_mock_completed_process()) as mock_full,
        patch("sys.stdout", new_callable=io.StringIO),
    ):
        args = _make_args()
        code = cli_mod._cmd_increment(args)
        assert code == 0
        mock_incr.assert_not_called()
        mock_full.assert_called_once()


def test_increment_removes_kuzu_warning(tmp_path: Path) -> None:
    """Verify no Kuzu incremental warning is emitted on stderr."""
    cfg = _make_cfg(tmp_path)
    cfg.source_root.mkdir(parents=True, exist_ok=True)
    cfg.index_dir.mkdir(parents=True, exist_ok=True)

    decision = RefreshDecision(
        lance_mode="incremental",
        kuzu_mode="incremental",
        reasons=(),
        detected_changes=ChangeSet(modified=("src/Foo.java",)),
    )

    with (
        patch("java_codebase_rag.cli._resolved_from_ns", return_value=cfg),
        patch("java_codebase_rag.cli._startup_hints"),
        patch("java_codebase_rag.cli.run_cocoindex_update", return_value=_mock_completed_process()),
        patch("refresh_decision.choose_refresh_mode", return_value=decision),
        patch("java_codebase_rag.cli.run_build_ast_graph_incremental", return_value=_mock_completed_process()),
        patch("sys.stdout", new_callable=io.StringIO),
        patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
    ):
        args = _make_args()
        cli_mod._cmd_increment(args)
        stderr = mock_stderr.getvalue()
        assert "not yet implemented" not in stderr
        assert "graph may be stale" not in stderr
