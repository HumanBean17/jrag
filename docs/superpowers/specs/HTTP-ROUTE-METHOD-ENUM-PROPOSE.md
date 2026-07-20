<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Brownfield HTTP annotations: rename `@CodebaseClient` → `@CodebaseHttpClient`, type `method` as a shared `CodebaseHttpMethod` enum on both sides, lock brownfield-exclusivity

**Status**: completed — shipped (see [`plans/completed/PLAN-HTTP-ROUTE-METHOD-ENUM.md`](../plans/completed/PLAN-HTTP-ROUTE-METHOD-ENUM.md)).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12 (v4 doc alignment)

## TL;DR

- **Rename**: `@CodebaseClient` → `@CodebaseHttpClient`, `@CodebaseClients` → `@CodebaseHttpClients`. Closes the channel-naming asymmetry vs `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`.
- **Enum-type `method`** on **both** `@CodebaseHttpRoute` and `@CodebaseHttpClient`, sharing a single `CodebaseHttpMethod` enum.
- Locked value set: **`GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS`** (seven values). No `TRACE`, no `CONNECT`, no `ANY`, no `OTHER`, no `INHERIT`.
- **New foundational principle**: a brownfield annotation is the **exclusive** source of truth for its facet — when present, framework introspection of that facet (path, method, target service) is **skipped entirely**. Annotation and Spring/JAX-RS metadata coexist on the same method; the brownfield annotation always wins; no merge, no diff, no warning on disagreement.
- `method` is unconditionally required on **all** `@CodebaseHttpClient` annotations, including `clientKind=feign_method`. The verb declared on the brownfield annotation is the truth even if the Feign interface also has `@GetMapping("/x")`. The duplication is **accepted as the price of brownfield-exclusivity**.
- **Breaking change accepted** — no users yet, no migration path, no soft-deprecation.
- Ships in **3 PRs**: PR-1 enum stub + structured-log emitter scaffolding **with zero behaviour change**; PR-2 atomic rename + parser rewrite to enum on client side + `_merge_layer_c_codebase_routes` HTTP branch rewritten from merge to replace (closes the only axis where brownfield-exclusivity was being violated, see §6 Q4) + **extractor-time** INFO `brownfield-exclusivity-shadowing` on brownfield/framework co-presence (§3); PR-3 docs (AGENT-GUIDE.md, exploration skill cheat sheet trailer).
- `Route.attrs.http_method` and `Client.attrs.http_method` graph attributes stay **`String`** (the enum's `.name()`). Wire format unchanged; agent-facing JSON identical.
- YAML override shape (`route_overrides.method`, `http_client_overrides.method`) stays **`String`** — independent of source-annotation surface per v2-locked decision.

## §1 — Frame

> **A brownfield annotation is a developer's exclusive contract with the indexer. It replaces framework introspection of the facet it covers; it never supplements it.**

The v2 propose (`propose/completed/BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md`) established the four-annotation surface — `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseClient`, `@CodebaseProducer` — but left two cracks:

1. **Naming asymmetry.** Inbound annotations name the channel (`HttpRoute`, `AsyncRoute`). Outbound annotations don't — `Client` could mean HTTP, gRPC, message-bus client, or something else. The asymmetry is invisible until a future `@CodebaseGrpcClient` or `@CodebaseMessageClient` proposal walks in and discovers the namespace is taken by an HTTP-only thing.
2. **String-typed `method`.** Closed-set field, freeform string. The same critique that retired the v1 `String clientKind` survives unchanged for `method`.

Both cracks are symptoms of a deeper unstated rule: **brownfield annotations were treated as hints stapled on top of framework introspection** rather than as a complete replacement. v1 had this confusion explicitly ("the annotation supplements `@GetMapping`"). v2 narrowed the surface but never wrote the rule down.

This propose writes the rule down and uses it to justify every locked decision below. After this propose lands:
- The annotation surface is channel-named: `Http{Route,Client}` and `Async{Route,Producer}` mirror each other.
- Every closed-set field on the brownfield surface is enum-typed; the only string fields left are genuinely open-set (`path`, `targetService`, `topic`, `broker`).
- The exclusivity rule is documented, asserted by the extractor, and visible in agent-facing tooling.

## §2 — Design principles

1. **Brownfield-exclusivity.** A brownfield annotation is the **complete and exclusive** source of truth for the facets it declares (`path`, `method`, `targetService`, `clientKind`). When `@CodebaseHttpRoute` or `@CodebaseHttpClient` is present on a method, the extractor skips framework introspection for that method entirely — `@GetMapping`, `@FeignClient`, JAX-RS, Vert.x routers, all bypassed.
2. **Closed-set fields are enum-typed. No exceptions.** Defended in §6 by an enumerated value-set audit.
3. **Annotation names declare their channel.** `Http*` for HTTP. `Async*` for non-HTTP messaging. A future `Grpc*` or `Mq*` annotation gets its own namespace, not a parameterised dodge inside `Codebase{Route,Client}`.
4. **Breaking changes are allowed; soft-deprecation is not.** Repo has no production users.
5. **The enum is shared between `@CodebaseHttpRoute` and `@CodebaseHttpClient`.** Both speak the same wire protocol; the value sets are identical.
6. **No `INHERIT` / `UNSPECIFIED` / `ANY` enum values.** Inheritance from framework annotations is forbidden by principle 1 — the brownfield annotation wins and is the only declared verb. Accepting a sentinel value would re-introduce the framework-introspection coupling the rename is meant to sever.
7. **Annotation-surface changes are independent of wire format and YAML.** `Route.attrs.http_method` and YAML overrides stay `String`. The enum lives at the Java annotation layer; everything downstream consumes the `.name()`.
8. **Source-stub copy-paste integration model is preserved.** New `CodebaseHttpMethod.java` ships alongside the existing stubs. Five stubs total post-merge: `CodebaseHttpRoute`, `CodebaseHttpRoutes`, `CodebaseHttpClient`, `CodebaseHttpClients`, `CodebaseHttpMethod` (plus the existing `CodebaseClientKind`, `CodebaseAsyncRoute`, `CodebaseProducer`, etc. — unchanged here).

## §3 — The proposed annotation surface

### `@CodebaseHttpRoute` (was: same name)

```java
package com.example.rag;

import java.lang.annotation.*;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute {
    String path();                  // mandatory — concatenated servlet form
    CodebaseHttpMethod method();    // mandatory — typed HTTP verb (was String)
}
```

### `@CodebaseHttpClient` (was: `@CodebaseClient`)

```java
package com.example.rag;

import java.lang.annotation.*;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpClients.class)
public @interface CodebaseHttpClient {
    CodebaseClientKind clientKind();              // unchanged from v2
    String targetService() default "";            // unchanged from v2
    String path() default "";                     // unchanged from v2
    CodebaseHttpMethod method();                  // mandatory — was String with default ""
}
```

**Three breaking changes vs v2:**
- Simple name `CodebaseClient` → `CodebaseHttpClient` (and `CodebaseClients` → `CodebaseHttpClients`).
- `method` is now `CodebaseHttpMethod` (was `String`).
- `method` is now **mandatory** (was `default ""`). Every `@CodebaseHttpClient` must declare a verb.

### Shared enum (new file)

```java
package com.example.rag;

/**
 * HTTP verbs supported as the `method` field on `@CodebaseHttpRoute`
 * and `@CodebaseHttpClient`.
 *
 * Closed value set — see propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md.
 * Adding a value is a breaking-change amendment to the enum file
 * plus a re-extract of every annotated codebase.
 */
public enum CodebaseHttpMethod {
    GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
}
```

### Call-site change (inbound)

```java
// Before
@CodebaseHttpRoute(path = "/v1/orders/{id}", method = "GET")
public Order getOrder(String id) { ... }

// After
@CodebaseHttpRoute(path = "/v1/orders/{id}", method = CodebaseHttpMethod.GET)
public Order getOrder(String id) { ... }
```

### Call-site change (outbound, Feign)

```java
// Before
@FeignClient(name = "order-service")
public interface OrderApi {
    @GetMapping("/v1/orders/{id}")
    @CodebaseClient(clientKind = CodebaseClientKind.feign_method, targetService = "order-service")
    Order getOrder(@PathVariable String id);
}

// After
@FeignClient(name = "order-service")
public interface OrderApi {
    @GetMapping("/v1/orders/{id}")
    @CodebaseHttpClient(
        clientKind   = CodebaseClientKind.feign_method,
        targetService = "order-service",
        path         = "/v1/orders/{id}",
        method       = CodebaseHttpMethod.GET
    )
    Order getOrder(@PathVariable String id);
}
```

`@GetMapping` and the brownfield annotation both declare the same verb. Per principle 1, **the framework annotation is ignored** by the extractor once `@CodebaseHttpClient` is present on the method. The duplication is the developer's contract with the indexer.

### Call-site change (outbound, RestTemplate)

```java
// Before
@CodebaseClient(
    clientKind = CodebaseClientKind.rest_template,
    targetService = "payment-service",
    path = "/v2/payments",
    method = "POST"
)
public PaymentReceipt charge(PaymentRequest r) {
    return restTemplate.postForObject(paymentBaseUrl + "/v2/payments", r, PaymentReceipt.class);
}

// After
@CodebaseHttpClient(
    clientKind = CodebaseClientKind.rest_template,
    targetService = "payment-service",
    path = "/v2/payments",
    method = CodebaseHttpMethod.POST
)
public PaymentReceipt charge(PaymentRequest r) {
    return restTemplate.postForObject(paymentBaseUrl + "/v2/payments", r, PaymentReceipt.class);
}
```

### Extractor change

`ast_java.py` currently has two relevant call sites:

- Line 152–153 — recognised-annotation simple-name list `{"CodebaseHttpRoute", "CodebaseHttpRoutes"}`. Add `CodebaseHttpClient`/`CodebaseHttpClients` to the existing `CODEBASE_CLIENT_ANNOTATIONS` set (line 157); remove `CodebaseClient`/`CodebaseClients` (no backward-compat alias).
- Line 1467 — `http_method = str(mv).upper() if mk == "enum" else str(mv).strip().upper()`. The `else` branch becomes dead code on the route side and stays alive on the client side until PR-2; after PR-2, both branches require `mk == "enum"`.

A new check fires **during extraction** (`ast_java.py`, per method): when a method carries both a brownfield annotation (`@CodebaseHttpRoute` / `@CodebaseHttpClient`) **and** at least one framework annotation that would normally drive route/client inference on that method (`@GetMapping`, `@FeignClient`, `@RequestMapping`, JAX-RS verbs, etc. — exact set locked in implementation), the extractor logs an INFO-level structured event **`brownfield-exclusivity-shadowing`** listing which framework annotations are bypassed for observability. This trigger is **co-presence on the method**, not “a built-in graph row was dropped” (that can miss Feign-only cases or double-fire if we also logged from `graph_enrich.py`). **`_merge_layer_c_codebase_routes`** only implements merge→replace **behaviour**; it does **not** emit a second INFO for the same concern. Logs remain stderr; operators typically enable verbose graph builds (`build_ast_graph.py --verbose`) to see high-volume diagnostics. Observability only — never blocks extraction.

## §4 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Migrate `@CodebaseAsyncRoute` / `@CodebaseProducer` to channel-name+method-enum | Out of scope. Async surface is already channel-named (`Async*`) and `method` doesn't apply (channels, not verbs). |
| Add a `CodebaseHttpMethod.INHERIT` sentinel for Feign | Violates principle 1. The annotation is the truth, full stop. |
| Add `CodebaseHttpMethod.ANY` / `OTHER` / `CUSTOM` | Defeats the point of an enum. If a real codebase needs `QUERY` or `PURGE`, amend the enum file (additive). |
| Expose the enum at the graph layer | `Route.attrs.http_method` / `Client.attrs.http_method` stay `String`. Wire format is JSON; downstream tooling already strings the value. |
| Change YAML override shape | YAML is independent of in-source annotation shape per v2 lock. |
| Ship as a Maven artifact | Locked in v2. Copy-paste source-stub model continues. |
| Provide a backward-compat alias for `@CodebaseClient` | Defeats the rename. Breaking change accepted. |
| Add a hard parse error when framework introspection is shadowed | Out of scope. INFO-level observability event is enough; agents reading the verbose build log can audit. |
| Validate at compile time that Feign + `@CodebaseHttpClient` use the same verb | Out of scope and **deliberately so** — the brownfield annotation is the truth; the Feign annotation is ignored. They are not required to agree. |
| Introduce an APT / annotation processor pipeline | YAGNI. Plain javac handles enum values fine. |

## §5 — Call-site audit (where does the change land?)

Inventory of every site that names either annotation today, sized for PR-2's blast radius.

| Site | File(s) | Count | Migration |
|---|---|---|---|
| Route source-stub declaration | `tests/fixtures/brownfield_route_stubs/com/example/rag/CodebaseHttpRoute.java` | 1 | `String method()` → `CodebaseHttpMethod method()` |
| Client source-stub declaration | `tests/fixtures/brownfield_client_stubs/com/example/rag/CodebaseClient.java` | 1 | Rename file → `CodebaseHttpClient.java`; rename simple name; `method` → `CodebaseHttpMethod method()` (drop default); update `@Repeatable` target |
| Client plural source-stub | `tests/fixtures/brownfield_client_stubs/com/example/rag/CodebaseClients.java` | 1 | Rename file → `CodebaseHttpClients.java`; update array type |
| New shared enum stub | `tests/fixtures/brownfield_stubs/com/example/rag/CodebaseHttpMethod.java` | 0 → 1 new file | Add enum file; canonical location TBD by repo convention |
| Python extractor — recognised-annotation sets | `ast_java.py` lines 152–153, 157 | 2 sets touched | Add `CodebaseHttpClient`/`CodebaseHttpClients` to client set; remove old names; route set keeps simple names |
| Python extractor — route element parser | `ast_java.py:1458–1500` | 1 branch hardened | Promote `mk == "enum"` to required; emit warning on string literal |
| Python extractor — client element parser | `ast_java.py:_parse_codebase_client_annotation` (≈ line 1540) | Same | Same treatment for client side |
| Python extractor — class-level annotation switch | `ast_java.py:1725–1750` | 2 simple-name checks | `CodebaseClient` / `CodebaseClients` → new names |
| Inline literals in route tests | `tests/test_brownfield_routes.py`, `tests/test_route_extraction.py`, `tests/test_cross_service_resolution_flag.py` | ~10 occurrences | `method = "VERB"` → `method = CodebaseHttpMethod.VERB` |
| Inline literals in client tests | `tests/test_brownfield_clients.py`, `tests/test_client_node_extraction.py`, `tests/test_assign_endpoint_client_extraction.py`, `tests/test_cross_service_resolution_flag.py` | Estimated ~15 occurrences across 4 files | Rename annotation; rewrite `method = "VERB"` literals; verify both `@CodebaseHttpClient` simple-name and `clientKind` enum reference still work |
| `graph_enrich.py` v1-deprecation warning | `graph_enrich.py:1097` mentions `"CodebaseClient"` in a printf message | 1 string | Update to `CodebaseHttpClient` |
| `build_ast_graph.py` user-facing log lines | Survey needed during PR-2; estimated 1–3 mentions | TBD | Rename in log messages |
| AGENT-GUIDE.md brownfield section | `docs/AGENT-GUIDE.md` | 0 mentions of annotation syntax today | PR-3 adds a brownfield-exclusivity callout |
| `java-codebase-explore` skill | `docs/skills/java-codebase-explore.md` | 0 mentions of annotation syntax | PR-3 adds a one-line note to the cheat sheet trailer if relevant |

**Net mechanical edit (PR-2)**: 4 stub files renamed/modified + 1 new enum stub + ~25 inline literal rewrites + extractor recognition-set updates + 2 extractor parser branches hardened.

## §6 — Why this enum, this rename, this principle

Three questions the consistency pass surfaced; each gets a locked answer here.

**Q1: Why share `CodebaseHttpMethod` between route and client instead of two enums?**
Same wire protocol, same value set, same RFC. Diverging the enums would be a fiction — a future change to one would always demand a sympathy change to the other. One file, two consumers.

**Q2: Why rename `Client` → `HttpClient` if `@CodebaseClient` already has `clientKind={feign_method, rest_template, web_client}` to disambiguate?**
The `clientKind` field disambiguates *implementations*; the simple name disambiguates *channels*. Today `@CodebaseClient(clientKind=…)` implicitly assumes HTTP because the only three clientKinds are HTTP frameworks. A future `@CodebaseClient(clientKind=grpc_stub)` or `(clientKind=jms_publisher)` would smuggle non-HTTP semantics through an HTTP-shaped annotation. The rename removes the implicit assumption and makes the channel explicit in the type name, mirroring `@CodebaseHttpRoute` exactly. Grpc / message-queue / event-bus outbound calls — when they're proposed — get their own typed annotations (`@CodebaseGrpcClient`, `@CodebaseMqProducer`), not a sneaky enum extension.

**Q3: Why is the brownfield-exclusivity rule worth a separate locked principle when v2 implicitly behaved this way?**
Because v2 *didn't* explicitly behave this way — and even today the behaviour is **asymmetric across axes** (see Q4). Writing the rule down forces three things: (a) the extractor / resolver stops doing the merge for facets a brownfield annotation declares, on every axis, (b) the agent-facing INFO log surfaces shadowing events for audit, (c) `Route.attrs.strategy = "brownfield-annotation"` becomes a *complete* assertion ("nothing else contributed") rather than a *winning* assertion ("brownfield outvoted everything else").

**Q4: What does "brownfield-exclusivity" actually require us to change in the code today?**
This is the question the v2 doc handwaved. Reading `graph_enrich.py` and `ast_java.py` against the principle, today's behaviour is **not uniform** — it's exclusive on three of the four axes and merge on one:

| Axis | Site | Today's behaviour | Matches principle 7? |
|---|---|---|---|
| Inbound HTTP (routes) | `_merge_layer_c_codebase_routes` in `graph_enrich.py:949–1003` | **Field-by-field merge** onto first same-method built-in HTTP row (`cr.path if cr.path else r.path`, etc.). Built-in framework row keeps facets the brownfield annotation didn't set. | **No — this is the real bug.** |
| Inbound async (routes) | Same function, lines 963–977 | **Replace.** Drops same-method built-in async rows, then appends brownfield rows. | Yes. |
| Outbound HTTP (clients) | `resolve_http_client_for_method` in `graph_enrich.py:1261–1351` | **Replace.** `return brownfield_calls if brownfield_calls else builtin_http` — any brownfield call drops all built-in calls. | Yes. |
| Outbound async (producers) | `resolve_async_producer_for_method`, same shape | **Replace.** Symmetric to outbound HTTP. | Yes. |

So principle 7 implies one concrete **graph** behaviour change in PR-2: rewrite `_merge_layer_c_codebase_routes`'s HTTP branch (currently lines 980–1000) to mirror the async branch above it — drop same-method built-in HTTP rows when a brownfield HTTP route is present, then append brownfield rows. That asymmetry-fix lives in one file, one function, one branch and does **not** by itself require any AST shape change. **Separate PR-2 work** (see §3 and §8): `ast_java.py` rename/enum parsing plus **extractor-time** `brownfield-exclusivity-shadowing` logging and WARN on string `method`.

The parser side is a separate change (see PR-2 in §8): client `method` is currently parsed by `_string_value_atoms` in `ast_java.py:1594` and must be **rewritten** to enum-aware parsing — it has no existing `mk == "enum"` branch to "tighten" the way the route side does.

## §7 — Use-case re-walk

Sixteen realistic scenarios walked against the post-cutover surface.

| # | Use case | Surface exercised | Outcome |
|---|---|---|---|
| UC1 | Spring `@GetMapping("/users")` handler missed by introspector | `@CodebaseHttpRoute(path="/users", method=CodebaseHttpMethod.GET)` | ✅ Compile-time verb check |
| UC2 | Netty handler, bare path + verb | `@CodebaseHttpRoute(path="/foo", method=CodebaseHttpMethod.POST)` | ✅ Identical shape, enum reference |
| UC3 | Multi-path legacy route (v1/v2/v3) on one method | Three repeated `@CodebaseHttpRoute(method=CodebaseHttpMethod.GET)` | ✅ `@Repeatable` unchanged |
| UC4 | Dispatcher: PUT and PATCH on same path | Two `@CodebaseHttpRoute` with `method=CodebaseHttpMethod.{PUT,PATCH}` | ✅ Plural form |
| UC5 | Developer fat-fingers `method = "GETT"` | Won't compile — `CodebaseHttpMethod.GETT` doesn't exist | ✅ javac catches |
| UC6 | Developer writes `method = "get"` lowercase | Won't compile; canonical form forced | ✅ |
| UC7 | Developer copies legacy `method = "TRACE"` | Won't compile — not in enum | ✅ Forces propose-amendment conversation |
| UC8 | Half-migrated codebase — some fixtures still string-typed | Extractor warning fires; route extracted but flagged | ✅ Loud failure mode |
| UC9 | Feign interface with `@FeignClient` + `@GetMapping` + `@CodebaseHttpClient` | Brownfield annotation wins; framework annotations bypassed; INFO `brownfield-exclusivity-shadowing` event logged | ✅ Principle 1 in action; observable in `--verbose` |
| UC10 | Feign developer fat-fingers method (Spring says GET, brownfield says POST) | Brownfield POST is the truth. No diff warning. Discovered only via runtime 405 or eyeballing the log | ⚠️ Accepted cost of principle 1; documented in AGENT-GUIDE.md (PR-3) |
| UC11 | RestTemplate caller, base URL via config | `@CodebaseHttpClient(clientKind=rest_template, targetService=…, path=…, method=CodebaseHttpMethod.POST)` | ✅ |
| UC12 | OkHttp / hand-rolled `HttpClient` call | Same as UC11 with `clientKind=web_client` or new kind | ✅ |
| UC13 | Generated gRPC-gateway stub (today: `clientKind=web_client`) | Same shape, verb enum-typed | ✅ But: gRPC-native may need its own annotation (out of scope; see §4) |
| UC14 | Repository class that secretly does HTTP | `@CodebaseHttpClient` on the method; brownfield-exclusivity ensures the indexer doesn't classify it as JPA-shaped | ✅ |
| UC15 | Multi-endpoint dispatcher (notification: email/sms/push) | `@CodebaseHttpClients` plural with three `@CodebaseHttpClient` entries, each verb enum-typed | ✅ |
| UC16 | Cross-service edge: caller annotates `method=PUT`, callee `@CodebaseHttpRoute` annotates `method=GET` | Resolver compares `.name()` strings; `HTTP_CALLS.attrs.match = "method_mismatch"` (existing logic, unchanged) | ✅ Enum narrows what can be declared, not what is matched |

No use case requires a primitive that doesn't exist. UC10 is the only case where principle 1 produces an arguably worse outcome than v2's merge-with-warning — and the choice to accept it is recorded explicitly in §9 decision #11.

## §8 — Migration plan — 3 PRs

### PR-1: `feat(brownfield): CodebaseHttpMethod enum stub + shadowing log scaffolding`

**Zero behaviour change.** Ships infrastructure that PR-2 will then turn on.

Touches:
- New file: `tests/fixtures/brownfield_route_stubs/com/example/rag/CodebaseHttpMethod.java` (the enum file itself; placement matches the existing split between `brownfield_route_stubs/` and `brownfield_client_stubs/` — the enum is referenced from the route side first, the client side picks it up in PR-2). Optional symlink or duplicate under `brownfield_client_stubs/` if Java compile order requires it; defer to plan-level.
- Structured logging **helpers** in Python (location chosen in the plan to avoid `ast_java` ↔ `build_ast_graph` import cycles — e.g. a tiny `brownfield_events.py` or private helpers in `build_ast_graph.py` with a documented import strategy): a parameterized `_emit_structured_brownfield_event(...)` (or equivalent) capable of emitting **`brownfield-exclusivity-shadowing`** at INFO with stable field names. **No production call sites** in PR-1: nothing in `ast_java.py`, `graph_enrich.py`, or CLI paths invokes the helper except an **optional unit test** that calls it directly. **Do not** add a `--verbose` flag or any behaviour that implies shadowing runs during normal graph builds in PR-1.
- One new test that exercises the helper in isolation (asserts structured log shape: `event=brownfield-exclusivity-shadowing`, `method_fqn=...`, `bypassed_anns=[...]` or the field names the implementation locks).

No `ast_java.py` parser changes. No `graph_enrich.py` resolver changes. No renames. No stub-format changes. CI: ruff + pytest as today; existing suite byte-identical except for the new log-helper test.

### PR-2: `feat(brownfield): rename @CodebaseClient→@CodebaseHttpClient, enum method, exclusivity for HTTP routes`

One atomic commit. The behaviour-change PR — four coordinated changes that must land together because they share fixture rewrites.

**1. Annotation rename + enum migration (source-stub layer):**
- Rename `CodebaseClient.java` → `CodebaseHttpClient.java`, `CodebaseClients.java` → `CodebaseHttpClients.java` under `brownfield_client_stubs/`. Rename annotation simple names inside both files; flip `method` from `String default ""` to `CodebaseHttpMethod` (no default — mandatory per decision #6); update `@Repeatable` target.
- Update inbound stub `CodebaseHttpRoute.java`'s `method` field type from `String` to `CodebaseHttpMethod` (was a route-side enum branch in the v2 doc; route side already parses enum-aware so this is field-type only).

**2. Parser rewrite (`ast_java.py`):**
- `_parse_codebase_client_annotation` (line 1565): replace the `_string_value_atoms` lookup for `method` with the enum-aware path (`_annotation_value(..., mk == "enum"`)). Route parser already does this; mirror it.
- Update `CODEBASE_CLIENT_ANNOTATIONS` frozenset (line 157): drop `{"CodebaseClient", "CodebaseClients"}`, add `{"CodebaseHttpClient", "CodebaseHttpClients"}`.
- Update string-literal switch arms at line 1540 (`n_simple == "CodebaseClient"`), line 1731 (`simple == "CodebaseClient"`), line 1744 (`simple == "CodebaseClients"`).
- Emit structured WARN when a `method` element is encountered with `mk != "enum"` (mid-migration safety per decision #13). Use the **same structured-log machinery** as INFO (shared parameterized `_emit_structured_brownfield_event(...)` or a dedicated thin wrapper) with `event=brownfield-method-string-literal` and severity WARN — **do not** reuse the shadowing-specific wrapper in a way that emits the wrong `event=` key.
- After parsing a method, if it carries `@CodebaseHttpRoute` / `@CodebaseHttpClient` **and** shadowable framework annotations (per §3), emit INFO **`brownfield-exclusivity-shadowing`** once per method via that machinery (UC9).

**3. Inbound-HTTP exclusivity (`graph_enrich.py`):**
- Rewrite `_merge_layer_c_codebase_routes` lines 980–1000 (HTTP branch) to mirror the async branch above it (lines 963–977): when any layer-C HTTP route is present for a method, drop same-method built-in HTTP rows from `merged` *before* appending layer-C rows. Closes the asymmetry documented in §6 Q4.
- **No** INFO `brownfield-exclusivity-shadowing` from this merge path — extractor co-presence (bullet above) is the single trigger so we never double-log the same principle-1 story.
- Update `CodebaseClient` string-keyed dispatch in `meta_chain` walker (lines 1300–1305) to match the renamed annotation. Note: the `meta_chain` dict is keyed by annotation simple name at index time — the indexer rebuild after PR-2 is what makes this consistent. Document in plan.
- Update `graph_enrich.py:1097` printf string (v1-deprecation log).

**4. Test + doc literals:**
- Inline literal rewrites across test files: ~25 sites across the 7 fixture files that reference `@CodebaseClient` / `@CodebaseClients` / string-typed `method="GET"`. README §469 / §506–518 / §536 code examples. `CODEBASE_REQUIREMENTS.md:183`.
- **Pre-merge grep checklist** (run on PR-2 branch, must return only post-rename hits): `grep -rn 'CodebaseClient\b\|CodebaseClients\b\|CODEBASE_CLIENT_ANNOTATIONS' --include='*.py' --include='*.md' --include='*.java'`. Documented in plan.

Tests: existing suite, rewritten fixtures + new route-exclusivity test (a Spring controller method with `@GetMapping("/x")` + `@CodebaseHttpRoute(path="/x")` produces exactly one route row with `source_layer="layer_c_source"`, no `framework="spring-mvc"` row). No new test files for the rename; existing literal rewrites cover it.

CI: ruff + pytest as today.

### PR-3: `docs(brownfield): document HttpClient rename, method enum, and brownfield-exclusivity rule`

Touches:
- `docs/AGENT-GUIDE.md` — new short subsection under brownfield-annotation guidance: "Brownfield-exclusivity: when you annotate, you assert. Framework introspection is bypassed for that facet on that method. UC10 caveat documented here."
- `docs/skills/java-codebase-explore.md` — if the cheat sheet trailer or anti-capabilities mention either annotation, update names; otherwise zero change
- `propose/completed/BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md` — addendum file `propose/completed/BROWNFIELD-ANNOTATIONS-V2-ADDENDUM-HTTP-METHOD-ENUM.md` cross-referencing this propose (per repo convention that completed proposes are immutable)

No code change. CI: docs only.

## §9 — Decisions taken (no longer open)

1. **Value set is exactly the seven verbs `GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS`.** No `TRACE`, no `CONNECT`, no `ANY`, no `OTHER`, no `INHERIT`.
2. **Enum name is `CodebaseHttpMethod`.** Avoids collision with Spring's `org.springframework.http.HttpMethod` and JAX-RS's `jakarta.ws.rs.HttpMethod`.
3. **Enum values are bare uppercase verb names.** No `HTTP_` prefix, no `_METHOD` suffix.
4. **Shared between `@CodebaseHttpRoute` and `@CodebaseHttpClient`.** Single enum, two consumers.
5. **`@CodebaseClient` → `@CodebaseHttpClient` rename. No backward-compat alias.** `@CodebaseClients` → `@CodebaseHttpClients` for the plural.
6. **`method` is mandatory on `@CodebaseHttpClient`.** No default. Every client annotation declares a verb regardless of `clientKind`.
7. **Brownfield-exclusivity principle.** When a brownfield annotation is present on a method, framework introspection of the facets the annotation declares is skipped entirely for that method.
8. **Shadowing is observability, not validation.** INFO-level structured log `brownfield-exclusivity-shadowing` lists which framework annotations are bypassed when a brownfield HTTP route/client annotation **co-exists** on the method with framework annotations that would otherwise drive inference (extractor-time; §3). It is **not** tied to whether a separate built-in Kuzu row existed. No warnings on disagreement (UC10), no compile-time enforcement. *This is a distinct event from decision #13's WARN — see decision #18 for the severity-by-event matrix.*
9. **Feign duplication accepted.** A Feign interface method with `@GetMapping("/x")` + `@CodebaseHttpClient(path="/x", method=CodebaseHttpMethod.GET, …)` is the canonical form. The duplication is the price of principle 7.
10. **Breaking change accepted, no migration path, no soft-deprecation.** PR-2 is one atomic commit.
11. **`Route.attrs.http_method` and `Client.attrs.http_method` stay `String`** (the enum's `.name()`). Wire format unchanged for all agent-facing consumers.
12. **YAML override `route_overrides.method` / `http_client_overrides.method` stay `String`.** YAML shape is independent of source-annotation shape per v2 lock.
13. **Parser rewrite, not enum-branch tightening.** `_parse_codebase_client_annotation` is rewritten from `_string_value_atoms` (today's path) to enum-aware parsing; the route side already does this and is field-type-flip only. Extractor emits a structured WARN on string-typed `method` literals (legacy fixtures mid-migration), not a hard parse error — mid-migration codebase still produces a graph.
14. **No annotation-processor / APT pipeline introduced.** Plain javac handles enum values.
15. **No alias support.** `Method.Get` does not exist; only `CodebaseHttpMethod.GET`.
16. **Future verb additions are propose-amendments to the enum file.** Not parameterised via a `CUSTOM("query")` escape hatch.
17. **Future non-HTTP outbound channels get their own typed annotations** (`@CodebaseGrpcClient`, `@CodebaseMqProducer`, etc.), not enum extensions inside `@CodebaseHttpClient`.
18. **Severity-by-event matrix.** Two structured events, two deliberately different severities:
    - `brownfield-exclusivity-shadowing` — **INFO**. Fires in **`ast_java.py`** when a method has brownfield HTTP annotations **and** shadowable framework annotations on the same method (co-presence). Agent-facing audit signal; not actionable at compile time. Decision #8. **Not** emitted from `_merge_layer_c_codebase_routes` (avoids duplicate / narrower merge-only triggers).
    - `brownfield-method-string-literal` — **WARN**. Fires when the parser sees `method="GET"` (legacy string form) instead of `method=CodebaseHttpMethod.GET`. Mid-migration safety net; the parse still produces a row. Decision #13.
    No third level. No collapsing to a single event. Both events may share one **parameterized** structured-log helper distinguished by `event=` and severity; they must not share a single hard-coded `event=` wrapper.
19. **Inbound-HTTP merge → replace is a real behaviour change in PR-2.** `_merge_layer_c_codebase_routes`'s HTTP branch (lines 980–1000) is rewritten to mirror its own async branch (lines 963–977): drop same-method built-in HTTP rows before appending layer-C rows. This is the *only* axis where principle 7 was being violated; the other three (inbound async, outbound HTTP, outbound async) already match principle 7 and are unchanged. §6 Q4 documents the asymmetry.
20. **PR-1 ships zero behaviour change.** Enum stub + structured-log helper only; not wired to call sites. PR-2 is the atomic behaviour PR. Avoids the v2-doc trap of a "foundation PR" that ambiguously claims to ship a guard for behaviour PR-2 actually introduces.

## §10 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| UC10 / Feign-verb-disagreement is silent — developer changes `@GetMapping` to `@PostMapping` but forgets the brownfield annotation; runtime 405 in production | INFO `brownfield-exclusivity-shadowing` log surfaces bypassed framework annotations when brownfield and framework annotations **co-exist** on the method (extractor; gate with verbose graph build). AGENT-GUIDE.md (PR-3) documents the principle and the inspection workflow. Real defence is the agent-facing audit, not compile-time. |
| Half-migrated codebases (PR-2 reverts partially via rebase mistake; a fixture file still has string literals or old annotation name) | Extractor WARN on string-typed `method` field; extractor IGNORES old `@CodebaseClient` simple name (no fallback) so half-migrated test files fail loudly at extraction time. |
| A real-world codebase indexes `@CodebaseHttpClient(method="TRACE")` style legacy code | Won't compile after PR-2 ships. Forces explicit propose-amendment conversation. |
| Cross-service edge breaks because caller/callee disagree on enum-vs-string method comparison | Wire format unchanged: `attrs.http_method` is `.name()` string after extraction. Resolver compares strings, identical to today. |
| Developer pastes a verb from external source (`"GET"`) and trips on compile error | Strictly desirable — the verb came from an unvalidated source; the enum is the validation. |
| Three new source-stub files (rename creates new files; copy-paste discipline broken in a downstream codebase that copied two of five) | Documentation in AGENT-GUIDE.md (PR-3) lists all five HTTP stubs as a unit. Source-stub model has always required copy-paste discipline. |
| Future `@CodebaseGrpcClient` proposal collides with `@CodebaseHttpClient` semantics | Channel-naming convention (principle 3) gives each channel its own typed annotation. No collision. |
| Existing v1-deprecation log string `"CodebaseClient"` in `graph_enrich.py:1097` becomes stale | Updated in PR-2 alongside the rename. |
| **Rename blast radius miss — stale `"CodebaseClient"` literal slips through PR-2.** Particular danger: `graph_enrich.py:1300–1305` does **string-keyed dispatch** on the literal `"CodebaseClient"` in the `meta_chain` walker (runtime data-flow, not just a comment). A stale literal here silently breaks meta-annotation closure for the renamed annotation. | Pre-merge grep checklist in PR-2 (§8): `grep -rn 'CodebaseClient\b\|CodebaseClients\b\|CODEBASE_CLIENT_ANNOTATIONS' --include='*.py' --include='*.md' --include='*.java'`. Must return only post-rename hits before PR-2 is merged. Reviewer of PR-2 runs the same grep on the branch. |
| **`meta_chain` dict keyed by old annotation name after partial rebuild.** `meta_chain` is built at index time from annotation simple names; if an operator runs PR-2 code against a graph indexed with PR-1 stubs, the dispatch at `graph_enrich.py:1300–1305` won't match anything. | Plan-level: PR-2 release note mandates a reindex (`java-codebase-rag init` or `reprocess`). No code-level migration; index files are not a public format. |
| Annotation processors (Lombok, MapStruct, custom APT) breaking on enum-typed values | Plain `javac` annotation processing handles enums fine; verified by precedent on `clientKind` and `producerKind` in v2. |

## Appendix A — Concrete enum source

```java
package com.example.rag;

/**
 * HTTP verbs supported as the `method` field on
 * {@code @CodebaseHttpRoute} and {@code @CodebaseHttpClient}.
 *
 * Closed value set — see propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md.
 * Adding a value is a breaking-change amendment to the enum file
 * plus a re-extract of every annotated codebase.
 *
 * Used identically by inbound (route) and outbound (client) HTTP
 * annotations; the value set is the same on both sides because
 * the wire protocol is the same.
 */
public enum CodebaseHttpMethod {
    GET,
    POST,
    PUT,
    PATCH,
    DELETE,
    HEAD,
    OPTIONS
}
```

## Appendix B — What changed (traceability)

**v1 → v2 (this revision):**

| Change | Why |
|---|---|
| Added: rename `@CodebaseClient` → `@CodebaseHttpClient` | User feedback: closes the channel-naming asymmetry vs `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`. Future-proofs for non-HTTP outbound channels. |
| Added: enum-type `method` on `@CodebaseHttpClient` too (was: only inbound) | User feedback: outbound `method` should be enum-typed for the same reasons as inbound. |
| Added: principle 1 (brownfield-exclusivity) as a lifted, locked principle | User instruction: "There's no framework inspections if brownfield annotation is applied. Period." This is the unstated rule v2 implicitly half-followed; writing it down forces extractor + observability + documentation alignment. |
| Added: `method` is mandatory on `@CodebaseHttpClient` (no default) | Direct consequence of principle 1 — every annotation is a complete assertion, no inherited defaults. User confirmation: "Require method on all clients". |
| Added: shared `CodebaseHttpMethod` enum used by both annotations | Symmetric design; same wire protocol implies same value set. |
| Added: §6 Q1–Q3 design-question section | Forces the three non-obvious choices (shared enum, rename rationale, why brownfield-exclusivity needs to be a named principle) to be answered explicitly. |
| Added: UC9, UC10, UC15 use cases | Principle 1's Feign-shadowing case, its silent-disagreement consequence, and the outbound dispatcher pattern. |
| Bumped: 2 PRs → 3 PRs | PR-2 now carries both the enum migration and the rename in one atomic commit; PR-3 carries the docs. PR-1 stays the foundation. |

**What stayed unchanged from v1 of this propose:**
- Value set (seven verbs, no escape hatch)
- Wire-format invariance (`Route.attrs.http_method` stays `String`)
- YAML-override invariance
- Source-stub copy-paste integration model
- Breaking-change posture (no migration path, no soft-deprecation)
- v2 propose's locked decisions on `clientKind` and `producerKind` enums (untouched)

---
**v2 → v3 (this revision — PR-85 review response):**

| Change | Why |
|---|---|
| Added: §6 Q4 "inbound vs outbound exclusivity asymmetry table" — four-axis matrix showing today's behaviour | Reviewer point 2: "HTTP inbound exclusivity is not already implicit. `_merge_layer_c_codebase_routes` **merges** layer C onto built-in HTTP. That's supplement, not replace." Audit confirmed: async, outbound HTTP, outbound async ALL replace (line 968–977, 1351, symmetric). Only inbound HTTP merges. Q4 names this as the real change PR-2 must make. |
| Restructured: PR-1 ships zero behaviour change (enum stub + structured-log helper only). PR-2 is the atomic behaviour PR (rename + parser rewrite + merge→replace + WARN). | Reviewer point 6: PR-1's old description ambiguously claimed to ship a "guard" for behaviour PR-2 actually introduces. Locked as decision #20. |
| Rewrote: PR-2 parser bullets explicitly call client `method` parsing a **rewrite**, not a "tightening" of an existing enum branch | Reviewer point 1: `_parse_codebase_client_annotation` at `ast_java.py:1594` uses `_string_value_atoms`, not `mk == "enum"`. There is no enum branch to tighten on the client side. Decision #13 reworded. |
| Rewrote: decision #19 — inbound-HTTP merge → replace is a real behaviour change in PR-2 | Locks the asymmetry-fix to one site (`_merge_layer_c_codebase_routes` HTTP branch, lines 980–1000), mirroring the async branch above it. Graph-only: extractor still changes for rename/enum/shadowing logging, but the merge asymmetry fix itself is one function. |
| Added: decision #18 severity-by-event matrix | Reviewer point 3: #8 (INFO shadowing) and #13 (WARN string-method) are two distinct events with deliberately different severities. Doc was internally consistent but invited misreading. Explicit matrix removes ambiguity. |
| Renamed: shadowing event `brownfield-exclusivity-shadowing` (INFO) vs string-method event `brownfield-method-string-literal` (WARN) named separately | Same as above. Two named events, two severities, no overlap. |
| Fixed: PR-1 stub path `tests/fixtures/brownfield_stubs/...` → `tests/fixtures/brownfield_route_stubs/...` | Reviewer point 4: actual repo split is `brownfield_route_stubs/` + `brownfield_client_stubs/`. |
| Added: pre-merge grep checklist in PR-2 + rename-blast-radius risk row in §10 | Reviewer point 5: rename touches `CODEBASE_CLIENT_ANNOTATIONS` (`ast_java.py:157`), string-literal switches at `ast_java.py:1540/1731/1744`, **runtime data-flow at `graph_enrich.py:1300–1305`** (string-keyed `meta_chain` dispatch — most dangerous), `graph_enrich.py:1097` log string, README §469/§506–518/§536, CODEBASE_REQUIREMENTS.md:183. Grep checklist + dedicated risk row + `meta_chain`-reindex risk row catch all of them. |
| Added: decision #20 locking PR-1's zero-behaviour-change posture | Same intent as the restructure above; gives implementer one numbered rule to reject any drift back toward a "foundation PR with a guard." |
| Bumped: 17 → 20 locked decisions | Three new (#18, #19, #20). #8 and #13 reworded but kept their numbers for stability. |
| Rebased: branch onto current `master` (PR #87 merged in between) | Reviewer meta: clean two-dot diffs. |

**What stayed unchanged from v2 of this propose:**
- Value set (seven verbs, no escape hatch)
- Wire-format invariance
- YAML-override invariance
- Migration shape (3 PRs total)
- 16 use cases (UC1–UC16) — all still walk against the post-cutover surface; UC10 is still the only "principle 1 produces a worse outcome than v2" case and still mitigated by INFO-only audit
- Brownfield-exclusivity principle as the spine of the doc (TL;DR / §1 Frame / §2 Principle #1 / §6 Q3–Q4 / §9 #7)
- Feign verb duplication accepted as price of principle 7
- Five HTTP source stubs (`CodebaseHttpRoute`, `CodebaseHttpRoutes`, `CodebaseHttpClient`, `CodebaseHttpClients`, `CodebaseHttpMethod`)

---
**v3 → v4 (doc alignment — shadowing trigger + PR-1 + WARN emitter):**

| Change | Why |
|---|---|
| §3 / §8 / #8 / #18: **`brownfield-exclusivity-shadowing` fires at extractor co-presence** (`ast_java.py`); `_merge_layer_c_codebase_routes` does **merge→replace only**, no duplicate INFO from graph merge | Plan vs propose had diverged (merge-drop vs co-presence); UC9 needs Feign + brownfield without requiring a dropped built-in row. |
| §8 PR-1: drop “`--verbose` calls the helper”; helpers are test-only invocation in PR-1 | Avoid implying PR-1 ships runtime shadowing. |
| §8 PR-2 WARN: shared **parameterized** structured emitter (or two thin wrappers), not wrong `event=` reuse | Decision #18 stays two distinct events. |
| §6 Q4 closing paragraph | Clarify graph-only merge fix vs separate `ast_java.py` work. |
