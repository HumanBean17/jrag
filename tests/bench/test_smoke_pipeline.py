"""Deterministic, API-free run -> grade -> report pipeline smoke test.

Exercised by ``.github/workflows/bench-smoke.yml`` as a per-PR regression gate on
the *harness* (the run->grade->report pipeline), NOT a re-measurement of the
model. Uses a fake ``run_cell_fn`` and canned transcripts/final_answers, so no
``claude -p`` and no judge API call fire. The headline assertion — condition D
(graph) correctness > condition A (lexical) on an interface-impls question — is
the benchmark's central signal, checked here against canned data.
"""

import json
from pathlib import Path

from bench.claude_runner import CellResult
from bench.grade import grade_run
from bench.load_conditions import Condition
from bench.load_corpora import CorpusRecord, IndexManifest
from bench.load_questions import Question
from bench.report import report_main
from bench.run_bench import CellSpec, run_grid


def _question(qid: str) -> Question:
    return Question(
        id=qid,
        corpus="bank-chat-system",
        category="interface-impls",
        difficulty="easy",
        question="Which classes implement Foo?",
        oracle_source="manual",
        claim_refs=["C1"],
        grading="programmatic_set_match",
    )


def _condition(cid: str) -> Condition:
    return Condition(
        id=cid,
        name=f"Condition {cid}",
        allowed_tools=["bash"],
        disallowed_tools=[],
        prompt_file="bench/prompts/a.md",
    )


def _corpus(checkout: str) -> CorpusRecord:
    return CorpusRecord(
        name="bank-chat-system",
        source_kind="local",
        git_url=None,
        commit_sha="abc123",
        local_path=None,
        pinned_repo_sha="abc123",
        checkout_path=checkout,
        index=IndexManifest(
            index_dir="bench/indexes/test",
            ontology_version=19,
            build_id="build-123",
        ),
    )


def _cell_result(spec: CellSpec, final_answer: str, transcript_path: str) -> CellResult:
    return CellResult(
        run_id=f"{spec.question.id}_{spec.condition.id}_{spec.model}_s{spec.seed}",
        question_id=spec.question.id,
        corpus=spec.corpus.name,
        corpus_commit=spec.corpus.commit_sha or spec.corpus.pinned_repo_sha,
        condition=spec.condition.id,
        model=spec.model,
        seed=spec.seed,
        temperature=spec.temperature,
        claude_code_version="fake-1.0",
        ontology_version=spec.corpus.index.ontology_version,
        index_build_id=spec.corpus.index.build_id,
        prompt_hash="sha256:fake",
        started_at="2026-07-22T00:00:00+00:00",
        finished_at="2026-07-22T00:00:01+00:00",
        wall_s=1.0,
        n_turns=3,
        n_tool_calls=2,
        tool_call_breakdown={"Read": 2},
        tokens={"input": 10, "output": 5, "total": 15},
        context_bytes_retrieved=100,
        exit_reason="done",
        final_answer=final_answer,
        transcript_path=transcript_path,
        grade=None,
    )


def test_smoke_pipeline_d_beats_a(tmp_path):
    """run -> grade -> report: condition D (full answer) > A (partial answer).

    A 2-cell grid (conditions A and D on one interface-impls question) is run
    with a fake run_cell_fn, graded programmatically (set_match — no judge), and
    reported. The full answer (D, "Foo Bar") outscores the partial one (A, "Foo").
    """
    q = _question("smoke-impl")
    cond_a = _condition("A")
    cond_d = _condition("D")
    corpus = _corpus(str(tmp_path / "checkout"))
    (tmp_path / "checkout").mkdir(parents=True, exist_ok=True)

    specs = [
        CellSpec(
            question=q, condition=cond_a, corpus=corpus, model="glm-4.7",
            seed=0, temperature=0.0, max_turns=10, repo_root=str(tmp_path),
        ),
        CellSpec(
            question=q, condition=cond_d, corpus=corpus, model="glm-4.7",
            seed=0, temperature=0.0, max_turns=10, repo_root=str(tmp_path),
        ),
    ]

    def fake_run_cell(spec, *, results_transcript_path, **kwargs):
        # grade_run reads the transcript file, so write a canned one.
        Path(results_transcript_path).write_text(
            '{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"Read"}]}}\n'
        )
        # D answers completely (Foo + Bar); A answers partially (Foo only).
        final = "Foo Bar" if spec.condition.id == "D" else "Foo"
        return _cell_result(spec, final, results_transcript_path)

    run_dir = str(tmp_path / "run")
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    run_grid(specs, run_dir, resume=False, run_cell_fn=fake_run_cell)

    # Expected oracle: 2 symbols.
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "smoke-impl.json").write_text(
        json.dumps(
            {
                "question_id": "smoke-impl",
                "expected": {
                    "kind": "symbol_set",
                    "fqns": ["com.example.Foo", "com.example.Bar"],
                    "ids": [],
                },
            }
        )
    )

    graded = str(Path(run_dir) / "graded.jsonl")
    grade_run(
        str(Path(run_dir) / "cells.jsonl"),
        str(expected_dir),
        [q],
        out_path=graded,
    )

    # Report (no plots — matplotlib-free in CI).
    rc = report_main(["--run-dir", run_dir, "--no-plots"])
    assert rc == 0
    assert (Path(run_dir) / "report.md").exists()

    # Headline: D correctness strictly greater than A.
    by_cond = {}
    for line in Path(graded).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cell = json.loads(line)
        by_cond[cell["condition"]] = cell["grade"]["correctness"]
    assert "D" in by_cond and "A" in by_cond
    assert by_cond["D"] == 1.0
    assert by_cond["A"] < 1.0
    assert by_cond["D"] > by_cond["A"]
