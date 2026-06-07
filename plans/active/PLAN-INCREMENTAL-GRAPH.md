# Plan: Incremental Graph Rebuild

Status: **active (planning)**. This plan implements
[`propose/active/INCREMENTAL-GRAPH-PROPOSE.md`](../../propose/active/INCREMENTAL-GRAPH-PROPOSE.md)
as a multi-PR sequence. This file is plan-only and does not implement code.

Depends on: none.

## Goal

- `increment` updates both Lance vectors and Kuzu graph — users no longer choose between fast-but-stale or slow-but-correct.
- Only changed files and their single-hop dependents are re-parsed and re-written to the graph (scoped pass 1–4). Passes 5–6 (client/producer extraction and cross-service matching) always run globally.
- Phantom nodes (external types outside the codebase) are never deleted by file-scoped logic.
- Crash mid-increment falls back to a full `reprocess` on the next run — no permanently corrupted state.
- Edge tables gain a `source_file STRING` column; Symbol nodes already have `filename` and are unchanged.
- `--vectors-only` flag on `increment` for users who want the old Lance-only behavior.

## Principles (do not relitigate in review)

- **Delete-then-rebuild, not upsert.** Delete nodes/edges for changed files, then re-run the pipeline on those files only. No diffing of individual edges.
- **`source_file` on edges only.** Symbol nodes already carry `filename`. Edge tables need `source_file` to enable file-scoped deletion without joining through nodes.
- **Single-hop dependent expansion.** When file X changes, also reprocess files whose nodes had edges pointing into X's nodes. Cap at 50 files; fall back to full reprocess if exceeded.
- **Pass 5–6 always global.** Client/producer extraction and cross-service matching iterate all members/routes — cheap in-memory operations that ensure consistency.
- **Crash safety via marker file.** A `.graph_increment_in_progress` marker file is written before the incremental writes begin and removed on successful completion. If found on next run, fall back to full reprocess.
- **No changes to MCP tools or query API.** This is a pipeline change only — `search`, `find`, `describe`, `neighbors`, `resolve` are untouched.
- **One-time schema migration.** Existing databases lack `source_file` on edges; a full `reprocess` (or `init`) is required once. No automatic migration.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-G1 | Hash tracker + `source_file` on edge schemas | 16 → 17 | Schema DDL vs writer drift; hash computation edge cases | unit: hash tracker + schema | — |
| PR-G2 | Incremental orchestrator: scoped pass 1–4, global pass 5–6, dependent expansion, crash safety | none | Dependent expansion correctness; phantom node preservation; crash marker lifecycle | unit: orchestrator + integration: file change scenarios | PR-G1 |
| PR-G3 | CLI integration: `increment` updates graph, `--vectors-only` flag, remove stale warning | none | CLI flag wiring; backwards compatibility; progress output | CLI integration tests | PR-G2 |

Landing order: **G1 → G2 → G3**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Hash storage format | JSON file `.graph_hashes.json` in index directory (same level as `code_graph.kuzu`) |
| Hash algorithm | SHA-256 of file content — fast, collision-resistant, no security concerns here |
| Dependent reprocessing depth | Single hop only, with configurable expansion cap (default 50 files) |
| Edge `source_file` semantics | Origin-side file only (e.g., for CALLS, the caller's file). Dependent expansion covers target-side changes |
| Fallback on corrupted state | Fall back to full `reprocess` with warning to stderr |
| Pass 5–6 skip optimization | Always run globally — cheap and ensures consistency |
| Marker file name | `.graph_increment_in_progress` in index directory |
| `source_file` value | Relative POSIX path from source root (same format as `Symbol.filename`) |
| Existing increment behavior | `increment` now updates both vectors and graph; `--vectors-only` preserves old Lance-only behavior |

---

# PR-G1 — Hash tracker + `source_file` edge schema

## File-by-file changes

### 1. `build_ast_graph.py` — edge schema DDL

Add `source_file STRING` as the first property column to every relationship table DDL:

- `_SCHEMA_EXTENDS`: add `source_file STRING` before existing columns → `"CREATE REL TABLE EXTENDS(FROM Symbol TO Symbol, source_file STRING, dst_name STRING, dst_fqn STRING, resolved BOOLEAN)"`
- `_SCHEMA_IMPLEMENTS`: add `source_file STRING` before existing columns
- `_SCHEMA_INJECTS`: add `source_file STRING` before existing columns
- `_SCHEMA_DECLARES`: add `source_file STRING` → `"CREATE REL TABLE DECLARES(FROM Symbol TO Symbol, source_file STRING)"`
- `_SCHEMA_OVERRIDES`: add `source_file STRING`
- `_SCHEMA_CALLS`: add `source_file STRING` before `call_site_line`
- `_SCHEMA_UNRESOLVED_AT`: add `source_file STRING`
- `_SCHEMA_EXPOSES`: add `source_file STRING` before `confidence`
- `_SCHEMA_DECLARES_CLIENT`: add `source_file STRING` before `confidence`
- `_SCHEMA_DECLARES_PRODUCER`: add `source_file STRING` before `confidence`
- `_SCHEMA_HTTP_CALLS`: add `source_file STRING` before `confidence`
- `_SCHEMA_ASYNC_CALLS`: add `source_file STRING` before `confidence`

**Why `source_file` as first column:** Kuzu stores rel table properties in declaration order. Putting `source_file` first makes the column offset predictable for the incremental writer without having to look up per-table schemas.

### 2. `build_ast_graph.py` — edge write queries

Every edge-write Cypher query (in `_write_edges`, `_write_routes_and_exposes`, `_write_unresolved`) must include the new `source_file` parameter. The value comes from the source node's `filename` field, which is already available in the edge row's source `MemberEntry.file_path` or `TypeIndexEntry.file_path`.

For edges where the source is a Symbol (EXTENDS, IMPLEMENTS, INJECTS, CALLS, DECLARES, OVERRIDES), pass the source node's `file_path`. For UNRESOLVED_AT, pass the caller's file. For EXPOSES, DECLARES_CLIENT, DECLARES_PRODUCER, pass the originating member's `file_path`. For HTTP_CALLS and ASYNC_CALLS, pass the Client/Producer's `filename`.

Specifically:
- In `_write_edges()`: add `source_file` parameter to each `conn.execute(_CREATE_*EDGE*, ...)` call. For `EdgeRow`/`InjectsRow`/`CallsRow`, derive from the source entry's `file_path` via `tables.members` or `tables.types` lookup by src_id.
- In `_write_routes_and_exposes()`: add `source_file` to EXPOSES, DECLARES_CLIENT, DECLARES_PRODUCER, HTTP_CALLS, ASYNC_CALLS write calls. Derive from `RouteRow.filename`, `ClientRow.filename`, `ProducerRow.filename` respectively.
- In `_write_unresolved()`: add `source_file` to UNRESOLVED_AT from the caller Symbol's filename.

### 3. `build_ast_graph.py` — GraphMeta bump

Add `source_file_schema BOOLEAN` column to `_SCHEMA_META` with value `true`. This lets the incremental orchestrator check whether the existing DB has the new schema.

### 4. `ast_java.py` — ontology version bump

Change `ONTOLOGY_VERSION = 16` to `ONTOLOGY_VERSION = 17`. This triggers a re-index requirement for existing installations.

### 5. `build_ast_graph.py` — new `FileHashTracker` class (new top-level class)

```python
class FileHashTracker:
    """Track content hashes for incremental graph rebuild."""
    def __init__(self, index_dir: Path):
        self._path = index_dir / ".graph_hashes.json"
        self._hashes: dict[str, str] = {}  # rel_path -> sha256_hex

    def load(self) -> None:
        """Load hashes from disk. No-op if file missing (first run)."""

    def save(self) -> None:
        """Persist hashes to disk atomically (write .tmp, rename)."""

    def detect_changes(self, source_root: Path, ignore: LayeredIgnore) -> tuple[set[str], set[str], set[str]]:
        """Return (added, changed, removed) sets of relative POSIX paths."""

    def update(self, rel_paths: set[str], source_root: Path) -> None:
        """Compute and store hashes for the given paths."""
```

- `detect_changes` walks the source tree (via `iter_java_source_files`), hashes each file with `hashlib.sha256`, and compares against stored hashes. Returns three sets: `added` (not in hash store), `changed` (hash differs), `removed` (in hash store but file no longer exists).
- `update` writes new hashes for the given paths.
- `save` writes atomically: dump to `.graph_hashes.json.tmp`, then `os.replace()`.
- Location: top-level in `build_ast_graph.py` (same file as the pipeline; no new module needed for a single class).

### 6. `build_ast_graph.py` — update `_drop_all`

Add `DROP TABLE IF EXISTS GraphMeta` is already there. No changes needed — `_drop_all` drops everything.

### 7. `tests/test_incremental_graph.py` (NEW FILE)

## Tests for PR-G1

1. `test_file_hash_tracker_detects_added_file` — empty hash store, one file in source → `added = {"path.java"}, changed = set(), removed = set()`
2. `test_file_hash_tracker_detects_changed_file` — stored hash differs from current → `changed` populated
3. `test_file_hash_tracker_detects_removed_file` — hash store has entry but file gone → `removed` populated
4. `test_file_hash_tracker_no_changes` — identical hashes → all three sets empty
5. `test_file_hash_tracker_save_and_load_roundtrip` — save hashes, new tracker instance loads same data
6. `test_file_hash_tracker_atomic_save` — `.graph_hashes.json.tmp` not left behind on successful save
7. `test_edge_schema_has_source_file` — build a full graph on the bank-chat fixture, query each edge table for `source_file` column existence and non-empty values
8. `test_source_file_value_matches_symbol_filename` — for edges originating from Symbol nodes, the edge's `source_file` equals the source Symbol's `filename`
9. `test_graph_meta_has_source_file_schema_flag` — GraphMeta node has `source_file_schema = true`
10. `test_ontology_version_bumped_to_17` — `ONTOLOGY_VERSION == 17`

## Definition of done (PR-G1)

- [ ] All 10 new tests pass
- [ ] Existing test suite passes (schema change is backwards-incompatible — tests that build fresh graphs will auto-adapt)
- [ ] Edge tables in a freshly built graph have `source_file` populated on every row
- [ ] `ruff check build_ast_graph.py ast_java.py tests/test_incremental_graph.py` is clean
- [ ] `GraphMeta` contains `source_file_schema = true`

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Bump `ONTOLOGY_VERSION` to 17 | `ast_java.py` | `test_ontology_version_bumped_to_17` passes |
| 2 | Add `source_file STRING` to all 12 edge DDL constants | `build_ast_graph.py` | DDL strings compile |
| 3 | Add `source_file_schema BOOLEAN` to `_SCHEMA_META` | `build_ast_graph.py` | GraphMeta has new column |
| 4 | Update all edge-write Cypher queries to pass `source_file` | `build_ast_graph.py` | Tests 7–9 pass |
| 5 | Implement `FileHashTracker` class | `build_ast_graph.py` | Tests 1–6 pass |
| 6 | Add `test_incremental_graph.py` with all 10 tests | `tests/test_incremental_graph.py` | All 10 tests pass |
| 7 | Run full validation | all | `ruff check` + `pytest tests -v` green |

---

# PR-G2 — Incremental orchestrator

## File-by-file changes

### 1. `build_ast_graph.py` — new `incremental_rebuild()` function

This is the core function. Signature:

```python
def incremental_rebuild(
    source_root: Path,
    kuzu_path: Path,
    *,
    verbose: bool,
    expansion_cap: int = 50,
) -> IncrementalResult:
```

Returns an `IncrementalResult` dataclass:

```python
@dataclass
class IncrementalResult:
    mode: str  # "incremental" | "full_fallback"
    files_changed: int
    files_added: int
    files_removed: int
    dependents_reprocessed: int
    elapsed_sec: float
```

**Algorithm:**

1. **Load existing graph and detect changes.**
   - Open existing Kuzu database (read-only).
   - Load `FileHashTracker` from index dir.
   - Call `detect_changes(source_root, ignore)` to get `(added, changed, removed)`.
   - If the total set `changed_files = added | changed | removed` is empty, return immediately (no-op).
   - Check that `GraphMeta.source_file_schema == true`; if not, fall back to full rebuild.

2. **Crash marker.**
   - Write `.graph_increment_in_progress` marker file.
   - If marker already exists on entry, fall back to full rebuild (call `main()` logic directly) and remove the marker on success.

3. **Dependent expansion.**
   - Query the existing graph for nodes whose `filename` is in `changed_files`. Collect their node IDs.
   - For each changed file's node IDs, query all incoming edges (any edge type) to find source nodes in *other* files. Those files are single-hop dependents.
   - Union `changed_files` with dependent files. If the union size > `expansion_cap`, fall back to full rebuild with a warning: `"[increment] dependent expansion cap ({expansion_cap}) exceeded ({len(union)} files); falling back to full reprocess"`.
   - The final set is `scope_files`.

4. **Scoped deletion.**
   - For each file in `scope_files`:
     - Collect all Symbol node IDs where `filename = <file>` (primary key lookup, efficient).
     - For each edge table, delete rows where `source_file = <file>`.
     - Delete UnresolvedCallSite nodes whose `caller_id` is one of the collected node IDs.
     - Delete the Symbol nodes themselves.
     - **Skip phantom nodes** — phantoms have `filename = ""` so they are never matched by file-scoped deletion.
   - For `removed` files: nodes and edges are deleted but no rebuild (file is gone).
   - For Route, Client, Producer nodes: delete where `filename = <file>`.

5. **Scoped pass 1–4 (rebuild).**
   - Run `pass1_parse()` but only on files in `scope_files` (not the full tree). This requires a new parameter or a filtered walk.
   - Run `pass2_edges()`, `pass3_calls()`, `pass4_routes()` on the scoped `GraphTables`.
   - Before pass 2, load existing types from Kuzu into `tables.types` so cross-file type resolution works for unchanged types. This query: `MATCH (s:Symbol) WHERE s.kind IN ['class','interface','enum','annotation','record'] RETURN s.*`.
   - Write the scoped nodes and edges to the *existing* Kuzu database (not a fresh one). Use the same write functions but without `_drop_all`/`_create_schema`.

6. **Global pass 5–6.**
   - Load *all* members from Kuzu (not just scoped) for pass 5.
   - Run `pass5_imperative_edges()` globally — iterate all members, extract clients/producers.
   - Delete all existing Client, Producer nodes and their edges (DECLARES_CLIENT, DECLARES_PRODUCER, HTTP_CALLS, ASYNC_CALLS) before rewriting.
   - Run `pass6_match_edges()` globally — match all HTTP_CALLS and ASYNC_CALLS.
   - Write Client, Producer, and cross-service edges.

7. **Update hash store and metadata.**
   - Call `tracker.update(scope_files, source_root)`.
   - Call `tracker.save()`.
   - Update `GraphMeta` node with new build time and statistics.
   - Remove crash marker.

### 2. `build_ast_graph.py` — modify `pass1_parse` for scoped mode

Add an optional `scope_files: set[str] | None = None` parameter to `pass1_parse`:

```python
def pass1_parse(root: Path, tables: GraphTables, *, verbose: bool, scope_files: set[str] | None = None) -> dict[str, JavaFileAst]:
```

- When `scope_files is None`: existing behavior (walk all files).
- When `scope_files` is provided: only parse files in the set. Skip all other files during the walk. The `asts` dict only contains scoped files.

### 3. `build_ast_graph.py` — new helper: `_load_existing_types()`

```python
def _load_existing_types(conn: kuzu.Connection, tables: GraphTables) -> None:
    """Load type entries from existing Kuzu graph into tables for cross-file resolution."""
```

Queries all Symbol nodes with `kind IN ['class','interface','enum','annotation','record']` and populates `tables.types`, `tables.by_simple_name`, `tables.by_package`. Does not populate `tables.members` (those are re-created from scoped files).

### 4. `build_ast_graph.py` — new helper: `_load_existing_members()`

```python
def _load_existing_members(conn: kuzu.Connection) -> list[MemberEntry]:
    """Load all member entries from existing Kuzu graph for global pass 5."""
```

Queries all Symbol nodes with `kind IN ['method','constructor']` and returns a list of `MemberEntry`-compatible objects. Used by the global pass 5 to iterate all members, not just scoped ones.

### 5. `build_ast_graph.py` — new helper: `_scoped_write()`

```python
def _scoped_write(conn: kuzu.Connection, tables: GraphTables, *, project_root: Path, meta_chain: dict[str, frozenset[str]] | None) -> None:
```

Like the node/edge writing portions of `write_kuzu()` but does NOT call `_drop_all` or `_create_schema`. Writes nodes and edges into the existing database. Used by the incremental rebuild.

### 6. `build_ast_graph.py` — new helper: `_find_dependents()`

```python
def _find_dependents(conn: kuzu.Connection, changed_node_ids: set[str]) -> set[str]:
    """Find files whose nodes have edges pointing into changed nodes. Returns set of filenames."""
```

For each edge table, query `MATCH (src:Symbol)-[e]->(dst:Symbol) WHERE dst.id IN $ids RETURN DISTINCT src.filename`. Collect unique filenames that are NOT in the changed files themselves. This is the single-hop dependent expansion.

### 7. `build_ast_graph.py` — new helper: `_delete_file_scope()`

```python
def _delete_file_scope(conn: kuzu.Connection, filenames: set[str]) -> None:
    """Delete all nodes and edges originating from the given files."""
```

For each filename in the set:
- Delete all edge rows where `source_file = filename` (one DELETE per edge table).
- Collect Symbol node IDs where `filename = filename`.
- Delete UnresolvedCallSite nodes whose `caller_id` is in the collected set.
- Delete Symbol nodes where `filename = filename`.
- Delete Route nodes where `filename = filename`.
- Delete Client nodes where `filename = filename`.
- Delete Producer nodes where `filename = filename`.
- Does NOT delete nodes where `filename = ""` (phantoms).

### 8. `build_ast_graph.py` — new `--incremental` CLI flag

Add `--incremental` flag to `build_ast_graph.py`'s argparse. When set, calls `incremental_rebuild()` instead of the full `main()` pipeline. When `--incremental` is used and no previous graph exists, fall back to full build with a warning.

### 9. `java_codebase_rag/pipeline.py` — add `run_incremental_graph()`

```python
def run_incremental_graph(
    *,
    source_root: Path,
    kuzu_path: Path,
    verbose: bool,
    quiet: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
```

Like `run_build_ast_graph()` but passes the `--incremental` flag to the subprocess.

### 10. `tests/test_incremental_graph.py` — extend with orchestrator tests

## Tests for PR-G2

11. `test_incremental_single_file_change` — change one .java file, run incremental, verify only that file's nodes changed
12. `test_incremental_new_file` — add a new .java file, run incremental, verify all new nodes/edges appear
13. `test_incremental_deleted_file` — remove a .java file from fixture, run incremental, verify orphaned nodes/edges cleaned up
14. `test_incremental_phantom_nodes_preserved` — run incremental after a change, verify phantom nodes (those with `filename = ""`) are untouched
15. `test_incremental_dependent_expansion` — change a base class, verify that files with EXTENDS/IMPLEMENTS edges into it are also reprocessed
16. `test_incremental_expansion_cap_fallback` — mock expansion_cap=2, change a widely-used file that has >2 dependents, verify fallback to full rebuild
17. `test_incremental_crash_marker_triggers_fallback` — leave `.graph_increment_in_progress` marker, run incremental, verify full rebuild happens
18. `test_incremental_crash_marker_removed_on_success` — run successful incremental, verify marker file is removed
19. `test_incremental_no_changes_is_noop` — run incremental with no file changes, verify graph is unchanged (same node/edge counts)
20. `test_incremental_pass5_6_always_global` — change a file unrelated to routes, verify Client/Producer/HTTP_CALLS/ASYNC_CALLS are still fully rebuilt
21. `test_load_existing_types_populates_indexes` — build full graph, then load existing types into empty GraphTables, verify types/by_simple_name/by_package populated
22. `test_find_dependents_returns_incoming_edge_sources` — seed graph with EXTENDS edge from file B to file A, change file A, verify `_find_dependents` returns file B's filename
23. `test_delete_file_scope_removes_only_matching` — delete scope for one file, verify other files' nodes/edges untouched

## Definition of done (PR-G2)

- [ ] All 13 new tests (11–23) pass
- [ ] Existing test suite passes
- [ ] `incremental_rebuild()` function produces correct graph on single-file, multi-file, and no-change scenarios
- [ ] Phantom nodes survive incremental rebuild
- [ ] Crash marker lifecycle works (creation, detection, removal)
- [ ] `ruff check` clean on all modified files

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `scope_files` parameter to `pass1_parse` | `build_ast_graph.py` | `pass1_parse` works with scoped file set |
| 2 | Implement `_load_existing_types()` | `build_ast_graph.py` | Test 21 passes |
| 3 | Implement `_load_existing_members()` | `build_ast_graph.py` | Function returns all members from existing graph |
| 4 | Implement `_find_dependents()` | `build_ast_graph.py` | Test 22 passes |
| 5 | Implement `_delete_file_scope()` | `build_ast_graph.py` | Test 23 passes |
| 6 | Implement `_scoped_write()` | `build_ast_graph.py` | Nodes/edges written to existing DB without drop |
| 7 | Implement `incremental_rebuild()` main function | `build_ast_graph.py` | Tests 11–20 pass |
| 8 | Add `--incremental` CLI flag to `build_ast_graph.py` | `build_ast_graph.py` | Subprocess invocation works |
| 9 | Add `run_incremental_graph()` to `pipeline.py` | `pipeline.py` | Subprocess wrapper passes `--incremental` |
| 10 | Write orchestrator integration tests (11–23) | `tests/test_incremental_graph.py` | All pass |
| 11 | Run full validation | all | `ruff check` + `pytest tests -v` green |

---

# PR-G3 — CLI integration

## File-by-file changes

### 1. `java_codebase_rag/cli.py` — update `_cmd_increment()`

Current behavior: runs CocoIndex update only, emits stale-graph warning.

New behavior:
1. Run CocoIndex update (unchanged).
2. On CocoIndex success, run `run_incremental_graph()` from `pipeline.py`.
3. If incremental returns `mode == "full_fallback"`, log a warning to stderr: `"[increment] fell back to full graph rebuild — this is normal after schema changes or first run"`.
4. Emit success message: `"increment completed (Lance + graph updated)"` instead of the current `"Lance only; graph may be stale"` message.
5. Remove the `_emit_increment_kuzu_warning()` call — no longer needed.

### 2. `java_codebase_rag/cli.py` — add `--vectors-only` flag to `increment` subcommand

Add `--vectors-only` flag to the `increment` subparser. When set:
- Run CocoIndex update only (existing behavior).
- Emit the old stale-graph warning.
- Do not call `run_incremental_graph()`.

This preserves the old `increment` behavior for users who want it.

### 3. `java_codebase_rag/cli.py` — update `increment` subparser help text

Change description from `"Runs cocoindex catch-up (no full reprocess). Does not rebuild Kuzu; see stderr warning."` to `"Runs cocoindex catch-up and incremental Kuzu graph update. Use --vectors-only to skip graph update."`.

### 4. `java_codebase_rag/cli.py` — update CLI help group description

The lifecycle help string for `increment` (line 630) currently says `"Pick up changes since the last index update (Lance only)."`. Change to `"Pick up changes since the last index update (Lance + graph)."`.

### 5. `java_codebase_rag/cli.py` — remove `_INCREMENT_WARNING_LINES` and `_emit_increment_kuzu_warning()`

These are no longer needed in the default `increment` path. The stale warning only applies when `--vectors-only` is used — emit a shorter, targeted warning inline.

### 6. `java_codebase_rag/cli.py` — update `_cmd_reprocess()` description/docs

No code changes needed. `reprocess` remains the full-rebuild safety net. The `--graph-only` flag already exists and works.

### 7. `README.md` — update CLI cheat sheet

Update the `increment` row description from `"CocoIndex catch-up (Lance only); Kuzu stays stale until reprocess."` to `"CocoIndex catch-up + incremental Kuzu update. --vectors-only for Lance only."`.

Update the Roadmap section: remove `"Incremental Kuzu updates (per-changed-file)."` since it's now implemented.

### 8. `docs/JAVA-CODEBASE-RAG-CLI.md` — update `increment` command documentation

Document the new behavior:
- `increment` now updates both Lance and Kuzu.
- `--vectors-only` flag description.
- First-time migration note: after upgrading, run `reprocess` once to get `source_file` on edges.

### 9. `tests/test_java_codebase_rag_cli.py` — update CLI tests

Update `test_increment_emits_kuzu_stale_warning_block` to verify the warning is no longer emitted by default (or rename/update the test). Add new tests for `--vectors-only` flag.

## Tests for PR-G3

24. `test_increment_runs_graph_update` — run `increment` on a fixture with a changed file, verify graph is updated (no stale warning)
25. `test_increment_vectors_only_skips_graph` — run `increment --vectors-only`, verify graph is NOT updated and stale warning IS emitted
26. `test_increment_first_run_falls_back_to_full` — run `increment` on a fresh index (no graph hashes), verify it falls back gracefully and produces a correct graph
27. `test_increment_cli_help_mentions_vectors_only` — `increment --help` output contains `--vectors-only`
28. `test_increment_cli_help_no_longer_says_lance_only` — `increment --help` does not say "Lance only"
29. `test_lifecycle_round_trip_init_increment_meta` — update existing `test_cli_lifecycle_round_trip_init_increment_meta_erase` to verify graph is fresh after increment (not just Lance)

## Definition of done (PR-G3)

- [ ] All 6 new/updated tests (24–29) pass
- [ ] Existing test suite passes (with updates to stale-warning tests)
- [ ] `increment` updates both Lance and graph by default
- [ ] `increment --vectors-only` preserves old Lance-only behavior
- [ ] README and CLI docs reflect the new behavior
- [ ] `ruff check` clean on all modified files

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Update `_cmd_increment()` to call `run_incremental_graph()` after CocoIndex | `cli.py` | Graph updated after increment |
| 2 | Add `--vectors-only` flag to `increment` subparser | `cli.py` | Test 27 passes |
| 3 | Remove `_INCREMENT_WARNING_LINES` and `_emit_increment_kuzu_warning()` | `cli.py` | No stale warning in default path |
| 4 | Update `increment` subparser help text | `cli.py` | Test 28 passes |
| 5 | Update `increment` group help string | `cli.py` | Help text accurate |
| 6 | Write/update CLI tests (24–29) | `tests/test_java_codebase_rag_cli.py` | All pass |
| 7 | Update README CLI cheat sheet and roadmap | `README.md` | Docs reflect new behavior |
| 8 | Update CLI docs | `docs/JAVA-CODEBASE-RAG-CLI.md` | Increment docs accurate |
| 9 | Run full validation | all | `ruff check` + `pytest tests -v` green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Schema mismatch: existing DB lacks `source_file` on edges, incremental orchestrator queries it | high | `incremental_rebuild()` checks `GraphMeta.source_file_schema` before proceeding; falls back to full rebuild if absent. PR-G1 ensures all new builds have the column. |
| 2 | Dependent expansion misses indirect callers (multi-hop) | medium | Proposal explicitly scopes to single-hop. Document as known limitation. Multi-hop is out of scope (listed in proposal). |
| 3 | Pass 2–3 type resolution fails when dependent types aren't loaded | medium | `_load_existing_types()` loads ALL types from existing graph before scoped rebuild. Cross-file resolution works against loaded types. |
| 4 | Edge `source_file` populated incorrectly during write | high | PR-G1 tests 7–9 verify `source_file` matches source Symbol's `filename` for every edge type. |
| 5 | Concurrent `increment` runs corrupt graph | medium | Crash marker file prevents concurrent runs. Marker is checked at start of `incremental_rebuild()`. |
| 6 | Hash computation differs across platforms (line endings) | low | Use `read_bytes()` (same as pass1_parse) — raw bytes, no line-ending normalization. Consistent across platforms. |

# Out of scope

- Automatic/manual trigger improvements (user must still run `increment` explicitly)
- Watch mode or file-system watcher for automatic increment
- Multi-hop dependent propagation beyond one hop
- Full `reprocess` path optimization
- Streaming/progress reporting for long increments
- MCP server tool or query API changes
- Sharing CocoIndex's change tracking (internal LMDB, no public API)
- Automatic schema migration for existing databases (user runs `reprocess` once)
- Configurable expansion cap via YAML/CLI (hard-code default 50 for now)

# Whole-plan done definition

1. All 29 named tests pass across the three PRs.
2. Existing test suite passes without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
3. `ruff check .` is clean.
4. `increment` on a changed file updates both Lance and Kuzu graph in <25% of full `reprocess` time.
5. `increment --vectors-only` preserves old Lance-only behavior.
6. `increment` on a fresh index (no prior graph) produces a correct graph.
7. Phantom nodes survive incremental rebuild.
8. Crash mid-increment triggers full `reprocess` fallback on next run.
9. README and CLI docs reflect the new `increment` behavior.

# Tracking

- `PR-G1`: _pending_
- `PR-G2`: _pending_
- `PR-G3`: _pending_
