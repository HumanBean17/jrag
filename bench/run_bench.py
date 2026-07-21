"""Benchmark driver: grid expansion + execution (Plan 2, Tasks 6-10).

Pure grid expansion (Task 6): cross-product of questions × conditions × models × seeds,
each Question paired with its CorpusRecord by name. Raises ConfigError if any question's
corpus has no matching record.
"""

from bench.claude_runner import CellSpec
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
