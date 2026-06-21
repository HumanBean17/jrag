# INIT-INCREMENT-PERF-PROPOSE

## Status
Proposal — not yet implemented. Design-only; no production code in this PR.
Scope agreed with maintainer: PR-1 rewrites the **full rebuild path only**
(init / reprocess); the incremental path and the two smaller levers follow as
separate PRs under this same proposal.

## Problem Statement
`init` / `increment` wall-clock is the project's stated pain point. A profiled
`java-codebase-rag init` on a medium Java corpus (Shopizer: 1210 files → 1167
indexed, 3879 chunks, ~32k graph edges, total **395s**) breaks down as:

| phase | time | share |
|---|---|---|
| **LadybugDB graph write** (edges ~250s + nodes ~62s + routes/meta ~10s) | **~322s** | **~81%** |
| cocoindex vectors (embed ~28s + LayeredIgnore-per-file ~25s + parse/enrich ~3s + LanceDB ~12s) | ~68s | ~17% |
| optimize | ~5s | ~1% |

Three independent root causes surface:

1. **Per-row graph writes (the ~81% lever).** `build_ast_graph.py` writes every
   node and edge one statement at a time via `conn.execute(query, row)` inside
   loops — nodes at `build_ast_graph.py:3046-3093` (`_MERGE_SYMBOL` /
   `_CREATE_SYMBOL`), edges at `3250-3398` (the `CREATE (...)` strings defined
   `3108-3315`). 44 `conn.execute` call sites, almost all in per-row loops.
   Measured ~7.8 ms/edge → ~250s for ~32k edges. kuzu (LadybugDB) is 1–2 orders
   faster via `COPY FROM` bulk import.
2. **`LayeredIgnore` rebuilt per file (a ~25s waste inside the vectors phase).**
   `process_java_file` / `process_sql_file` / `process_yaml_file` in
   `java_index_flow_lancedb.py` each construct `LayeredIgnore(project_root)`
   once per file. On 1167 files that is ~25s of pure re-construction of an
   object that is identical for the whole flow run.
3. **Embeddings run on CPU by default (~28s) when MPS is available (~16s).**
   `SBERT_DEVICE` is unset → the embedder defaults to CPU. On Apple Silicon MPS
   is available and ~1.7x faster for `all-MiniLM-L6-v2`; the device resolution
   never considers it.

## Proposed Solution

### PR-1 — Bulk `COPY FROM` for the full rebuild path (the big win)
The full build assembles the entire graph in memory (`GraphTables`) and then
writes it. That makes bulk insert a clean swap: instead of 44 per-row
`conn.execute` loops, stage the assembled nodes and edges and load them with
kuzu `COPY <table> FROM <source>`.

- **Paths in scope:** the full rebuild used by `init` and `reprocess`
  (`_write_nodes_impl(...)` callers at `build_ast_graph.py:824-825` and `:3103`,
  plus the edge-emission block `3250-3398`).
- **Paths NOT in scope for PR-1:** the incremental delete-then-emit path
  (`_delete_file_scope`, `:673`, and the pass5/6 `Route` MERGE at `:3819-3821`).
  Incremental touches a small scope (changed files + single-hop dependents), so
  its per-row cost is low; converting it is a follow-up PR under this proposal.
- **Mechanism:** stage node rows and each edge-type's rows to a bulk source, then
  `COPY FROM`. Recommended source format is **Parquet** (see Open Question 1) —
  `pyarrow` is already a transitive dependency via `lancedb`, and Parquet avoids
  CSV-quoting hazards for Java FQNs / annotations / signatures.
- **De-risk:** PR-1 begins with a ~10-line spike confirming `COPY FROM` passes
  through the `ladybug` wrapper unchanged (Open Question 2), then proceeds to the
  rewrite.
- **Equivalence:** the rewritten build MUST produce a byte-for-byte equivalent
  graph. An equivalence harness (see Tests) proves node/edge counts, GraphMeta
  counters, and a battery of Cypher queries are identical between old and new.

Expected: the ~312s graph-write phase → tens of seconds; overall `init` on this
corpus from ~395s toward ~120s (projected; measured in PR-1).

### PR-2 (follow-up) — Bulk write for the incremental path
Refactor a shared stage→`COPY FROM` primitive out of PR-1 and apply it to the
incremental `_delete_file_scope` → re-emit flow, preserving the pass5/6
`MERGE (r:Route)` dedup semantics (`build_ast_graph.py:3819-3821`).

### PR-3 — Cache `LayeredIgnore` as a cocoindex `ContextKey`
Replace the three per-file `LayeredIgnore(project_root)` constructions with a
single ignore instance built once per flow run, exposed via a cocoindex
`ContextKey` (lifespan-scoped). The ignore decision per file is unchanged; only
construction is hoisted. Keeps the cocoindex dependency inside
`java_index_flow_lancedb.py` (AGENTS.md compliant). Expected ~25s → ~0s.

### PR-4 — Default embedding device to MPS when available
Extend the device resolution in `index_common.py` to `cuda → mps → cpu`
(overridable via the existing `SBERT_DEVICE`). On Apple Silicon this cuts embed
~28s → ~16s; on Linux servers / CI without MPS it falls back to CPU unchanged.
Same model, same 384-dim embeddings — only the backend changes.

## Scope
- **PR-1:** replace per-row node/edge writes in the full rebuild path with
  bulk `COPY FROM`; add equivalence harness + benchmark.
- **PR-2:** shared bulk primitive applied to the incremental path.
- **PR-3:** hoist `LayeredIgnore` to a flow-lifespan `ContextKey`.
- **PR-4:** `cuda → mps → cpu` device default in `index_common.py`.
- No new MCP tools, no new env vars (MPS reuses `SBERT_DEVICE`), no new public
  surface.

## Schema / Ontology / Re-index impact
- **Ontology bump:** not required. No node/edge kinds, properties, or
  enrichment semantics change. `ontology_version` stays 17.
- **PR-1 / PR-2 re-index:** not required. The graph contents are identical
  (proven by the equivalence harness); only the write mechanism changes. Users
  pick up the faster path on their next `init` / `reprocess` / `increment`
  naturally.
- **PR-3 re-index:** not required. Same chunks, same vectors; only the ignore
  check is faster.
- **PR-4 re-index:** recommended (optional), not required. Switching the
  default backend to MPS changes stored embeddings at the ~1e-5 level (different
  kernel numerics); cosine ranking is stable, so existing CPU-built indexes keep
  working, but a fresh `init` yields a single consistent backend. Needs a README
  "Re-index recommended" callout.
- **Config / tool surface:** none new.

## Tests / Validation
- **PR-1 equivalence harness (mandatory):** build the same source tree old-way
  (per-row) and new-way (`COPY FROM`); assert identical: node count, per-type
  edge counts, `GraphMeta` counters (via `java-codebase-rag meta` /
  `GraphMetaOutput`), and a battery of representative Cypher queries
  (`neighbors`, `find`, `describe`) return identical rows. Run on
  `tests/bank-chat-system`, the call-graph smoke fixture, and one larger corpus.
- **PR-1 benchmark:** capture `init` wall-clock before/after on the medium
  corpus; report the graph-write phase delta.
- **PR-2:** incremental equivalence — `increment` after a single-file change
  yields the same graph as a full rebuild of that state (reuse the harness).
- **PR-3:** assert the ignore object is constructed once per flow run (not per
  file); existing flow tests unchanged; micro-benchmark confirms the ~25s drop.
- **PR-4:** unit test that device resolution prefers mps when
  `torch.backends.mps.is_available()` (monkeypatched), falls back to cpu
  otherwise; embedding shape/dim unchanged.

## Open Questions ([TBD])
1. **Bulk source format** — Parquet vs CSV. Recommended: **Parquet** —
   `pyarrow` is already present (transitive via `lancedb`), and it sidesteps
   CSV quoting for Java FQNs / annotations / signatures. CSV is the simpler
   fallback if Parquet proves awkward through the wrapper.
2. **Does `COPY FROM` pass through the `ladybug` wrapper unchanged?** —
   Recommended: confirm with a ~10-line spike as the first step of PR-1
   (low-cost de-risk, folded into PR-1, not a separate spike PR). kuzu 0.11.3
   supports `COPY FROM` natively; the only unknown is whether `ladybug`'s
   `conn.execute` forwards it verbatim.
3. **MPS-vs-CPU numerical drift (PR-4)** — re-index required or optional?
   Recommended: **optional**; document in a README "Re-index recommended"
   callout. Cosine ranking is stable across the ~1e-5 backend difference.
4. **PR-3 cache vehicle** — cocoindex `ContextKey` vs a module-global?
   Recommended: **`ContextKey`** (cocoindex-native, correct across multiple flow
   runs / lifespans, keeps the dependency in the flow module).
5. **Does PR-1 touch `increment`?** — No. Per agreed scope, `increment` keeps
   its current per-row write until PR-2. PR-1 is init/reprocess only.

## Out of scope
- ANN vector index — parked (issue #337); query latency is fine today and ANN
  would tax indexing.
- `watch` live mode — issue #336.
- Replacing or restructuring the cocoindex flow.
- Changing the embedding model or dimension.
- Parallelizing the graph analysis passes (pass1–pass6).
- Converting the incremental write path in PR-1 (it is PR-2).

## Sequencing / Follow-ups
- **PR-1** — bulk `COPY FROM` for the full rebuild path + equivalence harness +
  benchmark. Biggest win (~81% phase). Starts with the ladybug-pass-through
  spike (Open Question 2).
- **PR-2** — shared bulk primitive applied to the incremental path (preserve
  Route-MERGE dedup).
- **PR-3** — `LayeredIgnore` → flow-lifespan `ContextKey`.
- **PR-4** — `cuda → mps → cpu` device default + README callout.
- PR-3 and PR-4 are independent of PR-1/2 and of each other; they can land in
  any order. PR-2 depends on PR-1's shared primitive.

## PR body (proposal-only) template
### What
Adds `propose/active/INIT-INCREMENT-PERF-PROPOSE.md` describing the init /
increment performance program: bulk `COPY FROM` graph writes (full path first),
lifespan-cached `LayeredIgnore`, and an MPS embedding default.

### Why now
Profiling (2026-06-21) showed graph writes are ~81% of `init`; the three levers
above are measured, independent, and unblock the project's stated init/increment
latency pain.

### Highlights
- PR-1: bulk `COPY FROM` for the full rebuild path — projected ~312s graph write
  → tens of seconds; `init` ~395s → ~120s on the profiled corpus.
- PR-2: same primitive extended to the incremental path.
- PR-3: hoist `LayeredIgnore` to a `ContextKey` — ~25s → ~0s.
- PR-4: default embedding device `cuda → mps → cpu` — ~28s → ~16s on Apple Silicon.
- No ontology bump; PR-1/2/3 re-index-free; PR-4 optional re-index callout.

### Tests
Proposal-only; baseline unchanged.

### Out of scope
- Implementation of any PR (PR-1…PR-4 follow).
- ANN index (#337) and watch mode (#336).
