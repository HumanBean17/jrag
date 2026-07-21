# Pre-registration — jrag Effectiveness Benchmark (Plan 1 freeze)

Pre-registration discipline: the claims and the question inventory below are
**frozen before any agent run** (Plan 2/3), so post-hoc metric selection is
impossible. The full grading rubric is finalized with the grader in Plan 2; here
we freeze only the claims, the question inventory, and the programmatic-vs-judge
grading split.

- **Frozen at repo SHA:** `c64b145e594626ba6526dbf1c9f3d362c94a06b1` (branch
  `bench-foundation`).
- **jrag / java-codebase-rag version:** `0.12.0` (python 3.11.4).
- **Ontology version:** `19` (read from index meta; recorded in `corpora.yml`).
- **jqassistant:** CLI `2.9.1` (neo4jv5 distribution, Neo4j 5.26.20).
- **Corpora (pinned SHAs in `bench/corpora.yml`):**
  - bank-chat-system — local fixture, pinned to repo SHA `e042940c…`
  - shopizer — `6a4a0a65a3408ee8f62597b51d1b3aac24b77dee`
  - spring-petclinic-microservices — `305a1f13e4f961001d4e6cb50a9db51dc3fc5967`
- **Calibration gate:** passed on bank-chat — every mechanical category
  (interface-impls, upstream-consumers, role-listing, blast-radius) at ratio 1.0,
  overall 10/10, threshold 0.9 (see `bench/oracle/calibration_report.json`).

## Claims under test (C1–C6, verbatim from the spec)

| # | Claim | Metric | Question subset |
|---|-------|--------|-----------------|
| **C1** | On structural questions (impls / callers / injectors / blast-radius), jrag answers more correctly than vector-only and grep. | Answer correctness (0–1); retrieval precision/recall vs oracle. | All `interface-impls`, `upstream-consumers`, `call-trace`, `blast-radius`, `role-listing`, `absence`, and `semantic` questions (40 of 50). |
| **C2** | jrag reaches a correct answer in fewer agent steps and fewer tokens (graph hops vs reading whole files). | Steps-to-answer; total tokens; context bytes. | All 50 questions. |
| **C3** | On cross-service questions, per-file/per-repo baselines fail structurally; jrag resolves them. | Cross-service subset correctness; binary "resolved the seam?". | All 10 `cross-service` questions + blast-radius (C1∩C3). |
| **C4** | Re-indexing is deterministic — identical node/edge counts run-to-run, unlike LLM-built graphs. | Graph-stat diff across rebuilds. | Per-corpus (not per-question). bank-chat n=2 confirmed identical (`PHASE0_FINDINGS.md`). |
| **C5** | Index build + per-query cost stay within ~2× of vector-only, not 20×. | Build time; on-disk size; $/query. | Per-corpus (`corpora.yml` build_time_s / on_disk_bytes). |
| **C6** *(directional)* | The structural advantage holds across model capability tiers, and weaker models benefit more. | Correctness gap (jrag − baseline) per model tier. | All questions, sliced by model. |

**C6 limitation:** two model tiers (glm-4.7 < glm-5.1) show a *direction* only;
not statistically powered. A third tier (e.g. glm-4.5-air) is a Phase 6 stretch.

## Question inventory (50; generated from live files — do not hand-edit)

Regenerate with:
`python -c "from bench.load_questions import load_all_questions; ..."` (see
`bench/load_questions.py`). Distribution: bank-chat 20, shopizer 15, petclinic 15.

| id | corpus | category | difficulty | claim_refs | grading | oracle_source |
|----|--------|----------|------------|------------|---------|---------------|
| bc-abs-01 | bank-chat-system | absence | medium | C1 | absence_check | manual |
| bc-blast-01 | bank-chat-system | blast-radius | hard | C1,C3 | programmatic_set_match | jqassistant:transitive_blast.cypher |
| bc-blast-02 | bank-chat-system | blast-radius | hard | C1,C3 | programmatic_set_match | jqassistant:transitive_blast.cypher |
| bc-cs-01 | bank-chat-system | cross-service | hard | C3 | programmatic_client_route_match | manual |
| bc-cs-02 | bank-chat-system | cross-service | hard | C3 | programmatic_client_route_match | manual |
| bc-cs-03 | bank-chat-system | cross-service | hard | C3 | programmatic_client_route_match | manual |
| bc-cs-04 | bank-chat-system | cross-service | hard | C3 | programmatic_client_route_match | manual |
| bc-cs-05 | bank-chat-system | cross-service | hard | C3 | programmatic_client_route_match | manual |
| bc-cs-06 | bank-chat-system | cross-service | medium | C3 | programmatic_client_route_match | manual |
| bc-impl-01 | bank-chat-system | interface-impls | easy | C1 | programmatic_set_match | jqassistant:implements.cypher |
| bc-impl-02 | bank-chat-system | interface-impls | easy | C1 | programmatic_set_match | jqassistant:implements.cypher |
| bc-impl-03 | bank-chat-system | interface-impls | easy | C1 | programmatic_set_match | jqassistant:implements.cypher |
| bc-role-01 | bank-chat-system | role-listing | easy | C1 | programmatic_set_match | jqassistant:role_controllers.cypher |
| bc-role-02 | bank-chat-system | role-listing | easy | C1 | programmatic_set_match | jqassistant:role_controllers.cypher |
| bc-sem-01 | bank-chat-system | semantic | medium | C1 | llm_judge | manual |
| bc-trace-01 | bank-chat-system | call-trace | hard | C1,C2 | programmatic_path_match | manual |
| bc-trace-02 | bank-chat-system | call-trace | hard | C1,C2 | programmatic_path_match | manual |
| bc-up-01 | bank-chat-system | upstream-consumers | medium | C1 | programmatic_set_match | jqassistant:injects.cypher |
| bc-up-02 | bank-chat-system | upstream-consumers | medium | C1 | programmatic_set_match | jqassistant:injects.cypher |
| bc-up-03 | bank-chat-system | upstream-consumers | medium | C1 | programmatic_set_match | jqassistant:injects.cypher |
| sh-abs-01 | shopizer | absence | medium | C1 | absence_check | manual |
| sh-blast-01 | shopizer | blast-radius | hard | C1,C3 | programmatic_set_match | manual |
| sh-blast-02 | shopizer | blast-radius | hard | C1,C3 | programmatic_set_match | manual |
| sh-impl-01 | shopizer | interface-impls | medium | C1 | programmatic_set_match | manual |
| sh-impl-02 | shopizer | interface-impls | medium | C1 | programmatic_set_match | manual |
| sh-impl-03 | shopizer | interface-impls | medium | C1 | programmatic_set_match | manual |
| sh-role-01 | shopizer | role-listing | easy | C1 | programmatic_set_match | manual |
| sh-role-02 | shopizer | role-listing | easy | C1 | programmatic_set_match | manual |
| sh-role-03 | shopizer | role-listing | easy | C1 | programmatic_set_match | manual |
| sh-sem-01 | shopizer | semantic | medium | C1 | llm_judge | manual |
| sh-sem-02 | shopizer | semantic | medium | C1 | llm_judge | manual |
| sh-trace-01 | shopizer | call-trace | hard | C1,C2 | programmatic_path_match | manual |
| sh-up-01 | shopizer | upstream-consumers | medium | C1 | programmatic_set_match | manual |
| sh-up-02 | shopizer | upstream-consumers | medium | C1 | programmatic_set_match | manual |
| sh-up-03 | shopizer | upstream-consumers | medium | C1 | programmatic_set_match | manual |
| pt-abs-01 | spring-petclinic-microservices | absence | medium | C1 | absence_check | manual |
| pt-blast-01 | spring-petclinic-microservices | blast-radius | hard | C1,C3 | programmatic_set_match | jqassistant:transitive_blast.cypher |
| pt-cs-01 | spring-petclinic-microservices | cross-service | hard | C3 | programmatic_client_route_match | manual |
| pt-cs-02 | spring-petclinic-microservices | cross-service | hard | C3 | programmatic_client_route_match | manual |
| pt-cs-03 | spring-petclinic-microservices | cross-service | hard | C3 | programmatic_client_route_match | manual |
| pt-cs-04 | spring-petclinic-microservices | cross-service | medium | C3 | programmatic_client_route_match | manual |
| pt-impl-01 | spring-petclinic-microservices | interface-impls | easy | C1 | programmatic_set_match | jqassistant:implements.cypher |
| pt-role-01 | spring-petclinic-microservices | role-listing | easy | C1 | programmatic_set_match | jqassistant:role_controllers.cypher |
| pt-role-02 | spring-petclinic-microservices | role-listing | easy | C1 | programmatic_set_match | jqassistant:role_controllers.cypher |
| pt-role-03 | spring-petclinic-microservices | role-listing | easy | C1 | programmatic_set_match | jqassistant:role_controllers.cypher |
| pt-sem-01 | spring-petclinic-microservices | semantic | medium | C1 | llm_judge | manual |
| pt-trace-01 | spring-petclinic-microservices | call-trace | hard | C1,C2 | programmatic_path_match | manual |
| pt-trace-02 | spring-petclinic-microservices | call-trace | hard | C1,C2 | programmatic_path_match | manual |
| pt-up-01 | spring-petclinic-microservices | upstream-consumers | medium | C1 | programmatic_set_match | jqassistant:injects.cypher |
| pt-up-02 | spring-petclinic-microservices | upstream-consumers | medium | C1 | programmatic_set_match | jqassistant:injects.cypher |

## Grading split (frozen; full rubric finalized in Plan 2)

- **Programmatic graders (objective, no judge):**
  - `programmatic_set_match` — exact FQN-set equality vs oracle (interface-impls,
    upstream-consumers, role-listing, blast-radius).
  - `programmatic_path_match` — ordered hop equality (call-trace); order matters.
  - `programmatic_client_route_match` — set of `(client_fqn, route,
    target_service)` tuples (cross-service).
  - `absence_check` — verdict equality (`not_in_project` vs found).
  - `programmatic_jaccard` — FQN-set Jaccard (reserved; not used by any current
    question).
- **LLM-judge (glm-5.2, condition-blinded):** `llm_judge` — all `semantic`
  questions (4). Transcript scrubbed of tool names / MCP identifiers so the judge
  cannot favor "the fancy tool." Human κ gate (≥0.6) on a random 20% of judged
  answers before unblinding.

Count: 46 programmatic + 4 judge-graded = 50.

## Honesty commitments

- The **semantic** category is *expected* to be where vector-only (condition B)
  ties or beats jrag — that is a feature of the report, not a bug. Losses will be
  visible.
- **Raw transcripts** (per-cell `claude -p` stream-json) will be published with
  the results; no answer is retried (a wrong answer is data; only API errors are
  retried).
- shopizer's ground truth is **manual** (its Maven build is broken; see
  `PHASE0_FINDINGS.md`) — reported as such, with a Plan 2/3 follow-up to
  re-point it at the mechanical oracle once the build is fixed.
- No metric is added post-hoc. If a budget dial is pulled (seeds 3→2, drop
  condition C, trim questions), it is recorded in the results report.

## TL;DR

Frozen at repo SHA `c64b145`: 6 pre-registered claims (C1–C6), 50 engineer-phrased
golden questions (bank-chat 20 / shopizer 15 / petclinic 15) with frozen
expected answers (jqassistant 17, manual 33), a programmatic-vs-judge grading
split (46 programmatic, 4 judged), and the calibration gate passed on bank-chat
(all mechanical categories 1.0). Versions: jrag 0.12.0, ontology 19, jqassistant
2.9.1. Plan 2 builds the agent harness against this frozen ground truth.

## Amendment 2026-07-21

Three design decisions, finalized during Plan 2 implementation, are recorded here as locked amendments to the pre-registration.

**(a) Condition-C relabel and enforcement** — Condition C's `name` is changed to `Raw agent + shell (no Grep tool, no MCP)` to accurately reflect its actual tool exposure (`allowed_tools: [Read, Glob, Bash]`). The Grep tool is now DENIED directly via `disallowed_tools: ["Grep"]` (so "no Grep tool" is enforced by the harness, not merely monitored); the Bash-shell grep/find/cat CAPABILITY leak remains accepted as the documented caveat of the "raw agent + shell" relabel (Bash cannot be prohibited without also disabling shell grep). C's `allowed_tools` and `mcp_servers` remain unchanged.

**(b) Ablation decision** — Of the four ablation knobs considered for condition D, only D₃ (`brownfield_only`) is in scope for the benchmark. D₂ (role-ranking) is excluded because there is no runtime knob to disable it (it requires source-code instrumentation, out of scope). D₄ (graph-expansion) is excluded because the `context_neighbors` toggle is already off in the MCP configuration, and `graph_expand` is a different feature.

**(c) Temperature and seed property** — The `claude -p` CLI exposes no flags for temperature or seed. These parameters (`seed`, `temperature`) are recorded as metadata in the run artifacts only. No determinism claim is made about the agent run.
