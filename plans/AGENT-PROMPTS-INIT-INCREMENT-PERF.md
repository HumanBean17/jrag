# Agent task prompts — Faster init/increment (PR-P1 → PR-P3)

Status: **active**. One self-contained prompt per PR. Copy the prompt verbatim
into the agent, attach the files in its `@-files` block, and let it execute.

**Workflow per PR:**

1. Create the branch named in the prompt off the stated base.
2. Read the cited plan section in full **before** writing code.
3. Implement step-by-step; run the listed tests after each step.
4. Run the sentinel greps — every "must return zero" line must be empty.
5. Paste the manual-evidence output into the PR description.
6. Open a PR with the exact title in the Definition of Done.

**Universal rules for every prompt:**

- Use only `.venv/bin/python` and `.venv/bin/pip` (never system python/pip).
- `server.py` is stdio — never write to stdout from anything reachable by a tool handler.
- Do not add a cocoindex dependency outside `java_index_flow_lancedb.py`.
- The plan is the source of truth — if this prompt and the plan disagree, the plan wins.
- Do not touch any file outside the prompt's `@-files` + the test files it names. If you think an adjacent file must change, **stop and ask** — don't ship it.
- Do not loosen any existing test assertion to make it pass.
- Breaking changes are allowed; no compatibility shims.

---

## PR-P1 — Bulk `COPY FROM` for the full rebuild path

**Branch:** `perf/bulk-graph-writes-p1` off `master`.
**Base:** `master`.
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P1 (read this first).
**Estimated diff size:** medium (one module + tests + a fixture).

**Attach (`@-files`):**

- `@build_ast_graph.py` (the full-rebuild write path: `_write_nodes`, `_write_edges`, `_write_routes_and_exposes`, `write_ladybug`, `_node_row`, `_SCHEMA_*`, `_callee_declaring_role_at_write`, `_populate_declares_rows`, `_populate_overrides_rows`)
- `@propose/active/INIT-INCREMENT-PERF-PROPOSE.md` (design + staging invariants)
- `@tests/test_ast_graph_build.py` (existing regression net + where the new tests go)
- `@tests/_builders.py` (graph-build helpers: `build_ladybug_full_into`, etc.)

**Prompt:**

````
You are implementing PR-P1 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.

Read the **PR-P1** section of the plan in full before writing any code, plus the
proposal's "Staging invariants" and "Equivalence" paragraphs. The plan is the
source of truth — if this prompt and the plan disagree, the plan wins.

## Scope

Implement PR-P1 exactly as specified: replace the per-row `conn.execute` writes
in the **full rebuild path** (`write_ladybug`) with bulk in-memory-pyarrow
`COPY FROM`. Concretely:

1. **Step-1 spike (first commit):** confirm the exact REL `COPY FROM` column
   order + `pa.Table.from_pylist` LIST typing with a throwaway 2-node + 1-edge
   toy. Record the working incantation in the `_bulk_copy` docstring.
2. Add `_bulk_copy(conn, table_name, columns, rows)` + the `*_COLUMNS` /
   `_REL_*_COLUMNS` constants (column order matches `_SCHEMA_*`; REL tables list
   FROM/TO first).
3. Convert `_write_nodes` (full path) to stage all node rows then
   `_bulk_copy(conn, "Symbol", NODE_COLUMNS, rows)`. Delete `_CREATE_SYMBOL`.
4. Convert `_write_edges` to per-edge-type row staging (applying the SAME
   `seen_calls`/`seen_ucs` dedup and SAME `_callee_declaring_role_at_write`
   lookup, at staging time) then `_bulk_copy` each REL table. Delete the dead
   `_CREATE_EXT/IMPL/INJ/DECL/OVERRIDES/CALL/UNRESOLVED/UNRESOLVED_AT` strings.
5. Convert `_write_routes_and_exposes` (+ Client/Producer/HTTP_CALLS/ASYNC_CALLS)
   to bulk; delete the dead `_CREATE_ROUTE/EXPOSES/CLIENT/DECLARES_CLIENT/
   PRODUCER/DECLARES_PRODUCER/HTTP_CALL/ASYNC_CALL` strings.
6. Convert the `GraphMeta` write (`:3472-3473`) to a single-row `_bulk_copy`.
7. Generate + commit `tests/fixtures/graph_baseline_bank_chat.json` from the
   **last per-row build** before you remove the per-row path.
8. Add the four named tests.

## Out of scope (do NOT touch)

- The incremental path: `_write_nodes_merge`, `_MERGE_SYMBOL`, `_delete_file_scope`,
  `incremental_rebuild`, and the pass5/6 `MERGE (r:Route)` (`:3819-3821`) stay
  exactly as-is. PR-P1 is full-rebuild only.
- `java_index_flow_lancedb.py`, `path_filtering.py`, `server.py`, `search_lancedb.py`.
- Any schema (`_SCHEMA_*` DDL), ontology, or re-index change.
- CSV or Parquet-file staging (pyarrow in-memory only).
- Loosening any existing test.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. `_bulk_copy` helper + column-order constants in `build_ast_graph.py`.
2. `write_ladybug` full path bulk-loads all node tables then all REL tables.
3. Dead `_CREATE_*` / `_CREATE_SYMBOL` strings removed.
4. `tests/fixtures/graph_baseline_bank_chat.json` committed.
5. Four new tests in `tests/test_ast_graph_build.py`.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_ast_graph_build.py -v
.venv/bin/python -m pytest tests/test_incremental_graph.py tests/test_bank_chat_brownfield_integration.py tests/test_call_edges_e2e.py -q
.venv/bin/ruff check .
```

Sentinel greps — **must all return zero** (no output):
```
grep -n "_CREATE_SYMBOL\b" build_ast_graph.py
grep -nE "conn\.execute\(_CREATE_(EXT|IMPL|INJ|DECL|OVERRIDES|CALL|UNRESOLVED|ROUTE|EXPOSES|CLIENT|DECLARES_CLIENT|PRODUCER|DECLARES_PRODUCER|HTTP_CALL|ASYNC_CALL)" build_ast_graph.py
```

Sentinel greps — **must be non-zero** (guards against over-deletion):
```
grep -n "_MERGE_SYMBOL\b" build_ast_graph.py        # incremental path, kept
grep -n "MERGE (r:Route" build_ast_graph.py          # pass5/6 dedup, kept
grep -n "COPY .*FROM \$rows" build_ast_graph.py      # bulk path present
```

## Manual evidence (paste in PR description)

Build the fixture via the bulk path and inspect meta:
```bash
rm -rf /tmp/p1 && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --ladybug-path /tmp/p1/code_graph.lbug --verbose
.venv/bin/java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/p1
```
Expected: meta `counts_json` and node/edge counts **identical** to a pre-PR
per-row build (paste both side by side). Note the graph-write phase timing from
the `JCIRAG_PROGRESS` lines vs the pre-PR baseline.

## Definition of Done

- [ ] Step-1 spike result recorded in `_bulk_copy` docstring.
- [ ] Full path bulk-loads nodes-then-edges; no per-row `CREATE` remains in the full path.
- [ ] `_CREATE_SYMBOL` and the dead `_CREATE_*` edge/node strings deleted.
- [ ] All four new tests pass; full regression suites pass unchanged.
- [ ] All "must return zero" sentinel greps are empty; all "non-zero" greps hit.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] Benchmark (before/after graph-write phase) pasted in the PR description.
- [ ] PR title: `perf(graph): bulk COPY FROM for the full rebuild path (PR-P1)`.
````

---

## PR-P2 — Bulk write for the incremental path

**Branch:** `perf/bulk-graph-writes-p2` off PR-P1's branch (or `master` if PR-P1 has merged).
**Base:** PR-P1 merged.
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P2 (read this first).
**Estimated diff size:** small-medium.

**Attach (`@-files`):**

- `@build_ast_graph.py` (`_write_nodes_merge`, `_MERGE_SYMBOL`, `_delete_file_scope`, `incremental_rebuild`, `_bulk_copy` + `_REL_*_COLUMNS` from PR-P1)
- `@tests/test_incremental_graph.py` (regression net)

**Prompt:**

````
You are implementing PR-P2 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.

Read the **PR-P2** section in full. It depends on PR-P1's `_bulk_copy` primitive
and `_REL_*_COLUMNS` constants, which are already merged. The plan wins if this
prompt disagrees.

## Scope

Apply PR-P1's bulk primitive to the incremental path:

1. Convert `_write_nodes_merge` (`:817`, uses `_MERGE_SYMBOL`) to stage rows then
   `_bulk_copy(conn, "Symbol", NODE_COLUMNS, rows)`. The incremental path is
   delete-then-insert (`_delete_file_scope` already removed the old scope), so
   plain `COPY` insert into the cleaned scope is correct. Delete `_MERGE_SYMBOL`;
   remove `_write_nodes_impl` if it has no remaining caller.
2. Convert the incremental edge re-emit to per-type staging + `_bulk_copy`,
   scoped to the re-emitted files (same pattern as PR-P1's `_write_edges`).
3. **Retain the pass5/6 `MERGE (r:Route)` dedup** (`:3819-3821`) verbatim and add
   a one-line comment explaining it is intentionally kept (routes written during
   the scoped step must MERGE, not duplicate, against the global step). Do NOT
   bulk-convert the Route writes that this MERGE guards.
4. Add the two named tests.

## Out of scope (do NOT touch)

- The full-rebuild path (already bulk in PR-P1).
- Route dedup semantics — keep the `MERGE (r:Route)` exactly.
- `_delete_file_scope`, `incremental_rebuild` algorithm, dependent-expansion,
  crash-marker logic.
- Anything outside `build_ast_graph.py` + `tests/test_incremental_graph.py`.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. Incremental node re-emit via `_bulk_copy`; `_MERGE_SYMBOL` deleted.
2. Incremental edge re-emit via per-type `_bulk_copy`.
3. `MERGE (r:Route)` retained + commented.
4. Two new tests in `tests/test_incremental_graph.py`.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_incremental_graph.py -v
.venv/bin/python -m pytest tests/test_ast_graph_build.py -q
.venv/bin/ruff check .
```

Sentinel greps — **must return zero**:
```
grep -n "_MERGE_SYMBOL\b" build_ast_graph.py
```

Sentinel greps — **must be non-zero** (Route dedup still present; bulk still used):
```
grep -n "MERGE (r:Route" build_ast_graph.py
grep -n "COPY .*FROM \$rows" build_ast_graph.py
```

## Manual evidence (paste in PR description)

Single-file change equivalence:
```bash
# set up an index, touch one file, increment, then full-rebuild the same state
# and diff the graphs (node count, per-type edge counts, GraphMeta counters).
```
Expected: incremental(bulk) == full-rebuild(bulk) for that state. Paste the
side-by-side counts.

## Definition of Done

- [ ] Incremental node/edge re-emit uses `_bulk_copy`; `_MERGE_SYMBOL` deleted.
- [ ] `MERGE (r:Route)` retained and commented.
- [ ] `test_incremental_bulk_write_equivalent_to_full_rebuild`,
      `test_incremental_route_merge_dedup_preserved` pass; full
      `tests/test_incremental_graph.py` passes unchanged.
- [ ] Sentinel greps pass (zero where required, non-zero where required).
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(graph): bulk COPY FROM for the incremental path (PR-P2)`.
````

---

## PR-P3 — Cached `LayeredIgnore` + `is_ignored` memo

**Branch:** `perf/cached-ignore-p3` off `master`.
**Base:** `master` (independent of PR-P1/P2).
**Plan section:** `plans/active/PLAN-INIT-INCREMENT-PERF.md` § PR-P3 (read this first).
**Estimated diff size:** small.

**Attach (`@-files`):**

- `@java_index_flow_lancedb.py` (`ContextKey` defs `:60-72`, `coco_lifespan` provide sites `~:287-306`, `process_java_file`/`process_sql_file`/`process_yaml_file` `:344`/`:416`/`:464`)
- `@path_filtering.py` (`LayeredIgnore`, `_mega`, `_mega_build_for_rel`, `is_ignored`)
- `@tests/test_lancedb_e2e.py` (ignore test to keep green)

**Prompt:**

````
You are implementing PR-P3 from `plans/active/PLAN-INIT-INCREMENT-PERF.md`.

Read the **PR-P3** section in full. It is independent of PR-P1/P2. The plan wins
if this prompt disagrees.

## Scope

1. Define `IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")`
   alongside `PROJECT_ROOT`/`EMBEDDER`/`LANCE_DB` (`:60-72`), reusing the SAME
   `_ck_params` (`detect_change` vs `tracked`) detection block.
2. In `coco_lifespan`, add `builder.provide(IGNORE, LayeredIgnore(root))` next to
   the other `builder.provide(...)` calls — built **once** per flow run.
3. In `process_java_file` (`:344`), `process_sql_file` (`:416`), `process_yaml_file`
   (`:464`): add `ignore = coco.use_context(IGNORE)` and replace
   `LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())`
   with `ignore.is_ignored((project_root / file.file_path.path).resolve())`.
   Keep `project_root` (still used for path resolution and `_parse_and_enrich_java`).
4. In `path_filtering.py` `LayeredIgnore`: add `self._mega_cache` in `__init__`
   and memoize `_mega(rel)` keyed by `Path(rel_project).parent.as_posix()` (mega
   depends only on the directory — `_mega_build_for_rel` reads `dir_parts` only).
   `is_ignored`/`diagnose_dict` call `_mega` unchanged and benefit transparently.
5. Add the three named tests.

## Out of scope (do NOT touch)

- `build_ast_graph.py` and the graph write path (PR-P1/P2 own that).
- The ignore *decision* logic (`_mega_build_for_rel`, `_winning_row`, negation
  scanning) — only memoize, do not alter semantics.
- Any schema, ontology, or re-index change.
- Loosening any existing test.

If you find yourself wanting to touch any of the above, **stop and ask**.

## Deliverables

1. `IGNORE` ContextKey defined + provided once in `coco_lifespan`.
2. The three `process_*_file` functions consume it (no per-file construction).
3. `_mega` memoized by directory in `LayeredIgnore`.
4. Three new tests.

## Tests

Run, all must pass:
```
.venv/bin/python -m pytest tests/test_lancedb_e2e.py -q
.venv/bin/python -m pytest tests -q -k "ignore or path_filter or vectors_progress"
.venv/bin/ruff check .
```
Heavy (only if you can run cocoindex e2e locally):
```
JAVA_CODEBASE_RAG_RUN_HEAVY=1 .venv/bin/python -m pytest tests/test_lancedb_e2e.py -q
```

Sentinel greps — **must return zero**:
```
grep -nE "LayeredIgnore\(project_root\)" java_index_flow_lancedb.py
```

Sentinel greps — **must be non-zero**:
```
grep -n "coco.use_context(IGNORE)" java_index_flow_lancedb.py   # 3 sites
grep -n "_mega_cache" path_filtering.py                          # memo present
```

## Manual evidence (paste in PR description)

Show the ignore object is built once: with `JAVA_CODEBASE_RAG_RUN_HEAVY=1`, run
the flow over a small corpus and log `id(ignore)` per file (temporary instrumentation),
confirm a single object id across all files. Then confirm a micro-benchmark of
`is_ignored` over N files drops with the `_mega` cache (same-directory files hit
the cache).

## Definition of Done

- [ ] `IGNORE` ContextKey defined (version-detected) + provided once in `coco_lifespan`.
- [ ] The three `process_*_file` consume it; no per-file `LayeredIgnore(project_root)`.
- [ ] `_mega` memoized by directory; `is_ignored`/`diagnose_dict` results unchanged.
- [ ] `test_is_ignored_mega_caches_by_directory`,
      `test_layered_ignore_memo_preserves_decisions`,
      `test_layered_ignore_provided_once_per_flow` pass.
- [ ] Existing ignore + vectors-progress tests pass unchanged.
- [ ] Sentinel greps pass.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `perf(vectors): lifespan-cached LayeredIgnore + is_ignored memo (PR-P3)`.
````

---

## Notes for the orchestrator

- **Landing order:** PR-P1 → PR-P2 (P2 depends on P1's `_bulk_copy` +
  `_REL_*_COLUMNS`). PR-P3 is independent and can go first, last, or between.
- **Review between PRs:** request code review after each PR lands (see
  `superpowers:requesting-code-review`) — the equivalence harness is the gate
  for P1/P2; the memo-parity test is the gate for P3.
- **Sentinel greps are binding:** a non-empty "must return zero" grep means scope
  leaked; a empty "must be non-zero" grep means an over-deletion. Either blocks merge.
