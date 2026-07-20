> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: `FEIGN_CLIENT` role → `CLIENT` + `HTTP_CLIENT` capability

Status: **completed** — shipped (propose merged in [#28](https://github.com/HumanBean17/java-codebase-rag/pull/28)).
Source: `propose/CLIENT-ROLE-RENAME-PROPOSE.md` on master.
Sequence: ships **after** PR-F1 (#31, merged) and PR-G1 (#30, merged). Master baseline at this plan's start: `aed732b`, `ONTOLOGY_VERSION = 8`, **281 passed, 4 skipped**.

## TL;DR

Single PR that hard-renames the role enum value `FEIGN_CLIENT` → `CLIENT`,
adds a new capability `HTTP_CLIENT` auto-detected for `@FeignClient` types,
and updates every `FEIGN_CLIENT` literal in production code, tests,
docs, and MCP tool descriptions.

```python
# ast_java.py:91 (single source-of-truth flip)
ROLE_ANNOTATIONS["FeignClient"] = "CLIENT"

# ast_java.py:114 (added entry)
_TYPE_ANN_TO_CAPABILITY["FeignClient"] = "HTTP_CLIENT"
```

`VALID_ROLES` and `VALID_CAPABILITIES` are auto-derived (`java_ontology.py:16,18`),
so flipping the two table entries above propagates `CLIENT` into the role allow-list
and `HTTP_CLIENT` into the capability allow-list automatically.

**No deprecation alias.** Per propose §5.3 and merged decision: MCP bundle has
no users yet, breaking changes are explicitly allowed. Brownfield YAML / annotation
input that still says `FEIGN_CLIENT` falls through the existing validator's
warn-and-drop path (`graph_enrich.py:443-447, 481-486`) — same UX as any
typo'd role literal today.

~115 LOC. Ontology bump 8 → 9 (vocabulary change in stored Symbol rows).
9 tests; combined target after this PR: **~290 passed, 4 skipped**.

## Origin

| From | Item | Severity |
|------|------|----------|
| Brownfield annotation honesty (propose §1.1) | Annotating a non-Feign class with `@CodebaseRole(role="FEIGN_CLIENT")` is a smell — the annotation lies about what the class is. `CLIENT` is honest; `HTTP_CLIENT` capability says how it talks. | medium (vocabulary cleanup) |
| Spring-native peer to `CONTROLLER` (propose §1.2) | `CONTROLLER` is a generic Spring-MVC role; `FEIGN_CLIENT` is library-specific. Promoting `CLIENT` to peer status mirrors the existing pattern (CONTROLLER role + REST_CONTROLLER capability for Spring MVC). | medium (consistency) |

## Recommended PR boundaries

Single PR, ~115 LOC. Scope is narrow (table flip + literal sweep + ontology
bump + tests), and splitting it forces an awkward intermediate state where
some literals say `CLIENT` and others say `FEIGN_CLIENT`.

- **PR-H1** — table flip + ontology bump + literal sweep across
  `kuzu_queries.py`, `search_lancedb.py`, `server.py`, README, CODEBASE_REQUIREMENTS,
  one e2e test + 9 new tests.

§9 [TBD] items in the propose are all v1-resolved with explicit
recommendations. The plan inherits those defaults.

---

## PR-H1 — Hard rename `FEIGN_CLIENT` → `CLIENT` + add `HTTP_CLIENT` capability

Touches: `ast_java.py` (role table + capability table + ontology bump),
`kuzu_queries.py` (`_FLOW_STAGES[2]`, `_ENTRYPOINT_ROLES`, `trace_flow`
docstring), `search_lancedb.py` (`_ROLE_SCORE_WEIGHTS`),
`server.py` (6 docstring / enum literal references),
`README.md` (`_ROLE_SCORE_WEIGHTS` table + `trace_flow` description + role enum list),
`CODEBASE_REQUIREMENTS.md` (annotation map), `tests/test_lancedb_e2e.py:342`
(role allow-list assertion), new `tests/test_client_role_rename.py` (9 tests).

Out of scope:
- Async role/capability changes — `MESSAGE_PRODUCER` already covers
  KafkaTemplate/RabbitTemplate/JmsTemplate/StreamBridge/ApplicationEventPublisher
  via `_INJECTED_TYPES_TO_CAPABILITY` (`ast_java.py:117-122`); the propose §G7
  explicitly defers async work as already-handled.
- Auto-promoting `RestTemplate`/`WebClient`-injecting classes to `CLIENT` role.
  Brownfield-only opt-in via `@CodebaseRole(role="CLIENT")` (propose §G6).
- Backwards-compat alias for `FEIGN_CLIENT` — propose §5.3, "no users yet" decision.
- Database migration tooling for old graphs — full rebuild on ontology bump,
  same as every previous bump.
- Adding `HTTP_CLIENT` to `_ROLE_SCORE_WEIGHTS` (it's a capability, not a role).
- Renaming `_ROLE_SCORE_WEIGHTS` *key* — covered by changing `"FEIGN_CLIENT"` →
  `"CLIENT"` literal; no structural rename.
- New MCP tools, new annotation types.
- Role-based query expansion (e.g. extending `list_by_role` enum strings) beyond
  the literal `FEIGN_CLIENT` → `CLIENT` substitution.

### Background

`@FeignClient` interfaces today get `Symbol.role == "FEIGN_CLIENT"` via
`ast_java.py:91`. The capability detector tables (`_METHOD_ANN_TO_CAPABILITY`,
`_TYPE_ANN_TO_CAPABILITY`, `_INJECTED_TYPES_TO_CAPABILITY`,
`_SUPERTYPE_TO_CAPABILITY`) currently emit `EXCEPTION_HANDLER`,
`SCHEDULED_TASK`, `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `REST_CONTROLLER`,
`AUDITED`, `EXCEPTION_TYPE` — but **no** `HTTP_CLIENT`. PR-H1 is the first
PR to add `HTTP_CLIENT` to the vocabulary.

`VALID_ROLES` and `VALID_CAPABILITIES` (`java_ontology.py:16,18`) are
auto-derived from these tables, so the rename propagates automatically into
the brownfield validator's allow-list (`graph_enrich.py:410-411`) without
any code change there.

### Failure modes the rename addresses

1. **Annotation lies.** A user with a `RestTemplate`-using class wanting
   to mark it as an HTTP client today must annotate `@CodebaseRole(role="FEIGN_CLIENT")`
   — but the class isn't a Feign client. After H1, they annotate
   `role="CLIENT"` honestly.
2. **Library bleed into ontology.** Every other role in `ROLE_ANNOTATIONS`
   describes a Spring-MVC concept (`CONTROLLER`, `SERVICE`, `REPOSITORY`,
   `COMPONENT`, `CONFIG`, `ENTITY`, `MAPPER`). `FEIGN_CLIENT` is the
   only library-specific entry — propose §1.2.
3. **Capability gap for HTTP clients.** Today there's no way to say "this
   class talks HTTP" in the capability vocabulary. After H1, `HTTP_CLIENT`
   exists as a peer to `MESSAGE_PRODUCER`, opening the door to brownfield
   annotations on `RestTemplate`/`WebClient` classes (propose §G6, future PR).

### Resolution

#### Change 1: Flip the role enum

`ast_java.py:91`:

```python
# BEFORE:
"FeignClient": "FEIGN_CLIENT",

# AFTER:
"FeignClient": "CLIENT",
```

#### Change 2: Add the HTTP_CLIENT capability

`ast_java.py:114-116`:

```python
_TYPE_ANN_TO_CAPABILITY: dict[str, str] = {
    "ControllerAdvice":     "EXCEPTION_HANDLER",
    "RestControllerAdvice": "EXCEPTION_HANDLER",
    "FeignClient":          "HTTP_CLIENT",   # NEW
}
```

`VALID_CAPABILITIES` auto-extends because it's derived from the four
capability tables (`java_ontology.py:18-25`).

#### Change 3: Update `_FLOW_STAGES` and `_ENTRYPOINT_ROLES`

`kuzu_queries.py:994-1007`:

```python
_FLOW_STAGES: tuple[tuple[str, ...], ...] = (
    ("CONTROLLER",),
    ("SERVICE", "COMPONENT"),
    ("CLIENT", "REPOSITORY", "MAPPER"),  # was: FEIGN_CLIENT
)

_ENTRYPOINT_ROLES: tuple[str, ...] = (
    "CONTROLLER", "COMPONENT", "SERVICE", "CLIENT",  # was: FEIGN_CLIENT
)
```

Also update the docstring on line 1016 ("Walk stages CONTROLLER -> SERVICE/COMPONENT
-> FEIGN_CLIENT/REPOSITORY/MAPPER") to use `CLIENT`.

#### Change 4: Update `_ROLE_SCORE_WEIGHTS`

`search_lancedb.py:185-194`:

```python
_ROLE_SCORE_WEIGHTS: dict[str, float] = {
    "CONTROLLER": 0.10,
    "SERVICE": 0.08,
    "CLIENT": 0.06,           # was: FEIGN_CLIENT
    "COMPONENT": 0.03,
    "REPOSITORY": 0.02,
    ...
}
```

Same numeric weight (`0.06`). Per propose §9 [TBD-2]: "keep 0.06 for v1.
Revisit only if behavioural-search drift is reported."

#### Change 5: Update `server.py` MCP tool descriptions and enum strings

Six references at `server.py:49, 689, 1141, 1338, 1342, 1418`:

- Line 49: `"SERVICE / FEIGN_CLIENT"` → `"SERVICE / CLIENT"`
- Line 689: enum string `"...|FEIGN_CLIENT|MAPPER|DTO"` → `"...|CLIENT|MAPPER|DTO"`
- Line 1141: enum string `"...|FEIGN_CLIENT|MAPPER|OTHER"` → `"...|CLIENT|MAPPER|OTHER"`
- Line 1338: docstring `"...SERVICE / FEIGN_CLIENT..."` → `"...SERVICE / CLIENT..."`
- Line 1342: docstring `"...FEIGN_CLIENT/REPOSITORY/MAPPER..."` → `"...CLIENT/REPOSITORY/MAPPER..."`
- Line 1418: `entry_roles = ["CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"]` → `[..., "CLIENT"]`

#### Change 6: Update `README.md` and `docs/CODEBASE_REQUIREMENTS.md`

`README.md`:
- Line 137: `trace_flow` description's stage chain `FEIGN_CLIENT/REPOSITORY/MAPPER` → `CLIENT/REPOSITORY/MAPPER`
- Line 211: `_ROLE_SCORE_WEIGHTS` table row label `FEIGN_CLIENT` → `CLIENT`
- Line 333: role enum list `..., FEIGN_CLIENT, MAPPER, DTO` → `..., CLIENT, MAPPER, DTO`
- Add a brief brownfield-section note (1-2 lines): "`HTTP_CLIENT` capability auto-attached
  to `@FeignClient` interfaces. To mark a `RestTemplate`/`WebClient`-using class as
  `CLIENT`, use `@CodebaseRole(role="CLIENT")` and `@CodebaseCapability(capability="HTTP_CLIENT")`
  — both are independent (propose §9 [TBD-1])."

`CODEBASE_REQUIREMENTS.md`:
- Line 146: annotation-map row `@FeignClient | FEIGN_CLIENT` → `@FeignClient | CLIENT (+ capability HTTP_CLIENT)`
- Line 162: explanatory paragraph on RestTemplate/WebClient — update to use the
  new vocabulary, link to `@CodebaseRole(role="CLIENT")` workflow.
- Line 346-347: code sample that lists `ROLE_ANNOTATIONS` — update both `FeignClient` and
  `RegisterRestClient` mappings to `"CLIENT"`. (RegisterRestClient is MicroProfile's
  Feign equivalent; same role applies.)

#### Change 7: Update `tests/test_lancedb_e2e.py:342`

```python
# BEFORE:
s["symbol"]["role"] in {"CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"}

# AFTER:
s["symbol"]["role"] in {"CONTROLLER", "COMPONENT", "SERVICE", "CLIENT"}
```

#### Change 8: Bump `ONTOLOGY_VERSION` 8 → 9

`ast_java.py:73-75`:

```python
# Phase 5: HTTP_CALLS + ASYNC_CALLS (B2b); Phase 6: cross-service resolution mode on GraphMeta;
# Phase 7: FEIGN_CLIENT role → CLIENT + HTTP_CLIENT capability vocabulary cleanup.
# Bumps whenever extraction / enrichment semantics change.
ONTOLOGY_VERSION = 9
```

No `_SCHEMA_META` column changes — `Symbol.role` remains `STRING`, just stores
a different literal. No `kuzu_queries.py` `meta()` tier extension needed.
The bump signals "rebuild required to refresh role literals."

### Tests

Add a new `tests/test_client_role_rename.py` with 9 tests. Use existing
`tests/fixtures/cross_service_smoke/` (which has `BFeignClient`) — no
new fixture needed.

| # | Test name | Asserts |
|---|---|---|
| 1 | `test_feign_client_emits_client_role` | Build `cross_service_smoke`; query Symbol for `BFeignClient`; `role == "CLIENT"` and `"HTTP_CLIENT" in capabilities`. |
| 2 | `test_no_legacy_feign_client_role_in_graph` | Build same fixture; `MATCH (s:Symbol) WHERE s.role = 'FEIGN_CLIENT' RETURN count(s)` returns 0. |
| 3 | `test_resttemplate_class_unchanged` | Class with `RestTemplate` field but no `@FeignClient` (use `ClientA` from cross_service_smoke) → role unchanged from today's behaviour (`SERVICE` / whatever it had); no `HTTP_CLIENT` capability auto-added. |
| 4 | `test_brownfield_feign_client_role_dropped` | YAML with `role_overrides.fqn: "smoke.a.X": "FEIGN_CLIENT"` → build emits a stderr warning matching `unknown role 'FEIGN_CLIENT'`; the symbol's role is the auto-detected one (override silently dropped via the existing warn-and-drop path in `graph_enrich.py:443-447, 481-486`). Same behaviour for the `annotations` and `fqn` sub-tables. |
| 5 | `test_brownfield_client_role_accepted` | YAML with `role_overrides.fqn: "smoke.a.X": "CLIENT"` → built graph has `role="CLIENT"`. No warning. |
| 6 | `test_brownfield_http_client_capability_accepted` | YAML with `capability_overrides.fqn: "smoke.a.X": ["HTTP_CLIENT"]` → built graph has `"HTTP_CLIENT" in capabilities`. No warning. |
| 7 | `test_message_producer_capability_unchanged` | Class injecting `KafkaTemplate` (use the kafka producer in `cross_service_smoke`) → `"MESSAGE_PRODUCER" in capabilities`. Regression guard: this PR does NOT touch async. |
| 8 | `test_trace_flow_includes_client_in_stage_2` | Use `KuzuGraph.trace_flow` against a fixture seeding from `BFeignClient`; assert the `CLIENT` role correctly participates in `_FLOW_STAGES[2]`. |
| 9 | `test_codebase_search_entry_roles_includes_client` | `entry_roles` filter at `server.py:1418` accepts `CLIENT` and excludes `FEIGN_CLIENT` (assert by listing `entry_roles` literal in the response or by hitting the underlying `_ENTRYPOINT_ROLES` constant). |

Test count target: 281 baseline + 9 = **~290 passed, 4 skipped**.

Note on test #4: the propose's example showed `raise ValueError`, but the
actual `graph_enrich.py` validator is **warn-and-drop** (consistent with how
every other unknown literal in the YAML is handled today). The test asserts
the warning is on stderr and the override is dropped — not an exception.
This is a plan delta from propose §5.3; non-substantive, just aligning
with reality.

### Manual evidence to capture in PR description

```bash
cd /home/user/workspace/user-rag

rm -rf /tmp/check_h1 && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_h1 --verbose 2>&1 | tail -5

# Expected: no errors. ontology_version logged as 9.

# Verify role rename
python -c "
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
g = KuzuGraph('/tmp/check_h1')
syms = [r['symbol'] for r in g.list_by_role(role='CLIENT')]
print('CLIENT symbols:', [s['fqn'] for s in syms])
print('legacy FEIGN_CLIENT count:', len(g.list_by_role(role='FEIGN_CLIENT')))
"
# Expected:
#   CLIENT symbols: ['smoke.a.BFeignClient']
#   legacy FEIGN_CLIENT count: 0

# Verify HTTP_CLIENT capability
python -c "
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
g = KuzuGraph('/tmp/check_h1')
syms = [r['symbol'] for r in g.list_by_capability(capability='HTTP_CLIENT')]
print('HTTP_CLIENT symbols:', [s['fqn'] for s in syms])
"
# Expected: ['smoke.a.BFeignClient']

# Verify meta
python -c "
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
print('ontology_version:', KuzuGraph('/tmp/check_h1').meta()['ontology_version'])
"
# Expected: 9

# Verify brownfield warn-and-drop on FEIGN_CLIENT input
echo 'role_overrides:
  fqn:
    "smoke.a.BFeignClient": "FEIGN_CLIENT"' \
  > tests/fixtures/cross_service_smoke/.lancedb-mcp.yml
rm -rf /tmp/check_drop && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_drop --verbose 2>&1 | grep -iE 'unknown role'
# Expected: stderr line containing "unknown role 'FEIGN_CLIENT'"

# Cleanup
rm tests/fixtures/cross_service_smoke/.lancedb-mcp.yml
```

### Migration

No data migration. Old graphs stop loading via `meta()['ontology_version']`
checks — `KuzuGraph` already prints the rebuild prompt when ontology mismatch
is detected. Same migration story as every previous bump.

The `meta()` tiered query chain in `kuzu_queries.py` does NOT need a new tier:
`Symbol.role` is read as a string regardless of value, and the
`_META_PR_F1`/`_META_PR_E3`/etc. chain only differs by which `GraphMeta`
columns exist — none of which change in this PR. Old graphs still produce
a valid `meta()` dict; their stored role literals just say `FEIGN_CLIENT`,
which won't match anything in the new `_ROLE_SCORE_WEIGHTS` / `_FLOW_STAGES`
/ `_ENTRYPOINT_ROLES` constants. Documented "rebuild to apply" in PR
description.

### Definition of Done

- [ ] `ROLE_ANNOTATIONS["FeignClient"]` flipped to `"CLIENT"` (`ast_java.py:91`)
- [ ] `_TYPE_ANN_TO_CAPABILITY["FeignClient"] = "HTTP_CLIENT"` added (`ast_java.py:114`)
- [ ] `_FLOW_STAGES[2]`, `_ENTRYPOINT_ROLES`, and `trace_flow` docstring updated (`kuzu_queries.py`)
- [ ] `_ROLE_SCORE_WEIGHTS["CLIENT"] = 0.06` (was `FEIGN_CLIENT`) (`search_lancedb.py:188`)
- [ ] Six `server.py` literal references updated (lines 49, 689, 1141, 1338, 1342, 1418)
- [ ] `README.md` updated (3 lines + brownfield note)
- [ ] `docs/CODEBASE_REQUIREMENTS.md` updated (lines 146, 162, 346-347)
- [ ] `tests/test_lancedb_e2e.py:342` allow-list updated
- [ ] `ONTOLOGY_VERSION` bumped 8 → 9 with phase-comment update
- [ ] All 9 new tests in `tests/test_client_role_rename.py` pass
- [ ] `pytest tests -q` baseline does not regress; combined target **~290 passed, 4 skipped**
- [ ] No new MCP tools, no new annotation types, no schema column changes
- [ ] No leftover `FEIGN_CLIENT` literals in production code (verify with
      `grep -rn "FEIGN_CLIENT" --include='*.py' --exclude-dir=tests --exclude-dir=plans/completed --exclude-dir=propose/completed .`
      → 0 matches)
- [ ] PR description includes manual evidence block

### Risk register (from propose §6)

| # | Risk | Mitigation |
|---|---|---|
| 1 | Existing YAML overrides use `FEIGN_CLIENT` and break | N/A — no users yet. Hard rename is explicitly allowed. Validation warn-and-drop redirects users to the allow-list (test #4). |
| 2 | Old graphs' rows have `role="FEIGN_CLIENT"` | Ontology bump 8→9; rebuild required. Same migration story as every previous bump. |
| 3 | Downstream consumers of MCP tool `role` enum (e.g., LLM prompts) hardcode `FEIGN_CLIENT` | Bump tool descriptions visibly (Change 5). Add note in README "breaking change in v9". |
| 4 | Behavioural-search ranking changes silently | Same numeric weight (`0.06`). Test #8 asserts trace_flow still works. Listed as propose [TBD-2] for review only if drift reported. |
| 5 | `trace_flow` integration stage misclassifies brownfield-annotated RestTemplate clients | They get role `CLIENT`, which IS in `_FLOW_STAGES[2]`. No regression — actually an improvement (they were `SERVICE` before, missed the integration tier). |
| 6 | Auto-promotion scope is unchanged but users assume it expanded | Document explicitly in README brownfield note: "Only `@FeignClient` interfaces auto-promote to `CLIENT`. RestTemplate/WebClient users keep their existing role; opt in via brownfield annotations." |
| 7 | Test isolation (per-fixture caches in `_load_brownfield_overrides`) leak between tests | Mirror PR-G1's pattern — use `tmp_path / "proj"` per-test fixture copies; don't reuse the shared fixture for tests that flip YAML. |

---

## Followups (non-blockers, capture in PR description as TBDs)

1. **`@CodebaseRole(role="CLIENT")` honesty audit on real codebases.** After
   real-project test, verify users actually annotate RestTemplate classes
   with `CLIENT` role. If they instead use `SERVICE` role + manual
   `HTTP_CLIENT` capability, that's also fine (propose §9 [TBD-1]:
   capabilities are independent). No code change unless real use surfaces a gap.
2. **`HTTP_CLIENT` listed in `entry_roles`?** Today `_ENTRYPOINT_ROLES` is
   role-only. If `list_by_capability("HTTP_CLIENT")` becomes a common
   navigation entry, consider extending entry semantics to capabilities.
   Defer to v2 once usage data exists.
3. **`RegisterRestClient` (MicroProfile) coverage.** `CODEBASE_REQUIREMENTS.md:347`
   shows `RegisterRestClient` was *intended* to map to `FEIGN_CLIENT` too,
   but `ast_java.py:91` only has `FeignClient`. Current behaviour: MicroProfile
   classes don't get any role. After H1, if a real-project test surfaces this,
   add `"RegisterRestClient": "CLIENT"` to `ROLE_ANNOTATIONS` and
   `"RegisterRestClient": "HTTP_CLIENT"` to `_TYPE_ANN_TO_CAPABILITY` —
   2-line follow-up.
4. **Capability-based search weight.** Today `_ROLE_SCORE_WEIGHTS` boosts roles;
   no analogous `_CAPABILITY_SCORE_WEIGHTS`. If `HTTP_CLIENT` becomes a
   first-class navigation hint (parallel to `MESSAGE_LISTENER`/`MESSAGE_PRODUCER`
   today), consider adding a capability-weighted score component. Out of scope
   for H1.
5. **Multi-attribution Routes (PR #24).** Parked. Independent of vocabulary
   cleanup.
6. **Mass-rename in `propose/completed/` and `plans/completed/`.** Historical
   docs frozen by convention — leave `FEIGN_CLIENT` references intact.
   Listed here so the literal-sweep grep doesn't fail on those.

---

## References

- `propose/CLIENT-ROLE-RENAME-PROPOSE.md` — the merged propose (PR #28)
  that this plan implements
- `ast_java.py:91` — `ROLE_ANNOTATIONS["FeignClient"]` (the rename target)
- `ast_java.py:114-116` — `_TYPE_ANN_TO_CAPABILITY` (where `HTTP_CLIENT` is added)
- `ast_java.py:117-122` — `_INJECTED_TYPES_TO_CAPABILITY` (already has
  `MESSAGE_PRODUCER` for KafkaTemplate / RabbitTemplate / JmsTemplate /
  StreamBridge / ApplicationEventPublisher; no async work)
- `ast_java.py:73-75` — `ONTOLOGY_VERSION = 8` (bump to 9)
- `java_ontology.py:16, 18-25` — `VALID_ROLES` and `VALID_CAPABILITIES`
  (auto-derived from the four capability tables; no edit needed)
- `kuzu_queries.py:994-1007, 1016` — `_FLOW_STAGES`, `_ENTRYPOINT_ROLES`,
  `trace_flow` docstring
- `search_lancedb.py:185-194` — `_ROLE_SCORE_WEIGHTS`
- `server.py:49, 689, 1141, 1338, 1342, 1418` — six MCP tool docstring /
  enum literal references
- `graph_enrich.py:410-411, 443-447, 481-486` — brownfield validator
  warn-and-drop path (no code change; new behaviour is automatic via
  the auto-derived `VALID_ROLES`)
- `tests/test_lancedb_e2e.py:342` — one e2e role allow-list assertion
- `README.md:137, 211, 333` — three doc references
- `CODEBASE_REQUIREMENTS.md:146, 162, 346-347` — annotation map + sample code
- `plans/PLAN-FEIGN-NOT-AN-EXPOSER.md` (PR #31, merged) — orthogonal predecessor
- `plans/PLAN-CROSS-SERVICE-RESOLUTION-FLAG.md` (PR #30, merged) —
  orthogonal predecessor; established the meta() tiered fallback pattern that
  H1 does NOT need to extend
