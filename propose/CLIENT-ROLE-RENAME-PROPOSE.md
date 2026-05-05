# Propose: `FEIGN_CLIENT` role → `CLIENT` role + `HTTP_CLIENT` capability

**Status:** Draft (2026-05-06)
**Author:** Dmitry (with Computer)
**Companion docs:**
- `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` (PR #25, merged) — orthogonal
- `propose/CROSS-SERVICE-RESOLUTION-FLAG-PROPOSE.md` (PR #26, merged) — orthogonal
- `plans/PLAN-FEIGN-NOT-AN-EXPOSER.md` — implements PR-F1 (must ship before this)
- `plans/PLAN-CROSS-SERVICE-RESOLUTION-FLAG.md` — implements PR-G1 (must ship before this)

**Supersedes:** `propose/DEFERRED-REST-CLIENT-MIGRATION-PROPOSE.md`
(deleted in this branch — its 2026-04-26 conclusion that
"`REST_CLIENT` should be a capability, not a role rename" is reversed
by this proposal because the architecture has moved on: capabilities
already exist, brownfield overrides already exist, and the
`@CodebaseRole` annotation already lets users assign primary roles
honestly).

## TL;DR

Rename the role enum value `FEIGN_CLIENT` → `CLIENT` and add a new
capability `HTTP_CLIENT`. When `@FeignClient` is detected, emit role
`CLIENT` AND capability `HTTP_CLIENT` (instead of just role
`FEIGN_CLIENT`).

Keep auto-promotion **scoped exactly as today** — only `@FeignClient`
interfaces are auto-promoted to `CLIENT` role. RestTemplate-using
classes, WebClient-using classes, and other HTTP outbound patterns
keep their existing role (typically `SERVICE` or `COMPONENT`) and can
opt into `CLIENT` via brownfield annotations (`@CodebaseRole(role="CLIENT")`
+ `@CodebaseCapability(capability="HTTP_CLIENT")`) on a case-by-case
basis.

The motivation is **annotation honesty for brownfield**. Today, a user
who wants to mark a `RestTemplate`-using class as an HTTP client must
annotate it `@CodebaseRole(role="FEIGN_CLIENT")` — which lies (the
class is not a Feign client). After this change, the same user
annotates `@CodebaseRole(role="CLIENT") @CodebaseCapability(capability="HTTP_CLIENT")`
and the annotation is true.

`KAFKA_PRODUCER`/`RABBIT_PRODUCER`/`JMS_PRODUCER`/`STREAM_PRODUCER`
**already exist** today as a unified `MESSAGE_PRODUCER` capability
(`ast_java.py:117-122`), auto-detected from injected types. No async
rename is needed.

~120 LOC. Ontology bump (likely 8 → 9, depending on PR-G1's bump
landing first). Two-phase deprecation: `FEIGN_CLIENT` accepted in
brownfield YAML as an alias for `CLIENT+HTTP_CLIENT` for one release,
with a deprecation warning. Dropped in the release after.

## 1. Why this is the right shape

### 1.1 The annotation-honesty problem (brownfield)

Today's `ROLE_ANNOTATIONS` (`ast_java.py:77-92`):

```python
ROLE_ANNOTATIONS: dict[str, str] = {
    "RestController": "CONTROLLER",
    "Controller": "CONTROLLER",
    "Service": "SERVICE",
    "Repository": "REPOSITORY",
    "Component": "COMPONENT",
    "Configuration": "CONFIG",
    "Entity": "ENTITY",
    "MappedSuperclass": "ENTITY",
    "Embeddable": "ENTITY",
    "FeignClient": "FEIGN_CLIENT",   # ← the leak
    "Mapper": "MAPPER",
}
```

`FEIGN_CLIENT` is **library-specific**. Every other role describes an
architectural concept (service, repository, controller, entity) that
is independent of the library. A `@FeignClient` interface is, at the
architectural level, an **HTTP client** that happens to be implemented
via Spring Cloud OpenFeign.

This causes a real problem in brownfield codebases. When a user has a
`RestTemplate`-using class that they want to mark as an HTTP client —
to make it visible to "list all HTTP clients in svc-a"-style queries —
the only way today is:

```java
@CodebaseRole(role = "FEIGN_CLIENT")  // ← lie
@Service
class UserApiClient {
    private final RestTemplate restTemplate;
    // calls user-svc over HTTP
}
```

The annotation literally says "this is a Feign client" when it is
not. For a brownfield system whose entire value proposition is
**making the codebase legible to humans and AI agents**, the role
label is a public contract. Lying in annotations is a smell that
propagates: code reviewers see `FEIGN_CLIENT` and look for a
`@FeignClient` interface that doesn't exist.

### 1.2 What the rename buys

With `CLIENT` as the role name + `HTTP_CLIENT` as the capability:

```java
@CodebaseRole(role = "CLIENT")
@CodebaseCapability(capability = "HTTP_CLIENT")
@Service
class UserApiClient {
    private final RestTemplate restTemplate;
}
```

Now the annotation is honest: "this class plays the **client** role,
with **HTTP** as the protocol." A future async client gets the same
treatment with `@CodebaseCapability(capability="MESSAGE_PRODUCER")` —
a capability that already exists in the system.

### 1.3 What the rename does NOT buy (be honest about scope)

The role label is **almost never** consulted in resolution paths:

- **Cross-service matcher** (`build_ast_graph.py:1620-1648`): reads
  `route.kind` and `OutgoingCall.client_kind`, not role
- **PR #25's EXPOSES gate**: reads `route.kind`, not role
- **PR-G1's brownfield_only flag**: reads `route_source_layer` and
  `resolution_strategy`, not role
- **Outgoing call extraction** (`ast_java.py:1818, 1901`): reads
  injected type names (`RestTemplate`, `WebClient`), not role

The role label IS consulted in:

- **`search_lancedb.py:188`** `_ROLE_SCORE_WEIGHTS["FEIGN_CLIENT"] = 0.06`
  (ranking nudge for behavioural search)
- **`kuzu_queries.py:956`** `_FLOW_STAGES[2]` includes `FEIGN_CLIENT`
  (trace_flow integration stage)
- **`server.py:1415`** `entry_roles = [..., "FEIGN_CLIENT"]`
  (codebase_search entry-role filter)
- **MCP tool `role` enum strings** in `server.py:687, 1138`

These are surface-level — a rename is mechanical, not architectural.

So **be clear about what this proposal is**: it is a vocabulary
cleanup that makes brownfield annotations honest. It is **not** a
new resolution mechanism, **not** a richer cross-service edge model,
**not** auto-promotion of more class types.

## 2. Goals

- **G1.** Rename `FEIGN_CLIENT` → `CLIENT` in `ROLE_ANNOTATIONS`
  (`ast_java.py:90`).
- **G2.** When `@FeignClient` is detected, emit role `CLIENT` AND
  capability `HTTP_CLIENT`. The capability is added to a new
  `_TYPE_ANN_TO_CAPABILITY` entry (`ast_java.py:111-114`) — same
  detector path as `EXCEPTION_HANDLER`.
- **G3.** All call sites that key on `FEIGN_CLIENT` literal switch
  to `CLIENT`. List in §5.
- **G4.** Brownfield YAML and `@CodebaseRole`/`@CodebaseCapability`
  annotations accept the new vocabulary natively. Both `CLIENT`
  (new) and `FEIGN_CLIENT` (alias) accepted for one release;
  `FEIGN_CLIENT` warns "deprecated, use CLIENT+HTTP_CLIENT".
- **G5.** Ontology bump (7→8 if PR-G1 hasn't landed; 8→9 if it has).
- **G6.** No change to auto-promotion scope. RestTemplate-using
  and WebClient-using classes keep their existing role.
- **G7.** `MESSAGE_PRODUCER` capability already exists and covers
  Kafka/Rabbit/JMS/Stream/event publishers. **No change** to
  async detection.

## 3. Non-goals

- **NG1.** Auto-promoting `RestTemplate`/`WebClient`/`RestClient`
  users to `CLIENT` role. Deferred until after real-project test
  shows it's needed.
- **NG2.** Auto-promoting `KafkaTemplate`-injecting classes to
  `CLIENT` role. They already get capability `MESSAGE_PRODUCER`
  via the existing `_INJECTED_TYPES_TO_CAPABILITY` table.
- **NG3.** Removing `FEIGN_CLIENT` as a brownfield-input alias in
  v1. Deprecation is two-phase to avoid breaking existing YAML.
- **NG4.** Changing `CONTROLLER` to be the inbound counterpart of
  `CLIENT` (i.e., extending `CONTROLLER` to async listeners).
  Separate proposal (parked).
- **NG5.** Changing how `@FeignClient` is **routed** (`kind=http_consumer`
  Route emission unchanged, EXPOSES gate from PR-F1 unchanged,
  cross-service matcher unchanged).
- **NG6.** Adding `HTTP_CLIENT` as a sub-classifier under role
  `CLIENT` for downstream queries to distinguish protocol. The
  capability already does this.

## 4. Current state (verified 2026-05-06)

### 4.1 Role detection today

| Class signal | Role assigned today | Where |
|---|---|---|
| `@FeignClient interface X` | `FEIGN_CLIENT` | `ast_java.py:90` |
| `@RestController class X` | `CONTROLLER` | `ast_java.py:79` |
| `class X { RestTemplate rt; }` | `SERVICE` (or whatever stereotype) | unchanged by RestTemplate injection |
| `class X { KafkaTemplate kt; }` | `SERVICE` (or whatever stereotype) + capability `MESSAGE_PRODUCER` | `ast_java.py:117` |
| `class X { WebClient wc; }` | `SERVICE` (or whatever stereotype) | unchanged by WebClient injection |

### 4.2 Capability detection today (verified)

`MESSAGE_PRODUCER` already exists and is auto-detected from injected
types (`ast_java.py:116-122`):

```python
_INJECTED_TYPES_TO_CAPABILITY: dict[str, str] = {
    "KafkaTemplate":             "MESSAGE_PRODUCER",
    "RabbitTemplate":            "MESSAGE_PRODUCER",
    "JmsTemplate":               "MESSAGE_PRODUCER",
    "StreamBridge":              "MESSAGE_PRODUCER",
    "ApplicationEventPublisher": "MESSAGE_PRODUCER",
}
```

`MESSAGE_LISTENER` covers the consumer side (`ast_java.py:101-107`)
via method-level annotations. So the async picture is **already
complete and unified** — no work needed there.

`EXCEPTION_HANDLER` is the existing example of a type-annotation-driven
capability (`ast_java.py:111-114`), and is the pattern this proposal
mirrors for `HTTP_CLIENT`.

### 4.3 Where `FEIGN_CLIENT` literal is referenced

Verified count: **5 production files, ~12 references**:

| File | Lines | Purpose |
|---|---|---|
| `ast_java.py` | 90 | `ROLE_ANNOTATIONS` — the source |
| `kuzu_queries.py` | 956, 965, 975 | `_FLOW_STAGES[2]` integration tier; `_ENTRYPOINT_ROLES`; `trace_flow` docstring |
| `search_lancedb.py` | 188 | `_ROLE_SCORE_WEIGHTS["FEIGN_CLIENT"] = 0.06` |
| `server.py` | 49, 687, 1138, 1335, 1339, 1415 | MCP tool docstrings, `role` enum strings, entry-role filter |
| `tests/test_lancedb_e2e.py` | 342 | One assertion |

Plus docs: `README.md`, `CODEBASE_REQUIREMENTS.md`. Doc sweep is
straightforward.

### 4.4 Brownfield input today

`graph_enrich.py:375` uses `VALID_ROLES` (derived from
`ROLE_ANNOTATIONS.values() + {"DTO"}`) to validate brownfield YAML
inputs. `_INJECT_FIELD_ANNOTATIONS` and `VALID_CAPABILITIES`
(`java_ontology.py:18-25`) already cover the capability validator
side.

After the rename, `VALID_ROLES` automatically contains `CLIENT`
(no extra wiring), and `VALID_CAPABILITIES` automatically contains
`HTTP_CLIENT` once added to `_TYPE_ANN_TO_CAPABILITY`.

## 5. Design

### 5.1 Source-of-truth rename

`ast_java.py:90`:

```python
# BEFORE:
"FeignClient": "FEIGN_CLIENT",

# AFTER:
"FeignClient": "CLIENT",
```

`ast_java.py:111-114`, extend `_TYPE_ANN_TO_CAPABILITY`:

```python
_TYPE_ANN_TO_CAPABILITY: dict[str, str] = {
    "ControllerAdvice":     "EXCEPTION_HANDLER",
    "RestControllerAdvice": "EXCEPTION_HANDLER",
    "FeignClient":          "HTTP_CLIENT",   # ← new
}
```

This is the canonical pattern: a class annotated `@FeignClient` now
gets:
- Role `CLIENT` via `ROLE_ANNOTATIONS`
- Capability `HTTP_CLIENT` via `_TYPE_ANN_TO_CAPABILITY`

`VALID_ROLES` and `VALID_CAPABILITIES` (`java_ontology.py:16-25`)
update automatically — they are derived sets.

### 5.2 Mechanical literal updates

#### `kuzu_queries.py:956` (`_FLOW_STAGES`)

```python
# BEFORE:
("FEIGN_CLIENT", "REPOSITORY", "MAPPER"),

# AFTER:
("CLIENT", "REPOSITORY", "MAPPER"),
```

#### `kuzu_queries.py:965` (`_ENTRYPOINT_ROLES`)

```python
# BEFORE:
"CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT",

# AFTER:
"CONTROLLER", "COMPONENT", "SERVICE", "CLIENT",
```

#### `kuzu_queries.py:975` (docstring)

Update inline reference.

#### `search_lancedb.py:188` (`_ROLE_SCORE_WEIGHTS`)

```python
# BEFORE:
"FEIGN_CLIENT": 0.06,

# AFTER:
"CLIENT": 0.06,
```

Same numeric weight — the rename does not change behavioural-search
ranking. (Open question [TBD-3]: is `0.06` still the right weight
when the role now also includes brownfield-annotated RestTemplate
clients? Recommend keep `0.06` for v1; revisit only if users complain.)

#### `server.py` (~6 sites)

Replace `FEIGN_CLIENT` → `CLIENT` in:
- Tool docstring (`server.py:49, 1335, 1339`)
- `role` enum descriptions (`server.py:687, 1138`)
- `entry_roles` list (`server.py:1415`)

#### `tests/test_lancedb_e2e.py:342`

```python
# BEFORE:
s["symbol"]["role"] in {"CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"}

# AFTER:
s["symbol"]["role"] in {"CONTROLLER", "COMPONENT", "SERVICE", "CLIENT"}
```

### 5.3 Brownfield deprecation alias

In `graph_enrich.py` brownfield validation, accept `FEIGN_CLIENT` as
a deprecated input that translates to `CLIENT` + `HTTP_CLIENT`:

```python
_DEPRECATED_ROLE_ALIASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "FEIGN_CLIENT": ("CLIENT", ("HTTP_CLIENT",)),
}

def _normalize_brownfield_role(
    role: str,
    annotation_or_fqn: str,
) -> tuple[str, tuple[str, ...]]:
    """Translate deprecated role aliases to (role, extra_capabilities).
    Logs deprecation warning."""
    aliased = _DEPRECATED_ROLE_ALIASES.get(role)
    if aliased is None:
        return role, ()
    new_role, extra_caps = aliased
    print(
        f"[lancedb-mcp] role_overrides: {role!r} is deprecated for {annotation_or_fqn!r}; "
        f"use {new_role!r} + capability {extra_caps!r} instead. Translated automatically.",
        file=sys.stderr,
    )
    return new_role, extra_caps
```

Apply in `_load_brownfield_overrides` before validating against
`VALID_ROLES`. The translated capabilities are merged into the
target's existing capability set.

This means users with existing YAML like:

```yaml
role_overrides:
  fqn:
    "com.example.UserApiClient": "FEIGN_CLIENT"
```

continue to work — they just get a deprecation warning and the
role becomes `CLIENT` with capability `HTTP_CLIENT`. Clean v1.

### 5.4 Ontology version

If PR-G1 has merged before this PR, bump 8 → 9.
If PR-G1 has not merged, bump 7 → 8 and PR-G1's bump becomes 8 → 9.

The bump is required because existing graphs have rows with
`role = "FEIGN_CLIENT"` — incompatible with the new `VALID_ROLES`.
Document "rebuild to apply" in the PR description (same migration
story as every other graph-shape change).

### 5.5 Documentation

- README: rename role table; mention `CLIENT` + `HTTP_CLIENT` capability;
  document the `MESSAGE_PRODUCER` capability that already exists for
  symmetry.
- `CODEBASE_REQUIREMENTS.md`: rename references.
- `propose/DEFERRED-REST-CLIENT-MIGRATION-PROPOSE.md`: **delete** (this
  proposal supersedes it; the rename-vs-capability decision is
  reversed by current architecture).

## 6. Risks and mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | Existing YAML overrides use `FEIGN_CLIENT` and break | Medium | Two-phase deprecation alias (§5.3). Warn in v1, drop in v2. |
| 2 | Old graphs' rows have `role="FEIGN_CLIENT"` | High | Ontology bump documented. Rebuild required. Same migration story as past breaking changes. |
| 3 | Downstream consumers of MCP tool `role` enum (e.g., LLM prompts) hardcode `FEIGN_CLIENT` | Low | The MCP `role` field is documented; this is a vocabulary change. Bump tool description visibly. Note in README "breaking change". |
| 4 | Behavioural-search ranking changes silently | Low | Same numeric weight (`0.06`). [TBD-3] flags it for review if user reports drift. |
| 5 | `trace_flow` integration stage misclassifies brownfield-annotated RestTemplate clients | Medium | They get role `CLIENT`, which IS in `_FLOW_STAGES[2]`. No regression — actually an improvement (they were `SERVICE` before, missed the integration tier). |
| 6 | Brownfield deprecation alias adds complexity to `_load_brownfield_overrides` | Low | Single helper function, ~15 LOC, clearly documented. Localized to one file. |
| 7 | Auto-promotion scope is unchanged but users assume it expanded | Medium | Document explicitly in README and PR description: "Only `@FeignClient` interfaces auto-promote to `CLIENT`. RestTemplate/WebClient users keep their existing role; opt in via brownfield annotations." |

## 7. Verification

### Tests

| # | Test | Asserts |
|---|---|---|
| 1 | `test_feign_client_emits_client_role` | `@FeignClient interface X` → `Symbol.role == "CLIENT"`, `"HTTP_CLIENT" in Symbol.capabilities` |
| 2 | `test_no_legacy_feign_client_role_in_graph` | After build, `MATCH (s:Symbol) WHERE s.role = 'FEIGN_CLIENT' RETURN count(s)` returns 0 |
| 3 | `test_resttemplate_class_unchanged` | Class with `RestTemplate` field but no `@FeignClient` → role unchanged from today's behaviour (e.g., `SERVICE`), no `HTTP_CLIENT` capability auto-added |
| 4 | `test_brownfield_feign_client_alias_translated` | YAML with `role_overrides.fqn: "com.x.Y": "FEIGN_CLIENT"` → built graph has `role="CLIENT"`, `"HTTP_CLIENT" in capabilities`, deprecation warning logged |
| 5 | `test_brownfield_client_role_accepted` | YAML with `role_overrides.fqn: "com.x.Y": "CLIENT"` → built graph has `role="CLIENT"`. No warning. |
| 6 | `test_brownfield_http_client_capability_accepted` | YAML with `capability_overrides.fqn: "com.x.Y": ["HTTP_CLIENT"]` → built graph has `"HTTP_CLIENT" in capabilities`. No warning. |
| 7 | `test_message_producer_capability_unchanged` | Class injecting `KafkaTemplate` → `"MESSAGE_PRODUCER" in capabilities` (regression: this proposal does NOT touch async) |
| 8 | `test_trace_flow_includes_client_in_stage_2` | trace_flow walks through `_FLOW_STAGES[2]` correctly with the new `CLIENT` literal |
| 9 | `test_codebase_search_entry_roles_includes_client` | `entry_roles` filter accepts `CLIENT` and excludes `FEIGN_CLIENT` (ensures server.py:1415 was updated) |

Test count target: existing baseline + 9. Combined with PR-F1 (+6) and PR-G1 (+8), total target: **~289 passed, 4 skipped** (266 baseline + 6 + 8 + 9).

### Manual evidence

```bash
cd /home/user/workspace/user-rag

rm -rf /tmp/check_client && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_client --verbose 2>&1 | tail -10

# Verify:
python -c "
import kuzu
db = kuzu.Database('/tmp/check_client'); conn = kuzu.Connection(db)
print('=== role distribution ===')
r = conn.execute('MATCH (s:Symbol) WHERE s.role IS NOT NULL RETURN s.role, count(s) ORDER BY s.role')
while r.has_next(): print(' ', r.get_next())

print('=== HTTP_CLIENT capability holders ===')
r = conn.execute(\"MATCH (s:Symbol) WHERE 'HTTP_CLIENT' IN s.capabilities RETURN s.fqn\")
while r.has_next(): print(' ', r.get_next())
"
# Expected:
#   role distribution includes CLIENT (the BFeignClient interface)
#   role distribution does NOT include FEIGN_CLIENT
#   HTTP_CLIENT capability holders include com.smoke.a.BFeignClient
```

## 8. Sequencing

This proposal **must ship after** PR-F1 (Feign-not-an-exposer) and
PR-G1 (cross_service_resolution flag). Reasons:

1. PR-F1 and PR-G1 are reviewed against today's familiar `FEIGN_CLIENT`
   vocabulary. Stacking the rename first complicates two PRs that are
   otherwise simple.
2. The rename's ontology bump composes cleanly on top of PR-G1's
   bump (7→8 → 8→9). Reverse order requires renumbering.
3. The fixture assertions in PR-F1 ("`BFeignClient` does not appear
   in EXPOSES") are unchanged regardless of role naming. Same for
   PR-G1's brownfield gating.

So the queue is:
1. Plans PR (#27) merges → cursor prompts for PR-F1, PR-G1
2. PR-F1 ships
3. PR-G1 ships
4. Real-project test in `brownfield_only` mode (data-gathering)
5. **This proposal** drafted PR (covered by this doc); plan & implementing PR drafted next
6. (Parallel) review of #24 multi-attribution and any HTTP_CLIENT
   role unification expansion based on real-project data

## 9. [TBD]

| # | Decision | Notes |
|---|----------|-------|
| 1 | Should the deprecation alias for `FEIGN_CLIENT` be removed in the next ontology bump after this one, or kept indefinitely? | Recommend remove in the next major bump (v2). Single-release deprecation window is enough. Document in v1 PR description. |
| 2 | Should `@CodebaseRole(role="CLIENT")` without `@CodebaseCapability(capability="HTTP_CLIENT")` be accepted as-is, or auto-add the capability? | Recommend accept as-is (no auto-add). The user explicitly opted in to `CLIENT` role; they may have a non-HTTP client (e.g., gRPC) where `HTTP_CLIENT` would be wrong. Capabilities are independent. |
| 3 | Is `_ROLE_SCORE_WEIGHTS["CLIENT"] = 0.06` still right when the role can include brownfield-annotated RestTemplate clients? | Recommend keep `0.06` for v1. Revisit only if behavioural-search drift is reported. |
| 4 | Should the deprecation alias also apply to `@CodebaseRole(role="FEIGN_CLIENT")` in source, not just YAML? | Recommend yes — the alias should be uniform across all brownfield input layers (annotation + YAML). Add to `collect_annotation_meta_chain` translation. |
| 5 | Should this proposal be folded into a single PR with PR-F1 / PR-G1 (smaller surface) or kept as PR-H1 (separate ontology bump)? | Recommend separate PR-H1. Combining muddies review. |
| 6 | Should we also rename `_ROLE_SCORE_WEIGHTS` key? | Yes — covered in §5.2. |
| 7 | Add a test that checks `VALID_ROLES` contains `CLIENT` but not `FEIGN_CLIENT`? | Recommend yes — guards against accidental re-introduction. Test #2 covers the graph-level check; add a unit test on `VALID_ROLES` directly. |

## 10. References

- `ast_java.py:90` — `ROLE_ANNOTATIONS["FeignClient"]` (the rename target)
- `ast_java.py:111-114` — `_TYPE_ANN_TO_CAPABILITY` (where `HTTP_CLIENT` is added)
- `ast_java.py:116-122` — `_INJECTED_TYPES_TO_CAPABILITY` (existing
  `MESSAGE_PRODUCER` precedent — no change needed)
- `java_ontology.py:18-25` — `VALID_CAPABILITIES` (auto-derived; updates
  for free)
- `kuzu_queries.py:956, 965, 975` — `_FLOW_STAGES`, `_ENTRYPOINT_ROLES`,
  trace_flow docstring
- `search_lancedb.py:188` — `_ROLE_SCORE_WEIGHTS`
- `server.py:49, 687, 1138, 1335, 1339, 1415` — MCP tool docstrings,
  `role` enum strings, `entry_roles` list
- `tests/test_lancedb_e2e.py:342` — one e2e assertion
- `graph_enrich.py:375, 408, 446, 733-735` — brownfield role validation
  (where the deprecation alias hooks in)
- `propose/DEFERRED-REST-CLIENT-MIGRATION-PROPOSE.md` — **superseded
  and deleted** by this proposal
- `propose/FEIGN-NOT-AN-EXPOSER-PROPOSE.md` (PR #25) — orthogonal
  bug fix; ships first via PR-F1
- `propose/CROSS-SERVICE-RESOLUTION-FLAG-PROPOSE.md` (PR #26) —
  orthogonal flag; ships before this via PR-G1
