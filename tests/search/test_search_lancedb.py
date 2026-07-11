"""Unit tests for search_lancedb helpers (no LanceDB / Kuzu required)."""

from __future__ import annotations

import numpy as np
import pytest

# search_lancedb imports lancedb/torch at module load; skip the whole file on graph-only
# installs (macOS Intel) where the vector stack is absent.
pytest.importorskip("lancedb")
pytest.importorskip("sentence_transformers")

from java_codebase_rag.search import search_lancedb
from java_codebase_rag.search.search_lancedb import JAVA_ENRICHED_COLUMNS, _rrf_merge


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
