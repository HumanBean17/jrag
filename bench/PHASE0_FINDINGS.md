# Phase 0 De-risk Findings

Living log of spikes that gate later phases. Each entry: what was checked, the
result, and any downstream implication.

## jqassistant injection coverage (Task 1)

**Verdict: COVERED.** See `bench/oracle/JQASSISTANT_COVERAGE.md`. The mechanical
oracle (`injects.cypher`, `upstream-consumers`) may use jqassistant; collection
injection needs a `DEPENDS_ON` intersection but is not a hard gap.

## Operator CLI build workflow + path resolution

The operator CLI (`java-codebase-rag`) splits index creation from the build:

- `init` only scaffolds the index dir (empty Ladybug graph). It refuses (exit 2)
  if the resolved index dir already holds a graph — so `rm -rf` first or use
  `erase --yes`.
- `reprocess` performs the full Lance vector reprocess + LadybugDB graph rebuild.

So a "build" = `init` + `reprocess`. C5 `build_time_s` below is the wall-clock
for both steps together.

**Path-resolution gotcha (load-bearing for Plan 2's driver):** `--index-dir` is
resolved **relative to `--source-root`**, not the process cwd. Passing a
relative `--index-dir` therefore lands the index *inside* the checkout
(`bench/checkouts/<corpus>/bench/indexes/...`). Always pass `--index-dir` as an
**absolute** path. `bench/checkouts/` and `bench/indexes/` are both gitignored.

## C4 determinism (n=2 of 3)

Two independent `init`+`reprocess` builds of bank-chat-system (different output
dirs) produce byte-identical node/edge counts:

| metric          | build 1 | build 2 |
|-----------------|---------|---------|
| files           | 130     | 130     |
| types           | 140     | 140     |
| members         | 606     | 606     |
| calls (edges)   | 684     | 684     |
| injects         | 94      | 94      |
| implements      | 21      | 21      |
| extends         | 18      | 18      |
| overrides       | 38      | 38      |
| routes          | 28      | 28      |
| http_calls      | 8       | 8       |
| async_calls     | 9       | 9       |
| ontology        | 19      | 19      |

`COUNTS IDENTICAL · EDGES IDENTICAL · ONT IDENTICAL`. The third rebuild for
n=3 is a Plan 2/3 stretch; n=2 already establishes the determinism seed (C4).
Full count tables for all three corpora are in `bench/corpora.yml` (build_id
ties each index to `name:sha:ontology_version`).

## C5 build-cost (per corpus, single build)

| corpus                        | files | types | build_time_s | on_disk_bytes | build_id           |
|-------------------------------|-------|-------|--------------|---------------|--------------------|
| bank-chat-system              | 130   | 140   | 24.31        | 19,579,624    | 359a90a1993004f6   |
| shopizer                      | 1167  | 1201  | 74.34        | 55,462,518    | 2cc6f3554cd35296   |
| spring-petclinic-microservices| 53    | 61    | 22.16        | 13,614,076    | 05746336a2fb08df   |

All builds: 0 parse errors, ontology_version 19. shopizer is the cost ceiling
(≈3× bank-chat build time, ~55 MB on disk). petclinic is smaller than the spec's
"~hundreds" (62 `.java` files in the checkout) but carries the Feign
cross-service seam (1 detected `http_calls` edge) and the multi-module shape.

## `claude -p` flag stability (Plan 2 driver gate) — RESOLVED

**Verdict: PASS.** Headless `claude -p` drives the benchmark; 4 probe runs
(~$0.40 total) confirmed the flag surface and exposed **4 corrections** the
driver must apply. Raw transcripts saved under `bench/spikes/`.

**Confirmed working:**

| Need | Finding |
|------|---------|
| Headless execution | `claude -p ... --output-format json\|stream-json` exits 0, no stderr. |
| Model routing | Env default is **glm-4.7** (a spec subject) — driven headless with no `--model`. Explicit `--model glm-5.1` is the only remaining (trivial) check. |
| Token/cost accounting | Terminal `result` event carries `usage.{input,output,cache_read,cache_creation}_tokens`, `total_cost_usd`, per-model `modelUsage`. |
| Tool-call capture | `assistant.content[].tool_use.{name,input}` → `tool_call_breakdown`; `user.content[].tool_result` → `context_bytes_retrieved` (sum of content lengths). |
| Exit reason | `stop_reason` (end_turn) + `terminal_reason` (completed) + `is_error` + `api_error_status` distinguish done/cap/error. |
| MCP integration | jrag server wires up via `--mcp-config` headless; agent calls `mcp__jrag__resolve`+`mcp__jrag__neighbors` and answers correctly. |

**4 driver corrections (load-bearing):**

1. **`--max-turns` does not exist.** No turn-cap flag in the 227-line help (only
   `--max-budget-usd`, `--effort`). Cap = **driver-side**: count `assistant`
   events in stream-json, SIGTERM at N (`num_turns` is reported post-hoc in the
   result event for verification). Real-time counting is feasible — `assistant`
   events equal model turns (observed num_turns: 1 no-tools, 2 one-tool, 3 two-tool).
2. **`--output-format stream-json` requires `--verbose`** with `-p` (else exit 1,
   empty output). Driver must pass `--verbose`.
3. **`claude -p` waits on stdin** ("no stdin data received in 3s"). Driver must
   close stdin (`subprocess stdin=DEVNULL` / `< /dev/null`).
4. **`--add-dir` does NOT set cwd.** It only grants access to extra dirs; the
   agent's cwd = the process cwd. Spec's "the corpus checkout is the cwd
   (`--add-dir`)" is wrong. No `--cwd` flag exists — driver must launch `claude`
   with subprocess `cwd=<checkout>`.

**Isolation enforcement — methodology correction (most important):**

`--disallowedTools` blocks a *tool name*, **not a capability**. Denying `Read`
did not stop file reads — the agent used `ReadMcpResourceTool` (default `local`
MCP `file://` server) and `Bash` (`head -1`) instead. `permission_denials`
stayed **empty** (the agent never *attempted* the denied tool). Per condition:

- **B (vector-only): isolation HOLDS.** Graph data lives only in the index,
  unreachable except via the denied `mcp__jrag__{find,describe,neighbors,resolve}`
  tools. Bash/Read can't reach it. The load-bearing B-vs-D comparison is sound.
- **C (raw agent, "no Grep"): isolation is VIOLABLE.** Spec allows `Bash` in C;
  Bash can `grep`/`find`/`cat`, replicating Grep/Glob/Read. "No Grep" is
  unenforceable while Bash is unrestricted. Fix options: (a) restrict Bash to
  `ls` via `Bash(ls:*)` allowlist syntax (probe needed), (b) drop Bash from C,
  or (c) relabel C as "raw agent + shell."
- **`ReadMcpResourceTool` is always present**, even under `--strict-mcp-config`
  (surfaced in the run-4 event stream; directly invoked in run 3). It is a
  Read-equivalent in every condition — not a graph-leak risk for B/D, but another
  read path for C that must be denied explicitly if C is read-limited.
- **Enforcement monitoring must inspect `tool_call_breakdown`** for unexpected
  tools, not rely on `permission_denials` (which fires only on an *attempted*
  denied call).

**End-to-end smoke cell (condition D, `bc-impl-01`):** 3 turns, 2 jrag tool
calls (`resolve` → `neighbors`), $0.17, glm-4.7. Answer = 12 `EventProcessor`
implementers = **exact 12/12 FQN match** with the frozen jqassistant-grounded
oracle. Validates MCP harness + graph-hop pattern + grading pipeline in one cell.
Transcript: `bench/spikes/run4-condition-D-bc-impl-01.stream.jsonl`.

## Ablation toggles (Plan 2/3) — RESOLVED

**Verdict: the D₂/D₃/D₄ ablation row is mostly NOT cleanly supported via config.**
Per the spec's own feasibility caveat ("any it cannot support is dropped and
noted"), this weakens the ablation story. Source: `src/java_codebase_rag/`.

| Ablation | Knob | Time | Disable | Verdict |
|----------|------|------|---------|---------|
| **D₂ role-ranking** | none (no CLI/env/YAML) | query-time (`search/search_scoring.py:72` `_ROLE_SCORE_WEIGHTS`) | per-query only via `role=` filter (`search_lancedb.py:1039` `skip_role_weight`) | **NOT EXPOSED** — ablate only by source-patch (zero weights or force skip). `RUN_HEAVY` is a pytest gate, not runtime. |
| **D₃ cross-service edges** | `cross_service_resolution: brownfield_only` (project YAML) | index-time (`reprocess --graph-only`; `build_ast_graph.py:2947-2952`) | **PARTIAL** — demotes only *auto-detected* edges to `unresolved`; brownfield-sourced edges survive | Feasible but imperfect; fully-off also requires stripping brownfield YAML overrides. |
| **D₄ graph-expansion** | `context_neighbors` (CLI-only on `search_lancedb.py` script; **default 0 in MCP** — `mcp_v2.py:1021+` never passes it) | query-time | fully off at 0 | **MISNAMED** — already OFF in the MCP path the benchmark uses. The actually-on graph feature is `graph_expand`/`expand_depth` (3-list RRF fusion, `mcp_v2.py:1052`); that is the real lever and needs its own toggle check. |

**Plan 2/3 implication:** ablations require either source patches (forked
instrumented builds) or are infeasible. Recommend either (a) scope the ablation
row down to **D₃ `brownfield_only`** (the one real config toggle; accept partial)
and note D₂/D₄ as "deferred — requires source instrumentation," or (b) build a
small instrumented jrag variant (env-gated zero-role-weights + `graph_expand=False`)
if the ablation row is essential to the effectiveness story.

## shopizer Maven build is broken (Task 16 deviation)

shopizer's POM imports BOMs from `s01.oss.sonatype.org` (snapshots repo) that
are unresolvable in this environment — the build stalls on
`micrometer-bom`, `netty-bom`, `spring-data-bom`, etc. and produces **no**
`.class` files, so jqassistant cannot scan shopizer. Consequences, recorded
honestly:

- shopizer's 15 questions are all `oracle_source: "manual"` (structural answers
  derived by independent **source grep**, not jqassistant). blast-radius is
  depth-1 (direct importers), not depth-2, with rationale noting the manual
  derivation.
- bank-chat (calibration corpus) and petclinic (compiled cleanly via `mvn
  -DskipTests compile`, Java 17 on JDK 25) use the mechanical jqassistant oracle.
- The calibration gate (bank-chat, all-mechanical categories at 1.0) still
  validates the mechanical oracle. Re-pointing shopizer at jqassistant once its
  build is fixed (mirror the snapshots repo / pin a buildable commit) is a
  Plan 2/3 follow-up; the expected answers can then be regenerated mechanically
  and diffed against the manual truth recorded here.

