# `@FeignClient` is a caller, not an exposer

Status: **draft — open for review**.

This is a **proposal**, not an implementable plan. After review and the
§9 [TBD] decisions, an implementable plan will be derived under
`plans/PLAN-FEIGN-NOT-AN-EXPOSER.md`. The shipping work is expected
to be one focused PR.

## TL;DR

A Java type annotated with `@FeignClient(name = "svc-b", path = "/chat")`
is **a client** — its methods make outbound HTTP calls to another
microservice. Today the indexer treats every `@FeignClient` method
as if it also **exposed** an HTTP endpoint, emitting an `EXPOSES`
edge from the Feign method to a Route node attributed to the
caller's microservice.

The result is that **the same HTTP wire is represented as two
unrelated Route nodes** — one consumer-side Route owned by the
caller, one endpoint-side Route owned by the server — and the
caller's Feign method has an `EXPOSES` edge it shouldn't have. This
breaks every "who exposes this URL?" query, every reachability walk
into a cross-service controller, and the cross-service call
matcher.

This proposal removes the `EXPOSES` edge for Feign methods, keeps
the `Route` node (it remains the catalogue entry the
`HTTP_CALLS` edge targets), and updates the cross-service call
matcher to pair the consumer-side Route against the endpoint-side
Route.

## Why this is a design issue (not just a bug)

The wrong edge isn't a typo. It's the natural outcome of a design
assumption that doesn't generalise:

> "Any method with a `@*Mapping` annotation declares a Route, and
> any method declaring a Route gets an EXPOSES edge."

That's correct for `@RestController` but incorrect for
`@FeignClient`. The author noticed the difference and distinguished
the two cases by setting `kind = "http_consumer"` vs.
`kind = "http_endpoint"` at `ast_java.py:2178`:

```python
feign_iface = type_kind == "interface" and _type_has_feign_client(type_anns)
…
kind = "http_consumer" if feign_iface else "http_endpoint"
```

But the `EXPOSES` emission at `build_ast_graph.py:1355-1364`
unconditionally appends one edge per `RouteDecl`, regardless of
`kind`. The semantic distinction was made; it's just not honoured
downstream.

The deeper smell: the `Route` node tries to be **both** "endpoint
catalogue" and "client-side declaration of what it calls". A
`Route` with `framework=feign, kind=http_consumer` is not the
same shape as a `Route` with `framework=spring_mvc,
kind=http_endpoint` — they describe opposite sides of the same
wire. Pretending they're the same node type forces every
downstream traversal to filter on `kind` to know which side it's
looking at, and the EXPOSES emission forgot to filter.

## Goals

1. **No `EXPOSES` edge from Feign methods.** A `@FeignClient`
   method is a caller; emitting `EXPOSES` from it is semantically
   wrong.
2. **`Route` nodes for Feign declarations stay.** They're still
   the `HTTP_CALLS` edge target, and they still document what
   the Feign client calls. They just lose their inbound `EXPOSES`
   edge.
3. **Cross-service matcher pairs consumer↔endpoint Routes.** The
   pre-existing matcher (PR-D3) already pairs `HTTP_CALLS` edges
   against same-shape endpoint Routes; extend it to walk
   consumer-side Routes (`framework=feign, kind=http_consumer`)
   and route the `HTTP_CALLS` edge to the **endpoint-side** Route
   (the `@RestController` on svc-b) when one exists.
4. **`graph_neighbors` of an endpoint Route reaches the Feign
   caller.** Today the Feign method is `EXPOSES`-attached to its
   own Route, not the endpoint Route, so reachability stops at
   the wrong node. Post-fix, neighbours of svc-b's endpoint Route
   include svc-a's Feign method.
5. **`FEIGN_CLIENT` role unchanged.** The role is a separate
   discussion (see §8 Future work). This proposal does not
   touch it.
6. **Backwards compatibility.** Older graphs read fine: queries
   that walk `EXPOSES` from a Feign symbol return zero rows,
   which is the correct semantics under the new model.

## Non-goals

- **Removing the `FEIGN_CLIENT` role.** Discussed in §8; out of
  scope here.
- **Generalising `HTTP_CLIENT` as a role.** Same.
- **Collapsing consumer-side and endpoint-side Routes into one
  node.** Tempting but premature — they're emitted from
  different source files in different microservices, may not
  both exist (e.g. svc-b might be a third-party API not in the
  source tree), and the consumer-side Route is the natural
  fallback target when no endpoint Route exists.
- **Schema bump.** Not needed — the change is in *which* edges
  get emitted, not what their shape is.
- **Touching `RestTemplate` / `WebClient` brownfield code.** PR-D
  series produced HTTP_CALLS edges for those clients; they
  already correctly do not emit EXPOSES.

## 1. Current state (verified on `cross_service_smoke`)

```python
# Routes with /chat/joinOperator in path:
ms=svc-a  fw=feign     kind=http_consumer  id=r:2350f45b...
ms=svc-b  fw=spring_mvc kind=http_endpoint  id=r:64685ba1...

# EXPOSES edges to /chat/joinOperator:
svc-a::BFeignClient#joinOperator()    -> fw=feign kind=http_consumer    ❌ wrong
svc-b::JoinControllerB#joinOperator() -> fw=spring_mvc kind=http_endpoint  ✅ correct

# HTTP_CALLS edges:
svc-a::BFeignClient#joinOperator() --[HTTP_CALLS strategy=feign_method
                                      match=intra_service]--> r:2350f45b... (svc-a's own Route)  ❌
                                                                                                  (should target svc-b's r:64685ba1...)
```

So for one HTTP wire (svc-a calls svc-b's `POST /chat/joinOperator`)
we emit:

- ✅ A correct `EXPOSES` from svc-b's controller method
- ❌ An incorrect `EXPOSES` from svc-a's Feign method
- ❌ An `HTTP_CALLS` edge that lands on the wrong Route (the
  consumer-side one, not the endpoint-side one), and the matcher
  even labels it `intra_service` (because both endpoints of the
  edge share `microservice=svc-a` — the Route is attributed to
  svc-a because it was declared there).

### 1.1. Failure modes this produces

| Query | Today's wrong answer | Correct answer |
|---|---|---|
| "Who exposes `POST /chat/joinOperator`?" via EXPOSES | svc-a *and* svc-b | svc-b only |
| `graph_neighbors(svc-b's endpoint Route)` | does NOT include svc-a's caller | should include svc-a's Feign method |
| `analyze_pr` impact analysis on svc-b's controller | misses svc-a as a downstream caller | should report svc-a |
| Cross-service matcher (PR-D3) | labels Feign call `intra_service` | should label `cross_service` and reach svc-b's Route |
| `g.meta()['cross_service_calls_total']` | undercounts Feign cross-service calls | accurate |

### 1.2. Why the Route node itself stays

Removing the consumer-side Route entirely would break:
- The `HTTP_CALLS` edge needs a target. Without the consumer-side
  Route as a fallback target, the matcher would have to drop the
  edge entirely when no endpoint Route exists (e.g. svc-b is a
  third-party API not in the indexed sources).
- The `feign_name` and `feign_url` literals (parsed at
  `ast_java.py:1377-1397`) live on the Route. Moving them
  somewhere else is more disruptive than keeping the node.
- Tests rely on `MATCH (r:Route {framework:'feign'})` returning
  consumer-side declarations.

So the Route stays. Only the `EXPOSES` edge is removed.

## 2. Design

### 2.1. The fix

One conditional in `pass4_routes` (`build_ast_graph.py:1354-1364`)
suppresses the EXPOSES emission for `kind=http_consumer`. The
existing emission loop becomes:

```python
ek = (member.node_id, rid)
if ek not in exposes_seen and decl.kind != "http_consumer":
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

This is the **smallest possible change** — five characters of new
code (`and decl.kind != "http_consumer"`) plus the necessary
schema doc-string update at `build_ast_graph.py:13`.

### 2.2. The cross-service matcher update

The PR-D3 matcher in `build_ast_graph.py:1614-1690` already finds a
target Route for each `HTTP_CALLS` edge. Today, for Feign methods
it picks the same-service consumer Route (the wrong one). We need
to teach it to prefer an endpoint-side Route on the **target**
microservice when one exists, identifiable by:

- Same path template (after normalization)
- Same HTTP method
- `framework=spring_mvc | webflux` and `kind=http_endpoint`
- Microservice ≠ caller's microservice

If a matching endpoint Route exists, the `HTTP_CALLS` edge
re-targets to it and the match label becomes `cross_service`. If
not, the edge keeps its current target (consumer-side Route in the
caller's microservice) and the match stays `unresolved` or the
appropriate fallback. **Always emit the edge** — never drop.

### 2.3. Order of operations

The matcher already runs after `pass4_routes`. The lookup table
keyed by `(path_template, http_method, microservice)` over
endpoint Routes is rebuilt regardless of this change; we just add
a Feign-aware secondary lookup that searches for an endpoint
Route matching the consumer-side Route's `(path_template,
http_method)` across all microservices ≠ caller's.

### 2.4. New stat

`graph_meta` gains `pass4_feign_exposes_suppressed: int` — the
count of EXPOSES edges that would have been emitted under the old
rule but are now suppressed. Useful as a regression-detection
signal: if this value drops to zero unexpectedly on a known-Feign
project, the fix has regressed.

Tiered `KuzuGraph.meta()` ladder gains a new top tier
`_META_PR_FEIGN` with fall-through to `_META_PR_E3`.

## 3. Risks and mitigations

### 3.1. Tests that asserted the wrong behaviour

**Risk:** Some test asserts `EXPOSES` count == N where N includes
Feign emissions. Removing those edges drops the count.

**Mitigation:** Audit `tests/test_route_extraction.py` and
`tests/test_kuzu_meta.py` for `EXPOSES`-counting assertions before
the implementing PR. Update assertions that were anchored to the
buggy behaviour. The DoD checklist requires explicit before/after
counts on `cross_service_smoke` and `bank-chat-system`.

### 3.2. Downstream tools depending on Feign EXPOSES

**Risk:** A tool somewhere reads "give me all the routes my
service exposes" and includes Feign methods in the answer.

**Mitigation:** Audit `kuzu_queries.py`, `server.py`, and
`pr_analysis.py` for `EXPOSES`-reading queries. The semantic shift
is "Feign methods stop being exposers" — every consumer of this
needs explicit consideration. Document each in the implementing
PR's description.

### 3.3. Match=intra_service Feign calls becoming cross_service

**Risk:** PR-E3's `pass3_skipped_cross_service` counter and the
matcher's `cross_service` label both shift on this PR. Existing
fixtures may have hardcoded counts.

**Mitigation:** PR-E3's counter is on CALLS edges, not HTTP_CALLS
— independent. The matcher's `cross_service` label is the right
new value (Feign IS a cross-service call); update tests
explicitly. Document the count delta in the PR description.

### 3.4. Feign route to a service not in the source tree

**Risk:** svc-a calls svc-b's endpoint via Feign, but svc-b lives
in another repo. No endpoint Route exists.

**Mitigation:** The matcher falls back to the consumer-side Route
(today's behaviour for the target). The `HTTP_CALLS` edge still
exists with `match=unresolved` — current behaviour preserved.

### 3.5. Older graph compatibility

**Risk:** A graph built before this change has Feign EXPOSES
edges. Reading it post-change shouldn't crash.

**Mitigation:** No schema change. Old graphs read fine — they
just have extra (incorrect) EXPOSES edges that downstream queries
will return. This is a build-time fix, not a read-time fix.
Documented in §6.

## 4. Verification

### 4.1. On `cross_service_smoke`

Pre-fix:
```
EXPOSES total: N
EXPOSES from Feign methods: 1   (svc-a::BFeignClient#joinOperator → consumer Route)
HTTP_CALLS to consumer Route: 1
HTTP_CALLS to endpoint Route: 0
```

Post-fix:
```
EXPOSES total: N - 1
EXPOSES from Feign methods: 0   ✅
HTTP_CALLS to consumer Route: 0  (re-targeted)
HTTP_CALLS to endpoint Route: 1  ✅ (svc-a's caller now reaches svc-b's controller)
match=cross_service: +1
match=intra_service: -1
graph_meta.pass4_feign_exposes_suppressed: 1
```

### 4.2. On `bank-chat-system`

Bank-chat-system has zero Feign clients (uses RestTemplate). All
counters unchanged. The fix is inert here.

### 4.3. Determinism

Two independent rebuilds produce identical EXPOSES, Route,
HTTP_CALLS, and CALLS edge hashes. The matcher's "search for an
endpoint Route across all microservices" is keyed on a sorted
lookup table; ties broken deterministically by Route id.

### 4.4. New fixture `feign_cross_service_smoke`

Trimmed reproduction of cross_service_smoke focusing on a single
Feign method:
- svc-a: `@FeignClient(name="svc-b") interface UserClient { @GetMapping("/users/{id}") User get(@PathVariable Long id); }`
- svc-b: `@RestController class UserController { @GetMapping("/users/{id}") User get(...) {} }`
- One test per assertion above.

## 5. Suggested PR scope

**Single PR, ~150 LOC:**

- Code: `build_ast_graph.py` (the conditional + matcher extension
  + new graph_meta field), `kuzu_queries.py` (new meta tier),
  schema-doc updates.
- Tests: new `tests/test_feign_not_an_exposer.py` (~5 tests),
  new fixture `tests/fixtures/feign_cross_service_smoke/`,
  updates to existing tests that asserted the buggy counts.
- Docs: schema doc-string and `kuzu_queries.py` query
  docstrings.

DoD: every assertion in §4.1 reproduces; bank-chat-system fully
inert (§4.2); existing test suite green (266 passed, 4 skipped)
plus ~5 new tests → expected **271 passed, 4 skipped**.

## 6. Backwards compatibility

- Schema unchanged. Existing graphs read with no migration.
- Old graphs **will** still have buggy EXPOSES edges for Feign
  methods. The fix is build-time; rebuild to get the corrected
  graph. No automatic migration.
- MCP tool surface unchanged. Tools that walked `EXPOSES` from
  Feign methods now return empty results — which is the
  semantically correct answer.
- `graph_meta` adds one field with tiered fallback. Older readers
  see the old tier and miss the new field; defaults to 0.

## 7. Why not the alternatives

**Why not "consumer Route gets pushed down to a `ConsumerRoute`
node type"?** A separate node type would be cleaner ontologically
but costs a schema change, an extra REL table, and a migration
for every downstream tool. The single-conditional fix solves the
reported failure modes with no schema impact.

**Why not "match consumer Route against endpoint Route at index
time and merge them"?** Tempting but conflates two distinct
declarations from two distinct source files. Merging loses the
provenance — which file declared which side. Matcher-side pairing
preserves provenance.

**Why not "drop the consumer Route entirely"?** Discussed in §1.2.
The Route is a useful catalogue entry even when no endpoint
exists.

**Why not "fix `EXPOSES` AND collapse `FEIGN_CLIENT` role"?** The
role smell is a separate, larger discussion. Bundling them makes
this PR risk-heavy. Ship the bug fix; queue the role
generalisation as a follow-up.

## 8. Future work (out of scope for this proposal)

- **Generalise `HTTP_CLIENT` role.** `FEIGN_CLIENT`,
  `RestTemplate`-wrapping classes (today: `SERVICE`/`COMPONENT`),
  and `WebClient`-wrapping classes all serve the same architectural
  role. Unifying them is a larger ontology cleanup.
- **Drop `FEIGN_CLIENT` once `HTTP_CLIENT` lands.** Or keep it as
  a sub-classifier. Decision deferred.
- **Symmetric diagnostic for ASYNC_CALLS.** ~~Kafka producers and
  consumers emit two Route nodes per topic too; the matcher
  pairs them. Verify that the same EXPOSES bug doesn't exist on
  the producer side.~~ **Audited 2026-05-05 — bug does not exist
  on the async side.** Async messaging uses structurally
  different annotations per direction: `@KafkaListener` /
  `@RabbitListener` / `@JmsListener` / `@StreamListener` /
  `@Bean Function|Consumer|Supplier` on the consumer side, and
  plain method calls (`KafkaTemplate.send`, `StreamBridge.send`,
  `JmsTemplate.convertAndSend`) on the producer side. All five
  `RouteDecl` emission sites in `ast_java.py` (lines 2040, 2071,
  2096, 2118, 2140) are gated on listener annotations or
  consumer-Bean signatures; producers (lines 1721, 1883, 1929)
  emit `OutgoingCall` records with `client_kind=kafka_send` /
  `stream_bridge_send`, **not** `RouteDecl`. So no producer-side
  Route is ever created. Empirically confirmed on
  `tests/fixtures/cross_service_smoke`: exactly 1 Kafka Route
  (`svc-b kind=kafka_topic`), 1 EXPOSES (from svc-b's listener,
  correct), and 1 cross-service ASYNC_CALLS edge
  (`svc-a::ClientA#produce() -> svc-b's kafka_topic`,
  `match=cross_service`). The asymmetry exists because HTTP
  reuses `@*Mapping` on both sides (controller and Feign)
  forcing kind-based disambiguation; async never had that
  collision. **No scope expansion needed; this fix stays
  HTTP-only.**
- **Multi-attribution Routes.** When the shared-module
  multi-attribution proposal lands, Route ownership should
  follow the same set semantics. Cross-reference at
  implementation time.

## 9. [TBD]

| # | Decision | Notes |
|---|----------|-------|
| 1 | Should the matcher walk endpoint Routes across **all** non-caller microservices, or only those declared in `@FeignClient(name=...)`? | The `name` is a Spring Cloud service-discovery name, not a microservice identifier. Matching by `name` requires a name→microservice map. Recommend: search across all microservices, prefer one whose name matches `feign_name` if multiple match. |
| 2 | When the matcher finds multiple endpoint Routes (collision)? | Ambiguous match. Today's matcher labels these `ambiguous` for HTTP_CALLS via PR-D3. Use the same label here. |
| 3 | What's the migration path for old graphs? | Document "rebuild to apply the fix", do not auto-migrate. Confirmed in §6. |
| 4 | Should `pass4_feign_exposes_suppressed` be in `KuzuGraph.meta()` or only logged? | Recommend meta — useful regression signal. New `_META_PR_FEIGN` tier. |
| 5 | Audit checklist for downstream tools | Recommend ship a small audit (1-2h) before the implementing PR opens, listing every EXPOSES-reading query and its expected new behaviour. Capture in PR description. |
| 6 | Symmetric ASYNC_CALLS bug? | **Resolved 2026-05-05 — does not exist.** See §8. Async uses different annotations per direction (`@KafkaListener` consumer vs `KafkaTemplate.send` producer); only listeners emit `RouteDecl`, producers emit `OutgoingCall`. Verified empirically on `cross_service_smoke`. Fix stays HTTP-only. |

## 10. References

- `ast_java.py:2161-2178` — the `feign_iface` branch that sets
  `kind=http_consumer`
- `build_ast_graph.py:1354-1364` — the EXPOSES emission that
  ignores `kind`
- `build_ast_graph.py:1614-1690` — the cross-service matcher
  (PR-D3)
- `tests/fixtures/cross_service_smoke/svc-a/.../BFeignClient.java`
  + `svc-b/.../JoinControllerB.java` — fixture that reproduces
  the bug
- `propose/SHARED-MODULE-MULTI-ATTRIBUTION-PROPOSE.md` — sibling
  proposal; both touch microservice attribution but on
  independent axes
- `plans/PLAN-POST-TIER1B-FOLLOWUPS.md` § PR-E3 — the matcher
  context
