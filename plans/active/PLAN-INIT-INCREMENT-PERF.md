# Plan: Faster init/increment — bulk graph writes + cached ignore

Status: **active (planning)**. This plan implements
[`propose/active/INIT-INCREMENT-PERF-PROPOSE.md`](../../propose/active/INIT-INCREMENT-PERF-PROPOSE.md)
(see also PR #338).

Depends on: proposal PR #338 should land first (or this plan tracks against the
proposal text directly). No code dependency on other in-flight work.

## Goal

- Cut `init` / `reprocess` wall-clock on a medium Java corpus from ~395s toward
  ~120s by replacing the per-row graph write with bulk `COPY FROM` (PR-P1).
- Extend the bulk primitive to the incremental path so `increment` on large
  change-sets is also fast (PR-P2).
- Remove the ~25s `LayeredIgnore` re-construction + `is_ignored` re-merge paid
  per file in the cocoindex vectors phase (PR-P3).
- Preserve graph contents exactly: no ontology bump, no re-index, no query-result
  change (proven by an equivalence harness).

## Principles (do not relitigate in review)

- **Byte-equivalent graph.** PR-P1/P2 change only the write mechanism; the graph
  (node/edge rows, properties, `GraphMeta` counters) must be identical to today.
  The equivalence harness is the merge gate — a failing equivalence test blocks
  the PR.
- **Full rebuild path only in PR-P1.** The incremental path keeps its current
  per-row writes until PR-P2. PR-P1 does not touch `increment`.
- **In-memory pyarrow `COPY FROM`** is the bulk mechanism (verified: the `ladybug`
  wrapper forwards `COPY FROM` verbatim and accepts a pyarrow/pandas param —
  `ladybug/connection.py:337/488`). Parquet-file staging is a fallback, not the
  default. Do not propose CSV.
- **MPS device default is out of scope** — the flow already auto-selects MPS. Do
  not add a device PR.
- **No new env vars, CLI flags, or public surface.** No compatibility shims.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-P1** | Bulk `COPY FROM` for the full rebuild path (`init`/`reprocess`) in `build_ast_graph.py` | none | REL-table `COPY FROM` column order (FROM/TO first); LIST column arrow typing; CALLS dedup + `callee_declaring_role` materialized at staging; node-before-edge load order; `GraphMeta` bulk | equivalence + determinism + baseline + full `test_ast_graph_build.py` regression | — |
| **PR-P2** | Shared bulk primitive applied to the incremental `_delete_file_scope` → re-emit path | none | Preserve pass5/6 `MERGE (r:Route)` dedup (`build_ast_graph.py:3819-3821`); incremental delete-then-emit ordering; equivalence vs full rebuild | `test_incremental_graph.py` regression + new incremental-equivalence test | depends on **PR-P1** |
| **PR-P3** | Hoist `LayeredIgnore` to a cocoindex `ContextKey` + memoize `is_ignored`'s `_mega` | none | `ContextKey` lifespan scoping (build once, not per file); `_mega` memo correctness (mega depends on the file's directory only — match_file stays per-file); no change to any ignore *decision* | flow ignore tests + memo unit tests + heavy vectors-progress test | independent of PR-P1, PR-P2 |

Landing order: **P1 → P2**; **P3** may land in any order (independent).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Bulk mechanism | In-memory pyarrow: `conn.execute("COPY <table> FROM $rows", {"rows": pa_table})`. Verified against `ladybug/connection.py:337` (`FROM $param`) and `:488` (pyarrow/pandas/polars accepted). |
| REL-table column rule | First two staged columns are the FROM/TO node primary keys (`Symbol.id` / `Route.id` / etc.). Exact column *naming* for REL `COPY FROM` is confirmed in PR-P1 step 1 (a 5-line spike); the contract below assumes positional FROM/TO. |
| `GraphMeta` MERGE (`build_ast_graph.py:3472-3473`) | Folded into PR-P1 bulk (it is in the full-rebuild path; the table is freshly created and empty, so `COPY` insert is correct). Resolves proposal Open Q1. |
| PR-P3 cache vehicle | cocoindex `ContextKey[LayeredIgnore]` (lifespan-scoped, built once). Resolves proposal Open Q2. |
| `is_ignored` memo | Cache `_mega(rel)` → `(mega, spec)` keyed by the file's project-relative **directory** (`Path(rel).parent.as_posix()`), because `_mega_build_for_rel` uses only `dir_parts = parts[:-1]`. `spec.match_file(rel)` stays per-file (cheap, filename-dependent). |

---

# PR-P1 — Bulk `COPY FROM` for the full rebuild path

**Goal:** replace the per-row `conn.execute` writes in `write_ladybug` (the
init/reprocess full-rebuild entry, `build_ast_graph.py:3893`) with bulk
in-memory-pyarrow `COPY FROM`. Graph contents stay byte-equivalent.

## File-by-file changes

### 1. `build_ast_graph.py` — bulk-write primitive + staging

#### 1a. New helper `_bulk_copy`
Add near the other write helpers (after `_node_row`, ~`build_ast_graph.py:2994`):

```python
import pyarrow as pa

def _bulk_copy(conn: ladybug.Connection, table_name: str, columns: list[str], rows: list[dict]) -> None:
    """Bulk-load rows into a (node or rel) table via in-memory pyarrow COPY FROM.

    `columns` fixes column order — for REL tables the first two MUST be the
    FROM/TO node ids (kuzu requirement). Empty `rows` is a no-op.
    """
    if not rows:
        return
    tbl = pa.Table.from_pylist(rows)  # infers types from non-empty data
    # from_pylist infers STRING / list<STRING> correctly for non-empty rows;
    # step-1 spike confirms LIST columns (modifiers/annotations/capabilities)
    # infer as pa.list_(pa.string()). If any column is all-empty-list, pass an
    # explicit pa.schema derived from the _SCHEMA_* strings instead.
    conn.execute(f"COPY {table_name} FROM $rows", {"rows": tbl})
```

Column-order constants (define next to the `_SCHEMA_*` strings, ~`:2812`), each
matching its `_SCHEMA_*` column order exactly — for REL tables the first two
entries are the endpoint ids:

- `NODE_COLUMNS` = the `Symbol` property columns in `_SCHEMA_NODE` order.
- `ROUTE_COLUMNS`, `CLIENT_COLUMNS`, `PRODUCER_COLUMNS`, `GRAPHMETA_COLUMNS`,
  `UNRESOLVED_CALL_SITE_COLUMNS` — same, from their `_SCHEMA_*`.
- For each REL table: `_REL_*_COLUMNS` = `["FROM", "TO", <props in _SCHEMA_* order>]`
  for `EXTENDS/IMPLEMENTS/INJECTS/DECLARES/OVERRIDES/CALLS/UNRESOLVED_AT/EXPOSES/
  DECLARES_CLIENT/DECLARES_PRODUCER/HTTP_CALLS/ASYNC_CALLS`.

> **Step-1 spike (mandatory, first commit of PR-P1):** confirm (a) the exact
> REL `COPY FROM` column naming — build a 2-node + 1-edge toy, `COPY CALLS FROM
> $rows` with columns `["FROM","TO","source_file","resolved",...]`, assert the
> edge lands with correct endpoints; (b) `pa.Table.from_pylist` type inference
> for the `modifiers`/`annotations`/`capabilities` LIST columns. Record the
> working incantation in the helper docstring. If inference fails on empty
> lists, build an explicit `pa.schema` map keyed by table.

#### 1b. Convert `_write_nodes` (full path) to bulk
`_write_nodes` (`:3096`) currently calls `_write_nodes_impl(..., symbol_query=_CREATE_SYMBOL)`
which loops `conn.execute(_CREATE_SYMBOL, _node_row(...))` per node. Replace the
full-path body: iterate packages / files / types / members exactly as
`_write_nodes_impl` does, but append each `_node_row(...)` to a list, then call
`_bulk_copy(conn, "Symbol", NODE_COLUMNS, node_rows)`. Keep the role/capability
resolution (`resolve_role_and_capabilities`) and `tables.type_role_by_node_id`
population identical (those are pure in-memory steps before staging).

`_write_nodes_impl` + `_MERGE_SYMBOL` stay — they are the **incremental** path
(`_write_nodes_merge`, `:817`) used until PR-P2. `_CREATE_SYMBOL` becomes dead
(only `_write_nodes` used it) → delete it.

#### 1c. Convert `_write_edges` to bulk
`_write_edges` (`:3244`) loops per edge, calling `conn.execute(_CREATE_EXT,
{...})` etc. with two dedup sets (`seen_calls` `:3282-3288`, `seen_ucs`
`:3317-3321`) and a `callee_declaring_role` lookup (`_callee_declaring_role_at_write`,
`:1647`/`:3302`). Restructure to accumulate **per-edge-type row lists**, applying
the *same* dedup and *same* `callee_declaring_role` materialization, then
`_bulk_copy` each REL table once:

```python
extends_rows: list[dict] = []
...
for ...:
    extends_rows.append({"FROM": src_id, "TO": dst_id, "source_file": ..., "dst_name": ..., "dst_fqn": ..., "resolved": ...})
...
calls_rows.append(...)  # only when key not in seen_calls; include "callee_declaring_role"
...
_bulk_copy(conn, "EXTENDS", _REL_EXTENDS_COLUMNS, extends_rows)
_bulk_copy(conn, "CALLS", _REL_CALLS_COLUMNS, calls_rows)   # CALLS_COLUMNS ends with callee_declaring_role
...
```

The dedup sets and `_callee_declaring_role_at_write` calls are computed
**before** appending (i.e. at staging), exactly preserving current semantics.
`_CREATE_EXT`/`_CREATE_IMPL`/`_CREATE_INJ`/`_CREATE_DECL`/`_CREATE_OVERRIDES`/
`_CREATE_CALL`/`_CREATE_UNRESOLVED`/`_CREATE_UNRESOLVED_AT` become dead → delete.

`_populate_declares_rows` (`:3189`) and `_populate_overrides_rows` (`:3206`) are
pure in-memory population — unchanged; they feed the DECLARES/OVERRIDES row lists.

#### 1d. Convert `_write_routes_and_exposes` (and Client/Producer/HTTP_CALLS/ASYNC_CALLS) to bulk
Same pattern (`~`:3349-3408`): stage `Route`/`Client`/`Producer` node rows and
`EXPOSES`/`DECLARES_CLIENT`/`DECLARES_PRODUCER`/`HTTP_CALLS`/`ASYNC_CALLS` edge
rows (with FROM/TO ids), then `_bulk_copy` each. The pass5/6 `MERGE (r:Route)`
dedup (`:3819-3821`) is **incremental-only** and untouched in PR-P1; in the full
path routes are emitted once into a fresh table, so plain `COPY` insert is
correct. Delete the now-dead `_CREATE_ROUTE`/`_CREATE_EXPOSES`/`_CREATE_CLIENT`/
`_CREATE_DECLARES_CLIENT`/`_CREATE_PRODUCER`/`_CREATE_DECLARES_PRODUCER`/
`_CREATE_HTTP_CALL`/`_CREATE_ASYNC_CALL` strings.

#### 1e. Convert `GraphMeta` write to bulk
The `MERGE (m:GraphMeta {key: $k})` loop (`:3472-3473`) becomes a single-row
`_bulk_copy(conn, "GraphMeta", GRAPHMETA_COLUMNS, [meta_row])` (table is freshly
created and empty in a full rebuild). Keep the row contents (ontology_version,
built_at, source_root, parse_errors, counts_json) identical.

#### 1f. `write_ladybug` load order (node-before-edge)
`write_ladybug` (`:3893`) already calls `_write_nodes` → `_write_edges` →
`_write_routes_and_exposes` → GraphMeta. Preserve this order: all node tables
(`Symbol`, `Route`, `Client`, `Producer`, `UnresolvedCallSite`, `GraphMeta`) are
bulk-loaded before any REL table, so endpoint rows exist when kuzu validates REL
`COPY FROM`. Add an inline comment stating the ordering invariant.

### 2. `tests/test_ast_graph_build.py` — equivalence harness + regression

Add an equivalence harness and a committed baseline. The existing 26 tests in
this file (e.g. `test_schema_has_all_expected_tables`,
`test_graph_meta_present_and_versioned`, `test_each_edge_type_populated`,
`test_pass3_callee_declaring_role_bank_annotated_types`,
`test_pass3_unresolved_call_site_emitted`, `test_pass3_known_external_calls_preserved`)
must continue to pass unchanged — they ARE the regression net.

Add a committed baseline artifact `tests/fixtures/graph_baseline_bank_chat.json`
(node count, per-type edge counts, `GraphMeta` counters, and N=3 sampled edge
property rows per type incl. `source_file` and CALLS `callee_declaring_role`).
Generated once during PR-P1 from the **last per-row build** before its removal,
committed, and regenerated only when `ontology_version` changes.

### 3. `scripts/bench_init_graph_write.py` (new, dev-only) — benchmark
A small script that times `init` (or `build_ast_graph.py --source-root
<corpus>`) on a medium corpus and prints the graph-write phase delta vs the
pre-PR baseline. Not shipped in the package; lives under `scripts/`. Documents
the measured speedup in the PR description.

## Tests for PR-P1

1. `test_bulk_write_graph_matches_per_row_baseline` — build `tests/bank-chat-system`
   via the bulk path, assert node count, per-type edge counts, `GraphMeta`
   counters, and the sampled edge property rows equal `tests/fixtures/graph_baseline_bank_chat.json`.
2. `test_bulk_write_is_deterministic_double_build` — build bank-chat twice to two
   DB paths via the bulk path, assert identical node count, per-type edge counts,
   `GraphMeta` counters, and a query battery (`MATCH (s:Symbol) RETURN ...`,
   `neighbors`-shaped reads). Models on existing
   `test_29_determinism_pass4_route_ids` / `test_overrides_edge_set_deterministic_double_build`.
3. `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role` — assert
   CALLS rows are deduped by `(src,dst,argc,line)` and carry the correct
   `callee_declaring_role` (reuse the `@Service` callee assertion from
   `test_pass3_callee_declaring_role_bank_annotated_types` against a bulk build).
4. `test_bulk_write_empty_rel_table_is_noop` — a corpus with no `EXTENDS` edges
   must not error (`_bulk_copy` no-ops on empty rows) and the table is empty.

**Must-still-pass (regression, do not loosen):** the full `tests/test_ast_graph_build.py`,
`tests/test_bank_chat_brownfield_integration.py`, `tests/test_call_edges_e2e.py`,
and `tests/test_incremental_graph.py` suites (incremental path untouched).

## Definition of done (PR-P1)

- [ ] `_bulk_copy` helper added; step-1 spike result recorded in its docstring.
- [ ] `write_ladybug` full path writes all node tables then all REL tables via `_bulk_copy`; no per-row `CREATE` remains in the full path.
- [ ] Dead `_CREATE_*` strings + `_CREATE_SYMBOL` deleted.
- [ ] `test_bulk_write_graph_matches_per_row_baseline`,
      `test_bulk_write_is_deterministic_double_build`,
      `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role`,
      `test_bulk_write_empty_rel_table_is_noop` added and pass.
- [ ] Full existing graph/edge/brownfield/incremental test suites pass unchanged.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] Benchmark numbers (before/after graph-write phase) pasted in the PR description.
- [ ] PR title: `perf(graph): bulk COPY FROM for the full rebuild path (PR-P1)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Spike: confirm REL `COPY FROM` column order + `from_pylist` LIST typing | `build_ast_graph.py` (throwaway) | toy 2-node+1-edge `COPY CALLS FROM $rows` lands with correct endpoints; result in `_bulk_copy` docstring |
| 2 | Add `_bulk_copy` + column-order constants | `build_ast_graph.py` | helper + `_REL_*_COLUMNS`/`*_COLUMNS` defined |
| 3 | Convert `_write_nodes` (full) to bulk; delete `_CREATE_SYMBOL` | `build_ast_graph.py` | node tables bulk-loaded; Symbol count matches baseline |
| 4 | Convert `_write_edges` to per-type staging + bulk; delete dead `_CREATE_*` edge strings | `build_ast_graph.py` | edge counts match baseline; dedup + callee_declaring_role preserved |
| 5 | Convert `_write_routes_and_exposes` (+ Client/Producer/HTTP_CALLS/ASYNC_CALLS) to bulk | `build_ast_graph.py` | route/client/producer/call-edge counts match baseline |
| 6 | Convert GraphMeta write to bulk | `build_ast_graph.py` | `test_graph_meta_present_and_versioned` passes |
| 7 | Generate + commit baseline fixture | `tests/fixtures/graph_baseline_bank_chat.json` | produced from last per-row build before removal |
| 8 | Add 4 equivalence/determinism tests | `tests/test_ast_graph_build.py` | all pass |
| 9 | Run full regression + ruff; capture benchmark | repo | suites green; numbers in PR description |

---

# PR-P2 — Bulk write for the incremental path

**Goal:** apply the PR-P1 bulk primitive to the incremental rebuild path so
`increment` on a large change-set is also fast. **Depends on PR-P1** (`_bulk_copy`
+ column constants + the staging pattern).

## File-by-file changes

### 1. `build_ast_graph.py` — incremental path bulk conversion
- `_write_nodes_merge` (`:817`, uses `_MERGE_SYMBOL`): the incremental path is
  delete-then-insert (`_delete_file_scope`, `:673`, removes changed/dependent
  files' nodes+edges first), so re-emit is into a cleaned scope → convert the
  per-row `_MERGE_SYMBOL` loop to `_bulk_copy(conn, "Symbol", NODE_COLUMNS, rows)`.
  `_MERGE_SYMBOL` becomes dead → delete; `_write_nodes_impl` is removed if no
  caller remains (it does not, after P1+P2).
- Incremental edge re-emit: apply the same per-type staging + `_bulk_copy` as
  PR-P1's `_write_edges`, scoped to the re-emitted files.
- **Preserve the pass5/6 `MERGE (r:Route)` dedup** (`:3819-3821`): routes written
  during the scoped step must still MERGE (not duplicate) against routes from the
  global step. Keep those specific `MERGE` statements; only convert the
  non-Route, non-dedup writes to bulk. Add a comment that this MERGE is
  intentionally retained.
- `incremental_rebuild` (`:3535`): no algorithmic change; it calls into the
  converted write helpers.

### 2. `tests/test_incremental_graph.py` — incremental equivalence
The existing 28 tests (e.g. `test_incremental_single_file_change`,
`test_incremental_new_file`, `test_incremental_deleted_file`,
`test_incremental_phantom_nodes_preserved`, `test_incremental_dependent_expansion`,
`test_incremental_expansion_cap_fallback`, `test_incremental_crash_marker_*`,
`test_incremental_no_changes_is_noop`, `test_delete_file_scope_preserves_dependent_nodes`)
must pass unchanged.

## Tests for PR-P2

1. `test_incremental_bulk_write_equivalent_to_full_rebuild` — make a single-file
   change, run `increment` (bulk incremental), then build the same state via a
   full rebuild (bulk full), assert identical node count, per-type edge counts,
   and `GraphMeta` counters.
2. `test_incremental_route_merge_dedup_preserved` — a corpus where pass5/6 would
   re-emit an existing route; assert no duplicate `Route` rows after `increment`
   (the retained `MERGE (r:Route)` still dedups).

**Must-still-pass:** full `tests/test_incremental_graph.py`.

## Definition of done (PR-P2)

- [ ] Incremental node/edge re-emit uses `_bulk_copy`; `_MERGE_SYMBOL` deleted.
- [ ] pass5/6 `MERGE (r:Route)` dedup retained and commented.
- [ ] `test_incremental_bulk_write_equivalent_to_full_rebuild`,
      `test_incremental_route_merge_dedup_preserved` added and pass.
- [ ] Full `tests/test_incremental_graph.py` passes unchanged.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(graph): bulk COPY FROM for the incremental path (PR-P2)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Convert `_write_nodes_merge` to bulk; delete `_MERGE_SYMBOL` | `build_ast_graph.py` | incremental node re-emit via `_bulk_copy` |
| 2 | Convert incremental edge re-emit to per-type staging + bulk | `build_ast_graph.py` | incremental edge counts match full rebuild |
| 3 | Retain + comment the pass5/6 `MERGE (r:Route)` dedup | `build_ast_graph.py` | no duplicate Route rows after increment |
| 4 | Add incremental-equivalence + route-dedup tests | `tests/test_incremental_graph.py` | both pass; full suite green |
| 5 | ruff + full regression | repo | clean + green |

---

# PR-P3 — Cached `LayeredIgnore` (+ `is_ignored` memo) as a `ContextKey`

**Goal:** remove the ~25s paid per-flow-run from re-constructing
`LayeredIgnore(project_root)` per file and re-merging `_mega(spec)` per file.
**Independent** of PR-P1/P2.

## File-by-file changes

### 1. `java_index_flow_lancedb.py` — `ContextKey` for the ignore object
- Define `IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")`
  alongside the existing `PROJECT_ROOT`/`EMBEDDER`/`LANCE_DB` definitions
  (`:60-72`), using the **same** `_ck_params` (`detect_change` vs `tracked`)
  detection block so it works across cocoindex versions.
- In `coco_lifespan` (where `builder.provide(PROJECT_ROOT, root)` /
  `builder.provide(EMBEDDER, embedder)` are called, `~`:287-306`), add
  `builder.provide(IGNORE, LayeredIgnore(root))` — built **once** per flow run.
- In `process_java_file` (`:344`), `process_sql_file` (`:416`),
  `process_yaml_file` (`:464`): replace
  `if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve()):`
  (`:351`/`:423`/`:471`) with
  `ignore = coco.use_context(IGNORE)` then
  `if ignore.is_ignored((project_root / file.file_path.path).resolve()):`.
  `project_root` stays (still needed for `_parse_and_enrich_java` and path
  resolution).

### 2. `path_filtering.py` — memoize `_mega` by directory
- Add an instance cache `self._mega_cache: dict[str, tuple[list[str], GitIgnoreSpec, list]] = {}`
  in `LayeredIgnore.__init__`.
- In `LayeredIgnore._mega` (`:334`): key on
  `dir_rel = Path(rel_project).parent.as_posix()`. If present, return the cached
  `(mega, spec, meta)`; else compute via `_mega_build_for_rel` + compile, store,
  return. Correctness rests on `_mega_build_for_rel` reading only
  `dir_parts = parts[:-1]` (verified: it never reads the filename).
- `is_ignored` (`:345`) and `diagnose_dict` (`:377`) keep calling `_mega(rel)`
  unchanged — they transparently benefit from the cache.
- Add a module/env knob `LAYERED_IGNORE_MEGA_CACHE` only if a disable path is
  needed for debugging — **default enabled**, no new public surface otherwise.

## Tests for PR-P3

1. `test_is_ignored_mega_caches_by_directory` — build a `LayeredIgnore` over a
   fixture with nested ignore files; call `is_ignored` for several files in the
   same directory; assert `_mega` is computed once (spy on `_mega_build_for_rel`
   or count `GitIgnoreSpec.from_lines` calls) and that decisions match the
   uncached path.
2. `test_layered_ignore_memo_preserves_decisions` — for a corpus of paths
   (including nested `.java-codebase-rag/ignore` + `.gitignore` with negations),
   assert `LayeredIgnore(...).is_ignored(p)` is identical with and without the
   memo cache (correctness invariant).
3. `test_layered_ignore_provided_once_per_flow` (HEAVY,
   `JAVA_CODEBASE_RAG_RUN_HEAVY=1`) — run the real cocoindex flow over a small
   corpus; assert the `LayeredIgnore` provided via `IGNORE` is a single instance
   (identity check), not reconstructed per file.

**Must-still-pass:** `tests/test_lancedb_e2e.py::test_lancedb_ignore_file_reduces_indexed_java_files`,
any `tests/test_path_filtering*` / ignore tests, and the heavy
`test_flow_emits_vectors_progress_per_file`.

## Definition of done (PR-P3)

- [ ] `IGNORE` `ContextKey` defined (version-detected) + provided once in `coco_lifespan`.
- [ ] The three `process_*_file` functions consume it; no per-file `LayeredIgnore(project_root)`.
- [ ] `_mega` memoized by directory; `is_ignored`/`diagnose_dict` unchanged in result.
- [ ] `test_is_ignored_mega_caches_by_directory`,
      `test_layered_ignore_memo_preserves_decisions`,
      `test_layered_ignore_provided_once_per_flow` added and pass.
- [ ] Existing ignore + vectors-progress tests pass unchanged.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(vectors): lifespan-cached LayeredIgnore + is_ignored memo (PR-P3)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `IGNORE` ContextKey (version-detected) + provide in lifespan | `java_index_flow_lancedb.py` | `IGNORE` resolvable in process_*_file |
| 2 | Switch the three `process_*_file` to `coco.use_context(IGNORE)` | `java_index_flow_lancedb.py` | no per-file construction |
| 3 | Add `_mega` dirname cache | `path_filtering.py` | repeated same-dir calls hit cache |
| 4 | Add memo + correctness + once-per-flow tests | `tests/` | all pass |
| 5 | ruff + regression (incl. heavy ignore/vectors tests) | repo | clean + green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | REL-table `COPY FROM` column ordering/naming differs from assumption | High | PR-P1 step-1 spike locks the exact incantation before any conversion; recorded in `_bulk_copy` docstring. |
| 2 | LIST columns (`modifiers`/`annotations`/`capabilities`) mis-type under `from_pylist` | Medium | Spike asserts LIST inference; explicit `pa.schema` fallback documented. Equivalence test catches any row diff. |
| 3 | CALLS dedup / `callee_declaring_role` drift when moving to staging | High | `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role` + the sampled-edge baseline check; semantics computed identically, just earlier. |
| 4 | Bulk write changes observable query results | High | Equivalence + determinism tests + full existing graph/edge/brownfield suites are the gate. |
| 5 | PR-P2 breaks the pass5/6 Route dedup | Medium | `MERGE (r:Route)` retained by name; `test_incremental_route_merge_dedup_preserved` guards it. |
| 6 | PR-P3 `_mega` memo returns stale rules if dirParts logic changes | Low | `_mega_build_for_rel` reads only `dir_parts`; `test_layered_ignore_memo_preserves_decisions` asserts parity with uncached path. |
| 7 | `ContextKey` lifespan differs across cocoindex versions | Low | Reuse the existing `_ck_params` `detect_change`/`tracked` detection block used by `PROJECT_ROOT`. |

# Out of scope

- MPS embedding default (already auto-selected — see proposal Out of scope).
- ANN vector index (#337) and `watch` mode (#336).
- Replacing/restructuring the cocoindex flow; changing embedding model/dim.
- Parallelizing graph analysis passes (pass1–pass6).
- Converting the incremental path in PR-P1 (it is PR-P2).
- Parquet-file or CSV bulk paths (pyarrow in-memory only).

# Whole-plan done definition

1. `init`/`reprocess` graph-write phase on the medium corpus drops from ~321s to
   tens of seconds (benchmark in PR-P1 description).
2. `increment` on a large change-set uses the bulk path (PR-P2).
3. The vectors phase pays no per-file `LayeredIgnore`/`_mega` cost (PR-P3).
4. No ontology bump (`ontology_version` stays 17); no re-index required; all
   existing graph/edge/brownfield/incremental/ignore/vectors tests pass.
5. Proposal moved to `propose/completed/` and this plan to `plans/completed/`
   once all three PRs land.

# Tracking

- `PR-P1`: _pending_
- `PR-P2`: _pending_ (blocked by PR-P1)
- `PR-P3`: _pending_
