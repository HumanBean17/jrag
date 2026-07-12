"""Tests for eval.runner — shopizer-style eval sweep over a tiny fixture corpus.

These tests build a REAL (tiny) index via the operator CLI subprocess and run
``run_search`` under multiple ``RankConfig``s. They assert SHAPE and file
persistence only — never specific ranking numbers (those are research outputs).

Skipped cleanly when the vector stack (torch / lancedb / sentence_transformers)
or the cocoindex CLI is unavailable (graph-only envs).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Skip the whole file when the vector stack is missing — runner needs it.
pytest.importorskip("lancedb")
pytest.importorskip("sentence_transformers")
pytest.importorskip("torch")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY_CORPUS = REPO_ROOT / "tests" / "fixtures" / "cross_service_smoke"

# Expected metric keys (table columns) — order-stable.
METRIC_KEYS = (
    "recall@1",
    "recall@5",
    "recall@10",
    "recall@20",
    "precision@5",
    "mrr",
    "p50_latency_ms",
)


def _cocoindex_available() -> bool:
    """True when the cocoindex CLI sits next to the pytest interpreter."""
    return (Path(sys.executable).parent / "cocoindex").is_file()


pytestmark = pytest.mark.skipif(
    not _cocoindex_available(),
    reason="cocoindex CLI not installed in this venv; runner integration test needs the full stack",
)


def _cfg(
    tmp_path: Path,
    *,
    tier_b_path: str | None = None,
    tag: str = "run",
    max_queries: int | None = None,
):
    from java_codebase_rag.eval.runner import EvalConfig

    # Fresh index dir per tag — `init` refuses an occupied index_dir, and some
    # tests invoke run_eval twice into the same tmp_path.
    kwargs = dict(
        corpus_dir=str(TINY_CORPUS),
        index_dir=str(tmp_path / f"index_{tag}"),
        results_dir=str(tmp_path / f"results_{tag}"),
        tier_b_path=tier_b_path,
        ks=(60,),  # single k keeps the smoke fast; shape is what we assert
        top_k_metrics=(1, 5, 10, 20),
    )
    if max_queries is not None:
        kwargs["max_queries"] = max_queries
    return EvalConfig(**kwargs)


def test_eval_report_shape(tmp_path):
    from java_codebase_rag.eval.runner import run_eval

    report = run_eval(_cfg(tmp_path))

    # 1 baseline + 1 per swept k.
    assert len(report.configs) == 1 + 1

    for entry in report.configs:
        assert entry.config_name
        assert entry.num_queries >= 0
        for key in METRIC_KEYS:
            assert key in entry.metrics, f"missing metric {key} in {entry.config_name}"
            assert isinstance(entry.metrics[key], float)
        assert entry.metrics["p50_latency_ms"] >= 0.0


def test_eval_report_persists_files(tmp_path):
    from java_codebase_rag.eval.runner import run_eval

    cfg = _cfg(tmp_path)
    report = run_eval(cfg)

    # Outputs are namespaced under a timestamped subdir (no flat-path clobbering).
    out_dir = Path(cfg.results_dir) / report.timestamp
    md_path = out_dir / "report.md"
    json_path = out_dir / "report.json"
    assert md_path.is_file(), f"missing report.md at {md_path}"
    assert json_path.is_file(), f"missing report.json at {json_path}"
    # EvalReport also exposes the resolved out_dir.
    assert report.out_dir == str(out_dir)

    md = md_path.read_text()
    # Header row mentions every metric column.
    for key in METRIC_KEYS:
        assert key in md, f"report.md header missing column {key}"
    # One data row per config (count pipe-led rows under the header).
    data_rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "-" not in ln[:3]]
    assert len(data_rows) >= len(report.configs)

    payload = json.loads(json_path.read_text())
    assert "configs" in payload
    assert len(payload["configs"]) == len(report.configs)


def test_eval_tier_b_optional(tmp_path):
    """tier_b_path=None completes on Tier-A only; a one-entry file still works."""
    from java_codebase_rag.eval.runner import run_eval

    # None — no exception.
    report = run_eval(_cfg(tmp_path, tier_b_path=None, tag="a"))
    assert len(report.configs) == 1 + 1

    # With a single Tier-B entry in a temp file.
    tier_b = tmp_path / "tier_b.json"
    tier_b.write_text(
        json.dumps([{"query": "OrderService", "relevant": ["com.example.OrderService"]}])
    )
    report_b = run_eval(_cfg(tmp_path, tier_b_path=str(tier_b), tag="b"))
    assert len(report_b.configs) == 1 + 1
    for entry in report_b.configs:
        for key in METRIC_KEYS:
            assert key in entry.metrics


def test_eval_max_queries_caps_tier_a(tmp_path):
    """EvalConfig.max_queries deterministically caps the Tier-A query set.

    On the tiny fixture the default kind filter yields a handful of type-level
    symbols, so a max_queries=2 cap must keep num_queries_available >= 2 while
    the actually-scored Tier-A count is exactly 2 (no Tier-B here). Also
    confirms the report records the pre-cap availability.
    """
    from java_codebase_rag.eval.runner import run_eval

    report = run_eval(_cfg(tmp_path, max_queries=2, tag="cap"))

    # Pre-cap Tier-A availability is recorded and at least 2 (the fixture has
    # multiple type-level symbols; the cap must have had something to bite on).
    assert report.num_queries_available >= 2
    # With no Tier-B, num_queries == Tier-A kept == max_queries(2).
    assert report.num_queries == 2
    # Every config scored exactly the capped query count.
    for entry in report.configs:
        assert entry.num_queries <= 2


def test_eval_max_queries_validation():
    """max_queries < 1 is rejected with ValueError at construction time."""
    from java_codebase_rag.eval.runner import EvalConfig

    with pytest.raises(ValueError):
        EvalConfig(max_queries=0)
    # Boundary: 1 is accepted.
    EvalConfig(max_queries=1)
