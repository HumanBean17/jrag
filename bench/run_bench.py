"""Benchmark driver: grid expansion + execution (Plan 2, Tasks 6-10).

Pure grid expansion (Task 6): cross-product of questions × conditions × models × seeds,
each Question paired with its CorpusRecord by name. Raises ConfigError if any question's
corpus has no matching record.
"""

import argparse
import json
import os
import sys
import time

from bench import claude_runner
from bench.claude_runner import CellSpec, CellResult, run_id, to_cell_jsonl
from bench.load_conditions import load_conditions
from bench.load_corpora import load_corpora
from bench.load_questions import load_all_questions
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
    # Reuse ``cell_paths`` so the per-cell directory + jsonl path stay in sync
    # with the rest of the driver (run_grid, cell_completed). Discards the
    # transcript_path return value (write_cell does not write the transcript).
    _, cell_jsonl_path = cell_paths(run_dir, rid)
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


# --- Task 8: CLI orchestration (run_grid + main) ---

# Smoke config: a fast, cheap end-to-end shakedown (4 questions × 4 conditions
# × 1 model × 1 seed = 16 cells). Same bank-chat-system corpus, real prompts,
# real grid — just a small slice.
SMOKE_QUESTIONS = ["bc-impl-01", "bc-role-01", "bc-cs-01", "bc-sem-01"]
SMOKE_MODELS = ["glm-4.7"]
SMOKE_SEEDS = [0]
SMOKE_TEMPERATURE = 0.0

DEFAULT_MAX_TURNS = 15


def run_grid(
    cells: list[CellSpec],
    run_dir_path: str,
    *,
    resume: bool,
    run_cell_fn=claude_runner.run_cell,
) -> list[CellResult]:
    """Execute every cell, optionally skipping already-completed ones.

    Per cell:
        1. ``rid = run_id(cell)``
        2. if ``resume`` and ``cell_completed(run_dir_path, rid)``: skip
        3. else: resolve the transcript path, call ``run_cell_fn(cell, ...)``,
           and persist the result via ``write_cell``.

    ``run_cell_fn`` is a DI seam: it defaults to ``claude_runner.run_cell`` (the
    module attribute, evaluated at definition time). Callers that need to
    intercept — e.g. ``main`` under a test monkeypatch — should pass
    ``run_cell_fn=claude_runner.run_cell`` so the attribute is re-resolved at
    call time.

    Args:
        cells: Cells to execute (in order).
        run_dir_path: Run directory (created upstream by ``run_dir``).
        resume: If True, skip cells whose ``cell.jsonl`` already exists.
        run_cell_fn: Callable with the ``run_cell`` signature.

    Returns:
        List of ``CellResult`` for the cells actually executed (skipped cells
        are absent).
    """
    results: list[CellResult] = []
    for cell in cells:
        rid = run_id(cell)
        if resume and cell_completed(run_dir_path, rid):
            continue
        transcript_path, _ = cell_paths(run_dir_path, rid)
        result = run_cell_fn(cell, results_transcript_path=transcript_path)
        write_cell(run_dir_path, result)
        results.append(result)
    return results


def _resolve_run_dir(out: str, resume: bool) -> str:
    """Pick the run directory for this invocation.

    When ``resume`` is False (default): always create a fresh
    ``<out>/<timestamp>`` subdir (current behavior).

    When ``resume`` is True, target an existing run dir so completed cells
    can be skipped:
      - if ``<out>/cells.jsonl`` exists → reuse ``out`` directly (operator
        passed an existing run dir);
      - elif one or more ``<out>/<sub>/cells.jsonl`` exist → reuse the
        lexicographically-greatest such ``<sub>`` (latest run under ``out``);
      - else → fall back to creating a fresh ``<out>/<timestamp>`` (nothing
        to resume).
    """
    if not resume:
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        return run_dir(out, timestamp)

    out_cells = os.path.join(out, "cells.jsonl")
    if os.path.exists(out_cells):
        return out

    # Scan <out>/<sub>/cells.jsonl for the lexicographically-greatest sub.
    candidates: list[str] = []
    if os.path.isdir(out):
        for entry in os.listdir(out):
            sub = os.path.join(out, entry)
            if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "cells.jsonl")):
                candidates.append(entry)
    if candidates:
        latest = sorted(candidates)[-1]
        return os.path.join(out, latest)

    # No resume target — fresh timestamp subdir.
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    return run_dir(out, timestamp)


def _parse_csv_list(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_csv_ints(value: str | None) -> list[int]:
    """Parse a comma-separated string of ints.

    Used as the argparse ``type=`` callable for ``--seeds`` so that a non-int
    item produces a clean argparse usage error (stderr message + SystemExit(2))
    instead of a bare ``ValueError`` traceback. argparse catches
    ``ArgumentTypeError`` / ``ValueError`` from a ``type=`` callable and
    converts it via ``parser.error(...)``.

    Args:
        value: Comma-separated int string (e.g. ``"0,1,2"``), or ``None``.

    Returns:
        List of ints. Empty list for ``None`` or an empty string.
    """
    if value is None:
        return []
    out: list[int] = []
    for item in _parse_csv_list(value):
        try:
            out.append(int(item))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid int in --seeds: {item!r}"
            ) from exc
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: load config, expand the grid, run all cells.

    See module docstring + task brief for flag semantics. ``--smoke`` overrides
    ``--models``/``--seeds``/``--temperature`` with the SMOKE_* constants and
    filters the loaded questions to ``SMOKE_QUESTIONS``.
    """
    parser = argparse.ArgumentParser(
        description="jrag effectiveness benchmark driver",
    )
    parser.add_argument("--corpora", default="bench/corpora.yml")
    parser.add_argument("--conditions", default="bench/conditions.yml")
    parser.add_argument("--questions-glob", default="bench/questions/*.jsonl")
    parser.add_argument("--out", default="bench/results")
    parser.add_argument("--models", default=None,
                        help="Comma-separated model ids (pinned to SMOKE_MODELS by --smoke)")
    parser.add_argument("--seeds", default=None, type=_parse_csv_ints,
                        help="Comma-separated int seeds (pinned to SMOKE_SEEDS by --smoke)")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--resume", action="store_true",
                        help="Skip cells whose cell.jsonl already exists")
    parser.add_argument("--smoke", action="store_true",
                        help="Pin model/seed/temperature and filter questions to SMOKE_QUESTIONS")
    args = parser.parse_args(argv)

    if args.smoke:
        models = list(SMOKE_MODELS)
        seeds = list(SMOKE_SEEDS)
        temperature = SMOKE_TEMPERATURE
    else:
        models = _parse_csv_list(args.models) if args.models else list(SMOKE_MODELS)
        # ``--seeds`` uses ``type=_parse_csv_ints`` so argparse already converted
        # it (or errored cleanly). ``None`` means the flag was absent → SMOKE default.
        seeds = args.seeds if args.seeds is not None else list(SMOKE_SEEDS)
        temperature = args.temperature

    # Plan-1 loaders: typed records, registry/isolation invariants enforced.
    corpora = load_corpora(args.corpora)
    conditions = load_conditions(args.conditions)
    questions = load_all_questions(args.questions_glob, corpora_path=args.corpora)

    if args.smoke:
        smoke_set = set(SMOKE_QUESTIONS)
        questions = [q for q in questions if q.id in smoke_set]

    cells = expand_grid(
        questions,
        conditions,
        corpora,
        models,
        seeds,
        temperature,
        args.max_turns,
        os.getcwd(),
    )

    rd = _resolve_run_dir(args.out, args.resume)

    # Pass `run_cell_fn=claude_runner.run_cell` EXPLICITLY so the attribute is
    # re-resolved at call time. This lets tests monkeypatch the module attribute
    # (the default-arg form binds at definition time and would bypass the patch).
    results = run_grid(
        cells,
        rd,
        resume=args.resume,
        run_cell_fn=claude_runner.run_cell,
    )

    skipped = len(cells) - len(results)
    print(
        f"run: {len(results)} / skipped: {skipped} / total: {len(cells)} "
        f"-> {rd}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
