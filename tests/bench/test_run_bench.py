"""Tests for bench/run_bench.py grid expansion."""

import pytest
from dataclasses import dataclass

from bench.claude_runner import CellSpec
from bench.load_questions import Question
from bench.load_conditions import Condition
from bench.load_corpora import CorpusRecord, IndexManifest


# Minimal Question factory
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


# Minimal Condition factory
def make_condition(cid: str) -> Condition:
    return Condition(
        id=cid,
        name=f"Condition {cid}",
        mcp_servers=[],
        allowed_tools=["bash"],
        disallowed_tools=[],
        prompt_file="bench/prompts/a.md",
    )


# Minimal CorpusRecord factory
def make_corpus(name: str, checkout_path: str = "bench/checkouts/test") -> CorpusRecord:
    return CorpusRecord(
        name=name,
        source_kind="git",
        git_url="https://github.com/test/repo",
        commit_sha="abc123",
        local_path=None,
        pinned_repo_sha=None,
        checkout_path=checkout_path,
        index=IndexManifest(
            index_dir="bench/indexes/test",
            ontology_version=1,
            build_id="build-123",
        ),
    )


def test_expand_grid_smoke_dimensions():
    """4 questions × 4 conditions × 1 model × 1 seed = 16 cells."""
    from bench.run_bench import expand_grid

    questions = [
        make_question("q1"),
        make_question("q2"),
        make_question("q3"),
        make_question("q4"),
    ]
    conditions = [
        make_condition("A"),
        make_condition("B"),
        make_condition("C"),
        make_condition("D"),
    ]
    corpora = [make_corpus("bank-chat-system")]
    models = ["glm-4.7"]
    seeds = [0]
    temperature = 0.7
    max_turns = 50
    repo_root = "/tmp/test"

    cells = expand_grid(questions, conditions, corpora, models, seeds, temperature, max_turns, repo_root)

    # 4 × 4 × 1 × 1 = 16
    assert len(cells) == 16
    assert all(isinstance(cell, CellSpec) for cell in cells)

    # First element: q1, condition A, glm-4.7, seed 0
    first = cells[0]
    assert first.question.id == "q1"
    assert first.condition.id == "A"
    assert first.model == "glm-4.7"
    assert first.seed == 0
    assert first.corpus.name == "bank-chat-system"


def test_expand_grid_unknown_corpus_raises():
    """Question with unknown corpus raises ConfigError."""
    from bench.run_bench import expand_grid, ConfigError

    questions = [make_question("q1", corpus="unknown-corpus")]
    conditions = [make_condition("A")]
    corpora = [make_corpus("bank-chat-system")]
    models = ["glm-4.7"]
    seeds = [0]
    temperature = 0.7
    max_turns = 50
    repo_root = "/tmp/test"

    with pytest.raises(ConfigError) as excinfo:
        expand_grid(questions, conditions, corpora, models, seeds, temperature, max_turns, repo_root)

    assert "unknown-corpus" in str(excinfo.value)
