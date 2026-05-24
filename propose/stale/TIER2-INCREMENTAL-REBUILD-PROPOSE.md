# Tier 2 — Incremental Kuzu rebuild

Status: **active — ready for planning**.
User-facing tracking for graph-side incremental work: GitHub issue **#73** (linked from the `increment` stderr warning in `java_codebase_rag/cli.py`).
Pairs with the focused MCP-tool proposal
[`propose/INDEX-AUTO-MODE-PROPOSE.md`](INDEX-AUTO-MODE-PROPOSE.md)
(decision engine for `refresh_code_index`) and supersedes its
"future Kuzu work" footnote in [`docs/PRODUCT-VISION.md`](../docs/PRODUCT-VISION.md) §99.

This is a **proposal**, not an implementable plan. After review and
scoping decisions (the §11 [TBD] list), an implementable
`plans/PLAN-TIER2-INCREMENTAL-REBUILD.md` will be derived.

## TL;DR

`build_ast_graph.py` is a full rebuild on every run: `_drop_all` plus
`pass1`–`pass6` re-parse every `.java` file in the source tree and
re-derive every node and edge. That worked through Tier 1B because
every fixture is small and the production user runs an indexing job
manually after big changes. As we move toward continuous indexing
(`refresh_code_index` with `mode=incremental`, future watch-mode, or
post-merge CI hooks), the full-rebuild cost becomes the dominant
latency. This proposal introduces a **diff-driven Kuzu rebuild path**
that updates only what's changed at file granularity, while keeping
the full-rebuild path as the safe fallback.

The diff-driven path is gated by the same decision engine that the
REFRESH proposal already specifies for LanceDB — meaning incremental
is *only* used when the decision engine decides it's safe. Full
rebuild remains the default for renames, deletes, config changes, and
ambiguous cases.

## Why now (and not earlier)

1. **Tier 1B closed the schema.** Ontology 7 (with `pass6_match_edges`)
   is the surface that downstream tooling depends on. Adding incremental
   rebuild now doesn't risk schema churn.
2. **The decision engine already exists in proposal form.** REFRESH
   proposal §"Decision Engine" specifies exactly when full vs incremental
   is safe. The current Kuzu path can't take advantage because there is
   no incremental option to dispatch to — this proposal closes that gap.
3. **Idempotency hooks are already added.** `pass6_match_edges` resets
   its match-breakdown counters at the start of every run (PR-D3 review
   obs 3, addressed in PR-E1 follow-up plan). Future passes can adopt
   the same idempotency contract trivially.
4. **Single-developer pain point.** As the user maintains real codebases
   with the AMA agent, edit-rebuild-query loops are the inner loop. A
   sub-second incremental rebuild is the difference between the agent
   feeling responsive and feeling batch-oriented.

## Goals

1. **File-level incremental rebuild** that touches only nodes/edges
   derived from changed `.java` files (and their meta-annotation /
   brownfield-overrides closure — see §3.2).
2. **Bit-for-bit equivalence** between incremental and full rebuild for
   the same final source-tree state (verified by determinism test).
3. **Safe fallback to full rebuild** in any ambiguous case, never
   silent partial state.
4. **No schema churn** — the surface (`graph_meta`, ontology 7, MCP
   tools) is identical; incremental is a build-strategy optimisation.
5. **Backwards-compatible CLI** — `python build_ast_graph.py
   --source-root … --kuzu-path …` keeps its current full-rebuild
   semantics. A new `--changed-paths …` (or equivalent) opt-in gates
   the incremental path.

## Non-goals

- Watch-mode (filesystem watcher running continuously).
  This proposal lays the foundation; a future `propose/WATCH-MODE-PROPOSE.md`
  can build on top.
- Multi-tenant/concurrent rebuilds. Single writer assumed.
- Distributed/sharded indexing. Out of scope.
- LanceDB incremental rebuild (already covered by CocoIndex's native
  incremental in the REFRESH proposal — this is Kuzu-only).
- Cross-repo or cross-source-root incremental. One source tree at a time.
- Schema migrations between ontology versions. Schema bumps still
  require a full rebuild — flagged by the decision engine.

## 1. Current state

### 1.1 The full-rebuild pipeline

```
build_ast_graph.main()
  └─ _drop_all(conn)            # 11 tables dropped
  └─ pass1_parse(root)          # parse every .java → JavaFileAst
  └─ pass2_edges                # EXTENDS/IMPLEMENTS/INJECTS edges
  └─ pass3_calls                # CALLS edges (intra-service)
  └─ pass4_routes               # Route extraction (4 strategies)
  └─ pass5_imperative_edges     # HTTP_CALLS / ASYNC_CALLS caller edges
  └─ pass6_match_edges          # 5-outcome match resolution + cross-service
  └─ _write_meta                # graph_meta JSON-blob columns
```

Every pass either holds rows in `GraphTables` or writes them after the
parse phase. Storage is single-file Kuzu DB at `--kuzu-path`. Phase 1
*always* drops and recreates.

### 1.2 What's already well-positioned for incremental

- **Pass1 is per-file** — each `JavaFileAst` is independent. Parsing is
  trivially incrementalisable.
- **Pass6 is idempotent** — match counters reset, then re-derive from
  edges in the DB. With `pass5` edges intact for unchanged files,
  `pass6` can re-run cheaply over the union of "edges from changed
  files" + "all matched edges that *might* now have new candidates".
- **Pass4's route extraction** is per-file at extraction time but
  cross-file at resolution time (3 strategies in PR-A2). Route hints
  from one file can affect another file's resolution.
- **Determinism test (`tests/test_determinism.py`)** exists and
  enforces that two runs on the same input produce identical sorted
  IDs. This is the single most important property for incremental
  rebuild — we'll extend it to `incremental(input) == full(input)`.

### 1.3 What blocks naive incremental

- **Pass2's edges (EXTENDS/IMPLEMENTS/INJECTS)** are computed across
  files. If `Foo.java` extends `Bar.java`, both are needed to emit the
  edge. Editing `Bar.java` invalidates the edge from `Foo`.
- **Pass3's CALLS edges** depend on resolution state across files
  (a method call in `A.java` may resolve to a method in `B.java`).
- **Brownfield Layer 4 + 5** (`@CodebaseClient`, `@CodebaseProducer`,
  meta-annotation chains) propagate effects: editing the meta-annotation
  hub silently changes every annotated method downstream.
- **Pass5/Pass6 cross-service matching** matches caller-side calls
  against routes from *all* services. A new route in svc-b can flip
  an unmatched HTTP_CALLS edge in svc-a to `cross_service`.

These constraints mean we need **change-set closure**, not just
file-level dirty tracking.

## 2. Design

### 2.1 Two-tier change set

The runtime computes a **change set** with two tiers:

- **Tier 1 (direct):** files in `--changed-paths` (added, modified).
- **Tier 2 (closure):** files reachable via "depends on" links from
  Tier 1 — see §3.2.

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
    delete_rows_for_files(dirty)                 # symmetric to pass1's emits
    asts = pass1_parse_subset(dirty)             # only re-parse dirty files
    pass2_edges_subset(asts, dirty)              # re-emit cross-file edges
                                                 #   for symbols touching dirty
    pass3_calls_subset(asts, dirty)
    pass4_routes_subset(asts, dirty)
    pass5_imperative_edges_subset(asts, dirty)

    # Pass6 always reruns globally — its cost is proportional to total
    # call edges, not changed files, and it computes match outcomes
    # that span services. Cheap (~ms on bank-chat-system) and worth
    # the simplicity. See §3.4.
    pass6_match_edges()

    _write_meta(conn, tables, source_root)       # always rewritten
    _write_dependency_index(conn, asts)          # NEW (see §2.4)
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
   back to full rebuild for now (see §11 [TBD-1]).
6. **Route resolution closure:** if `F` contains route declarations
   or `@RequestMapping`-class hints, every file with a method on
   that class is dirty (PR-A2's three-strategy ladder reads class-level
   `@RequestMapping` from a possibly-different file).

The `_write_dependency_index` step (§2.4) caches the inverse maps so
closure is O(|dirty| × avg_inverse_degree), not O(|dirty| × |all-files|).

### 2.4 Dependency index

A new node table `FileDeps` (or a sidecar JSON file at
`<kuzu-path>/.deps.json` — see §11 [TBD-2]) stores per-file:

```json
{
  "src/main/java/com/example/Foo.java": {
    "ext_hash":  "sha256:abc...",         // for change detection w/o git
    "declares":  ["com.example.Foo", "com.example.Foo.Bar"],
    "injects":   ["com.example.Service"],
    "extends":   ["com.example.Base"],
    "calls":     ["com.example.Service#run", ...],
    "uses_anno": ["@RestController", "@CodebaseEndpoint"]
  }
}
```

Inverse lookups are computed on demand or cached at write time —
implementation detail, not in this proposal's scope.

### 2.5 Symmetric delete

Every pass that *emits* nodes/edges keyed by source file must have a
symmetric `delete_*_for_file(path)` helper. Concretely:

| Pass | Emits | Delete helper |
|------|-------|---------------|
| 1 | `Symbol`, `DECLARES` | `delete_symbols_for_file(p)` (cascades to DECLARES) |
| 2 | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | per-edge filter on src-symbol's file |
| 3 | `CALLS` | per-edge filter |
| 4 | `Route`, `EXPOSES` | per-route filter on owner-symbol's file |
| 5 | `HTTP_CALLS`, `ASYNC_CALLS` (rows) | per-edge filter on src-symbol's file |
| 6 | (only mutates pass5 rows in place) | n/a — always reruns globally |

Each helper takes a `Path` and a `kuzu.Connection`, executes the
DELETE Cypher, and returns a count for `--verbose` logging. None of
the existing tables need new columns — the file path is reachable
via the source `Symbol`'s `path` field.

### 2.6 Change-detection sources

Three sources, in priority order:

1. **`--changed-paths` flag** (or stdin list). Caller responsibility
   to be accurate. This is what the REFRESH proposal's decision engine
   would pass.
2. **Git diff** between `--git-ref-base` and `HEAD`. Same logic as
   REFRESH proposal §"Change Detection Strategy".
3. **Hash-based detection** using `FileDeps.ext_hash`. Walk the source
   tree, compute hashes, compare against cached. Slowest of the three;
   used as last-resort fallback.

If all three fail or are ambiguous, fall back to full rebuild.

## 3. Pass-by-pass incrementalisation notes

### 3.1 Pass1 (parse)

Trivial. Re-parse only files in the dirty set. No global state.

### 3.2 Pass2 (cross-file edges)

The edge `Foo EXTENDS Bar` lives in `pass2`. If only `Foo.java`
changed, the edge needs to be re-emitted with up-to-date metadata
(`Foo` may have changed which class it extends). If only `Bar.java`
changed, the edge target is unchanged but `Bar`'s symbol ID may have
new fields — re-emit.

Concretely: pass2 re-runs over the dirty set, but only for *outgoing*
edges from symbols in dirty files. Inverse-EXTENDS lookup ensures
that when `Bar` changes, `Foo`'s edge is also refreshed — captured by
closure §2.3 rule 2.

### 3.3 Pass3 (CALLS)

Same shape as pass2. CALLS edges re-emitted for caller-symbols in
dirty files. Closure rule 3 catches inverse-CALLS.

### 3.4 Pass4 (Routes)

Two sub-passes:

- **Extraction** is per-file. Easy.
- **Resolution** (PR-A2's three-strategy ladder) reads class-level
  hints from a possibly-different file. Closure rule 6 covers this.

Phantom-route cleanup currently runs in `pass6` (see PR-D3 review).
That stays as-is — phantoms are derived from the global graph state.

### 3.5 Pass5 (HTTP_CALLS / ASYNC_CALLS caller-side)

Per-file emit. Brownfield Layer 4 (`@CodebaseClient` /
`@CodebaseProducer`) overrides — closure rule 5 currently forces full
rebuild when an override file changes; that's pessimistic but safe.
A future PR-F or similar could implement file-scoped brownfield
closure to avoid this, once the Layer-4/5 fanout rules are explicitly
documented (see [TBD-1]).

### 3.6 Pass6 (match outcomes + cross-service)

**Always reruns globally.** Three reasons:

1. Cross-service matching can flip outcomes service-wide when one
   route's path template changes. Computing the closure of "which
   pass5 rows might re-resolve" is O(|all routes|) anyway — might
   as well rerun pass6.
2. Pass6 is fast: on `bank-chat-system` (15 files / 8 routes / 7
   call edges) it runs in ~1ms. Even at 10× scale we're still under
   the human-perception threshold.
3. Pass6's idempotency contract (PR-E1 inline comment) was already
   added with this proposal in mind.

### 3.7 `_write_meta`

Always rewritten — fields like `routes_total`, `cross_service_calls_total`,
`http_calls_match_breakdown`, `async_calls_match_breakdown` are aggregations
over the global graph state. Cost is negligible.

## 4. CLI surface

```bash
# Existing — unchanged
python build_ast_graph.py --source-root <path> --kuzu-path <path> [--verbose]

# New — opt-in incremental
python build_ast_graph.py \
  --source-root <path> --kuzu-path <path> \
  --changed-paths <file-with-newline-separated-paths> \
  [--git-ref-base HEAD~1] \
  [--verbose]
```

Decision engine inside the script:

1. If `--changed-paths` is given and `<kuzu-path>` exists with a
   readable `FileDeps` index → attempt incremental.
2. If `<kuzu-path>` is empty/missing → full rebuild (no other choice).
3. If decision_engine.requires_full(changed) → full rebuild with a
   logged reason.
4. If incremental fails mid-flight → roll back transaction, log,
   fall back to full rebuild.

## 5. MCP integration

`refresh_code_index` (already in scope of the REFRESH proposal) gains
the ability to call the new incremental Kuzu path:

```python
# Pseudocode inside server.py:refresh_code_index
decision = decide_mode(changed_paths, git_ref_base)
if decision.lance_mode == "incremental":
    cocoindex_update(incremental=True, paths=decision.lance_paths)
else:
    cocoindex_update(full=True)

if decision.kuzu_mode == "incremental":
    subprocess.run([..., "--changed-paths", decision.kuzu_paths_file])
else:
    subprocess.run([...])  # full rebuild
```

Decision engine returns two independent mode choices — LanceDB and
Kuzu may incrementally update independently.

## 6. Determinism + correctness

### 6.1 Determinism test extension

Extend `tests/test_determinism.py` (or add `tests/test_incremental_equivalence.py`)
to assert:

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
    run_incremental(src, kuzu_inc := kuzu_a, changed=["svc-a/.../ClientA.java"])

    assert all_sorted_ids(kuzu_full) == all_sorted_ids(kuzu_inc)
    assert meta(kuzu_full) == meta(kuzu_inc)
```

This is the single most important test in the entire feature.
Without it, every incremental rebuild is a potential silent-divergence
risk.

### 6.2 Failure modes

- **Half-applied delete:** transaction wraps the entire incremental
  pass; on any exception, ROLLBACK and fall back to full rebuild.
- **Corrupt FileDeps index:** detected by checksum; treated as
  "incremental impossible, fall back to full".
- **Schema-version mismatch:** if `graph_meta.ontology_version` ≠
  current `ONTOLOGY_VERSION`, force full rebuild.

## 7. Rollout

1. **PR-T1 (foundation):** `FileDeps` table + `_write_dependency_index`
   helper, written but unused. Determinism test extended (full
   rebuild only, asserts FileDeps round-trip).
2. **PR-T2 (delete helpers):** symmetric `delete_*_for_file` for each
   pass. Unit-tested in isolation. Still no incremental codepath.
3. **PR-T3 (incremental orchestrator):** `build_ast_graph_incremental`
   function + `--changed-paths` flag. Pass-by-pass incremental
   implementation. `test_incremental_matches_full` lit up here — must
   pass for every fixture.
4. **PR-T4 (decision engine + CLI):** integrate the REFRESH proposal's
   decision engine; wire `refresh_code_index` to dispatch.
5. **PR-T5 (brownfield closure refinement, optional):** narrow the
   pessimistic "any brownfield-override change → full" rule once
   Layer-4/5 fanout is explicitly documented.

PR-T1 through PR-T4 are the headline. PR-T5 is an optimisation
on top.

## 8. Risk assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Silent divergence between full and incremental | **High** | `test_incremental_matches_full` mandatory per fixture; covered by §6.1 |
| Pessimistic full-fallback hides real incremental wins | Medium | Track full-fallback rate via `graph_meta.last_rebuild_mode` + reason; review monthly |
| `FileDeps` index becomes stale | Medium | Stored in same DB transaction as graph; mismatch → full rebuild |
| Decision engine bugs cause unsafe incremental | High | Default-to-full on ambiguity; conservative initial rules; expand only after burn-in |
| Pass2/Pass3 closure misses an edge type | Medium | Determinism test surfaces this immediately |
| Performance regression (incremental slower than full for small repos) | Low | Heuristic: skip incremental when dirty set is >50% of files |

## 9. Performance estimate

On a hypothetical 1000-file Java repo:

| Metric | Full rebuild | Incremental (1 file changed) |
|--------|--------------|-------------------------------|
| Pass1 parse | ~30s (1000 × 30ms) | <0.1s (1 × 30ms + 5-10 closure files) |
| Pass2-5 emit | ~20s | <0.2s |
| Pass6 match | ~1s | ~1s (always global) |
| `_write_meta` | <0.1s | <0.1s |
| **Total** | **~50s** | **~1.5s** |

These are estimates — actual numbers depend on closure breadth and
Kuzu transaction overhead. Worth establishing a baseline benchmark
in PR-T1 (run full rebuild on `bank-chat-system` 10 times, record
mean/p95) and re-running after each PR.

## 10. Open questions / explicit out-of-scope

### [TBD-1] Brownfield closure granularity

Layer-4 (`@CodebaseClient` / `@CodebaseProducer`) overrides today
operate via FQN matching. Editing the override file conceptually
invalidates every method that matches the override's FQN pattern.
The current proposal forces full rebuild in that case (closure rule
5). A more aggressive closure could parse the override and compute
exact dirty set. Defer to PR-T5 or a follow-up proposal once Layer-4
fanout rules are formalised.

### [TBD-2] FileDeps storage: in Kuzu vs sidecar JSON

Two options:

- **(a) New node table `FileDeps`** in the same Kuzu DB. Pros:
  transactional with the graph, single artifact. Cons: Cypher
  query overhead for inverse lookups; another schema column to
  evolve.
- **(b) Sidecar `<kuzu-path>/.deps.json`** file. Pros: cheap
  inverse-map computation in Python, no Cypher overhead. Cons: needs
  manual atomicity (write-temp-rename pattern), separate version
  field for migrations.

Recommendation: **(b) sidecar JSON** — the file-level dependency
index is conceptually a build cache, not graph data. Decide in
PR-T1.

### [TBD-3] Incremental for the LanceDB chunks?

Already covered by CocoIndex's native incremental support
(`cocoindex update` without `--full-reprocess`). The REFRESH
proposal handles this. Out of scope here.

### [TBD-4] Concurrent / interleaved rebuilds

Single-writer assumed. If two `build_ast_graph.py` runs race against
the same `--kuzu-path`, behaviour is undefined. Document; do not
guard against. (Standard Kuzu single-writer constraint.)

### [TBD-5] Watch-mode

Out of scope. A follow-on proposal should layer a filesystem watcher
on top of this incremental path; it's not a free add-on because the
watcher needs debouncing, batched-change semantics, and its own state
machine.

## 11. Done definition (proposal-level)

This proposal is "ready for plan derivation" when:

- [ ] All [TBD] items in §10 have either a decision or an explicit
      "deferred to PR-T5+" tag.
- [ ] §3 closure rules have been validated against a real
      multi-service fixture (`cross_service_smoke` is a candidate).
- [ ] §9 performance estimates have a measured baseline on at least
      one fixture.
- [ ] Reviewer (you, principal engineer) approves §2.5 (symmetric
      delete) as the right model — alternative is "wipe-and-rebuild
      the dirty closure", which has similar correctness but worse
      performance.
- [ ] Decision engine pseudocode in §4 + §5 is consistent with the
      REFRESH proposal.

When approved, derive `plans/PLAN-TIER2-INCREMENTAL-REBUILD.md` with
PR-T1 through PR-T5 broken out, and a per-PR Cursor task prompt set.
