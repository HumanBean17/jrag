# Plan: Tier 2 — Incremental Kuzu Rebuild

Status: **active (planning)**. This plan implements
[`propose/active/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](../../propose/active/TIER2-INCREMENTAL-REBUILD-PROPOSE.md)
as a four-PR sequence (PR-T5 brownfield refinement deferred). This file is
plan-only and does not implement code.

Depends on: none (ontology 16 surface is stable; no pending PRs block this).

## Goal

- File-level incremental Kuzu rebuild that touches only nodes/edges derived
  from changed `.java` files and their dependency closure.
- Bit-for-bit equivalence between incremental and full rebuild for the same
  final source-tree state (verified by determinism test per fixture).
- `java-codebase-rag increment` updates both Lance and Kuzu incrementally
  when the decision engine (co-proposed in `INDEX-AUTO-MODE-PROPOSE.md`)
  deems it safe; falls back to full rebuild otherwise.
- No schema churn — ontology 16 surface is identical; incremental is a
  build-strategy optimisation.

## Principles (do not relitigate in review)

- **Full rebuild is the safe fallback.** Any ambiguity, missing state, or
  unexpected condition triggers full rebuild. Incremental is opt-in at the
  decision-engine level, never the default for edge cases.
- **Pass6 always reruns globally.** Cross-service matching is fast (~ms on
  bank-chat-system) and spans services. Incremental optimisation of pass6
  is out of scope.
- **`_write_meta` always rewritten from global DB state.** Aggregation fields
  (`routes_total`, `cross_service_calls_total`, match breakdowns) reflect
  global state. In incremental mode, `_write_meta` must compute stats by
  querying the live Kuzu DB (not from the partial `GraphTables` accumulator),
  since the accumulator only holds dirty-file data.
- **`.deps.json` is a build cache, not graph data.** Sidecar file, atomic
  write-temp-rename, `ontology_version` field for staleness detection.
- **Symmetric delete model.** Every pass that emits nodes/edges keyed by
  source file has a matching `delete_*_for_file` helper. The delete helper
  set must stay in sync with the pass set.
- **Transaction-wrapped incremental writes.** On any exception mid-incremental,
  ROLLBACK and fall back to full rebuild.
- **No new MCP tools, no new CLI commands.** Existing `increment` command
  gains Kuzu incremental support. `build_ast_graph.py` gains internal
  `--changed-paths` flag (not user-facing).
- **Brownfield changes force full rebuild** (PR-T5 may narrow this). Any
  file containing `@CodebaseClient` / `@CodebaseProducer` / role/capability
  override annotations in the change set triggers full rebuild.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-T1** | Foundation: `FileDeps` dataclass, `.deps.json` read/write, determinism test, perf baseline | none | `.deps.json` schema must be right first time (version field, field coverage for closure rules); determinism test coverage must surface divergence | determinism + deps-read/write | prerequisite only |
| **PR-T2** | Symmetric delete helpers: `delete_*_for_file` for all node/edge types | none | Cypher DELETE must match current schema exactly; cascade semantics (Symbol delete must clean edges); count accuracy for verbose logging | per-node-type delete + cascade | PR-T1 (needs `.deps.json` read) |
| **PR-T3** | Incremental orchestrator: `build_ast_graph_incremental`, `--changed-paths`, per-pass subset functions, closure expansion | none | Closure correctness (missing rule = silent divergence); transaction semantics; pass6/global-invariant; incremental-write functions | equivalence on all fixtures + closure expansion + subset passes | PR-T1 + PR-T2 |
| **PR-T4** | CLI + decision engine: integrate into `_cmd_increment`, remove warning, create `refresh_code_index` MCP tool | none | Decision-engine correctness (wrong mode = stale graph); `refresh_code_index` is new (not an update); CLI stderr format consistency | decision engine + CLI integration + MCP tool | PR-T3 |
| **PR-T5** | Brownfield closure refinement (optional, deferred) | none | Brownfield fanout rules must be formalised before narrowing | brownfield closure tests | PR-T4 |

Landing order: **T1 -> T2 -> T3 -> T4**. PR-T5 is optional and may follow.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| FileDeps storage | **Sidecar `<kuzu-path>/.deps.json`** — cheap Python-level inverse-map, no Cypher overhead, no schema evolution burden. Atomic write-temp-rename. |
| Closure computation | **Invert `.deps.json` maps at runtime** — O(\|dirty\| x avg_inverse_degree), not O(\|dirty\| x \|all-files\|). |
| Pass6 scope | **Always global** — fast, spans services, idempotency contract already in place. |
| Brownfield granularity | **Pessimistic full fallback** — any brownfield-override file in change set triggers full rebuild. Fine-grained closure deferred to PR-T5. |
| Lance incremental | **Out of scope** — already covered by CocoIndex native incremental. This plan is Kuzu-only. |
| Concurrent rebuilds | **Single-writer assumed** — undefined behaviour if two runs race. Document only. |
| Schema migrations | **Full rebuild required** — ontology bump invalidates `.deps.json` via `ontology_version` check. |
| `_write_meta` in incremental | **Query live Kuzu DB for global stats** — the partial `GraphTables` accumulator only holds dirty-file data, so aggregation counts would be wrong. `_write_meta` in incremental mode runs a set of COUNT Cypher queries against the live DB to compute `routes_total`, `calls_total`, match breakdowns, etc. |
| `pass5_imperative_edges` and `asts` | **`pass5` does not use `asts`** (it does `del asts` and works from `tables.members`). Subset version mirrors this: `pass5_imperative_edges_subset(tables, dirty)` without an `asts` parameter. |
| `refresh_code_index` MCP tool | **Does not exist yet** — must be created in PR-T4. The proposal (INDEX-AUTO-MODE) specifies its schema; PR-T4 is the first implementation. |
| `refresh_decision.py` location | **Top-level module** (`refresh_decision.py`) — imported by both `java_codebase_rag/cli.py` and `server.py`. Lives alongside `build_ast_graph.py`. |
| Test fixture strategy | **Per-test fresh builds** for equivalence tests (Tier 3 in `tests/README.md`). Use `tests/_builders.py` helpers (`build_kuzu_full_into`, `build_graph_tables_to`) for full-rebuild baselines. Session fixtures (Tier 1/2) are read-only and cannot be mutated for incremental tests. |
| `graph_meta.last_rebuild_mode` | **Added in PR-T3** — string field on `GraphMeta` node: `"full"` or `"incremental"`. Used for fallback-rate monitoring (cross-PR risk #5). |

---

# PR-T1 — Foundation + determinism test + perf baseline

## File-by-file changes

### 1. `build_ast_graph.py`
- Add `FileDeps` dataclass (fields: `ext_hash`, `declares`, `injects`,
  `extends`, `calls`, `uses_anno`, `overrides`, `declares_clients`,
  `declares_producers`). File path is the dict key in `.deps.json`, not a
  field value — matches proposal §2.4 JSON schema.
- Add `_build_file_deps(tables, asts, source_root) -> dict[str, FileDeps]` —
  populates per-file dependency metadata from `GraphTables` in-memory data and
  parsed ASTs.
- Add `_write_dependency_index(deps_path, file_deps, ontology_version)` —
  writes sidecar JSON via write-temp-rename.
- Add `_read_dependency_index(deps_path) -> DepsIndex | None` — reads and
  validates sidecar JSON (checks `version`, `ontology_version`). Returns
  `None` on missing/corrupt/stale.
- Call `_write_dependency_index` at the end of `write_kuzu` (after `_write_meta`)
  to populate `.deps.json` on every full rebuild. This makes every full rebuild
  produce a fresh dependency index, ready for a subsequent incremental run.

### 2. `tests/test_incremental_equivalence.py` (new)
- `test_full_rebuild_is_deterministic` — run full rebuild on bank-chat-system
  twice into separate Kuzu paths; assert all node IDs and edge rows are
  identical. Validates the foundation: if full rebuild isn't deterministic,
  incremental equivalence testing is meaningless.
- `test_deps_json_written_on_full_rebuild` — after a full rebuild, verify
  `.deps.json` exists, is valid JSON, has `version: 1`, correct
  `ontology_version`, and non-empty `files` dict.
- `test_deps_json_fields_coverage` — spot-check a known file (e.g.
  `ChatController.java`) has expected `declares`, `injects`, `extends` entries
  matching the bank-chat fixture structure.
- `test_deps_json_stale_detection` — write a `.deps.json` with wrong
  `ontology_version`; assert `_read_dependency_index` returns `None`.
- `test_perf_baseline_full_rebuild` — measure wall-clock time for full rebuild
  on `bank-chat-system` and `cross_service_smoke` fixtures. Record in
  `tests/fixtures/perf_baselines.json` under a `tier2_full_rebuild` key.
  Non-blocking (informational) — establishes the number that incremental
  results will be compared against in PR-T3.

## Tests for PR-T1
1. `test_full_rebuild_is_deterministic`
2. `test_deps_json_written_on_full_rebuild`
3. `test_deps_json_fields_coverage`
4. `test_deps_json_stale_detection`
5. `test_perf_baseline_full_rebuild`

## Definition of done (PR-T1)
- `.deps.json` written on every full rebuild, readable, validated.
- Determinism test passes: two full rebuilds produce identical graph state.
- All existing tests pass (no regression from `write_kuzu` change).
- `ruff check .` clean.

## Implementation step list
| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `FileDeps` dataclass | `build_ast_graph.py` | Dataclass defined with all fields from proposal §2.4 |
| 2 | Add `_build_file_deps` | `build_ast_graph.py` | Populates FileDeps from GraphTables + ASTs |
| 3 | Add `_write_dependency_index` + `_read_dependency_index` | `build_ast_graph.py` | Write produces valid JSON; read validates version + ontology |
| 4 | Wire `_write_dependency_index` into `write_kuzu` | `build_ast_graph.py` | `.deps.json` appears after full rebuild |
| 5 | Write determinism test | `tests/test_incremental_equivalence.py` | Two full rebuilds produce identical output |
| 6 | Write deps.json tests | `tests/test_incremental_equivalence.py` | Read/write/validate/stale-detection pass |
| 7 | Run full test suite + ruff | all | Green |

---

# PR-T2 — Symmetric delete helpers

## File-by-file changes

### 1. `build_ast_graph.py`
- Add delete helper functions, each taking `(conn: kuzu.Connection, file_path: str) -> int`
  and returning the deleted row count:
  - `delete_symbols_for_file` — DELETE Symbol nodes where `path = $file_path`.
    Kuzu does not cascade rel-table deletes when a node is deleted (rel tables
    in Kuzu require explicit deletion). This helper also explicitly deletes
    all DECLARES edges where the declaring type's Symbol is in the file, plus
    member Symbol nodes for methods/constructors declared in those types.
  - `delete_extends_for_file` — DELETE EXTENDS edges where source Symbol's
    file matches.
  - `delete_implements_for_file` — DELETE IMPLEMENTS edges, same pattern.
  - `delete_injects_for_file` — DELETE INJECTS edges, same pattern.
  - `delete_calls_for_file` — DELETE CALLS edges where caller Symbol's file
    matches. Also deletes associated `UnresolvedCallSite` nodes and
    `UNRESOLVED_AT` edges whose caller Symbol is in the file.
  - `delete_routes_for_file` — DELETE Route nodes where owner Symbol's file
    matches, plus associated EXPOSES edges.
  - `delete_clients_for_file` — DELETE Client nodes where declaring method's
    file matches, plus DECLARES_CLIENT edges.
  - `delete_producers_for_file` — DELETE Producer nodes, plus
    DECLARES_PRODUCER edges.
  - `delete_http_calls_for_file` — DELETE HTTP_CALLS edges where Client's
    declaring method's file matches.
  - `delete_async_calls_for_file` — DELETE ASYNC_CALLS edges, same pattern.
  - `delete_overrides_for_file` — DELETE OVERRIDES edges where subtype
    method's file matches.
  - `delete_all_for_file(conn, file_path) -> dict[str, int]` — calls all
    above helpers, returns `{helper_name: count}` for verbose logging.

### 2. `tests/test_symmetric_delete.py` (new)
- Per-helper tests using the bank-chat-system session fixture:
  - `test_delete_symbols_for_file` — delete symbols from a known file,
    verify count > 0, verify nodes gone via Cypher query. Also verify
    associated DECLARES edges and member Symbol nodes are deleted.
  - `test_delete_extends_for_file` — delete EXTENDS edges for a file with
    known inheritance, verify count.
  - `test_delete_implements_for_file`
  - `test_delete_injects_for_file`
  - `test_delete_calls_for_file` — also verify UnresolvedCallSite cleanup.
  - `test_delete_routes_for_file` — also verify EXPOSES cleanup.
  - `test_delete_clients_for_file` — also verify DECLARES_CLIENT cleanup.
  - `test_delete_producers_for_file`
  - `test_delete_http_calls_for_file`
  - `test_delete_async_calls_for_file`
  - `test_delete_overrides_for_file`
  - `test_delete_all_for_file` — verify composite function calls all helpers.
  - `test_delete_idempotent` — calling delete twice returns 0 on second call.
  - `test_delete_unknown_file_returns_zero` — nonexistent path deletes nothing.

## Tests for PR-T2
1. `test_delete_symbols_for_file`
2. `test_deletes_declares_edges`
3. `test_delete_extends_for_file`
4. `test_delete_implements_for_file`
5. `test_delete_injects_for_file`
6. `test_delete_calls_for_file`
7. `test_deletes_unresolved_call_sites`
8. `test_delete_routes_for_file`
9. `test_deletes_exposes_edges`
10. `test_delete_clients_for_file`
11. `test_deletes_declares_client_edges`
12. `test_delete_producers_for_file`
13. `test_deletes_declares_producer_edges`
14. `test_delete_http_calls_for_file`
15. `test_delete_async_calls_for_file`
16. `test_delete_overrides_for_file`
17. `test_delete_all_for_file`
18. `test_calls_each_helper`
19. `test_delete_idempotent`
20. `test_delete_unknown_file_returns_zero`

## Definition of done (PR-T2)
- All delete helpers implemented and unit-tested in isolation.
- Each helper returns accurate count.
- Idempotent (second call on same file returns 0).
- No existing tests broken (helpers are additive, not yet called).

## Implementation step list
| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Implement `delete_symbols_for_file` | `build_ast_graph.py` | Test passes on bank-chat fixture |
| 2 | Implement edge delete helpers (extends/implements/injects/calls) | `build_ast_graph.py` | Tests pass |
| 3 | Implement route/client/producer delete helpers | `build_ast_graph.py` | Tests pass |
| 4 | Implement http_calls/async_calls/overrides delete helpers | `build_ast_graph.py` | Tests pass |
| 5 | Implement `delete_all_for_file` composite | `build_ast_graph.py` | Test passes |
| 6 | Write all tests in `test_symmetric_delete.py` | `tests/test_symmetric_delete.py` | All 14 tests pass |
| 7 | Run full test suite + ruff | all | Green |

---

# PR-T3 — Incremental orchestrator

## File-by-file changes

### 1. `build_ast_graph.py`
- Add `expand_to_closure(changed_paths, deps_index) -> set[str]` — implements
  the 8 closure rules from proposal §2.3 using inverted `.deps.json` maps.
- Add per-pass subset functions (or add `dirty: set[str]` filter parameter
  to existing passes). All subset functions forward `verbose` and other
  kwargs matching the parent pass signature:
  - `pass1_parse_subset(root, dirty, *, verbose) -> dict[str, JavaFileAst]`
    — re-parse only dirty files.
  - `pass2_edges_subset(tables, asts, dirty, *, verbose)` — re-emit
    EXTENDS/IMPLEMENTS/INJECTS edges for symbols touching dirty files.
  - `pass3_calls_subset(tables, asts, dirty, *, verbose)` — re-emit CALLS +
    UnresolvedCallSite for dirty caller files.
  - `pass4_routes_subset(tables, asts, dirty, *, source_root, verbose)` —
    re-emit Route/EXPOSES for dirty files. `source_root` required (loads
    brownfield overrides and config).
  - `pass5_imperative_edges_subset(tables, dirty, *, source_root, verbose)`
    — re-emit Client/Producer/HTTP_CALLS/ASYNC_CALLS for dirty files.
    Note: no `asts` parameter — `pass5` does `del asts` and works from
    `tables.members`.
- Add incremental write helpers (no `_drop_all`; append to existing DB):
  - `_write_nodes_incremental(conn, tables)` — COPY-from-CSV append for
    new Symbol/UnresolvedCallSite/Route/Client/Producer nodes.
  - `_write_edges_incremental(conn, tables)` — COPY-from-CSV append for
    new DECLARES/EXTENDS/IMPLEMENTS/INJECTS/CALLS/OVERRIDES/UNRESOLVED_AT
    edges.
  - `_write_routes_and_exposes_incremental(conn, tables)` — COPY-from-CSV
    append for Route/EXPOSES/Client/DECLARES_CLIENT/Producer/
    DECLARES_PRODUCER/HTTP_CALLS/ASYNC_CALLS.
- Add `build_ast_graph_incremental(source_root, kuzu_path, changed_paths, *,
  verbose)` — the main incremental orchestrator:
  1. Read `.deps.json`; return `None` if missing/stale (caller falls back).
  2. Compute `dirty = expand_to_closure(changed_paths, deps_index)`.
  3. Open Kuzu connection; begin transaction.
  4. Call `delete_all_for_file(conn, path)` for each dirty file.
  5. Run pass1–5 subset functions, accumulating into a fresh `GraphTables`.
  6. Run `_populate_overrides_rows` on the partial tables.
  7. Run `pass6_match_edges(tables, verbose=verbose)` globally (reads existing
     edges + new). Must pass `verbose` kwarg.
  8. Write incremental rows to DB.
  9. Rewrite `_write_meta` — **query live Kuzu DB for global stats** (the
     partial `GraphTables` only holds dirty-file data; aggregation counts
     like `routes_total`, `calls_total` must be computed via COUNT Cypher
     queries against the full DB). Also set `last_rebuild_mode="incremental"`.
  10. Rewrite `.deps.json` (merge dirty entries into existing index —
     unchanged file entries preserved, dirty file entries replaced).
  11. Commit transaction; on exception, ROLLBACK and re-raise.
- Add `--changed-paths` argument to `main()` — accepts a file path containing
  newline-separated paths. When present, calls
  `build_ast_graph_incremental` instead of full rebuild. When incremental
  returns `None` or raises, falls back to full rebuild with a logged reason.
- Heuristic: if `len(dirty) > 0.5 * total_files` in `.deps.json`, skip
  incremental and fall back to full (proposal §8, risk #6).

### 2. `tests/test_incremental_equivalence.py` (extend)
- `test_incremental_matches_full_bank_chat_system` — apply incremental
  rebuild on bank-chat-system with one file changed; compare node IDs and
  edge rows against a full rebuild on the same final state.
- `test_incremental_matches_full_cross_service_smoke` — same on
  cross_service_smoke fixture.
- `test_incremental_matches_full_call_graph_smoke` — same on call_graph_smoke.
- `test_incremental_matches_full_http_caller_smoke` — same on http_caller_smoke.
- `test_incremental_matches_full_route_extraction_smoke` — same on
  route_extraction_smoke.
- `test_incremental_multiple_files_changed` — change 2-3 files; verify
  equivalence.
- `test_incremental_fallback_on_missing_deps_json` — remove `.deps.json`,
  verify incremental returns None / falls back.
- `test_incremental_fallback_on_stale_ontology` — write wrong ontology
  version; verify fallback.
- `test_incremental_fallback_on_large_dirty_set` — mark >50% files dirty;
  verify fallback to full.
- `test_closure_includes_inverse_injects` — edit a file with an injected
  symbol; verify the injector's file is in the closure.
- `test_closure_includes_inverse_extends` — edit a supertype; verify subtype
  file is in closure.
- `test_closure_includes_inverse_calls` — edit a callee; verify caller file
  is in closure.
- `test_closure_includes_inverse_overrides` — edit a supertype method;
  verify subtype file with OVERRIDES edge is in closure.
- `test_closure_includes_meta_annotation` — edit an `@interface` used as
  a meta-annotation; verify files using that annotation are in closure.
- `test_closure_includes_route_resolution` — edit a file with
  `@RequestMapping` class-level hints; verify files with methods on that
  class are in closure.
- `test_incremental_transaction_rollback` — inject a failure mid-incremental
  (e.g. raise after delete but before write); verify the DB is unchanged
  (all deleted nodes restored / no partial state).
- `test_incremental_deps_json_merge` — after incremental rebuild, verify
  `.deps.json` has updated entries for dirty files and unchanged entries
  for non-dirty files.
- `test_incremental_pass6_global_invariant` — verify the incremental path
  does NOT skip pass6, and that pass6 match outcomes are identical to a
  full rebuild's pass6 outcomes.
- `test_changed_paths_cli_flag_valid` — pass `--changed-paths` with a valid
  temp file containing known paths; verify incremental dispatch.
- `test_changed_paths_cli_flag_empty` — pass `--changed-paths` with an
  empty file; verify fallback to full rebuild.
- `test_incremental_meta_global_stats` — after incremental rebuild, verify
  `graph_meta` counts (routes_total, calls_total, etc.) match a full
  rebuild's meta. Validates the live-DB query approach.

## Tests for PR-T3
1. `test_incremental_matches_full_bank_chat_system`
2. `test_incremental_matches_full_cross_service_smoke`
3. `test_incremental_matches_full_call_graph_smoke`
4. `test_incremental_matches_full_http_caller_smoke`
5. `test_incremental_matches_full_route_extraction_smoke`
6. `test_incremental_multiple_files_changed`
7. `test_incremental_fallback_on_missing_deps_json`
8. `test_incremental_fallback_on_stale_ontology`
9. `test_incremental_fallback_on_large_dirty_set`
10. `test_closure_includes_inverse_injects`
11. `test_closure_includes_inverse_extends`
12. `test_closure_includes_inverse_calls`
13. `test_closure_includes_inverse_overrides`
14. `test_closure_includes_meta_annotation`
15. `test_closure_includes_route_resolution`
16. `test_incremental_transaction_rollback`
17. `test_incremental_deps_json_merge`
18. `test_incremental_pass6_global_invariant`
19. `test_changed_paths_cli_flag_valid`
20. `test_changed_paths_cli_flag_empty`
21. `test_incremental_meta_global_stats`

## Definition of done (PR-T3)
- Incremental rebuild produces bit-for-bit identical graph state to full
  rebuild for all fixtures tested.
- `_write_meta` in incremental mode queries live Kuzu DB for global stats
  (not partial accumulator) — verified by `test_incremental_meta_global_stats`.
- `graph_meta.last_rebuild_mode` set to `"incremental"` after successful
  incremental rebuild, `"full"` after full rebuild.
- Closure expansion correctly includes all 8 rules from proposal §2.3
  (inverse-INJECTS, inverse-EXTENDS, inverse-CALLS, meta-annotation,
  brownfield-override, route-resolution, inverse-OVERRIDES, inverse-DECLARES_CLIENT/PRODUCER).
- Fallback to full rebuild triggers on: missing `.deps.json`, stale ontology,
  >50% dirty files, any exception during incremental.
- Transaction rollback verified: mid-incremental failure leaves DB unchanged.
- All existing tests pass (full-rebuild path unchanged).
- `ruff check .` clean.

## Implementation step list
| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Implement `expand_to_closure` | `build_ast_graph.py` | Closure tests pass |
| 2 | Implement `pass1_parse_subset` | `build_ast_graph.py` | Re-parses only dirty files |
| 3 | Implement `pass2_edges_subset` through `pass5_imperative_edges_subset` | `build_ast_graph.py` | Each subset pass produces correct partial GraphTables |
| 4 | Implement incremental write helpers | `build_ast_graph.py` | Nodes/edges appended without _drop_all |
| 5 | Implement `build_ast_graph_incremental` orchestrator | `build_ast_graph.py` | Transaction-wrapped; fallback on failure |
| 6 | Add `--changed-paths` to `main()` | `build_ast_graph.py` | CLI flag dispatches to incremental |
| 7 | Write equivalence tests for all fixtures | `tests/test_incremental_equivalence.py` | Incremental matches full for each fixture |
| 8 | Write closure and fallback tests | `tests/test_incremental_equivalence.py` | All pass |
| 9 | Run full test suite + ruff | all | Green |

---

# PR-T4 — CLI + decision engine integration

## File-by-file changes

### 1. `refresh_decision.py` (new, top-level module)
- `@dataclass ChangeSet` — `added`, `modified`, `deleted`, `renamed`,
  boolean risk flags (`config_changed`, `pipeline_changed`,
  `meta_annotation_changed`).
- `@dataclass RefreshDecision` — `lance_mode: Literal["incremental", "full"]`,
  `kuzu_mode: Literal["incremental", "full"]`, `reasons: list[str]`,
  `detected_changes: ChangeSet`. The `"auto"` mode from
  INDEX-AUTO-MODE-PROPOSE is resolved to concrete `incremental`/`full`
  by `_choose_refresh_mode` before returning — callers never see `"auto"`.
- `_detect_repo_changes(source_root, git_ref_base, changed_paths) -> ChangeSet`
  — git diff or hash-based change detection.
- `_choose_refresh_mode(changes: ChangeSet, deps_path: Path, total_files: int) -> RefreshDecision`
  — implements the decision rules from `INDEX-AUTO-MODE-PROPOSE.md`:
  - Full Kuzu when: deletes, renames, config changes, pipeline changes,
    meta-annotation changes, missing/stale `.deps.json`, >50% dirty,
    detection failure.
  - Full Lance when: config changes, pipeline changes, CocoIndex flow changes.
  - Incremental otherwise.

### 2. `java_codebase_rag/cli.py`
- Remove `_emit_increment_kuzu_warning()` (line 181) and
  `_INCREMENT_WARNING_LINES` (lines 29-42).
- In `_cmd_increment`: after CocoIndex update succeeds, call
  `_choose_refresh_mode` and dispatch:
  - If `kuzu_mode == "incremental"`: write changed paths to a temp file,
    call `run_build_ast_graph_incremental` which passes
    `--changed-paths <temp-file>` to `build_ast_graph.py`.
  - If `kuzu_mode == "full"`: call `run_build_ast_graph` without
    `--changed-paths` (full rebuild).
- Update success message to include effective mode and reasons.

### 3. `java_codebase_rag/pipeline.py`
- Add `run_build_ast_graph_incremental(source_root, kuzu_path, changed_paths, *,
  verbose, quiet, env)` wrapper — writes `changed_paths` to a temp file,
  then calls `build_ast_graph.py --source-root ... --kuzu-path ...
  --changed-paths <temp-file>`. The temp file contains newline-separated
  paths, matching `build_ast_graph.py`'s `--changed-paths` contract.

### 4. `server.py`
- **Create** `refresh_code_index` MCP tool (does not exist yet). Accepts
  optional inputs: `confirm: bool`, `mode: "auto" | "incremental" | "full"`,
  `changed_paths: list[str] | null`, `git_ref_base: str`, `reason: str | null`.
- Dispatches to incremental or full Kuzu rebuild based on `RefreshDecision`.
- Includes `effective_mode`, `decision_reasons`, `detected_changes` in
  response payload.
- Backward compatible: calls passing only `confirm=true` still work
  (mode defaults to `"auto"`).

### 5. `tests/test_refresh_decision.py` (new)
- `test_auto_modified_only_incremental` — modified-only changes → incremental.
- `test_auto_deleted_file_full_kuzu` — deletion → full Kuzu, incremental Lance.
- `test_auto_renamed_file_full_kuzu` — rename → full Kuzu.
- `test_auto_config_change_full` — `.java-codebase-rag.yml` change → full.
- `test_auto_detection_failure_full` — no git, no paths → full.
- `test_explicit_full_overrides` — `mode=full` → full regardless.
- `test_deps_missing_full_kuzu` — no `.deps.json` → full Kuzu.
- `test_deps_stale_ontology_full_kuzu` — wrong version → full Kuzu.
- `test_backward_compat_confirm_only` — `confirm=true` only → auto mode.

### 6. `tests/test_cli_increment.py` (new or extend existing CLI tests)
- `test_increment_dispatches_kuzu_incremental` — mock pipeline, verify
  `--changed-paths` passed.
- `test_increment_dispatches_kuzu_full_fallback` — mock pipeline, verify
  full rebuild on fallback.
- `test_increment_removes_kuzu_warning` — verify no warning on stderr.

## Tests for PR-T4
1. `test_auto_modified_only_incremental`
2. `test_auto_deleted_file_full_kuzu`
3. `test_auto_renamed_file_full_kuzu`
4. `test_auto_config_change_full`
5. `test_auto_detection_failure_full`
6. `test_explicit_full_overrides`
7. `test_deps_missing_full_kuzu`
8. `test_deps_stale_ontology_full_kuzu`
9. `test_backward_compat_confirm_only`
10. `test_increment_dispatches_kuzu_incremental`
11. `test_increment_dispatches_kuzu_full_fallback`
12. `test_increment_removes_kuzu_warning`

## Definition of done (PR-T4)
- `java-codebase-rag increment` updates both Lance and Kuzu incrementally
  when safe.
- Decision engine isolated in `refresh_decision.py` with full test coverage.
- `_emit_increment_kuzu_warning()` removed from `java_codebase_rag/cli.py`.
- `refresh_code_index` MCP tool created in `server.py`, backward compatible.
- All existing tests pass.
- `ruff check .` clean.

## Implementation step list
| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Implement `ChangeSet` + `RefreshDecision` dataclasses | `refresh_decision.py` | Types defined |
| 2 | Implement `_detect_repo_changes` | `refresh_decision.py` | Git diff + hash fallback work |
| 3 | Implement `_choose_refresh_mode` | `refresh_decision.py` | All 9 decision tests pass |
| 4 | Add `run_build_ast_graph_incremental` to pipeline | `java_codebase_rag/pipeline.py` | Wrapper writes temp file, dispatches `--changed-paths` |
| 5 | Update `_cmd_increment` in CLI | `java_codebase_rag/cli.py` | Dispatches incremental or full based on decision |
| 6 | Remove `_emit_increment_kuzu_warning` + `_INCREMENT_WARNING_LINES` | `java_codebase_rag/cli.py` | No warning emitted |
| 7 | Create `refresh_code_index` MCP tool | `server.py` | New tool with `mode`/`changed_paths`/`git_ref_base` inputs |
| 8 | Write decision engine tests | `tests/test_refresh_decision.py` | All pass |
| 9 | Write CLI integration tests | `tests/test_cli_increment.py` | All pass |
| 10 | Run full test suite + ruff | all | Green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Silent divergence between full and incremental rebuild | **High** | `test_incremental_matches_full_*` on every fixture — the single most important test in this feature |
| 2 | Closure rules miss an edge type | **High** | Determinism test surfaces this immediately; closure rules enumerated explicitly in `expand_to_closure` |
| 3 | `.deps.json` becomes stale between runs | Medium | `ontology_version` field check; mismatch → full rebuild |
| 4 | Delete helper Cypher drifts from schema | Medium | PR-T2 tests each helper against live Kuzu; any schema change must update helpers |
| 5 | Decision engine too conservative (always falls back to full) | Medium | Track fallback rate via `graph_meta.last_rebuild_mode` (added in PR-T3); review post-launch |
| 6 | Kuzu transaction semantics don't support incremental writes | Medium | Validate early in PR-T3: test that COPY-from-CSV within a transaction works without `_drop_all`; `test_incremental_transaction_rollback` proves rollback semantics |
| 7 | Incremental slower than full for small repos | Low | >50% dirty-set heuristic; benchmark on fixtures in PR-T1 |

# Out of scope

- Watch-mode (filesystem watcher). Foundation laid; future proposal.
- Multi-tenant/concurrent rebuilds. Single writer assumed.
- LanceDB incremental rebuild (CocoIndex handles this natively).
- Cross-repo or cross-source-root incremental.
- Schema migrations between ontology versions (full rebuild required).
- PR-T5 brownfield closure refinement (optional follow-up).
- New MCP tools or CLI commands.
- Performance optimisation of pass6 (always global is the contract).

# Whole-plan done definition

1. `java-codebase-rag increment` updates both Lance and Kuzu without warnings.
2. Incremental rebuild produces identical graph state to full rebuild on all
   tested fixtures.
3. Decision engine falls back to full rebuild on all documented risk triggers.
4. All existing tests pass; no regression.
5. `.deps.json` written on full rebuild, read on incremental, validated on
   every run.
6. `ruff check .` clean.

# Tracking

- `PR-T1`: _done_
- `PR-T2`: _done_
- `PR-T3`: _pending_
- `PR-T4`: _pending_
- `PR-T5`: _deferred_
