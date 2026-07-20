<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: capabilities model (multi-tag annotations on Java types)

Status: **completed** — shipped (`capabilities` on Symbol nodes + brownfield overrides on `master`). Self-contained: an agent picking
this up should be able to land it without re-deriving the design.

## Goal

Add a `capabilities: list[str]` field to every Java `Symbol` node in the
graph (and the corresponding LanceDB Java chunk metadata where useful),
populated by a deterministic detector pass over type-level and
**method-level** annotations / supertypes / injected types. The primary
`role` field stays unchanged in shape — capabilities are an *additional*
multi-tag axis, not a replacement.

The initial capability set is:

| Capability | Trigger (any of) |
|---|---|
| `MESSAGE_LISTENER` | method has `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@SqsListener`, `@EventListener`, or `@StreamListener` |
| `MESSAGE_PRODUCER` | type injects `KafkaTemplate`, `RabbitTemplate`, `JmsTemplate`, `StreamBridge`, or `ApplicationEventPublisher` |
| `SCHEDULED_TASK` | method has `@Scheduled`; OR type implements `org.quartz.Job` (simple-name match `Job`) |
| `EXCEPTION_HANDLER` | type has `@ControllerAdvice` / `@RestControllerAdvice`; OR has any method with `@ExceptionHandler` |

Granularity: **type-level only** (option α from the design discussion).
Method-level capability storage is explicitly out of scope; we aggregate
method-annotation evidence up to the enclosing type.

`REST_CLIENT` is **deferred** — see
`propose/DEFERRED-REST-CLIENT-MIGRATION-PROPOSE.md`. Do not add it here.

## Principle: pure evolution, not rework

Same posture as `propose/DEFERRED-CALL-GRAPH-PROPOSE.md`. Nothing existing
is removed or restructured.

### What stays exactly as-is

- LanceDB tables and the `JavaLanceChunk` schema for non-capability fields.
- The `role` field in `TypeDecl`, `Symbol` (Kuzu), and `SymbolHit`. All
  current ranking weights, role-based filters, and `trace_flow` stages
  keep working unchanged for callers that don't opt into capabilities.
- `_ROLE_SCORE_WEIGHTS`, `_ENTRYPOINT_ROLES`, `_FLOW_STAGES` defaults.
  Capabilities **augment** entrypoint/stage selection (additive
  predicates) but do not change the existing role behaviour.
- All existing MCP tools' signatures.
- `infer_role_for_type` semantics — kept verbatim. Capabilities live in a
  separate function `infer_capabilities_for_type` that runs in addition to
  it.

### What gets added on top

1. AST: `TypeDecl.capabilities` populated by a new pure function.
2. Graph: `Symbol.capabilities STRING[]`, written in `build_ast_graph.py`.
3. Kuzu queries: `SymbolHit.capabilities`, plumbed through
   `_symbol_return_for` and `_row_to_symbol`. New `list_by_capability`
   helper.
4. Server: new `list_by_capability` MCP tool. Existing role-keyed tools
   gain an optional `capability` filter (additive).
5. Search: `_ENTRYPOINT_ROLES` / `_FLOW_STAGES` matching becomes
   capability-aware via OR predicates, so a `@Service` that has a
   `@KafkaListener` method is seeded as an entrypoint.
6. Ontology bump: `ONTOLOGY_VERSION` 2 → 3. Graph rebuild required.

## File-by-file changes

### `ast_java.py`

Add the capability detector and the field on `TypeDecl`. ~120 lines.

```python
# Capability detector tables. Method-level annotation triggers are
# matched against any method on the type; type-level triggers against
# the type's own annotations or supertype simple names.
_METHOD_ANN_TO_CAPABILITY: dict[str, str] = {
    "KafkaListener":   "MESSAGE_LISTENER",
    "RabbitListener":  "MESSAGE_LISTENER",
    "JmsListener":     "MESSAGE_LISTENER",
    "SqsListener":     "MESSAGE_LISTENER",
    "EventListener":   "MESSAGE_LISTENER",
    "StreamListener":  "MESSAGE_LISTENER",
    "Scheduled":       "SCHEDULED_TASK",
    "ExceptionHandler":"EXCEPTION_HANDLER",
}

_TYPE_ANN_TO_CAPABILITY: dict[str, str] = {
    "ControllerAdvice":     "EXCEPTION_HANDLER",
    "RestControllerAdvice": "EXCEPTION_HANDLER",
}

# Type names that, when injected (as field/ctor-param/Lombok-RAC-final),
# imply the enclosing type emits messages. Simple names only.
_INJECTED_TYPES_TO_CAPABILITY: dict[str, str] = {
    "KafkaTemplate":             "MESSAGE_PRODUCER",
    "RabbitTemplate":            "MESSAGE_PRODUCER",
    "JmsTemplate":               "MESSAGE_PRODUCER",
    "StreamBridge":              "MESSAGE_PRODUCER",
    "ApplicationEventPublisher": "MESSAGE_PRODUCER",
}

# Supertype simple names that imply a capability (interface impl or
# class extension). Used for Quartz-style Job classes that are not
# annotation-marked.
_SUPERTYPE_TO_CAPABILITY: dict[str, str] = {
    "Job": "SCHEDULED_TASK",
}


def infer_capabilities_for_type(type_decl: "TypeDecl") -> list[str]:
    """Aggregate type-level capabilities. Stable, sorted, deduplicated.

    Pure function: derives capabilities from the parsed AST only. Does
    not consult external configuration; brownfield overrides are merged
    later in `graph_enrich.py` so this stays free of I/O.
    """
    caps: set[str] = set()

    for ann in type_decl.annotations:
        cap = _TYPE_ANN_TO_CAPABILITY.get(ann.name)
        if cap:
            caps.add(cap)

    for method in type_decl.methods:
        for ann in method.annotations:
            cap = _METHOD_ANN_TO_CAPABILITY.get(ann.name)
            if cap:
                caps.add(cap)

    for fld in type_decl.fields:
        cap = _INJECTED_TYPES_TO_CAPABILITY.get(fld.type_name)
        if cap:
            caps.add(cap)
    for method in type_decl.methods:
        if method.is_constructor:
            for p in method.parameters:
                cap = _INJECTED_TYPES_TO_CAPABILITY.get(p.type_name)
                if cap:
                    caps.add(cap)

    for sup in (*type_decl.extends, *type_decl.implements):
        cap = _SUPERTYPE_TO_CAPABILITY.get(sup)
        if cap:
            caps.add(cap)

    return sorted(caps)
```

Add to `TypeDecl`:

```python
@dataclass
class TypeDecl:
    ...
    capabilities: list[str] = field(default_factory=list)
```

Populate in `_parse_type` at the bottom of the function, **after** the
`TypeDecl` is constructed, by reassigning `type_decl.capabilities =
infer_capabilities_for_type(type_decl)`. (Cannot compute at construction
time because methods/fields are populated first.)

Export `infer_capabilities_for_type` and the three detector tables in
`__all__`. Bump `ONTOLOGY_VERSION` from `2` to `3`.

**Note on injection-driven detection.** Mirrors the existing
`_INJECT_FIELD_ANNOTATIONS` / Lombok-RAC logic in spirit, but operates
on *injected type names*, not on injection annotations. Constructor
params count as injection (Spring auto-wires single-ctor); Lombok
`@RequiredArgsConstructor` is implicitly covered because final fields
are already in `type_decl.fields`. Field-level `@Autowired` is also
covered because the field is in `type_decl.fields` regardless.

### `graph_enrich.py`

Add `capabilities: list[str]` to `ChunkEnrichment`:

```python
@dataclass
class ChunkEnrichment:
    ...
    capabilities: list[str] = field(default_factory=list)
```

In the function that builds `ChunkEnrichment` from a `TypeDecl` (currently
around line 316 — search for `infer_role_for_type(encl)`), add:

```python
capabilities=list(encl.capabilities),
```

If brownfield overrides land later (Phase 1 of
`PLAN-BROWNFIELD-ROLE-OVERRIDES.md`), they are merged here, not in
`ast_java.py`. Keep the AST module I/O-free.

### `build_ast_graph.py`

Schema:

```python
_SCHEMA_NODE = (
    "CREATE NODE TABLE Symbol("
    "id STRING PRIMARY KEY, "
    "kind STRING, name STRING, fqn STRING, package STRING, "
    "module STRING, microservice STRING, "
    "filename STRING, start_line INT64, end_line INT64, "
    "start_byte INT64, end_byte INT64, "
    "modifiers STRING[], annotations STRING[], capabilities STRING[], "
    "role STRING, signature STRING, parent_id STRING, resolved BOOLEAN"
    ")"
)
```

`_node_row` defaults:

```python
"capabilities": [],
```

`_CREATE_SYMBOL` Cypher:

```python
"... annotations: $annotations, capabilities: $capabilities, role: $role, ..."
```

Type write (around line 612):

```python
conn.execute(_CREATE_SYMBOL, _node_row(
    ...
    annotations=[a.name for a in d.annotations],
    capabilities=list(d.capabilities),
    role=infer_role_for_type(d),
    ...
))
```

Methods, packages, files do not get capabilities (empty list). The
default in `_node_row` already handles this — no extra code needed for
non-type nodes.

### Filter strategy — pushdown vs post-filter (READ FIRST)

The `capability` filter parameter added to user-facing tools must respect
the same `limit` semantics as the rest of the API: when a user asks for
`limit=N` results, they expect up to `N` results that *match the
filters*, not `N` candidates of which some happen to match.

This rules out naive post-filtering at the response boundary. Two
acceptable strategies, ordered by preference:

1. **Storage-layer pushdown (preferred).**
   - Kuzu-backed tools (`list_by_role`, `list_by_annotation`,
     `find_implementors`, `find_subclasses`, `find_injectors`): pass the
     `capability` parameter into the corresponding `KuzuGraph` method
     and add `$capability IN s.capabilities` to the Cypher `WHERE`.
     Apply `LIMIT` *after* the filter. The same pattern as the existing
     `module` / `microservice` scoping.
   - LanceDB-backed `codebase_search`: extend `_build_extra_predicates`
     in `search_lancedb.py` with a capability predicate against the
     `capabilities` list column (LanceDB SQL list-contains; verify the
     exact syntax for the project's LanceDB version — `array_has`,
     `array_position(...) > 0`, or `'X' = ANY(capabilities)` are all
     candidates). The predicate is conditioned on the column actually
     existing in the schema, mirroring how `role IN` is gated on
     `"role" in columns`.

2. **Post-filter with `over-fetch` widening (fallback).**
   Acceptable only if storage pushdown is impossible for some tool
   (e.g. LanceDB version doesn't expose a list predicate). The query
   fetches `limit * K` rows (K ~= 5 for capability filter), filters
   in Python, and trims to `limit`. Document the over-fetch factor
   in code.

**Do not** post-filter without over-fetch widening. That silently
under-delivers results and breaks the API contract.

The `capability` parameter on each tool keeps the same Pydantic shape
regardless of strategy: `capability: str | None = None`. Only the
implementation differs per backend.

### `kuzu_queries.py`

`SymbolHit`:

```python
@dataclass
class SymbolHit:
    ...
    capabilities: list[str]
```

`_symbol_return_for`:

```python
f"... {alias}.annotations AS annotations, "
f"{alias}.capabilities AS capabilities, "
f"{alias}.role AS role, ..."
```

`_row_to_symbol`:

```python
capabilities=list(row.get("capabilities") or []),
```

New helper, mirroring `list_by_role`:

```python
def list_by_capability(
    self, capability: str, *,
    module: str | None = None,
    microservice: str | None = None,
    limit: int = 100,
) -> list[SymbolHit]:
    filters = ["$capability IN s.capabilities"]
    params: dict[str, Any] = {"capability": capability}
    filters.extend(_scope_filters("s", module=module, microservice=microservice, params=params))
    where = " AND ".join(filters)
    query = f"MATCH (s:Symbol) WHERE {where} RETURN {_SYMBOL_RETURN} LIMIT {int(limit)}"
    return [_row_to_symbol(r) for r in self._rows(query, params)]
```

`trace_flow` seeding — **two coordinated changes** (both required).

The Kuzu seed query alone is insufficient: by the time FQNs reach Kuzu,
LanceDB has already discarded any chunk whose role isn't in the
entrypoint allow-list, so the Kuzu `OR capability` branch is dead code
for role=OTHER classes (e.g. a plain Quartz `Job` implementor). Both
sides must learn about capabilities.

1. **`kuzu_queries.py::_run_seed_query`** — accept entries by role *or*
   by capability:

   ```python
   # was:
   filters.append("s.role IN $entry_roles")
   # becomes:
   filters.append(
       "(s.role IN $entry_roles OR ANY(c IN s.capabilities WHERE c IN $entry_capabilities))"
   )
   params["entry_capabilities"] = ["MESSAGE_LISTENER", "SCHEDULED_TASK"]
   ```

2. **`server.py::trace_flow`** — widen the LanceDB pre-filter so
   capability-only entrypoints reach the Kuzu seed step at all.
   Today's `_seed` helper passes `role_in=entry_roles`, which is
   exclusive. After the change, `_seed` accepts a tuple
   `(role_allowlist, capability_allowlist)` and `_build_extra_predicates`
   is extended with a capability predicate (per the **Filter strategy**
   section above). The combined LanceDB predicate becomes:

   ```sql
   (role IN ('CONTROLLER','COMPONENT','SERVICE','FEIGN_CLIENT')
    OR <list-contains>(capabilities, 'MESSAGE_LISTENER')
    OR <list-contains>(capabilities, 'SCHEDULED_TASK'))
   ```

   The fallback pass (`role_allowlist=None`) stays as-is.

`_FLOW_STAGES` waterfall — the stage 1 / stage 2 BFS keeps its existing
role-only predicate. Producers/listeners only appear as stage 0
entrypoints, not as stage 1/2 destinations, so no schema change to
the staged walk is needed.

**Why the coordination matters:** without change (2), change (1) is
unreachable for any class whose primary role is `OTHER` — exactly the
cohort capability seeding is designed to surface. Implement and test
both in the same PR; verify with a fixture where a class implements
`org.quartz.Job` (role=OTHER, capability=SCHEDULED_TASK) and confirm
`trace_flow` picks it as a stage-0 seed.

### `server.py`

1. New MCP tool `list_by_capability`, mirroring `list_by_role`:

```python
@mcp.tool(
    description=(
        "All graph symbols carrying a given capability "
        "(MESSAGE_LISTENER|MESSAGE_PRODUCER|SCHEDULED_TASK|EXCEPTION_HANDLER). "
        "Capabilities are derived from method/type annotations and injected "
        "types; a class can carry several. Pair with `list_by_role` for "
        "primary-purpose questions."
    ),
)
def list_by_capability(
    capability: str = Field(
        description="MESSAGE_LISTENER|MESSAGE_PRODUCER|SCHEDULED_TASK|EXCEPTION_HANDLER",
    ),
    module: str | None = Field(default=None, description="..."),
    microservice: str | None = Field(default=None, description="..."),
    limit: int = Field(default=100, ge=1, le=500),
) -> list[SymbolHitModel]:
    ...
```

2. Add `capabilities: list[str] = []` to the `SymbolHitModel` Pydantic
   shape so it appears on every tool that returns symbols.

3. In `codebase_search`, **all** `find_*` tools (`find_implementors`,
   `find_subclasses`, `find_injectors`), `list_by_role`, and
   `list_by_annotation`, add an optional parameter
   `capability: str | None` that, when set, AND-filters results to those
   carrying that capability. **Implementation must use storage-layer
   pushdown** — see the **Filter strategy** section. Naive post-filter
   without over-fetch widening violates the `limit` contract and is not
   acceptable.

   For `find_injectors` specifically: the natural semantic is to filter
   on the *consumer* side (`edge.src.capabilities`), since the user is
   asking "which message-listener classes inject Foo?" — not the target
   type's capabilities. Push this into the Cypher `WHERE` on the source
   node alias.

4. `trace_flow` docstring update: replace the entrypoint list with
   "CONTROLLER / COMPONENT / SERVICE / FEIGN_CLIENT, plus types carrying
   MESSAGE_LISTENER or SCHEDULED_TASK capabilities".

5. Update the multi-line search-tool description block to mention
   capabilities and `list_by_capability`.

### `search_lancedb.py`

The Java chunk reranker pulls `role` for `_ROLE_SCORE_WEIGHTS`. **Do not
add capability-bias weights in this PR.** The capabilities feature is
about *findability* (entry seeding, explicit filter), not about implicit
ranking. A future change can introduce a `_CAPABILITY_SCORE_WEIGHTS`
table once we have telemetry on which capabilities deserve a nudge.

Three concrete edits required:

1. **`JAVA_ENRICHED_COLUMNS`** — add `"capabilities"` so the column is
   selected when present. (Existing schema-presence guard
   `[c for c in JAVA_ENRICHED_COLUMNS if c in enriched_cols]` makes this
   safe even if a stale index lacks the column.)

2. **`_build_extra_predicates`** — accept a new `capability: str | None`
   keyword argument and emit a list-contains predicate against the
   `capabilities` column when the column exists in `columns`. See the
   **Filter strategy** section for the exact LanceDB syntax to verify.

3. **`run_search`** — surface a `capability: str | None` parameter and
   forward it to `_build_extra_predicates`. `trace_flow`'s seeding
   helper (`server.py::_seed`) consumes this for the entrypoint
   widening.

Plumb `capabilities` through the response path: extend `CodeChunkHit`
with `capabilities: list[str] = Field(default_factory=list)` and map it
in `_rows_to_hits` via `_clean_str_list(r.get("capabilities"))` so
callers see them in results.

### Documentation

- `README.md` — add a section "Capabilities" describing the multi-tag
  axis, the initial capability set, and `list_by_capability`. Keep the
  existing "Roles" section intact.
- `docs/CODEBASE_REQUIREMENTS.md` — note the type-level granularity choice
  and the deferred per-method storage (link to this plan).
- MCP server `instructions` string in `server.py` — one extra sentence
  pointing at `list_by_capability` for behavioural questions about
  message-driven / scheduled / exception-handling code.

## Ontology version

Bump `ONTOLOGY_VERSION` `2 → 3` in `ast_java.py`. The Kuzu graph schema
changes (new `capabilities STRING[]` column) so a full rebuild is
required: `LANCEDB_MCP_ALLOW_REFRESH=1` + `refresh_code_index(confirm=true)`.
No online migration is provided — breaking-change policy permits this.

`GraphMeta.ontology_version` is consulted on read; existing graphs with
version `2` will be detected and the reader should refuse to start with a
clear error message pointing at the rebuild command. Add this guard if it
does not already exist (search `kuzu_queries.py` / `graph_enrich.py` for
`ontology_version`; if absent, add a one-line check in `KuzuGraph.get`).

## Test plan

Tests live alongside the existing suite in `tests/`. Reuse the synthetic
fixture pattern in `tests/test_lancedb_e2e.py`.

### Unit (pure AST)

In a new `tests/test_ast_java_capabilities.py` (or alongside whatever
unit harness exists for `ast_java.py` today):

1. Type with `@KafkaListener` method → `capabilities == ["MESSAGE_LISTENER"]`.
2. Type with `@KafkaListener` *and* `@Scheduled` methods → both, sorted.
3. `@Service` with `KafkaTemplate` field → `role == "SERVICE"`,
   `capabilities == ["MESSAGE_PRODUCER"]`. Confirms role/capability
   independence.
4. `@Service` with `KafkaTemplate` ctor-param → same result. Confirms
   constructor injection picks up.
5. `@Service` with `@KafkaListener` method **and** `KafkaTemplate` field →
   `capabilities == ["MESSAGE_LISTENER", "MESSAGE_PRODUCER"]`. Confirms
   composition.
6. `@RestControllerAdvice` class → `capabilities` contains
   `"EXCEPTION_HANDLER"`.
7. Class implementing `Job` with no annotations → `capabilities ==
   ["SCHEDULED_TASK"]`.
8. Plain `@Service` with no method annotations and no listener-y fields
   → `capabilities == []`.
9. Determinism: capabilities are sorted and deduplicated regardless of
   field/method declaration order.

### Integration (Kuzu round-trip)

In `tests/test_lancedb_e2e.py` or a sibling:

1. Build the synthetic fixture with one of each capability above.
   Refresh the index.
2. Assert `list_by_capability("MESSAGE_LISTENER")` returns the expected
   FQNs.
3. Assert `list_by_role("SERVICE", capability="MESSAGE_PRODUCER")`
   AND-filters correctly **with the full `limit`**: when the fixture
   contains 50 services of which 5 are also producers, `limit=50` must
   return all 5, not "5 of the first 50 services". This catches the
   post-filter regression.
4. `trace_flow("when an order event arrives, ...")` returns the
   `MESSAGE_LISTENER` class as a stage-0 seed even when its primary role
   is `SERVICE`.
5. **`trace_flow` capability-only seeding:** add a fixture class that
   `implements org.quartz.Job` with no Spring stereotype (role=OTHER,
   capability=SCHEDULED_TASK). Assert it appears as a stage-0 seed for a
   schedule-related query. This is the regression guard for the
   LanceDB pre-filter widening.
6. **`codebase_search` capability filter:** verify that
   `codebase_search(query="...", capability="MESSAGE_LISTENER", limit=N)`
   returns up to N hits *all carrying* the capability — not N candidates
   filtered down.
7. `GraphMeta.ontology_version == 3` after rebuild.

### Regression

All current role-keyed tests must keep passing unchanged. Capabilities
must default to `[]` for every non-type node and for types with no
detector hit.

## Out of scope (do not implement in this plan)

- Method-level capability storage on the graph (option β from design).
  Aggregate to type level; revisit if the deferred call-graph layer
  lands.
- `REST_CLIENT` capability — see separate proposal.
- Capability-aware ranking weights in `_ROLE_SCORE_WEIGHTS`.
- Brownfield overrides — handled in `PLAN-BROWNFIELD-ROLE-OVERRIDES.md`.
  Capability detection in this plan is annotation/type-name based only.
- Non-Spring frameworks (Micronaut `@Topic`, Jakarta EE `@MessageDriven`).
  Add detector entries in a follow-up if the user surfaces a need.

## Rollout

Single PR. The change is breaking (ontology bump → rebuild required) but
the rebuild is the only user-visible action. After merge:

1. User runs `refresh_code_index(confirm=true)` once.
2. New `capabilities` field is populated and the new MCP tool is live.
3. Existing tools' behaviour for callers who don't set the new
   `capability` parameter is unchanged.
