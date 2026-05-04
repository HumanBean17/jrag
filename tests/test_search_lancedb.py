"""Unit tests for search_lancedb helpers (no LanceDB / Kuzu required)."""

from __future__ import annotations

from search_lancedb import _rrf_merge


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
