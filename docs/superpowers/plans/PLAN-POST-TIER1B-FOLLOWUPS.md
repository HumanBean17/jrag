<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Post-Tier-1B follow-ups — small cleanup PR plan

Status: **completed** — PR-E1 (items 1–6) shipped in [#19](https://github.com/HumanBean17/java-codebase-rag/pull/19); PR-E2 (item 7) shipped in [#20](https://github.com/HumanBean17/java-codebase-rag/pull/20); PR-E3 (item 8) shipped in [#22](https://github.com/HumanBean17/java-codebase-rag/pull/22).
Source: catches collected from PR-D1, PR-D2, PR-D3 reviews + the PR-E1 review reply.
None are blockers; all are either contract-tightening, naming, doc gaps,
or invariant guards.

## Origin of each item

Each row below was a non-blocking observation in a merged PR review.
Links go to the review comment so the original context survives.

| # | From PR | Item | Severity |
|---|---------|------|----------|
| 1 | [PR-D3 #15](https://github.com/HumanBean17/java-codebase-rag/pull/15#issuecomment-4379649557) obs 1 | `risk_score` upper clamp removed — contract change `[0,1]` → `[0,6+]` | medium (contract) |
| 2 | [PR-D3 #15](https://github.com/HumanBean17/java-codebase-rag/pull/15#issuecomment-4379649557) obs 2 | `VALID_HTTP_CALL_MATCHES` is misnamed — also used by async loop | low (cosmetic) |
| 3 | [PR-D3 #15](https://github.com/HumanBean17/java-codebase-rag/pull/15#issuecomment-4379649557) obs 3 | `pass6_match_edges` reset is implicit; idempotency comment for future incremental-rebuild path | low (doc) |
| 4 | [PR-D3 #15](https://github.com/HumanBean17/java-codebase-rag/pull/15#issuecomment-4379649557) obs 5 | Empty-feign-name short-circuit in `_match_call_edge` — add reader comment | low (doc) |
| 5 | [PR-D2 #13](https://github.com/HumanBean17/java-codebase-rag/pull/13#issuecomment-4378995637) post-D3 follow-up 1 | README: document `anchor`-fills-from-builtin behaviour for partial brownfield overrides | low (doc) |
| 6 | [PR-D2 #13](https://github.com/HumanBean17/java-codebase-rag/pull/13#issuecomment-4378995637) post-D3 follow-up 2 | Proposal §6: add `channel` field to the `OutgoingCallDecl` schema sketch as durable | low (doc) |
| 7 | [PR-D1 #12](https://github.com/HumanBean17/java-codebase-rag/pull/12#issuecomment-4378723605) obs 2 | Second copy of strategy ladder in `graph_enrich.py` — **✅ shipped in PR-E2** ([#20](https://github.com/HumanBean17/java-codebase-rag/pull/20); `_route_path_atom` + sentinel test) | medium (tech debt) |
| 8 | [PR-E1 #19 review reply](https://github.com/HumanBean17/java-codebase-rag/pull/19#issuecomment-4380659734) | `pass3_calls` doesn't enforce the intra-JVM invariant for `CALLS` edges. Today no cross-microservice CALLS edge is emitted on any fixture (verified on `cross_service_smoke`: 9 CALLS edges, 0 cross), but the cleanliness is incidental — no `caller.microservice == callee.microservice` guard exists. FQN collisions across services or brownfield supertype overrides could in principle break the invariant silently. **✅ shipped in PR-E3** | low-to-medium (invariant) |

## Recommended PR boundaries

- **PR-E1** (✅ shipped in [#19](https://github.com/HumanBean17/java-codebase-rag/pull/19)) — items 1–6 in one PR (risk-score normalisation + the four small renames/comments + two doc fixes).
- **PR-E2** (✅ shipped in [#20](https://github.com/HumanBean17/java-codebase-rag/pull/20)) — item 7 (`_route_path_atom` + sentinel test in `tests/test_call_edge_matching.py`).
- **PR-E3** (✅ shipped in [#22](https://github.com/HumanBean17/java-codebase-rag/pull/22)) — item 8 (intra-JVM CALLS invariant guard).

This document covers all three. Implementation guidance below.

---

## PR-E1 — Risk-score contract + naming + doc fixes

Touches: `pr_analysis.py`, `java_ontology.py`, `build_ast_graph.py`, `server.py`,
`README.md`, `propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`.

Out of scope (deferred to PR-E2): consolidating the second strategy ladder in `graph_enrich.py`.

### 1. Risk-score `[0, 1]` re-normalisation (item 1, medium)

**Problem.** PR-D3 turned `pr_analysis.py`'s risk-score formula from
`max(0.0, min(1.0, raw))` into `max(0.0, raw + cross_service_bonus)` — silently
breaking the `[0, 1]` published contract. Downstream consumers (`risk_band`
mapping, dashboards, MCP DTO docs) all assumed `[0, 1]`.

**Resolution — pick one of two options:**

- **(a) Re-normalise into `[0, 1]` after summing.** Replace
  `score = max(0.0, raw + cross_service_bonus)` with
  `score = max(0.0, min(1.0, raw + cross_service_bonus / 5.0))` (the `/ 5.0`
  keeps the bump's relative weight — saturating at the +5.0 cap). This
  preserves the public contract.
- **(b) Accept the new range and document it.** Update the `analyze_pr`
  docstring and `pr_analysis.py:RiskRecord` field comment to say
  `risk_score ∈ [0, 6]` (or whatever the documented upper bound is), and
  extend the `risk_band` thresholds so the new range maps cleanly. More
  honest about how cross-service risk dominates intra-service risk, but
  breaks every existing dashboard.

**Recommendation: option (a).** The bump is *signal*, not *score* —
it says "this method is risky because cross-service callers are exposed",
which is exactly what the relative-weight normalisation captures.
Option (b) is appropriate later if/when we want a multi-axis risk vector
(intra-service vs cross-service vs blast-radius) instead of a single scalar.

**Tests.** One unit test: simulate a method with raw=0.9 and 6 cross-service
callers; assert `risk_score == 1.0` (saturated) under (a). Another with
raw=0.4 and 1 cross-service caller; assert `risk_score ≈ 0.4 + 0.2 = 0.6`
(`+1.0 / 5.0`).

### 2. Rename `VALID_HTTP_CALL_MATCHES` → `VALID_CALL_MATCHES` (item 2, low)

**Problem.** `java_ontology.py:69` defines `VALID_HTTP_CALL_MATCHES`
but `build_ast_graph.py:1713` reuses it for the async loop — the name lies.

**Resolution.** Rename to `VALID_CALL_MATCHES`, keep the old name as a
deprecation alias (`VALID_HTTP_CALL_MATCHES = VALID_CALL_MATCHES`) for one
release so external tools that import it don't break. Remove alias in PR-E2 or later.

**Tests.** No new tests; existing tests pass under rename.

### 3. Idempotency comment in `pass6_match_edges` (item 3, low)

**Problem.** `build_ast_graph.py:1643-1645` clears
`http_calls_match_breakdown`, `async_calls_match_breakdown`, and resets
`cross_service_calls_total = 0` before the per-row loops. Today this is
fine (Phase 1 is always a full rebuild), but if/when incremental rebuild
arrives this reset will need to remain idempotent — and a future contributor
will need to know that.

**Resolution.** Add a one-line comment at line 1643:

```python
# Pass 6 is idempotent — every full-rebuild run re-derives match outcomes.
# If/when incremental rebuild lands, this reset must run only once per pass,
# not once per affected file.
```

### 4. Reader comment on `_match_call_edge` feign branch (item 4, low)

**Problem.** `build_ast_graph.py:1591-1595` requires both `r.feign_name`
and `call.feign_target_name` to be truthy. The empty-string handling is
implicit — falsy short-circuit. A reader has to derive that from `if x and y`.

**Resolution.** Add inline comment:

```python
# Both feign_name fields must be non-empty: an unresolved Feign client
# (empty target name) cannot match a non-Feign route (empty feign_name).
```

### 5. README — `anchor`-fills-from-builtin behaviour (item 5, low)

**Problem.** PR-D2 merged a partial-override fill behaviour: when a
brownfield override specifies only a partial set of fields (e.g. `path`
without `method`), the missing fields are filled from the built-in detection.
This is undocumented; users will discover it experimentally.

**Resolution.** Add a paragraph to `README.md` (under the brownfield section,
near the override examples):

> When a brownfield override specifies only some of the fields the built-in
> detector would have produced, the remaining fields are inherited from the
> built-in result. This means partial overrides are non-destructive — they
> tighten rather than replace. To completely override built-in detection
> for a method, supply all fields; otherwise unspecified fields default to
> what the built-in detector would have produced. (See PR-D2 review notes
> for examples.)

### 6. TIER1B propose §6 — add `channel` to `OutgoingCallDecl` sketch (item 6, low)

**Problem.** `OutgoingCallDecl.channel` (`http` | `async`) was added in
PR-D2 but isn't in the proposal's schema sketch. Future contributors reading
the proposal will think `channel` is derivable from `client_kind`.

**Resolution.** Add `channel: str  # "http" | "async"` to the
`OutgoingCallDecl` block in `propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md` §6,
with a one-line note that this field is durable (used by `_match_call_edge`
to dispatch the matching strategy).

> **Note:** since this proposal will move to `propose/completed/` in the
> Tier-1B cleanup PR, do this update *before* the move (or update the file
> in its new home — same content).

### Definition of done (PR-E1)

1. `risk_score` is back in `[0, 1]` and a test asserts saturation at 6
   cross-service callers.
2. `VALID_CALL_MATCHES` is the canonical export; alias preserved.
3. Two inline code comments added (`pass6_match_edges` reset + feign branch).
4. README paragraph on `anchor`-fills-from-builtin shipped.
5. Proposal §6 schema sketch includes `channel`.
6. All tests still pass: `260 passed, 4 skipped`.

---

## PR-E2 — Strategy-ladder consolidation (item 7, medium tech debt)

Touches: `graph_enrich.py:720-724` and its call sites.

### Background

PR-D1's review observed: a second copy of the three-strategy ladder
(`annotation` / `spel` / `constant_ref`) lives in `graph_enrich.py:720-724`
for *brownfield route hints*, separate from the canonical resolver in
`graph_enrich.py:resolve_route_for_method` (which PR-A2 cleaned up).
PR-D1's DoD bullet 1 — *"no duplicate three-strategy ladder anywhere"* —
was interpreted (correctly, at the time) as scoped to PR-D1's introduction.
The pre-existing duplicate is left for a future PR.

### Why it matters

Two ladders mean two places to update when (e.g.) we add a fourth strategy
(injection-driven, etc.), and they will drift. We've already seen this
pattern hurt us in route resolution before PR-A2 consolidated.

### Scope of PR-E2

1. Identify the second ladder's responsibility (read `graph_enrich.py:720-724`
   in context — what does it produce that `resolve_route_for_method` doesn't?).
2. Either (a) delete the duplicate and route its callers through the
   canonical resolver, or (b) extract a shared helper that both call sites
   use, depending on which is structurally cleaner. (Implementation note:
   helper landed as `_route_path_atom` — path-atom focused naming.)
3. Add a sentinel grep test in `tests/test_call_edge_matching.py` (or a new
   `tests/test_resolver_unification.py`): assert `grep -c "annotation.*spel.*constant_ref"
   graph_enrich.py == 1` to prevent regression.

### Definition of done (PR-E2)

1. Single ladder in `graph_enrich.py`.
2. All existing tests still pass.
3. Sentinel grep test prevents regression.
4. PR description references this plan section.

### Risk

Low-to-medium. The duplicate has been stable for several PRs and isn't on
the hot path of any user-visible feature. Main risk is a subtle behavioural
diff between the two ladders that we don't notice until a brownfield route
hint stops resolving — mitigated by running `build_ast_graph.py --verbose`
on `bank-chat-system` before/after and diffing the route-resolution counts.

---

## PR-E3 — Intra-JVM CALLS invariant guard (item 8, low-to-medium)

Touches: `build_ast_graph.py` (pass3 + a new counter on `CallResolutionStats`
and a new `graph_meta` field), `tests/test_call_graph.py` (or a new
`tests/test_call_invariant.py`), one Java fixture pair (FQN collision across
two services).

Out of scope: changing `CALLS` schema, narrowing pass3 to per-microservice
resolution scopes (that's the right long-term answer but is significant work
— see related ideas in the open Tier-2 incremental rebuild proposal).

### Background

`CALLS` edges represent intra-JVM method dispatch. By Java's runtime model,
a method call cannot leave the JVM — anything crossing a microservice boundary
must be a network call (`HTTP_CALLS` or `ASYNC_CALLS`). Today `pass3_calls`
resolves callees through the global symbol table without filtering on
`microservice`. It works because real Maven/Gradle modules don't share
classpaths and fixtures replicate that, but the invariant is **implicit**.

Empirical baseline (from PR-E1 review reply, May 2026):

```
fixture: tests/fixtures/cross_service_smoke
Total CALLS edges: 9
Cross-microservice CALLS edges: 0
```

### Failure modes the guard would catch

1. **FQN collision across services** — two services declare the same FQN
   (e.g. shared DTO copy-paste). The `len(candidates) > 1` branch at
   `build_ast_graph.py:1119` could emit an `overload_ambiguous` CALLS edge
   crossing services.
2. **Brownfield supertype/role overrides** — future Layer-4 / Enhanced Role
   Recognition work could in principle introduce a phantom resolution that
   crosses the boundary.
3. **Refactors of `_resolve_and_emit_call`** — a future contributor could
   add a fallback resolution path that doesn't realise the implicit
   invariant exists.

### Resolution

Add a guard near `build_ast_graph.py:1119` (the candidate-emit loop):

```python
if member.microservice and candidate.microservice \
        and member.microservice != candidate.microservice:
    log.warning(
        "skipping cross-microservice CALLS edge %s -> %s "
        "(caller=%s, callee=%s)",
        member.fqn, candidate.fqn,
        member.microservice, candidate.microservice,
    )
    stats.skipped_cross_service += 1
    continue
```

The guard fires on:
- `len(candidates) == 1` branch (single resolved candidate is in another service)
- `len(candidates) > 1` branch (per-candidate filter inside the for loop)
- Phantom-receiver branch (`stats.phantom_other` path — phantom dst should
  not be in another service either, since phantoms are scoped to the
  caller's service)

Add `skipped_cross_service: int = 0` to `CallResolutionStats` and surface
as `pass3_skipped_cross_service` in `graph_meta`. Verbose log line in
pass3's stats summary mentions the count.

### Tests

One new fixture: `tests/fixtures/fqn_collision_smoke/` with two services
(`svc-x`, `svc-y`) each declaring `com.example.SharedDto#process()`. A third
file in `svc-x` calls `process()`. Without the guard, pass3 emits an
ambiguous edge — one to `svc-x.SharedDto` (correct) and one to
`svc-y.SharedDto` (cross-service — invalid).

```python
def test_call_invariant_blocks_cross_microservice_edges(tmp_path):
    # Build on the fqn_collision_smoke fixture
    ...
    # Assert: no CALLS edge spans services
    cross = g._rows(
        "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) "
        "WHERE a.microservice <> '' AND b.microservice <> '' "
        "AND a.microservice <> b.microservice "
        "RETURN count(*) AS c", {}
    )
    assert cross[0]['c'] == 0
    # Assert: counter shows the guard fired at least once
    assert g.meta()['pass3_skipped_cross_service'] >= 1
```

A second assertion checks the existing `cross_service_smoke` fixture is
unaffected (`pass3_skipped_cross_service == 0`) — confirming the guard
is surgical, not over-eager.

### Definition of done (PR-E3)

1. Guard in `pass3_calls`'s candidate-emit paths.
2. New counter on `CallResolutionStats` + `graph_meta` field
   `pass3_skipped_cross_service`.
3. New `fqn_collision_smoke` fixture and one test that asserts the guard
   fires (counter ≥ 1) and no cross-service CALLS edge is emitted.
4. Existing fixtures continue to show `pass3_skipped_cross_service == 0`
   (verified by extending one existing graph_meta assertion in
   `tests/test_kuzu_meta.py` or equivalent).
5. Pass3 verbose log mentions the new counter.
 6. `260+` (current baseline + 1 new test) tests still pass.

Status: shipped as PR-E3 in [#22](https://github.com/HumanBean17/java-codebase-rag/pull/22).

### Risk

Very low. Pure additive guard — it can only *prevent* edges, never create
them. The fixture-based test catches the failure mode it's designed to
catch. Worst case if the guard is buggy: it fires too aggressively and
suppresses a legal CALLS edge — surfaces immediately in the existing
determinism / call-resolution tests.

---

## Tracking

This plan supersedes loose mentions of these items scattered across PR
review threads. When PR-E1 / PR-E2 / PR-E3 ship, mark each row in the
table at the top with `✅ shipped in PR-E#`. When all rows are checked,
move this file to `plans/completed/`.
