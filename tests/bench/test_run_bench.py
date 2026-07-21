"""Tests for bench/run_bench.py grid expansion."""

import json
import os
import tempfile
import pytest
from dataclasses import dataclass
from datetime import datetime, timezone

from bench.claude_runner import CellSpec, CellResult
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


# --- Task 7 tests: Results write + idempotency/resume ---


def make_cell_result(
    run_id: str = "bc-impl-01_D_glm-4.7_s0",
    transcript_path: str = "/tmp/transcript.jsonl",
) -> CellResult:
    """Minimal CellResult factory for tests."""
    return CellResult(
        run_id=run_id,
        question_id="bc-impl-01",
        corpus="bank-chat-system",
        corpus_commit="abc123",
        condition="D",
        model="glm-4.7",
        seed=0,
        temperature=0.7,
        claude_code_version="1.2.3",
        ontology_version=1,
        index_build_id="build-123",
        prompt_hash="sha256:1234",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        wall_s=10.5,
        n_turns=5,
        n_tool_calls=3,
        tool_call_breakdown={"bash": 2, "read": 1},
        tokens={"input": 100, "output": 50, "total": 150},
        context_bytes_retrieved=1000,
        exit_reason="done",
        final_answer="The answer",
        transcript_path=transcript_path,
        grade=None,
    )


def test_write_cell_creates_files():
    """write_cell creates <rid>/cell.jsonl and appends to cells.jsonl."""
    from bench.run_bench import write_cell, run_dir

    with tempfile.TemporaryDirectory() as tmp:
        out_root = tmp
        timestamp = "20250101_120000"
        rd = run_dir(out_root, timestamp)

        # Create a real transcript file
        transcript_path = os.path.join(rd, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            f.write('{"type": "result", "result": "test"}\n')

        result = make_cell_result(
            run_id="bc-impl-01_D_glm-4.7_s0",
            transcript_path=transcript_path,
        )

        write_cell(rd, result)

        # Check <rid>/cell.jsonl exists with one valid JSON line
        cell_path = os.path.join(rd, "bc-impl-01_D_glm-4.7_s0", "cell.jsonl")
        assert os.path.exists(cell_path)
        with open(cell_path) as f:
            content = f.read()
            lines = content.strip().split("\n")
            assert len(lines) == 1
            cell_data = json.loads(lines[0])
            assert cell_data["run_id"] == "bc-impl-01_D_glm-4.7_s0"

        # Check cells.jsonl exists with one line
        cells_jsonl = os.path.join(rd, "cells.jsonl")
        assert os.path.exists(cells_jsonl)
        with open(cells_jsonl) as f:
            content = f.read()
            lines = content.strip().split("\n")
            assert len(lines) == 1
            cells_data = json.loads(lines[0])
            assert cells_data["run_id"] == "bc-impl-01_D_glm-4.7_s0"


def test_cell_completed_gate():
    """cell_completed is False before write_cell, True after."""
    from bench.run_bench import write_cell, cell_completed, run_dir

    with tempfile.TemporaryDirectory() as tmp:
        out_root = tmp
        timestamp = "20250101_120000"
        rd = run_dir(out_root, timestamp)
        rid = "bc-impl-01_D_glm-4.7_s0"

        # Before write_cell: False
        assert not cell_completed(rd, rid)

        # Create transcript and write
        transcript_path = os.path.join(rd, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            f.write('{"type": "result", "result": "test"}\n')

        result = make_cell_result(
            run_id=rid,
            transcript_path=transcript_path,
        )
        write_cell(rd, result)

        # After write_cell: True
        assert cell_completed(rd, rid)


def test_write_cell_idempotent_overwrite():
    """write_cell overwrites per-cell file but appends to cells.jsonl."""
    from bench.run_bench import write_cell, run_dir

    with tempfile.TemporaryDirectory() as tmp:
        out_root = tmp
        timestamp = "20250101_120000"
        rd = run_dir(out_root, timestamp)
        rid = "bc-impl-01_D_glm-4.7_s0"

        # Create transcript
        transcript_path = os.path.join(rd, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            f.write('{"type": "result", "result": "test"}\n')

        # First write
        result1 = make_cell_result(
            run_id=rid,
            transcript_path=transcript_path,
        )
        result1 = result1.__class__(
            run_id=rid,
            question_id=result1.question_id,
            corpus=result1.corpus,
            corpus_commit=result1.corpus_commit,
            condition=result1.condition,
            model=result1.model,
            seed=result1.seed,
            temperature=result1.temperature,
            claude_code_version=result1.claude_code_version,
            ontology_version=result1.ontology_version,
            index_build_id=result1.index_build_id,
            prompt_hash=result1.prompt_hash,
            started_at=result1.started_at,
            finished_at="2025-01-01T12:00:00+00:00",
            wall_s=result1.wall_s,
            n_turns=result1.n_turns,
            n_tool_calls=result1.n_tool_calls,
            tool_call_breakdown=result1.tool_call_breakdown,
            tokens=result1.tokens,
            context_bytes_retrieved=result1.context_bytes_retrieved,
            exit_reason=result1.exit_reason,
            final_answer="First answer",
            transcript_path=result1.transcript_path,
            grade=None,
        )
        write_cell(rd, result1)

        # Second write with different content
        result2 = make_cell_result(
            run_id=rid,
            transcript_path=transcript_path,
        )
        result2 = result2.__class__(
            run_id=rid,
            question_id=result2.question_id,
            corpus=result2.corpus,
            corpus_commit=result2.corpus_commit,
            condition=result2.condition,
            model=result2.model,
            seed=result2.seed,
            temperature=result2.temperature,
            claude_code_version=result2.claude_code_version,
            ontology_version=result2.ontology_version,
            index_build_id=result2.index_build_id,
            prompt_hash=result2.prompt_hash,
            started_at=result2.started_at,
            finished_at="2025-01-01T12:00:01+00:00",
            wall_s=result2.wall_s,
            n_turns=result2.n_turns,
            n_tool_calls=result2.n_tool_calls,
            tool_call_breakdown=result2.tool_call_breakdown,
            tokens=result2.tokens,
            context_bytes_retrieved=result2.context_bytes_retrieved,
            exit_reason=result2.exit_reason,
            final_answer="Second answer",
            transcript_path=result2.transcript_path,
            grade=None,
        )
        write_cell(rd, result2)

        # Check <rid>/cell.jsonl has single line (latest content)
        cell_path = os.path.join(rd, rid, "cell.jsonl")
        with open(cell_path) as f:
            content = f.read()
            lines = content.strip().split("\n")
            assert len(lines) == 1
            cell_data = json.loads(lines[0])
            assert cell_data["final_answer"] == "Second answer"

        # Check cells.jsonl has two lines (append-only)
        cells_jsonl = os.path.join(rd, "cells.jsonl")
        with open(cells_jsonl) as f:
            content = f.read()
            lines = content.strip().split("\n")
            assert len(lines) == 2
            first_data = json.loads(lines[0])
            second_data = json.loads(lines[1])
            assert first_data["final_answer"] == "First answer"
            assert second_data["final_answer"] == "Second answer"
