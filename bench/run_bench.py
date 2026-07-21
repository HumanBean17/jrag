"""Benchmark driver: grid expansion + execution (Plan 2, Tasks 6-10).

Pure grid expansion (Task 6): cross-product of questions × conditions × models × seeds,
each Question paired with its CorpusRecord by name. Raises ConfigError if any question's
corpus has no matching record.
"""

import json
import os

from bench.claude_runner import CellSpec, CellResult, to_cell_jsonl
from bench.load_questions import Question
from bench.load_conditions import Condition
from bench.load_corpora import CorpusRecord


class ConfigError(Exception):
    """Raised when grid configuration is invalid (e.g., unknown corpus)."""


def expand_grid(
    questions: list[Question],
    conditions: list[Condition],
    corpora: list[CorpusRecord],
    models: list[str],
    seeds: list[int],
    temperature: float,
    max_turns: int,
    repo_root: str,
) -> list[CellSpec]:
    """Generate benchmark grid: cross-product of (questions × conditions × models × seeds).

    Args:
        questions: List of questions to benchmark.
        conditions: List of experimental conditions (A/B/C/D).
        corpora: List of corpus records (must include all referenced corpora).
        models: List of model identifiers to test.
        seeds: List of random seeds for reproducibility.
        temperature: Sampling temperature for LLM calls.
        max_turns: Maximum number of assistant turns per cell.
        repo_root: Root directory for the benchmark repository.

    Returns:
        List of CellSpec objects, one per grid cell, in order:
        questions (input order) → conditions (input order) → models → seeds.

    Raises:
        ConfigError: If any question's corpus field has no matching CorpusRecord.
    """
    # Build name -> CorpusRecord map for efficient lookup
    corpus_by_name = {corpus.name: corpus for corpus in corpora}

    cells: list[CellSpec] = []

    for question in questions:
        # Find the matching corpus for this question
        corpus = corpus_by_name.get(question.corpus)
        if corpus is None:
            raise ConfigError(
                f"Question {question.id!r} references unknown corpus {question.corpus!r}. "
                f"Available corpora: {sorted(corpus_by_name.keys())}"
            )

        for condition in conditions:
            for model in models:
                for seed in seeds:
                    cell = CellSpec(
                        question=question,
                        condition=condition,
                        corpus=corpus,
                        model=model,
                        seed=seed,
                        temperature=temperature,
                        max_turns=max_turns,
                        repo_root=repo_root,
                    )
                    cells.append(cell)

    return cells


# --- Task 7: Results write + idempotency/resume (I/O) ---


def run_dir(out_root: str, timestamp: str) -> str:
    """Create and return the run directory path.

    Args:
        out_root: Root directory for results.
        timestamp: Timestamp string for this run.

    Returns:
        Path ``<out_root>/<timestamp>``, created if absent.
    """
    path = os.path.join(out_root, timestamp)
    os.makedirs(path, exist_ok=True)
    return path


def cell_paths(run_dir: str, rid: str) -> tuple[str, str]:
    """Generate per-cell file paths and ensure the cell directory exists.

    Args:
        run_dir: Run directory path.
        rid: Cell run identifier.

    Returns:
        Tuple of ``(transcript_path, cell_jsonl_path)`` where:
        - ``transcript_path`` = ``<run_dir>/<rid>/transcript.jsonl``
        - ``cell_jsonl_path`` = ``<run_dir>/<rid>/cell.jsonl``
    """
    cell_dir = os.path.join(run_dir, rid)
    os.makedirs(cell_dir, exist_ok=True)
    transcript_path = os.path.join(cell_dir, "transcript.jsonl")
    cell_jsonl_path = os.path.join(cell_dir, "cell.jsonl")
    return (transcript_path, cell_jsonl_path)


def write_cell(run_dir: str, result: CellResult) -> None:
    """Write a cell result to both per-cell and aggregate JSONL files.

    Writes (overwrites) ``<run_dir>/<rid>/cell.jsonl`` with a single JSON line,
    and appends the same line to ``<run_dir>/cells.jsonl`` (append-only across
    cells and across re-writes of the same rid).

    Args:
        run_dir: Run directory path.
        result: CellResult to write.
    """
    rid = result.run_id
    cell_dir = os.path.join(run_dir, rid)
    os.makedirs(cell_dir, exist_ok=True)

    cell_jsonl_path = os.path.join(cell_dir, "cell.jsonl")
    cells_jsonl_path = os.path.join(run_dir, "cells.jsonl")

    # Convert to JSONL-serializable dict
    cell_dict = to_cell_jsonl(result)
    json_line = json.dumps(cell_dict)

    # Write (overwrite) per-cell file
    with open(cell_jsonl_path, "w") as f:
        f.write(json_line + "\n")

    # Append to aggregate cells.jsonl
    with open(cells_jsonl_path, "a") as f:
        f.write(json_line + "\n")


def cell_completed(run_dir: str, rid: str) -> bool:
    """Check if a cell has been completed (cell.jsonl exists and is non-empty).

    Args:
        run_dir: Run directory path.
        rid: Cell run identifier.

    Returns:
        True iff ``<run_dir>/<rid>/cell.jsonl`` exists and is non-empty.
    """
    cell_jsonl_path = os.path.join(run_dir, rid, "cell.jsonl")
    if not os.path.exists(cell_jsonl_path):
        return False
    return os.path.getsize(cell_jsonl_path) > 0
