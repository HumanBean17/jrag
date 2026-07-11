"""Eval runner — index a corpus, sweep RankConfigs, emit recall/precision/MRR.

This is the integration layer of the eval harness (Task 7 of the hybrid-BM25
RRF plan). Unlike ``eval/metrics.py`` and ``eval/ground_truth.py`` (pure
stdlib), the runner MAY import the full vector stack (torch / lancedb /
sentence_transformers) and invokes the operator CLI to build a real index.

Pipeline (``run_eval``):

1. Build a fresh index into ``cfg.index_dir`` via the operator CLI
   (``java-codebase-rag init``) as a **subprocess** — the stable operator
   surface. Reaching into cocoindex/pipeline internals is fragile (process env,
   progress renderers), so the subprocess wins on robustness. A non-zero exit
   surfaces stdout/stderr in the raised ``RuntimeError``.
2. Open the index for query: set ``JAVA_CODEBASE_RAG_INDEX_DIR`` so
   ``resolve_ladybug_path`` + ``run_search``'s URI resolve to our temp index;
   load ``SentenceTransformer`` once and reuse.
3. Enumerate ``Symbol`` nodes from the LadybugDB graph (mirrors
   ``search_lexical``) and build Tier-A ground truth; optionally concat Tier-B.
4. For each ``RankConfig`` (``BASELINE_2LIST_CONFIG`` + a 3-list config per
   swept ``k``), run ``run_search`` per query, map rows to ``primary_type_fqn``
   and compute per-query recall@k / precision@k / MRR via ``eval.metrics``.
5. Aggregate, persist Markdown + JSON under
   ``<results_dir>/<ISO-timestamp>/report.{md,json}`` (timestamped subdir so
   successive sweeps don't clobber each other).

Granularity note (metric mapping)
---------------------------------
Tier-A ``build_tier_a`` sets ``relevant = {symbol.fqn}`` where the fqn may be a
MEMBER fqn (``com.x.A#processClientMessage()``). ``run_search`` rows carry
``primary_type_fqn`` = the enclosing TYPE fqn (``com.x.A``, no ``#``). To make
both sides type-level, the runner normalizes member→type via
``search_lexical._enclosing_type_fqn`` BEFORE scoring. This keeps Task 6's
``build_tier_a`` untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from java_codebase_rag.eval.ground_truth import LabeledQuery, build_tier_a, load_tier_b
from java_codebase_rag.eval import metrics as M
from java_codebase_rag.graph.ladybug_queries import LadybugGraph, resolve_ladybug_path
from java_codebase_rag.search.index_common import SBERT_MODEL
from java_codebase_rag.search.search_lexical import _enclosing_type_fqn, _SYMBOL_RETURN
from java_codebase_rag.search.search_lancedb import run_search
from java_codebase_rag.search.search_scoring import (
    BASELINE_2LIST_CONFIG,
    DEFAULT_RANK_CONFIG,
    RankConfig,
)

# Markdown table columns — order-stable, mirrored by the test suite.
METRIC_COLUMNS: tuple[str, ...] = (
    "recall@1",
    "recall@5",
    "recall@10",
    "recall@20",
    "precision@5",
    "mrr",
    "p50_latency_ms",
)


@dataclass(frozen=True)
class EvalConfig:
    """Configuration for a single eval run.

    ``index_dir`` may be empty — the runner creates a temp dir in that case
    (and writes it back into the returned ``EvalReport``).
    """

    corpus_dir: str = field(
        default_factory=lambda: str(Path.home() / "jrag-bench" / "shopizer")
    )
    index_dir: str = ""
    results_dir: str = field(
        default_factory=lambda: str(Path.home() / "jrag-bench" / "shopizer" / "results")
    )
    tier_b_path: str | None = None
    ks: tuple[int, ...] = (30, 60, 90, 120)
    top_k_metrics: tuple[int, ...] = (1, 5, 10, 20)
    model_name: str = SBERT_MODEL
    device: str | None = field(
        default_factory=lambda: os.environ.get("SBERT_DEVICE") or None
    )


@dataclass(frozen=True)
class ConfigMetrics:
    """Aggregated metrics for one RankConfig under test."""

    config_name: str
    metrics: dict[str, float]
    num_queries: int
    rrf_k: int
    lists: tuple[str, ...]


@dataclass(frozen=True)
class EvalReport:
    """Result of a full eval sweep."""

    configs: list[ConfigMetrics]
    timestamp: str
    num_queries: int
    corpus_dir: str
    index_dir: str
    # Absolute path to the timestamped output dir holding report.md / report.json.
    out_dir: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Index build (subprocess over the operator CLI — the stable surface)
# ---------------------------------------------------------------------------


def _build_index_subprocess(*, corpus_dir: str, index_dir: str) -> None:
    """Build a fresh index via ``java-codebase-rag init``.

    Raises FileNotFoundError if the corpus is missing, RuntimeError on a
    non-zero CLI exit (surfacing clipped stdout/stderr).
    """
    if not Path(corpus_dir).is_dir():
        raise FileNotFoundError(
            f"eval corpus_dir does not exist: {corpus_dir} "
            "(point EvalConfig.corpus_dir at a checked-out Java repo)"
        )

    Path(index_dir).mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(Path(index_dir).resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(Path(corpus_dir).resolve()),
    }
    cmd = [
        sys.executable,
        "-m",
        "java_codebase_rag.cli",
        "init",
        "--source-root",
        str(Path(corpus_dir).resolve()),
        "--index-dir",
        str(Path(index_dir).resolve()),
        "--quiet",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=int(os.environ.get("JAVA_CODEBASE_RAG_EVAL_INDEX_TIMEOUT", "1800")),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"java-codebase-rag init exited {proc.returncode} for corpus {corpus_dir}.\n"
            f"--- stdout (clipped 8000) ---\n{proc.stdout[-8000:]}\n"
            f"--- stderr (clipped 8000) ---\n{proc.stderr[-8000:]}"
        )


# ---------------------------------------------------------------------------
# Symbol enumeration (mirror search_lexical)
# ---------------------------------------------------------------------------


class _SymbolRow:
    """Attribute-view over a Cypher row dict (build_tier_a ducks on .fqn/.name)."""

    __slots__ = ("fqn", "name", "kind")

    def __init__(self, row: dict[str, Any]) -> None:
        self.fqn = str(row.get("fqn") or "")
        self.name = str(row.get("name") or "")
        self.kind = str(row.get("kind") or "")


def _enumerate_symbols(graph: LadybugGraph) -> list[_SymbolRow]:
    """Return Symbol rows as duck-typed objects (.fqn / .name) for build_tier_a.

    ``LadybugGraph._rows`` returns dicts; ``build_tier_a``'s ``SymbolLike``
    protocol ducks on attributes, so we wrap each row.
    """
    rows = graph._rows(f"MATCH (s:Symbol) RETURN {_SYMBOL_RETURN}")  # noqa: SLF001
    return [_SymbolRow(r) for r in rows]


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _retrieved_fqns(rows: list[dict]) -> list[str]:
    """Map search rows to ordered, deduped type-level FQNs."""
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        fqn = r.get("primary_type_fqn") or r.get("fqn") or ""
        if not fqn:
            continue
        if fqn in seen:
            continue
        seen.add(fqn)
        out.append(fqn)
    return out


def _relevant_type_fqns(labeled: LabeledQuery) -> set[str]:
    """Normalize member FQNs to enclosing-type FQNs so both sides are type-level."""
    return {_enclosing_type_fqn(fqn) for fqn in labeled.relevant if fqn}


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


def _eval_one_config(
    *,
    config_name: str,
    rank_config: RankConfig,
    queries: list[LabeledQuery],
    uri: str,
    ladybug_path: str,
    model,
    model_name: str,
    device: str | None,
    top_k_metrics: tuple[int, ...],
    limit: int,
) -> ConfigMetrics:
    """Run one RankConfig over all queries; return aggregated ConfigMetrics."""
    per_query: list[dict[str, float]] = []
    latencies_ms: list[float] = []

    for q in queries:
        t0 = time.perf_counter()
        rows = run_search(
            q.query,
            uri=uri,
            table_keys=["java"],
            limit=limit,
            offset=0,
            path_substring=None,
            model_name=model_name,
            device=device,
            model=model,
            rank_config=rank_config,
            graph_expand=True,
            expand_depth=1,
            ladybug_path=ladybug_path,
            dedup_by_fqn=True,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        retrieved = _retrieved_fqns(rows)
        relevant = _relevant_type_fqns(q)
        if not relevant:
            # No relevant set (e.g. Tier-B with empty relevant) — skip from scoring.
            continue

        qm: dict[str, float] = {}
        for k in top_k_metrics:
            qm[f"recall@{k}"] = M.recall_at_k(retrieved, relevant, k)
        qm["precision@5"] = M.precision_at_k(retrieved, relevant, 5)
        qm["mrr"] = M.reciprocal_rank(retrieved, relevant)
        per_query.append(qm)

    agg = M.aggregate(per_query)
    metrics: dict[str, float] = {}
    for k in top_k_metrics:
        metrics[f"recall@{k}"] = float(agg.get(f"recall@{k}", 0.0))
    metrics["precision@5"] = float(agg.get("precision@5", 0.0))
    metrics["mrr"] = float(agg.get("mrr", 0.0))
    metrics["p50_latency_ms"] = _p50(latencies_ms)

    return ConfigMetrics(
        config_name=config_name,
        metrics=metrics,
        num_queries=len(per_query),
        rrf_k=rank_config.rrf_k,
        lists=tuple(sorted(rank_config.lists)),
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_markdown(report: EvalReport) -> str:
    lines: list[str] = []
    lines.append(f"# Eval Report — {report.timestamp}")
    lines.append("")
    lines.append(
        f"Corpus: `{report.corpus_dir}`  |  Index: `{report.index_dir}`  "
        f"|  Queries scored: {report.num_queries}"
    )
    lines.append("")
    header = "| config | " + " | ".join(METRIC_COLUMNS) + " |"
    sep = "| --- " * (len(METRIC_COLUMNS) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for entry in report.configs:
        cells = [entry.config_name]
        for col in METRIC_COLUMNS:
            v = entry.metrics.get(col, 0.0)
            if col == "p50_latency_ms":
                cells.append(f"{v:.1f}")
            else:
                cells.append(f"{v:.4f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_eval(cfg: EvalConfig) -> EvalReport:
    """Build a fresh index, sweep RankConfigs, return an EvalReport.

    See module docstring for the pipeline and the metric-granularity note.
    """
    # Late import — torch/lancedb only needed for the run, not for module import.
    from sentence_transformers import SentenceTransformer

    # Resolve index_dir (temp dir if blank).
    if not cfg.index_dir:
        index_dir = tempfile.mkdtemp(prefix="jrag-eval-")
    else:
        index_dir = cfg.index_dir
    Path(index_dir).mkdir(parents=True, exist_ok=True)

    # 1. Build the index (subprocess).
    _build_index_subprocess(corpus_dir=cfg.corpus_dir, index_dir=index_dir)

    # 2. Wire the process env so resolve_ladybug_path + run_search's URI hit our index.
    os.environ["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(Path(index_dir).resolve())
    os.environ.setdefault(
        "JAVA_CODEBASE_RAG_SOURCE_ROOT", str(Path(cfg.corpus_dir).resolve())
    )
    uri = str(Path(index_dir).resolve())
    ladybug_path = resolve_ladybug_path(None)

    # Load the model ONCE — pass into every run_search call.
    model = SentenceTransformer(
        cfg.model_name, device=cfg.device, trust_remote_code=True
    )

    # 3. Open graph + build ground truth.
    # Reset the LadybugGraph singleton — a prior test/process may have cached
    # a different path. We force-bind to our index's graph.
    LadybugGraph.reset_for_path(None)
    graph = LadybugGraph.get(ladybug_path)

    symbols = _enumerate_symbols(graph)
    queries = list(build_tier_a(symbols))
    if cfg.tier_b_path:
        queries.extend(load_tier_b(cfg.tier_b_path))

    # 4. Enumerate configs: BASELINE_2LIST_CONFIG (k=60) + 3-list at each swept k.
    limit = max(cfg.top_k_metrics)
    configs: list[tuple[str, RankConfig]] = [
        ("baseline_2list_k60", BASELINE_2LIST_CONFIG),
    ]
    for k in cfg.ks:
        configs.append(
            (
                f"hybrid_3list_k{k}",
                RankConfig(
                    lists=frozenset({"vector", "graph", "bm25"}),
                    rrf_k=k,
                ),
            )
        )

    # 5. Run + aggregate.
    results: list[ConfigMetrics] = []
    for name, rc in configs:
        results.append(
            _eval_one_config(
                config_name=name,
                rank_config=rc,
                queries=queries,
                uri=uri,
                ladybug_path=ladybug_path,
                model=model,
                model_name=cfg.model_name,
                device=cfg.device,
                top_k_metrics=cfg.top_k_metrics,
                limit=limit,
            )
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Namespace outputs under the timestamp so successive sweeps don't clobber.
    out_dir = str(Path(cfg.results_dir) / timestamp)
    report = EvalReport(
        configs=results,
        timestamp=timestamp,
        num_queries=len(queries),
        corpus_dir=cfg.corpus_dir,
        index_dir=index_dir,
        out_dir=out_dir,
    )

    # 6. Persist into <results_dir>/<timestamp>/report.{md,json}.
    _persist(report, cfg.results_dir)
    return report


def _persist(report: EvalReport, results_dir: str) -> None:
    # Write into a timestamped subdir so successive sweeps don't clobber.
    out = Path(results_dir) / report.timestamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(_render_markdown(report))
    (out / "report.json").write_text(report.to_json())
