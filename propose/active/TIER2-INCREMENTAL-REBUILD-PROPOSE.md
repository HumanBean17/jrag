# Tier 2 — Incremental Kuzu rebuild

Status: **active — ready for planning**.
User-facing tracking: GitHub issue **#73**.
Companion proposal: [`propose/active/INDEX-AUTO-MODE-PROPOSE.md`](INDEX-AUTO-MODE-PROPOSE.md)
(decision engine for `increment`/`reprocess`).

This is a **proposal**, not an implementable plan. After review,
an implementable `plans/active/PLAN-TIER2-INCREMENTAL-REBUILD.md` will
be derived.

## TL;DR

`build_ast_graph.py` is a full rebuild on every run: `write_kuzu` calls
`_drop_all` then writes all nodes and edges from scratch. Every `.java`
file is re-parsed and every node/edge re-derived. That worked through
Tier 1B because every fixture is small and users run `reprocess` manually
after big changes. As the tool reaches more users running
`java-codebase-rag increment` frequently, the full-rebuild cost becomes
the dominant latency. This proposal introduces a **diff-driven Kuzu
rebuild path** that updates only what's changed at file granularity,
while keeping the full-rebuild path as the safe fallback.

The diff-driven path is gated by the decision engine defined in
[`INDEX-AUTO-MODE-PROPOSE.md`](INDEX-AUTO-MODE-PROPOSE.md) — meaning
incremental is *only* used when the decision engine decides it's safe.
Full rebuild remains the default for renames, deletes, config changes,
and ambiguous cases.

## Why now

1. **The schema is stable.** Ontology 16 (with Client, Producer,
   OVERRIDES, UnresolvedCallSite) is the surface that downstream
   tooling depends on. Adding incremental rebuild now doesn't risk
   schema churn.
2. **The decision engine is co-proposed.** `INDEX-AUTO-MODE-PROPOSE.md`
   specifies when full vs incremental is safe for both Lance and Kuzu.
3. **Users need it.** The `increment` command updates Lance vectors
   but prints a warning that the Kuzu graph is stale. Users running
   edit-index-query loops get divergent results.
4. **Idempotency hooks are in place.** `pass6_match_edges` resets
   its match-breakdown counters at the start of every run. Future
   passes can adopt the same contract trivially.
5. **Single-developer pain point.** Edit-rebuild-query loops are the
   inner loop. A sub-second incremental rebuild is the difference
   between the tool feeling responsive and feeling batch-oriented.

## Goals

1. **File-level incremental rebuild** that touches only nodes/edges
   derived from changed `.java` files (and their dependency closure —
   see §2.3).
2. **Bit-for-bit equivalence** between incremental and full rebuild for
   the same final source-tree state (verified by determinism test).
3. **Safe fallback to full rebuild** in any ambiguous case, never
   silent partial state.
4. **No schema churn** — the surface (`graph_meta`, ontology 16, MCP
   tools) is identical; incremental is a build-strategy optimisation.
5. **CLI integration via `increment`** — `java-codebase-rag increment`
   updates both Lance and Kuzu incrementally. `reprocess` remains the
   explicit full-rebuild command. `build_ast_graph.py` gains
   `--changed-paths` as an internal flag (not user-facing).

## Non-goals

- Watch-mode (filesystem watcher running continuously).
  This proposal lays the foundation; a future
  `propose/WATCH-MODE-PROPOSE.md` can build on top.
- Multi-tenant/concurrent rebuilds. Single writer assumed.
- Distributed/sharded indexing. Out of scope.
- LanceDB incremental rebuild (already covered by CocoIndex's native
  incremental in the `increment` command — this is Kuzu-only).
- Cross-repo or cross-source-root incremental. One source tree at a time.
- Schema migrations between ontology versions. Schema bumps still
  require a full rebuild — flagged by the decision engine.

## 1. Current state

### 1.1 The full-rebuild pipeline

```
build_ast_graph.main()
  └─ pass1_parse(root, tables)           # parse every .java → JavaFileAst
  └─ pass2_edges(tables, asts)           # EXTENDS/IMPLEMENTS/INJECTS edges
  └─ pass3_calls(tables, asts)           # CALLS + UnresolvedCallSite edges
  └─ pass4_routes(tables, asts)          # Route extraction (4 strategies)
  └─ pass5_imperative_edges(tables, asts) # Client/Producer nodes, HTTP_CALLS/ASYNC_CALLS
  └─ pass6_match_edges(tables)           # match resolution + cross-service
  └─ write_kuzu(kuzu_path, tables)       # _drop_all → _create_schema → write nodes/edges/meta
       └─ _drop_all(conn)                # all tables dropped
       └─ _create_schema(conn)           # recreate from scratch
       └─ _write_nodes(conn, tables)     # Symbol, Route, Client, Producer nodes
       └─ _populate_declares_rows        # DECLARES edges
       └─ _populate_overrides_rows       # OVERRIDES edges (subtype→supertype method)
       └─ _write_edges(conn, tables)     # EXTENDS/IMPLEMENTS/INJECTS/CALLS/OVERRIDES
       └─ _write_routes_and_exposes      # Route/EXPOSES, Client/DECLARES_CLIENT, Producer/DECLARES_PRODUCER
       └─ _write_meta(conn, tables)      # graph_meta JSON-blob columns
```

Every pass populates the in-memory `GraphTables` struct. `write_kuzu`
then drops the entire Kuzu DB and writes everything from scratch.

### 1.2 What's already well-positioned for incremental

- **Pass1 is per-file** — each `JavaFileAst` is independent. Parsing is
  trivially incrementalisable.
- **Pass6 is idempotent** — match counters reset, then re-derive from
  the edges in the DB. With pass5 edges intact for unchanged files,
  `pass6` can re-run cheaply over the union of "edges from changed
  files" + "all matched edges that *might* now have new candidates".
- **Pass4's route extraction** is per-file at extraction time but
  cross-file at resolution time (3 strategies). Route hints from one
  file can affect another file's resolution.
- **`GraphTables` is an in-memory accumulator** — passes add to it,
  `write_kuzu` reads from it. An incremental path can construct a
  partial `GraphTables` (only dirty files' data) and merge it into the
  existing DB.

### 1.3 What blocks naive incremental

- **Pass2's edges (EXTENDS/IMPLEMENTS/INJECTS)** are computed across
  files. If `Foo.java` extends `Bar.java`, both are needed to emit the
  edge. Editing `Bar.java` invalidates the edge from `Foo`.
- **Pass3's CALLS edges** depend on resolution state across files
  (a method call in `A.java` may resolve to a method in `B.java`).
  Pass3 also emits `UnresolvedCallSite` + `UNRESOLVED_AT` edges.
- **OVERRIDES edges** span files — `Foo.method()` overrides
  `Bar.method()`. Editing `Bar.java` invalidates the OVERRIDES edge.
  These are computed in `_populate_overrides_rows` during the write
  phase.
- **Brownfield Layer 4 + 5** (`@CodebaseClient`, `@CodebaseProducer`,
  meta-annotation chains) propagate effects: editing the meta-annotation
  hub silently changes every annotated method downstream.
- **Pass5 emits Client/Producer nodes** in addition to HTTP_CALLS/
  ASYNC_CALLS edges. These nodes have their own DECLARES_CLIENT/
  DECLARES_PRODUCER edges. HTTP_CALLS now goes Client→Route and
  ASYNC_CALLS goes Producer→Route (not Symbol→Route as in ontology 7).
- **Pass6 cross-service matching** matches caller-side calls against
  routes from *all* services. A new route in svc-b can flip an
  unmatched HTTP_CALLS edge in svc-a to `cross_service`.

These constraints mean we need **change-set closure**, not just
file-level dirty tracking.

## 2. Design

### 2.1 Two-tier change set

The runtime computes a **change set** with two tiers:

- **Tier 1 (direct):** files in `--changed-paths` (added, modified).
- **Tier 2 (closure):** files reachable via "depends on" links from
  Tier 1 — see §2.3.

Both tiers are re-parsed and their nodes/edges deleted from the DB
before pass1's incremental write. Files outside both tiers are left
untouched.

### 2.2 The incremental main loop (sketch)

```
build_ast_graph_incremental(changed_paths):
  if decision_engine.requires_full(changed_paths):
    return run_full_rebuild()                    # safe fallback

  # Compute change set (Tier 1 + Tier 2 closure)
  dirty = expand_to_closure(changed_paths)

  with conn:
    delete_rows_for_files(conn, dirty)           # symmetric deletes (see §2.5)
    asts = pass1_parse_subset(root, dirty)       # only re-parse dirty files
    tables = GraphTables()                        # fresh accumulator — only dirty data
    pass2_edges_subset(tables, asts, dirty)      # re-emit cross-file edges
                                                 #   for symbols touching dirty
    pass3_calls_subset(tables, asts, dirty)
    pass4_routes_subset(tables, asts, dirty)
    pass5_imperative_edges_subset(tables, asts, dirty)

    # OVERRIDES for dirty files only
    _populate_overrides_rows(tables)

    # Pass6 always reruns globally — its cost is proportional to total
    # call edges, not changed files, and it computes match outcomes
    # that span services. Cheap (~ms on bank-chat-system) and worth
    # the simplicity. See §3.6.
    pass6_match_edges(tables)

    # Write incremental rows to existing DB (no _drop_all)
    _write_nodes_incremental(conn, tables)
    _write_edges_incremental(conn, tables)
    _write_routes_and_exposes_incremental(conn, tables)
    _write_meta(conn, tables, source_root)       # always rewritten
    _write_dependency_index(deps_path, asts)     # sidecar .deps.json (see §2.4)
```

### 2.3 Change-set closure rules

For each file `F` in Tier 1, mark these files as Tier 2:

1. **Inverse-INJECTS:** every file that `INJECTS` a symbol declared in `F`.
2. **Inverse-EXTENDS / Inverse-IMPLEMENTS:** every file whose symbol
   extends or implements a symbol from `F`. (Class hierarchy
   re-resolution may change brownfield-overrides resolution.)
3. **Inverse-CALLS:** every file that calls a symbol declared in `F`.
   (CALLS resolution can flip targets when overload sets change.)
4. **Meta-annotation closure:** if `F` declares an `@interface` (a
   meta-annotation), every file that uses that annotation is dirty.
   (Brownfield Layer 5 can re-fanout.)
5. **Brownfield-override closure:** if `F` contains
   `@CodebaseClient` / `@CodebaseProducer` / `@CodebaseEndpoint` /
   role/capability override annotations, every file in the same
   FQN scope (per Layer 4 rules) is potentially affected — fall
   back to full rebuild (see TBD-1 resolution below).
6. **Route resolution closure:** if `F` contains route declarations
   or `@RequestMapping`-class hints, every file with a method on
   that class is dirty.
7. **Inverse-OVERRIDES:** if `F` declares a method that overrides a
   supertype method, editing the supertype's file invalidates the
   subtype's OVERRIDES edge — the subtype's file is dirty.
8. **Inverse-DECLARES_CLIENT / Inverse-DECLARES_PRODUCER:** if `F`
   declares a Client or Producer, and a route change in another file
   could affect the match outcome, the Client/Producer's file is
   dirty. In practice, pass6 already reruns globally and handles
   match resolution — this closure ensures the Client/Producer
   *nodes* are current when their declaring method's file changes.

The `_write_dependency_index` step (§2.4) caches the inverse maps so
closure is O(|dirty| × avg_inverse_degree), not O(|dirty| × |all-files|).

### 2.4 Dependency index

A sidecar file at `<kuzu-path>/.deps.json` stores per-file dependency
metadata. This is a build cache, not graph data — kept outside the Kuzu
DB for cheap Python-level inverse-map computation and no Cypher overhead.

```json
{
  "version": 1,
  "ontology_version": 16,
  "files": {
    "src/main/java/com/example/Foo.java": {
      "ext_hash":  "sha256:abc...",
      "declares":  ["com.example.Foo", "com.example.Foo.Bar"],
      "injects":   ["com.example.Service"],
      "extends":   ["com.example.Base"],
      "calls":     ["com.example.Service#run"],
      "uses_anno": ["@RestController", "@CodebaseEndpoint"],
      "overrides": ["com.example.Base#method()"],
      "declares_clients": ["com.example.Foo#callApi()"],
      "declares_producers": []
    }
  }
}
```

Atomicity via write-temp-rename pattern. Version field for future
migrations. `ontology_version` field to detect stale index — mismatch
triggers full rebuild.

### 2.5 Symmetric delete

Every pass that *emits* nodes/edges keyed by source file must have a
symmetric `delete_*_for_file(path)` helper. Concretely:

| Pass | Emits | Delete helper |
|------|-------|---------------|
| 1 | `Symbol`, `DECLARES` | `delete_symbols_for_file(p)` (cascades to DECLARES) |
| 2 | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | per-edge filter on src-symbol's file |
| 3 | `CALLS`, `UnresolvedCallSite`, `UNRESOLVED_AT` | per-edge/site filter on caller-symbol's file |
| 4 | `Route`, `EXPOSES` | per-route filter on owner-symbol's file |
| 5 | `Client`, `DECLARES_CLIENT`, `Producer`, `DECLARES_PRODUCER` | per-node filter on declaring-method's file |
| 5 | `HTTP_CALLS` (Client→Route) | per-edge filter on Client's declaring-method's file |
| 5 | `ASYNC_CALLS` (Producer→Route) | per-edge filter on Producer's declaring-method's file |
| overrides | `OVERRIDES` | per-edge filter on subtype-method's file |

Each helper takes a `Path` and a `kuzu.Connection`, executes the
DELETE Cypher, and returns a count for `--verbose` logging. The file
path is reachable via the source `Symbol`'s `path` field (or via
`Client.filename` / `Producer.filename` for those node types).

### 2.6 Change-detection sources

Three sources, in priority order:

1. **`--changed-paths` flag** (or stdin list). Caller responsibility
   to be accurate. This is what the INDEX-AUTO-MODE decision engine
   would pass.
2. **Git diff** between `--git-ref-base` and `HEAD`. Same logic as
   INDEX-AUTO-MODE §"Change Detection Strategy".
3. **Hash-based detection** using `.deps.json` `ext_hash` fields. Walk
   the source tree, compute hashes, compare against cached. Slowest;
   used as last-resort fallback.

If all three fail or are ambiguous, fall back to full rebuild.

## 3. Pass-by-pass incrementalisation notes

### 3.1 Pass1 (parse)

Trivial. Re-parse only files in the dirty set. No global state.

### 3.2 Pass2 (cross-file edges)

The edge `Foo EXTENDS Bar` lives in `pass2`. If only `Foo.java`
changed, the edge needs to be re-emitted with up-to-date metadata.
If only `Bar.java` changed, the edge target is unchanged but `Bar`'s
symbol ID may have new fields — re-emit.

OVERRIDES edges (computed in `_populate_overrides_rows` during write)
follow the same pattern. If `Foo.method()` overrides `Bar.method()`
and `Bar.java` changes, `Foo`'s OVERRIDES edge is stale — covered by
closure rule 7.

### 3.3 Pass3 (CALLS + UnresolvedCallSite)

Same shape as pass2. CALLS edges re-emitted for caller-symbols in
dirty files. Closure rule 3 catches inverse-CALLS. `UnresolvedCallSite`
rows and their `UNRESOLVED_AT` edges are also deleted and re-emitted
for the dirty subset.

### 3.4 Pass4 (Routes)

Two sub-passes:

- **Extraction** is per-file. Easy.
- **Resolution** (three-strategy ladder) reads class-level hints from
  a possibly-different file. Closure rule 6 covers this.

Phantom-route cleanup currently runs in `pass6`. That stays as-is —
phantoms are derived from the global graph state.

### 3.5 Pass5 (Client/Producer + HTTP_CALLS/ASYNC_CALLS)

Now emits Client and Producer *nodes* (not just edges) plus their
DECLARES_CLIENT/DECLARES_PRODUCER edges. HTTP_CALLS now originates from
Client→Route and ASYNC_CALLS from Producer→Route.

Symmetric delete must clean up all of these per-file. Brownfield
Layer 4 (`@CodebaseClient` / `@CodebaseProducer`) overrides — closure
rule 5 currently forces full rebuild when an override file changes;
pessimistic but safe. Fine-grained brownfield closure deferred.

### 3.6 Pass6 (match outcomes + cross-service)

**Always reruns globally.** Three reasons:

1. Cross-service matching can flip outcomes service-wide when one
   route's path template changes.
2. Pass6 is fast: on `bank-chat-system` it runs in ~1ms. Even at 10×
   scale we're still under the human-perception threshold.
3. Pass6's idempotency contract was already added with incremental
   in mind.

### 3.7 `_write_meta`

Always rewritten — fields like `routes_total`, `cross_service_calls_total`,
`http_calls_match_breakdown`, `async_calls_match_breakdown` are aggregations
over the global graph state. Cost is negligible.

## 4. CLI surface

The incremental path integrates with the existing `java-codebase-rag`
CLI, not as a user-facing flag on `build_ast_graph.py`:

```bash
# Existing — unchanged (full rebuild of Lance + Kuzu)
java-codebase-rag reprocess
java-codebase-rag reprocess --graph-only
java-codebase-rag reprocess --vectors-only

# Updated — now also increments Kuzu graph (not just Lance)
java-codebase-rag increment

# Internal — build_ast_graph.py gains --changed-paths (called by CLI, not users)
python build_ast_graph.py \
  --source-root <path> --kuzu-path <path> \
  --changed-paths <file-with-newline-separated-paths> \
  [--verbose]
```

Decision engine inside `cli.py`'s `_cmd_increment`:

1. If `<kuzu-path>` doesn't exist or `.deps.json` is missing/stale →
   fall back to full graph rebuild.
2. If `decision_engine.requires_full(changed)` → full graph rebuild
   with a logged reason.
3. If incremental fails mid-flight → roll back transaction, log,
   fall back to full graph rebuild.
4. On success → both Lance and Kuzu are incremental.

The `_emit_increment_kuzu_warning()` call in `cli.py` is removed once
this ships.

## 5. Determinism + correctness

### 5.1 Determinism test

Create `tests/test_incremental_equivalence.py` as a prerequisite:

```python
def test_incremental_matches_full(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    copy_fixture(src, "tests/fixtures/cross_service_smoke")

    # Full rebuild on initial state
    run_full(src, kuzu_a := tmp_path / "a")

    # Mutate one file
    (src / "svc-a/.../ClientA.java").write_text(modified_source)

    # Two paths to the same final state
    run_full(src, kuzu_full := tmp_path / "full")
    run_incremental(kuzu_a, changed=["svc-a/.../ClientA.java"])

    assert all_sorted_ids(kuzu_full) == all_sorted_ids(kuzu_a)
    assert meta(kuzu_full) == meta(kuzu_a)
```

This is the single most important test in the entire feature.
Without it, every incremental rebuild is a potential silent-divergence
risk. Run against every fixture.

### 5.2 Failure modes

- **Half-applied delete:** transaction wraps the entire incremental
  pass; on any exception, ROLLBACK and fall back to full rebuild.
- **Corrupt `.deps.json`:** detected by checksum + `ontology_version`
  field; treated as "incremental impossible, fall back to full".
- **Schema-version mismatch:** if `graph_meta.ontology_version` ≠
  current `ONTOLOGY_VERSION`, force full rebuild.

## 6. Rollout

1. **PR-T1 (foundation + determinism test):** Create
   `tests/test_incremental_equivalence.py` with full-rebuild
   determinism assertions. Add `.deps.json` writer
   (`_write_dependency_index`) and reader, written but unused.
   Establish performance baseline on fixtures.
2. **PR-T2 (delete helpers):** symmetric `delete_*_for_file` for each
   pass and node/edge type (Symbol, DECLARES, EXTENDS, IMPLEMENTS,
   INJECTS, CALLS, UnresolvedCallSite, UNRESOLVED_AT, Route, EXPOSES,
   Client, DECLARES_CLIENT, Producer, DECLARES_PRODUCER, HTTP_CALLS,
   ASYNC_CALLS, OVERRIDES). Unit-tested in isolation. Still no
   incremental codepath.
3. **PR-T3 (incremental orchestrator):** `build_ast_graph_incremental`
   function + `--changed-paths` flag on `build_ast_graph.py`. Pass-by-
   pass incremental implementation including Client/Producer/OVERRIDES/
   UnresolvedCallSite. `test_incremental_matches_full` extended to
   cover all fixtures.
4. **PR-T4 (CLI + decision engine):** Integrate INDEX-AUTO-MODE
   decision engine into `cli.py`'s `_cmd_increment`. Remove
   `_emit_increment_kuzu_warning()`. Index building is
   CLI-only — no MCP tools for index refresh.
5. **PR-T5 (brownfield closure refinement, optional):** Narrow the
   pessimistic "any brownfield-override change → full" rule once
   Layer-4/5 fanout is explicitly documented.

PR-T1 through PR-T4 are the headline. PR-T5 is an optimisation
on top.

## 7. Risk assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Silent divergence between full and incremental | **High** | `test_incremental_matches_full` mandatory per fixture; covered by §5.1 |
| Pessimistic full-fallback hides real incremental wins | Medium | Track full-fallback rate via `graph_meta.last_rebuild_mode` + reason; review monthly |
| `.deps.json` becomes stale | Medium | `ontology_version` field check; mismatch → full rebuild |
| Decision engine bugs cause unsafe incremental | High | Default-to-full on ambiguity; conservative initial rules; expand only after burn-in |
| Pass2/Pass3 closure misses an edge type | Medium | Determinism test surfaces this immediately |
| Performance regression (incremental slower than full for small repos) | Low | Heuristic: skip incremental when dirty set is >50% of files |

## 8. Performance estimate

On a hypothetical 1000-file Java repo:

| Metric | Full rebuild | Incremental (1 file changed) |
|--------|--------------|-------------------------------|
| Pass1 parse | ~30s (1000 × 30ms) | <0.1s (1 × 30ms + 5-10 closure files) |
| Pass2-5 emit | ~20s | <0.2s |
| Pass6 match | ~1s | ~1s (always global) |
| `_write_meta` | <0.1s | <0.1s |
| **Total** | **~50s** | **~1.5s** |

These are estimates — actual numbers depend on closure breadth and
Kuzu transaction overhead. PR-T1 establishes a measured baseline on
`bank-chat-system` and other fixtures.

## 9. Resolved TBDs

### TBD-1: Brownfield closure granularity — **pessimistic fallback**

Layer-4 (`@CodebaseClient` / `@CodebaseProducer`) overrides operate
via FQN matching. Editing the override file conceptually invalidates
every matching method. The current proposal forces full rebuild in
that case (closure rule 5). Fine-grained closure is deferred to PR-T5
or a follow-up proposal once Layer-4 fanout rules are formalised.

### TBD-2: FileDeps storage — **sidecar JSON**

Decision: **sidecar `<kuzu-path>/.deps.json`**. The file-level dependency
index is conceptually a build cache, not graph data. Benefits: cheap
inverse-map computation in Python, no Cypher overhead, no schema
evolution burden. Atomicity via write-temp-rename. `version` +
`ontology_version` fields for migration/staleness detection.

### TBD-3: Incremental for the LanceDB chunks — **out of scope**

Already covered by CocoIndex's native incremental support
(`java-codebase-rag increment` without `--full-reprocess`).

### TBD-4: Concurrent / interleaved rebuilds — **document only**

Single-writer assumed. If two runs race against the same `--kuzu-path`,
behaviour is undefined. Document; do not guard against.

### TBD-5: Watch-mode — **out of scope, future proposal**

A follow-on proposal should layer a filesystem watcher on top of this
incremental path. It needs debouncing, batched-change semantics, and
its own state machine.

## 10. Done definition (proposal-level)

This proposal is "ready for plan derivation" when:

- [x] All TBD items have a decision.
- [ ] §3 closure rules have been validated against a real
      multi-service fixture (`cross_service_smoke` is a candidate).
- [ ] §9 performance estimates have a measured baseline on at least
      one fixture.
- [ ] Reviewer approves §2.5 (symmetric delete) as the right model.
- [ ] Decision engine pseudocode in §4 + §5 is consistent with
      `INDEX-AUTO-MODE-PROPOSE.md`.

When approved, derive `plans/active/PLAN-TIER2-INCREMENTAL-REBUILD.md`
with PR-T1 through PR-T5 broken out, and a per-PR agent task prompt set.
