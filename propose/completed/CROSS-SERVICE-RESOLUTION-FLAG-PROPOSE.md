# Propose: `cross_service_resolution` config flag

**Status:** Draft (2026-05-05)
**Author:** Dmitry (with Computer)
**Companion docs:**
- `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` (PR #25) — fixes a wrong-direction edge in the auto path; **must ship before this**
- `propose/SHARED-MODULE-MULTI-ATTRIBUTION-PROPOSE.md` (PR #24) — parked
- `propose/HTTP-CLIENT-ROLE-PROPOSE.md` — parked (not yet drafted)

## TL;DR

Add a single config flag `cross_service_resolution: brownfield_only | auto`
to `.lancedb-mcp.yml`, **default `auto`** (preserves today's behaviour).
When set to `brownfield_only`, `pass6_match_edges` skips
**cross-service** matching but keeps `intra_service`, `phantom`,
`unresolved`, and `ambiguous` labelling untouched.

The motivation is **regaining control** before testing on a real
brownfield project: the existing brownfield system (Layers A/B/C
covering roles, routes, clients, async producers — verified complete
2026-05-06) already supports authoritative cross-service edges via
`@CodebaseRoute` on the consumer side + `@CodebaseClient` on the
caller side. The flag lets a user run **brownfield-first** for
maximum precision without deleting the auto path. If brownfield-only
proves too tedious on real code, flip the flag back to `auto`.

The flag is **global** (single setting for HTTP_CALLS + ASYNC_CALLS).
Per-channel granularity is explicitly deferred.

## Why this is a control issue (not a bug)

Auto cross-service resolution is working as designed on greenfield
fixtures (`cross_service_smoke`, `bank-chat-system`). But:

1. **PR #25 just discovered a wrong-direction EXPOSES bug** the auto
   path was silently producing for ~3 weeks. The bug existed in code
   that already had test coverage; the fix is small (~150 LOC) but
   the discovery shows the auto path's correctness is not yet
   battle-tested.
2. **No real-project shakedown has happened.** Every test run to
   date has been against curated fixtures. Real brownfield
   codebases will surface ambiguity and edge cases the matcher
   wasn't designed for.
3. **Brownfield infrastructure is complete.** `@CodebaseRoute`
   (consumer side) + `@CodebaseClient` with `target_service`
   (caller side) already produce `cross_service` edges
   automatically when both sides exist — the matcher in
   `pass6_match_edges` doesn't filter by `route_source_layer`,
   so layer-C routes match exactly like built-in routes.
4. **Mental model creep.** The auto path now spans PR-D3 (matcher),
   PR-E2 (3-strategy ladder), PR-E3 (intra-JVM invariant),
   `_lookup_method_candidates` (graph walk), and the `match`
   labelling enum (5 outcomes). A user who only cares about
   "annotate the legacy hot spots, get correct edges" doesn't need
   any of it.

The flag does not **delete** any of this. It lets a user **opt out**
when they want simplicity, and **opt in** when they want the
convenience.

## Goals

- **G1.** Single global flag in `.lancedb-mcp.yml`:
  `cross_service_resolution: auto | brownfield_only`. Default `auto`
  (no behaviour change unless user sets it).
- **G2.** When `brownfield_only`, `pass6_match_edges` skips
  cross-service candidate scoring; edges that would have been
  labelled `cross_service` from the auto path become `unresolved`
  instead. Edges already produced via brownfield layers
  (`route_source_layer in {layer_*}` AND
  `resolution_strategy in {codebase_route, layer_*}`) remain
  `cross_service`.
- **G3.** `intra_service` matching, `phantom` detection,
  `ambiguous` labelling, and the PR-E3 invariant guard all remain
  active in `brownfield_only` mode. They do not require microservice
  topology guessing.
- **G4.** `KuzuGraph.meta()` reports the active mode so downstream
  consumers can detect it (e.g., a query tool can warn "this graph
  was built in `brownfield_only` mode; expect higher
  `unresolved` rate").
- **G5.** Backwards compatibility: graphs built before the flag
  exists continue to load and report `cross_service_resolution`
  meta as `null` (= "unknown / built before flag").
- **G6.** Test coverage: at minimum 4 tests — auto-mode unchanged
  (regression), brownfield_only suppresses cross-service auto
  matches, brownfield_only preserves brownfield-sourced
  cross-service edges, brownfield_only preserves intra_service.

## Non-goals

- **NG1.** Per-channel flags (HTTP vs async). Single flag covers
  both. Split later if needed.
- **NG2.** Removing or refactoring the auto path. Deferred until
  after real-project test data informs the decision.
- **NG3.** Auto-migration of existing graphs. New flag means
  old graphs remain valid; the meta field is `null` for them.
- **NG4.** A CLI override (`--cross-service-resolution=auto`).
  Config-file only for v1; CLI override is a 5-line follow-up if
  needed.
- **NG5.** New brownfield annotations. The existing
  `@CodebaseRoute` + `@CodebaseClient` cover the brownfield path
  completely.

## 1. Current state (verified 2026-05-06)

### How auto cross-service matching works today

`build_ast_graph.py:1620-1648` — the `_classify_match_for_call`
helper iterates `tables.routes_rows` and matches HTTP calls by
`(method, path_regex)` and async calls by `(broker, topic)`.
It **does not filter** by `route_source_layer`. So a layer-C
`@CodebaseRoute` lands in the same candidate pool as a built-in
`@GetMapping` route.

`pass6_match_edges` (`build_ast_graph.py:1651-`) walks each
unresolved HTTP_CALL / ASYNC_CALL row, computes candidates,
and assigns `match` ∈ {`cross_service`, `intra_service`,
`ambiguous`, `phantom`, `unresolved`}.

### Brownfield coverage today (verified)

| Concern | Annotation | YAML | Coverage |
|---|---|---|---|
| Class role | `@CodebaseRole` | `role_overrides` | ✅ Layer A/B/C |
| Class capability | `@CodebaseCapability` | `capability_overrides` | ✅ Layer A/B/C |
| Route declaration (consumer side) | `@CodebaseRoute` / `@CodebaseRoutes` | `route_overrides` | ✅ Layer A/B/C |
| HTTP client target (caller side) | `@CodebaseClient` (target_service, path, method) | `http_client_overrides` | ✅ Layer A/B/C |
| Async producer target (caller side) | `@CodebaseAsyncProducer` (broker, topic) | `async_producer_overrides` | ✅ Layer A/B/C |

`HttpClientHint` (`graph_enrich.py:190`) carries
`(client_kind, target_service, path, method)`. `AsyncProducerHint`
(`graph_enrich.py:198`) carries `(client_kind, topic, broker)`.
Both feed into `OutgoingCall` records that the cross-service
matcher consumes.

**There is no missing brownfield primitive.** The flag is purely
about **whether to also run the auto matcher** when both sides
of a brownfield edge are not available.

### Concrete example

User writes in `svc-a`:

```java
@FeignClient(name = "svc-b")
public interface OrdersClient {
    @CodebaseClient(targetService = "svc-b", path = "/orders", method = "POST")
    @PostMapping("/orders")
    Order create(@RequestBody OrderRequest req);
}
```

User writes in `svc-b`:

```java
@RestController
public class OrdersController {
    @CodebaseRoute(microservice = "svc-b", path = "/orders", method = "POST")
    @PostMapping("/orders")
    public Order create(@RequestBody OrderRequest req) { ... }
}
```

In **both** modes (`auto` and `brownfield_only`), the
cross-service edge is produced — once via the brownfield layers,
once via the auto matcher (which agrees). In `auto` mode, the
auto matcher would have produced the same edge even without the
annotations. In `brownfield_only` mode, only annotated pairs
produce cross-service edges.

## 2. Design

### 2.1 Config file

`.lancedb-mcp.yml`:

```yaml
microservice_roots:
  - svc-a
  - svc-b

cross_service_resolution: brownfield_only  # auto | brownfield_only
                                            # default: auto
```

`graph_enrich._load_config_microservice_roots` is the existing
config reader — extend it (or add a sibling `_load_config_flags`)
to read this single new key. Validate to one of two literal
strings; warn-and-fall-back-to-`auto` on unknown values.

### 2.2 Threading the flag

The flag needs to reach `pass6_match_edges`. Two options:

- **Option (a) — pass via `GraphTables`**: add a single field
  `cross_service_resolution: str = "auto"` on the tables container.
  Set in `build_ast_graph.py` near `overrides = load_brownfield_overrides(...)`
  (line 1274/1409/1927). `pass6_match_edges` reads
  `tables.cross_service_resolution`.
- **Option (b) — pass as kwarg**: `pass6_match_edges(tables, *, verbose, cross_service_resolution)`.
  Slightly cleaner separation but requires touching every caller.

Recommend **Option (a)** — fewer signature changes, mirrors how
`overrides` already lives near pass-call sites. Single-line read
inside `pass6`.

### 2.3 The gate in `pass6_match_edges`

Inside `pass6_match_edges`, after computing candidates per row:

```python
match, candidates = _classify_match_for_call(call, all_routes, caller_microservice)

if (
    tables.cross_service_resolution == "brownfield_only"
    and match == "cross_service"
    and not _is_brownfield_sourced(call, candidates)
):
    match = "unresolved"
    candidates = []
```

Where `_is_brownfield_sourced(call, candidates)` returns `True`
iff:

- `call.resolution_strategy in {"layer_c_source", "layer_b_ann", "layer_b_fqn", "layer_a_meta"}`
  (the call was emitted from a brownfield client layer), **AND**
- All candidates have `route_source_layer in
  {"layer_c_source", "layer_b_ann", "layer_b_fqn", "layer_a_meta"}`
  (the matched route came from a brownfield route layer).

**Both sides must be brownfield-sourced** for the edge to count
as authoritative. A brownfield client matching an auto-extracted
route is rejected — same direction the user is pointing toward
("regain control"). In auto-mixed cases, the user can add the
counterpart annotation explicitly.

`intra_service` is **never** suppressed regardless of
`route_source_layer` — same JVM is structural, not topological.
PR-E3's invariant guard already covers correctness here.

### 2.4 Meta exposure

`KuzuGraph.meta()` already returns a dict of build-time scalars.
Add one key:

```python
{
    ...
    "cross_service_resolution": "auto" | "brownfield_only" | None,
    ...
}
```

Stored in the `GraphMeta` Kuzu node as a `STRING` column
`cross_service_resolution`. `None` for graphs built before this
flag exists (read fallback in `KuzuGraph.meta()`).

### 2.5 Logging

When `brownfield_only` is active, `pass6_match_edges` logs at
verbose-level:

```
[pass6] cross_service_resolution=brownfield_only:
  N cross_service edges from brownfield layers,
  M auto-cross-service candidates suppressed → unresolved
```

The user can use this to detect "I should annotate more places"
or "auto would have caught this; flip the flag".

## 3. Risks and mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | A brownfield-sourced call matches an auto-sourced route, user expects edge | Medium | Both sides must be brownfield-sourced. Document this explicitly. If user wants cross-layer matching, they flip to `auto`. |
| 2 | `unresolved` rate spikes silently when user flips the flag | Medium | Verbose log line in §2.5. Also expose in `KuzuGraph.meta()` so query tools can warn. |
| 3 | Default `auto` masks the new flag's value | Low | Document the flag in README's brownfield section. Add a one-line note in `KuzuGraph.meta()` docstring. |
| 4 | Test fixtures (`cross_service_smoke`, `bank-chat-system`) don't exercise `brownfield_only` mode | High | Add at least 2 fixture-level tests in `brownfield_only` mode (see §4). |
| 5 | Old graphs lack the meta field | Low | `KuzuGraph.meta()` returns `None` when missing. Documented in §2.4. |
| 6 | User confuses "unresolved" with "broken" | Medium | The verbose log distinguishes "suppressed by mode" from genuinely unresolved. Optionally add a new `match` value `suppressed_by_mode` (alternative to `unresolved`); deferred to [TBD-3]. |
| 7 | Flag interacts unexpectedly with PR #25's Feign-not-an-exposer fix | Low | #25 fix runs in `pass4_routes` (route emission), not `pass6` (matching). They're orthogonal. Test by building both fixes together. |

## 4. Verification plan

### Tests

| # | Test | Asserts |
|---|---|---|
| 1 | `test_cross_service_resolution_auto_default` | Without `cross_service_resolution` set, behaviour matches today's `cross_service_smoke` baseline (1 cross-service HTTP_CALL + 1 cross-service ASYNC_CALL) |
| 2 | `test_brownfield_only_suppresses_auto_cross_service` | `cross_service_resolution: brownfield_only` on `cross_service_smoke` (no `@CodebaseRoute` / `@CodebaseClient` annotations) → 0 `cross_service` edges; the would-be edges become `unresolved` |
| 3 | `test_brownfield_only_keeps_annotated_cross_service` | Add `@CodebaseRoute` to `JoinControllerB` and `@CodebaseClient` to `BFeignClient` in fixture → `brownfield_only` mode produces 1 `cross_service` edge |
| 4 | `test_brownfield_only_preserves_intra_service` | Build a fixture with intra-JVM CALLS in `brownfield_only` mode → all `intra_service` edges still present (PR-E3 invariant holds) |
| 5 | `test_meta_reports_cross_service_resolution` | Built-with-flag graph → `KuzuGraph.meta()["cross_service_resolution"]` returns the literal value |
| 6 | `test_meta_resolution_null_for_old_graphs` | Synthesize a graph without the `GraphMeta.cross_service_resolution` column → `KuzuGraph.meta()["cross_service_resolution"]` returns `None` |

### Manual evidence

```bash
cd /home/user/workspace/user-rag

# Auto mode (default, unchanged)
rm -rf /tmp/check_auto && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_auto --verbose 2>&1 | grep -E 'cross_service|brownfield_only'

# Expected:
#   [pass6] http_calls cross_service=1, intra_service=0, ambiguous=0, phantom=0, unresolved=0
#   [pass6] async_calls cross_service=1, ...

# Brownfield-only mode (annotate fixture or run unannotated for negative case)
echo "cross_service_resolution: brownfield_only" > tests/fixtures/cross_service_smoke/.lancedb-mcp.yml
rm -rf /tmp/check_bo && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_bo --verbose 2>&1 | grep -E 'cross_service|brownfield_only'

# Expected (without annotations):
#   [pass6] cross_service_resolution=brownfield_only: 0 brownfield, 2 auto-cross-service suppressed
```

### Determinism

Two builds in the same mode produce identical sorted `(caller, route, match)`
triples. (Existing determinism property; the flag does not introduce
non-determinism.)

## 5. Suggested PR scope

Single PR, ~150 LOC:

1. `graph_enrich.py`: add `_load_config_cross_service_resolution(project_root_str) -> str`
   reading `.lancedb-mcp.yml`. Cached per root, mirroring
   `_load_config_microservice_roots`.
2. `build_ast_graph.py`: add `cross_service_resolution: str = "auto"`
   field on `GraphTables`. Set near `overrides = load_brownfield_overrides(...)`.
3. `build_ast_graph.py`: add `_is_brownfield_sourced(call, candidates)`
   helper. Add the gate inside `pass6_match_edges` after each
   candidate-match call site (HTTP and async loops both — ~6 LOC each).
4. `build_ast_graph.py`: extend `GraphMeta` schema with
   `cross_service_resolution STRING` (nullable). Bump
   `ONTOLOGY_VERSION` 7→8.
5. `kuzu_queries.py`: extend `KuzuGraph.meta()` to read the new
   column with `None` fallback for old graphs.
6. `tests/`: 6 tests above.
7. Verbose log line per §2.5.
8. README brownfield section: document the flag.

**No** new MCP tools, no new annotation types, no new
client/route extraction logic.

## 6. Backwards compatibility

- Old `.lancedb-mcp.yml` files without the flag → behaviour unchanged
  (default `auto`).
- Old graphs built before the flag → `KuzuGraph.meta()` returns
  `None` for the field; queries that don't read it are unaffected.
- Brownfield annotations themselves (`@CodebaseRoute`, `@CodebaseClient`)
  unchanged. They still produce edges in both modes.
- No data migration required. Rebuild to apply the flag.
- Ontology bump 7→8 documented in the PR description.

## 7. Why not the alternatives

### Why not delete the auto path entirely (the "rollback" option)?

- Loses ~3 weeks of working infrastructure (PR-D3, PR-E2, PR-E3).
- Greenfield codebases would require annotation burden they don't
  need today.
- The decision should be data-driven (after real-project test),
  not preemptive.
- Reversible: if `brownfield_only` is what the user always uses,
  a v2 cleanup PR can delete the auto path. The flag preserves
  optionality.

### Why not per-channel flags (HTTP vs async)?

- Async path is structurally cleaner (verified in PR #25 §8 audit:
  no symmetric Feign-style bug). Per-channel is overengineered for
  v1.
- Trivial to split later (one literal becomes two).
- Single flag is one config knob; two flags is a matrix.

### Why not a CLI override?

- 95% of users will set this once per project, in `.lancedb-mcp.yml`.
- A CLI override is a 5-line follow-up if needed (read `argv` first,
  fall back to YAML).
- Keeping config in one place avoids "which mode am I in?" confusion.

### Why both sides must be brownfield-sourced?

- A brownfield client matching an auto-extracted route would still
  rely on the auto path (path-regex matching, microservice topology
  inference). The whole point of `brownfield_only` is to **not**
  trust the auto path.
- Forces the user to explicitly annotate both sides if they want
  the edge — matches the "regain control" intent.
- If this proves too strict in practice, [TBD-2] flags it for v2
  relaxation.

### Why not a new `match` value `suppressed_by_mode`?

- Adds enum surface that downstream queries must handle.
- Same effect achievable via `match=unresolved` + verbose log
  (the user knows the mode from `meta()`).
- Deferred as [TBD-3] — easy to add later if multiple consumers
  ask for it.

## 8. Future work (out of scope)

- **CLI override** for the flag — 5-line follow-up.
- **Per-channel granularity** — `cross_service_http_resolution`
  + `cross_service_async_resolution` if real use surfaces the need.
- **Mixed mode** — allow brownfield client + auto route (or vice
  versa). Currently rejected; revisit if real-project testing
  shows this is the common case.
- **`suppressed_by_mode` match value** — distinguish mode-suppressed
  edges from genuinely unresolved ones in the graph itself.
- **Auto-suggest annotations** — when `brownfield_only` suppresses
  an edge that the auto path would have caught, log a hint:
  "would have matched svc-b's `POST /orders`; consider
  `@CodebaseClient(targetService='svc-b', path='/orders', method='POST')`".

## 9. [TBD]

| # | Decision | Notes |
|---|----------|-------|
| 1 | YAML key naming: `cross_service_resolution` vs `cross_service_matching` vs `cross_service_mode` | Recommend `cross_service_resolution` — matches existing terminology in `_classify_match_for_call` and the matcher module. |
| 2 | Should brownfield client + auto route still produce `cross_service` edge? | Recommend NO for v1 (matches "regain control" intent). Revisit after real-project test if user finds this too strict. |
| 3 | Should suppressed edges use `unresolved` or a new `suppressed_by_mode` match value? | Recommend `unresolved` for v1 (smaller enum surface). Add new value if downstream queries need to distinguish. |
| 4 | Verbose log threshold: log every suppressed edge or just the count? | Recommend count + first 5 examples. Avoids log spam on large graphs. |
| 5 | Should `KuzuGraph.meta()` return `None` or `"auto"` (the default) for old graphs? | Recommend `None` to distinguish "explicitly auto" from "didn't have the flag". |
| 6 | Should we also add a `microservices_topology_known: bool` meta? | Out of scope. Today's microservice list comes from `microservice_roots` in YAML, which is separate from this flag. |

## 10. References

- `build_ast_graph.py:1620-1648` — `_classify_match_for_call`,
  the function that does cross-service candidate matching
  (does NOT filter by `route_source_layer`)
- `build_ast_graph.py:1651-` — `pass6_match_edges`, the gate
  point for the new flag
- `build_ast_graph.py:1274,1409,1927` — three call sites that
  load `BrownfieldOverrides`; sibling location for the new
  config flag read
- `graph_enrich.py:91` — `CONFIG_FILENAMES`
- `graph_enrich.py:115-166` — `_load_config_microservice_roots`,
  the pattern to mirror for `_load_config_cross_service_resolution`
- `graph_enrich.py:190-203` — `HttpClientHint` and `AsyncProducerHint`
  carrying brownfield client target info
- `graph_enrich.py:838,919,952,981,995,1033,1267,1358` — the
  `route_source_layer` and `resolution_strategy` values that
  `_is_brownfield_sourced` must check against
- `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` — sibling fix that
  must ship first; orthogonal (different pass)
- `plans/PLAN-POST-TIER1B-FOLLOWUPS.md` § PR-E3 — the intra-JVM
  invariant guard, which remains active in `brownfield_only` mode
