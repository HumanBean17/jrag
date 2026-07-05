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

    # Verify skip_role_weight consistency: when _skip_role_weight is set,
    # _role_weight returns 0.0 and writes 0.0 to comps["role_weight"],
    # so _effective_distance correctly uses 0.0 (not the actual role weight).
    row_with_role = {
        "filename": "e.java",
        "range_start": 5,
        "range_end": 50,
        "_distance": 0.5,
        "role": "CONTROLLER",  # Would normally give 0.10 role_weight
        "_skip_role_weight": True,  # But we're skipping role weights
        "_score_components": {"distance": 0.5, "role_weight": 0.0},  # _role_weight set this
    }
    effective_dist = _effective_distance(row_with_role["_score_components"])
    # Should be 0.5 (not 0.5 - 0.10 = 0.40) because role_weight is 0.0 when skipped
    assert effective_dist == 0.5, f"expected 0.5, got {effective_dist}"


def test_hybrid_score_normalized_to_unit_range() -> None:
    """Hybrid search displayed scores are rank-monotonic and in [0,1].

    Exercises the actual hybrid sort + post-sort normalization code path.
    The composite sort metric (raw_rrf * import_factor + role_weight + symbol_bonus)
    must produce displayed scores that are non-increasing with rank.
    """
    from search_lancedb import _hybrid_sort_key, _hybrid_post_sort_normalization

    # Build controlled hybrid rows with varying raw scores and components.
    # Row 2 should rank highest due to large role_weight despite lower raw RRF.
    # Row 1 should rank second due to high raw RRF + symbol_bonus.
    # Row 3 should rank lowest (low raw RRF, no bonuses).
    rows = [
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "_score": 0.032,  # high raw RRF
            "_hints": {"import_heavy": False},
            "_score_components": {
                "hybrid_rrf": 0.032,
                "symbol_bonus": 0.06,  # max symbol bonus
                "role_weight": 0.0,
            },
        },
        {
            "filename": "b.java",
            "range_start": 2,
            "range_end": 20,
            "_score": 0.016,  # lower raw RRF but high role weight
            "role": "CONTROLLER",
            "_hints": {"import_heavy": False},
            "_score_components": {
                "hybrid_rrf": 0.016,
                "symbol_bonus": 0.0,
                "role_weight": 0.10,  # CONTROLLER weight
            },
        },
        {
            "filename": "c.java",
            "range_start": 3,
            "range_end": 30,
            "_score": 0.025,  # mid raw RRF, no bonuses
            "_hints": {"import_heavy": False},
            "_score_components": {
                "hybrid_rrf": 0.025,
                "symbol_bonus": 0.0,
                "role_weight": 0.0,
            },
        },
    ]

    # Run through actual hybrid sort + post-sort normalization
    rows.sort(key=_hybrid_sort_key)
    _hybrid_post_sort_normalization(rows)

    # Extract displayed scores in sorted order
    displayed_scores = [r["_score"] for r in rows]

    # Verify all scores in [0,1]
    for score in displayed_scores:
        assert 0.0 <= score <= 1.0, f"score {score} not in [0,1]"

    # Verify scores are non-increasing (rank-monotonic)
    for i in range(len(displayed_scores) - 1):
        assert displayed_scores[i] >= displayed_scores[i + 1], (
            f"scores not non-increasing: {displayed_scores[i]} < {displayed_scores[i + 1]}"
        )

    # Verify ranking: row 2 (CONTROLLER) should be first, row 1 second, row 3 last
    assert rows[0]["filename"] == "b.java", f"expected b.java first, got {rows[0]['filename']}"
    assert rows[1]["filename"] == "a.java", f"expected a.java second, got {rows[1]['filename']}"
    assert rows[2]["filename"] == "c.java", f"expected c.java last, got {rows[2]['filename']}"
