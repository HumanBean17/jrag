"""Unit tests for search_lancedb helpers (no LanceDB / Kuzu required)."""

from __future__ import annotations

import numpy as np
import pytest

# search_lancedb imports lancedb/torch at module load; skip the whole file on graph-only
# installs (macOS Intel) where the vector stack is absent.
pytest.importorskip("lancedb")
pytest.importorskip("sentence_transformers")

import search_lancedb
from search_lancedb import JAVA_ENRICHED_COLUMNS, _rrf_merge


def test_rrf_merge_weights_second_list_by_row() -> None:
    vec = [{"filename": "a.java", "range_start": 1, "range_end": 10}]
    graph = [
        {
            "filename": "b.java",
            "range_start": 2,
            "range_end": 20,
            "_graph_expand_weight": 1.0,
        },
        {
            "filename": "c.java",
            "range_start": 3,
            "range_end": 30,
            "_graph_expand_weight": 0.5,
        },
    ]
    merged = _rrf_merge(
        [vec, graph],
        k=60,
        row_weight_for_list_index=[
            None,
            lambda row: float(row.get("_graph_expand_weight", 1.0)),
        ],
    )
    by_file = {m["filename"]: float(m["_rrf_score"]) for m in merged}
    # Rank 0 in graph list (weight 1.0) should contribute more than rank 1 (weight 0.5).
    assert by_file["b.java"] > by_file["c.java"]


def test_rrf_merge_reinforced_row_across_lists_outranks_singleton() -> None:
    """Multi-list RRF (issue #358): a row reinforced across two ranked lists
    accumulates score and outranks a row appearing in only one list. Merging
    dedups by (filename, range_start, range_end) and orders by summed score —
    the core of multi-table fused ranking, previously covered only for the
    weighted two-list case."""
    list_a = [{"filename": "a.java", "range_start": 1, "range_end": 5}]
    list_b = [{"filename": "a.java", "range_start": 1, "range_end": 5}]  # same key, distinct row
    list_c = [{"filename": "z.java", "range_start": 9, "range_end": 99}]  # singleton, rank 0
    merged = _rrf_merge([list_a, list_b, list_c], k=60)
    by_file = {m["filename"]: float(m["_rrf_score"]) for m in merged}
    # 'a.java' (rank 0 in two lists) sums two contributions; 'z.java' only one.
    assert by_file["a.java"] > by_file["z.java"]
    # The two a.java entries collapse to one (dedup by row key).
    assert len(merged) == 2
    # Highest summed score first.
    assert merged[0]["filename"] == "a.java"


def test_java_enriched_columns_include_symbol_identity_fields() -> None:
    assert "symbol_id" in JAVA_ENRICHED_COLUMNS
    assert "metadata" in JAVA_ENRICHED_COLUMNS


def test_search_one_table_selects_symbol_identity_columns_when_schema_has_them(monkeypatch) -> None:
    selected: list[str] = []

    class _FakeQuery:
        def select(self, cols):
            selected[:] = list(cols)
            return self

        def limit(self, _n):
            return self

        def to_list(self):
            return []

    class _FakeTable:
        def search(self, *_args, **_kwargs):
            return _FakeQuery()

    class _FakeDb:
        def open_table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(
        search_lancedb,
        "_table_columns",
        lambda *_args, **_kwargs: {
            "filename",
            "text",
            "start",
            "end",
            "language",
            "package",
            "primary_type_fqn",
            "symbol_id",
            "metadata",
        },
    )
    search_lancedb._search_one_table(
        "javacodeindex_java_code",
        uri="mem://",
        db=_FakeDb(),
        query_vec=np.zeros((3,), dtype=np.float32),
        limit=5,
        path_predicate=None,
        kind="java",
        hybrid=False,
        fts_text=None,
        extra_predicates=None,
    )
    assert "symbol_id" in selected
    assert "metadata" in selected


def test_vector_displayed_score_is_rank_monotonic() -> None:
    """Vector search displayed score is non-increasing with rank and clamped to [0,1].

    The honest score uses the adjusted distance (distance + import_penalty - role_weight - symbol_bonus).
    This matches the sort key, so the displayed score is monotonic. After clamping, scores are in [0,1].
    """
    from search_lancedb import _effective_distance, l2_distance_to_score, _clamp01

    # Build controlled rows with varying distances and bonuses
    rows = [
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "_distance": 0.3,
            "_score_components": {"distance": 0.3},
        },
        {
            "filename": "b.java",
            "range_start": 2,
            "range_end": 20,
            "_distance": 0.5,
            "_score_components": {
                "distance": 0.5,
                "role_weight": 0.1,
            },  # role_weight reduces distance
        },
        {
            "filename": "c.java",
            "range_start": 3,
            "range_end": 30,
            "_distance": 0.7,
            "_score_components": {
                "distance": 0.7,
                "import_penalty": 0.2,
                "symbol_bonus": 0.15,
            },
        },
        {
            "filename": "d.java",
            "range_start": 4,
            "range_end": 40,
            "_distance": 1.2,
            "_score_components": {"distance": 1.2},
        },
    ]

    # Simulate the post-sort honest-score pass
    for r in rows:
        comps = r["_score_components"]
        effective_dist = _effective_distance(comps)
        r["_score"] = _clamp01(l2_distance_to_score(effective_dist))

    # Verify scores are in [0,1]
    for r in rows:
        assert 0.0 <= r["_score"] <= 1.0, f"score {r['_score']} not in [0,1]"

    # Verify scores are non-increasing (rank monotonic)
    scores = [r["_score"] for r in rows]
    for i in range(len(scores) - 1):
        assert (
            scores[i] >= scores[i + 1]
        ), f"score not monotonic: {scores[i]} < {scores[i + 1]}"


def test_hybrid_score_normalized_to_unit_range() -> None:
    """Hybrid search raw RRF scores (~0.016-0.032) are normalized to [0,1].

    LanceDB hybrid uses RRF with k=60; theoretical max for 2 lists is 2/(60+1) ≈ 0.0328.
    After normalization, top hits should score ≥ 0.5 and all scores in [0,1].
    """
    from search_lancedb import _clamp01

    # Theoretical max for 2-list RRF with k=60
    rrf_k = 60
    max_rrf = 2.0 / (rrf_k + 1)  # ≈ 0.0328

    # Simulate hybrid rows with raw RRF scores
    rows = [
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "_score": 0.032,  # top hit
            "_score_components": {"hybrid_rrf": 0.032, "rrf_raw": 0.032},
        },
        {
            "filename": "b.java",
            "range_start": 2,
            "range_end": 20,
            "_score": 0.016,  # mid-tier hit
            "_score_components": {"hybrid_rrf": 0.016, "rrf_raw": 0.016},
        },
        {
            "filename": "c.java",
            "range_start": 3,
            "range_end": 30,
            "_score": 0.008,  # lower hit
            "_score_components": {"hybrid_rrf": 0.008, "rrf_raw": 0.008},
        },
    ]

    # Normalize displayed scores
    for r in rows:
        raw = r["_score_components"]["rrf_raw"]
        r["_score"] = _clamp01(raw / max_rrf)

    # Verify all scores in [0,1]
    for r in rows:
        assert 0.0 <= r["_score"] <= 1.0, f"normalized score {r['_score']} not in [0,1]"

    # Verify top hit scores high (≥ 0.5 since it's near the max)
    assert rows[0]["_score"] >= 0.5, f"top hit score {rows[0]['_score']} < 0.5"
