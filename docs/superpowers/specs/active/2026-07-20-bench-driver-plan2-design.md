# Benchmark — Agent Harness Driver (Plan 2 / Phase 2)

- **Date:** 2026-07-20
- **Status:** Active (design approved, pre-implementation)
- **Scope:** The `run_bench.py` driver that executes the benchmark grid one headless `claude -p` cell at a time and emits the JSONL evidence that `grade.py` (Plan 2b) will consume. Grading, the LLM judge, ablations, and the full run are explicitly out of scope.
- **Predecessors:** Plan 1 (frozen corpora, `conditions.yml`, oracle, 50 questions, `PREREGISTRATION.md`) — merged. Phase-0 de-risk spikes — resolved in `bench/PHASE0_FINDINGS.md` (commit `227e413`).

## Motivation

Plan 1 froze the ground truth; nothing has yet run an agent against it. Plan 2 builds the auditable harness that does — and bakes in the four corrections the de-risk spikes forced (no `--max-turns`; `--verbose` + `stdin=DEVNULL`; cwd via subprocess not `--add-dir`; capability-vs-tool-name enforcement). The smoke cell already proved the thesis end-to-end (condition-D `bc-impl-01` → 12/12 FQN match with the jqassistant-grounded oracle in a $0.17 cell); Plan 2 turns that one probe into a repeatable, idempotent driver over the grid.

## Scope

**Numbering:** the roadmap's Plan 2 (Phases 2-3) is split here — this spec is **Plan 2a (Phase 2: the driver)**; grading (Phase 3) is **Plan 2b**; the full run, ablations, and report remain **Plan 3** (Phases 4-6).

**In:** `run_bench.py` + `claude_runner.py`; the JSONL cell schema (contract); the condition-C relabel + `PREREGISTRATION.md` amendment; a 12-cell smoke grid on bank-chat producing real JSONL + transcripts; pytest for the driver.

**Out (deferred):** `grade.py`, the glm-5.2 blinded judge, the κ harness (Plan 2b); ablation conditions D₂/D₃ (Plan 3); the ~1,200-run grid, parallelism, model-tier C6 slicing, `report.py`, CI smoke workflow (Plan 3).

## Driver architecture

The driver is **condition-agnostic**: each cell is assembled declaratively from Plan 1's loaders, so ablation conditions added to `conditions.yml` / `corpora.yml` in Plan 3 require no driver change.

Per cell `(question, condition, model, seed)`:

- `bench.load_conditions.to_flags(cond)` → allowed/disallowed tools, MCP-config arg, condition prompt.
- `bench.load_corpora` → corpus checkout path (the subprocess **cwd**) + index dir.
- `bench.load_questions` → question text (the `-p` prompt).
- MCP config materialized per cell from the `bench/mcp/jrag.json` template (`${JRAG_INDEX_DIR}` / `${JRAG_SOURCE_ROOT}` → absolute paths). Conditions A and C pass no MCP config.

**Modules:**

- `bench/run_bench.py` — expands the grid from `(questions × conditions × models × seeds)`, dispatches each cell through `claude_runner`, writes `results/<timestamp>/<run_id>/{transcript.jsonl, cell.jsonl}`. Idempotent (overwrite) and resumable (skip cells whose `cell.jsonl` already exists).
- `bench/claude_runner.py` — owns `CellSpec` (the declarative cell inputs) and `CellResult` (the parsed outcome); spawns one headless `claude -p`, enforces the turn cap, single-pass-parses stream-json. Has no grid knowledge.

**Spike corrections baked into `claude_runner` (contracts; evidence in `bench/PHASE0_FINDINGS.md`):**

1. `--verbose` (stream-json requires it with `-p`); subprocess `stdin=DEVNULL` (avoids the 3s stdin-wait).
2. subprocess `cwd=<checkout>` (`--add-dir` grants access, does **not** set cwd).
3. **Turn cap is driver-side** — `--max-turns` does not exist. The runner counts `assistant` events in the stream; at the (N+1)th it SIGTERMs the subprocess and returns `exit_reason="cap"`. `result.num_turns` is recorded for post-hoc verification. **N = 15** (spec value).
4. Enforcement is monitored via `tool_call_breakdown`, **not** `permission_denials` — the latter fires only on an *attempted* denied call, and capability-equivalent workarounds (Bash, `ReadMcpResourceTool`) do not trip it.

## The cell contract (load-bearing)

**Invocation** (MCP lines present for conditions B and D only):

```
claude -p "<question>" --output-format stream-json --verbose \
  --permission-mode bypassPermissions --model <id> \
  --add-dir <checkout> --append-system-prompt-file <condition prompt> \
  --allowedTools <...> [--disallowedTools <...>] \
  [--mcp-config <tmp> --strict-mcp-config]
```

with subprocess `cwd=<checkout>`, `stdin=DEVNULL`.

**JSONL cell schema** — one line per cell; each field's real source (mapped by the spike):

| field | source |
|---|---|
| `run_id`, `question_id`, `corpus`, `corpus_commit`, `condition`, `model`, `seed`, `temperature`, `claude_code_version`, `ontology_version`, `index_build_id`, `prompt_hash`, `started_at`, `finished_at`, `wall_s` | cell spec + timing + `corpora.yml` |
| `n_turns` | `result.num_turns` (or driver cap count) |
| `n_tool_calls` | `sum(tool_call_breakdown)` |
| `tool_call_breakdown` | `Counter(tool_use.name)` over the stream |
| `tokens` | `result.usage {input, output, total}` |
| `context_bytes_retrieved` | `Σ tool_result.content` length |
| `exit_reason` | `done` \| `cap` \| `error` ← `terminal_reason` / `is_error` / `api_error_status` + driver cap |
| `final_answer` | `result.result` |
| `transcript_path` | `results/<run_id>/transcript.jsonl` |
| `grade` | `null` (Plan 2b) |

## Condition-C amendment + pre-registration

C's tool list (`Read, Glob, Bash`) is **unchanged**. The amendment is documentary only — it aligns C's *stated* isolation with its tool list, which the spike showed was violable as written ("no Grep" is unenforceable while Bash is unrestricted):

- `bench/conditions.yml` C entry `name`: `Raw agent + shell (no Grep tool, no MCP)`.
- `bench/PREREGISTRATION.md` — dated amendment recording: C permits DIY grep via Bash; its distinction from A is the *absence of the purpose-built Grep tool*, and from B/D the absence of any jrag tooling. Enforcement monitors `tool_call_breakdown` for the Grep tool, not for grep-capability.

This is a pre-run amendment (no cell has run against the published conditions), so it does not invalidate the pre-registration discipline.

## Smoke grid + acceptance

**Grid:** 3 questions × 4 conditions × 1 model × 1 seed = **12 cells on bank-chat**, temp=0.

| question | category | why included |
|---|---|---|
| `bc-impl-01` | interface-impls | already proven 12/12 on D; cross-checks the driver against a known-good cell |
| `bc-role-01` | role-listing | different programmatic category |
| `bc-cs-01` | cross-service | exercises the C3 seam; the category where per-file baselines are meant to fail |

Model: `glm-4.7` (the env default, confirmed headless-viable in the spike). Seed: temp=0 (one deterministic run per cell; the 3-seed × temp=0.7 expansion is Plan 3).

**Acceptance:**

- Every cell emits a valid JSONL line (no null required fields except `grade`); transcript saved.
- **Enforcement sanity:** condition-B cells show zero graph-tool calls in `tool_call_breakdown` (only `mcp__jrag__search` + `Read`); condition-C cells show no `Grep`-tool calls.
- **Correctness eyeball** (not formal grading — that is Plan 2b): condition-D answers match the frozen oracle on the structural questions.
- Idempotency: re-running a cell overwrites its JSONL + transcript.
- `pytest tests/bench/ -q` passes (driver tests use canned stream-json fixtures, including the real run-4 transcript; no API calls in tests).

## Repo layout (Plan 2 deliverables)

```
bench/
  run_bench.py                  # grid expansion + dispatch + results write (idempotent, resumable)
  claude_runner.py              # CellSpec -> spawn claude -p, enforce cap, parse stream -> CellResult
  conditions.yml                # C `name` relabeled (amendment)
  PREREGISTRATION.md            # condition-C amendment appended (dated)
  results/<timestamp>/<run_id>/ # transcript.jsonl + cell.jsonl per cell (gitignored)
tests/bench/
  test_run_bench.py             # grid expansion, idempotency, resume-skip
  test_claude_runner.py         # stream-json parse (canned fixtures), turn-cap SIGTERM, exit_reason mapping, field-source correctness
  fixtures/streams/             # canned stream-json samples (incl. the real run-4 transcript)
```

## Open questions / risks

- **Cap N=15** is the spec value; a thrashing agent still burns budget before hitting it. The smoke grid reveals real turn distributions and may inform tuning before the Plan 3 grid.
- **C ≈ A-minus-Grep** once relabeled. If the smoke grid shows C adds no signal beyond A, it is a candidate for the spec's documented budget-dial drop — decided in Plan 3, not here.
- **`ReadMcpResourceTool` is always present** (survives `--strict-mcp-config`). It is a Read-equivalent in every condition but not a graph-leak (graph data is index-only). Recorded as a known property; no driver action.
- **Explicit-subject `--model glm-5.1`** is the one spike item not yet exercised (only the glm-4.7 default was driven). The smoke grid uses glm-4.7; the Plan 3 grid exercises both subjects and confirms `--model` routing for glm-5.1 then.

## TL;DR

Plan 2 builds `run_bench.py` + `claude_runner.py`: a condition-agnostic driver that spawns one headless `claude -p` per grid cell, bakes in the four de-risk corrections (driver-side turn cap counting `assistant` events; `--verbose` + `stdin=DEVNULL`; subprocess cwd = checkout; enforcement monitored via `tool_call_breakdown`), and emits the spec's JSONL cell schema with each field sourced from the real stream. It ships a documentary condition-C relabel + PREREGISTRATION amendment (C = raw agent + shell, distinct from A by the absence of the Grep tool), and proves itself on a 12-cell smoke grid (3 bank-chat questions × 4 conditions × glm-4.7 × temp=0). Grading, ablations, and the full run are deferred to Plan 2b / Plan 3.
