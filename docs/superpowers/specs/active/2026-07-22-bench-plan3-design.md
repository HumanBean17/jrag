# Benchmark — Methodology, Reporting & At-Scale Run (Plan 3 / Phases 4-5)

- **Date:** 2026-07-22
- **Status:** Active (design approved, pre-implementation)
- **Depends on:** Plan 2 (PR #460, merged) — the `run_bench.py` driver, `claude_runner.py` wrapper, and `grade.py` graders.

## Motivation (the gap Plan 2 left)

Plan 2 delivered a working harness and a 16-cell smoke grid graded on bank-chat / glm-4.7
(D=0.722 > A=0.547 > C=0.526 > B=0.458). Its whole-branch review flagged a specific carry-over list
(tracked in `.superpowers/sdd/progress.md` under "DEFERRED TO PLAN 3"). The load-bearing item is a
**broken κ**: smoke Cohen's κ = −0.333 (N=4) because the LLM judge grades the *blinded transcript* while
the human κ-gate labels off *final_answer* — they grade different inputs, so κ is meaningless. The other
deferred items are robustness gaps (no driver wall-clock timeout; non-atomic per-cell writes; a brittle
`==1.0` binarization), the missing reporting layer (`report.py`, plots, the published report), and the
absence of CI. Plan 3 closes all of these and then executes the full pre-registered grid.

## Scope

**Plan 3 in full = Phases 4 (full run) + 5 (ablations + report) + the deferred methodology/tooling fixes.**

This design covers both, but execution is sliced:

- **This session (T0-T7):** SDD docs; methodology fixes (κ, driver timeout, atomic writes, cap policy);
  `report.py`; deterministic CI smoke; a report regenerated from the existing 16-cell smoke. **No new
  big run.**
- **Post-session (same plan):** the full ~1,200-cell run (max-turns 30, wall-timeout on), ablation D₃,
  the final published `bench/README.md`, archiving this spec/plan → ADR, and the PR.

Two user decisions are locked and drive the design: **run scale = full 3-corpus (~1,200 cells)**, and
**cap policy = raise max-turns 15→30 + cap sentinel**.

## Decisions locked in this plan (resolved, not deferred)

1. **κ input alignment preserves the pre-registered judge design.** The spec mandates the judge sees the
   condition-blinded transcript; Plan 3 does *not* change that. Instead the human κ-gate is aligned to
   label from the **same blinded transcript** the judge graded (emitted as an artifact). κ then measures
   inter-rater agreement on identical evidence — which is what κ is for.
2. **Capped cells are a structural failure, scored deterministically.** `exit_reason=="cap"` short-circuits
   `grade_cell` to `Grade(0.0)` with no grader/judge call, and `run_cell` writes a self-documenting
   sentinel into `final_answer` (non-null). This kills both the "judge scores a no-answer cell from its
   transcript exploration" artifact and the null-`final_answer` data hole, and it spends zero judge budget
   on capped cells.
3. **Binarization threshold replaces brittle `==1.0`.** `_grade_to_judge_label` uses
   `correctness >= JUDGE_CORRECT_THRESHOLD` (constant, default **0.5**); a 0.90 answer is "correct."
4. **A fourth `exit_reason="timeout"` is added** (Plan 2 locked done|cap|error). Driver wall-clock
   timeout is distinct from the turn cap (turns vs wall-time; cap = too much work, timeout = stall/hang).
   Precedence: cap > timeout > error > done.
5. **Atomicity is applied to the per-cell file** (the resume gate via `cell_completed`), not the aggregate
   append — `run_grid` is sequential and the per-cell file is the source of truth. `temp` + `fsync` +
   `os.replace` closes the "partial write reads as complete" gap.
6. **`report.py` plots are matplotlib, gracefully optional.** Tables + CSV always emit; plots emit only
   when matplotlib imports, else a warning. The real-run report installs matplotlib.
7. **CI smoke is deterministic and API-free.** Real `claude -p` runs are non-deterministic and need paid
   API credentials — unsuitable for per-PR CI. The workflow runs a fake-claude-driven pipeline test
   (run → grade → report) that asserts the headline D>A signal on canned transcripts. The real ~8-question
   smoke is a manual/nightly stretch, documented.
8. **Weighted κ is documented as stretch, not built.** It only diverges from unweighted κ when *both*
   raters use ≥3 ordinal categories; with binary human labels it collapses to unweighted. Built only if
   human labels adopt a graded scale.

## Methodology architecture (the κ fix)

Three localized changes in `bench/grade.py` + `bench/claude_runner.py`:

- **Cap sentinel + short-circuit** — `run_cell` sets `final_answer` to
  `[BENCH_CAP: reached max-turns {N} without a final result]` when capped; `grade_cell` returns
  `Grade(0.0, method=<method>, detail={"reason": "cap"})` for `exit_reason=="cap"` before dispatch.
- **Blinded-transcript artifacts** — `grade_run` writes `<run_dir>/<run_id>.blinded.txt` (the exact text
  passed to `judge_answer`) for every judged cell. The human κ-gate reads these.
- **Threshold binarization** — `JUDGE_CORRECT_THRESHOLD = 0.5`; `_grade_to_judge_label` uses `>=`.

`cohen_kappa` itself is unchanged (simple unweighted κ); the fix is entirely in *what the two label
streams mean*, not in the κ formula.

## Reporting architecture (`bench/report.py`, greenfield)

Consumes one run dir (`graded.jsonl` + sibling `cells.jsonl` for raw efficiency metrics) and emits:

- `report.md` — markdown tables: condition × category (mean correctness / steps / tokens / context bytes);
  condition × question; cross-service category by condition (C3); model × condition correctness delta (C6);
  cap/error counts; κ; headline numbers; "reproduce in 3 commands."
- `results.csv` — one flat row per graded cell (all 24 cell fields + grade fields), for external analysis.
- `*.png` plots (optional) — correctness-by-category bar; correctness-vs-tokens scatter (the quality-per-cost
  headline); steps-to-answer by condition; model-tier deltas. Palette/method per the `dataviz` skill.

CLI: `--run-dir <dir> [--out <dir>]`. Defaults write alongside the run. Plotting degrades gracefully.

## CI architecture (`.github/workflows/bench-smoke.yml`)

Mirrors `.github/workflows/test.yml` (paths-filter, `setup-python@v5` 3.11, `pip install -e ".[dev]"`).
The bench job runs `tests/bench/test_smoke_pipeline.py`: a fake-claude 2-cell grid (conditions A and D on
one question) → `grade.py` with a monkeypatched judge → `report.py` → assert D-correctness > A-correctness
on the canned transcripts. Deterministic, no API, fast. This is a regression gate on the *harness*, not a
re-measurement of the model.

## Amendments to `PREREGISTRATION.md` (dated 2026-07-22)

One `## Amendment 2026-07-22` section recording: (a) the κ methodology fix (judge + human both grade the
blinded transcript; capped cells score 0.0 via short-circuit; `final_answer` cap sentinel;
`JUDGE_CORRECT_THRESHOLD=0.5`); (b) the new `exit_reason="timeout"` value and the driver wall-clock
timeout; (c) the max-turns raise 15→30 for the full run; (d) the deterministic-CI decision (real smoke is
manual/nightly); (e) weighted-κ stretch status.

## Acceptance (definition of done — this session's slice)

- `tests/bench/` green, ~114 → ~130+; each new test RED→GREEN with fake-claude/monkeypatch fixtures only.
- Existing 16-cell run re-graded with the fixed `grade.py`; `report.py` emits `bench/README.md` +
  `results.csv` + plots; headline means within expectation (D≈0.72 / A≈0.55 / C≈0.53 / B≈0.46; per-cell
  grades unchanged — only κ and capped handling shift).
- `bench-smoke.yml`'s pipeline test passes locally (deterministic).
- PREREG amendment committed; SDD spec+plan committed.

## Repo layout (Plan 3 deliverables)

```
bench/
  report.py                     # NEW — aggregate -> report.md + results.csv + plots
  grade.py                      # MODIFY — cap short-circuit, blinded artifacts, threshold
  claude_runner.py              # MODIFY — cap sentinel, wall-clock timeout, exit_reason=timeout
  run_bench.py                  # MODIFY — atomic per-cell write, --wall-timeout CLI
  PREREGISTRATION.md            # MODIFY — Amendment 2026-07-22
  README.md                     # NEW (Task 7) — report from the 16-cell smoke
  results/<run>/                # + <run_id>.blinded.txt artifacts (judged cells)
.github/workflows/
  bench-smoke.yml               # NEW — deterministic pipeline smoke
tests/bench/
  test_smoke_pipeline.py        # NEW — fake-claude run->grade->report, assert D>A
docs/superpowers/{specs,plans}/active/
  2026-07-22-bench-plan3-design.md   # THIS FILE
  2026-07-22-bench-plan3.md          # the task breakdown
```

## TL;DR

Plan 3 finishes the effectiveness benchmark. The crux is fixing the broken κ (judge graded blinded
transcript, human labeled final_answer → κ=−0.333): align the human κ-gate to the same blinded transcript
the judge sees (emitted as an artifact), make capped cells score a deterministic 0.0 via short-circuit +
cap sentinel, and replace the brittle `==1.0` binarization with a 0.5 threshold — all without changing
the pre-registered judge design. Plus a driver wall-clock timeout (`exit_reason="timeout"`), atomic
per-cell writes, a greenfield `report.py` (tables + CSV + optional matplotlib plots), and a deterministic
API-free CI smoke. This session lands those + a report from the existing 16-cell smoke; the full
~1,200-cell run, ablation D₃, and the final published report follow on the fixed harness.
