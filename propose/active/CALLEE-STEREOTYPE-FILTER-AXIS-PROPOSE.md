# CALLEE-STEREOTYPE-FILTER-AXIS — preserve stereotype signal when brownfield overrides role

**Status**: active
**Author**: Dmitry Teryaev
**Date**: 2026-05-30
**Tracks**: [#237](https://github.com/HumanBean17/java-codebase-rag/issues/237)

## TL;DR

- `Symbol.role` is a single field merging two concerns: **stereotype** (Spring `@Service`, `@Repository`, etc.) and **brownfield semantic role** (`@CodebaseRole(CLIENT)`, etc.). When both exist, brownfield wins.
- `callee_declaring_role` on CALLS edges reads from `Symbol.role` at write time, so it reflects only the final overridden value — losing the stereotype signal.
- Filtering `callee_declaring_role='SERVICE'` silently drops cross-service calls from `@Service @CodebaseRole(CLIENT)` classes — arguably the most important calls to trace.
- **Solution**: add a parallel `callee_stereotype` column to CALLS edges that captures the pre-brownfield role, plus matching `EdgeFilter` fields. Additive, non-breaking.
- Ontology bump 15 → 16. One re-index.

## §1 — Frame

`callee_declaring_role` is the CALLS edge attribute that lets agents project "calls to services," "calls to repositories," etc. It is populated at graph-build time by reading `Symbol.role` of the callee's declaring type (`build_ast_graph.py:1175-1187`).

`Symbol.role` is set by `resolve_role_and_capabilities` (`graph_enrich.py:672`) which runs five layers, the strongest being Layer C (`@CodebaseRole` in source). When a class carries both `@Service` and `@CodebaseRole(CLIENT)`:

- `Symbol.role` becomes `CLIENT` (brownfield wins)
- `callee_declaring_role` on all CALLS edges pointing at methods of this class becomes `CLIENT`
- `edge_filter={callee_declaring_role: 'SERVICE'}` excludes these calls

This is a signal loss. The class is *both* a Spring `@Service` and an architectural client. An agent asking "what does this method call that delegates to other services?" misses the cross-service calls — which are the ones most worth tracing.

The issue is not that brownfield overrides exist — they are correct and intentional. The issue is that `Symbol.role` conflates two independent dimensions into one field, and CALLS filtering only sees the merged result.

## §2 — Design principles

1. **Additive, non-breaking.** Existing `callee_declaring_role` semantics unchanged. No existing query breaks.
2. **Pre-brownfield role is the stereotype.** The role after `resolve_role_and_capabilities` steps 1–3 (built-in inference, Layer B annotation map, Layer A meta-annotation walk) but before steps 4–5 (Layer C `@CodebaseRole`, Layer B per-FQN map) is the "stereotype" role. This is what Spring + config + meta-chain determined before brownfield in-source overrides kicked in.
3. **Both axes independently filterable.** `callee_stereotype='SERVICE' AND callee_declaring_role='CLIENT'` captures exactly the cross-service pattern from #237.
4. **Single source of truth preserved.** `VALID_ROLES` still governs both fields. No new role vocabulary.
5. **Minimal schema change.** One new CALLS column, one new `GraphTables` dict, three new `EdgeFilter` fields. No new node/edge tables.

## §3 — The proposed surface

### §3.1 — GraphTables: `type_stereotype_by_node_id`

New dict alongside existing `type_role_by_node_id` (`build_ast_graph.py:401`):

```python
type_stereotype_by_node_id: dict[str, str] = field(default_factory=dict)
```

Populated in `_write_nodes` (`build_ast_graph.py:2569-2574`) after `resolve_role_and_capabilities` returns `(role, capabilities)`:

```python
role, capabilities = resolve_role_and_capabilities(...)
tables.type_role_by_node_id[entry.node_id] = role
tables.type_stereotype_by_node_id[entry.node_id] = _stereotype_role(role, overrides, entry, meta_chain)
```

The stereotype role is computed by replaying steps 1–3 of the resolver (or equivalently, running `resolve_role_and_capabilities` with a stripped `overrides` that omits Layer C / per-FQN map). See §3.2 for the simpler approach.

### §3.2 — How to extract the stereotype role

**Approach: snapshot before Layer C.**

In `resolve_role_and_capabilities` (`graph_enrich.py:672`), the role value after step 3 (meta-annotation walk, line 721–742) and before step 4 (`@CodebaseRole`, line 744+) is the stereotype role. Rather than replaying the resolver, capture it:

```python
# After step 3 (line ~742), before step 4 (line ~744):
stereotype_role = role
```

Return it as a third element: `-> tuple[str, list[str], str]` (role, capabilities, stereotype_role).

At the call site (`build_ast_graph.py:2569`):

```python
role, capabilities, stereotype_role = resolve_role_and_capabilities(...)
tables.type_role_by_node_id[entry.node_id] = role
tables.type_stereotype_by_node_id[entry.node_id] = stereotype_role
```

When there is no brownfield override, `stereotype_role == role`. The dict is still populated for every type — no conditional logic at read time.

### §3.3 — CALLS DDL change

Current (`build_ast_graph.py:2430-2434`):

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING)
```

Proposed:

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING,
  callee_stereotype STRING)
```

`callee_stereotype` reads from `tables.type_stereotype_by_node_id` (analogous to `_callee_declaring_role_at_write`). Default `"OTHER"` if parent is missing or unroleable.

### §3.4 — New helper: `_callee_stereotype_at_write`

Mirrors `_callee_declaring_role_at_write` (`build_ast_graph.py:1175-1187`):

```python
def _callee_stereotype_at_write(
    tables: GraphTables,
    dst_id: str,
    *,
    member_by_id: dict[str, MemberEntry],
) -> str:
    if dst_id in tables.phantoms:
        return "OTHER"
    member = member_by_id.get(dst_id)
    if member is None:
        return "OTHER"
    return tables.type_stereotype_by_node_id.get(member.parent_id, "OTHER")
```

### §3.5 — `CallsRow` dataclass

Add field to `CallsRow` (`build_ast_graph.py:173-183`):

```python
@dataclass
class CallsRow:
    ...
    callee_declaring_role: str = "OTHER"
    callee_stereotype: str = "OTHER"  # new
```

### §3.6 — `EdgeFilter` extension

Add three fields to `EdgeFilter` (`mcp_v2.py:142-172`):

```python
class EdgeFilter(BaseModel):
    ...
    # existing role axes (final resolved role, includes brownfield)
    callee_declaring_role: str | None = None
    callee_declaring_roles: list[str] | None = None
    exclude_callee_declaring_roles: list[str] | None = None

    # new stereotype axes (pre-brownfield role)
    callee_stereotype: str | None = None
    callee_stereotypes: list[str] | None = None
    exclude_callee_stereotypes: list[str] | None = None
```

Validation: the three new fields are mutually exclusive with each other (same pattern as existing `_role_axes_mutually_exclusive` validator). They are **not** mutually exclusive with the existing role axes — combining `callee_stereotype='SERVICE' AND callee_declaring_role='CLIENT'` is the primary use case from #237.

### §3.7 — `kuzu_queries.py` filtering

Extend `calls_for_origin` (`kuzu_queries.py:720-769`) with the same pattern as existing role filtering:

```python
if callee_stereotype is not None:
    wh_parts.append("e.callee_stereotype = $callee_stereotype")
    params["callee_stereotype"] = callee_stereotype
if callee_stereotypes:
    wh_parts.append("e.callee_stereotype IN $callee_stereotypes")
    params["callee_stereotypes"] = callee_stereotypes
if exclude_callee_stereotypes:
    wh_parts.append("NOT (e.callee_stereotype IN $exclude_callee_stereotypes)")
    params["exclude_callee_stereotypes"] = exclude_callee_stereotypes
```

### §3.8 — Ontology version

Bump `ONTOLOGY_VERSION` from 15 to 16 in `ast_java.py:86`.

## §4 — Use-case re-walk

| # | Use case | Today (#237-symptom) | Tomorrow |
|---|---|---|---|
| UV1 | `@Service @CodebaseRole(CLIENT)` class makes cross-service calls | `callee_declaring_role='CLIENT'`; filter for `SERVICE` misses them | `callee_stereotype='SERVICE'` finds them; `callee_declaring_role='CLIENT'` still works; both combinable |
| UV2 | Agent asks "what does this method delegate to other services?" | `edge_filter={callee_declaring_role: 'SERVICE'}` misses CLIENT-overridden services | `edge_filter={callee_stereotype: 'SERVICE'}` captures all Spring `@Service` callees regardless of brownfield override |
| UV3 | Agent asks "what are the cross-service calls?" | Must know about `@CodebaseRole` and use `callee_declaring_role='CLIENT'` | `callee_stereotype='SERVICE' AND callee_declaring_role='CLIENT'` — both axes combined |
| UV4 | Agent drops accessor noise | `edge_filter={exclude_callee_declaring_roles: ['ENTITY','DTO']}` works | Unchanged. Same filter still works; `callee_declaring_role` semantics identical to today |
| UV5 | Class with no brownfield override (`@Service` only) | `callee_declaring_role='SERVICE'` | `callee_stereotype='SERVICE'` returns the same result (no override, both fields equal) |
| UV6 | Class with brownfield override only (`@CodebaseRole(CLIENT)`, no Spring stereotype) | `callee_declaring_role='CLIENT'` | `callee_stereotype='OTHER'` (no stereotype detected). Both fields differ, both independently filterable |
| UV7 | `@Repository @CodebaseRole(SERVICE)` (config class acting as persistence gateway) | `callee_declaring_role='SERVICE'` — repository signal lost | `callee_stereotype='REPOSITORY'` preserves the `@Repository` signal; `callee_declaring_role='SERVICE'` preserves the brownfield intent |

### Awkward cases

- **UV5/UV6** (no override / override only): `callee_stereotype == callee_declaring_role` in the no-override case. This is expected and correct — no information is lost or duplicated, and queries against either axis return the same result.
- **UV6** (override only, no stereotype): `callee_stereotype='OTHER'`. This is honest — the class had no framework stereotype. Agents filtering for `callee_stereotype='SERVICE'` won't find it, which is correct: it wasn't annotated as a service.
- **Layer B per-FQN map** (step 5 in the resolver): the per-FQN map runs *after* the stereotype snapshot point. FQN-mapped roles appear in `callee_declaring_role` but not in `callee_stereotype`. This is intentional — the per-FQN map is a brownfield override, not a stereotype inference.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Change `callee_declaring_role` semantics | Non-breaking requirement. Existing queries must continue to work identically. |
| Store a list of all roles (union) on the edge | Kuzu `STRING[]` column support is fine, but it complicates every filter clause and has no clear precedence rule. Two independent string columns are simpler. |
| Add `callee_stereotype` to other edge types (`HTTP_CALLS`, `ASYNC_CALLS`) | Per CALLS-NOISE-AND-RESOLUTION Decision 7: those edges encode role in their endpoint kind already. |
| Add `callee_capability` / `callee_annotation` filter axes | Per CALLS-NOISE-AND-RESOLUTION Decision 32: out of scope. Re-opens via a new propose. |
| Rename `callee_declaring_role` to `callee_role` | Breaking for no benefit. Both names are descriptive as-is. |

## §6 — Migration plan — single PR

One code PR. No multi-PR plan needed — this is a bounded additive schema change.

**Title**: `feat(schema): add callee_stereotype to CALLS for pre-brownfield role filtering`

**Scope**:
- `ast_java.py` — bump `ONTOLOGY_VERSION` 15 → 16
- `graph_enrich.py` — return `stereotype_role` as third element from `resolve_role_and_capabilities`; snapshot role after step 3, before step 4
- `build_ast_graph.py` — add `type_stereotype_by_node_id` to `GraphTables`; add `callee_stereotype` to `CallsRow`; add `_callee_stereotype_at_write`; populate at all `_emit_call_edge` call sites; add to CALLS DDL; store at write time
- `mcp_v2.py` — add `callee_stereotype` / `callee_stereotypes` / `exclude_callee_stereotypes` to `EdgeFilter` with mutual-exclusivity validator; wire into `neighbors_v2` call path
- `kuzu_queries.py` — add stereotype filter parameters to `calls_for_origin`; add to RETURN clause; wire Cypher WHERE predicates
- `java_ontology.py` — register `callee_stereotype` as a known filterable attribute on `CALLS` in `EDGE_SCHEMA`
- README — document new filter axes; "Re-index required" callout for ontology 16

**Test summary**:
- `type_stereotype_by_node_id` populated for all types in `bank-chat-system`
- `callee_stereotype == callee_declaring_role` when no brownfield override (UV5)
- `callee_stereotype != callee_declaring_role` when `@CodebaseRole` overrides (UV1/UV7)
- `edge_filter={callee_stereotype: 'SERVICE'}` returns CALLS to `@Service` types regardless of brownfield override
- `edge_filter={callee_stereotype: 'SERVICE', callee_declaring_role: 'CLIENT'}` (combined via existing AND logic) returns cross-service calls specifically
- `EDGE_SCHEMA` snapshot test reflects the new column

## §7 — Decisions taken

1. **`callee_stereotype` is a separate CALLS column**, not a multi-valued field on `callee_declaring_role`. Two independent string columns compose naturally with AND in `EdgeFilter`.
2. **Stereotype is "role before Layer C and Layer B per-FQN map."** Steps 1–3 (built-in, Layer B annotation map, Layer A meta-chain) are stereotype inference; steps 4–5 are brownfield overrides.
3. **Both axes can be combined.** `callee_stereotype='SERVICE' AND callee_declaring_role='CLIENT'` is the primary use case. They are not mutually exclusive with each other.
4. **`type_stereotype_by_node_id` is populated for every type**, even when `stereotype == role`. No conditional logic at read time.
5. **Layer B annotation map (step 2) is included in stereotype.** User-configured annotation-to-role mappings are treated as stereotype-level, not brownfield overrides. Only in-source `@CodebaseRole` (step 4) and per-FQN map (step 5) are brownfield.
6. **No new edge/node tables.** One new column on existing CALLS, one new dict on `GraphTables`, three new `EdgeFilter` fields.
7. **Single PR.** The change is bounded and additive — no multi-PR plan needed.
8. **Ontology bump 15 → 16.** The new column enriches CALLS semantics; a re-index is required.

## §8 — Risks and mitigations

| Risk | Mitigation |
|---|---|
| `resolve_role_and_capabilities` return type change (`tuple[str, list[str]]` → `tuple[str, list[str], str]`) breaks callers | Grep all call sites; update destructuring. The function has exactly one call site (`build_ast_graph.py:2569`). |
| `callee_stereotype` confuses users who expect it to match `callee_declaring_role` | Document in README + `docs/CONFIGURATION.md`: stereotype is pre-brownfield; declaring role is final. When no brownfield override exists, they are identical. |
| Performance: one more column on CALLS DDL | STRING column, same size as existing `callee_declaring_role`. Kuzu handles this without issue. |
| Agents use `callee_stereotype` when they should use `callee_declaring_role` | HINTS update: high-fanout template mentions both axes and when to use each. |

## Appendix A — Concrete DDL diff

```sql
-- CALLS DDL (build_ast_graph.py:2430-2434)
-- Before:
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING);

-- After:
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING,
  callee_stereotype STRING);
```

## Appendix B — Cross-propose references

- Builds on [`CALLS-NOISE-AND-RESOLUTION-PROPOSE.md`](../completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md) — adds a second role axis to the CALLS edge; extends `EdgeFilter` with matching fields.
- Resolves [#237](https://github.com/HumanBean17/java-codebase-rag/issues/237).
- Decision 29 of CALLS-NOISE-AND-RESOLUTION is preserved: `callee_declaring_role` still picks up brownfield transparently. The new `callee_stereotype` column provides the parallel pre-brownfield signal.
