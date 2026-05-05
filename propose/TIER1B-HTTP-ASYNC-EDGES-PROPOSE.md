# B2b + B6 — `HTTP_CALLS` / `ASYNC_CALLS` + cross-service matcher

Status: **active — ready to implement**. Tier 1 (B2a + B4 + B5) is
merged; the `Route` schema in §3.1 has been verified against what
shipped. The implementable plan derived from this proposal lives at
[`plans/PLAN-TIER1B-COMPLETION.md`](../plans/PLAN-TIER1B-COMPLETION.md);
per-PR Sonnet/Cursor prompts at
[`plans/CURSOR-PROMPTS-TIER1B.md`](../plans/CURSOR-PROMPTS-TIER1B.md).

This document is now the **rationale + interface contract** that the
plan implements. Section 11 lists the open questions that have been
resolved (or formally deferred to a v2 proposal) — see the inline
resolutions there. The join-key contract in §3 remains the source of
truth for the B2a ↔ B2b ↔ B6 boundary.

---

## 0. Reading order

Before working on this proposal, read in order:

1. [`TIER1-COMPLETION-PROPOSE.md`](TIER1-COMPLETION-PROPOSE.md) §4
   (B2a `Route` + `EXPOSES`) — defines every join key used here.
2. [`reports/what-to-borrow-from-cmm.md`](../reports/what-to-borrow-from-cmm.md)
   §B2 (Route shape) and §B6 (cross-service edges).
3. [`reports/call-graph-review.md`](../reports/call-graph-review.md)
   — same correctness invariants apply (microservice scoping,
   confidence semantics, phantom-id collisions).
4. [`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](../plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md)
   — brownfield surface for the **caller** side mirrors the same
   pattern as B2a (see §6).
5. CMM source (pattern reference, do not port):
   - [`pass_http_edges.c`](https://github.com/DeusData/codebase-memory-mcp/tree/master/src/pipeline) (or equivalent) — shape only.

---

## 1. Why one proposal, not two

B2b (imperative HTTP/async edges) and B6 (cross-service matcher) were
split out of the original Tier 1 plan together for one reason: **they
share state.**

- The same canonical path/topic representation is read by *exposers*
  (B2a writes it) and *callers* (B2b emits, B6 matches).
- `confidence` for a `HTTP_CALLS` edge to a phantom `Route` flips
  meaning depending on whether B6 has matched it cross-service —
  designing one without the other locks in the wrong scale.
- Feign's `name="user-service"` is a *service-registry join key* for
  B6, not just a string property on a Feign-client method.
- The edge-direction decision in B2a
  (`(Symbol)-[:EXPOSES]->(Route)`) only pays off when the matching
  query is `(caller)-[:HTTP_CALLS]->(Route)<-[:EXPOSES]-(handler)` —
  testing that traversal end-to-end requires both sides.

**Decision:** ship B2b and B6 together. B7 (Louvain) and B8 (dead
code) are separate proposals because they consume the resulting
graph but don't change its shape.

---

## 2. Scope

### In scope

- New `HTTP_CALLS` rel: `(Symbol caller)-[:HTTP_CALLS]->(Route target)`.
- New `ASYNC_CALLS` rel: `(Symbol producer)-[:ASYNC_CALLS]->(Route topic)`.
- New `pass5_imperative_edges` (runs after `pass4_routes` — see B2a §4.4).
- Cross-service matching of caller-side edges to exposer-side `Route`
  nodes via the join keys defined in §4.
- Brownfield override surface for **caller-side** declarations
  (`@CodebaseClient`, `@CodebaseProducer` — mirrors `@CodebaseRoute`
  on the exposer side; see §6).
- New MCP tools: `find_route_callers`, `trace_request_flow`.

### Out of scope (explicit non-goals)

- **Path matching of intra-service controller-to-controller HTTP
  calls.** These are rare in well-modeled microservice codebases and
  add 4-way matching combinatorics. If the user has a `RestTemplate`
  hitting `localhost`, B2b emits a phantom-`Route` edge with
  `confidence ≤ 0.5` and stops. Re-evaluate after B7.
- **Spring Cloud Gateway route definitions** (`RouteLocator` DSL).
  Treat as a follow-on once B2b stabilizes.
- **Runtime trace ingestion.** That's B3, separate proposal.
- **OpenAPI/AsyncAPI doc parsing** as a fallback resolver. Maybe
  later; not needed to ship B2b/B6.

---

## 3. The join-key contract

This is the **only** part of this skeleton that is fully specified.
B2a writes these keys; B2b reads them; B6 matches on them. Any change
breaks both sides.

### 3.1 Keys produced by B2a (read-only for B2b/B6)

These come from the `Route` node defined in
[`TIER1-COMPLETION-PROPOSE.md`](TIER1-COMPLETION-PROPOSE.md) §4.3.
Reproduced here for the implementer's convenience — **if these
diverge from B2a as shipped, B2a is the source of truth, fix this
doc**.

| Field           | Used by              | Purpose                                                  |
| --------------- | -------------------- | -------------------------------------------------------- |
| `Route.id`      | B2b edge target      | Stable hash incl. `microservice` — same path in svc A vs svc B = two routes |
| `path_template` | B6 HTTP matcher      | `/api/users/{}` — already curly-collapsed in B2a         |
| `path_regex`    | B6 HTTP matcher      | `^/api/users/[^/]+/?$` — pre-derived in B2a, do not re-derive |
| `method`        | B6 HTTP matcher      | Must match caller's HTTP method (or `''` allows any)     |
| `topic`         | B6 async matcher     | Producer→consumer join                                   |
| `broker`        | B6 async matcher     | Disambiguates same-topic across brokers                  |
| `feign_name`    | B6 Feign matcher     | Service-registry join key — primary cross-service link   |
| `feign_url`     | B6 Feign fallback    | Used only when `feign_name` is empty (URL-mode clients)  |
| `microservice`  | B6 scoping           | Skip self-edges; flag intra-service matches as low-conf  |
| `kind`          | B2b edge-type select | `http_endpoint` → `HTTP_CALLS`; `kafka_topic` etc. → `ASYNC_CALLS` |

### 3.2 Keys produced by B2b (caller side)

For each imperative call site B2b discovers, it computes a tuple
that is **structurally identical** to the exposer side, then asks B6
to match. The fields are:

| Field                | Source                                                                                | Notes                                                                            |
| -------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `client_kind`        | `feign_method` / `rest_template` / `web_client` / `kafka_send` / `stream_bridge_send` | Picks the matcher branch.                                                        |
| `feign_target_name`  | `@FeignClient(name=…)` on the interface the caller's method belongs to                | Resolution: literal → SpEL → constant. Same three-strategy ladder as B2a §4.4.5. |
| `path_template_call` | URI argument of `RestTemplate.exchange` etc., curly-collapsed via B2a's normalizer    | Re-use B2a's normalizer — do not re-implement.                                   |
| `method_call`        | `HttpMethod.GET` etc., or extracted from the called function (`getForObject` → `GET`) | `''` means "couldn't determine".                                                 |
| `topic_call`         | First arg of `KafkaTemplate.send` / `StreamBridge.send`                               | Same three-strategy resolution.                                                  |
| `broker_call`        | The bean name of the template, when multi-broker                                      | `''` for the default broker. Heuristic; see §5.                                  |
| `caller_microservice` | The caller `Symbol`'s microservice                                                    | Required for cross-service detection.                                            |

### 3.3 Match outcome enum

B6 returns one of these for every B2b call site:

| Outcome         | Meaning                                                  | Effect on edge                                       |
| --------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| `cross_service` | B6 found exactly one `Route` in a *different* svc        | Emit edge to that `Route`, `confidence` per §5.3     |
| `intra_service` | B6 matched a `Route` in the *same* svc as caller         | Emit edge with `confidence ≤ 0.5`, flag in stats     |
| `ambiguous`     | More than one `Route` matched                            | Emit phantom-`Route` edge, `confidence=0.4`, log all candidates |
| `phantom`       | No `Route` matched at all (external API, missing svc)    | Emit phantom-`Route` edge, `confidence=0.3`          |
| `unresolved`    | Caller-side fields couldn't be extracted (SpEL, dynamic) | Emit phantom-`Route` edge, `confidence=0.2`, `resolved=false` |

`phantom` `Route` nodes follow the same shape as resolved ones but
with empty `path_template` / `path_regex` and a synthetic id — same
trick B2a uses for `strategy='spel'` routes.

---

## 4. Schema additions

```sql
-- Two new edge tables. Edge direction matches B2a §4.3 traversal.
CREATE REL TABLE HTTP_CALLS(
    FROM Symbol TO Route,
    confidence  DOUBLE,
    strategy    STRING,    -- 'feign_inherit' | 'feign_method' | 'rest_template' | 'web_client'
    method_call STRING,    -- duplicated for query convenience (same as Route.method on a perfect match)
    raw_uri     STRING,    -- the unresolved URI string when strategy='unresolved'; for debugging
    match       STRING     -- 'cross_service' | 'intra_service' | 'ambiguous' | 'phantom' | 'unresolved'
);

CREATE REL TABLE ASYNC_CALLS(
    FROM Symbol TO Route,
    confidence DOUBLE,
    strategy   STRING,    -- 'kafka_template' | 'stream_bridge' | 'rabbit_template' | 'jms_template'
    direction  STRING,    -- 'producer' (always, in B2b — consumers are EXPOSES on B2a)
    raw_topic  STRING,
    match      STRING
);
```

No new node tables. **Do not** introduce a separate `HttpCallSite`
node — the `Symbol` (the caller method) is the source of truth, and
`Route` is the destination. This keeps the graph queryable as a
pure `Symbol → Route ← Symbol` triangle.

`ONTOLOGY_VERSION` 5 → 6.

---

## 5. Caller-side extraction — `pass5_imperative_edges`

**`[Skeleton — full design pass needed before planning.]`**

Runs after `pass4_routes` (defined in B2a §4.4). Purely additive;
does not consult or modify `tables.routes_rows` (already written) or
`tables.calls_rows`.

### 5.1 Detection patterns (per `client_kind`)

Stub list — to be expanded with concrete AST patterns and tests.

| `client_kind`        | Pattern                                                                                    | Notes                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `feign_method`       | Method on a `@FeignClient` interface                                                       | The exposer side already wrote a `Route` per method via `feign_inherit` — this is just a join on `Symbol.id`, no new resolution work. **Cleanest case.** |
| `rest_template`      | `RestTemplate.{exchange,getForObject,getForEntity,postForEntity,postForObject,put,delete}` invocation | **Resolved (PR-D1).** URI is first arg → `_string_value_atoms`. Method derived from method name (`getForObject` → `GET`) or `HttpMethod.X` second arg of `exchange`. String-concat tail (`base + "/path"`) captured at `confidence_base=0.7`. |
| `web_client`         | `WebClient.{get,post,…}().uri(…).retrieve()` chain                                         | **Deferred to v2.** PR-D1 detects the `WebClient` receiver and emits `unresolved` (`confidence_base=0.3`). Backward-walking the fluent chain is a v2 design pass. |
| `kafka_send`         | `KafkaTemplate.send(topic, …)`                                                             | **Resolved (PR-D1).** Topic is first arg → `_string_value_atoms`.            |
| `stream_bridge_send` | `StreamBridge.send(binding, …)`                                                            | **Deferred to v2.** PR-D1 emits `unresolved`. Spring Cloud Stream binding-to-topic resolution requires config ingestion. |

### 5.2 Resolution ladder

Mirror B2a §4.4.5 exactly. Three strategies in order:

1. **Literal string** — `confidence_base = 1.0`, `strategy='annotation'`/`feign_method`/etc.
2. **SpEL `${prop}`** — keep literal, `confidence_base = 0.85`,
   `resolved=false`.
3. **Constant reference** — keep expression, `confidence_base = 0.7`,
   `resolved=false`.

Re-use B2a's resolver — do not re-implement. **Resolved (PR-D1):**
rename `_route_value_atoms` (`ast_java.py:1041`) → `_string_value_atoms`
and re-use from the new `pass5_imperative_edges`. No separate
extraction PR — the rename + four call-site updates ship in PR-D1
as a single atomic change. See
[`plans/PLAN-TIER1B-COMPLETION.md`](../plans/PLAN-TIER1B-COMPLETION.md)
§ PR-D1 deliverable #1.

### 5.3 Final confidence

```
confidence = confidence_base × match_factor × micro_factor
```

Where:

- `match_factor`: `cross_service=1.0`, `intra_service=0.6`,
  `ambiguous=0.5`, `phantom=0.4`, `unresolved=0.3`.
- `micro_factor`: `1.0` if caller microservice is known, `0.85`
  otherwise.

**Resolved:** adopt §5.3 baseline as written. Validation against the
real 5-service codebase is a follow-on action item, **not** a release
blocker — telemetry from `graph_meta.http_calls_match_breakdown`
(added in PR-D3) feeds the recalibration.

### 5.4 Where to plug in

`build_ast_graph.py` has `pass3_calls` at line 1067 and the call
site is at line 1421. B2a adds `pass4_routes` after `pass3_calls`.
B2b adds `pass5_imperative_edges` after `pass4_routes`. **Each pass
is purely additive on `tables.*` — no shared mutable state across
passes.**

---

## 6. Brownfield surface — caller side

Mirrors B2a §4.6 exactly — same dataclass, same YAML config file,
same in-source stubs, same 5-layer resolution table. **Do not invent
a parallel system; extend `BrownfieldOverrides` again.**

### 6.1 New YAML keys

```yaml
# .lancedb-mcp.yml
http_client_overrides:
  annotations:
    "com.acme.LegacyHttpClient":
      client_kind: rest_template
      target_service: "user-service"   # forces the cross-service join key
  fqn:
    "com.legacy.OldUserApi":
      client_kind: feign_method
      target_service: "user-service"

async_producer_overrides:
  annotations:
    "com.acme.LegacyEvent":
      client_kind: kafka_send
      topic: "user-events"
  fqn: {}
```

### 6.2 New in-source stubs

```java
@Target(METHOD)
@Repeatable(CodebaseClients.class)
public @interface CodebaseClient {
    String clientKind();        // 'feign_method' | 'rest_template' | 'web_client'
    String targetService() default "";
    String path() default "";
    String method() default "";
}

@Target(METHOD)
@Repeatable(CodebaseProducers.class)
public @interface CodebaseProducer {
    String clientKind();        // 'kafka_send' | 'stream_bridge_send' | …
    String topic();
    String broker() default "";
}
```

### 6.3 5-layer resolution table

Identical structure to B2a §4.6.4, applied to caller-side fields
instead of route-side. Composition order:

1. Built-in client/producer detection
2. Layer B: `http_client_overrides.annotations` /
   `async_producer_overrides.annotations`
3. Layer A: meta-annotation chain walk (re-use
   `collect_annotation_meta_chain`)
4. Layer C: `@CodebaseClient` / `@CodebaseProducer` in source
5. Layer B: `http_client_overrides.fqn` / `async_producer_overrides.fqn`

Last writer wins, exactly like B2a.

### 6.4 Plumbing

Add to `BrownfieldOverrides`:

- `http_client_overrides_by_annotation: dict[str, dict]`
- `http_client_overrides_by_fqn: dict[str, dict]`
- `async_producer_overrides_by_annotation: dict[str, dict]`
- `async_producer_overrides_by_fqn: dict[str, dict]`

New `graph_enrich.resolve_http_client_for_method` and
`resolve_async_producer_for_method` — shape-identical to
`resolve_role_and_capabilities` and B2a's
`resolve_routes_for_method`.

`graph_meta` exposes
`http_clients_from_brownfield_pct` / `async_producers_from_brownfield_pct`.

---

## 7. MCP surface

### 7.1 New tools

| Tool                  | Purpose                                                                                  | Inputs                                       | Output                                       |
| --------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------- | -------------------------------------------- |
| `find_route_callers`  | All `Symbol`s that call a given `Route` (cross- and intra-service)                       | `route_id` *or* (`microservice`, `path_template`, `method`) | List of caller `Symbol`s with `confidence`, `microservice`, `match` |
| `trace_request_flow`  | Walk `(entry)-[:HTTP_CALLS\|ASYNC_CALLS]->(Route)<-[:EXPOSES]-(handler)-[:CALLS*]->(…)` for N hops | `entry_route_id`, `max_hops`                 | Ordered chain across services, with confidence per hop |

### 7.2 Existing tool changes

- `impact_analysis`: extend reverse closure to follow `HTTP_CALLS`
  and `ASYNC_CALLS` edges *outbound from the changed `Route`* — so
  "what breaks if I rename `POST /api/orders`" works.
- `trace_flow`: add `HTTP_CALLS` and `ASYNC_CALLS` to its budgeted
  walk; preserve the structural-first ordering from the call-graph
  D5 fix.
- `analyze_pr` (B4): if the PR touches a method with `EXPOSES` edges,
  surface "N callers across M services" in the risk score.

---

## 8. Tests

**[TBD — full plan after extraction patterns settle.]** Mandatory
buckets:

- **Per-pattern detection** (one fixture per `client_kind`).
- **Three-strategy resolution** (literal / SpEL / constant) — same
  cases as B2a but on the caller side.
- **Cross-service matching** — Feign name match, HTTP path-template
  match, Kafka topic+broker match.
- **Match-outcome enum** — at least one fixture per outcome
  (`cross_service`, `intra_service`, `ambiguous`, `phantom`,
  `unresolved`).
- **Brownfield**: 12 fixtures mirroring B2a §4.8 (custom annotation,
  fqn override, meta-chain, `@CodebaseClient` wins over auto-detect,
  repeatable, etc.).
- **Confidence semantics** — assert `match_factor` × `confidence_base`
  matches §5.3 for each outcome.
- **Microservice scoping** — feed a fixture with two services that
  expose the same path; assert callers from each service match
  *only* their counterpart, not their own service.
- **End-to-end traversal** — assert the
  `(caller)-[:HTTP_CALLS]->(Route)<-[:EXPOSES]-(handler)` query
  works without direction reversal (validates B2a's edge-direction
  decision).

---

## 9. Risks and open questions

| #  | Risk                                                                                       | Severity | Mitigation                                                                                  |
| -- | ------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------- |
| 1  | B2a's `path_regex` regression breaks B6                                                    | High     | B2a §4.8 must include round-trip tests on `path_template ↔ path_regex` so B6 inherits a stable contract. |
| 2  | `feign_name` resolution rules diverge between B2a (interface decl) and B2b (caller side)   | High     | One resolver, used by both passes (§5.2). PR description must cite shared helper location.  |
| 3  | SpEL routes can't be matched cross-service                                                 | Medium   | Accepted — `unresolved` outcome with `confidence=0.2`. Ingest property files in a follow-on PR. |
| 4  | Multi-broker Kafka — same topic on different brokers wrongly merged                        | Medium   | Include `broker` in the join key. Default broker = `''` so single-broker codebases are unaffected. |
| 5  | Brownfield divergence from B2a's role/route resolver                                       | High     | Same mitigation as B2a §8 risk #5: implementer cites `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` line numbers. |
| 6  | Spring Cloud Gateway routes never appear, leaving phantom edges to gateway-routed services | Medium   | Out of scope (§2). Document as known gap in `README` so users add brownfield overrides.     |
| 7  | `RestTemplate` URIs built via `UriComponentsBuilder` chains                                | Medium   | **Deferred to v2.** PR-D1 emits `unresolved`. A v2 proposal will design the linear-builder-chain walk.                              |
| 8  | Performance — `pass5` adds another full AST walk                                           | Low      | Re-use the visitor from `pass3_calls`; only the *handlers* differ. Measure on the 5-service codebase before merge. |

---

## 10. Definition of done

- [ ] `Route` schema as it shipped in B2a verified against §3.1.
- [ ] `HTTP_CALLS` and `ASYNC_CALLS` tables created; ontology bumped to 6.
- [ ] `pass5_imperative_edges` runs after `pass4_routes`; stats
      counter exposes per-`match`-outcome counts.
- [ ] Three-strategy resolver shared between B2a and B2b (no
      duplication).
- [ ] Brownfield: `http_client_overrides`, `async_producer_overrides`,
      `@CodebaseClient`, `@CodebaseProducer` all wired into
      `BrownfieldOverrides` (extending, not paralleling).
- [ ] `graph_meta` reports `http_clients_from_brownfield_pct` and
      `async_producers_from_brownfield_pct`.
- [ ] All test buckets in §8 covered.
- [ ] `find_route_callers` and `trace_request_flow` MCP tools live;
      `impact_analysis` and `trace_flow` extended.
- [ ] Microservice-scoped CALLS gap (Tier 1 §10 follow-up #2)
      either fixed in a sibling PR *before* this lands, or risk #2
      elevated and explicitly accepted.
- [ ] README / PRODUCT-VISION sections marked *planned* for
      `HTTP_CALLS` / `ASYNC_CALLS` flipped to *shipped*.

---

## 11. Resolutions and v2 deferrals

The original skeleton left the items below open. Each is now resolved
or explicitly deferred. Every deferral carries a v1 escape hatch
(emit `unresolved`, `confidence_base=0.3`) so the graph stays correct
but conservative.

| #  | Question                                                                  | Resolution                                                                                              |
| -- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1  | `WebClient` fluent-chain AST patterns                                     | **Deferred to v2.** PR-D1 detects the receiver, emits `unresolved`. v2 proposal will design the backward walk. |
| 2  | `UriComponentsBuilder` URI construction                                   | **Deferred to v2.** PR-D1 emits `unresolved`. v2 will spec linear-builder-chain handling.               |
| 3  | `StreamBridge` binding → topic                                            | **Deferred to v2.** PR-D1 emits `unresolved`. v2 needs Spring Cloud Stream config ingestion.            |
| 4  | `RestTemplate` string concatenation (`base + "/path"`)                    | **In scope (PR-D1).** Right-most literal `/path` operand captured as `path_template_call`, `confidence_base=0.7`, `resolved=False`. Real-world pattern in `ChatCoreJoinClient`. |
| 5  | Confidence weights in §5.3 on the real 5-service codebase                 | **Adopt baseline; validate post-merge.** `graph_meta.http_calls_match_breakdown` (PR-D3) provides telemetry. Not a release blocker. |
| 6  | OpenAPI / AsyncAPI doc sources as a fallback resolver                     | **Out of scope (proposal §2 explicit non-goal).** Re-evaluate after B7.                                 |
| 7  | `find_route_callers` regex vs exact-match                                 | **Exact-match in v1 (PR-D3).** Inputs: `route_id` *or* (`microservice`, `path_template`, `method`). Regex variant is a follow-up.                                                              |
| 8  | Shared resolver extraction — separate PR or rolled in?                    | **Rolled into PR-D1.** Rename `_route_value_atoms` → `_string_value_atoms` + four call-site updates ship as PR-D1's deliverable #1, not a standalone PR. |

---

## 12. References

- [`TIER1-COMPLETION-PROPOSE.md`](TIER1-COMPLETION-PROPOSE.md) — B2a, B4, B5 (active).
- [`reports/what-to-borrow-from-cmm.md`](../reports/what-to-borrow-from-cmm.md) §B2, §B6.
- [`reports/call-graph-review.md`](../reports/call-graph-review.md) — invariants this proposal must not regress.
- [`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](../plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md) — mandatory reading for §6.
- [`propose/PRODUCT-VISION.md`](PRODUCT-VISION.md) §3 — `HTTP_CALLS` / `ASYNC_CALLS` are listed as *planned*; this proposal flips them to *shipped*.
