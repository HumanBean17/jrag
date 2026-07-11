"""Unit tests for the pure scoring helpers in ``search_scoring``.

These functions are dependency-free (no lancedb/torch), so this file runs on
every install — including graph-only (macOS Intel), where the
``search_lancedb`` test module is skipped.
"""

from __future__ import annotations

import pytest

from java_codebase_rag.search.search_scoring import (
    _ACTION_VERB_BONUS,
    _HYBRID_SCORE_MAX,
    _ROLE_SCORE_WEIGHTS,
    _SYMBOL_MATCH_BONUS_CAP,
    _TYPE_MATCH_BONUS_CAP,
    declaration_line_number,
    vector_display_score,
)


# ---------- vector_display_score (F6) ----------


def test_vector_display_score_is_bounded_and_decreasing() -> None:
    # Boundaries of the unit-embedding L2 range [0, 2].
    assert vector_display_score(0.0) == pytest.approx(1.0)
    assert vector_display_score(2.0) == pytest.approx(0.0)
    # Strictly decreasing: lower distance -> higher score (rank-monotonic).
    assert vector_display_score(0.2) > vector_display_score(0.5) > vector_display_score(1.0)


def test_vector_display_score_does_not_collapse_past_sqrt2() -> None:
    """The whole point of F6: the old ``1 - d²/2`` map clamps to 0 past √2≈1.414,
    so a weak-but-best match (d≈1.5) showed score=0.000. The new linear map keeps
    it visibly non-zero."""
    assert vector_display_score(1.5) == pytest.approx(0.25)
    assert vector_display_score(1.414) == pytest.approx(0.293, abs=1e-3)
    assert vector_display_score(1.5) > 0.0


def test_vector_display_score_clamps_to_unit_interval() -> None:
    # vector_display_score(d) = clamp01(1 - d/2); nonsensical inputs stay in [0,1].
    assert vector_display_score(3.0) == 0.0  # 1 - 1.5 = -0.5 -> clamps to 0
    assert vector_display_score(-0.5) == 1.0  # 1 + 0.25 = 1.25 -> clamps to 1
    for d in (-1.0, 0.0, 0.7, 1.5, 2.0, 5.0):
        assert 0.0 <= vector_display_score(d) <= 1.0


# ---------- declaration_line_number (F8) ----------


def test_declaration_line_number_finds_primary_type() -> None:
    chunk = "package com.x;\nimport java.util.List;\n\npublic class ChatPort {\n}\n"
    # decl on 1-based line 4 (0-based index 3)
    assert declaration_line_number(chunk, anchor_line=1) == 4
    assert declaration_line_number(chunk, anchor_line=10) == 13  # anchor + offset


def test_declaration_line_number_skips_javadoc_mentioning_type() -> None:
    """Regression for the F8 review finding: a Javadoc line that mentions the
    type name (``* This class Bar handles things.``) must NOT win — the returned
    line is the real ``public class`` declaration."""
    chunk = "/**\n * This class Bar handles things.\n */\npublic class Bar {\n"
    assert declaration_line_number(chunk, anchor_line=1, type_name="Bar") == 4


def test_declaration_line_number_skips_line_and_block_comments() -> None:
    line_cmt = "// class Foo is not it\npublic class Foo {\n"
    assert declaration_line_number(line_cmt, 1, "Foo") == 2
    block_cmt = "/* stale class Baz\n */\npublic class Baz {\n"
    assert declaration_line_number(block_cmt, 1, "Baz") == 3


def test_declaration_line_number_pins_to_named_type_over_nested() -> None:
    """When ``type_name`` is given, a nested/earlier type decl must not win."""
    chunk = "class Helper {\n}\npublic class Primary {\n"
    assert declaration_line_number(chunk, 1, type_name="Primary") == 3
    # Without a pin, the first decl (Helper) wins.
    assert declaration_line_number(chunk, 1) == 1


def test_declaration_line_number_method_only_chunk_keeps_anchor() -> None:
    """A method-only chunk (no type decl) returns the anchor unchanged."""
    chunk = "    public void doWork() {\n        return;\n    }\n"
    assert declaration_line_number(chunk, anchor_line=42) == 42
    # Empty / missing inputs are safe.
    assert declaration_line_number(None, 5) == 5
    assert declaration_line_number("public class X {", None) is None


# ---------- _rrf_max (Task 1) ----------


def test_rrf_max_formula() -> None:
    """Test the RRF max formula: num_lists / (k + 1)."""
    from java_codebase_rag.search.search_scoring import _rrf_max

    # Test the exact formula with different inputs
    assert _rrf_max(2, 60) == pytest.approx(2.0 / 61.0, abs=1e-12)
    assert _rrf_max(3, 60) == pytest.approx(3.0 / 61.0, abs=1e-12)
    assert _rrf_max(3, 30) == pytest.approx(3.0 / 31.0, abs=1e-12)


def test_hybrid_score_max_unchanged() -> None:
    """Verify _HYBRID_SCORE_MAX preserves its exact numeric value after refactor."""
    # Compute the expected value from the same constants used in the definition
    expected = (2.0 / 61.0) + max(_ROLE_SCORE_WEIGHTS.values()) + _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS

    # The refactor must not introduce any numeric drift
    assert _HYBRID_SCORE_MAX == pytest.approx(expected, abs=1e-12)


# ---------- RankConfig (Task 2) ----------


def test_rank_config_defaults() -> None:
    """DEFAULT_RANK_CONFIG ships the 3-list set (bm25 inert until Task 4)
    and the canonical RRF k=60 from the original paper."""
    from java_codebase_rag.search.search_scoring import (
        BASELINE_2LIST_CONFIG,
        DEFAULT_RANK_CONFIG,
    )

    assert DEFAULT_RANK_CONFIG.lists == frozenset({"vector", "graph", "bm25"})
    assert DEFAULT_RANK_CONFIG.rrf_k == 60
    # Eval convenience: omits bm25.
    assert BASELINE_2LIST_CONFIG.lists == frozenset({"vector", "graph"})
    assert BASELINE_2LIST_CONFIG.rrf_k == 60


def test_rank_config_validation() -> None:
    """RankConfig validates its lists set and rrf_k range at construction."""
    from java_codebase_rag.search.search_scoring import RankConfig

    # Missing required "vector" element.
    with pytest.raises(ValueError):
        RankConfig(lists=frozenset({"graph"}))
    # Unknown list name.
    with pytest.raises(ValueError):
        RankConfig(lists=frozenset({"vector", "nope"}))
    # Empty set.
    with pytest.raises(ValueError):
        RankConfig(lists=frozenset())
    # rrf_k below 1.
    with pytest.raises(ValueError):
        RankConfig(lists=frozenset({"vector"}), rrf_k=0)
    # Sanity: a minimal valid config constructs cleanly.
    ok = RankConfig(lists=frozenset({"vector"}), rrf_k=1)
    assert ok.lists == frozenset({"vector"})
    assert ok.rrf_k == 1


def test_rank_config_frozen() -> None:
    """RankConfig is frozen so configs can be safely shared as defaults."""
    from dataclasses import FrozenInstanceError

    from java_codebase_rag.search.search_scoring import DEFAULT_RANK_CONFIG

    with pytest.raises(FrozenInstanceError):
        DEFAULT_RANK_CONFIG.rrf_k = 5  # type: ignore[misc]
