<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Faster init/increment — bulk graph writes + cached ignore

## Status
Proposal — not yet implemented. Design-only; no production code in this PR.

Scope agreed with maintainer: PR-1 rewrites the **full rebuild path only** (init / reprocess); the incremental path and the ignore-cache fix follow as separate PRs under this same proposal.

## Problem Statement

`init` / `increment` wall-clock is the project's stated pain point. A profiled `java-codebase-rag init` on a medium Java corpus (Shopizer: 1210 files → 1167 indexed, 3879 chunks, ~32k graph edges, total **395s**) breaks down as:

| phase | time | share |
|---|---|---|
| **LadybugDB graph write** (edges ~250s + nodes ~62s + routes ~4s write + passes ~5s analysis) | **~321s** | **~81%** |
| cocoindex vectors (embed ~16s on MPS + LayeredIgnore-per-file ~25s + parse/enrich ~3s + LanceDB/orchestration residual) | ~68s | ~17% |
| optimize | ~5s | ~1% |

Two independent root causes account for almost all of it:

1. **Per-row graph writes (the ~81% lever).** `build_ast_graph.py` writes every node and edge one statement at a time via `conn.execute(query, row)` inside loops — ~21 per-row `conn.execute` sites across the full-rebuild write functions (44 is the file-wide total). Nodes via `_write_nodes` (`build_ast_graph.py:3096`, impl `_write_nodes_impl:3029` using `_CREATE_SYMBOL`/`_MERGE_SYMBOL`, strings at `:3007-3026`), called from `write_ladybug:3893`; edges at `3250-3398`. Measured ~7.8 ms/edge → ~250s for ~32k edges. A micro-benchmark on the real Symbol schema measured ~300×: ~5.6 ms/row per-row vs ~0.018 ms/row bulk `COPY FROM`.
2. **`LayeredIgnore` rebuilt per file (a ~25s waste inside the vectors phase).** `process_java_file` / `process_sql_file` / `process_yaml_file` in `java_index_flow_lancedb.py` each construct `LayeredIgnore(project_root)` once per file *and* re-run `is_ignored`'s `_mega(spec)` merge per file. On 1167 files that is ~25s of work for an object + spec that are identical for the whole flow run.

A third candidate — defaulting the embedding device to MPS — was investigated and rejected (see Out of scope): the flow already auto-selects MPS, so the profiled embed ran on MPS (~16s), not CPU.

## Proposed Solution

Three PRs. The graph write is by far the largest lever, so PR-1 is the priority; PR-2 and PR-3 are independent of each other and can land in any order.

### PR-1 — Bulk `COPY FROM` for the full rebuild path

The full build assembles the entire graph in memory (`GraphTables`, fully populated by pass1–pass6 before any `_write_*` call, `build_ast_graph.py:3914`) and then writes it. That makes bulk insert a clean swap: instead of per-row `conn.execute` loops, stage the assembled rows and load them with kuzu `COPY FROM`.

**Mechanism — in-memory pyarrow.** The `ladybug` wrapper's first-class bulk path is `COPY <table> FROM $param` with an in-memory pyarrow table (`conn.execute` forwards `COPY FROM` verbatim and accepts a pyarrow param). Build the `pa.table` from the existing in-memory `*_rows` lists — zero disk I/O, native to the wrapper. Parquet-file staging is the fallback only.

**Staging invariants (must hold for byte-equivalence):**
- **REL-table column rule.** kuzu `COPY FROM` into a REL table requires the first two columns to be the FROM/TO node primary keys (the node `id`). The staging shape per table must match the `_SCHEMA_*` constants exactly.
- **Materialize write-time work at staging.** Several rows are computed *during* the current per-row writes and must be produced before staging: the CALLS dedup (`seen_calls`, `build_ast_graph.py:3282-3288`), the `callee_declaring_role` lookup, and the UNRESOLVED dedup (`seen_ucs`, `3317-3321`). Apply these to the in-memory lists, then stage the result.
- **Node-before-edge ordering.** Stage and load all node tables before REL tables (kuzu enforces endpoint existence). The current `_write_*` call order already does this; preserve it.

**Atomicity.** The current per-row path is not atomic (per-statement autocommit; a crash mid-build leaves a partial graph). `COPY FROM` raises this to per-table atomicity — an improvement, not a regression; the overall "rebuild in place" crash-safety story is unchanged.

**Equivalence.** The rewritten build must produce a byte-equivalent graph, proven by an equivalence harness (see Tests): node/edge counts, GraphMeta counters, full edge property rows (incl. `source_file`, CALLS `callee_declaring_role`), and a battery of Cypher queries identical between old and new.

Expected: ~321s graph-write phase → tens of seconds; overall `init` on this corpus from ~395s toward ~120s (projected; measured in PR-1).

### PR-2 — Bulk write for the incremental path (follow-up)

Refactor a shared stage→`COPY FROM` primitive out of PR-1 and apply it to the incremental `_delete_file_scope` → re-emit flow, preserving the pass5/6 `MERGE (r:Route)` dedup semantics (`build_ast_graph.py:3819-3821`).

### PR-3 — Cached `LayeredIgnore` (+ `is_ignored` memo) as a `ContextKey`

Hoist the three per-file `LayeredIgnore(project_root)` constructions (`java_index_flow_lancedb.py:351/423/471`) into a single instance built once per flow run via a cocoindex `ContextKey` (lifespan-scoped — `PROJECT_ROOT`, `EMBEDDER`, `LANCE_DB` are already `ContextKey`s in `coco_lifespan`, `:60-72`/`:287-306`). Additionally memoize `is_ignored`'s `_mega(spec)` merge (cache the merged spec, or LRU by relative path) — the per-file cost is in `is_ignored`, not just the constructor, so construction hoisting alone will not reach ~0s. Keeps the cocoindex dependency inside `java_index_flow_lancedb.py` (AGENTS.md compliant). Expected ~25s → ~0s.

## Scope

### In scope

- **PR-1:** replace per-row node/edge writes in the full rebuild path (`write_ladybug:3893` → `_write_nodes:3096` → `_write_nodes_impl:3029`; edge emit `3250-3398`) with bulk in-memory-pyarrow `COPY FROM`; add equivalence harness + benchmark.
- **PR-2:** shared bulk primitive applied to the incremental path (preserve Route-MERGE dedup).
- **PR-3:** hoist `LayeredIgnore` to a flow-lifespan `ContextKey` and memoize `is_ignored`.

### Out of scope

- **MPS embedding default — not needed.** The init flow already auto-selects MPS on Apple Silicon: `SBERT_DEVICE` unset → `config.py:280` omits it from the subprocess env → flow constructs `SentenceTransformerEmbedder(device=None)` (`java_index_flow_lancedb.py:291`) → `SentenceTransformer(device=None)` auto-detects `cuda → mps → cpu`. On this machine `torch.backends.mps.is_available()` is true, so the profiled init ran on MPS (~16s embed). There is no CPU→MPS win to recover; an override already exists (`SBERT_DEVICE` / `--embedding-device` / YAML `embedding.device`).
- ANN vector index — parked (issue #337); query latency is fine today and ANN would tax indexing.
- `watch` live mode — issue #336.
- Replacing or restructuring the cocoindex flow.
- Changing the embedding model or dimension.
- Parallelizing the graph analysis passes (pass1–pass6).
- Converting the incremental write path in PR-1 (it is PR-2).

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required. No node/edge kinds, properties, or enrichment semantics change. `ontology_version` stays 17.
- **Re-index required:** no. PR-1/2 change only the write mechanism; PR-3 changes only a cache. The graph contents are identical (proven by the equivalence harness), so users pick up the faster path on their next `init` / `reprocess` / `increment` naturally — no migration.
- **Config / tool surface:** none new.

## Tests / Validation

1. **PR-1 equivalence harness (mandatory).** Build the same source tree old-way (per-row) and new-way (`COPY FROM`); assert identical: node count, per-type edge counts, `GraphMeta` counters (via `java-codebase-rag meta` / `GraphMetaOutput`), full property rows for a sample of N edges per type (including `source_file` and CALLS `callee_declaring_role` — proving the staging dedup/materialization is correct), and a battery of representative Cypher queries (`neighbors`, `find`, `describe`) returning identical rows. Run on `tests/bank-chat-system`, the call-graph smoke fixture, and one larger corpus.
2. **PR-1 benchmark.** Capture `init` wall-clock before/after on the medium corpus; report the graph-write phase delta.
3. **PR-2 incremental equivalence.** `increment` after a single-file change yields the same graph as a full rebuild of that state (reuse the harness).
4. **PR-3.** Assert the ignore object is constructed once per flow run (not per file) and `is_ignored` is memoized; existing flow tests unchanged; micro-benchmark confirms the ~25s drop.

## Open Questions ([TBD])

1. Should the `GraphMeta` single-row MERGE (`build_ast_graph.py:3472-3473`) also move to bulk in PR-1, or stay per-row? — Recommended: **fold it into PR-1** (it is in the full-rebuild path; one extra small staging set).
2. PR-3 cache vehicle — cocoindex `ContextKey` vs a module-global? — Recommended: **`ContextKey`** (cocoindex-native, lifespan-scoped, keeps the dependency in the flow module).

## Sequencing / Follow-ups

- **PR-1** — bulk in-memory-pyarrow `COPY FROM` for the full rebuild path + equivalence harness + benchmark. Biggest win (~81% phase).
- **PR-2** — shared bulk primitive applied to the incremental path. Depends on PR-1's primitive.
- **PR-3** — `LayeredIgnore` + `is_ignored` → flow-lifespan `ContextKey`. Independent of PR-1/2.
