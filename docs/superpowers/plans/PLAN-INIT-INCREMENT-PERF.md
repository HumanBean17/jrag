<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: Faster init/increment — bulk graph writes + cached ignore

Status: **active (planning)**. This plan implements
[`propose/active/INIT-INCREMENT-PERF-PROPOSE.md`](../../propose/active/INIT-INCREMENT-PERF-PROPOSE.md).
The proposal lands via PR #338; this plan lands via PR #339 and **stacks behind
#338** (the proposal file is on #338's branch until it merges). The staging
invariants are inlined below so this plan is self-contained if #338 has not
merged yet.

Depends on: PR #338 (proposal) — non-blocking for reading this plan (key
invariants inlined), but the proposal should merge first.

> **Revised after a 5-lens subagent review (PR #339 thread).** The original
> draft assumed `_write_edges` / `_write_routes_and_exposes` / `_write_meta`
> were full-rebuild-only and split PRs by *path*. They are **shared by both
> paths** (verified: `_write_edges` `build_ast_graph.py:3244` is called at
> `:3926` full + `:805` incremental; `_write_routes_and_exposes` `:3338` at
> `:3930` full + `:811` incremental; `_write_meta` `:3421` at `:3933` full +
> `:3733` incremental). PRs are now split by **write-function**, not by path,
> and GraphMeta is left on MERGE (not bulk-converted).

## Goal

- Cut `init` / `reprocess` / `increment` graph-write wall-clock by replacing
  per-row `conn.execute` writes with bulk `COPY FROM` — the ~81% init lever.
- Remove the ~25s `LayeredIgnore` re-construction + `is_ignored` re-merge paid
  per file in the cocoindex vectors phase.
- Preserve graph contents exactly: no ontology bump, no re-index, no
  query-result change (proven by an equivalence harness).

## Principles (do not relitigate in review)

- **Byte-equivalent graph.** Every PR changes only the write mechanism; the
  graph (node/edge rows, properties, `GraphMeta` counters) must be identical to
  today. The equivalence harness + the full `test_incremental_graph.py` suite
  are the merge gate.
- **Split by write-function, not by path.** The graph write helpers are shared
  between the full path (`write_ladybug:3893`) and the incremental path
  (`incremental_rebuild:3535`): `_write_edges`, `_write_routes_and_exposes`,
  `_write_nodes_impl`, `_write_meta` are each called by BOTH. Converting a
  shared helper accelerates **both** paths at once — there is no "full-only"
  conversion for edges/routes. `_write_clients_producers_and_calls:3810` is
  **incremental-only** (the global pass5/6 step; sole caller `:3716`).
- **GraphMeta stays on MERGE.** `_write_meta:3421` is shared and recomputes
  counters before a single `MERGE (m:GraphMeta {key:$k})` (`:3472`). It is one
  row and not worth the risk — **do not bulk-convert it**. (This reverses the
  proposal's Open Q1 recommendation.)
- **In-memory pyarrow `COPY FROM`** is the bulk mechanism (verified: the
  `ladybug` wrapper forwards `COPY FROM` and accepts a pyarrow param —
  `ladybug/connection.py:337`/`:488`). Parquet-file is a fallback, not the
  default. Do not propose CSV.
- **MPS device default is out of scope** — the flow already auto-selects MPS.
- **No new env vars, CLI flags, or public surface.** No compatibility shims.

## PR breakdown - overview

| PR | Scope (write-functions converted) | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-P1** | Add `_bulk_copy`; convert **`_write_edges`** (shared → both paths' Symbol→Symbol edges + UnresolvedCallSite/UNRESOLVED_AT) | none | REL-table `COPY FROM` column order (FROM/TO first); CALLS dedup + `callee_declaring_role` materialized at staging; UnresolvedCallSite loaded before UNRESOLVED_AT | equivalence + determinism + baseline; full `test_ast_graph_build.py` **and** `test_incremental_graph.py` (shared helper) | — |
| **PR-P2** | Convert **`_write_nodes_impl`** (shared nodes), **`_write_routes_and_exposes`** (shared routes/clients/producers/calls), **`_write_clients_producers_and_calls`** (incremental-only global; Route MERGE preserved) | none | Route/Client/Producer nodes loaded before EXPOSES/DECLARES_*/HTTP_CALLS/ASYNC_CALLS; the 6 client/producer `_CREATE_*` constants are shared with the incremental-only function — delete only after BOTH convert; pass5/6 Route MERGE retained | `test_incremental_graph.py` regression + new incremental-equivalence test | depends on **PR-P1** (`_bulk_copy`) |
| **PR-P3** | Hoist `LayeredIgnore` to a cocoindex `ContextKey` + memoize `is_ignored`'s `_mega` by directory | none | `ContextKey` lifespan scoping; `_mega` memo correctness (mega depends on directory only); leave the once-per-run sites (`:177`, `:569`) alone; no change to any ignore *decision* | `tests/test_path_filtering.py` memo tests + `tests/test_lancedb_e2e.py` (HEAVY) once-per-flow | independent of PR-P1, PR-P2 |

Landing order: **P1 → P2**; **P3** may land in any order (independent).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Bulk mechanism | In-memory pyarrow: `conn.execute("COPY <table> FROM $rows", {"rows": pa_table})`. Verified `ladybug/connection.py:337` (`FROM $param`) + `:488` (pyarrow accepted). |
| REL-table column rule | First two staged columns are the FROM/TO node primary keys. Exact column naming for REL `COPY FROM` locked by the PR-P1 step-1 spike. |
| GraphMeta (`_write_meta`) | **Leave on MERGE.** Shared helper, one row, recomputes counters (`build_ast_graph.py:3421`). Reverses proposal Open Q1. |
| `_write_nodes_impl` (shared workhorse) | Converted in PR-P2; both `_write_nodes` (full, `_CREATE_SYMBOL`) and `_write_nodes_merge` (incremental, `_MERGE_SYMBOL`) call it, so converting it once kills both constants. |
| PR-P3 cache vehicle | cocoindex `ContextKey[LayeredIgnore]` (lifespan-scoped, built once). |
| `is_ignored` memo | Cache `_mega(rel)` → `(mega, spec, meta)` keyed by the file's project-relative **directory** (`Path(rel).parent.as_posix()`); `_mega_build_for_rel` reads only `dir_parts = parts[:-1]` (`path_filtering.py:226-227`), so this is correct. `spec.match_file(rel)` stays per-file. |
| Sites `:177` / `:569` | Left alone — both call `cocoindex_excluded_patterns()` **once per run** (the `_approximate_vectors_total` helper and the app_main pre-walk), not per-file. |

---

# PR-P1 — Bulk `COPY FROM` for `_write_edges` (shared; the ~250s prize)

**Goal:** add the bulk primitive and convert the shared `_write_edges` helper.
Because `_write_edges` is called by both paths, this accelerates the
Symbol→Symbol edges + UnresolvedCallSite writes for **both** `init` and
`increment` (the largest graph-write cost: ~250s of ~321s).

## File-by-file changes

### 1. `build_ast_graph.py` — `_bulk_copy` primitive + `_write_edges` conversion

#### 1a. New helper `_bulk_copy` (add near `_node_row`, ~`:2994`)
```python
import pyarrow as pa

def _bulk_copy(conn, table_name, columns, rows):
    """Bulk-load rows into a node/rel table via in-memory pyarrow COPY FROM.

    `columns` fixes column order; for REL tables the first two MUST be the
    FROM/TO node primary keys (kuzu requirement). Empty `rows` is a no-op.
    """
    if not rows:
        return
    tbl = pa.Table.from_pylist(rows)
    conn.execute(f"COPY {table_name} FROM $rows", {"rows": tbl})
```
Column-order constants next to the `_SCHEMA_*` strings (`~:2812`), each matching
its `_SCHEMA_*` order; for REL tables the first two entries are the endpoint ids:
`_REL_EXTENDS_COLUMNS = ["FROM","TO","source_file","dst_name","dst_fqn","resolved"]`,
`_REL_CALLS_COLUMNS = ["FROM","TO","source_file","callee_declaring_role", <…resolved props>]`,
`UNRESOLVED_CALL_SITE_COLUMNS`, `_REL_UNRESOLVED_AT_COLUMNS = ["FROM","TO","source_file"]`, etc.

> **Step-1 spike (mandatory, first commit of PR-P1):** confirm (a) the exact REL
> `COPY FROM` column naming — toy 2-Symbol + 1-CALLS `COPY CALLS FROM $rows`
> with `["FROM","TO",…]`, assert the edge lands with correct endpoints and
> `callee_declaring_role`; (b) `pa.Table.from_pylist` type inference for any LIST
> columns this helper touches. Record the working incantation in the docstring.
> (On kuzu 0.11.3 + the repo's pybind backend, all-empty LIST columns infer as
> `list<null>` and are accepted — confirmed by review — so an explicit pa.schema
> is a fallback, not required.)

#### 1b. Convert `_write_edges` (`:3244`, shared) to per-type staging + bulk
Today it loops `conn.execute(_CREATE_EXT, {…})` etc. with two dedup sets —
`seen_calls` (`:3282-3288`, key `(src_id,dst_id,arg_count,call_site_line)` —
verified) and `seen_ucs` (`:3317-3321`) — and a `callee_declaring_role` lookup
(`_callee_declaring_role_at_write`, `:1647`/`:3302`). Restructure to accumulate
per-edge-type row lists, applying the **same** dedup and **same**
`callee_declaring_role` materialization **before appending**, then `_bulk_copy`
each REL table once. REL row dicts get `FROM`/`TO` = src/dst node ids plus the
properties in `_SCHEMA_*` order.

**Within-helper load order (kuzu validates endpoint existence):** Symbol nodes
are already loaded by `_write_nodes` (called before `_write_edges` in both
paths). So bulk-load the **UnresolvedCallSite node rows before the UNRESOLVED_AT
edge rows** (UNRESOLVED_AT is Symbol→UnresolvedCallSite). The Symbol→Symbol edges
(EXTENDS/IMPLEMENTS/INJECTS/DECLARES/OVERRIDES/CALLS) reference only already-loaded
Symbol nodes.

`_CREATE_EXT`/`_CREATE_IMPL`/`_CREATE_INJ`/`_CREATE_DECL`/`_CREATE_OVERRIDES`/
`_CREATE_CALL` (module constants) become dead → delete. `_CREATE_UNRESOLVED` and
`_CREATE_UNRESOLVED_AT` are **locals** defined inside `_write_edges` (`:3307`/`:3313`),
removed when the function is rewritten to bulk. `_populate_declares_rows`
(`:3189`) / `_populate_overrides_rows` (`:3206`) are pure in-memory population —
unchanged.

### 2. `tests/test_ast_graph_build.py` — equivalence harness + baseline
The existing 26 tests are the regression net (e.g.
`test_schema_has_all_expected_tables`, `test_each_edge_type_populated`,
`test_pass3_callee_declaring_role_bank_annotated_types`,
`test_pass3_unresolved_call_site_emitted`,
`test_pass3_known_external_calls_preserved`). Add a committed baseline
`tests/fixtures/graph_baseline_bank_chat.json` (node count, per-type edge counts,
`GraphMeta` counters, and N=3 sampled edge property rows per type incl.
`source_file` and CALLS `callee_declaring_role`). **It is an equivalence anchor,
not a production invariant** — regenerated from the last per-row build before
removal, and regenerated only when `ontology_version` changes (it does not in
this plan). Asserting invariants here is acceptable because PR-P1 is a
behavior-preserving write-mechanism swap.

### 3. `scripts/bench_init_graph_write.py` (new, dev-only) — benchmark
Times `init`/`build_ast_graph.py` on a medium corpus; prints the graph-write
phase delta. Not packaged; documents the measured speedup in the PR description.

## Tests for PR-P1

1. `test_bank_chat_bulk_build_matches_committed_baseline` (renamed from
   `test_bulk_write_edges_match_per_row_baseline` in PR-P4 once the per-row
   reference was gone) — build `tests/bank-chat-system` via the bulk path,
   assert node count, per-type edge counts, `GraphMeta` counters, and sampled
   edge rows equal `graph_baseline_bank_chat.json` (a drift anchor, not a
   per-row equivalence proof).
2. `test_bulk_write_is_deterministic_double_build` — build bank-chat twice to two
   DBs via the bulk path, assert identical counts + query battery. Models on
   `tests/test_brownfield_routes.py::test_29_determinism_pass4_route_ids` and
   `tests/test_mcp_v2_compose.py::test_overrides_edge_set_deterministic_double_build`.
3. `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role` — CALLS rows
   deduped by `(src,dst,argc,line)`; carry correct `callee_declaring_role`
   (reuse the `@Service` callee assertion against a bulk build).
4. `test_bulk_write_empty_rel_table_is_noop` — a corpus with no `EXTENDS` edges
   must not error (`_bulk_copy` no-ops on empty rows).

**Must-still-pass (regression — `_write_edges` is shared, so both paths):** full
`tests/test_ast_graph_build.py`, `tests/test_incremental_graph.py` (28 tests),
`tests/test_bank_chat_brownfield_integration.py`, `tests/test_call_edges_e2e.py`.

## Definition of done (PR-P1)

- [ ] `_bulk_copy` helper added; step-1 spike result in its docstring.
- [ ] `_write_edges` stages per-type rows (CALLS dedup + `callee_declaring_role` at staging) and bulk-loads UnresolvedCallSite before UNRESOLVED_AT.
- [ ] `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL` deleted; local `_CREATE_UNRESOLVED/_UNRESOLVED_AT` gone with the rewrite.
- [ ] `test_bank_chat_bulk_build_matches_committed_baseline`,
      `test_bulk_write_is_deterministic_double_build`,
      `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role`,
      `test_bulk_write_empty_rel_table_is_noop` pass.
- [ ] Full `test_ast_graph_build.py` + `test_incremental_graph.py` pass unchanged.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] Sentinel greps pass (see AGENT-PROMPTS); benchmark numbers in PR description.
- [ ] PR title: `perf(graph): bulk COPY FROM for _write_edges (PR-P1)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Spike: REL `COPY FROM` column order + `from_pylist` typing | `build_ast_graph.py` (throwaway) | toy CALLS edge lands with correct endpoints; result in `_bulk_copy` docstring |
| 2 | Add `_bulk_copy` + `_REL_*_COLUMNS`/column constants | `build_ast_graph.py` | helper + constants defined |
| 3 | Convert `_write_edges` to per-type staging + bulk; load UnresolvedCallSite before UNRESOLVED_AT | `build_ast_graph.py` | edge counts match baseline; dedup + callee_declaring_role preserved |
| 4 | Delete dead `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL` + locals | `build_ast_graph.py` | sentinel greps pass |
| 5 | Generate + commit baseline | `tests/fixtures/graph_baseline_bank_chat.json` | from last per-row `_write_edges` build |
| 6 | Add 4 tests | `tests/test_ast_graph_build.py` | all pass |
| 7 | Full regression (ast_graph_build + incremental) + ruff; benchmark | repo | green; numbers in PR description |

---

# PR-P2 — Bulk write for nodes + routes/clients/producers/calls

**Goal:** convert the remaining graph writes. **Depends on PR-P1's `_bulk_copy`.**

## File-by-file changes

### 1. `build_ast_graph.py`

#### 1a. Convert `_write_nodes_impl` (`:3029`, shared workhorse)
It is called by `_write_nodes` (`:3103`, full, `_CREATE_SYMBOL`) and
`_write_nodes_merge` (`:825`, incremental, `_MERGE_SYMBOL`). Convert its body to
stage all Symbol rows (packages/files/types/members, with the existing
`resolve_role_and_capabilities` + `type_role_by_node_id` population done before
staging) then `_bulk_copy(conn, "Symbol", NODE_COLUMNS, rows)`. Both wrappers now
share one bulk path; `_CREATE_SYMBOL` and `_MERGE_SYMBOL` become dead → delete
both. (`_write_nodes_impl`'s per-row loop is gone; the two wrappers may collapse
to one, but keep both names if they differ in non-write setup — minimize churn.)
> Note: this removes the ~40-line node-row-building duplication that a
> full-path-only conversion would have created — converting the shared workhorse
> avoids it entirely.

#### 1b. Convert `_write_routes_and_exposes` (`:3338`, shared) to bulk
Today it loops over `routes_rows`/`exposes_rows`/`client_rows`/`declares_client_rows`/
`producer_rows`/`declares_producer_rows`/`http_call_rows`/`async_call_rows`, calling
`_CREATE_ROUTE`/`_CREATE_EXPOSES`/`_CREATE_CLIENT`/`_CREATE_DECLARES_CLIENT`/
`_CREATE_PRODUCER`/`_CREATE_DECLARES_PRODUCER`/`_CREATE_HTTP_CALL`/`_CREATE_ASYNC_CALL`
with the existing `_file_by_node_id`/`_file_by_client_id`/`_file_by_producer_id`
source_file resolution. Stage each table's rows (applying that resolution) and
`_bulk_copy`. **Load order within the helper:** bulk-load Route/Client/Producer
NODE rows before the EXPOSES/DECLARES_CLIENT/DECLARES_PRODUCER/HTTP_CALLS/
ASYNC_CALLS edges (those edges reference those nodes + already-loaded Symbol).
`_CREATE_ROUTE`/`_CREATE_EXPOSES` become dead → delete.

#### 1c. Convert `_write_clients_producers_and_calls` (`:3810`, incremental-only) to bulk
This is the global pass5/6 step (sole caller `incremental_rebuild:3716`). It
writes Route (via `MERGE (r:Route {id:$id}) …` to dedup against the scoped step,
`:3819-3828`), Client/Producer nodes (`_CREATE_CLIENT`/`_CREATE_PRODUCER`), and
DECLARES_CLIENT/DECLARES_PRODUCER/HTTP_CALLS/ASYNC_CALLS edges. Convert the
Client/Producer/edge writes to per-type staging + `_bulk_copy` (same
`member_by_id`/`client_by_id`/`producer_by_id` source_file resolution).
**Retain the `MERGE (r:Route …)` dedup verbatim** — routes written during the
scoped step must not duplicate; add a one-line comment that it is intentionally
kept. (Route upsert stays MERGE; only the Client/Producer/edge writes bulk.)
Now that BOTH `_write_routes_and_exposes` and `_write_clients_producers_and_calls`
are converted, the 6 shared constants — `_CREATE_CLIENT`/`_CREATE_PRODUCER`/
`_CREATE_DECLARES_CLIENT`/`_CREATE_DECLARES_PRODUCER`/`_CREATE_HTTP_CALL`/
`_CREATE_ASYNC_CALL` — are dead → delete all six.

#### 1d. `_write_meta` — UNCHANGED
Leave `_write_meta` (`:3421`) on its `MERGE (m:GraphMeta …)`. Do not touch.

### 2. `tests/test_incremental_graph.py` — incremental equivalence
Add the new tests as **methods of `TestIncrementalOrchestrator`** (same class as
`test_incremental_single_file_change`, `:230`-ish). The existing 28 tests must
pass unchanged.

## Tests for PR-P2

1. `test_incremental_bulk_write_equivalent_to_full_rebuild` (in
   `TestIncrementalOrchestrator`) — single-file change → `increment` (bulk) →
   full rebuild of that state (bulk) → assert identical node count, per-type edge
   counts, `GraphMeta` counters.
2. `test_incremental_route_merge_dedup_preserved` (in
   `TestIncrementalOrchestrator`) — a corpus where pass5/6 re-emits an existing
   route → no duplicate `Route` rows after `increment` (the retained MERGE
   dedups).

**Must-still-pass:** full `tests/test_incremental_graph.py`, `test_ast_graph_build.py`.

## Definition of done (PR-P2)

- [ ] `_write_nodes_impl` bulk; `_CREATE_SYMBOL` + `_MERGE_SYMBOL` deleted.
- [ ] `_write_routes_and_exposes` bulk; `_CREATE_ROUTE`/`_CREATE_EXPOSES` deleted.
- [ ] `_write_clients_producers_and_calls` Client/Producer/edge writes bulk; `MERGE (r:Route)` retained + commented; 6 shared `_CREATE_*` deleted.
- [ ] `_write_meta` untouched.
- [ ] Both new tests pass (in `TestIncrementalOrchestrator`); full incremental + ast_graph_build suites green.
- [ ] Sentinel greps pass; `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(graph): bulk COPY FROM for nodes, routes, clients/producers (PR-P2)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Convert `_write_nodes_impl` to bulk; delete `_CREATE_SYMBOL`+`_MERGE_SYMBOL` | `build_ast_graph.py` | node counts match baseline; both wrappers share bulk |
| 2 | Convert `_write_routes_and_exposes` to bulk; load Route/Client/Producer before edges; delete `_CREATE_ROUTE`/`_CREATE_EXPOSES` | `build_ast_graph.py` | route/client/producer/call counts match |
| 3 | Convert `_write_clients_producers_and_calls` Client/Producer/edges to bulk; retain Route MERGE; delete 6 shared `_CREATE_*` | `build_ast_graph.py` | no duplicate routes; sentinel greps pass |
| 4 | Add 2 tests to `TestIncrementalOrchestrator` | `tests/test_incremental_graph.py` | both pass; full suite green |
| 5 | ruff + full regression | repo | clean + green |

---

# PR-P3 — Cached `LayeredIgnore` (+ `is_ignored` memo) as a `ContextKey`

**Goal:** remove the ~25s from re-constructing `LayeredIgnore(project_root)` per
file and re-merging `_mega` per file. **Independent** of PR-P1/P2.

## File-by-file changes

### 1. `java_index_flow_lancedb.py`
- Define `IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")`
  alongside `PROJECT_ROOT`/`EMBEDDER`/`LANCE_DB` (`:60-72`), reusing the SAME
  `_ck_params` (`detect_change` vs `tracked`) detection block.
- In `coco_lifespan` (`:287-306`), add `builder.provide(IGNORE, LayeredIgnore(root))`
  — built **once** per flow run.
- In `process_java_file` (`:345`), `process_sql_file` (`:417`), `process_yaml_file`
  (`:465`): add `ignore = coco.use_context(IGNORE)` and replace
  `LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())`
  (`:351`/`:423`/`:471`) with `ignore.is_ignored((project_root / file.file_path.path).resolve())`.
  Keep `project_root` (still used for path resolution + `_parse_and_enrich_java`).
- **Leave `:177` and `:569` alone** — they call `cocoindex_excluded_patterns()`
  once per run (the `_approximate_vectors_total` helper and the app_main
  pre-walk), not per file.

### 2. `path_filtering.py`
- Add `self._mega_cache: dict[str, tuple[list[str], GitIgnoreSpec, list]] = {}`
  in `LayeredIgnore.__init__`.
- In `_mega` (`:334`): key on `Path(rel_project).parent.as_posix()`; return cached
  `(mega, spec, meta)` if present, else compute via `_mega_build_for_rel` +
  `GitIgnoreSpec.from_lines`, store, return. Correctness rests on
  `_mega_build_for_rel` reading only `dir_parts` (`:226-227`).
- `is_ignored` (`:345`) and `diagnose_dict` (`:377`) call `_mega` unchanged and
  benefit transparently (both consume the full `(mega, spec, meta)` tuple).

## Tests for PR-P3

1. `test_is_ignored_mega_caches_by_directory` — in `tests/test_path_filtering.py`;
   assert `_mega` is computed once per directory (spy on `_mega_build_for_rel`)
   and decisions match the uncached path.
2. `test_layered_ignore_memo_preserves_decisions` — in `tests/test_path_filtering.py`;
   for a corpus with nested ignore + gitignore negations, assert `is_ignored` is
   identical with and without the cache.
3. `test_layered_ignore_provided_once_per_flow` — in `tests/test_lancedb_e2e.py`
  (HEAVY, `JAVA_CODEBASE_RAG_RUN_HEAVY=1`); run the real flow, assert a single
  `LayeredIgnore` instance (identity check), not per-file.

**Must-still-pass:** `tests/test_lancedb_e2e.py::test_lancedb_ignore_file_reduces_indexed_java_files`,
`tests/test_path_filtering.py`, and the heavy
`tests/test_vectors_progress.py::test_flow_emits_vectors_progress_per_file`.

## Definition of done (PR-P3)

- [ ] `IGNORE` ContextKey (version-detected) + provided once in `coco_lifespan`.
- [ ] The three `process_*_file` consume it; sites `:177`/`:569` untouched.
- [ ] `_mega` memoized by directory; `is_ignored`/`diagnose_dict` results unchanged.
- [ ] The 3 named tests pass; existing ignore/vectors-progress tests pass unchanged.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(vectors): lifespan-cached LayeredIgnore + is_ignored memo (PR-P3)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `IGNORE` ContextKey (version-detected) + provide in lifespan | `java_index_flow_lancedb.py` | resolvable in process_*_file |
| 2 | Switch the three `process_*_file` to `coco.use_context(IGNORE)` | `java_index_flow_lancedb.py` | only `:351`/`:423`/`:471` changed; `:177`/`:569` untouched |
| 3 | Add `_mega` dirname cache | `path_filtering.py` | repeated same-dir calls hit cache |
| 4 | Add memo + correctness tests in `test_path_filtering.py`; once-per-flow in `test_lancedb_e2e.py` | `tests/` | all pass |
| 5 | ruff + regression (incl. heavy ignore/vectors tests) | repo | clean + green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | REL-table `COPY FROM` column order/naming differs from assumption | High | PR-P1 step-1 spike locks it before conversion. |
| 2 | Shared-helper conversion changes incremental output | High | `_write_edges`/`_write_routes_and_exposes`/`_write_nodes_impl` are shared → full `test_incremental_graph.py` (28 tests) is a merge gate for P1 and P2, plus the new incremental-equivalence test. |
| 3 | CALLS dedup / `callee_declaring_role` drift when moved to staging | High | `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role` + sampled-edge baseline. |
| 4 | Deleting a `_CREATE_*` constant still used by the other path | High | PR-P2 deletes the 6 client/producer constants only AFTER both `_write_routes_and_exposes` and `_write_clients_producers_and_calls` are converted (same PR). Sentinel greps enforce. |
| 5 | pass5/6 Route dedup breaks | Medium | `MERGE (r:Route)` retained by name; `test_incremental_route_merge_dedup_preserved` guards it. |
| 6 | PR-P3 `_mega` memo returns stale rules | Low | `_mega_build_for_rel` reads only `dir_parts`; `test_layered_ignore_memo_preserves_decisions` asserts parity. |
| 7 | PR-P3 sentinel over-matches `:177`/`:569` | Medium | Sentinel is `LayeredIgnore\(project_root\)\.is_ignored` (matches only the 3 process sites), NOT the bare constructor. |
| 8 | `ContextKey` lifespan differs across cocoindex versions | Low | Reuse the existing `_ck_params` `detect_change`/`tracked` detection block. |

# Out of scope

- MPS embedding default (already auto-selected).
- GraphMeta bulk conversion (left on MERGE — shared, one row).
- ANN index (#337) and `watch` mode (#336).
- Replacing/restructuring the cocoindex flow; changing embedding model/dim.
- Parallelizing graph analysis passes (pass1–pass6).
- Parquet-file or CSV bulk paths (pyarrow in-memory only).
- Converting `:177`/`:569` (once-per-run setup, not hot paths).

# Whole-plan done definition

1. `init`/`reprocess`/`increment` graph-write phase on the medium corpus drops
   from ~321s to tens of seconds (benchmark in PR-P1; completed in PR-P2).
2. The vectors phase pays no per-file `LayeredIgnore`/`_mega` cost (PR-P3).
3. No ontology bump (`ontology_version` stays 17); no re-index required; all
   existing graph/edge/brownfield/incremental/ignore/vectors tests pass.
4. Proposal moved to `propose/completed/` and this plan to `plans/completed/`
   once all three PRs land.

# Tracking

- `PR-P1`: _pending_
- `PR-P2`: _pending_ (blocked by PR-P1)
- `PR-P3`: _pending_
