# Deferred `FEIGN_CLIENT` → `REST_CLIENT` migration — proposal

Status: **not implemented, intentionally deferred**. This document captures
the agreed direction so the next iteration can pick it up coherently with
the cross-service tracing work that gates it.

## Why this is needed

The current role taxonomy in `ast_java.py` recognises only `@FeignClient` as
an outbound HTTP integration:

```python
ROLE_ANNOTATIONS = {
    ...
    "FeignClient": "FEIGN_CLIENT",
    ...
}
```

The role name is tied to one specific Spring Cloud annotation. From an
end-user point of view this is leaky: the question being answered (in
`trace_flow`, `_ROLE_SCORE_WEIGHTS`, integration-stage seeding) is *"is this
a remote-service client?"*, not *"which library generated the proxy?"*. As
soon as the codebase mixes Feign with `@HttpExchange` (Spring 6 declarative
HTTP), Retrofit, raw `RestTemplate` / `WebClient` / `RestClient` (Spring
6.1+), or hand-rolled `OkHttpClient` callers, every one of those edges
becomes invisible to the integration stage of `trace_flow`.

For cross-service tracing this is not a cosmetic gap — **a missing edge
silently breaks the chain**. Spurious edges can be filtered by the consumer;
missing edges cannot be recovered.

## Why this is deferred (and not implemented now)

The right scope of `REST_CLIENT` is determined by what **cross-service
tracing** actually needs to consume. Until that consumer exists we can
only guess at the right detector aggressiveness. Concretely:

- The annotation-driven detectors (`@FeignClient`, `@HttpExchange`,
  `@RetrofitClient`) are zero-false-positive and cheap. They are safe to
  ship at any time.
- The injection-driven detectors (`RestTemplate`, `WebClient`, `RestClient`,
  `OkHttpClient`, JDK `HttpClient`) are necessary for completeness but
  produce noise: any `@Service` that makes one outbound call gets pulled
  into the integration stage, displacing genuine orchestrators.

Without a cross-service tracer to validate against, we cannot tell whether
the noisy detectors help or hurt. Shipping them speculatively would
destabilise current behavioural-search ranking for users who do *not* yet
have a tracer.

The capabilities model (see `plans/PLAN-CAPABILITIES-MODEL.md`) resolves
the noise problem cleanly: keep `role = SERVICE` for the orchestrator,
add `REST_CLIENT` as a **capability** so cross-service tracing can find
the edge without the role-bias table downgrading the class.
**`REST_CLIENT` should therefore be implemented as a capability, not a
role rename, when the time comes.**

## Migration shape (when picked up)

### Pre-conditions (must be true before starting)

1. The capabilities model from `plans/PLAN-CAPABILITIES-MODEL.md` is
   merged. `TypeDecl.capabilities`, `Symbol.capabilities` (Kuzu),
   `SymbolHit.capabilities`, `list_by_capability` MCP tool all exist.
2. A cross-service tracing consumer exists (or is being implemented in the
   same change set) that demonstrably uses `REST_CLIENT` as input.

### Detector set

Two tiers, gated independently:

#### Tier 1 — annotation-driven (always safe, ship first)

| Annotation | Source | Notes |
|---|---|---|
| `@FeignClient` | Spring Cloud OpenFeign | already detected today |
| `@HttpExchange` | Spring 6 declarative HTTP | type-level on the interface |
| `@RetrofitClient` | spring-boot-starter-retrofit | community starter |

#### Tier 2 — injection-driven (gated behind a flag, validate before defaulting on)

A type acquires the `REST_CLIENT` capability if it injects (field, ctor
param, or Lombok-RAC final field) any of:

| Type | Library |
|---|---|
| `RestTemplate` | Spring Web |
| `WebClient` | Spring WebFlux |
| `RestClient` | Spring Web 6.1+ |
| `OkHttpClient` | Square OkHttp |
| `HttpClient` | JDK 11+ `java.net.http` |

Tier 2 is opt-in via the brownfield config (see
`plans/PLAN-BROWNFIELD-ROLE-OVERRIDES.md`):

```yaml
rest_client_detection:
  injection_based: true   # default false until cross-service tracing validated
```

### Decision: `REST_CLIENT` as **capability**, not a role rename

Do not rename the `FEIGN_CLIENT` role to `REST_CLIENT`. Instead:

1. Stop emitting `FEIGN_CLIENT` as a primary role (i.e. remove the
   `"FeignClient": "FEIGN_CLIENT"` entry from `ROLE_ANNOTATIONS`).
2. Emit `REST_CLIENT` as a **capability** populated by the detectors above.
3. The primary role for a `@FeignClient` interface falls back to its
   stereotype if any (`@Component`, etc.) or `OTHER`. Most `@FeignClient`s
   are interfaces with no other annotation, so `OTHER + capability=REST_CLIENT`
   is the typical resulting tag set.

Why capability rather than rename:

- A `@Service` that injects `WebClient` is genuinely both a service and a
  REST client. Forcing one role discards information.
- The `_ROLE_SCORE_WEIGHTS` table in `search_lancedb.py` tunes ranking on a
  *single* axis. Treating a service-with-outbound-calls as `REST_CLIENT`
  would silently demote it from `0.08` → `0.06`. That is wrong for
  behavioural search.
- Multiple capabilities compose naturally: a `@FeignClient` interface that
  is also `@CircuitBreaker`-wrapped at the type level can carry both
  `REST_CLIENT` and (future) `RESILIENCE_PATTERN` capabilities.

## Call sites to update

| File | Change |
|---|---|
| `ast_java.py` | Drop `FeignClient` from `ROLE_ANNOTATIONS`. Add `REST_CLIENT` to the capability detector tables (annotation set + injection-type set). |
| `search_lancedb.py` | Drop `"FEIGN_CLIENT": 0.06` from `_ROLE_SCORE_WEIGHTS`. Decide whether `REST_CLIENT` capability gets a *capability-bias* (parallel table) — recommend **no** for now; let cross-service tracing surface them via explicit capability filter rather than ranking nudge. |
| `kuzu_queries.py` | Replace `FEIGN_CLIENT` in `_FLOW_STAGES[2]` with a capability-aware predicate: `(s.role IN ['REPOSITORY','MAPPER'] OR 'REST_CLIENT' IN s.capabilities)`. Same for `_ENTRYPOINT_ROLES` if `REST_CLIENT` proxies are ever entrypoints (rare — usually they are *called from* services). |
| `server.py` | Remove `FEIGN_CLIENT` from the `role` enum strings in `codebase_search`, `list_by_role`, and the `trace_flow` docstring. Update mention of "CONTROLLER → SERVICE → REPOSITORY/FEIGN" to "CONTROLLER → SERVICE → REPOSITORY/REST_CLIENT". |
| `README.md`, `CODEBASE_REQUIREMENTS.md` | Doc sweep. |
| `tests/test_lancedb_e2e.py` | Update any assertions that key on `FEIGN_CLIENT`. |

## Ontology version

This is a breaking schema change for the Kuzu graph (existing rows have
`role = "FEIGN_CLIENT"` that no longer maps cleanly). Bump
`ONTOLOGY_VERSION` (will already be `3` after the capabilities plan; this
change bumps it to `4`).

The graph **must** be rebuilt; no online migration is provided. This is
acceptable per project policy ("breaking changes are allowed").

## Test plan

1. **Unit (annotation tier).** Fixture file with a `@FeignClient` interface,
   an `@HttpExchange` interface, and a plain interface. Assert:
   - both annotated interfaces have `REST_CLIENT` in `capabilities`
   - neither has `role = "FEIGN_CLIENT"` (the role is gone)
   - the plain interface has no `REST_CLIENT` capability.

2. **Unit (injection tier, when enabled).** Fixture `@Service` with a
   `WebClient` field. Assert `role = "SERVICE"`, `capabilities` contains
   `REST_CLIENT`. With injection-tier disabled (default), the same fixture
   yields `capabilities = []`.

3. **End-to-end.** Reuse the e2e test pattern in `tests/test_lancedb_e2e.py`.
   Add a synthetic two-service fixture where service A's `@Service`
   injects a `@FeignClient` for service B. Assert that:
   - `list_by_capability("REST_CLIENT", microservice="service-a")` returns
     the Feign interface.
   - `trace_flow("...", microservice="service-a")` reaches the Feign
     interface in stage 2.

4. **Regression.** All current `FEIGN_CLIENT`-keyed e2e assertions must
   keep passing under the new spelling (`REST_CLIENT` capability).

## Out of scope

- Inbound HTTP detection beyond `@RestController` / `@Controller` (covered by
  existing primary roles).
- gRPC clients — separate `GRPC_CLIENT` capability if the codebase warrants it.
- Service-mesh-injected clients (Istio sidecars, etc.) — invisible to the AST.
- Cross-service tracing itself — that is the *consumer* of this change and
  has its own design.

## Decision log

- 2026-04-26: Agreed to defer until cross-service tracing exists. Agreed
  `REST_CLIENT` is a capability, not a role rename. Recorded in this file.
