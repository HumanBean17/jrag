"""eval --reuse-index — measure the live index without a rebuild (Task 12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.test_runner import TINY_CORPUS, _cocoindex_available, _cfg

# Only the run_eval integration tests need the vector stack; the pure-argparse
# flag-mapping test below runs everywhere (skipping it on stack-less venvs
# would silently drop the only --reuse-index wiring coverage).
_needs_stack = pytest.mark.skipif(
    not _cocoindex_available(),
    reason="cocoindex CLI not installed in this venv; runner integration test needs the full stack",
)


@_needs_stack
def test_reuse_requires_index_dir(tmp_path: Path) -> None:
    from java_codebase_rag.eval.runner import EvalConfig, run_eval

    cfg = EvalConfig(
        corpus_dir=str(TINY_CORPUS),
        index_dir="",
        results_dir=str(tmp_path / "r"),
        reuse_index=True,
    )
    with pytest.raises(ValueError, match="reuse_index requires"):
        run_eval(cfg)


@_needs_stack
def test_reuse_skips_build_and_carries_graph_meta(tmp_path: Path, monkeypatch) -> None:
    from java_codebase_rag.eval import runner

    # Build once (fresh) with the real builder, then guard it for the reuse run.
    first = runner.run_eval(_cfg(tmp_path, tag="fresh"))

    def _must_not_build(**_kwargs):
        raise AssertionError("reuse_index must not rebuild the index")

    monkeypatch.setattr(runner, "_build_index_subprocess", _must_not_build)
    reused_cfg = runner.EvalConfig(
        corpus_dir=first.corpus_dir,
        index_dir=first.index_dir,
        results_dir=str(tmp_path / "results_reuse"),
        tier_b_path=None,
        ks=(60,),
        top_k_metrics=(1, 5, 10, 20),
        reuse_index=True,
    )
    report = runner.run_eval(reused_cfg)
    assert report.graph_meta is not None
    assert "built_at" in report.graph_meta
    # report.json round-trips the meta snapshot.
    persisted = json.loads(
        (Path(report.out_dir) / "report.json").read_text()
    )
    assert persisted["graph_meta"]["built_at"] == report.graph_meta["built_at"]
    # Fresh-build runs carry no snapshot and are marked as non-reuse.
    assert first.graph_meta is None
    assert first.reuse_index is False
    assert report.reuse_index is True


def test_cli_flag_maps_to_config() -> None:
    from java_codebase_rag.eval.runner import _build_eval_config_from_args

    cfg = _build_eval_config_from_args(["corpus", "--reuse-index"])
    assert cfg.reuse_index is True
    cfg = _build_eval_config_from_args(["corpus"])
    assert cfg.reuse_index is False
