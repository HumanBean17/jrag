"""Unit tests for search_lancedb helpers (no LanceDB / Kuzu required)."""

from __future__ import annotations

import numpy as np
import pytest

# search_lancedb imports lancedb/torch at module load; skip the whole file on graph-only
# installs (macOS Intel) where the vector stack is absent.
pytest.importorskip("lancedb")
pytest.importorskip("sentence_transformers")

from java_codebase_rag.search import search_lancedb
from java_codebase_rag.search.search_lancedb import JAVA_ENRICHED_COLUMNS, _rrf_merge, run_search


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


def test_graph_expand_merge_honors_injected_k(monkeypatch) -> None:
    """RankConfig.rrf_k flows through _graph_expand_merge into _rrf_merge.

    With k=30 injected, a row reinforced at rank 0 across the vector and graph
    lists has ``rrf_raw = 2/(k+1) = 2/31``. Under the old default k=60 it would
    be 2/61 — strictly smaller — so observing 2/31 proves the injected k wins.
    """
    import sys
    import types

    from java_codebase_rag.search.search_scoring import RankConfig

    # Stub the lazily-imported LadybugGraph so the function reaches the merge.
    class _FakeGraph:
        @staticmethod
        def exists(path):
            return True

        @staticmethod
        def get(path):
            class _G:
                def expand_fqns(self, fqns, depth):
                    # One novel FQN so the function proceeds to graph fetch + fuse.
                    return ["com.example.Other"]

                def expand_methods(self, fqns, depth, exclude_external=False):
                    return []
            return _G()

    fake_mod = types.ModuleType("java_codebase_rag.graph.ladybug_queries")
    fake_mod.LadybugGraph = _FakeGraph
    monkeypatch.setitem(sys.modules, "java_codebase_rag.graph.ladybug_queries", fake_mod)

    # Same (filename, range_start, range_end) key in both lists → rank-0 in both,
    # so raw RRF = 1/(k+1) + 1/(k+1) = 2/(k+1).
    vector_rows = [
        {"filename": "a.java", "range_start": 1, "range_end": 10,
         "primary_type_fqn": "com.example.Foo"},
    ]
    graph_rows = [
        {"filename": "a.java", "range_start": 1, "range_end": 10,
         "primary_type_fqn": "com.example.Other"},
    ]

    monkeypatch.setattr(search_lancedb, "_search_one_table", lambda *a, **kw: graph_rows)
    monkeypatch.setattr(search_lancedb, "_table_columns", lambda *a, **kw: set())
    monkeypatch.setattr(search_lancedb, "_build_extra_predicates", lambda **kw: [])
    monkeypatch.setattr(search_lancedb, "_apply_chunk_hints", lambda rows: None)
    monkeypatch.setattr(search_lancedb, "_refine_java_start_lines", lambda rows: None)
    monkeypatch.setattr(search_lancedb, "_vector_sort_key", lambda r: 0.0)

    result = search_lancedb._graph_expand_merge(
        vector_rows,
        query="query",
        query_vec=np.zeros(3),
        db=object(),
        uri="mem://",
        limit=10,
        extra_predicates=[],
        expand_depth=1,
        ladybug_path=None,
        rank_config=RankConfig(lists=frozenset({"vector", "graph"}), rrf_k=30),
    )

    top = result[0]
    # k=30 → raw = 2/31; would be 2/61 if the default leaked through.
    assert top["_score_components"]["rrf_raw"] == pytest.approx(2.0 / 31.0, abs=1e-12)
    assert top["_score_components"]["rrf_raw"] > 2.0 / 61.0


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
    from java_codebase_rag.search.search_lancedb import (
        _effective_distance,
        vector_display_score,
    )

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

    # Simulate the post-sort honest-score pass (run_search uses vector_display_score
    # on the effective distance — the new non-clamping map; see F6).
    for r in rows:
        comps = r["_score_components"]
        effective_dist = _effective_distance(comps)
        r["_score"] = vector_display_score(effective_dist)

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
    from java_codebase_rag.search.search_lancedb import _hybrid_sort_key, _hybrid_post_sort_normalization

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


def test_hybrid_import_penalty_rendered_as_additive_penalty() -> None:
    """Hybrid import_penalty is rendered as +0.12 (not +0.88 multiplier).

    Regression for search redesign PR: _hybrid_sort_key stores import_penalty
    as the EFFECT (1.0 - 0.88 = 0.12) so explain output is not misleading.
    The normalization function uses the constant directly, so changing the
    stored value is safe and doesn't affect the actual hybrid score.
    """
    from java_codebase_rag.search.search_lancedb import _hybrid_sort_key, explain_score_components

    # Create a row with import_heavy hint
    row = {
        "filename": "a.java",
        "range_start": 1,
        "range_end": 10,
        "_score": 0.032,
        "role": "SERVICE",
        "_hints": {"import_heavy": True},
        "_score_components": {},
    }

    # Run through hybrid sort key (this populates _score_components)
    _ = _hybrid_sort_key(row)

    # Verify import_penalty is 0.12 (the effect), not 0.88 (the multiplier)
    comps = row["_score_components"]
    assert "import_penalty" in comps, "import_penalty should be in score_components"
    assert abs(comps["import_penalty"] - 0.12) < 0.001, (
        f"import_penalty should be ~0.12, got {comps['import_penalty']}"
    )

    # Verify explain output shows +0.12 (not +0.88)
    explain = explain_score_components(comps, hybrid=True, role=row.get("role"))
    assert "import_penalty:+0.12" in explain, (
        f"explain should show import_penalty:+0.12, got: {explain}"
    )
    assert "+0.88" not in explain, (
        f"explain should NOT show misleading +0.88 bonus, got: {explain}"
    )


def test_run_search_dedup_collapses_by_fqn() -> None:
    """Dedup by primary_type_fqn collapses multiple chunks of the same type.

    First-seen-wins (rows are pre-sorted, so first is best chunk).
    Each survivor gets _chunks_collapsed count (>=1).
    """
    from java_codebase_rag.search.search_lancedb import _dedup_by_fqn

    # Build controlled sorted rows: 3 rows FQN=A (best first), 1 row FQN=B, 2 rows FQN=C
    rows = [
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeA",
            "_score": 0.95,
        },
        {
            "filename": "a.java",
            "range_start": 11,
            "range_end": 20,
            "primary_type_fqn": "com.example.TypeA",
            "_score": 0.85,
        },
        {
            "filename": "a.java",
            "range_start": 21,
            "range_end": 30,
            "primary_type_fqn": "com.example.TypeA",
            "_score": 0.75,
        },
        {
            "filename": "b.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeB",
            "_score": 0.90,
        },
        {
            "filename": "c.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeC",
            "_score": 0.88,
        },
        {
            "filename": "c.java",
            "range_start": 11,
            "range_end": 20,
            "primary_type_fqn": "com.example.TypeC",
            "_score": 0.78,
        },
    ]

    deduped = _dedup_by_fqn(rows)

    # Should collapse to 3 unique FQNs
    assert len(deduped) == 3, f"expected 3 rows, got {len(deduped)}"

    # Check FQNs are unique
    fqns = [r.get("primary_type_fqn") for r in deduped]
    assert len(fqns) == len(set(fqns)), "FQNs should be unique"

    # First should be TypeA (best score of the three)
    assert deduped[0]["primary_type_fqn"] == "com.example.TypeA"
    assert deduped[0]["_score"] == 0.95, "Best chunk should survive"

    # Check _chunks_collapsed counts
    chunks_by_fqn = {r["primary_type_fqn"]: r["_chunks_collapsed"] for r in deduped}
    assert chunks_by_fqn["com.example.TypeA"] == 3, "TypeA should have 3 chunks collapsed"
    assert chunks_by_fqn["com.example.TypeB"] == 1, "TypeB should have 1 chunk"
    assert chunks_by_fqn["com.example.TypeC"] == 2, "TypeC should have 2 chunks collapsed"


def test_run_search_dedup_offset_pagination() -> None:
    """Dedup with offset/limit still fills pages and preserves truncation detection.

    8 distinct FQNs × 3 chunks each = 24 rows.
    With limit=5, offset=5, dedup_by_fqn=True → should return FQNs #6..#10 (5 unique).
    Over-fetch (4x) ensures we fetch enough to fill the page after dedup.
    """
    from java_codebase_rag.search.search_lancedb import _dedup_by_fqn

    # Build 8 FQNs × 3 chunks each = 24 rows, pre-sorted by FQN then score
    rows = []
    for fqn_idx in range(8):
        fqn = f"com.example.Type{chr(65 + fqn_idx)}"  # TypeA, TypeB, ...
        for chunk_idx in range(3):
            rows.append({
                "filename": f"file{fqn_idx}.java",
                "range_start": chunk_idx * 10 + 1,
                "range_end": chunk_idx * 10 + 10,
                "primary_type_fqn": fqn,
                "_score": 1.0 - (chunk_idx * 0.1),  # Best chunk first per FQN
            })

    # Simulate the over-fetch + dedup + window flow
    # limit=5, offset=5 → we want FQNs #5, #6, #7, #8, #9 (but we only have 8)
    # After dedup: 8 unique FQNs total
    # offset=5 → skip first 5 FQNs → should get FQNs #5, #6, #7 (3 remaining)
    # limit=5 → but we only have 3, so no truncation

    deduped = _dedup_by_fqn(rows)

    # Should have 8 unique FQNs
    assert len(deduped) == 8, f"expected 8 unique FQNs after dedup, got {len(deduped)}"

    # Window [5:10] (offset=5, limit=5) → should get 3 rows (FQNs #5, #6, #7)
    windowed = deduped[5:10]
    assert len(windowed) == 3, f"expected 3 rows in window, got {len(windowed)}"

    # Check the windowed FQNs are #5, #6, #7 (TypeF, TypeG, TypeH)
    expected_fqns = ["com.example.TypeF", "com.example.TypeG", "com.example.TypeH"]
    actual_fqns = [r["primary_type_fqn"] for r in windowed]
    assert actual_fqns == expected_fqns, f"expected {expected_fqns}, got {actual_fqns}"

    # Check each has chunks=3
    for r in windowed:
        assert r["_chunks_collapsed"] == 3, f"FQN {r['primary_type_fqn']} should have 3 chunks"

    # Simulate a case where we DO have truncation
    # With limit=3, offset=0 → we get 3 FQNs, but 5 more exist → truncation=True
    windowed_truncated = deduped[0:4]  # +1 for truncation detection
    assert len(windowed_truncated) == 4, "Should get 4 rows with +1 fetch"
    # First 3 are the actual page, 4th is the truncation sentinel
    actual_page = windowed_truncated[:3]
    assert len(actual_page) == 3, "Actual page should have 3 rows"
    # Truncation detected because we have 4 rows (> limit=3)
    has_truncation = len(windowed_truncated) > 3
    assert has_truncation, "Should detect truncation when +1 row exists"


def test_run_search_dedup_passes_through_sql_yaml() -> None:
    """Rows without primary_type_fqn (sql/yaml) are NOT collapsed.

    Each row without primary_type_fqn gets a unique dedup key __id:<id>
    so they pass through unchanged.
    """
    from java_codebase_rag.search.search_lancedb import _dedup_by_fqn

    rows = [
        {
            "filename": "schema.sql",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": None,  # SQL row
            "_score": 0.90,
            "id": "sql1",
        },
        {
            "filename": "schema.sql",
            "range_start": 11,
            "range_end": 20,
            "primary_type_fqn": None,  # SQL row
            "_score": 0.85,
            "id": "sql2",
        },
        {
            "filename": "config.yaml",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": None,  # YAML row
            "_score": 0.88,
            "id": "yaml1",
        },
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeA",  # Java row
            "_score": 0.95,
        },
        {
            "filename": "a.java",
            "range_start": 11,
            "range_end": 20,
            "primary_type_fqn": "com.example.TypeA",  # Java row - should collapse
            "_score": 0.85,
        },
    ]

    deduped = _dedup_by_fqn(rows)

    # Should have 4 rows: 3 sql/yaml (unique) + 1 java (collapsed from 2)
    assert len(deduped) == 4, f"expected 4 rows, got {len(deduped)}"

    # Check sql/yaml rows are all present (not collapsed)
    sql_yaml_rows = [r for r in deduped if r["primary_type_fqn"] is None]
    assert len(sql_yaml_rows) == 3, f"expected 3 sql/yaml rows, got {len(sql_yaml_rows)}"

    # Check java row is collapsed to 1
    java_rows = [r for r in deduped if r["primary_type_fqn"] == "com.example.TypeA"]
    assert len(java_rows) == 1, "Java rows should be collapsed to 1"
    assert java_rows[0]["_chunks_collapsed"] == 2, "Should have 2 chunks collapsed"
    assert java_rows[0]["_score"] == 0.95, "Best chunk should survive"


def test_run_search_dedup_off_is_byte_identical() -> None:
    """dedup_by_fqn=False reproduces prior output exactly (regression guard).

    The non-dedup path should be byte-identical to today's behavior.
    This test ensures we don't accidentally change behavior when dedup is off.
    """
    from java_codebase_rag.search.search_lancedb import _dedup_by_fqn

    rows = [
        {
            "filename": "a.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeA",
            "_score": 0.95,
        },
        {
            "filename": "a.java",
            "range_start": 11,
            "range_end": 20,
            "primary_type_fqn": "com.example.TypeA",
            "_score": 0.85,
        },
        {
            "filename": "b.java",
            "range_start": 1,
            "range_end": 10,
            "primary_type_fqn": "com.example.TypeB",
            "_score": 0.90,
        },
    ]

    # When dedup is OFF, should return rows unchanged
    result = _dedup_by_fqn(rows, dedup_by_fqn=False)

    assert len(result) == len(rows), f"expected {len(rows)} rows, got {len(result)}"
    assert result == rows, "Rows should be unchanged when dedup is OFF"

    # Verify no _chunks_collapsed added
    for r in result:
        assert "_chunks_collapsed" not in r, "Should not add _chunks_collapsed when dedup is OFF"


# ---------- F7: _silence_lance_autoproj_warnings ----------


def test_silence_lance_warnings_drops_markers_keeps_rest() -> None:
    """The autoprojection deprecation lines are swallowed; real stderr survives.

    The silencer redirects the OS-level fd 2 (what LanceDB's Rust tracing writes
    to), so the test must write via ``os.write(2, ...)`` — not ``sys.stderr``
    (a Python object that may not be fd 2)."""
    import io
    import os
    import sys

    from java_codebase_rag.search.search_lancedb import _silence_lance_autoproj_warnings

    # Capture what the silencer RE-EMITS to sys.stderr after restoring fd 2.
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        with _silence_lance_autoproj_warnings():
            os.write(2, b"WARN tracing: did not include `_distance`. Call disable_scoring_autoprojection\n")
            os.write(2, b"ERROR something went wrong: boom\n")
    finally:
        sys.stderr = real_stderr
    emitted = buf.getvalue()
    assert "did not include" not in emitted
    assert "disable_scoring_autoprojection" not in emitted
    assert "ERROR something went wrong: boom" in emitted


def test_silence_lance_warnings_opt_out_passthrough(monkeypatch) -> None:
    """JAVA_CODEBASE_RAG_KEEP_LANCE_WARNINGS disables the silencer entirely."""
    import sys

    from java_codebase_rag.search.search_lancedb import _silence_lance_autoproj_warnings

    monkeypatch.setenv("JAVA_CODEBASE_RAG_KEEP_LANCE_WARNINGS", "1")
    # No redirect happens -> fd 2 is untouched; just verify the context yields.
    with _silence_lance_autoproj_warnings():
        sys.stderr.write("")  # would land on real stderr (no capture buffer)
    # No assertion crash == fd 2 was never hijacked.


def test_silence_lance_warnings_restores_fd2_on_exception() -> None:
    """fd 2 is restored even when the wrapped call raises."""
    import os

    from java_codebase_rag.search.search_lancedb import _silence_lance_autoproj_warnings

    saved_before = os.dup(2)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with _silence_lance_autoproj_warnings():
                raise RuntimeError("boom")
        # fd 2 must be a valid, open descriptor after the exception.
        assert os.fstat(2) is not None
    finally:
        os.close(saved_before)


# ---------- F8: _refine_java_start_lines ----------


def test_refine_java_start_lines_points_at_declaration() -> None:
    from java_codebase_rag.search.search_lancedb import _refine_java_start_lines

    chunk = "package com.x;\nimport java.util.List;\n\npublic class ChatPort {\n"
    rows = [
        {"_kind": "java", "start": {"line": 1}, "text": chunk,
         "_hints": {"primary_type_hint": "ChatPort"}},
    ]
    _refine_java_start_lines(rows)
    assert rows[0]["start"]["line"] == 4  # decl line, not the package anchor


def test_refine_java_start_lines_skips_nonjava_and_method_chunks() -> None:
    from java_codebase_rag.search.search_lancedb import _refine_java_start_lines

    rows = [
        # Non-java row untouched.
        {"_kind": "sql", "start": {"line": 7}, "text": "SELECT 1", "_hints": {}},
        # Java method-only chunk (no type decl) keeps its anchor.
        {"_kind": "java", "start": {"line": 30}, "text": "    void doWork() {}\n",
         "_hints": {"primary_type_hint": "Worker"}},
        # Missing start dict -> safe (unchanged).
        {"_kind": "java", "text": "public class X {", "_hints": {}},
    ]
    _refine_java_start_lines(rows)
    assert rows[0]["start"]["line"] == 7
    assert rows[1]["start"]["line"] == 30
    assert "start" not in rows[2]


# ---------- Task 4: BM25 candidate fetch + third RRF list ----------


def _eval_pred(rows: list[dict], pred: str | None) -> list[dict]:
    """Tiny SQL-ish predicate evaluator for test fakes (AND / IN / <> / =).

    Splits on `` AND `` WITHOUT destroying parens (the prior implementation
    pre-stripped ``(``/``)`` everywhere, which wiped the ``IN (...)`` marker so
    the ``primary_type_fqn IN (...)`` filter never evaluated and rows that
    should be filtered passed). ``_combine_predicates`` wraps each conjunct in a
    paren pair when there are several, so we strip ONE outer paren layer per
    clause while preserving the inner ``IN (...)`` parens needed to extract the
    value list.
    """
    if not pred:
        return list(rows)
    clauses: list[str] = []
    for c in pred.split(" AND "):
        c = c.strip()
        # Strip ONE outer wrapping paren pair (multi-predicate case); leave
        # ``col IN (...)`` and its inner value-list parens intact.
        if c.startswith("(") and c.endswith(")"):
            c = c[1:-1].strip()
        if c:
            clauses.append(c)
    out: list[dict] = []
    for r in rows:
        keep = True
        for clause in clauses:
            if " IN (" in clause:
                col = clause.split(" IN ", 1)[0].strip()
                vals_part = clause[clause.index("(") + 1 : clause.rindex(")")]
                vals = [v.strip().strip("'") for v in vals_part.split(",")]
                if str(r.get(col)) not in vals:
                    keep = False
                    break
            elif "<>" in clause:
                col, val = [p.strip() for p in clause.split("<>", 1)]
                val = val.strip("'")
                if str(r.get(col)) == val:
                    keep = False
                    break
            elif "=" in clause:
                col, val = [p.strip() for p in clause.split("=", 1)]
                val = val.strip("'")
                if str(r.get(col)) != val:
                    keep = False
                    break
        if keep:
            out.append(r)
    return out


class _FilterQuery:
    """Fake LanceDB filter-only query: search().where().select().limit().to_list()."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._pred: str | None = None
        self._limit: int | None = None

    def where(self, pred: str | None, prefilter: bool = False) -> "_FilterQuery":
        self._pred = pred
        return self

    def select(self, _cols: list[str]) -> "_FilterQuery":
        return self

    def limit(self, n: int) -> "_FilterQuery":
        self._limit = n
        return self

    def to_list(self) -> list[dict]:
        filtered = _eval_pred(self._rows, self._pred)
        if self._limit is not None:
            filtered = filtered[: self._limit]
        return list(filtered)


class _FilterTable:
    """Fake LanceDB table: open_table(...).search() with no vector → filter-only."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def search(self, *args, **kwargs) -> _FilterQuery:
        # Filter-only when no positional vector arg is passed.
        return _FilterQuery(self._rows)


class _RecordingDb:
    """Fake DB recording open_table calls; returns a per-table _FilterTable."""

    def __init__(self) -> None:
        self.tables: dict[str, _FilterTable] = {}
        self.opened: list[str] = []

    def add(self, name: str, rows: list[dict]) -> None:
        self.tables[name] = _FilterTable(rows)

    def open_table(self, name: str) -> _FilterTable:
        self.opened.append(name)
        return self.tables.get(name) or _FilterTable([])


def _bm25_chunk(filename: str, fqn: str, rs: int, re_: int) -> dict:
    return {
        "filename": filename,
        "range_start": rs,
        "range_end": re_,
        "primary_type_fqn": fqn,
        "text": f"body of {fqn}",
        "language": "java",
        "start": {"line": rs, "byte_offset": 0},
        "end": {"line": re_, "byte_offset": 100},
    }


def _patch_bm25_environment(monkeypatch, *, fts_result, chunk_rows) -> _RecordingDb:
    """Wire the common monkeypatches for _bm25_candidate_rows unit tests."""
    from java_codebase_rag.search import search_lexical

    monkeypatch.setattr(search_lexical, "fetch_fts_candidates", lambda *a, **kw: fts_result)
    monkeypatch.setattr(search_lexical, "enclosing_type_fqn", lambda fqn: fqn.split("#", 1)[0])
    monkeypatch.setattr(search_lancedb, "_apply_chunk_hints", lambda rows: None)
    monkeypatch.setattr(search_lancedb, "_refine_java_start_lines", lambda rows: None)
    # Provide a schema that includes the enriched java columns the helper selects.
    monkeypatch.setattr(
        search_lancedb, "_table_columns",
        lambda *a, **kw: {"filename", "text", "start", "end", "language", "range_start",
                          "range_end", "primary_type_fqn", "role", "package"},
    )
    db = _RecordingDb()
    db.add(search_lancedb.TABLES["java"], list(chunk_rows))
    return db


def test_bm25_candidate_rows_orders_by_bm25_score(monkeypatch) -> None:
    """(a) BM25 candidates are emitted in BM25-desc order with the owning score attached.

    FTS rows are supplied SCRAMBLED relative to score order [C(10), A(20), B(30)]
    so only a real score-desc sort produces the asserted [B, A, C] output — a
    first-seen/passthrough bug would yield [C, A, B] instead.
    """
    fts_result = {
        "rows": [
            {"id": "sc", "fqn": "com.x.C", "kind": "class", "name": "C"},
            {"id": "sa", "fqn": "com.x.A", "kind": "class", "name": "A"},
            {"id": "sb", "fqn": "com.x.B", "kind": "class", "name": "B"},
        ],
        "scores": {"sb": 30.0, "sa": 20.0, "sc": 10.0},
    }
    chunk_rows = [
        _bm25_chunk("a.java", "com.x.A", 1, 10),
        _bm25_chunk("b.java", "com.x.B", 1, 10),
        _bm25_chunk("c.java", "com.x.C", 1, 10),
    ]
    db = _patch_bm25_environment(monkeypatch, fts_result=fts_result, chunk_rows=chunk_rows)

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=[], columns={"primary_type_fqn"},
    )
    fqns = [r["primary_type_fqn"] for r in out]
    assert fqns == ["com.x.B", "com.x.A", "com.x.C"]
    scores = {r["primary_type_fqn"]: r["_score_components"]["bm25"] for r in out}
    assert scores == {"com.x.B": 30.0, "com.x.A": 20.0, "com.x.C": 10.0}


def test_bm25_candidate_rows_fts_unavailable_returns_empty(monkeypatch) -> None:
    """(b) FTS returns None → [] and no LanceDB fetch is attempted."""
    db = _patch_bm25_environment(monkeypatch, fts_result=None, chunk_rows=[])

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=[], columns={"primary_type_fqn"},
    )
    assert out == []
    assert db.opened == []  # no chunk fetch attempted


def test_bm25_candidate_rows_respects_filter(monkeypatch) -> None:
    """(c) extra_predicates flow into the chunk fetch and filter out types."""
    fts_result = {
        "rows": [
            {"id": "sa", "fqn": "com.x.A", "kind": "class", "name": "A"},
            {"id": "sb", "fqn": "com.x.B", "kind": "class", "name": "B"},
        ],
        "scores": {"sa": 20.0, "sb": 10.0},
    }
    chunk_rows = [
        _bm25_chunk("a.java", "com.x.A", 1, 10),
        _bm25_chunk("b.java", "com.x.B", 1, 10),
    ]
    db = _patch_bm25_environment(monkeypatch, fts_result=fts_result, chunk_rows=chunk_rows)

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=["primary_type_fqn <> 'com.x.B'"],
        columns={"primary_type_fqn"},
    )
    fqns = [r["primary_type_fqn"] for r in out]
    assert fqns == ["com.x.A"]


def test_bm25_candidate_rows_in_predicate_excludes_non_matching_types(monkeypatch) -> None:
    """(c2) The ``primary_type_fqn IN (...)`` predicate (built from ordered_types)
    excludes chunks whose FQN is not among the BM25-ordered types.

    Regression: the ``_RecordingDb`` fake's ``_eval_pred`` previously destroyed
    parens before checking ``" IN ("``, so the IN predicate never evaluated and
    non-matching chunks leaked through. FTS yields only ``com.x.A`` (so
    ordered_types=['com.x.A'] and the IN predicate is ``primary_type_fqn IN
    ('com.x.A')``); a ``com.x.B`` chunk sitting in the table must be filtered out.
    """
    fts_result = {
        "rows": [
            {"id": "sa", "fqn": "com.x.A", "kind": "class", "name": "A"},
        ],
        "scores": {"sa": 20.0},
    }
    chunk_rows = [
        _bm25_chunk("a.java", "com.x.A", 1, 10),
        _bm25_chunk("b.java", "com.x.B", 1, 10),  # NOT in ordered_types — must be filtered
    ]
    db = _patch_bm25_environment(monkeypatch, fts_result=fts_result, chunk_rows=chunk_rows)

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=[], columns={"primary_type_fqn"},
    )
    fqns = [r["primary_type_fqn"] for r in out]
    assert fqns == ["com.x.A"]
    assert "com.x.B" not in fqns  # filtered out by the IN (...) predicate


def test_bm25_candidate_rows_multiple_chunks_per_symbol_preserve_order(monkeypatch) -> None:
    """(d) Multiple chunks per type stay grouped, in table order, ordered by BM25 rank."""
    fts_result = {
        "rows": [
            {"id": "sa", "fqn": "com.x.A", "kind": "class", "name": "A"},
            {"id": "sb", "fqn": "com.x.B", "kind": "class", "name": "B"},
        ],
        "scores": {"sa": 20.0, "sb": 10.0},
    }
    chunk_rows = [
        _bm25_chunk("a.java", "com.x.A", 1, 10),
        _bm25_chunk("a.java", "com.x.A", 11, 20),
        _bm25_chunk("b.java", "com.x.B", 1, 10),
    ]
    db = _patch_bm25_environment(monkeypatch, fts_result=fts_result, chunk_rows=chunk_rows)

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=[], columns={"primary_type_fqn"},
    )
    keys = [(r["primary_type_fqn"], r["range_start"]) for r in out]
    assert keys == [("com.x.A", 1), ("com.x.A", 11), ("com.x.B", 1)]
    for r in out:
        if r["primary_type_fqn"] == "com.x.A":
            assert r["_score_components"]["bm25"] == 20.0
        else:
            assert r["_score_components"]["bm25"] == 10.0


def test_bm25_candidate_rows_dedup_members_of_same_type_keep_max(monkeypatch) -> None:
    """(d2) Two member symbols (#-fqns) of the SAME type dedup to one type entry,
    emitted ONCE at the MAX BM25 score among the members.

    FTS rows are ordered LOWER-score-first [sm2(20), sm1(30)] so a first-wins
    bug would yield 20.0 and only a keep-max implementation yields the asserted
    30.0 — this discriminates keep-max from first-seen-wins.
    """
    fts_result = {
        "rows": [
            {"id": "sm2", "fqn": "com.x.A#method2()", "kind": "method", "name": "method2"},
            {"id": "sm1", "fqn": "com.x.A#method1()", "kind": "method", "name": "method1"},
        ],
        "scores": {"sm1": 30.0, "sm2": 20.0},
    }
    chunk_rows = [
        _bm25_chunk("a.java", "com.x.A", 1, 10),
        _bm25_chunk("a.java", "com.x.A", 11, 20),
    ]
    db = _patch_bm25_environment(monkeypatch, fts_result=fts_result, chunk_rows=chunk_rows)

    out = search_lancedb._bm25_candidate_rows(
        g=object(), query="query", uri="mem://", db=db,
        extra_predicates=[], columns={"primary_type_fqn"},
    )
    # The type appears exactly once (both member chunks present, but only one type rank).
    type_fqns = [r["primary_type_fqn"] for r in out]
    assert type_fqns.count("com.x.A") == 2  # 2 chunks of the same single type
    assert set(type_fqns) == {"com.x.A"}    # no other type leaked; deduped to one entry
    # Keep-max: every emitted chunk carries the MAX member score (30.0), not 20.0.
    for r in out:
        assert r["_score_components"]["bm25"] == 30.0


def _stub_ladybug_graph(monkeypatch) -> None:
    """Install a LadybugGraph stub that exists but expands to nothing novel."""
    import sys
    import types

    class _FakeGraph:
        @staticmethod
        def exists(_path):
            return True

        @staticmethod
        def get(_path):
            class _G:
                def expand_fqns(self, fqns, depth):
                    return []

                def expand_methods(self, fqns, depth, exclude_external=False):
                    return []

            return _G()

    fake_mod = types.ModuleType("java_codebase_rag.graph.ladybug_queries")
    fake_mod.LadybugGraph = _FakeGraph
    monkeypatch.setitem(sys.modules, "java_codebase_rag.graph.ladybug_queries", fake_mod)


def test_graph_expand_merge_includes_bm25_list(monkeypatch) -> None:
    """(e) With DEFAULT_RANK_CONFIG (3-list), a BM25-only row surfaces in the fusion."""
    from java_codebase_rag.search.search_scoring import RankConfig

    _stub_ladybug_graph(monkeypatch)
    rc = RankConfig(lists=frozenset({"vector", "graph", "bm25"}), rrf_k=60)

    vector_rows = [
        {"filename": "v.java", "range_start": 1, "range_end": 10,
         "primary_type_fqn": "com.V"},
    ]
    bm25_rows = [
        {"filename": "b.java", "range_start": 1, "range_end": 10,
         "primary_type_fqn": "com.B", "_kind": "java",
         "_score_components": {"bm25": 0.42}},
    ]
    monkeypatch.setattr(search_lancedb, "_bm25_candidate_rows", lambda **kw: list(bm25_rows))
    monkeypatch.setattr(search_lancedb, "_table_columns", lambda *a, **kw: set())
    monkeypatch.setattr(search_lancedb, "_build_extra_predicates", lambda **kw: [])
    # Force the graph path to produce nothing (no novel fqns via stub) → graph_rows = [].

    result = search_lancedb._graph_expand_merge(
        vector_rows,
        query="something",
        query_vec=np.zeros(3),
        db=object(),
        uri="mem://",
        limit=10,
        extra_predicates=[],
        expand_depth=1,
        ladybug_path=None,
        rank_config=rc,
    )
    files = [r["filename"] for r in result]
    assert "b.java" in files
    bm25_row = next(r for r in result if r["filename"] == "b.java")
    assert bm25_row["_score_components"]["bm25"] == 0.42


def test_graph_expand_merge_omits_bm25_when_excluded(monkeypatch) -> None:
    """(f) With BASELINE_2LIST_CONFIG, _bm25_candidate_rows is never called."""
    from java_codebase_rag.search.search_scoring import BASELINE_2LIST_CONFIG

    _stub_ladybug_graph(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(search_lancedb, "_bm25_candidate_rows",
                        lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(search_lancedb, "_table_columns", lambda *a, **kw: set())
    monkeypatch.setattr(search_lancedb, "_build_extra_predicates", lambda **kw: [])

    vector_rows = [
        {"filename": "v.java", "range_start": 1, "range_end": 10,
         "primary_type_fqn": "com.V"},
    ]
    result = search_lancedb._graph_expand_merge(
        vector_rows,
        query="something",
        query_vec=np.zeros(3),
        db=object(),
        uri="mem://",
        limit=10,
        extra_predicates=[],
        expand_depth=1,
        ladybug_path=None,
        rank_config=BASELINE_2LIST_CONFIG,
    )
    assert calls == []
    # Result is the vector rows (graph produced nothing novel via stub).
    assert result == vector_rows


def test_run_search_bm25_degrades_silently_when_fts_missing(monkeypatch, tmp_path) -> None:
    """(g) When FTS is unavailable, the 3-list config degrades to the 2-list baseline.

    Builds a real LanceDB index with one java row, stubs LadybugGraph to exist (so
    _graph_expand_merge runs) but expand to nothing, and forces _ensure_fts_loaded→False
    so the BM25 fetch returns None. Asserts: no exception, no row carries a `bm25`
    score component, and the result equals the BASELINE_2LIST_CONFIG run.
    """
    import uuid

    import lancedb
    from sentence_transformers import SentenceTransformer

    from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION
    from java_codebase_rag.search import search_lexical
    from java_codebase_rag.search.index_common import SBERT_MODEL
    from java_codebase_rag.search.search_lancedb import TABLES, _query_vector
    from java_codebase_rag.search.search_scoring import (
        BASELINE_2LIST_CONFIG,
        DEFAULT_RANK_CONFIG,
    )

    _stub_ladybug_graph(monkeypatch)
    # Force FTS unavailable → _try_fts_candidates returns None.
    monkeypatch.setattr(search_lexical, "_ensure_fts_loaded", lambda g: False)

    uri = str(tmp_path / "ldb")
    model = SentenceTransformer(SBERT_MODEL, device="cpu", trust_remote_code=True)
    text = "service that processes inbound Kafka records on the listener endpoint"
    emb = _query_vector(model, text)
    row = {
        "id": str(uuid.uuid4()),
        "filename": "smoke/p/Svc.java",
        "text": text,
        "language": "java",
        "range_start": 0,
        "range_end": 500,
        "start": {"line": 1, "byte_offset": 0},
        "end": {"line": 20, "byte_offset": 400},
        "embedding": emb,
        "package": "p",
        "module": "smoke",
        "microservice": "smoke",
        "primary_type_fqn": "p.Svc",
        "primary_type_kind": "class",
        "role": "SERVICE",
        "annotations_on_type": [],
        "symbols": ["process"],
        "ontology_version": ONTOLOGY_VERSION,
        "capabilities": [],
    }
    db = lancedb.connect(uri)
    db.create_table(TABLES["java"], [row], mode="create")

    common = dict(
        uri=uri, table_keys=["java"], limit=5, path_substring=None,
        model_name=SBERT_MODEL, device="cpu", model=model,
        graph_expand=True, expand_depth=1,
    )
    three = run_search(text, rank_config=DEFAULT_RANK_CONFIG, **common)
    two = run_search(text, rank_config=BASELINE_2LIST_CONFIG, **common)

    # No bm25 component anywhere on either run.
    for rows in (three, two):
        assert all("bm25" not in (r.get("_score_components") or {}) for r in rows)
    # Degradation: 3-list with FTS-missing == 2-list baseline.
    assert [r["filename"] for r in three] == [r["filename"] for r in two]
    assert len(three) == len(two) and len(two) >= 1


def _build_minimal_fts_graph(db_path, *, symbol_fqn: str, symbol_name: str) -> bool:
    """Build a 1-Symbol LadybugDB graph with the real ``sym_fts`` BM25 index.

    Returns True when the FTS index was created (extension available); False when
    the FTS extension could not load (so the caller can ``pytest.skip``).

    Mirrors the production write path: ``_drop_all`` + ``_create_schema`` for the
    table structure, one Symbol node + one GraphMeta node, then
    ``_ensure_symbol_fts_index`` to build the BM25 index over ``search_text``.
    """
    import ladybug

    from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION
    from java_codebase_rag.graph.build_ast_graph import (
        _compute_symbol_search_text,
        _create_schema,
        _drop_all,
        _ensure_symbol_fts_index,
    )
    from java_codebase_rag.search.search_scoring import SYMBOL_FTS_INDEX

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ladybug.Database(str(db_path))
    conn = ladybug.Connection(db)
    try:
        _drop_all(conn)
        _create_schema(conn)
        search_text = _compute_symbol_search_text(
            name=symbol_name, fqn=symbol_fqn, signature="",
            annotations=[], capabilities=[], package=symbol_fqn.rsplit(".", 1)[0],
        )
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.kind = 'class', s.name = $name, s.fqn = $fqn, "
            "s.package = $package, s.module = '', s.microservice = '', "
            "s.filename = $filename, s.start_line = 1, s.end_line = 20, "
            "s.start_byte = 0, s.end_byte = 200, "
            "s.modifiers = $modifiers, s.annotations = $annotations, "
            "s.capabilities = $capabilities, s.role = 'SERVICE', "
            "s.signature = '', s.parent_id = '', s.resolved = false, "
            "s.generated = false, s.generated_by = '', "
            "s.search_text = $search_text",
            {
                "id": symbol_fqn, "name": symbol_name, "fqn": symbol_fqn,
                "package": symbol_fqn.rsplit(".", 1)[0],
                "filename": f"{symbol_name}.java",
                "modifiers": [], "annotations": [], "capabilities": [],
                "search_text": search_text,
            },
        )
        # GraphMeta with current ontology so LadybugGraph.get() accepts it.
        conn.execute(
            "MERGE (m:GraphMeta {key: 'meta'}) "
            "SET m.ontology_version = $ov, m.built_at = 0, m.source_root = '', "
            "m.counts_json = '', m.parse_errors = 0",
            {"ov": ONTOLOGY_VERSION},
        )
        _ensure_symbol_fts_index(conn, verbose=False)
        idx = conn.execute("CALL SHOW_INDEXES() RETURN index_name")
        names: set[str] = set()
        while idx.has_next():
            names.add(idx.get_next()[0])
        return SYMBOL_FTS_INDEX in names
    finally:
        conn.close()
        db.close()


def test_run_search_bm25_contributes_on_camelcase_query_via_real_fts(tmp_path) -> None:
    """(h) A camelCase identifier query lands in sym_fts's token space and the BM25
    list contributes to the 3-list fusion.

    Regression for A-I1: ``_bm25_candidate_rows`` used to pass the RAW query to
    ``fetch_fts_candidates``; LadybugDB FTS does not split camelCase, so a query
    like ``DistributionChunkService`` matched nothing and BM25 silently no-op'd.
    With the ``build_fts_query`` pre-split, the FTS path matches.

    Exercises the UN-stubbed FTS path: a real LadybugDB ``sym_fts`` index on a
    1-Symbol graph + a real LanceDB index with a matching chunk. Does NOT
    monkeypatch ``fetch_fts_candidates``.

    BM25 contribution is asserted two ways:
      1. Direct (un-stubbed) ``fetch_fts_candidates(g, build_fts_query(name))``
         returns the namesake Symbol with a positive BM25 score; the RAW query
         returns nothing (the bug).
      2. ``run_search`` under the 3-list config produces a row whose
         ``_score_components`` carries ``rrf_raw`` — set only by ``_rrf_merge``
         when the bm25 list joined the fusion. The graph is edge-less, so the
         second list is necessarily bm25; the 2-list baseline has no ``rrf_raw``.
    """
    import uuid

    import lancedb
    from sentence_transformers import SentenceTransformer

    from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    from java_codebase_rag.search import search_lexical
    from java_codebase_rag.search.index_common import SBERT_MODEL
    from java_codebase_rag.search.search_lancedb import TABLES, _query_vector
    from java_codebase_rag.search.search_scoring import (
        BASELINE_2LIST_CONFIG,
        DEFAULT_RANK_CONFIG,
        build_fts_query,
    )

    symbol_name = "DistributionChunkService"  # multi-token camelCase — the trigger
    symbol_fqn = f"smoke.{symbol_name}"

    # 1. Real LadybugDB graph: 1 Symbol + sym_fts BM25 index.
    ladybug_path = tmp_path / "code_graph.lbug"
    fts_ready = _build_minimal_fts_graph(
        ladybug_path, symbol_fqn=symbol_fqn, symbol_name=symbol_name,
    )
    if not fts_ready:
        pytest.skip("FTS extension unavailable in this environment")
    # build_fts_query must actually split the identifier (else the test is moot).
    assert build_fts_query(symbol_name) == "distribution chunk service"

    LadybugGraph.reset_for_path(None)
    g = LadybugGraph.get(str(ladybug_path))

    # 2. Direct (un-stubbed) FTS sanity check: pre-split matches, raw does not.
    pre = search_lexical.fetch_fts_candidates(
        g, build_fts_query(symbol_name), filter=None, path_contains=None,
    )
    assert pre and pre["rows"], "pre-split query must match via the real sym_fts index"
    assert symbol_fqn in {r["fqn"] for r in pre["rows"]}
    assert any(v > 0.0 for v in (pre.get("scores") or {}).values())
    raw = search_lexical.fetch_fts_candidates(
        g, symbol_name, filter=None, path_contains=None,
    )
    assert not raw or not raw.get("rows"), (
        "raw camelCase query should NOT match (FTS does not split camelCase); "
        f"got: {raw}"
    )

    # 3. Real LanceDB index: 1 chunk matching the Symbol's enclosing type.
    uri = str(tmp_path / "ldb")
    model = SentenceTransformer(SBERT_MODEL, device="cpu", trust_remote_code=True)
    text = "service that distributes chunks across the pipeline stages"
    emb = _query_vector(model, text)
    row = {
        "id": str(uuid.uuid4()),
        "filename": f"smoke/{symbol_name}.java",
        "text": text,
        "language": "java",
        "range_start": 0,
        "range_end": 500,
        "start": {"line": 1, "byte_offset": 0},
        "end": {"line": 20, "byte_offset": 400},
        "embedding": emb,
        "package": "smoke",
        "module": "smoke",
        "microservice": "smoke",
        "primary_type_fqn": symbol_fqn,
        "primary_type_kind": "class",
        "role": "SERVICE",
        "annotations_on_type": [],
        "symbols": ["distribute"],
        "ontology_version": ONTOLOGY_VERSION,
        "capabilities": [],
    }
    db = lancedb.connect(uri)
    db.create_table(TABLES["java"], [row], mode="create")

    common = dict(
        uri=uri, table_keys=["java"], limit=5, path_substring=None,
        model_name=SBERT_MODEL, device="cpu", model=model,
        graph_expand=True, expand_depth=1, ladybug_path=str(ladybug_path),
    )
    # 4. 3-list run: bm25 joins the fusion → _rrf_merge runs → rrf_raw appears.
    three = run_search(symbol_name, rank_config=DEFAULT_RANK_CONFIG, **common)
    assert three, "expected non-empty results for the namesake query"
    has_rrf_raw = [
        r for r in three if "rrf_raw" in (r.get("_score_components") or {})
    ]
    assert has_rrf_raw, (
        "3-list run should reflect bm25 fusion (rrf_raw set by _rrf_merge); "
        f"components: {[r.get('_score_components') for r in three]}"
    )
    # rrf_raw > 0 confirms the bm25 list contributed a positive rank contribution.
    assert any(
        float((r.get("_score_components") or {}).get("rrf_raw", 0.0)) > 0.0
        for r in has_rrf_raw
    )
    # The namesake type is present in the 3-list result.
    assert symbol_fqn in {r.get("primary_type_fqn") for r in three}

    # 5. 2-list baseline (vector+graph): edge-less graph → no fusion → no rrf_raw.
    # Drop the cached singleton so the baseline run reopens cleanly.
    LadybugGraph.reset_for_path(None)
    two = run_search(symbol_name, rank_config=BASELINE_2LIST_CONFIG, **common)
    assert all(
        "rrf_raw" not in (r.get("_score_components") or {}) for r in two
    ), "2-list baseline should not produce rrf_raw (graph is edge-less)"

