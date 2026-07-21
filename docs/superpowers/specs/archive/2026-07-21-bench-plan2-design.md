# Benchmark — Agent Harness (Plan 2 / Phases 2-3)

- **Date:** 2026-07-21 (reframed from the 2026-07-20 driver-only draft to the full Phases 2-3: driver + grading)
- **Status:** Active (design approved, pre-implementation)
- **Scope:** The complete agent harness — the `run_bench.py` driver (Phase 2) **and** the `grade.py` grading pipeline (Phase 3): programmatic graders, a condition-blinded glm-5.2 LLM judge, and a κ harness. Validated end-to-end on a graded smoke grid.
- **Predecessors:** Plan 1 (frozen corpora, `conditions.yml`, oracle, 50 questions, `PREREGISTRATION.md`) — merged. Phase-0 de-risk spikes — resolved in `bench/PHASE0_FINDINGS.md` (commit `227e413`).
- **Boundary (not "deferred" — a different activity):** running the benchmark at scale (~1,200 cells), `report.py`, and the CI smoke workflow consume this harness; they are not part of building/validating it.

## Motivation

Plan 1 froze the ground truth; nothing has yet run an agent against it, and nothing has graded an answer. Plan 2 builds the complete, auditable harness that does both — and bakes in the corrections the de-risk spikes forced (no `--max-turns`; `--verbose` + `stdin=DEVNULL`; cwd via subprocess not `--add-dir`; capability-vs-tool-name enforcement) plus the grading machinery an independent, blinded judge needs. Grading is decoupled from running by design: `run_bench.py` emits raw evidence, `grade.py` fills `grade` in a separate pass, so re-grading after a rubric tweak never re-spends API budget. The smoke cell already proved the retrieval thesis (condition-D `bc-impl-01` → 12/12 FQN match with the oracle); Plan 2 turns that probe into a repeatable driver **and** a grader, validated together on a 16-cell grid.

## Scope

**In:** `run_bench.py` + `claude_runner.py` (driver); `grade.py` (programmatic graders + condition-blinded glm-5.2 LLM judge + κ harness); the JSONL cell schema and the `Grade` schema (contracts); the condition-C relabel, the ablation decision, and the temperature/seed property recorded as `PREREGISTRATION.md` amendments; a 16-cell graded smoke grid on bank-chat; pytest for driver + grading.

**Not this plan's goal:** the ~1,200-cell run, `report.py`, CI smoke workflow. The harness built here is what those run at scale.

## Decisions locked in this plan (resolved, not deferred)

The de-risk spikes left three open questions. This plan resolves each to a concrete decision rather than carrying it forward:

- **Ablations.** **D₃** (`cross_service_resolution: brownfield_only`, project-YAML) is the one config-supported ablation → it is the chosen ablation condition (index-time; added to `conditions.yml`/`corpora.yml` when the grid runs — the condition-agnostic driver needs no change for it). **D₂** (role-ranking) is **excluded**: no config knob exists, and ablating it would require source instrumentation, which is out of scope. **D₄** (graph-expansion) is **excluded**: `context_neighbors` is already off in the MCP retrieval path the benchmark uses; the on-by-default graph lever is `graph_expand`, a different feature outside the ablation taxonomy. Recorded in `PREREGISTRATION.md`.
- **Temperature / seed.** `claude -p` exposes **no** temperature or seed flags (confirmed against the installed help). The harness records `seed` (run index) and `temperature` (intended value) as JSONL metadata but does **not** enforce them or claim determinism. The smoke grid runs each cell once (`seed=0`).
- **Condition C.** Relabeled `Raw agent + shell (no Grep tool, no MCP)`; tool list (`Read, Glob, Bash`) unchanged. Enforcement monitors `tool_call_breakdown` for the Grep tool, not for grep-capability (Bash can grep — an accepted, recorded property).

## Driver architecture (Phase 2)

The driver is **condition-agnostic**: each cell is assembled declaratively from Plan 1's loaders, so the D₃ ablation condition (and any future condition) is a `conditions.yml`/`corpora.yml` edit, not a code change.

Per cell `(question, condition, model, seed)`:

- `bench.load_conditions.to_flags(cond)` → allowed/disallowed tools, MCP-config arg, condition prompt.
- `bench.load_corpora` → corpus checkout path (the subprocess **cwd**) + index dir.
- `bench.load_questions` → question text (the `-p` prompt).
- MCP config materialized per cell from the `bench/mcp/jrag.json` template (`${JRAG_INDEX_DIR}` / `${JRAG_SOURCE_ROOT}` → absolute paths; `command` rewritten to `sys.executable`). Conditions A and C pass no MCP config.

**Modules:**

- `bench/run_bench.py` — expands the grid from `(questions × conditions × models × seeds)`, dispatches each cell through `claude_runner`, writes `results/<timestamp>/<run_id>/{transcript.jsonl, cell.jsonl}` and an aggregated `cells.jsonl`. Idempotent (overwrite) and resumable (skip cells whose `cell.jsonl` exists).
- `bench/claude_runner.py` — owns `CellSpec` and `CellResult`; spawns one headless `claude -p`, enforces the turn cap, single-pass-parses stream-json. No grid knowledge.

**Spike corrections baked into `claude_runner` (contracts; evidence in `bench/PHASE0_FINDINGS.md`):**

1. `--verbose` (stream-json requires it with `-p`); subprocess `stdin=DEVNULL`.
2. subprocess `cwd=<checkout>` (`--add-dir` grants access, does **not** set cwd).
3. **Turn cap is driver-side** — `--max-turns` does not exist. The runner counts `assistant` events; at the (N+1)th it SIGTERMs and returns `exit_reason="cap"`. `result.num_turns` recorded post-hoc. **N = 15.**
4. Enforcement monitored via `tool_call_breakdown`, not `permission_denials`.

## Grading architecture (Phase 3)

`grade.py` consumes `cells.jsonl` + each cell's transcript + the oracle `expected/<id>.json`, and writes `graded.jsonl` (identical schema, `grade` filled). Raw `cells.jsonl` stays immutable. Dispatch is by the question's `grading` field.

**Programmatic graders (objective, no judge):**

| grader | categories | behavior | `detail` |
|---|---|---|---|
| `set_match` | interface-impls, upstream-consumers, role-listing | extract simple symbol names from `final_answer`; compare as a set vs the simple names of `expected.fqns` | `{precision, recall, f1, predicted_n, expected_n}`; `correctness = f1` |
| `path_match` | call-trace | extract the ordered hop sequence from `final_answer`; ordered exact-match vs `expected.hops` plus Jaccard over the hop set | `{ordered_match, jaccard}`; `correctness = 1.0 if ordered_match else jaccard` |
| `client_route_match` | cross-service | extract client→route pairs from `final_answer`; compare vs `expected.pairs` | `{matched, missing, spurious}`; `correctness = matched/expected` |
| `absence_check` | absence | answer asserts "not present" iff `expected.verdict == "not_in_project"` | `{verdict_match}`; `correctness = 1.0/0.0` |

**LLM judge (glm-5.2, condition-blinded, locked rubric)** — for the `llm_judge` (semantic) category:

- `blind_transcript(transcript_text)` scrubs tool names (`mcp__jrag__*`, `Grep`, `Glob`, `Read`, `Bash`) and MCP/jrag identifiers from the transcript, so the judge cannot favor a condition.
- `judge_answer(blinded_transcript, question, expected)` invokes `glm-5.2` headless with a locked rubric, returning `correctness` (0–1) + rationale. `method="llm_judge"`, `judge_model="glm-5.2"`. The judge is independent of both subject models (glm-4.7/5.1).

**κ harness:**

- `cohen_kappa(judge_labels, human_labels)` — standard Cohen's κ over the judged subset. A human-labels file (produced procedurally) supplies the second set. The harness is unit-validated on synthetic label sets of known agreement; on the smoke grid it runs over however many judged cells exist (small N reported honestly).

**`Grade` schema (contract):**

| field | type | source |
|---|---|---|
| `correctness` | `float` (0.0–1.0) | grader/judge |
| `method` | `str` (`set_match` \| `path_match` \| `client_route_match` \| `absence_check` \| `llm_judge`) | the question's `grading` |
| `detail` | `dict` | grader-specific (see table above) |
| `judge_model` | `str \| None` | `"glm-5.2"` for `llm_judge`, else `None` |

The cell's `grade` field is this `Grade` serialized (or `null` before `grade.py` runs).

## The cell contract (load-bearing)

**Invocation** (MCP lines present for conditions B/D only):

```
claude -p "<question>" --output-format stream-json --verbose \
  --permission-mode bypassPermissions --model <id> \
  --add-dir <checkout> --append-system-prompt <prompt contents> \
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
| `tool_call_breakdown` | `Counter(tool_use.name)` |
| `tokens` | `result.usage {input, output, total}` |
| `context_bytes_retrieved` | `Σ tool_result.content` length |
| `exit_reason` | `done` \| `cap` \| `error` |
| `final_answer` | `result.result` |
| `transcript_path` | `results/<run_id>/transcript.jsonl` |
| `grade` | `null` from the driver; filled by `grade.py` |

## Amendments to `PREREGISTRATION.md` (dated 2026-07-21)

One amendment section records: (a) condition-C relabel + the Bash-grep property; (b) the ablation decision (D₃ chosen; D₂/D₄ excluded with rationale); (c) the temperature/seed property (no flags; metadata-only). All pre-run (no cell has run against the published conditions).

## Smoke grid + acceptance

**Grid:** 4 questions × 4 conditions × 1 model × 1 seed = **16 cells on bank-chat**, temp=0.

| question | category | grading | why included |
|---|---|---|---|
| `bc-impl-01` | interface-impls | set_match | proven 12/12 on D; cross-checks driver + set grader |
| `bc-role-01` | role-listing | set_match | second set-match category |
| `bc-cs-01` | cross-service | client_route_match | exercises the C3 seam + client-route grader |
| `bc-sem-01` | semantic | llm_judge | exercises the blinded judge end-to-end |

Model: `glm-4.7`. Seed: `0`.

**Acceptance:**

- Every cell emits a valid JSONL line; transcript saved; `grade.py` produces `graded.jsonl` with `grade` filled on all 16.
- **Driver enforcement sanity:** condition-B cells show zero graph-tool calls; condition-C cells show no `Grep`-tool call.
- **Grader sanity:** condition-D `bc-impl-01` scores `correctness ≈ 1.0` vs the oracle (12/12); condition-D `bc-cs-01` resolves the seam (non-zero `matched`).
- **Judge sanity:** `bc-sem-01` produces a `grade` with `method="llm_judge"`, `judge_model="glm-5.2"`, and a rationale; the blinding scrub is verified (no tool names in the judged transcript).
- **κ harness:** runs over the judged subset (reported with its N).
- Idempotency: re-running a cell overwrites; re-grading overwrites `graded.jsonl`.
- `pytest tests/bench/ -q` passes.

## Repo layout (Plan 2 deliverables)

```
bench/
  claude_runner.py              # CellSpec -> spawn claude -p, turn cap, parse stream -> CellResult
  run_bench.py                  # grid + dispatch + results I/O (idempotent, resumable) + CLI
  grade.py                      # programmatic graders + blinded glm-5.2 judge + kappa + CLI
  conditions.yml                # C relabeled — MODIFY
  PREREGISTRATION.md            # relabel + ablation + temp/seed amendments — MODIFY
  results/<timestamp>/          # transcript.jsonl + cell.jsonl per cell; cells.jsonl; graded.jsonl — gitignored
tests/bench/
  test_claude_runner.py         # stream parse, mcp materialize, argv, run_cell + cap
  test_run_bench.py             # grid, results I/O, idempotency/resume, CLI
  test_grade.py                 # graders, blinding, cohen_kappa, grade_cell dispatch, CLI
  fixtures/streams/             # canned stream-json (incl. real run-4 transcript)
  fixtures/fake_claude/         # scripts emitting canned stream-json
```

## TL;DR

Plan 2 builds the complete agent harness in two phases, validated together: **Phase 2** is the condition-agnostic `run_bench.py` + `claude_runner.py` driver (four spike corrections: driver-side turn cap on `assistant` events, `--verbose` + `stdin=DEVNULL`, subprocess cwd = checkout, enforcement via `tool_call_breakdown`); **Phase 3** is `grade.py` — programmatic set/path/client-route/absence graders, a condition-blinded glm-5.2 LLM judge with a locked rubric, and a Cohen's κ harness — filling the `grade` field in a separate, re-runnable pass. It locks the ablation decision (D₃ in; D₂/D₄ out), the temperature/seed property (no flags; metadata-only), and the condition-C relabel into `PREREGISTRATION.md`, and proves the whole pipeline on a 16-cell graded smoke grid (4 bank-chat questions × 4 conditions × glm-4.7 × seed 0). The at-scale run, report, and CI consume this harness and are separately scoped.
