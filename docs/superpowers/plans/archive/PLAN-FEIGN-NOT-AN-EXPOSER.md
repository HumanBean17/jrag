<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: @FeignClient is a caller, not an exposer

Status: **completed** — shipped (PR-F1; propose merged in [#25](https://github.com/HumanBean17/java-codebase-rag/pull/25)).
Source: `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` on master.
Companion plan: `plans/PLAN-CROSS-SERVICE-RESOLUTION-FLAG.md` (orthogonal — different pass; can ship in either order).

## TL;DR

Single PR fix for the wrong-direction `EXPOSES` edge that `@FeignClient`
methods produce today. ~150 LOC, no schema bump, no new ontology version,
no new annotation types. The fix is a 5-character conditional in the
`EXPOSES` emission loop in `build_ast_graph.py` plus a small extension
to the cross-service matcher so it can pair Feign callers with the
endpoint Route in the target microservice (which now has no
counterpart on the caller side).

## Origin

| From | Item | Severity |
|------|------|----------|
| Mid-session investigation 2026-05-05 | `BFeignClient#joinOperator` emits BOTH `EXPOSES` (wrong — Feign is caller) AND `HTTP_CALLS`, with `HTTP_CALLS` landing on consumer-side Route in svc-a (mislabeled `intra_service`) instead of svc-b's endpoint Route. | medium (correctness) |

Discovered while polishing inter-service edge resolution after PR-E3.
The annotation has `client` in its name, but the AST extractor still
emits a `RouteDecl` for it (`ast_java.py:2178` correctly sets
`kind="http_consumer"`); the bug is downstream in the EXPOSES emission
loop in `build_ast_graph.py` which ignores `kind` when deciding whether
to emit an EXPOSES edge.

## Recommended PR boundaries

Single PR, ~150 LOC. No need to split — the change is one conditional,
one matcher extension, two metadata fields, and a handful of tests.

- **PR-F1** — gate EXPOSES emission on `Route.kind != "http_consumer"`,
  extend matcher so Feign callers find the endpoint Route in the
  target microservice, surface the suppression count in
  `KuzuGraph.meta()`, and add fixture-level tests on
  `cross_service_smoke`.

§9 [TBD-6] in the propose was resolved 2026-05-05: ASYNC_CALLS does
not have the symmetric bug (audit shipped in commit `0be1142`),
so the fix stays HTTP-only.

---

## PR-F1 — Suppress wrong-direction EXPOSES on Feign clients

Touches: `build_ast_graph.py` (one conditional in the `EXPOSES` emission
loop near line 1357 + new counter on stats + matcher extension to
allow caller-side resolution of `http_consumer` routes against
the target microservice's `http_endpoint` route),
`kuzu_queries.py` (surface the new meta field),
`tests/test_ast_graph_build.py` (or a new `tests/test_feign_not_exposer.py`),
`tests/fixtures/cross_service_smoke/` (existing fixture is sufficient
for both before/after assertions).

Out of scope:
- HTTP_CLIENT role unification — separate proposal, parked.
- Multi-attribution Routes / shared-module ownership — separate
  proposal #24, parked.
- Changes to async path (`@KafkaListener` etc.) — audit confirmed
  no symmetric bug.
- New brownfield annotations.
- Schema or ontology version bump (additive change to `GraphMeta`
  is documented as nullable read in §6 of the propose).

### Background

`ast_java.py:2178` already emits `RouteDecl(kind="http_consumer",
framework="feign", ...)` for `@FeignClient` interface methods,
distinguishing them from `kind="http_endpoint"` routes emitted by
`@RestController` methods. The matcher in `_classify_match_for_call`
already only matches HTTP_CALLS against `kind == "http_endpoint"`
(`build_ast_graph.py:1623`) — that part is correct.

The bug lives in **EXPOSES** emission. The loop near
`build_ast_graph.py:1357` adds an `ExposesRow(symbol_id, route_id,
...)` for every `(member, route)` pair the Layer-merge produced,
regardless of `route.kind`. So `BFeignClient#joinOperator` lands as
"exposes consumer-side Route in svc-a" — semantically wrong. Feign
is a **caller**, not an exposer.

Empirical evidence on `cross_service_smoke` before fix (verified
2026-05-05):

```
EXPOSES edges:
  svc-a::BFeignClient#joinOperator  -> svc-a::http_consumer:/joinOperator   ❌ wrong direction
  svc-b::JoinControllerB#join       -> svc-b::http_endpoint:/joinOperator   ✅ correct

HTTP_CALLS edges:
  svc-a::SomeService#callJoin -> svc-a::http_consumer route   ❌ should be svc-b::http_endpoint
```

After fix, the wrong EXPOSES is gone, and HTTP_CALLS lands on the
endpoint Route in svc-b with `match=cross_service`.

### Failure modes the fix addresses

1. **Querying "what does svc-a expose?"** today returns Feign client
   methods as if they were endpoints. Wrong answer for any
   architecture diagram or risk analysis.
2. **HTTP_CALLS matching ambiguity** today picks the consumer-side
   Route in the same microservice as the caller (matched as
   `intra_service`) before the cross-service matcher gets a chance
   at the endpoint Route in the target microservice. The fix
   removes the `http_consumer` Route from the candidate pool, so
   the matcher correctly walks to svc-b.
3. **Cross-service edge counts are silently inflated** today by
   intra-service Feign→consumer matches, masking the true
   cross-service signal.

### Resolution

Three coordinated changes in `build_ast_graph.py`:

#### Change 1: Gate EXPOSES emission

In the EXPOSES emission loop near line 1357, add a check on
`route.kind`:

```python
ek = (member.node_id, rid)
if ek not in exposes_seen:
    route_kind = routes_by_id[rid].kind
    if route_kind == "http_consumer":
        stats.exposes_suppressed_feign += 1
        continue
    exposes_seen.add(ek)
    tables.exposes_rows.append(
        ExposesRow(
            symbol_id=member.node_id,
            route_id=rid,
            confidence=decl.confidence,
            strategy=decl.resolution_strategy,
        ),
    )
```

The Route node itself stays — it is the **caller-side declaration**
that the matcher uses to derive `OutgoingCall` records (path,
method, feign_target_name) for HTTP_CALLS resolution. We only
suppress the spurious **EXPOSES edge**.

#### Change 2: Extend cross-service matcher to resolve Feign callers

Today the matcher pairs an `OutgoingCall` against `routes_rows`
filtered to `kind == "http_endpoint"`. With the fix, the
`http_consumer` Route in svc-a no longer has a wrong EXPOSES,
but the matcher still needs to walk from the Feign caller to
svc-b's endpoint Route.

The current matcher already does this correctly when
`OutgoingCall.client_kind == "feign_method"` and
`feign_target_name` is set (`build_ast_graph.py:1696-1746` area
in pass6). Verify in tests that the existing matcher path
produces a `cross_service` HTTP_CALLS edge after the EXPOSES
suppression.

If the matcher needs adjustment (likely a small one to read
`feign_url` / `feign_name` from the caller-side Route when
`OutgoingCall.path_template_call` is empty — the propose §2
notes this as a possible delta), document the change inline
with a comment referencing this plan.

**Note.** The propose §2 estimated the matcher extension at
"~5 LOC". Verify by running the failing-before / passing-after
test in §Tests below — if the matcher already handles it, this
change is zero LOC.

#### Change 3: Surface the suppression count

Add `exposes_suppressed_feign: int = 0` to whichever stats class
covers route emission (likely `RouteEmissionStats` near pass4 —
verify exact name on master). Surface as
`pass4_exposes_suppressed_feign` in `GraphMeta` (additive, nullable
read in `KuzuGraph.meta()` for pre-fix graphs — no ontology bump
because `GraphMeta` is read-only meta, not a structural schema).

Verbose log line in pass4's stats summary mentions the count:

```
[pass4] Route extraction: routes=N, resolved_pct=...,
        exposes_suppressed_feign=K  (Feign clients, no longer
        emitted as EXPOSES; client→endpoint edges go through
        HTTP_CALLS matcher in pass6)
```

### Tests

Use the existing `tests/fixtures/cross_service_smoke/` fixture —
it already has `BFeignClient` (svc-a) calling `JoinControllerB`
(svc-b) over `/joinOperator`.

| # | Test name | Asserts |
|---|---|---|
| 1 | `test_feign_client_does_not_emit_exposes` | Build `cross_service_smoke`; query EXPOSES; `BFeignClient#joinOperator` does NOT appear as a source of any EXPOSES edge. `JoinControllerB#join` (the endpoint) DOES still appear. |
| 2 | `test_feign_caller_resolves_to_target_endpoint` | Build same fixture; query HTTP_CALLS for the upstream Feign-caller method; assert the matched Route is the `http_endpoint` Route in svc-b (not the `http_consumer` Route in svc-a) and `match == "cross_service"`. |
| 3 | `test_feign_route_node_still_present` | Even though the EXPOSES is suppressed, the `http_consumer` Route node itself remains in the graph (it is the AST-level record of the Feign declaration; downstream tools that query "all routes for svc-a" still see it). |
| 4 | `test_meta_reports_exposes_suppressed_feign_count` | Build fixture; `KuzuGraph.meta()["pass4_exposes_suppressed_feign"]` returns the integer count (1 for `cross_service_smoke`). |
| 5 | `test_meta_returns_none_for_old_graphs` | Synthesize a graph without the `GraphMeta.pass4_exposes_suppressed_feign` column → `meta()["pass4_exposes_suppressed_feign"]` returns `None` (nullable read). |
| 6 | `test_no_change_to_async_routes` | Build same fixture; assert kafka EXPOSES edges still emitted (svc-b's `OrdersListenerB#onOrder` → kafka_topic). The `kind != "http_consumer"` filter must not affect async kinds. |

### Manual evidence to capture in PR description

```bash
cd /home/user/workspace/user-rag

rm -rf /tmp/check_feign && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_feign --verbose 2>&1 | grep -E 'pass4|exposes_suppressed'

# Expected:
# [pass4] Route extraction: routes=N, ..., exposes_suppressed_feign=1

# Verify EXPOSES counts (before fix: 2; after fix: 1)
python -c "
import kuzu
db = kuzu.Database('/tmp/check_feign'); conn = kuzu.Connection(db)
r = conn.execute('MATCH (s:Symbol)-[:EXPOSES]->(r:Route) RETURN s.fqn, r.kind, r.path')
while r.has_next(): print(r.get_next())
"
# Expected after fix: only the JoinControllerB#join → http_endpoint row
```

### Migration

No data migration. Existing graphs are still readable; the
`pass4_exposes_suppressed_feign` field returns `None` until rebuild.
Document "rebuild to apply the fix" in the PR description.

### Definition of Done

- [ ] EXPOSES emission gated on `route.kind != "http_consumer"`
- [ ] `pass4_exposes_suppressed_feign` counter populated and surfaced via `KuzuGraph.meta()` (nullable read for old graphs)
- [ ] All 6 tests above pass
- [ ] Cross-service smoke fixture: 1 EXPOSES (down from 2), 1 cross-service HTTP_CALL (up from 1 intra-service)
- [ ] Verbose pass4 log line includes the suppression count
- [ ] Existing `pytest tests -q` baseline (`266 passed, 4 skipped` post-PR-E3) does not regress; new tests bring it to **~272 passed, 4 skipped**
- [ ] Existing `find_route_handlers` behavior is preserved for endpoint routes; Feign consumer routes are either covered by an explicit empty-result contract or a dedicated non-EXPOSES traversal.
- [ ] PR description includes manual evidence block from above
- [ ] No new MCP tools, no new annotation types, no schema bump

### Risk register (from propose §3)

| # | Risk | Mitigation |
|---|---|---|
| 1 | Brownfield-overridden `@CodebaseRoute` on a Feign interface that intentionally claims `kind=http_endpoint` | The brownfield Layer-C resolution already overrides `kind`; the fix gates on the **final** kind after all layers, so brownfield can rescue if needed. Test #6 covers this implicitly via async unaffected. Add a TBD test in PR-F2 if user reports the case in real code. |
| 2 | Old graphs have wrong EXPOSES and downstream queries break | "Rebuild to apply the fix" — same migration story as every other graph-shape change in this codebase. |
| 3 | Matcher extension (Change 2) turns out to be larger than 5 LOC | Verify with test #2 first; if matcher already handles it, skip the change. If real work is needed, time-box at 30 min and split to PR-F2 if larger. |

---

## Followups (non-blockers, capture in PR description as TBDs)

1. **Document Feign-as-caller in the user-facing graph schema docs.**
   Today's README still implies "all Routes have an EXPOSES." After
   PR-F1 ships, update README to clarify "endpoint Routes have
   EXPOSES; consumer (Feign) Routes do not — they participate in
   HTTP_CALLS as the caller side."
2. **HTTP_CLIENT role unification** (parked propose, not yet drafted).
3. **`cross_service_resolution` flag** — companion plan
   `plans/PLAN-CROSS-SERVICE-RESOLUTION-FLAG.md`. Orthogonal; can
   ship before or after PR-F1.

---

## References

- `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` — the merged propose
  (PR #25) that this plan implements
- `ast_java.py:2178` — the `feign_iface` branch that sets
  `kind=http_consumer` (already correct, no change needed)
- `build_ast_graph.py:1357` — the EXPOSES emission site (the
  one-line fix)
- `build_ast_graph.py:1620-1648` — `_classify_match_for_call`
  (already filters to `kind=http_endpoint`, no change needed)
- `build_ast_graph.py:1696-1746` — pass6 HTTP_CALLS resolution
  (verify Feign-caller path works without modification)
- `tests/fixtures/cross_service_smoke/` — fixture sufficient for
  all 6 tests
- `propose/SHARED-MODULE-MULTI-ATTRIBUTION-PROPOSE.md` (#24) —
  parked sibling proposal
