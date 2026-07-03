"""Unit tests for search_lancedb helpers (no LanceDB / Kuzu required)."""

from __future__ import annotations

import numpy as np

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
