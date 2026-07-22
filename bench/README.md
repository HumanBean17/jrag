# jrag Effectiveness Benchmark

A reproducible benchmark that measures **answer effectiveness** (not just
performance) of `jrag` on Claude Code: headless `claude -p` is driven against a
frozen oracle under four tool-isolation conditions, graded by an independent
hybrid oracle, and aggregated into a report. Claims C1–C6 are pre-registered in
[`PREREGISTRATION.md`](./PREREGISTRATION.md); the design lives in
`docs/superpowers/specs/` (Plan 1 foundation, Plan 2 harness, Plan 3 methodology
+ reporting).

## Conditions (enforced by harness tool deny-lists, not prompts)

| Cond | Retrieval | Tools |
|------|-----------|-------|
| **A** | lexical | `Grep`/`Glob`/`Read`/`Bash` (no MCP) |
| **B** | vector-only (graph off) | jrag `search` only |
| **C** | raw agent + shell | `Read`/`Glob`/`Bash` (no `Grep`, no MCP) |
| **D** | jrag full (system under test) | all 5 jrag tools + read/grep/glob |

All conditions deny the escape/integrity set (`Edit`/`Write`/`NotebookEdit`/
`WebSearch`/`WebFetch`/`Agent`/`Task`). See `conditions.yml`.

## Reproduce (3 commands)

```bash
# 1. run the grid (full: ~1,200 cells; resumable)
.venv/bin/python -m bench.run_bench --models glm-4.7,glm-5.1 --seeds 0,1,2 \
  --max-turns 30 --wall-timeout 900
# 2. grade (programmatic + condition-blinded glm-5.2 judge; emits blinded transcripts)
.venv/bin/python -m bench.grade \
  --cells bench/results/<run>/cells.jsonl \
  --human-labels bench/results/<run>/human_labels.json
# 3. report (markdown tables + CSV + optional plots)
.venv/bin/python -m bench.report --run-dir bench/results/<run>
```

> Invoke bench scripts as `python -m bench.<name>` (the editable install does not
> expose `bench` as a top-level package; `tests/bench/conftest.py` handles this
> for pytest). Plots need `pip install -e ".[bench]"` (matplotlib); `report.py`
> degrades gracefully without it.

## Smoke result — bank-chat / glm-4.7, 16 cells, **methodology-fixed grading**

Re-graded with the Plan-3 fixes (capped cells score a deterministic 0.0;
blinded-transcript κ artifacts emitted; 0.5 binarization threshold). Run dir:
`bench/results/20260721T225610/` (`report.md`, `results.csv`, `*.png`,
`*.blinded.txt`).

**Mean correctness by condition** (n=4 each):

| Condition | Mean | |
|---|---|---|
| B | 0.48 | |
| **D** | **0.47** | jrag full |
| A | 0.32 | |
| C | 0.31 | |

**By category × condition** (the cleaner signal — only cells that *answered*):

| Category | A | B | C | D | signal |
|---|---|---|---|---|---|
| interface-impls | 0.92 | 0.51 | 0.89 | **1.00** | graph perfect where vector-only fails |
| role-listing | 0.36 | 0.42 | 0.36 | **0.89** | graph ≈ 2.4× baselines |
| cross-service | 0.00 | 0.00 | 0.00 | 0.00 | all 4 capped at max-turns 15 |
| semantic | 0.00 | 1.00 | 0.00 | 0.00 | only B answered; A/C/D capped |

**Inter-rater κ (judge ↔ human) = 1.000** (N=4, `bc-sem-01`).

### How to read this

- **The κ fix is demonstrated.** Pre-fix κ was **−0.333**: the judge graded the
  blinded *transcript* (scoring capped cells from their exploration), while human
  labels keyed off `final_answer` (empty for capped cells) — they graded
  different inputs. Plan 3 makes capped cells a deterministic 0.0 (no judge call)
  and aligns the human κ-gate to the same blinded transcript the judge sees
  (emitted as `<run_id>.blinded.txt`). Post-fix κ = 1.000 — judge and human now
  agree on all four cells (three capped → incorrect; one answered → correct).
- **The headline is depressed by the cap rate.** 7/16 cells hit the max-turns=15
  cap (all cross-service; 3/4 semantic). The condition-level ordering is muddied
  because `bc-sem-01` answered only in condition B, handing B the lone semantic
  point. **The full run uses `--max-turns 30`** (Plan 3) so cross-service and
  semantic questions get a fair shot; the category-level signals above (where
  cells answered) already reproduce the PR #460 findings: graph nails
  find-implementations perfectly where vector-only fails, and ≈2.4× baselines on
  role questions.
- **This smoke validates the pipeline**, not the claims. The pre-registered C1–C6
  verdicts come from the full ~1,200-cell run (post-session).

## Layout

```
bench/
  run_bench.py     # driver: claude -p per cell -> cells.jsonl + transcripts
  claude_runner.py # subprocess wrapper (turn cap + wall-clock timeout)
  grade.py         # programmatic graders + blinded glm-5.2 judge + Cohen's κ
  report.py        # aggregate graded run -> report.md + results.csv + plots
  conditions.yml   # the executable isolation spec (A–D tool sets)
  corpora.yml      # 3 corpora pinned to SHAs + build-cost (C5)
  questions/*.jsonl  # 50 engineer-phrased golden questions
  oracle/          # jqassistant + jdeps + manual; expected/<qid>.json
  prompts/{A,B,C,D}.md
  PREREGISTRATION.md  # claims C1–C6 + amendments (2026-07-21, 2026-07-22)
  results/<run>/   # cells.jsonl, transcript.jsonl, graded.jsonl, *.blinded.txt
```

## TL;DR

Four conditions (lexical / vector-only / raw-agent / jrag-full) enforced by
harness deny-lists, graded by an independent oracle (programmatic for structural
questions, condition-blinded glm-5.2 judge for semantic, human κ gate). Plan 3
fixed the broken κ (capped→0, aligned judge/human on the blinded transcript,
0.5 threshold: smoke κ −0.333 → 1.000), added a driver wall-clock timeout,
atomic writes, `report.py`, and a deterministic CI smoke. The smoke reproduces
the headline category signals (graph perfect on find-impls; ≈2.4× on roles); the
full ~1,200-cell run (max-turns 30) delivers the pre-registered verdicts.
