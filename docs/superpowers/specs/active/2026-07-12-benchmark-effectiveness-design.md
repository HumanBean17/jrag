# Benchmark — Effectiveness Proof for `java-codebase-rag`

- **Date:** 2026-07-12
- **Status:** Active (design approved, pre-implementation)
- **Scope:** A reproducible benchmark that produces credible proof of *effectiveness* (answer quality), complementing the existing performance-only benches in `~/jrag-bench/`.
- **Host under test:** Claude Code (the agent), driven by GLM models, with `java-codebase-rag` wired in as an MCP server.

> **Plan 4 note (2026-07-22):** the MCP surface described throughout this
> original design is **superseded** — the benchmark now drives the shipped
> `jrag` CLI (`jrag <verb>` via Bash, gated by a per-condition PATH shim). The
> conditions (A/B/C/D), claims (C1–C6), oracle, and grading are unchanged (both
> surfaces drive the same backend). See
> `docs/superpowers/specs/active/2026-07-22-bench-plan4-cli-reframe-design.md`
> and `bench/PREREGISTRATION.md` Amendment 2026-07-22 (Plan 4).

## Motivation (the gap)

`~/jrag-bench/` measures *performance* only — index init/reprocess wall-time, vectors-phase latency, LanceDB fragment counts, and BM25 lexical query latency. There is **no measurement of retrieval/answer effectiveness** anywhere in the project. Meanwhile `docs/PRODUCT-VISION.md` already *claims* a result ("DKB 15/15 vs vector 6/15 on architecture-tracing queries") attributed to a "2026 benchmark" — but no reproducible artifact backs that claim. This spec defines the process that produces that artifact, audibly.

## Claims under test (pre-registered)

Each claim maps to a metric, a question subset, and a results-table row. Targets are *hypotheses to evaluate*, not pre-committed outcomes.

| # | Claim | Metric | Question subset |
|---|-------|--------|-----------------|
| **C1** | On structural questions (impls / callers / injectors / blast-radius), jrag answers more correctly than vector-only and grep. | Answer correctness (0–1); retrieval precision/recall vs oracle. | ~30 of the ~50 questions (structural categories). |
| **C2** | jrag reaches a correct answer in fewer agent steps and fewer tokens (graph hops vs reading whole files). | Steps-to-answer; total tokens; context bytes. | All questions. |
| **C3** | On cross-service questions (HTTP_CALLS / ASYNC_CALLS spanning two services), per-file/per-repo baselines fail *structurally*; jrag resolves them. | Cross-service subset correctness; binary "resolved the seam?". | Dedicated ~8 questions. |
| **C4** | Re-indexing is deterministic — identical node/edge counts run-to-run, unlike LLM-built graphs. | Graph-stat diff across 3 rebuilds. | Per-corpus, not per-question. |
| **C5** | Index build + per-query cost stay within ~2× of vector-only, not 20× (the LLM-KB failure mode). | Build time; on-disk size; $/query. | Per-corpus. |
| **C6** *(directional)* | The structural advantage holds across model capability tiers, and weaker models benefit more. | Correctness gap (jrag − baseline) plotted per model tier. | All questions, sliced by model. |

**C6 limitation (stated up front):** the subject set has two model tiers (glm-4.7 < glm-5.1). Two points show a *direction* only; the claim is not statistically powered. Restoring a third tier (e.g. glm-4.5-air) is a Phase 6 stretch that powers C6 up.

## Subjects, judge, and corpora

- **Agent host:** Claude Code, invoked headless via `claude -p` (it *is* Claude Code, so the "tested on Claude Code" claim is literal). Fallback to the Claude Agent SDK only if flag-based isolation or transcript capture proves too coarse.
- **Subject models:** `glm-4.7`, `glm-5.1`.
- **Judge model:** `glm-5.2` — independent of both subjects. Residual same-family bias is bounded by condition-blinding and the human κ gate (see Grading), not by cross-family selection.
- **Corpora** (each pinned to a commit SHA in `bench/corpora.yml`, checkout frozen under `bench/checkouts/`):

| Corpus | Role | Size |
|--------|------|------|
| `tests/bank-chat-system` | Controlled fixture, 2 services, has HTTP_CALLS + ASYNC_CALLS. Densest coverage incl. all cross-service questions; cheapest ground truth. | 130 files |
| `shopizer` | Real OSS (PRODUCT-VISION-cited); already indexed in `~/jrag-bench/`. Scale + parse-error realism. | 1210 files |
| `spring-petclinic-microservices` | Canonical Spring Cloud (Feign + service discovery) → clean cross-service cases. | ~hundreds |

## Conditions and isolation

Conditions differ *only* in the tool set exposed to Claude Code — enforced by harness flags (`--mcp-config`, `--allowedTools`, `--disallowedTools`), never by prompt pleas. jrag MCP tools are named `mcp__jrag__{search,find,describe,neighbors,resolve}`.

| Cond | Retrieval available | Exact Claude Code tool set |
|------|---------------------|----------------------------|
| **A — Lexical** | grep/ripgrep only | `Grep`, `Glob`, `Read`, `Bash`; no jrag MCP. Floor baseline. |
| **B — Vector-only** | semantic search, **graph off** | jrag MCP + `--disallowedTools mcp__jrag__find,mcp__jrag__describe,mcp__jrag__neighbors,mcp__jrag__resolve` → only `search` survives; plus `Read` (fair: open a hit). |
| **C — Raw agent** | nothing — navigate + read only | `Read`, `Glob`, `Bash` (ls); no MCP, no `Grep`. Token-burn ceiling, distinct from A. |
| **D — jrag full** | vector + graph + cross-service (system under test) | all 5 jrag MCP tools + `Read`/`Grep`/`Glob`. |
| **E — LLM-graph KB** | *(deferred to Phase 6 stretch)* | separate MCP, built later. |

**Why the isolation is airtight:** the allow/deny list is enforced by the harness. An agent in condition B cannot call `neighbors` even if it tries — Claude Code rejects the tool. This is the single biggest methodological guard.

**Held constant across every cell:** subject model + seed; the condition's locked prompt skeleton (`bench/prompts/<cond>.md`, identical except the "Your tools" section); `--max-turns 15` (a thrashing agent scores as failure, not an infinite loop); `--permission-mode bypassPermissions` (no human-in-the-loop prompts); the frozen corpus checkout as cwd.

**Index handling:** conditions B and D share *one* pre-built index per corpus (built once via the operator CLI, pinned to `ontology_version`). Index-build cost is measured **separately** as the per-corpus C5 metric; it never contaminates per-question latency.

## Run grid and budget

> **~50 questions (each bound to one corpus) × 4 conditions × 2 models × 3 seeds = 1,200 runs** (+ 450 if E is added later).

One additional temp=0 deterministic run per cell is produced as the headline value; the 3 seeds use a fixed moderate temperature (e.g. 0.7) and report mean ± std.

**Budget dials**, in the order pulled if spend/time bites: drop seeds 3→2 (−400 runs); drop condition C raw-agent (−300); trim questions 50→40 (−240). Irreducible minimum = conditions A/B/D with ≥2 seeds — A/B/D is what proves the graph layer earns its keep.

## Harness architecture

**Driver contract.** `run_bench.py` iterates the grid. For each cell `(question, condition, model, seed)` it assembles one `claude -p` invocation from a declarative spec:

- `bench/conditions.yml` — per-condition tool allow/deny list, MCP config path, prompt file. This file *is* the executable isolation spec.
- The frozen corpus checkout is the cwd (`--add-dir`); the pre-built index's MCP server is registered via `--mcp-config`.
- Flags: `--max-turns 15`, `--permission-mode bypassPermissions`, `--model <id>`, `--append-system-prompt` from the condition prompt, `--output-format stream-json`.

The driver streams JSON to `results/<run_id>/transcript.jsonl`, parses it once, and emits **one JSONL line per cell**. The schema is the load-bearing contract of the whole bench:

```json
{
  "run_id": "bc-impl-01_D_glm-4.7_s2",
  "question_id": "bc-impl-01",
  "corpus": "bank-chat-system",
  "corpus_commit": "abc123",
  "condition": "D",
  "model": "glm-4.7",
  "seed": 2,
  "temperature": 0.7,
  "claude_code_version": "1.x",
  "ontology_version": 18,
  "index_build_id": "...",
  "prompt_hash": "sha256:...",
  "started_at": "...",
  "finished_at": "...",
  "wall_s": 123.4,
  "n_turns": 9,
  "n_tool_calls": 14,
  "tool_call_breakdown": {"mcp__jrag__neighbors": 5, "mcp__jrag__search": 3, "Read": 6},
  "tokens": {"input": 12400, "output": 1850, "total": 14250},
  "context_bytes_retrieved": 48211,
  "exit_reason": "done|cap|error",
  "final_answer": "...",
  "transcript_path": "results/<run_id>/transcript.jsonl",
  "grade": null
}
```

- `exit_reason` distinguishes *answered* from *gave up at the max-turns cap* (`cap`, scored as failure) from *API error* (`error`, the only retried case). A wrong answer is never retried — it is data.
- **Grading is decoupled.** `run_bench.py` only produces raw evidence; `grade.py` consumes the JSONL + transcript + the oracle's expected answers and fills `grade` in a separate pass. Re-grading after a rubric tweak never re-spends API budget.
- Idempotent: re-running a cell overwrites its JSONL.

## Golden question set

**Taxonomy** — each category maps to a claim, an oracle source, and a grading method:

| Category | Example (engineer-phrased) | Claim | Oracle source | Grading |
|----------|----------------------------|-------|---------------|---------|
| Interface impls | "Which classes implement `AssignStrategy`?" | C1 | jqassistant `IMPLEMENTS` rule | set-match (programmatic) |
| Upstream consumers | "Which controllers depend on `PaymentService`?" | C1 | jqassistant `INJECTS`/`CALLS` in | set-match |
| Call trace | "Trace `/assign` from controller to DB write" | C1,C2 | jqassistant path + manual | path exact-match + Jaccard |
| Blast radius | "If I change DTO `X`, which services break?" | C1,C3 | jqassistant transitive + manual | set-match |
| Cross-service | "Who calls `POST /join` from *another* service?" | **C3** | manual (Feign/HTTP matching) | client→route pair match |
| Role listing | "List all controllers in chat-core" | C1 | jqassistant role/annotation rule | set-match |
| Semantic lookup | "How is a chat message persisted?" | C1 (honesty zone — vector ties/wins) | manual (method spans) | **LLM-judge** |
| Absence | "Is there a Redis cache layer?" (when there isn't) | C1 | manual + proof | absence correctness |

**Distribution:** bank-chat carries the densest coverage incl. all cross-service questions; shopizer carries structural + semantic; petclinic carries structural + cross-service. No category dominates; cross-service ≥8.

**Authoring protocol (anti-leakage).** Questions are written in an engineer's voice — *never* the tool's vocabulary ("which INJECTS edges" is banned). Each record:

```json
{
  "id": "bc-impl-01",
  "corpus": "bank-chat-system",
  "category": "interface-impls",
  "difficulty": "easy",
  "question": "Which classes implement the AssignStrategy interface?",
  "expected": {"kind": "symbol_set", "fqns": ["..."], "ids": ["sym:..."]},
  "oracle_source": "jqassistant:implements.cypher",
  "claim_refs": ["C1"],
  "grading": "programmatic_set_match"
}
```

`expected` is filled by `build_oracle.py` from the **independent** oracle, then a human reviews and freezes it.

## Independent oracle (hybrid)

The oracle that grades jrag must not *be* jrag (circular). Hybrid strategy:

- **jqassistant** scans each corpus into its own Neo4j graph (different parser — javaparser/ASM, not tree-sitter). Cypher rules per category answer impls/calls/injects/role independently of jrag.
- **jdeps** cross-checks dependency edges at class granularity (a second, JDK-native oracle).
- **Calibration gate:** on bank-chat-system, jqassistant's mechanical answers are diffed against the manual expert set. If they disagree, we investigate *before* trusting the mechanical oracle on shopizer/petclinic. Agreement % is published.
- **Honest boundary:** jqassistant/jdeps cannot independently resolve Spring HTTP routes or cross-service HTTP matching — those stay manual expert annotation, with matching rules documented in `oracle/manual/`. The mechanical-vs-manual split is stated per question.

Ground truth is stored frozen in `oracle/expected/<question_id>.json`.

## Metrics

Three families. Bolded metrics need **no judge** — the most defensible signal.

- **Quality:** answer correctness (0–1); **retrieval precision/recall/F1** (retrieved symbol set ∩ oracle — pure transcript-vs-oracle, objective); **hop/path exact-match + Jaccard**; hallucination rate (entities asserted but ∉ oracle); absence correctness.
- **Efficiency:** steps-to-answer; total tokens; context bytes; wall latency (already in the JSONL).
- **Build-cost (per corpus):** build time; **determinism** (graph-stat diff ×3 → C4); on-disk size; embedding $ (→ C5).

## Grading

- **Structural categories** → programmatic graders (set / Jaccard / path). Covers the majority of questions; fully objective.
- **Semantic/narrative categories** → LLM-judge (glm-5.2) with a **locked rubric**, **condition-blinded**: the transcript shown to the judge is scrubbed of tool names and MCP identifiers, so it cannot favor "the fancy tool."
- **Human κ gate:** a human double-checks a random 20% of *judged* answers; Cohen's κ (judge↔human) is reported. κ below ~0.6 → rubric revised before unblinding results.

## Rigor, ablations, honesty

- **Pre-registration:** claims C1–C6, the frozen question set, and the grading rubric are committed to `bench/PREREGISTRATION.md` *before* the full run. No metric added post-hoc.
- **Pinning:** every results file records corpus SHAs, `claude_code_version`, model ids, `ontology_version`, embedding model id, Claude Code flags, prompt hashes, index build ids, seeds.
- **Ablations (which design choice earns the win):** run jrag-only in degraded modes as extra conditions on the D column, wherever the tool exposes a toggle:
  - D₁ full · D₂ role-ranking off · D₃ cross-service edges off · D₄ graph-expansion off (≈ collapses toward B).
  - Each ablation's marginal delta credits a specific design choice.
  - **Feasibility caveat:** some toggles are query-time (role-ranking, expansion), some index-time (cross-service). Phase 0 confirms which ablations the tool's actual config/flags support; any it cannot support is dropped and noted, not silently skipped.
- **Honesty:** the semantic category is *expected* to be where vector-only (B) ties or beats jrag — that is a feature of the report, not a bug. Raw transcripts published. Losses visible.

## Deliverables

1. **Results table** — condition × category → correctness / steps / tokens; jrag-winning cells highlighted; losses visible.
2. **Plots** — correctness-by-category (bar); correctness-vs-tokens (scatter, the "quality per unit cost" headline); steps-to-answer (boxplot); per-model-tier deltas (for C6).
3. **Report** (`bench/README.md`) — methodology, pinned setup, headline numbers, limitations, "reproduce in 3 commands."
4. **CI smoke bench** (`.github/workflows/bench-smoke.yml`) — ~8 questions on bank-chat, fails on correctness regression.

## Repo layout

```
bench/
  PREREGISTRATION.md     # claims + question set + rubric, frozen pre-run
  corpora.yml            # name -> git url + pinned SHA + index config
  conditions.yml         # A-D tool sets (the executable isolation spec)
  questions/<corpus>.jsonl
  prompts/{A,B,C,D}.md   # locked; identical except "Your tools"
  oracle/
    jqassistant_rules/*.cypher
    jdeps/
    manual/<corpus>.json
    build_oracle.py      # emits expected answers, runs calibration gate
    expected/<question_id>.json
  run_bench.py           # driver: claude -p per cell -> JSONL + transcript
  grade.py               # programmatic + judge graders (separate pass)
  report.py              # aggregate -> tables, plots, markdown
  results/<timestamp>/   # raw JSONL + transcripts = the published proof
  README.md              # reproduce in 3 commands
```

## Phased plan (~8–12 days)

| Phase | Days | Work |
|-------|------|------|
| 0 — Scaffold | 1 | `bench/` layout, `corpora.yml`, pin SHAs, 3 corpora index cleanly, `conditions.yml` + prompts, confirm ablation toggles. |
| 1 — Ground truth | 2–3 | jqassistant + jdeps setup, Cypher rules, bank-chat manual annotation, **calibration gate**, author ~50 questions. Longest pole. |
| 2 — Driver | 1–2 | `run_bench.py` (claude -p harness), wire conditions, validate 1 cell/condition end-to-end. |
| 3 — Grading | 1 | programmatic graders + glm-5.2 judge harness + κ harness; validate on a small set. |
| 4 — Full run | 1–2 | 1,200 runs, parallelized across model/seed workers; JSONL + transcripts. |
| 5 — Ablations + report | 1–2 | D-variants, aggregate, plots, report, publish logs. |
| 6 — Stretch | — | add glm-4.5-air (powers up C6); LLM-graph baseline E; CI smoke bench. |

## Open questions / risks

- **Ablation toggles:** which of role-ranking / cross-service-edges / graph-expansion the tool can actually disable is unconfirmed until Phase 0. If none are exposed, the ablation row of the story weakens (D₁ alone, no D₂–D₄).
- **Claude Code flag stability:** `claude -p` flag surface (allow/deny MCP tool names, `--max-turns`, stream-json shape) must be verified against the installed version in Phase 0; SDK is the fallback.
- **jqassistant coverage:** whether jqassistant independently resolves every structural category we need (esp. Spring `@Autowired` constructor injection) is to be confirmed during oracle setup; gaps fall back to manual with the boundary stated.
- **Judge bias:** glm-5.2 judging same-family subjects (5.x) is bounded by blinding + κ, not eliminated; report this limitation honestly.

## TL;DR

Build a reproducible effectiveness benchmark that proves (or falsifies) six pre-registered claims about jrag on Claude Code. Subjects: glm-4.7 + glm-5.1; judge: glm-5.2. Corpora: bank-chat-system (controlled), shopizer (real OSS), spring-petclinic-microservices (cross-service). Four conditions enforced by harness tool-allow-lists, not prompts: A lexical, B vector-only (graph off), C raw-agent, D jrag-full. Grid: ~50 engineer-phrased golden questions × 4 conditions × 2 models × 3 seeds = 1,200 runs, graded by an independent hybrid oracle (jqassistant + jdeps + manual, cross-calibrated) — programmatic for structural questions, condition-blinded LLM-judge for free-form, human κ gate. Output: results table + plots + report + CI smoke bench, with raw transcripts published. Headline differentiator: a directional claim (C6) that the graph layer's value holds across model tiers and helps weaker models more. Longest pole is ground truth (Phase 1). The semantic category is *expected* to be where jrag ties or loses — reported honestly.
