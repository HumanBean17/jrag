# Agent task prompts — Faster init/increment (PR-P1 → PR-P3)

Status: **active**. One self-contained prompt per PR. Copy the prompt verbatim
into the agent, attach the files in its `@-files` block, and let it execute.

> **Revised after the PR #339 subagent review.** PRs are split by **write-function**,
> not by path — the graph write helpers are shared between the full and incremental
> paths, so converting one accelerates both. Sentinel greps were corrected (the
> PR-P3 "must return zero" grep previously over-matched once-per-run sites).

**Workflow per PR:**

1. Create the branch named in the prompt off the stated base.
2. Read the cited plan section in full **before** writing code.
3. Implement step-by-step; run the listed tests after each step.
4. Run the sentinel greps — every "must return zero" line must be empty, every
   "must be non-zero" line must hit.
5. Paste the manual-evidence output into the PR description.
6. Open a PR with the exact title in the Definition of Done.

**Universal rules for every prompt:**

- Use only `.venv/bin/python` and `.venv/bin/pip` (never system python/pip).
- `server.py` is stdio — never write to stdout from anything reachable by a tool handler.
- Do not add a cocoindex dependency outside `java_index_flow_lancedb.py`.
- The plan is the source of truth — if this prompt and the plan disagree, the plan wins.
- Do not touch any file outside the prompt's `@-files` + the test files it names. If you think an adjacent file must change, **stop and ask**.
- Do not loosen any existing test assertion to make it pass.
- Breaking changes are allowed; no compatibility shims.

---

## PR-P1 — Bulk `COPY FROM` for `_write_edges` (shared; the ~250s prize)

**Branch:** `perf/bulk-graph-writes-p1` off `master`.
**Base:** `master`.
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P1 (read this first).
**Estimated diff size:** medium (one module + tests + a fixture).

**Attach (`@-files`):**

- `@build_ast_graph.py` (focus: `_write_edges:3244`, `_node_row:2994`, `_SCHEMA_*:2812-2940`, `_callee_declaring_role_at_write:1647`, `seen_calls:3282`, `seen_ucs:3317`, `_populate_declares_rows:3189`)
- `@propose/active/INIT-INCREMENT-PERF-PROPOSE.md` (design; on PR #338's branch — if absent, the staging invariants are inlined in the plan §PR-P1)
- `@tests/test_ast_graph_build.py` (regression net + where new tests go)
- `@tests/test_incremental_graph.py` (`_write_edges` is shared — incremental regression is binding)
- `@tests/_builders.py` (graph-build helpers)

**Prompt:**

````
You are implementing PR-P1 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.

Read the **PR-P1** section in full. The plan wins if this prompt disagrees.

KEY FACT: `_write_edges` (build_ast_graph.py:3244) is a SHARED helper called by
BOTH the full path (write_ladybug:3926) and the incremental path (:805). So
converting it accelerates both — there is no "full-only" edge conversion. PR-P1
converts ONLY `_write_edges` (+ adds the `_bulk_copy` primitive). Nodes, routes,
clients/producers, and GraphMeta are PR-P2.

## Scope

1. **Step-1 spike (first commit):** confirm the exact REL `COPY FROM` column
   naming + `pa.Table.from_pylist` typing with a throwaway 2-Symbol + 1-CALLS
   toy. Record the working incantation in the `_bulk_copy` docstring.
2. Add `_bulk_copy(conn, table_name, columns, rows)` + the `_REL_*_COLUMNS` /
   column constants (REL tables list FROM/TO first; match `_SCHEMA_*` order).
3. Convert `_write_edges` to per-edge-type row staging (apply the SAME
   `seen_calls`/`seen_ucs` dedup and SAME `_callee_declaring_role_at_write`
   lookup at staging, before appending), then `_bulk_copy` each REL table.
   Bulk-load UnresolvedCallSite NODE rows before UNRESOLVED_AT edges (Symbol
   nodes are already loaded by `_write_nodes`).
4. Delete the dead module constants `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL`
   and the local `_CREATE_UNRESOLVED`/`_CREATE_UNRESOLVED_AT` (defined inside
   `_write_edges` at :3307/:3313 — removed with the rewrite).
5. Generate + commit `tests/fixtures/graph_baseline_bank_chat.json` from the
   last per-row `_write_edges` build before removal.
6. Add the four named tests.

## Out of scope (do NOT touch)

- Node writes (`_write_nodes`, `_write_nodes_impl`, `_write_nodes_merge`,
  `_CREATE_SYMBOL`, `_MERGE_SYMBOL`) — PR-P2.
- Routes/clients/producers/calls (`_write_routes_and_exposes`,
  `_write_clients_producers_and_calls`, and their `_CREATE_*` constants
  including `_CREATE_CLIENT`/`_CREATE_PRODUCER`/`_CREATE_ROUTE` etc.) — PR-P2.
- `_write_meta` / GraphMeta — leave the MERGE alone (PR-P2 also leaves it).
- `java_index_flow_lancedb.py`, `path_filtering.py`, `server.py`.
- Any schema/ontology/re-index change. CSV or Parquet-file staging.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. `_bulk_copy` helper + column-order constants.
2. `_write_edges` stages per-type rows and bulk-loads (CALLS dedup + callee_declaring_role at staging; UnresolvedCallSite before UNRESOLVED_AT).
3. Dead `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL` + locals removed.
4. `tests/fixtures/graph_baseline_bank_chat.json` committed.
5. Four new tests in `tests/test_ast_graph_build.py`.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_ast_graph_build.py tests/test_incremental_graph.py -v
.venv/bin/python -m pytest tests/test_bank_chat_brownfield_integration.py tests/test_call_edges_e2e.py -q
.venv/bin/ruff check .
```

Sentinel greps — **must return zero**:
```
grep -nE "_CREATE_(EXT|IMPL|INJ|DECL|OVERRIDES|CALL|UNRESOLVED|UNRESOLVED_AT)\b" build_ast_graph.py
```

Sentinel greps — **must be non-zero** (guards against over-deletion; these belong to PR-P2 / are retained):
```
grep -n "_MERGE_SYMBOL\b" build_ast_graph.py            # node upsert, kept until PR-P2
grep -n "_CREATE_CLIENT\b" build_ast_graph.py           # routes/clients, PR-P2
grep -n "MERGE (r:Route" build_ast_graph.py             # pass5/6 dedup, kept
grep -n "COPY .*FROM \$rows" build_ast_graph.py         # bulk path present
```

## Manual evidence (paste in PR description)

```bash
rm -rf /tmp/p1 && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --ladybug-path /tmp/p1/code_graph.lbug --verbose
.venv/bin/java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/p1
```
Expected: meta `counts_json` + node/edge counts identical to a pre-PR per-row
build (paste both). Note the graph-write phase timing from `JCIRAG_PROGRESS`
lines vs the pre-PR baseline.

## Definition of Done

- [ ] Step-1 spike result recorded in `_bulk_copy` docstring.
- [ ] `_write_edges` stages per-type rows (CALLS dedup + callee_declaring_role at staging); UnresolvedCallSite bulk-loaded before UNRESOLVED_AT.
- [ ] `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL` + local `_CREATE_UNRESOLVED/_UNRESOLVED_AT` deleted.
- [ ] `test_bulk_write_edges_match_per_row_baseline`, `test_bulk_write_is_deterministic_double_build`, `test_bulk_write_preserves_calls_dedup_and_callee_declaring_role`, `test_bulk_write_empty_rel_table_is_noop` pass.
- [ ] Full `test_ast_graph_build.py` + `test_incremental_graph.py` pass unchanged.
- [ ] Sentinel greps: zero where required, non-zero where required.
- [ ] `.venv/bin/ruff check .` clean; benchmark in PR description.
- [ ] PR title: `perf(graph): bulk COPY FROM for _write_edges (PR-P1)`.
````

---

## PR-P2 — Bulk write for nodes + routes/clients/producers/calls

**Branch:** `perf/bulk-graph-writes-p2` off PR-P1's branch (or `master` if PR-P1 merged).
**Base:** PR-P1 merged.
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P2 (read this first).
**Estimated diff size:** medium.

**Attach (`@-files`):**

- `@build_ast_graph.py` (`_write_nodes_impl:3029`, `_write_nodes:3096`, `_write_nodes_merge:817`, `_CREATE_SYMBOL`, `_MERGE_SYMBOL`, `_write_routes_and_exposes:3338`, `_write_clients_producers_and_calls:3810`, the Route `MERGE (r:Route` at `:3819`, `_write_meta:3421`, `_bulk_copy` + `_REL_*_COLUMNS` from PR-P1)
- `@tests/test_incremental_graph.py` (regression; add to `TestIncrementalOrchestrator`)

**Prompt:**

````
You are implementing PR-P2 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.
Read the **PR-P2** section in full. It reuses PR-P1's `_bulk_copy` +
`_REL_*_COLUMNS`. The plan wins.

## Scope

1. Convert `_write_nodes_impl` (:3029, the shared workhorse called by
   `_write_nodes` full + `_write_nodes_merge` incremental) to stage Symbol rows
   then `_bulk_copy(conn, "Symbol", NODE_COLUMNS, rows)`. Delete `_CREATE_SYMBOL`
   and `_MERGE_SYMBOL` (both dead once the workhorse is bulk). Do the existing
   `resolve_role_and_capabilities` + `type_role_by_node_id` population before
   staging, unchanged.
2. Convert `_write_routes_and_exposes` (:3338, shared) to per-table staging +
   `_bulk_copy` for Route/EXPOSES/Client/Producer/DECLARES_CLIENT/DECLARES_PRODUCER/
   HTTP_CALLS/ASYNC_CALLS (keep the existing `_file_by_node_id`/`_file_by_client_id`/
   `_file_by_producer_id` source_file resolution). Bulk-load Route/Client/Producer
   NODES before the EXPOSES/DECLARES_*/HTTP_CALLS/ASYNC_CALLS edges. Delete
   `_CREATE_ROUTE`/`_CREATE_EXPOSES`.
3. Convert `_write_clients_producers_and_calls` (:3810, incremental-only global
   pass5/6) Client/Producer/edge writes to per-type staging + `_bulk_copy` (keep
   the `member_by_id`/`client_by_id`/`producer_by_id` resolution). **Retain the
   `MERGE (r:Route {id:$id}) …` dedup (:3819-3828) verbatim** + add a one-line
   comment it is intentionally kept. Now delete the 6 shared constants —
   `_CREATE_CLIENT`/`_CREATE_PRODUCER`/`_CREATE_DECLARES_CLIENT`/
   `_CREATE_DECLARES_PRODUCER`/`_CREATE_HTTP_CALL`/`_CREATE_ASYNC_CALL` — which
   are dead only after BOTH routes/exposes and clients_producers functions convert.
4. Leave `_write_meta` (:3421) and its `MERGE (m:GraphMeta …)` UNTOUCHED.
5. Add the two named tests as methods of `TestIncrementalOrchestrator` in
   `tests/test_incremental_graph.py`.

## Out of scope (do NOT touch)

- `_write_edges` (done in PR-P1).
- `_write_meta` / GraphMeta MERGE — leave it.
- `_delete_file_scope`, `incremental_rebuild` algorithm, dependent-expansion,
  crash-marker logic.
- Anything outside `build_ast_graph.py` + `tests/test_incremental_graph.py`.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. `_write_nodes_impl` bulk; `_CREATE_SYMBOL` + `_MERGE_SYMBOL` deleted.
2. `_write_routes_and_exposes` bulk; `_CREATE_ROUTE`/`_CREATE_EXPOSES` deleted.
3. `_write_clients_producers_and_calls` Client/Producer/edges bulk; `MERGE (r:Route)` retained + commented; 6 shared `_CREATE_*` deleted.
4. `_write_meta` untouched.
5. Two new tests in `TestIncrementalOrchestrator`.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_incremental_graph.py tests/test_ast_graph_build.py -v
.venv/bin/ruff check .
```

Sentinel greps — **must return zero**:
```
grep -nE "_CREATE_(SYMBOL|ROUTE|EXPOSES|CLIENT|PRODUCER|DECLARES_CLIENT|DECLARES_PRODUCER|HTTP_CALL|ASYNC_CALL)\b" build_ast_graph.py
grep -nE "_MERGE_SYMBOL\b" build_ast_graph.py
```

Sentinel greps — **must be non-zero** (Route dedup + GraphMeta MERGE retained; bulk present):
```
grep -n "MERGE (r:Route" build_ast_graph.py
grep -n "MERGE (m:GraphMeta" build_ast_graph.py
grep -n "COPY .*FROM \$rows" build_ast_graph.py
```

## Manual evidence (paste in PR description)

Single-file change equivalence:
```bash
# set up an index, touch one file, increment (bulk), then full-rebuild the same
# state (bulk) and diff graphs (node count, per-type edge counts, GraphMeta).
```
Expected: incremental(bulk) == full-rebuild(bulk) for that state. Paste side-by-side counts.

## Definition of Done

- [ ] `_write_nodes_impl` bulk; `_CREATE_SYMBOL` + `_MERGE_SYMBOL` deleted.
- [ ] `_write_routes_and_exposes` bulk (Route/Client/Producer before edges); `_CREATE_ROUTE`/`_CREATE_EXPOSES` deleted.
- [ ] `_write_clients_producers_and_calls` Client/Producer/edges bulk; `MERGE (r:Route)` retained + commented; 6 shared `_CREATE_*` deleted.
- [ ] `_write_meta` untouched.
- [ ] `test_incremental_bulk_write_equivalent_to_full_rebuild`, `test_incremental_route_merge_dedup_preserved` (both in `TestIncrementalOrchestrator`) pass; full `test_incremental_graph.py` + `test_ast_graph_build.py` green.
- [ ] Sentinel greps pass.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(graph): bulk COPY FROM for nodes, routes, clients/producers (PR-P2)`.
````

---

## PR-P3 — Cached `LayeredIgnore` + `is_ignored` memo

**Branch:** `perf/cached-ignore-p3` off `master`.
**Base:** `master` (independent of PR-P1/P2).
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P3 (read this first).
**Estimated diff size:** small.

**Attach (`@-files`):**

- `@java_index_flow_lancedb.py` (`ContextKey` defs `:60-72`, `coco_lifespan` provide sites `:287-306`, `process_java_file:345`/`process_sql_file:417`/`process_yaml_file:465`)
- `@path_filtering.py` (`LayeredIgnore`, `_mega:334`, `_mega_build_for_rel:193`, `is_ignored:345`, `diagnose_dict:377`)
- `@tests/test_path_filtering.py` (where the two memo unit tests go)
- `@tests/test_lancedb_e2e.py` (HEAVY once-per-flow test)

**Prompt:**

````
You are implementing PR-P3 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.

Read the **PR-P3** section in full. Independent of PR-P1/P2. The plan wins.

KEY FACT: `LayeredIgnore(project_root)` appears at FIVE sites in
java_index_flow_lancedb.py — :177, :351, :423, :471, :569. PR-P3 converts ONLY
the three `process_*_file` sites (:351/:423/:471). The other two (:177 in
`_approximate_vectors_total`, :569 in the app_main pre-walk) call
`cocoindex_excluded_patterns()` ONCE PER RUN — leave them alone.

## Scope

1. Define `IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")`
   alongside `PROJECT_ROOT`/`EMBEDDER`/`LANCE_DB` (:60-72), reusing the SAME
   `_ck_params` (`detect_change` vs `tracked`) detection block.
2. In `coco_lifespan` (:287-306), add `builder.provide(IGNORE, LayeredIgnore(root))`
   — built ONCE per flow run.
3. In `process_java_file`/`process_sql_file`/`process_yaml_file`: add
   `ignore = coco.use_context(IGNORE)` and replace
   `LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())`
   with `ignore.is_ignored((project_root / file.file_path.path).resolve())`.
   Keep `project_root`. DO NOT touch :177 or :569.
4. In `path_filtering.py` `LayeredIgnore`: add `self._mega_cache` in `__init__`
   and memoize `_mega(rel)` keyed by `Path(rel_project).parent.as_posix()`
   (`_mega_build_for_rel` reads only `dir_parts`, so this is correct).
5. Add the three named tests in the right files.

## Out of scope (do NOT touch)

- `build_ast_graph.py` and the graph write path (PR-P1/P2).
- The ignore *decision* logic (`_mega_build_for_rel`, `_winning_row`, negation
  scanning) — only memoize.
- Sites :177 and :569.
- Any schema/ontology/re-index change. Loosening any existing test.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. `IGNORE` ContextKey (version-detected) + provided once in `coco_lifespan`.
2. The three `process_*_file` consume it; :177/:569 untouched.
3. `_mega` memoized by directory in `LayeredIgnore`.
4. Three tests: two in `tests/test_path_filtering.py`, one (HEAVY) in `tests/test_lancedb_e2e.py`.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_path_filtering.py tests/test_lancedb_e2e.py -q
.venv/bin/python -m pytest tests -q -k "ignore or path_filter or vectors_progress"
.venv/bin/ruff check .
```
Heavy (only if you can run cocoindex e2e locally):
```
JAVA_CODEBASE_RAG_RUN_HEAVY=1 .venv/bin/python -m pytest tests/test_lancedb_e2e.py -q
```

Sentinel greps — **must return zero** (matches ONLY the 3 process sites; :177/:569 use the bare constructor + `cocoindex_excluded_patterns`, not `.is_ignored`):
```
grep -nE "LayeredIgnore\(project_root\)\.is_ignored" java_index_flow_lancedb.py
```

Sentinel greps — **must be non-zero**:
```
grep -n "coco.use_context(IGNORE)" java_index_flow_lancedb.py   # 3 sites
grep -n "_mega_cache" path_filtering.py                          # memo present
```

## Manual evidence (paste in PR description)

With `JAVA_CODEBASE_RAG_RUN_HEAVY=1`, run the flow over a small corpus and log
`id(ignore)` per file (temporary instrumentation) — confirm a single object id
across all files. Then micro-benchmark `is_ignored` over N files: same-directory
files hit the `_mega` cache.

## Definition of Done

- [ ] `IGNORE` ContextKey (version-detected) + provided once in `coco_lifespan`.
- [ ] The three `process_*_file` consume it; :177/:569 untouched.
- [ ] `_mega` memoized by directory; `is_ignored`/`diagnose_dict` results unchanged.
- [ ] `test_is_ignored_mega_caches_by_directory`, `test_layered_ignore_memo_preserves_decisions` (in `tests/test_path_filtering.py`), `test_layered_ignore_provided_once_per_flow` (in `tests/test_lancedb_e2e.py`, HEAVY) pass.
- [ ] Existing ignore + vectors-progress tests pass unchanged.
- [ ] Sentinel greps pass.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(vectors): lifespan-cached LayeredIgnore + is_ignored memo (PR-P3)`.
````

---

## Notes for the orchestrator

- **Landing order:** PR-P1 → PR-P2 (P2 needs `_bulk_copy` + `_REL_*_COLUMNS`).
  PR-P3 is independent (can go first, last, or between).
- **Shared-helper awareness:** `_write_edges`, `_write_routes_and_exposes`,
  `_write_nodes_impl`, `_write_meta` are each called by BOTH paths. Converting
  one accelerates both — so `test_incremental_graph.py` is a binding gate for
  PR-P1 and PR-P2, not just PR-P2.
- **Review between PRs** (`superpowers:requesting-code-review`): the equivalence
  harness gates P1/P2; the memo-parity test gates P3.
- **Sentinel greps are binding:** a non-empty "must return zero" grep = scope
  leak; an empty "must be non-zero" grep = over-deletion. Either blocks merge.
