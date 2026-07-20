<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Brownfield Annotations v2: Direction-Honest, Enum-Typed, No Redundant Kinds

## Status

**Completed** — shipped (brownfield v2 annotations: `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, `@CodebaseProducer`; HTTP method enum addendum landed separately). Breaking change, no migration required (no production users yet, per project policy).

> **Revision note.** An earlier draft of this propose split `@CodebaseRoute`
> into `@CodebaseHttpRoute` + `@CodebaseAsyncRoute` while *keeping* the
> `kind` enum on each. Review feedback exposed three further problems:
> (a) `framework` on the HTTP annotation is never used by the resolver
> (label-only); (b) `broker` on the async annotation is fully derivable
> from `kind` (bijection); (c) most importantly, `kind=http_consumer`
> represented an **outbound** Feign declaration smuggled into a
> nominally inbound annotation. The shape below incorporates all three
> fixes.

## Problem Statement

The v1 brownfield annotation set ships in two halves with mismatched
shape, weak typing, and a **direction contradiction**:

- **`@CodebaseRoute`** advertised itself as the inbound annotation but
  accepted `kind=http_consumer` — the Feign declaration kind, which is
  outbound (a caller-side declaration of a remote endpoint contract).
  The annotation was simultaneously inbound and outbound depending on
  `kind`.

- **`@CodebaseRoute`** carried 6 fields where 5 were conditionally
  required. Cross-validation lived in the resolver, not the compiler.
  Nothing prevented `@CodebaseRoute(framework=kafka, kind=http_endpoint)`
  except a stderr warning at index time.

- **`@CodebaseClient`** and **`@CodebaseProducer`** were correctly
  split by channel, but their `clientKind()` field was a plain
  `String` despite a 5-element valid set (`feign_method`,
  `rest_template`, `web_client`, `kafka_send`, `stream_bridge_send`).
  Typos failed silently with stderr warnings the user usually never
  sees.

### Why this matters in practice

Two real failure modes have been observed during rollout on a real
Java project:

1. **Direction confusion.** A user trying to register a Kafka producer
   reached for `@CodebaseRoute(framework=kafka, …)` — the right
   transport on the wrong direction. The annotation set's asymmetry
   (one inbound annotation across HTTP+async, two outbound annotations
   split by transport) was the proximate cause; the deeper cause was
   that the inbound annotation was lying about its direction (it
   accepted Feign, which is outbound).

2. **Silent typos in `clientKind`.** No IDE auto-completion, no
   compile-time check.

### What the data shows

Field usage on `@CodebaseRoute` is genuinely bimodal — fields split
along the HTTP-vs-async axis. The `kind` enum encoded that split
implicitly; the cleaner shape is to lift the split into separate
annotations and remove redundant fields.

Resolver inspection (`build_ast_graph.py:1620–1670`) confirms that
`framework` is **never** used to match a call edge — it's a label for
`list_routes` filtering and the `routes_by_framework` cosmetic count
map. Any matcher branch that varies by framework is keyed off
`kind` instead. Likewise, `broker` on the async route is fully
determined by `kind`: `kind=kafka_topic` ⇒ broker is always `kafka`;
no async kind admits more than one transport.

## Proposed Solution

Split `@CodebaseRoute` into a strictly inbound annotation per channel,
move Feign declarations to the outbound side where they belong,
promote every "kind" field to a typed enum where one is still needed,
and drop the redundant `framework` / `broker` / inbound-`kind` fields.

### v2 annotation set — clean 2×2

| Direction      | HTTP                       | Async                        |
| -------------- | -------------------------- | ---------------------------- |
| **Inbound**    | **`@CodebaseHttpRoute`**   | **`@CodebaseAsyncRoute`**    |
| **Outbound**   | `@CodebaseClient`          | `@CodebaseProducer`          |

Class-level: `@CodebaseRole`, `@CodebaseCapability` (unchanged from v1).

The two inbound annotations carry **only** what identifies the
listener (path+method or topic). The two outbound annotations carry
the channel kind enum **plus** the call's destination hints. No field
on any annotation is non-trivially conditional.

### `@CodebaseHttpRoute` (inbound HTTP only)

```java
package com.example.rag;

import java.lang.annotation.*;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute {
    String path();      // mandatory — concatenated servlet form
    String method();    // mandatory — uppercase HTTP verb
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpRoutes {
    CodebaseHttpRoute[] value();
}
```

**What changed vs v1 `@CodebaseRoute`:**

- Renamed.
- Direction is now strictly inbound — no Feign declarations, no
  outbound contracts.
- `framework` field **dropped**. The greenfield extractor still
  populates `Route.framework` from source annotations
  (`@RestController` ⇒ `spring_mvc`) for cosmetic stats; the user
  never has to declare it on a brownfield override.
- `kind` field **dropped**. `@CodebaseHttpRoute` always maps to
  `Route.kind = http_endpoint` internally. The enum value lives on,
  but as a resolver-internal classification, not user input.
- `path` and `method` promoted to **mandatory**. No-arg defaults
  removed.
- `topic` and `broker` removed (didn't apply to HTTP).
- Existing `@CodebaseRoute(framework=feign, kind=http_consumer, …)`
  uses move to `@CodebaseClient(clientKind=feign_method, …)` — see
  below.

### `@CodebaseAsyncRoute` (inbound async only)

```java
package com.example.rag;

import java.lang.annotation.*;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseAsyncRoutes.class)
public @interface CodebaseAsyncRoute {
    String topic();     // mandatory — destination name (topic, queue, binding)
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseAsyncRoutes {
    CodebaseAsyncRoute[] value();
}
```

**What changed vs v1 `@CodebaseRoute`:**

- Renamed and narrowed in scope.
- Direction is strictly inbound (consumes from a topic/queue).
- `kind` field **dropped**. `@CodebaseAsyncRoute` always maps to
  `Route.kind = <broker-derived>` internally; the broker is itself
  inferred at extraction time from listener-annotation context
  (e.g. `@KafkaListener` ⇒ `kafka_topic`, `@RabbitListener` ⇒
  `rabbit_queue`). For brownfield-only methods, the extractor falls
  back to `kafka_topic` (the dominant case); a YAML override can
  set the kind explicitly if needed.
- `framework` (renamed `broker` in the earlier draft) **dropped**.
  Bijection with `kind`: no async kind admits more than one
  transport family.
- `topic` promoted to **mandatory** (was already effectively
  mandatory — listener routes without a topic were dropped by the
  resolver).
- `path`, `method` removed (didn't apply to async).

### `@CodebaseClient` (outbound HTTP — Feign + RestTemplate + WebClient)

```java
package com.example.rag;

import java.lang.annotation.*;

public enum CodebaseClientKind {
    feign_method,    // declarative Feign interface method — known path+method+target at compile time
    rest_template,   // imperative RestTemplate / RestClient call site
    web_client;      // imperative WebClient call site
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseClients.class)
public @interface CodebaseClient {
    CodebaseClientKind clientKind();           // was String, now enum
    String targetService() default "";         // remote service name (esp. for Feign)
    String path()          default "";         // remote URL path template
    String method()        default "";         // remote HTTP verb
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseClients {
    CodebaseClient[] value();
}
```

**What changed:**

- `clientKind` field type: `String` → `CodebaseClientKind` (enum).
- The async-side values (`kafka_send`, `stream_bridge_send`) are
  removed from the enum — they belong to `@CodebaseProducer`.
- `clientKind` remains **required** — the resolver needs the
  channel hint even when path/method/target are missing.
- `path` and `method` remain **optional** — the partial-override
  use case (just say "this is a feign method on user-service",
  let the extractor recover path from the source) is dominant.
- **Feign declarations move here.** Existing v1
  `@CodebaseRoute(framework=feign, kind=http_consumer, path, method)`
  rewrites to
  `@CodebaseClient(clientKind=feign_method, targetService=…, path=…, method=…)`.
  See "Resolver impact" below for what this means for the matcher.

### `@CodebaseProducer` (outbound async)

```java
package com.example.rag;

import java.lang.annotation.*;

public enum CodebaseProducerKind {
    kafka_send,
    stream_bridge_send;
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseProducers.class)
public @interface CodebaseProducer {
    CodebaseProducerKind producerKind() default CodebaseProducerKind.kafka_send;  // renamed for clarity
    String topic();                                                              // already mandatory in v1
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseProducers {
    CodebaseProducer[] value();
}
```

**What changed:**

- `clientKind` field renamed to **`producerKind`** — `clientKind`
  was a v1 misnomer (a Kafka send is a producer, not a client).
- `producerKind` field type: `String` → `CodebaseProducerKind`.
- `producerKind` keeps its `kafka_send` default (dominant case).
- `topic` already mandatory; unchanged.
- Optional cluster-name `broker` field **dropped** — the resolver
  carried it through but no downstream tool filters on it.
  Recoverable later as `cluster()` if a real use case emerges.

### Class-level annotations

`@CodebaseRole`, `@CodebaseCapability`, `@CodebaseCapabilities`,
`CodebaseRoleKind`, `CodebaseCapabilityKind` — **unchanged**. They
were already enum-typed and structurally clean in v1.

## Summary of breaking changes

| v1                                                            | v2                                                              | Notes                                                                                |
| ------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `@CodebaseRoute(framework=spring_mvc, kind=http_endpoint, path, method)`  | `@CodebaseHttpRoute(path, method)`                                          | rename; drop `framework` + `kind`                                                    |
| `@CodebaseRoute(framework=webflux, kind=http_endpoint, path, method)`     | `@CodebaseHttpRoute(path, method)`                                          | as above                                                                             |
| `@CodebaseRoute(framework=feign, kind=http_consumer, path, method)`       | `@CodebaseClient(clientKind=CodebaseClientKind.feign_method, targetService, path, method)` | **direction fix** — Feign declarations are outbound, move to `@CodebaseClient`        |
| `@CodebaseRoute(framework=kafka, kind=kafka_topic, topic, broker)`        | `@CodebaseAsyncRoute(topic)`                                                | rename; drop `framework`/`kind`/`broker`; broker is bijective with kind              |
| `@CodebaseRoute(framework=rabbitmq, kind=rabbit_queue, topic)`            | `@CodebaseAsyncRoute(topic)`                                                | as above                                                                             |
| `@CodebaseClient(clientKind="rest_template", path, method)`               | `@CodebaseClient(clientKind=CodebaseClientKind.rest_template, path, method)`| string literal → enum reference                                                      |
| `@CodebaseProducer(clientKind="kafka_send", topic)`                       | `@CodebaseProducer(producerKind=CodebaseProducerKind.kafka_send, topic)`    | field renamed; string literal → enum reference; drop optional cluster-name `broker`  |

Per project policy, no deprecation phase: v2 lands as a single
breaking change.

## Resolver impact

Two structural changes in the resolver flow follow from the new
annotation shape:

### Feign declarations are no longer `Route` nodes

In v1, a Feign interface method (`@FeignClient` interface +
`@GetMapping` on each method) emitted a `Route` row with
`kind=http_consumer`. The call-edge matcher used those rows to
recover path+target hints when matching outbound Feign calls
(`build_ast_graph.py:1741–1770`).

In v2, Feign declarations emit a `@CodebaseClient` record on the
declaring member, not a `Route`. The matcher's hint-recovery walk
must change accordingly: instead of "look up the caller's
`http_consumer` route", look up the caller's
`@CodebaseClient(clientKind=feign_method)` declaration on the same
member. Same data, different storage location.

**Schema implication.** The `HTTP_CALLS` edge currently goes
`Symbol → Route`. To preserve the current shape, Feign clients
that resolve to a remote endpoint should still produce a
`HTTP_CALLS` edge from the caller `Symbol` to the remote service's
`http_endpoint` `Route`. Internally, the resolver may need a new
`Client` projection (or a new column on `Symbol`) to store the
client-kind metadata that previously lived on the `http_consumer`
`Route`. **Detailed design for the `Client` projection is a
separate proposal (see `propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`).**

### `list_routes` no longer returns Feign rows

A direct consequence: `list_routes` now returns only inbound things
this service exposes (HTTP handlers + async listeners). Outbound
client-side declarations need a new query path —
`list_clients` — covered by a separate propose.

This is a behavioural change visible to the AMA agent: any prompt
or recovery playbook that says "list Feign clients via
`list_routes(framework=feign)`" must be rewritten to use the new
tool.

## Implementation Details

### Files to update

- **`java_ontology.py`** — keep `VALID_ROUTE_KINDS` as resolver-internal
  classification; remove `feign`/`kafka`/`rabbitmq`/`jms`/`stream` from
  `VALID_ROUTE_FRAMEWORKS` (resolver-internal, populated by extractor).
  Tighten `VALID_CLIENT_KINDS` to HTTP-only; add `VALID_PRODUCER_KINDS`
  for async-only.
- **`graph_enrich.py`** — recognise `CodebaseHttpRoute`,
  `CodebaseAsyncRoute`, `CodebaseClient`, `CodebaseProducer` simple
  names (and their `*s` containers). Drop recognition of the v1
  `CodebaseRoute` simple names. Map `@CodebaseHttpRoute` → emit
  `Route(kind=http_endpoint, framework=spring_mvc)` by default;
  `@CodebaseAsyncRoute` → emit `Route(kind=<inferred>, framework=<inferred>)`.
- **`ast_java.py`** — annotation walkers (line refs in code:
  `CODEBASE_PRODUCER_ANNOTATIONS` set, `CodebaseProducer` /
  `CodebaseRoute` simple-name dispatches at lines 1566, 1982, 1995,
  2069, 2077). Add Feign-declaration ⇒ `@CodebaseClient` extraction
  path on Feign interface methods. Producer field rename
  `clientKind` → `producerKind` reflected throughout.
- **`build_ast_graph.py`** — `kind`-string equality checks
  (`if route_kind == "http_consumer"`, etc.) survive at the
  internal-classification layer. Pass6's hint-recovery walk
  (lines 1741–1770) updates to consult `@CodebaseClient` records
  on the caller member instead of the caller's `http_consumer` route.
- **`tests/fixtures/brownfield_route_stubs/`** and
  **`tests/fixtures/brownfield_client_stubs/`** — replace v1 stubs
  with v2.
  - Rename `brownfield_route_stubs/` → `brownfield_http_route_stubs/`
  - New: `brownfield_async_route_stubs/`
  - `brownfield_client_stubs/` stays — covers `@CodebaseClient`
    (incl. Feign declarations) and `@CodebaseProducer`.
- **`README.md`** — section §3b/§3c full rewrites with v2 shape and
  a fresh "Direction matters" inbound-vs-outbound table that
  *explicitly* names Feign as outbound.
- **`CODEBASE_REQUIREMENTS.md`** — section A.2.1 brownfield list
  re-enumerated against the four v2 annotations.
- **`docs/AGENT-GUIDE.md`** — Decision tree, slash aliases, Recovery
  playbook all reference v1 annotation names; rewrite to v2.
- **`docs/MANUAL-VERIFICATION-CHECKLIST.md`** — Phase 7 items
  rewritten as one verification per v2 annotation family.
- **All `tests/test_brownfield_*.py`** — update fixture paths and
  inline annotation literals.

### Resolver compatibility

Per project policy, the v1 simple names (`CodebaseRoute`,
`CodebaseRoutes`) are removed entirely. Any project still on v1 stubs
sees overrides drop with a stderr warning at index time (matching
existing behaviour for unknown annotation simple names). No silent
failure.

## Acceptance Criteria

1. The four v2 annotations + their stub source files exist under
   `tests/fixtures/brownfield_*_stubs/com/example/rag/` and parse
   cleanly with tree-sitter.
2. `graph_enrich` recognises `CodebaseHttpRoute`, `CodebaseAsyncRoute`,
   `CodebaseClient`, `CodebaseProducer` (and their `*s` containers)
   by simple name and emits the correct `Route` / `HTTP_CALLS` /
   `ASYNC_CALLS` records.
3. Feign interface methods, when annotated with
   `@CodebaseClient(clientKind=feign_method, …)`, are NOT emitted as
   `Route` nodes. They participate in call-edge resolution as
   outbound clients only.
4. `graph_enrich` does NOT recognise the v1 `CodebaseRoute` /
   `CodebaseRoutes` simple names; presence of v1 annotations in a
   project triggers a stderr warning ("v1 brownfield annotation
   detected; migrate to `CodebaseHttpRoute` / `CodebaseAsyncRoute` /
   `CodebaseClient`").
5. Compile-time enum typing is enforced: an integration test pastes
   a class using `@CodebaseClient(clientKind = "rest_template")`
   (string literal) into the fixture, and verifies it fails to
   compile under `javac`.
6. README §3b/§3c, CODEBASE_REQUIREMENTS A.2.1, AGENT-GUIDE
   Decision tree + Recovery playbook + slash aliases, and the
   verification checklist Phase 7 reference only v2 names. No
   `@CodebaseRoute` string remains in any doc.
7. Test baseline holds: full pytest suite green (current baseline
   290 passed, 4 skipped on `master @ d62b48c`). Exact count may
   change as v1-shape tests are rewritten.
8. Manual verification on `tests/bank-chat-system`:
   - `list_routes` no longer returns Feign declaration rows.
   - `find_route_callers` for a `@RestController` handler still
     resolves Feign-side callers via the new
     `@CodebaseClient(clientKind=feign_method)` extraction path.
   - `graph_meta().routes_by_framework` reports an HTTP-only
     framework distribution (no `kafka` framework on HTTP routes,
     no `feign` framework anywhere — Feign rows are gone).

## Out of Scope

- **No `Client` projection / `list_clients` MCP tool detail in this
  propose.** That's a follow-up — the propose covers only the
  annotation shape and the resolver flow change. The persistence
  shape for outbound client metadata is in
  `propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`.
- **No source-stub Maven dependency.** Stubs remain copy-paste
  source files; simple-name matching is preserved.
- **No infer-default for `clientKind`.** Rejected — keep field
  required, gain the win from enum typing alone.
- **No new annotation kinds beyond the v2 set.** Adding e.g.
  `@CodebaseScheduledTask` is a separate proposal.
- **No YAML override schema changes.** `route_overrides`,
  `http_client_overrides`, `async_producer_overrides` keep their v1
  shape — they already use string-typed `kind` / `client_kind`
  fields, validated against the (now-tightened) frozensets. The
  YAML shape is independent of the in-source annotation shape.

## Future Enhancements (post-v2)

- **Infer `clientKind` from call site.** Once v2 ships and we have
  real-project feedback, consider inferring `clientKind` from
  surrounding context (`@FeignClient` interface ⇒ `feign_method`)
  and making the field optional.
- **Maven coordinate for stubs.** Once shape stabilises, publishing
  the stubs as a no-op `org.userrag:annotations:1.0` jar would let
  users `import com.userrag.annotations.*` instead of copying the
  source files.

## Notes

- This proposal is the direct outcome of feedback during the first
  real-project rollout (May 2026). Three failure modes drove it:
  - User reached for `@CodebaseRoute(framework=kafka, …)` for an
    outbound Kafka producer.
  - User asked why `clientKind` is a string when the valid set is
    obviously a small enum.
  - User asked why `@CodebaseRoute` is unified across HTTP+async
    when `@CodebaseClient` and `@CodebaseProducer` are split.

  Direction-confused inbound annotation is the root cause of #1.
  Splitting the inbound side resolves it; tightening enums and
  removing redundant fields addresses #2 and #3 in the same
  release at near-zero marginal cost.

- v2 preserves the v1 design principles worth keeping: simple-name
  matching (no Maven dep), source-only retention, partial-override
  semantics on outbound annotations. It corrects four v1 design
  choices that experience showed were wrong:
  1. Inbound annotation that secretly accepted outbound Feign.
  2. Untyped `clientKind`.
  3. `clientKind` field name on the producer.
  4. Redundant `framework`/`broker`/inbound-`kind` fields the
     resolver doesn't actually use.
