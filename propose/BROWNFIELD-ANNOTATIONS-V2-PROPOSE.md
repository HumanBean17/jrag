# Brownfield Annotations v2: Channel-Split Routes + Enum-Typed Kinds

## Status

Proposal — not yet planned. Breaking change, no migration required (no
production users yet, per project policy).

## Problem Statement

The v1 brownfield annotation set ships in two halves with mismatched
shape and weak typing:

- **`@CodebaseRoute`** is a *single* method-level annotation that covers
  inbound HTTP endpoints, Feign-declared consumer routes, Kafka topics,
  Rabbit queues, JMS destinations, and Stream bindings. It carries 6
  fields where 5 are conditionally required depending on `kind`:
  - `framework()` and `kind()` are typed as enums, but
    cross-validation lives in the resolver, not the compiler.
  - `path` and `method` only make sense for `http_endpoint` /
    `http_consumer` / `feign` kinds.
  - `topic` and `broker` only make sense for `kafka_topic` /
    `rabbit_queue` / `jms_destination` / `stream_binding` kinds.
  - Nothing prevents the user from writing
    `@CodebaseRoute(framework=kafka, kind=http_endpoint)` and getting
    silent warn-and-drop at index time.

- **`@CodebaseClient`** and **`@CodebaseProducer`** are *split* by
  channel (HTTP vs async-publish), but their `clientKind()` field is a
  plain `String` even though the valid set is a 5-element frozenset
  (`feign_method`, `rest_template`, `web_client`, `kafka_send`,
  `stream_bridge_send`). Typos (`"restTemplate"` vs `"rest_template"`)
  fail silently with a stderr warning the user typically never sees.

### Why this matters in practice

Two real failure modes have been observed during rollout on a real
Java project:

1. **Direction confusion.** A user trying to register a Kafka producer
   reaches for `@CodebaseRoute(framework=kafka, …)` because that's the
   only annotation that mentions Kafka — when in fact `@CodebaseRoute`
   is inbound-only and the right tool is `@CodebaseProducer`. The
   asymmetry of the annotation set (one inbound annotation covering
   both transports, two outbound annotations split by transport) is
   the proximate cause of the confusion.

2. **Silent typos in `clientKind`.** No IDE auto-completion, no
   compile-time check; the resolver drops unknown values with a stderr
   warning that surfaces only in `--verbose` build logs.

### What the data shows

`@CodebaseRoute` field usage is genuinely bimodal — the field set
splits cleanly along the HTTP vs async axis. The `kind` enum already
encodes that split (`http_*` vs everything else), so the cleaner shape
is to lift the split into the type system instead of leaving it as a
runtime check.

## Proposed Solution

Split `@CodebaseRoute` into two transport-specific annotations and
promote every "kind" field to a typed enum.

### v2 annotation set

The full set becomes a clean 2×2 plus stable role/capability tags:

| Direction      | HTTP                       | Async                        |
| -------------- | -------------------------- | ---------------------------- |
| Inbound        | **`@CodebaseHttpRoute`**   | **`@CodebaseAsyncRoute`**    |
| Outbound       | `@CodebaseClient`          | `@CodebaseProducer`          |

Class-level: `@CodebaseRole`, `@CodebaseCapability` (unchanged from v1).

### `@CodebaseHttpRoute` (replaces `@CodebaseRoute` for HTTP kinds)

```java
package com.example.rag;

import java.lang.annotation.*;

public enum CodebaseHttpRouteFrameworkKind {
    spring_mvc,
    webflux,
    feign;
}

public enum CodebaseHttpRouteKind {
    http_endpoint,   // inbound handler exposed to external callers
    http_consumer;   // Feign-declared consumer (declares a contract on a remote)
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute {
    CodebaseHttpRouteFrameworkKind framework();
    CodebaseHttpRouteKind          kind();
    String path();      // mandatory — concatenated servlet form
    String method();    // mandatory — uppercase HTTP verb
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpRoutes {
    CodebaseHttpRoute[] value();
}
```

**What changed:**

- Renamed from `CodebaseRoute` → `CodebaseHttpRoute`.
- Framework enum narrowed to `{spring_mvc, webflux, feign}` (the three
  HTTP-flavoured frameworks). `kafka`, `rabbitmq`, `jms`, `stream` move
  to the async annotation.
- Kind enum narrowed to `{http_endpoint, http_consumer}`. The four
  async kinds move to the async annotation.
- `path` and `method` promoted to **mandatory**. No-arg defaults
  removed.
- `topic` and `broker` removed (didn't apply to HTTP).

### `@CodebaseAsyncRoute` (replaces `@CodebaseRoute` for messaging kinds)

```java
package com.example.rag;

import java.lang.annotation.*;

public enum CodebaseAsyncRouteBroker {
    kafka,
    rabbitmq,
    jms,
    stream;
}

public enum CodebaseAsyncRouteKind {
    kafka_topic,
    rabbit_queue,
    jms_destination,
    stream_binding;
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseAsyncRoutes.class)
public @interface CodebaseAsyncRoute {
    CodebaseAsyncRouteBroker broker();   // transport family (was `framework` in v1)
    CodebaseAsyncRouteKind   kind();
    String topic();                      // mandatory — destination name (topic, queue, binding)
    // Note: v1 had an optional cluster-name `broker` field. Per Open Question 1
    // it cannot coexist with the transport-family `broker()` above. The propose
    // recommends dropping it entirely; if a use case emerges, reintroduce as
    // `cluster()` or `brokerInstance()`.
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseAsyncRoutes {
    CodebaseAsyncRoute[] value();
}
```

**Naming note.** The v1 field `framework` is renamed to **`broker`**
on the async annotation — it more accurately describes what the field
holds (a message-broker family, not a web framework). The v1 had a
separate optional cluster-name field also called `broker` (e.g.
`chat-events`); it cannot coexist with the new enum field of the same
name. See Open Question 1 — the propose recommends dropping the
cluster-name field entirely (it carried no downstream resolver
behaviour) and reintroducing later as `cluster()` if a real use case
emerges.

**What changed vs v1 `@CodebaseRoute`:**

- Renamed and narrowed in scope.
- `framework` → `broker` (enum) — the four messaging families only.
- `kind` enum narrowed to the four async kinds.
- `topic` promoted to **mandatory**.
- `path`, `method` removed (didn't apply to async).

### `@CodebaseClient` (HTTP outbound — minor shape change)

```java
package com.example.rag;

import java.lang.annotation.*;

public enum CodebaseClientKind {
    feign_method,
    rest_template,
    web_client;
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseClients.class)
public @interface CodebaseClient {
    CodebaseClientKind clientKind();        // was String, now enum
    String targetService() default "";
    String path()          default "";
    String method()        default "";
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
- `clientKind` remains **required** (per partial-override semantics —
  the resolver needs the channel hint even if path/method/target are
  missing).
- `path` and `method` remain optional — the partial-override
  use case is the dominant one and forcing redundant restatement
  would be a step backwards.

### `@CodebaseProducer` (async outbound — minor shape change)

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

- `clientKind` field renamed to **`producerKind`** — `clientKind` was
  a v1 misnomer (a Kafka send is a producer, not a client).
  `producerKind` aligns with `@CodebaseClient`'s `clientKind` while
  still naming the actual concept.
- `producerKind` field type: `String` → `CodebaseProducerKind` (enum).
- `producerKind` keeps its `kafka_send` default (the dominant case).
- `topic` already mandatory; unchanged.
- Optional `broker` cluster-name field **dropped** for the same reason
  it's dropped on `@CodebaseAsyncRoute` (see Open Q1) — the resolver
  carries it through but no downstream tool filters on it. Recoverable
  later as `cluster()` if a real use case emerges.

### Class-level annotations

`@CodebaseRole`, `@CodebaseCapability`, `@CodebaseCapabilities`,
`CodebaseRoleKind`, `CodebaseCapabilityKind` — **unchanged**. They
were already enum-typed and structurally clean in v1.

## Summary of breaking changes

| v1                                                            | v2                                                              | Migration                                                                                |
| ------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `@CodebaseRoute(framework=spring_mvc, kind=http_endpoint, path, method)` | `@CodebaseHttpRoute(framework=spring_mvc, kind=http_endpoint, path, method)` | rename annotation; field set unchanged                                                   |
| `@CodebaseRoute(framework=feign, kind=http_consumer, path, method)`      | `@CodebaseHttpRoute(framework=feign, kind=http_consumer, path, method)`      | rename annotation; field set unchanged                                                   |
| `@CodebaseRoute(framework=kafka, kind=kafka_topic, topic, broker)`       | `@CodebaseAsyncRoute(broker=kafka, kind=kafka_topic, topic)`                 | rename annotation; rename `framework` field → `broker`; **drop optional cluster-name `broker`** (see Open Q1) |
| `@CodebaseRoute(framework=rabbitmq, kind=rabbit_queue, topic)`           | `@CodebaseAsyncRoute(broker=rabbitmq, kind=rabbit_queue, topic)`             | as above                                                                                 |
| `@CodebaseClient(clientKind="rest_template", path, method)`              | `@CodebaseClient(clientKind=CodebaseClientKind.rest_template, path, method)` | string literal → enum reference                                                          |
| `@CodebaseProducer(clientKind="kafka_send", topic)`                      | `@CodebaseProducer(producerKind=CodebaseProducerKind.kafka_send, topic)`     | field renamed `clientKind` → `producerKind`; string literal → enum reference; **drop optional cluster-name `broker`** (see Open Q1) |

Per project policy, no deprecation phase: v2 lands as a single
breaking change.

## Implementation Details

### Files to update

- **`java_ontology.py`** — split `VALID_ROUTE_FRAMEWORKS` and
  `VALID_ROUTE_KINDS` into `VALID_HTTP_ROUTE_FRAMEWORKS` /
  `VALID_HTTP_ROUTE_KINDS` and `VALID_ASYNC_ROUTE_BROKERS` /
  `VALID_ASYNC_ROUTE_KINDS`. Keep `VALID_CLIENT_KINDS` (renamed valid
  set: HTTP client kinds only) and add `VALID_PRODUCER_KINDS` (async
  send kinds only).

- **`graph_enrich.py`** — update brownfield extraction to recognise
  the four annotation simple names (`CodebaseHttpRoute`,
  `CodebaseHttpRoutes`, `CodebaseAsyncRoute`, `CodebaseAsyncRoutes`)
  and emit the right channel from each. Drop recognition of the v1
  `CodebaseRoute` / `CodebaseRoutes` simple names.

- **`ast_java.py`** — update annotation walkers (the
  `CODEBASE_PRODUCER_ANNOTATIONS` set, `CodebaseProducer` /
  `CodebaseRoute` simple-name dispatches at lines 1566, 1982, 1995,
  2069, 2077). Producer field rename `clientKind` → `producerKind`
  must be reflected in the AST extraction code.

- **`build_ast_graph.py`** — `kind`-string equality checks
  (`if route_kind == "http_consumer"`, etc.) are valid string values
  and don't need to change; only the source annotation that emits
  them changes.

- **`tests/fixtures/brownfield_route_stubs/`** and
  **`tests/fixtures/brownfield_client_stubs/`** — replace v1 stubs
  with v2. Stub directory names probably also want a rename for
  clarity:
  - `brownfield_route_stubs/` → `brownfield_http_route_stubs/`
  - new: `brownfield_async_route_stubs/`
  - `brownfield_client_stubs/` stays (covers both `@CodebaseClient`
    and `@CodebaseProducer`).

- **`README.md`** — section §3b (route stubs) and §3c (client/producer
  stubs) need full rewrites with the v2 shape and a fresh "Direction
  matters" inbound-vs-outbound table.

- **`CODEBASE_REQUIREMENTS.md`** — section A.2.1 brownfield list
  needs the four annotation families re-enumerated.

- **`docs/AGENT-GUIDE.md`** — the Decision tree, the slash aliases,
  and the Recovery playbook all reference the v1 annotation names.
  Update to v2.

- **`docs/MANUAL-VERIFICATION-CHECKLIST.md`** — Phase 7 items
  reference v1 annotations; rewrite to verify each of the four v2
  annotations independently (one item per annotation family).

- **All `tests/test_brownfield_*.py`** — update fixture stub paths
  and the inline `@CodebaseRoute(...)` / `@CodebaseProducer(...)`
  literals in test cases.

### Resolver compatibility

Per project policy, the v1 simple names (`CodebaseRoute`,
`CodebaseRoutes`) are removed entirely. Any project still on v1 stubs
will see its overrides drop with a stderr warning at index time
(matching the existing behaviour for unknown annotation simple
names). No silent failure.

## Acceptance Criteria

1. The four v2 annotations + their stub source files exist under
   `tests/fixtures/brownfield_*_stubs/com/example/rag/` and parse
   cleanly with tree-sitter.
2. `graph_enrich` recognises `CodebaseHttpRoute`,
   `CodebaseAsyncRoute`, `CodebaseClient`, `CodebaseProducer` (and
   their `*s` containers) by simple name and emits the correct
   `Route` / `HTTP_CALLS` / `ASYNC_CALLS` records.
3. `graph_enrich` does NOT recognise the v1 `CodebaseRoute` /
   `CodebaseRoutes` simple names; presence of v1 annotations in a
   project triggers a stderr warning ("v1 brownfield annotation
   detected; migrate to `CodebaseHttpRoute` or `CodebaseAsyncRoute`").
4. Compile-time enum typing is enforced: an integration test pastes
   a v2 stub plus a class using `@CodebaseClient(clientKind = "rest_template")`
   (string literal) into the fixture, and verifies it fails to
   compile under `javac` (or, for stub-only validation, that the
   tree-sitter parse classifies it as a string literal annotation
   value rather than an enum reference).
5. README §3b/§3c, CODEBASE_REQUIREMENTS A.2.1, AGENT-GUIDE Decision
   tree + Recovery playbook + slash aliases, and the verification
   checklist Phase 7 all reference only v2 names. No `@CodebaseRoute`
   string remains in any doc.
6. Test baseline holds: full pytest suite green
   (current baseline 290 passed, 4 skipped on `master @ d62b48c`).
   The exact count may change as v1-shape tests are rewritten, but
   no regressions outside the brownfield test files.
7. Manual verification on `tests/bank-chat-system`:
   - `graph_meta().routes_by_framework` reports an HTTP-only
     framework distribution (no `kafka` framework on HTTP routes).
   - A re-indexed fixture using a hand-applied `@CodebaseAsyncRoute`
     on one Kafka listener appears in `list_routes` with
     `kind=kafka_topic`, and the matching producer (when also
     annotated `@CodebaseProducer`) appears in `find_route_callers`.

## Out of Scope

- **No source-stub Maven dependency.** Stubs remain copy-paste
  source files; simple-name matching is preserved.
- **No infer-default for `clientKind`.** Rejected per discussion —
  keep the field required, gain the win from enum typing alone.
- **No new annotation kinds beyond the v2 set.** Adding e.g.
  `@CodebaseScheduledTask` (replacing the `SCHEDULED_TASK` capability
  with a method-level annotation) is a separate proposal.
- **No YAML override schema changes.** `route_overrides`,
  `http_client_overrides`, `async_producer_overrides` keep their v1
  shape — they already use string-typed `kind` / `client_kind`
  fields, which are validated against the (now-split) frozensets.
  The YAML shape is independent of the in-source annotation shape.

## Future Enhancements (post-v2)

- **`CodebaseClientKind` could absorb partial-override defaulting.**
  The discussion thread that produced this proposal floated inferring
  `clientKind` from the call site. Keep this in mind as a v3
  ergonomics improvement once v2 ships and we have real-project
  feedback on the field's pain.
- **A proper v2 stub Maven coordinate.** Once the shape stabilises,
  publishing the stubs as a no-op `org.userrag:annotations:1.0` jar
  would let users `import com.userrag.annotations.*` instead of
  copying the 12 source files. Out of scope for v2.

## Open Questions

1. **`broker` field collision + cluster-name fields dropped.** v2
   `@CodebaseAsyncRoute` reuses the v1 method name `broker()` for the
   transport-family enum, which collides with the v1 optional
   cluster-name `String broker()`. Two methods cannot share a name in
   a Java `@interface`. The propose **drops the optional cluster-name
   field from both `@CodebaseAsyncRoute` and `@CodebaseProducer`** —
   the resolver currently carries it through but no downstream tool
   filters on it. Recoverable later as `cluster()` on either annotation
   if a real use case emerges.
   - Alternative considered: rename the transport-family field on
     `@CodebaseAsyncRoute` to `transport()` instead. Rejected — reads
     weirdly ("transport=kafka").
   - Alternative considered: keep both `broker()` methods on
     `@CodebaseProducer` (no collision there) and only drop on
     `@CodebaseAsyncRoute`. Rejected — splitting the cleanup creates
     v2-internal asymmetry; better to drop uniformly and reintroduce
     uniformly if needed.

2. **`producerKind` rename.** The propose renames
   `@CodebaseProducer.clientKind` → `producerKind` for clarity.
   Open question: do we keep the v1 name `clientKind` to mirror
   `@CodebaseClient` despite the misnomer, or is the rename worth
   the breaking churn? **Recommendation: rename**, since v2 is a
   one-shot breaking release and now is the only time it's free.

3. **Should `@CodebaseHttpRoute.framework` be optional?** If
   `kind=http_endpoint`, the framework is rarely needed for
   resolution (it's a label for `list_routes` filtering). A default
   of `spring_mvc` would shorten the most common annotation.
   **Recommendation: keep mandatory** — the propose's whole thrust
   is "say what you mean, get a compile error if you don't".

## Notes

- This proposal is the direct outcome of feedback during the first
  real-project rollout (May 2026). Three failure modes drove it:
  - User reached for `@CodebaseRoute(framework=kafka, …)` for an
    outbound Kafka producer (right concept, wrong annotation).
  - User asked why `clientKind` is a string when the valid set is
    obviously a small enum.
  - User asked why `@CodebaseRoute` is unified across HTTP and async
    when `@CodebaseClient` and `@CodebaseProducer` are split. The
    asymmetry was the proximate cause of confusion #1.

  Annotation-set asymmetry is the root cause of #1. Splitting the
  inbound side resolves #1 while #2 and #3 are addressed in the
  same release at near-zero marginal cost.

- The v2 design preserves all three v1 design principles that are
  worth keeping: simple-name matching (no Maven dep), source-only
  retention, and partial-override semantics on `@CodebaseClient` /
  `@CodebaseProducer`. It corrects three v1 design choices that
  experience showed were wrong: unified inbound annotation, untyped
  `clientKind`, and `clientKind` field name on the producer.
