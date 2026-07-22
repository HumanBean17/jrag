# jrag Effectiveness Benchmark

A reproducible benchmark that measures **answer effectiveness** (not just
performance) of `jrag` on Claude Code: headless `claude -p` is driven against a
frozen oracle under four tool-isolation conditions, graded by an independent
hybrid oracle, and aggregated into a report. The agent reaches jrag through its
**CLI** (`jrag <verb>` via Bash) — the shipped, recommended surface — not MCP.
Claims C1–C6 are pre-registered in [`PREREGISTRATION.md`](./PREREGISTRATION.md);
the design lives in `docs/superpowers/specs/` (Plan 1 foundation, Plan 2
harness, Plan 3 methodology + reporting, Plan 4 MCP→CLI reframe).

## Conditions (enforced by harness deny-lists + a PATH shim, not prompts)

| Cond | Retrieval | Tools |
|------|-----------|-------|
| **A** | lexical | `Grep`/`Glob`/`Read`/`Bash` (no jrag) |
| **B** | vector-only (graph off) | `jrag search` only (via Bash) |
| **C** | raw agent + shell | `Read`/`Glob`/`Bash` (no `Grep`, no jrag) |
| **D** | jrag full (system under test) | all `jrag` query verbs + read/grep/glob |

All conditions auto-deny the escape/integrity set (`Edit`/`Write`/`NotebookEdit`/
`WebSearch`/`WebFetch`/`Agent`/`Task`). See `conditions.yml`.

### How the CLI surface is isolated

- **Verb-level (B vs D):** `claude_runner.materialize_cli_env` writes a per-cell
  `jrag` wrapper on the spawn `PATH` that exec's the real `.venv/bin/jrag` only
  for the condition's allow-list (B: `search`; D: all query verbs) and exits 2
  for any other verb — a vector-only cell literally cannot run a graph verb.
- **Lexical escape (B):** under `bypassPermissions`, granular `--disallowedTools`
  rules like `Bash(grep *)` *are* enforced (and compound commands are split on
  `&&`/`;`/`|`), so B denies `Grep`/`Glob` plus a `Bash(cat *)`/`Bash(grep *)`/…
  list. The residual is **measured**: `report.py` reports a per-condition lexical
  leakage rate (isolation fidelity as data, not a claim).

## Reproduce (3 commands)

```bash
# 1. run the grid (full: ~1,200 cells; resumable)
.venv/bin/python -m bench.run_bench --models glm-4.7,glm-5.1 --seeds 0,1,2 \
  --max-turns 30 --wall-timeout 900
# 2. grade (programmatic + condition-blinded glm-5.2 judge; emits blinded transcripts)
.venv/bin/python -m bench.grade \
  --cells bench/results/<run>/cells.jsonl \
  --human-labels bench/results/<run>/human_labels.json
# 3. report (markdown tables + CSV + optional plots + lexical leakage)
.venv/bin/python -m bench.report --run-dir bench/results/<run>
```

> Invoke bench scripts as `python -m bench.<name>` (the editable install does not
> expose `bench` as a top-level package; `tests/bench/conftest.py` handles this
> for pytest). Plots need `pip install -e ".[bench]"` (matplotlib); `report.py`
> degrades gracefully without it. Run results live under gitignored
> `bench/results/` (never committed).

## Smoke result — superseded (MCP surface)

The smoke numbers previously shown here (run `20260721T225610/`, D/A/C/B ≈
0.47/0.32/0.31/0.48; κ −0.333 → 1.000 after the Plan-3 fix) were produced on the
**legacy MCP surface** and are **superseded** by Plan 4. They are archived under
`bench/results/_superseded-mcp/` for local reference only. A CLI-surface smoke
is the next step; the methodology demonstrated there (capped→0 short-circuit,
blinded-transcript κ alignment, 0.5 binarization) carries over unchanged. See
`PREREGISTRATION.md` Amendment 2026-07-22 (Plan 4).

## Layout

```
bench/
  run_bench.py     # driver: claude -p per cell -> cells.jsonl + transcripts
  claude_runner.py # subprocess wrapper (turn cap + wall-clock timeout) + PATH shim
  grade.py         # programmatic graders + blinded glm-5.2 judge + Cohen's κ
  report.py        # aggregate graded run -> report.md + results.csv + plots + leakage
  load_conditions.py  # CLI condition model (JRAG_QUERY_VERBS, JRAG_LEXICAL_DENY, shim allow-lists)
  conditions.yml   # the executable isolation spec (A–D tool sets + verb allow-lists)
  corpora.yml      # 3 corpora pinned to SHAs + build-cost (C5)
  questions/*.jsonl  # 50 engineer-phrased golden questions
  oracle/          # jqassistant + jdeps + manual; expected/<qid>.json
  prompts/{A,B,C,D}.md
  PREREGISTRATION.md  # claims C1–C6 + amendments (2026-07-21, 2026-07-22 Plan 3, 2026-07-22 Plan 4)
  results/<run>/      # cells.jsonl, transcript.jsonl, <rid>/bin/jrag (shim), graded.jsonl
```

## TL;DR

Four conditions (lexical / vector-only / raw-agent / jrag-full) drive the
shipped **`jrag` CLI** (via Bash), enforced by harness deny-lists plus a
per-condition PATH shim, graded by an independent oracle (programmatic for
structural questions, condition-blinded glm-5.2 judge for semantic, human κ
gate). Plan 4 reframed the surface from legacy MCP to the CLI (same backend →
claims C1–C6 unchanged) and added verb-level shim isolation + a measured lexical
leakage metric. Plan 3's methodology (capped→0, blinded-transcript κ, 0.5
threshold, wall-clock timeout, atomic writes, deterministic CI smoke) is kept.
The prior MCP smoke is superseded; the full ~1,200-cell CLI run (max-turns 30)
delivers the pre-registered verdicts.
