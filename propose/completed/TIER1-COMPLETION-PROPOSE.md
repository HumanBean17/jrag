# Tier 1 completion — proposal (shipped)

Status: **completed — shipped via PR-A1 → PR-C** (merged 2026-04 → 2026-05). Moved to `propose/completed/` after PR-D3 (Tier 1B) landed. Pairs with the borrow guide
[`reports/what-to-borrow-from-cmm.md`](../../reports/what-to-borrow-from-cmm.md)
and follows on from the completed
[`propose/completed/CALL-GRAPH-PROPOSE.md`](CALL-GRAPH-PROPOSE.md).

This proposal closes out **Tier 1 of the borrow guide** within the
**static-analysis scope**. It explicitly defers items that require
runtime data (B3) and items already shipped (B1).

---

## 1. Why now

The call-graph layer (intra-JVM `CALLS`) is in. With
`confidence`-scored static edges plus `EXTENDS` / `IMPLEMENTS` /
`INJECTS`, the graph captures **every wire that lives inside one JVM**.

What it still cannot answer:

- *"List all controller routes under `/api/v1/`"* / *"What endpoints
  does `user-service` expose?"* — endpoints are only present today as
  `role=CONTROLLER` symbols with no path/method metadata. The Spring
  annotation tree carries the answer; we just don't surface it.
- *"What's the blast radius of this PR?"* — `impact_analysis` exists
  for symbols, but not for a `git diff`. The reverse-closure machinery
  is already there; only the diff-to-symbol mapper is missing.
- *"Why does indexing miss / over-include files in this monorepo?"* —
  the current single-list of glob excludes
  (`COMMON_EXCLUDED_PATH_PATTERNS`) ignores `.gitignore` content and has
  no per-project override.

These are the three remaining static-analysis items in Tier 1: **B2a**
(`Route` declarations), **B4** (`analyze_pr` with risk score), **B5**
(layered ignores).

---

## 2. Scope of this proposal

### Static-analysis remit

This proposal stays **inside the static-analysis layer**: source code,
AST, and FQN-keyed graph state. No runtime data, no cross-process
matching.

### Tier 1 status snapshot

| Borrow item | Status | Where |
|---|---|---|
| **B1** — confidence-scored `CALLS` cascade | ✅ done | `build_ast_graph.py`: `_resolve_receiver_type`, `_resolve_and_emit_call`; `kuzu_queries.py` `CALLS` schema with `confidence` / `strategy` / `source` |
| **B2** — `Route` as a first-class node | ❌ **not started** | Spring annotations are detected for *role classification* only (`ast_java.py` `_ROLE_BY_ANNOTATION`); no `Route` node, no `EXPOSES` / `HTTP_CALLS` / `ASYNC_CALLS` rel, no path/method extraction |
| **B3** — runtime trace ingestion | ⛔ **out of scope** (runtime data, not static) | — |
| **B4** — `analyze_pr` with risk score | ❌ **not started** | `impact_analysis` exists for a single symbol; no diff parser, no risk formula, no MCP tool |
| **B5** — layered ignore patterns | ❌ **not started** | `java_index_v1_common.py` `COMMON_EXCLUDED_PATH_PATTERNS` is a single hardcoded list; no `.gitignore` walk, no project-level override file |

Verified state — Cursor can re-confirm by `grep`-ing the files cited above.

### B2 split — declarations now, edges later (with B6)

Originally B2 was a single feature: `Route` node + `EXPOSES` (server
side) + `HTTP_CALLS` / `ASYNC_CALLS` (client side). Designing the
client-side edges in isolation forces decisions that B6 (cross-service
matching) would need to revisit:

- The **dominant case** for `HTTP_CALLS` is *cross-JVM* (Feign clients,
  external base-URL `RestTemplate`, Kafka producer→consumer). B6 is the
  matcher that joins those calls to peer services' `Route` nodes.
- Feign's `name="user-service"` argument is a service-registry join key
  for B6, not just a string property.
- `confidence` semantics for `HTTP_CALLS` to a *phantom* `Route`
  flip from "low — unknown target" (no B6) to "high — resolved
  cross-service" (with B6). Choosing one without the other locks in a
  schema that needs to change.
- `path_template` canonicalization needs a join-friendly normal form
  shared by *exposers* and *callers*; B6 may discover style mismatches
  between services that drive that decision.

So B2 is split:

- **B2a — `Route` + `EXPOSES` only** (this proposal). Pure server-side
  declarations: parse `@GetMapping` / `@RequestMapping` /
  `@KafkaListener` / etc., create `Route` nodes, link them to declaring
  methods via `EXPOSES`. **No `HTTP_CALLS` / `ASYNC_CALLS` in this
  proposal.**
- **B2b + B6 together** — the imperative side (`HTTP_CALLS` /
  `ASYNC_CALLS`) and the cross-service matcher land in a follow-on
  proposal. They share design decisions (path canonicalization, join
  keys, edge direction, cross-service `confidence` semantics) that are
  cleanest to make once.

What B2a unlocks immediately:

- *"List all controller routes under `/api/v1/`"* — answerable.
- *"What endpoints does `user-service` expose?"* — answerable.
- *"Show me the route for this method"* — answerable.

What B2a sets up cleanly: the join target for B2b/B6 is *already in the
graph* before the matcher is written.

### What this proposal delivers

Three independent sub-features, **landable as three separate PRs in any
order** (no shared code paths):

1. **B2a — `Route` nodes + `EXPOSES`** (§4)
2. **B4 — `analyze_pr` MCP tool with risk score** (§5)
3. **B5 — Layered ignore patterns** (§6)

### Explicit non-goals

- **Imperative-side HTTP / async edges (`HTTP_CALLS` / `ASYNC_CALLS`).**
  Deferred to the B2b + B6 proposal. See §10.
- **Cross-service matcher (B6 itself).** Same proposal as B2b — depends
  on the `Route` node B2a delivers.
- **Path-template canonicalization beyond simple `{var}` capture →
  template + regex.** Good enough for declarations alone; the full
  normal form discussion belongs in B2b/B6.
- **AOP / proxy-aware resolution** of route handlers. Confidence-flagged
  but unresolved remains the right behaviour for `@Async`,
  `@Transactional` self-invocations, etc. Runtime traces (B3) are the
  right fix.
- **Microservice classpath isolation in `CALLS` resolution.** Tracked
  separately ([noted in this session]) — does not block Tier 1.

---

## 3. Principle: additive evolution

Same posture as `CALL-GRAPH-PROPOSE.md`. Nothing existing is removed.

### What stays exactly as-is

- LanceDB tables, `JavaLanceChunk` schema, CocoIndex flow. **No re-index
  required for B4 / B5.** B2a requires a Kuzu rebuild only.
- All existing MCP tool signatures. New tools (`find_routes`,
  `analyze_pr`) are additive.
- `CALLS` schema and its three resolution passes. B2a emits its rels in
  a new pass that runs after `pass3_calls`.
- **Brownfield resolver execution order in `graph_enrich.py`** stays
  exactly as documented (built-in → Layer B annotations → Layer A meta
  chain → Layer C in-source → Layer B fqn). B2a hooks into this layered
  composition rather than running its own parallel resolution. See §4.6.

### What gets added on top

| Sub-feature | AST / parsing | Graph builder | Kuzu schema | Queries / MCP | Ontology |
|---|---|---|---|---|---|
| **B2a Routes** | Annotation-arg extraction (path / method / topic / queue) in `ast_java.py` | New `pass4_routes` | `Route` node + `EXPOSES` rel | `find_routes` MCP tool; `trace_flow` `follow_routes` flag | 4 → 5 |
| **B4 `analyze_pr`** | none | none | none | New `analyze_pr` tool; new `_diff_to_symbols.py` helper | none |
| **B5 Ignores** | none | New `path_filter.py` (gitignore-spec via `pathspec`) | none | `graph_meta` exposes the resolved ignore stack for diagnostics | none |

---

## 4. B2a — `Route` + `EXPOSES` (declarations only)

### 4.1 Goal

Turn endpoint declarations into graph-traversable metadata so that
listing, filtering, and per-method route lookup are first-class. This
is **server-side only** in this phase.

### 4.2 Annotation surface (Spring 6.x focus)

Three families. **Each populates the same `Route` node label**, with
`framework` distinguishing them.

| Family | Annotations | `framework` | `kind` |
|---|---|---|---|
| **HTTP server** (Spring MVC + WebFlux) | `@RequestMapping`, `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping` | `spring_mvc` | `http_endpoint` |
| **HTTP client (declarative)** | `@FeignClient` (class-level base path / `name` / `url`) + same mappings on its methods | `feign` | `http_consumer` |
| **Async listener** | `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@StreamListener` | `kafka` / `rabbitmq` / `jms` / `stream` | `kafka_topic` / `rabbit_queue` / `jms_destination` / `stream_binding` |

Why Feign declarations are in scope here even though Feign callers
aren't: a `@FeignClient` interface is a **declarative endpoint
description** — it tells us "this microservice expects to consume
`GET /users/{id}` on `user-service`". It's structurally a route
declaration. The *imperative* `userClient.findById(123)` call is
B2b/B6's job; B2a stops at the interface.

### 4.3 Schema additions

```sql
-- New node label
CREATE NODE TABLE Route(
    id            STRING PRIMARY KEY,    -- stable: hash(framework, normalized_path|topic, method, microservice)
    kind          STRING,                -- 'http_endpoint' | 'http_consumer' | 'kafka_topic' | 'rabbit_queue' | 'jms_destination' | 'stream_binding'
    framework     STRING,                -- 'spring_mvc' | 'webflux' | 'feign' | 'kafka' | 'rabbitmq' | 'jms' | 'stream'
    method        STRING,                -- 'GET' | 'POST' | … | '' for async
    path          STRING,                -- '/api/users/{id}' | '' for async
    path_template STRING,                -- normalized: '/api/users/{}' (curly captures collapsed)
    path_regex    STRING,                -- '^/api/users/[^/]+$' — provided here so B2b/B6 can reuse it without re-deriving
    topic         STRING,                -- async only
    broker        STRING,                -- async only
    feign_name    STRING,                -- @FeignClient(name=…) — empty for non-Feign; B2b/B6 will use it as the join key
    feign_url     STRING,                -- @FeignClient(url=…) — empty when name-based
    microservice  STRING,
    module        STRING,
    filename      STRING,
    start_line    INT64,
    end_line      INT64,
    resolved      BOOLEAN                -- false if path/topic was unparseable (SpEL, constant ref, etc.)
);

CREATE REL TABLE EXPOSES(
    FROM Symbol TO Route,
    confidence  DOUBLE,                  -- 1.0 annotation-derived literal | 0.85 SpEL ${prop} | 0.7 constant ref
    strategy    STRING                   -- 'annotation' | 'spel' | 'constant_ref' | 'feign_inherit'
);
```

`Route.id` is a stable hash so re-runs produce the same id and the same
rel does not duplicate. Including `microservice` in the hash means
*"`/api/users` exposed by service A"* and *"`/api/users` exposed by
service B"* are two different `Route` nodes — exactly the behaviour
B2b/B6 will need.

`path_regex` is precomputed at extraction time and stored on the node
so the eventual B2b/B6 matcher does not need to re-derive it; this
keeps regex generation in **one** code path.

**Edge direction is `(Symbol)-[:EXPOSES]->(Route)`.** The rationale: a
method exposes a route; the route is a destination. This makes the
B2b/B6 traversal `(caller)-[:HTTP_CALLS]->(Route)<-[:EXPOSES]-(handler)`
work without reversing direction at any hop. Locking it now.

### 4.4 Extraction algorithm

New `pass4_routes`, runs after `pass3_calls`. **Single phase** in this
proposal (B2a declarations only):

For every `MethodDecl` whose enclosing `TypeDecl` carries one of the
trigger annotations (see §4.6 for how those are *resolved* — not just
the literal Spring set):

1. Collect class-level base path. For Spring MVC controllers:
   `@RequestMapping("/api/v1")`. For Feign: `@FeignClient(name=…,
   url=…, path=…)`. For Kafka class listeners:
   `@KafkaListener(topics=…)` at class level.
2. Collect method-level mapping:
   - `value` / `path` — string or string array; arrays produce one
     `Route` per element.
   - `method` — for `@RequestMapping` only. `@GetMapping` etc. carry an
     implicit method.
   - `topics` / `queues` / `destination` for async.
3. Compose final path = `class_base + method_path` (handle leading /
   trailing slashes; `class_base ?? "" + method_path ?? "/"` collapsed
   via `posixpath.normpath`).
4. **Normalize path** (deterministic, source-of-truth for B2b/B6):
   - `/api/users/{id}` → `path_template = "/api/users/{}"`,
     `path_regex = "^/api/users/[^/]+/?$"`.
   - Trailing slash variants collapsed (`/foo` and `/foo/` produce the
     same template; regex allows both).
   - Multiple `{}` segments handled left-to-right.
5. **Resolve annotation argument values** through three strategies, in
   order:
   - **Literal string** — `confidence=1.0`, `strategy='annotation'`.
   - **SpEL `${app.api.base}`** — emit a `Route` with the literal SpEL
     placeholder kept in `path` (e.g. `/${app.api.base}/users`),
     `path_template` and `path_regex` left empty,
     `strategy='spel'`, `confidence=0.85`, `resolved=false`. Future
     enhancement: read property files.
   - **Constant reference** (`Endpoints.USERS`) — emit a `Route` with
     the unresolved expression in `path`, `strategy='constant_ref'`,
     `confidence=0.7`, `resolved=false`. Future enhancement: walk the
     constant.
6. Emit `Route` node + `EXPOSES` edge from the method's `Symbol.id` to
   `Route.id`. For Feign interfaces, emit one extra `EXPOSES` edge per
   method using `strategy='feign_inherit'` so per-method route lookup
   works without traversing the type-method-class triangle.

The phase is purely additive — it does not consult or modify
`tables.calls_rows`.

### 4.5 No imperative side here (deliberately)

This proposal does **not**:

- Visit `RestTemplate` / `WebClient` / `KafkaTemplate.send` /
  `StreamBridge.send` call sites.
- Emit `HTTP_CALLS` or `ASYNC_CALLS` edges.
- Match call-site URL literals against `Route.path_regex`.
- Walk Feign-interface method invocations to their `EXPOSES` route.
- Handle WebClient builder-chain extraction.

All of the above belong in B2b + B6, where the cross-service join is
the primary design constraint.

### 4.6 Brownfield integration (load-bearing — read carefully)

**Why this matters for B2a:** legacy and vendor codebases routinely
wrap Spring stereotypes (`@AcmeRestController extends @RestController`)
or use proprietary annotations (`@HttpEndpoint`, `@MessageHandler`)
that this proposal's hardcoded annotation list does not know about. The
existing brownfield system already solves this for *roles* /
*capabilities*; B2a must extend the same machinery, **not** introduce a
parallel one.

The user has explicitly called this out as critical for their use case
("legacy projects, if auto resolve did not work properly").

#### 4.6.1 What already exists (for context)

`graph_enrich.py::resolve_role_and_capabilities` runs five layers in
this order; **last to apply wins**:

1. Built-in inference (hardcoded annotation → role / capability map).
2. **Layer B annotations** — `role_overrides.annotations` and
   `role_overrides.capabilities` from `.lancedb-mcp.yml`.
3. **Layer A meta-chain** — automatic walk over project `@interface`
   declarations; resolves `@AcmeService → @Service → SERVICE`
   transitively.
4. **Layer C in-source** — `@CodebaseRole` / `@CodebaseCapability`
   stub annotations on a class.
5. **Layer B fqn** — `role_overrides.fqn` per-type config (highest
   priority).

#### 4.6.2 What B2a adds — `route_overrides`

A new top-level key in `.lancedb-mcp.yml`, shaped to match the existing
`role_overrides` style so the brownfield surface stays one mental model:

```yaml
microservice_roots: []

role_overrides:
  annotations:
    AcmeService: SERVICE
  fqn:
    com.legacy.OrderProcessor:
      role: SERVICE

# NEW — B2a
route_overrides:
  # Layer B annotations: simple-name → route declaration semantics
  annotations:
    AcmeRestController:
      framework: spring_mvc
      kind: http_endpoint
      # implies "this is a class-level controller; methods inside use
      # path_attribute and method_attribute (or @GetMapping etc.) below"
      class_path_attribute: basePath        # @AcmeRestController(basePath="/api/x")
    AcmeRoute:
      framework: spring_mvc
      kind: http_endpoint
      path_attribute: value                  # @AcmeRoute("/users")
      method_attribute: httpMethod           # @AcmeRoute(value="/users", httpMethod="GET")
      method_default: GET                    # used when method_attribute is absent
    AcmeKafkaTopic:
      framework: kafka
      kind: kafka_topic
      topic_attribute: name
    CompanyHttpEndpoint:
      framework: spring_mvc
      kind: http_endpoint
      path_attribute: url
      method_attribute: verb

  # Layer C-equivalent for routes — direct per-FQN declaration
  fqn:
    com.legacy.SoapBridge#process(Request):
      framework: spring_mvc
      kind: http_endpoint
      path: /legacy/soap/process
      method: POST
    com.legacy.JmsHandler:
      framework: jms
      kind: jms_destination
      topic: legacy.events.in
```

Two layers, mirroring the role override design:

- **`route_overrides.annotations`** — annotation simple name → "treat
  this annotation as a route declaration with these argument
  conventions". Resolved before the meta-chain walk.
- **`route_overrides.fqn`** — per-method or per-type FQN → fully
  specified `Route` declaration. Highest priority. The user can pin a
  route exactly when no annotation pattern matches at all.

#### 4.6.3 New in-source stub — `@CodebaseRoute`

Mirrors `@CodebaseRole` / `@CodebaseCapability` so the "last-resort
source stub" pattern carries over. The existing brownfield doc already
introduces `@CodebaseRole` as a way to fix things without YAML edits;
`@CodebaseRoute` is its route equivalent.

```java
package com.example.rag;  // any package; matched by simple name only

import java.lang.annotation.*;

public enum CodebaseRouteFramework {
    SPRING_MVC, WEBFLUX, FEIGN, KAFKA, RABBITMQ, JMS, STREAM
}

public enum CodebaseRouteKind {
    HTTP_ENDPOINT, HTTP_CONSUMER, KAFKA_TOPIC, RABBIT_QUEUE,
    JMS_DESTINATION, STREAM_BINDING
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseRoutes.class)
public @interface CodebaseRoute {
    CodebaseRouteFramework framework();
    CodebaseRouteKind kind();
    String path() default "";
    String method() default "";   // GET/POST/... ; empty for async
    String topic() default "";    // async only
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseRoutes {
    CodebaseRoute[] value();
}
```

Method-level only (routes are method-anchored), `@Repeatable` because
one method can legitimately serve multiple paths.

#### 4.6.4 Resolution order — full table for B2a

For every method, the route extractor runs **these layers in order**;
last to apply wins (consistent with `resolve_role_and_capabilities`).
Multiple paths produce multiple `Route` nodes; layers don't *replace*
each other's emissions, they *add* and the latest layer's
`(framework, kind, path|topic, method)` overrides any prior identical
tuple.

| # | Layer | Source | What it produces | Confidence |
|---|---|---|---|---|
| 1 | **Built-in annotation map** | hardcoded list in `pass4_routes` (Spring MVC + WebFlux + Feign + Kafka + Rabbit + JMS + Stream) | one `Route` per resolved literal | 1.0 (literal), 0.85 (SpEL), 0.7 (constant) |
| 2 | **Layer B route_overrides.annotations** | `.lancedb-mcp.yml` | one `Route` per matching annotation, using the configured `path_attribute` / `method_attribute` etc. | 1.0 (literal), 0.85 (SpEL), 0.7 (constant) |
| 3 | **Layer A meta-chain** | `graph_enrich.collect_annotation_meta_chain` (existing function, reused) | resolves `@AcmeMapping` → `@GetMapping` transitively, then runs Layer 1 logic | 1.0 (literal) etc. |
| 4 | **Layer C in-source `@CodebaseRoute`** | source code | one `Route` per `@CodebaseRoute` instance, fully specified | 1.0 |
| 5 | **Layer B route_overrides.fqn** | `.lancedb-mcp.yml` | one `Route` per FQN entry, fully specified; **and** suppresses any conflicting earlier emissions for the same FQN | 1.0 |

Crucial design points:

- **Layer A reuses the existing `collect_annotation_meta_chain`
  function.** No second filesystem walk, no parallel index. The
  function already returns `simple_name → frozenset[built-in
  simple names reachable]`. B2a passes its own annotation set
  (`{"GetMapping", "PostMapping", "RequestMapping", "FeignClient",
  "KafkaListener", …}`) and asks "for annotation X, does its meta-chain
  reach any of these?". Single source of truth, exactly as the existing
  brownfield architecture mandates.

- **`route_overrides.annotations` is checked BEFORE the meta-chain
  walk** — same precedence rule as `role_overrides`. Explicit user
  config wins over automatic resolution.

- **`route_overrides.fqn` is the strongest layer** and is the user's
  escape hatch when meta-chain + Layer C + literal annotations all fail
  (or, more importantly, produce wrong results that the user wants to
  override).

- **Conflict resolution within a layer**: if the same (framework, kind,
  path|topic, method) tuple is produced twice in the same layer, dedup
  silently. If two layers produce conflicting tuples for the same
  method, the later layer wins and the earlier layer's `Route` is not
  emitted (logged at INFO).

- **Validation on YAML load**: same pattern as `role_overrides` —
  unknown `framework` / `kind` strings are dropped with a stderr
  warning. Schema validated in `graph_enrich._load_brownfield_overrides`
  (extend the existing function rather than write a parallel one).

#### 4.6.5 Plumbing changes

- `graph_enrich.BrownfieldOverrides` dataclass gains two fields:
  ```python
  route_annotation_specs: dict[str, RouteAnnotationSpec]
  route_fqn_specs: dict[str, RouteFqnSpec]
  ```
- `graph_enrich._load_brownfield_overrides` parses `route_overrides:`
  alongside `role_overrides:`. Same YAML file, same load function, same
  cache.
- New `graph_enrich.resolve_routes_for_method(method, type, *,
  overrides, meta_chain) -> list[ResolvedRoute]` — the route analogue
  of `resolve_role_and_capabilities`, runs the five layers above and
  returns all emitted `Route` declarations for one method.
- `pass4_routes` in `build_ast_graph.py` calls
  `resolve_routes_for_method` for every member; emits `Route` + `EXPOSES`
  rows from the result.
- `java_ontology.py` gains `VALID_ROUTE_FRAMEWORKS` / `VALID_ROUTE_KINDS`
  for the validator (mirrors `VALID_ROLES`).

### 4.7 MCP surface

One new tool, read-only, filters by microservice:

- `find_routes(framework: str | None, method: str | None,
  path_pattern: str | None, microservice: str | None,
  kind: str | None) -> list[RouteHit]`

  Each `RouteHit` contains the `Route` row plus the methods that
  `EXPOSES` it.

Plus one optional flag on `trace_flow`:

- `follow_routes: bool = False`. When true, `EXPOSES` edges count as a
  stage transition (so `CONTROLLER -> Route` is visible in one walk).
  Default off — preserves existing behaviour.

`graph_meta` gains:

- `routes_total`
- `routes_by_framework`
- `routes_by_kind`
- `routes_unresolved_pct` (fraction with `resolved=false`)
- `routes_from_brownfield_pct` (fraction emitted by Layers 2–5; lets
  the user verify their overrides are actually being applied)

### 4.8 Tests (mandatory before merge)

A new fixture `tests/fixtures/routes_smoke/` with the following files:

```
src/main/java/smoke/
  GreetingController.java          // @RestController + @GetMapping("/hello/{name}")
  OrderController.java             // class-level @RequestMapping("/api/v1/orders") + @PostMapping("")
  MultiPathController.java         // @GetMapping({"/a", "/b"}) — one method, two routes
  PathVarController.java           // @GetMapping("/users/{id}/posts/{postId}") — two captures
  UserClient.java                  // @FeignClient(name="user-svc", path="/users") + @GetMapping("/{id}")
  KafkaConsumer.java               // class-level @KafkaListener(topics="orders.created")
  RabbitConsumer.java              // @RabbitListener(queues="orders.q")
  SpelPathController.java          // @GetMapping("${app.api.base}/foo") — SpEL case
  ConstantRefController.java       // @GetMapping(Endpoints.USERS) — constant case
```

Plus a brownfield fixture `tests/fixtures/routes_brownfield/`:

```
.lancedb-mcp.yml                   // route_overrides config (see §4.6.2 example)
src/main/java/smoke/
  AcmeController.java              // @AcmeRestController(basePath="/legacy") + @AcmeRoute(value="/x", httpMethod="POST")
  AcmeMapping.java                 // @AcmeRestController @interface meta-annotated with @RestController
  AcmeMappingUser.java             // class using @AcmeMapping (Layer A meta-chain target)
  CodebaseRouteUser.java           // method with @CodebaseRoute(framework=SPRING_MVC, kind=HTTP_ENDPOINT, path="/legacy/soap", method="POST")
  FqnOverrideTarget.java           // plain class with no annotations; route_overrides.fqn declares its route
```

Test cases (a list, not exhaustive code — implementor fills in):

**Built-in annotations:**
- `test_get_mapping_emits_route_and_exposes`
- `test_class_level_request_mapping_concatenates_with_method_path`
- `test_multi_value_mapping_emits_one_route_per_path`
- `test_path_variable_collapses_to_template_and_regex`
- `test_two_path_variables_handled_left_to_right`
- `test_feign_client_emits_route_with_framework_feign_and_feign_name`
- `test_feign_class_path_concatenates_with_method_path`
- `test_kafka_listener_class_emits_route_with_framework_kafka`
- `test_rabbit_listener_method_emits_route`

**Resolved values:**
- `test_spel_path_emits_route_with_resolved_false_and_strategy_spel`
- `test_constant_ref_path_emits_route_with_resolved_false_and_strategy_constant_ref`
- `test_route_id_is_stable_across_runs`
- `test_route_id_includes_microservice_so_same_path_in_two_services_is_two_routes`

**Brownfield (mandatory — not optional):**
- `test_route_overrides_annotations_layer_b_resolves_acme_rest_controller`
- `test_route_overrides_annotations_layer_b_uses_configured_path_attribute`
- `test_route_overrides_annotations_layer_b_uses_method_default_when_attribute_absent`
- `test_meta_chain_layer_a_resolves_user_defined_annotation_to_get_mapping`
- `test_codebase_route_layer_c_emits_fully_specified_route`
- `test_codebase_route_layer_c_repeatable_emits_multiple_routes`
- `test_route_overrides_fqn_layer_b_overrides_annotation_emission`
- `test_route_overrides_fqn_emits_route_for_method_with_no_annotations`
- `test_unknown_framework_in_yaml_dropped_with_warning`
- `test_unknown_kind_in_yaml_dropped_with_warning`
- `test_brownfield_layers_compose_in_documented_order`
- `test_graph_meta_routes_from_brownfield_pct_nonzero_when_overrides_apply`

**Query surface:**
- `test_find_routes_filters_by_microservice`
- `test_find_routes_filters_by_framework`
- `test_find_routes_filters_by_path_pattern_regex`
- `test_trace_flow_follow_routes_walks_exposes`

### 4.9 Ontology bump

`ONTOLOGY_VERSION` 4 → 5. Stale graphs must fail loudly on open
(existing N4 guard from the call-graph review applies automatically).

---

## 5. B4 — `analyze_pr` MCP tool

### 5.1 Goal

Take a `git diff` (text or `git_ref`), map its line ranges to graph
nodes, run the existing reverse closure, and return a structured
impact + risk report. Single call from a code review or CI gate.

### 5.2 Inputs and outputs

```python
@dataclass
class AnalyzePrInput:
    diff: str | None = None              # raw unified-diff text
    base_ref: str | None = None          # e.g. 'origin/main'
    head_ref: str | None = None          # default 'HEAD'
    # exactly one of (diff,) or (base_ref/head_ref) required
    risk_thresholds: tuple[float, float] = (1.0, 2.5)  # (low->medium, medium->high)
    max_blast_depth: int = 3
    microservice: str | None = None
    min_confidence: float = 0.0

@dataclass
class AnalyzePrOutput:
    changed_nodes: list[ChangedSymbol]
    blast_radius: list[Symbol]           # reverse closure unioned across all changed nodes
    risk_score: float
    risk_level: str                      # 'low' | 'medium' | 'high'
    per_file: list[FileImpact]           # for UX rendering
    unmapped_hunks: list[UnmappedHunk]   # diff lines that didn't map to any node
```

### 5.3 Algorithm

1. **Resolve diff source.**
   - If `diff` is set, use as-is.
   - Else run `git diff --unified=0 --no-color --no-renames {base_ref}..{head_ref}`
     via `subprocess.run` from `LANCEDB_MCP_PROJECT_ROOT`.
   - Parse with `unidiff` (well-tested PyPI library; pin in
     `requirements.txt`).
2. **Map hunks → symbols.**
   For each `(file, line_range)` from each hunk:
   - Look up Kuzu `Symbol` rows where `filename = file`
     AND `start_line <= range.end` AND `end_line >= range.start`.
   - Prefer the smallest enclosing symbol (method > type > file). Ties
     broken by `start_line` proximity.
   - Hunks that don't map to any symbol go to `unmapped_hunks` (e.g.,
     `pom.xml`, `README.md`; comment-only changes inside a method are
     still mapped to the method).
3. **Reverse closure.**
   For each changed symbol, run `find_callers(depth=max_blast_depth)`
   and union the results into `blast_radius`. Existing `find_callers`
   already supports `microservice` and `min_confidence` filters; expose
   them on `AnalyzePrInput` as optional pass-throughs.
4. **Risk score.**
   ```
   per_node_risk = log10(1 + len(downstream_consumers))
                 * role_weight[node.role]
                 * cross_service_factor

   role_weight:
     CONFIG          1.8
     CONTROLLER      1.5
     ENTITY          1.3
     SERVICE         1.2
     FEIGN_CLIENT    1.2
     COMPONENT       1.1
     REPOSITORY      1.0
     MAPPER          0.9
     DTO             0.6
     OTHER           0.7

   cross_service_factor = 2.0 if changed_nodes span >1 microservice else 1.0

   risk_score = max(per_node_risk for node in changed_nodes)
   ```
   Threshold: `low < 1.0 ≤ medium ≤ 2.5 < high` (overrideable via input).

   The `max` (not sum) keeps a single-controller change rated as high
   risk regardless of how many trivial DTO renames sit in the same PR.

### 5.4 What this tool deliberately doesn't do

- Run tests. It maps changes; review tooling decides.
- Understand semantic equivalence (renames look like add+delete in the
  graph). A future enhancement plugs into git-rename detection
  (`git diff -M`).
- Consult LanceDB (vector). Pure graph closure — same semantics as
  `impact_analysis`, just diff-driven instead of symbol-driven.

### 5.5 MCP surface

```
analyze_pr(
    diff: str | None,
    base_ref: str | None = None,
    head_ref: str | None = "HEAD",
    risk_thresholds: tuple[float, float] = (1.0, 2.5),
    max_blast_depth: int = 3,
    microservice: str | None = None,
    min_confidence: float = 0.0,
) -> AnalyzePrOutput
```

Reuses the existing `Symbol` / `CallEdge` projections (no new schema).

### 5.6 Tests

Unit tests (no Kuzu round-trip):

- `test_unidiff_parser_handles_unified_zero_context_diffs`
- `test_hunk_to_symbol_picks_smallest_enclosing_method`
- `test_hunk_to_symbol_falls_back_to_type_when_outside_methods`
- `test_unmapped_hunks_collected_for_pom_xml`
- `test_risk_role_weight_table_keys_match_known_roles`
- `test_cross_service_factor_two_when_changes_span_microservices`
- `test_risk_uses_max_not_sum_across_changed_nodes`
- `test_risk_thresholds_overridable_via_input`

Round-trip tests against the existing fixture:

- `test_analyze_pr_with_synthetic_diff_returns_blast_radius`
- `test_analyze_pr_blast_radius_respects_min_confidence`
- `test_analyze_pr_handles_pure_test_only_diff_as_low_risk` (paths
  under `src/test/` are excluded from blast radius — they consume but
  aren't consumed)

### 5.7 Performance note

`analyze_pr` against a 5-microservice repo with a 100-line diff should
finish well under a second — diff parsing + ~10–50 reverse closures,
each already a fast indexed query.

---

## 6. B5 — Layered ignore patterns

### 6.1 Goal

Replace the single hardcoded `COMMON_EXCLUDED_PATH_PATTERNS` list with
a layered resolver that respects existing `.gitignore` files **and**
allows project-level overrides. Existing behaviour stays the default;
the new layers are additive.

### 6.2 Layer order (innermost wins)

1. **Hardcoded must-skip** (cycle protection, security):
   `.git/`, `node_modules/`, `target/`, `build/`, `out/`, `.idea/`,
   `.gradle/`, `bin/`, `*.class`, symlinks. Cannot be overridden.
2. **Walk up `.gitignore` files** from each indexed directory toward
   `LANCEDB_MCP_PROJECT_ROOT`. Standard gitignore semantics (negation,
   directory-vs-file, anchored paths).
3. **Project-level `.lancedb-mcp.yml` `ignore:` list** at project root.
   Treated as gitignore-spec patterns. Already validated YAML — extend
   the schema.
4. **Project-level `.lancedb-mcp-ignore` file** with full gitignore
   syntax (one pattern per line, `#` comments). For users who don't
   want to commit project-internal index settings to YAML.

### 6.3 Implementation

New module `path_filter.py`:

```python
class IgnoreResolver:
    def __init__(self, project_root: Path): ...
    def is_ignored(self, path: Path) -> bool: ...
    def explain(self, path: Path) -> IgnoreDecision:
        """Return which layer matched (for diagnostics / graph_meta)."""
```

Use [`pathspec`](https://pypi.org/project/pathspec/) (gitignore-spec
compliant; used by Black / Ruff / pre-commit). Explicit-pin it.

Replace `compile_excluded_glob_patterns(COMMON_EXCLUDED_PATH_PATTERNS)`
call sites with `resolver.is_ignored(p)`. Three call sites:
`build_ast_graph.py:228`, `graph_enrich.py:216`,
`java_index_flow_lancedb.py:335-354`.

Keep `COMMON_EXCLUDED_PATH_PATTERNS` as the **layer 1** seed inside
`IgnoreResolver` so the constant remains the single source of truth
for "must-skip".

### 6.4 Diagnostics

Extend `graph_meta` with:

```python
ignore_layers: list[IgnoreLayerSummary]
# [
#   {layer: "hardcoded",        patterns: 8,  files_excluded: 1247},
#   {layer: ".gitignore",       sources: ["chat-app/.gitignore", ...], files_excluded: 89},
#   {layer: "lancedb-mcp.yml",  patterns: 3,  files_excluded: 12},
#   {layer: ".lancedb-mcp-ignore", patterns: 0, files_excluded: 0},
# ]
```

Lets the user diagnose "why isn't `Foo.java` in the index?" without
turning on debug logging.

### 6.5 Tests

Pure unit tests, no Kuzu / Lance:

- `test_hardcoded_must_skip_cannot_be_negated_by_gitignore`
- `test_gitignore_in_subdirectory_overrides_parent`
- `test_gitignore_negation_pattern_re_includes_file`
- `test_lancedb_yml_ignore_list_applied_after_gitignore`
- `test_lancedb_mcp_ignore_file_takes_precedence_over_yml`
- `test_symlinks_always_skipped`
- `test_explain_returns_innermost_winning_layer`
- `test_compatibility_default_excludes_match_old_behaviour` —
  important regression: with no `.gitignore` and no project files, the
  new resolver must skip exactly the same paths as the old constant
  list.

Integration test:

- `test_pass1_parse_skips_files_per_resolved_ignores` — tiny tmp_path
  fixture with one `.gitignore` and one `.lancedb-mcp-ignore`.

### 6.6 Migration

No migration. Layer 1 alone matches the current behaviour; layers 2–4
only kick in when the project has `.gitignore` / config files.
Existing users see zero behaviour change unless they opt in.

---

## 7. Cross-cutting concerns

### 7.1 Ontology version

| Sub-feature | Bump? |
|---|---|
| B2a Routes | **Yes**, 4 → 5 (new node label + new rel table) |
| B4 `analyze_pr` | No (read-only over existing schema) |
| B5 Ignores | No (no schema change) |

If B2a and the others land in the same release, one bump (4 → 5)
covers everything.

### 7.2 PR ordering recommendation

Independent PRs, but a sensible review order:

1. **B5 first** — pure code hygiene, no schema, smallest diff. Makes
   subsequent indexer test runs more deterministic on dirty workspaces.
2. **B4 second** — pure additive MCP tool. Risk is in the diff parser
   only, easily unit-testable.
3. **B2a last** — biggest scope, schema bump, requires Kuzu rebuild.
   Land it when the other two are stable.

### 7.3 Rollback strategy

- B2a — drop the rel table and the `Route` label, revert
  `ONTOLOGY_VERSION`. No data dependency from existing tools.
- B4 — remove the MCP tool registration. No persisted state.
- B5 — revert call-site replacements; the resolver module is dead
  code, remove or keep on the shelf.

### 7.4 Documentation updates

- `README.md`: add `Route` section under "What goes in the graph",
  document `find_routes` under MCP tools, document `analyze_pr`,
  document layered ignores under configuration. **Extend the brownfield
  section with `route_overrides` examples and `@CodebaseRoute`
  source stub** — same shape as the existing `role_overrides` /
  `@CodebaseRole` material.
- `CODEBASE_REQUIREMENTS.md`: update the schema diagram and the env-var
  table (`.lancedb-mcp-ignore` mention). Document the route resolver
  five-layer composition table from §4.6.4.
- `propose/PRODUCT-VISION.md`: tick B2a / B4 / B5 off the roadmap; note
  B2b + B6 as the next proposal.

---

## 8. Risks and open questions

| Risk | Likelihood | Mitigation |
|---|---|---|
| Spring annotation arg parsing edge cases (SpEL `${}`, constants, array form `value={"a","b"}`) | medium | Treat unparseable values as `Route` with `resolved=false`, low confidence; add unit fixtures as discovered. **When in doubt, emit phantom + low confidence rather than guess.** |
| `unidiff` not handling `git diff` extensions (e.g. `--stat`, binary) | low | Use `--unified=0 --no-color --no-renames` explicitly when shelling to git; document the input format. |
| `pathspec` performance on huge monorepos | low | Cache compiled `PathSpec` per directory; invalidate on `.gitignore` mtime change. Same caching pattern as `lru_cache` already used. |
| Path-template false positives (`/api/users` matches `/api/users/{id}` regex if anchors are wrong) | medium | Lock down regex generation in unit tests with the cases from §4.4; trailing slash and `$` anchors must be explicit. Note: B2b/B6 will be the primary consumer of these regexes — getting them right here matters for the next phase too. |
| Brownfield route resolver diverges from role resolver in subtle ways (ordering, caching, validation) | **high** | Extend the *same* `BrownfieldOverrides` dataclass and the *same* `_load_brownfield_overrides` function; reuse `collect_annotation_meta_chain`; mirror the `resolve_role_and_capabilities` execution-order docstring word-for-word in `resolve_routes_for_method`. Cursor implementor: read `graph_enrich.py` §"brownfield role / capability overrides" and `plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` before writing route resolution. |
| Feign + class-level `@RequestMapping` interactions | low | Resolved by §4.4's class-base + method-path concatenation rule, same as for controllers. |
| Conflicting `route_overrides.fqn` and `@CodebaseRoute` on the same method | low | Documented: Layer 5 (`fqn`) wins over Layer 4 (`@CodebaseRoute`). Same precedence rule as roles. Test `test_brownfield_layers_compose_in_documented_order` covers it. |

Open questions to settle during implementation, not now:

- Should `@MessageMapping` (WebSocket / STOMP) join the `Route` family?
  Defer until a real corpus uses it — it would slot in as another
  built-in `framework`.
- Should we extract `produces=` / `consumes=` content types onto the
  `Route` node? Probably yes when B2b/B6 lands (helpful for matching);
  not needed for B2a's listing use cases.

---

## 9. Definition of done (per sub-feature)

**B2a — `Route` + `EXPOSES`:**
- [ ] All §4.8 tests pass — including the **brownfield** group (mandatory, not optional).
- [ ] `graph_meta` reports non-zero `routes_total` and
  `routes_from_brownfield_pct` against the brownfield fixture.
- [ ] `find_routes` registered as an MCP tool with `microservice`,
  `framework`, `kind`, `path_pattern`, `method` filters.
- [ ] `trace_flow` `follow_routes` flag wired through.
- [ ] `ONTOLOGY_VERSION` bumped 4 → 5; stale-graph guard test added.
- [ ] README brownfield section extended with `route_overrides` and
  `@CodebaseRoute` examples.
- [ ] `CODEBASE_REQUIREMENTS.md` documents the §4.6.4 five-layer
  composition table.
- [ ] No regressions in existing role / capability resolution
  (run the existing brownfield test suite).

**B4 — `analyze_pr`:**
- [ ] All §5.6 tests pass.
- [ ] `analyze_pr` registered as an MCP tool with full input/output
  schemas.
- [ ] README documents the tool with a worked example.

**B5 — Ignores:**
- [ ] All §6.5 tests pass.
- [ ] Old `compile_excluded_glob_patterns` call sites replaced (3 of
  them).
- [ ] `graph_meta` exposes `ignore_layers`.
- [ ] `CODEBASE_REQUIREMENTS.md` documents the layer order.

---

## 10. What comes after Tier 1

This proposal closes Tier 1 within static-analysis scope. The natural
follow-ups, in order of leverage:

1. **B2b + B6 — imperative HTTP/async edges + cross-service matcher.**
   Single proposal because they share design constraints (path
   canonicalization, join keys, cross-service `confidence` semantics,
   edge direction). Depends on B2a's `Route` node landing first.
   Unlocks: *"what breaks if I rename `POST /api/orders`?"*,
   *"who calls this endpoint?"* across the whole system.
2. **Microservice-scoped resolution in `CALLS`** — the correctness gap
   noted during this session: `_lookup_method_candidates` should filter
   by caller's microservice. Small PR; orthogonal to anything here.
3. **B7 Louvain communities** and **B8 dead code** — both unlock from
   the existing `CALLS` graph.
4. **B3 runtime traces** — leaves static analysis. Lifts confidence on
   Spring AOP / polymorphic / reflective edges that no amount of static
   work can reach.

---

## 11. References

- [`TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`](TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md) - B2b + B6 propose
- [`reports/what-to-borrow-from-cmm.md`](../../reports/what-to-borrow-from-cmm.md) — original borrow guide (Tier 1 §B1–B5).
- [`propose/completed/CALL-GRAPH-PROPOSE.md`](CALL-GRAPH-PROPOSE.md) — completed call-graph proposal; same shape & style.
- [`reports/call-graph-review.md`](../../reports/call-graph-review.md) — review that surfaced the resolver / extractor invariants.
- [`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](../../plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md) — **mandatory reading** for the implementer of §4.6 (brownfield route resolver mirrors this design).
- `graph_enrich.py` §"brownfield role / capability overrides" — the
  existing implementation B2a extends.
- CMM source for pattern reference (read, don't fork):
  - [`pass_route_nodes.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_route_nodes.c) — Route extraction shape.
  - [`pass_gitdiff.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_gitdiff.c) — `analyze_pr` shape.
  - [`discover/`](https://github.com/DeusData/codebase-memory-mcp/tree/master/src/discover) — layered ignore shape.
- [`pathspec`](https://pypi.org/project/pathspec/) — gitignore-spec library for B5.
- [`unidiff`](https://pypi.org/project/unidiff/) — diff parser for B4.
