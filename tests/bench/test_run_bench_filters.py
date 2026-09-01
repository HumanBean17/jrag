"""Tests for bench/run_bench.py slice filters (--only-conditions/--only-questions).

The flags are pure pre-filtering ahead of ``expand_grid``: the loaders still
load the full universe (conditions.yml still loads exactly A/B/C/D), the flags
only choose a slice of it. Each test captures the REAL cell list ``run_grid``
receives and asserts on its contents — never on a re-derived count alone.
"""

import pytest

from bench import run_bench
from bench.load_conditions import Condition
from bench.load_corpora import CorpusRecord, IndexManifest
from bench.load_questions import Question


# The loaded fixture universe: 4 questions × 4 conditions (full-grid shape).
QUESTION_IDS = ["bc-impl-01", "bc-sem-01", "bc-tr-01", "bc-role-01"]
CONDITION_IDS = ["A", "B", "C", "D"]


# Minimal Question factory (same shape as test_run_bench.py's).
def make_question(qid: str, corpus: str = "bank-chat-system") -> Question:
    return Question(
        id=qid,
        corpus=corpus,
        category="interface-impls",
        difficulty="easy",
        question=f"Question {qid}",
        oracle_source="programmatic",
        claim_refs=["C1"],
        grading="programmatic_set_match",
    )


# Minimal Condition factory (same shape as test_run_bench.py's).
def make_condition(cid: str) -> Condition:
    return Condition(
        id=cid,
        name=f"Condition {cid}",
        allowed_tools=["bash"],
        disallowed_tools=[],
        prompt_file="bench/prompts/a.md",
    )


# Minimal CorpusRecord factory (same shape as test_run_bench.py's).
def make_corpus(name: str = "bank-chat-system") -> CorpusRecord:
    return CorpusRecord(
        name=name,
        source_kind="git",
        git_url="https://github.com/test/repo",
        commit_sha="abc123",
        local_path=None,
        pinned_repo_sha=None,
        checkout_path="bench/checkouts/test",
        index=IndexManifest(
            index_dir="bench/indexes/test",
            ontology_version=1,
            build_id="build-123",
        ),
    )


def _capture_grid(monkeypatch) -> dict:
    """Stub the loaders with the full 4×4 fixture and capture main's grid.

    ``run_grid`` is replaced by a fake that records the cell list it was handed
    and returns no results, so ``main`` runs to completion without touching
    claude/write_cell. The non-sliced axes are pinned to 1 model × 1 seed so
    expected cell counts stay derivable: questions × conditions × 1 × 1.

    Returns ``{"cells": <list or None>, "models": [...], "seeds": [...]}``;
    ``cells`` stays None until run_grid is actually reached, which lets the
    validation tests prove the driver exited BEFORE expanding/running anything.
    """
    questions = [make_question(qid) for qid in QUESTION_IDS]
    conditions = [make_condition(cid) for cid in CONDITION_IDS]
    corpora = [make_corpus()]

    monkeypatch.setattr(run_bench, "load_corpora", lambda *a, **kw: corpora)
    monkeypatch.setattr(run_bench, "load_conditions", lambda *a, **kw: conditions)
    monkeypatch.setattr(run_bench, "load_all_questions", lambda *a, **kw: questions)

    models = ["glm-4.7"]
    seeds = [0]
    monkeypatch.setattr(run_bench, "SMOKE_MODELS", list(models))
    monkeypatch.setattr(run_bench, "SMOKE_SEEDS", list(seeds))

    captured: dict = {"cells": None, "models": models, "seeds": seeds}

    def fake_run_grid(cells, *args, **kwargs):
        captured["cells"] = cells
        return []

    monkeypatch.setattr(run_bench, "run_grid", fake_run_grid)
    return captured


def test_only_conditions_filters_grid(monkeypatch, tmp_path):
    """--only-conditions A,D → cells whose condition is only A or D."""
    captured = _capture_grid(monkeypatch)

    rc = run_bench.main(["--only-conditions", "A,D", "--out", str(tmp_path)])

    assert rc == 0
    cells = captured["cells"]
    assert cells is not None
    assert {c.condition.id for c in cells} == {"A", "D"}
    # 4 questions × 2 conditions × 1 model × 1 seed.
    assert len(cells) == len(QUESTION_IDS) * 2 * 1 * 1
    # Loaded order is preserved (A before D), not flag-argument order.
    assert cells[0].condition.id == "A"


def test_only_questions_filters_grid(monkeypatch, tmp_path):
    """--only-questions bc-sem-01,bc-tr-01 → only those question ids."""
    captured = _capture_grid(monkeypatch)

    rc = run_bench.main(
        ["--only-questions", "bc-sem-01,bc-tr-01", "--out", str(tmp_path)]
    )

    assert rc == 0
    cells = captured["cells"]
    assert cells is not None
    assert {c.question.id for c in cells} == {"bc-sem-01", "bc-tr-01"}
    assert {c.condition.id for c in cells} == set(CONDITION_IDS)
    # 2 questions × 4 conditions × 1 model × 1 seed.
    assert len(cells) == 2 * len(CONDITION_IDS) * 1 * 1


def test_only_flags_compose(monkeypatch, tmp_path):
    """Both flags together: the #464-gate slice (2 questions × conditions A,D)."""
    captured = _capture_grid(monkeypatch)

    rc = run_bench.main(
        [
            "--only-conditions", "A,D",
            "--only-questions", "bc-sem-01,bc-tr-01",
            "--out", str(tmp_path),
        ]
    )

    assert rc == 0
    cells = captured["cells"]
    assert cells is not None
    assert {c.question.id for c in cells} == {"bc-sem-01", "bc-tr-01"}
    assert {c.condition.id for c in cells} == {"A", "D"}
    assert len(cells) == 2 * 2 * 1 * 1


def test_unknown_condition_id_exits(monkeypatch, tmp_path):
    """--only-conditions A,E → SystemExit naming E and the valid ids, no cells run."""
    captured = _capture_grid(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        run_bench.main(["--only-conditions", "A,E", "--out", str(tmp_path)])

    # SystemExit carries the message (raise SystemExit(msg)), not just a code —
    # `str()` of argparse's SystemExit(2) would be "2" and fail these asserts.
    msg = str(excinfo.value)
    assert "E" in msg
    for cid in CONDITION_IDS:
        assert f"'{cid}'" in msg
    # Exited before the grid was expanded/handed to run_grid.
    assert captured["cells"] is None


def test_unknown_question_id_exits(monkeypatch, tmp_path):
    """--only-questions with an unloaded id → SystemExit the same way."""
    captured = _capture_grid(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        run_bench.main(
            ["--only-questions", "bc-sem-01,no-such-q", "--out", str(tmp_path)]
        )

    msg = str(excinfo.value)
    assert "no-such-q" in msg
    for qid in QUESTION_IDS:
        assert qid in msg
    assert captured["cells"] is None


def test_no_flags_runs_all(monkeypatch, tmp_path):
    """Default (no slice flags): full grid, behavior unchanged."""
    captured = _capture_grid(monkeypatch)

    rc = run_bench.main(["--out", str(tmp_path)])

    assert rc == 0
    cells = captured["cells"]
    assert cells is not None
    expected = (
        len(QUESTION_IDS) * len(CONDITION_IDS)
        * len(captured["models"]) * len(captured["seeds"])
    )
    assert len(cells) == expected
    assert {c.question.id for c in cells} == set(QUESTION_IDS)
    assert {c.condition.id for c in cells} == set(CONDITION_IDS)
