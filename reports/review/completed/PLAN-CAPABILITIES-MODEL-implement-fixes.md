# PLAN-CAPABILITIES-MODEL — implementation fixes

**Inputs:** `reports/review/PLAN-CAPABILITIES-MODEL-implement-report.md` +
designer review of that report.
**Plan file (now amended):** `plans/PLAN-CAPABILITIES-MODEL.md`. Re-read
the plan first — it has two new sections (**Filter strategy** and the
expanded **`trace_flow` seeding** subsection) that change how some of
the original instructions should be implemented.
**Goal of this pass:** close 7 issues from the review **plus** correct a
design-level flaw the review surfaced but did not call out — the
existing four `capability` filters use naive post-filter, which silently
under-delivers results against the `limit` contract.

Apply the fixes in priority order. Run the full test suite after each
group; do not bundle group D into A/B/C.

---

## Group A — `codebase_search` response & filter (Issues 1, 2 + design correction)

### A.1 Add `capabilities` to `CodeChunkHit`

**File:** `server.py`

In the `CodeChunkHit` Pydantic model (currently around line 65), add the
field next to `annotations_on_type` / `symbols`:

```python
capabilities: list[str] = Field(
    default_factory=list,
    description=(
        "Multi-tag capabilities derived from method/type annotations "
        "and injected types (MESSAGE_LISTENER, MESSAGE_PRODUCER, "
        "SCHEDULED_TASK, EXCEPTION_HANDLER). A class can carry several."
    ),
)
```

In `_rows_to_hits` (around line 402), populate it alongside the other
list fields:

```python
capabilities=_clean_str_list(r.get("capabilities")),
```

`_clean_str_list` already handles the legacy-string / native-list dual
shape — no new helper needed.

`JAVA_ENRICHED_COLUMNS` already includes `"capabilities"`
(`search_lancedb.py` line 37), so the column is fetched when present.
The schema-presence guard on line 459 means stale indexes without the
column degrade gracefully.

### A.2 Add `capability` filter to `codebase_search` (storage-pushdown)

**Files:** `search_lancedb.py`, `server.py`

This is **not** a post-filter. The plan's amended **Filter strategy**
section is explicit: post-filter without over-fetch widening violates
the `limit` contract and is rejected.

#### Step A.2.1 — extend `_build_extra_predicates`

In `search_lancedb.py` (around line 65), accept a new keyword:

```python
def _build_extra_predicates(
    *,
    columns: set[str],
    role: str | None,
    module: str | None,
    microservice: str | None,
    package_prefix: str | None,
    fqn_in: list[str] | None,
    role_in: list[str] | None = None,
    exclude_roles: list[str] | None = None,
    capability: str | None = None,        # NEW
    capability_in: list[str] | None = None,  # NEW — used by trace_flow seeding
) -> list[str]:
    ...
```

Emit a list-contains predicate when the column exists. **Verify the
exact LanceDB SQL syntax for the project's installed version** before
wiring — likely candidates, in order of compatibility:

```python
# Preferred (Lance >=0.10):
preds.append(f"array_has(capabilities, '{_escape_sql_str(capability)}')")
# Fallback if array_has unavailable:
preds.append(f"array_position(capabilities, '{_escape_sql_str(capability)}') >= 0")
# Last resort (some Lance builds):
preds.append(f"'{_escape_sql_str(capability)}' = ANY(capabilities)")
```

Run a tiny ad-hoc query against the local index to confirm which form
parses. Pick one and use it consistently.

For the multi-value variant (`capability_in`, used only by `trace_flow`
seeding — see Group B), build a disjunction:

```python
if capability_in and "capabilities" in columns:
    parts = [
        f"array_has(capabilities, '{_escape_sql_str(c)}')"
        for c in capability_in
    ]
    preds.append("(" + " OR ".join(parts) + ")")
```

Both predicates must be conditioned on `"capabilities" in columns` so
older indexes lacking the column still answer queries (filter ignored).

#### Step A.2.2 — surface in `run_search`

`run_search` (around line 722) gains a `capability: str | None = None`
parameter and forwards it to `_build_extra_predicates`. Same for
`capability_in: list[str] | None = None`. No other ranking change.

#### Step A.2.3 — surface in `codebase_search` MCP tool

In `server.py::codebase_search` (around line 488), add the parameter
next to `role`:

```python
capability: str | None = Field(
    default=None,
    description=(
        "Java only: AND-filter to chunks whose enclosing type carries "
        "this capability (MESSAGE_LISTENER|MESSAGE_PRODUCER|"
        "SCHEDULED_TASK|EXCEPTION_HANDLER). Use `list_by_capability` "
        "for graph-only queries."
    ),
),
```

Forward to `run_search(..., capability=capability, ...)`.

### A.3 Update unit + integration tests

- Extend `tests/test_lancedb_e2e.py` with the **`limit` contract**
  assertion (plan test #3): a fixture with 50 `@Service` classes of
  which 5 are also `MESSAGE_PRODUCER`; `list_by_role("SERVICE",
  capability="MESSAGE_PRODUCER", limit=50)` must return exactly the 5.
  Same shape for `codebase_search(..., capability=...)` (plan test #6).

---

## Group B — `trace_flow` capability seeding coordination (Issue 4 + design fix)

This is the design gap the review surfaced. The implementer faithfully
wrote the Kuzu OR predicate the plan asked for, but the LanceDB
pre-filter in `server.py::trace_flow` discards capability-only
entrypoints (role=OTHER, capability=SCHEDULED_TASK) before the Kuzu
seed query ever sees their FQNs. **Both sides must learn about
capabilities together.**

The plan's amended **`trace_flow` seeding** subsection is now explicit
about this. The Kuzu side is already implemented; only the LanceDB side
needs work.

### B.1 Widen the LanceDB seed pre-filter

**File:** `server.py`

In `trace_flow` (around line 880), the existing seed helper is:

```python
entry_roles = ["CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"]

def _seed(role_allowlist: list[str] | None) -> list[dict[str, Any]]:
    return run_search(
        ...
        role_in=role_allowlist,
        exclude_roles=None if role_allowlist else sorted(baseline_excludes),
    )
```

Extend it to also pass capability allowlist. Match the Kuzu side
exactly — `["MESSAGE_LISTENER", "SCHEDULED_TASK"]`:

```python
entry_roles = ["CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"]
entry_capabilities = ["MESSAGE_LISTENER", "SCHEDULED_TASK"]

def _seed(role_allowlist: list[str] | None,
          capability_allowlist: list[str] | None) -> list[dict[str, Any]]:
    return run_search(
        ...
        role_in=role_allowlist,
        capability_in=capability_allowlist,
        exclude_roles=(
            None if (role_allowlist or capability_allowlist)
            else sorted(baseline_excludes)
        ),
    )
```

Then in the calling code:

```python
# First pass: restricted to entrypoint-like role OR entrypoint capability.
seed_rows = await asyncio.to_thread(_seed, entry_roles, entry_capabilities)
if not seed_rows:
    seed_rows = await asyncio.to_thread(_seed, None, None)
```

The `OR` semantics between `role_in` and `capability_in` are produced
by `_build_extra_predicates`: each predicate is a separate string,
joined with `AND` at the top level. To get the right semantics
(role-OR-capability rather than role-AND-capability), emit a *combined
disjunction* when both are set:

```python
# In _build_extra_predicates:
role_pred = None
if role_in and "role" in columns:
    vals = ", ".join(f"'{_escape_sql_str(v)}'" for v in role_in)
    role_pred = f"role IN ({vals})"

cap_pred = None
if capability_in and "capabilities" in columns:
    parts = [
        f"array_has(capabilities, '{_escape_sql_str(c)}')"
        for c in capability_in
    ]
    cap_pred = "(" + " OR ".join(parts) + ")"

if role_pred and cap_pred:
    preds.append(f"({role_pred} OR {cap_pred})")
elif role_pred:
    preds.append(role_pred)
elif cap_pred:
    preds.append(cap_pred)
```

The standalone `role_in` / single `capability` cases keep their existing
behaviour (each emitted independently as before). Only the *paired
seeding case* triggers the OR composition.

### B.2 Verify with a fixture

Add to `tests/test_lancedb_e2e.py` (plan test #5): a fixture class
implementing `org.quartz.Job` with **no** Spring stereotype. Confirm
that `trace_flow("scheduled order cleanup", ...)` returns this class as
a stage-0 seed. Without B.1 it will not — that is the regression guard.

---

## Group C — `find_*` and `list_by_*` storage pushdown (Issue 3 + design fix)

The four already-landed `capability` filters
(`find_implementors`, `find_subclasses`, `list_by_role`,
`list_by_annotation`) use naive post-filter. `find_injectors` is
missing the parameter entirely. Both flaws fix together by switching to
storage pushdown in Kuzu.

### C.1 Push the `capability` filter into `KuzuGraph` methods

**File:** `kuzu_queries.py`

For each of the five graph methods consumed by these tools
(`find_implementors`, `find_subclasses`, `find_injectors`,
`list_by_role`, `list_by_annotation`), add an optional `capability`
parameter:

```python
def list_by_role(
    self, role: str, *,
    module: str | None = None,
    microservice: str | None = None,
    capability: str | None = None,    # NEW
    limit: int = 100,
) -> list[SymbolHit]:
    filters = ["s.role = $role"]
    params: dict[str, Any] = {"role": role}
    if capability:
        filters.append("$capability IN s.capabilities")
        params["capability"] = capability
    filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
    where = " AND ".join(filters)
    query = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
    return [_row_to_symbol(r) for r in self._rows(query, params)]
```

Same shape for `list_by_annotation`, `find_implementors`,
`find_subclasses`. Apply the predicate against the result-node alias
(`s` for the `list_by_*` queries; whatever alias is used in the
implementor / subclass query). The `LIMIT` clause **must** come after
the capability filter — Kuzu's planner handles this automatically once
it's part of `WHERE`.

For `find_injectors`, the result is an *edge* between two `Symbol`
nodes (`src` injects `dst`). The user-relevant capability is on the
**consumer** (`src`):

```python
def find_injectors(
    self, name: str, *,
    module: str | None = None,
    microservice: str | None = None,
    capability: str | None = None,    # NEW
    limit: int = 100,
) -> list[EdgeHit]:
    # ... existing query that binds (src)-[:INJECTS]->(dst) ...
    if capability:
        filters.append("$capability IN src.capabilities")
        params["capability"] = capability
    ...
```

### C.2 Replace post-filter with parameter pass-through in `server.py`

For each of the five tools (`find_implementors`, `find_subclasses`,
`find_injectors`, `list_by_role`, `list_by_annotation`):

- Remove the post-filter line `rows = [r for r in rows if capability in r.capabilities]`.
- Pass `capability=capability` to the corresponding `KuzuGraph` method.
- For `find_injectors` (Issue 3): add the `capability` parameter to
  the tool signature in the first place. Reuse the same
  `Field(default=None, description=...)` shape as the other four. Pass
  through to `graph.find_injectors(..., capability=capability)`.

`list_by_capability` is unaffected — it already pushes down via Cypher.

### C.3 Tests

Convert the existing `capability` post-filter tests to assert
pushdown semantics: build a fixture with N=50 services of which only 5
have the requested capability, request `limit=50`, expect exactly 5
results. The previous post-filter implementation would also pass this
specific shape, but a stronger fixture (50 services, capability=Y on 5
services that are *not* in the first 50 vector hits or graph rows)
will distinguish the two implementations. Pick the stronger fixture.

---

## Group D — Documentation (Issues 5, 6)

### D.1 `README.md`

Add a new section **after** the existing "Roles" section, before the
search-tools section. Suggested skeleton:

```markdown
## Capabilities

In addition to the single primary `role` per Java type, the indexer
extracts a multi-tag `capabilities: list[str]` field from method-level
annotations, type-level annotations, injected types, and supertypes.
A type can carry zero or many capabilities. Capabilities never
*replace* the role; they augment it.

| Capability | Trigger |
|---|---|
| `MESSAGE_LISTENER` | `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@SqsListener`, `@EventListener`, `@StreamListener` on any method |
| `MESSAGE_PRODUCER` | type injects `KafkaTemplate`, `RabbitTemplate`, `JmsTemplate`, `StreamBridge`, or `ApplicationEventPublisher` |
| `SCHEDULED_TASK`   | `@Scheduled` on any method, or class implements `org.quartz.Job` |
| `EXCEPTION_HANDLER`| `@ControllerAdvice`, `@RestControllerAdvice`, or any method with `@ExceptionHandler` |

Use `list_by_capability` to enumerate types carrying a capability, or
pass `capability=...` to `codebase_search` / `list_by_role` /
`list_by_annotation` / `find_*` to AND-filter results.
```

### D.2 `CODEBASE_REQUIREMENTS.md`

Add a short note under the role-inference section:

```markdown
Capabilities are derived at the **type level**: method-level annotation
evidence is aggregated up to the enclosing type. Per-method capability
storage is intentionally out of scope for the current ontology
(version 3) — see `plans/PLAN-CAPABILITIES-MODEL.md`. The deferred
call-graph layer (`propose/DEFERRED-CALL-GRAPH-PROPOSE.md`) is the
designated place to revisit method-granularity if the need arises.
```

---

## Group E — Style nit (Issue 7)

**File:** `ast_java.py`, around line 113.

Insert a single blank line between `_SUPERTYPE_TO_CAPABILITY` and
`_TYPE_KINDS`. No other change. Verify by running the existing
formatter / linter the project uses.

---

## Acceptance checklist

Run before declaring done:

- [ ] **Group A:** `codebase_search` returns `capabilities` per hit;
  `capability` filter present and pushed down; `limit` contract
  test passes (50 services / 5 producers / `limit=50` → exactly 5).
- [ ] **Group B:** `trace_flow` returns a Quartz `Job` implementor
  (role=OTHER, capability=SCHEDULED_TASK) as a stage-0 seed.
- [ ] **Group C:** all five graph-backed tools push the `capability`
  filter into Cypher; `find_injectors` has the parameter; no Python
  post-filter on `r.capabilities` remains in `server.py` for these
  tools (verify with `rg "for r in rows if capability in" server.py`
  → no matches).
- [ ] **Group D:** `README.md` has a "Capabilities" section;
  `CODEBASE_REQUIREMENTS.md` notes the type-level granularity.
- [ ] **Group E:** blank line restored.
- [ ] All existing tests still pass.
- [ ] New tests cover (a) `limit` contract, (b) capability-only
  `trace_flow` seeding, (c) `codebase_search` capability filter.
- [ ] No new ontology bump (still `3`); no unrelated API changes.

## Notes for the implementer

- The plan was updated alongside this fix list. **Re-read
  `plans/PLAN-CAPABILITIES-MODEL.md`** — the **Filter strategy** and
  **`trace_flow` seeding** sections are new and binding. Anything in
  this file that conflicts with the plan, the plan wins.
- The reviewer attributed Issue 4 (`trace_flow` dead code) to
  implementation. It's actually a plan gap — the plan asked for a
  Kuzu change without specifying the LanceDB coordination. Group B
  closes that gap. You did not do anything wrong on that one; you
  faithfully implemented what the plan said. The plan is now
  complete.
- Verify LanceDB array-predicate syntax against the project's
  installed Lance version *before* writing the predicate. If the
  preferred form (`array_has`) is unavailable, document the chosen
  fallback in a comment on `_build_extra_predicates`.
- `find_injectors`' `capability` semantic (consumer side, not target)
  is a deliberate API decision; surface it in the Pydantic
  description string so callers don't guess wrong.
