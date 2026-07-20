> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: NEIGHBORS-DOT-KEY-TRAVERSAL

Status: **completed** (PR-1 landed in [#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)). Source propose:
[`propose/completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md`](../../propose/completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md)
(issue [#162](https://github.com/HumanBean17/java-codebase-rag/issues/162)).

Depends on: **none** (query-time / MCP surface only; graph schema unchanged).

## Goal

- **`neighbors` accepts three `DECLARES.*` composed dot-keys** as `edge_types` values and executes a single 2-hop Cypher walk per key, returning terminal `Client` / `Producer` / `Route` nodes with `via_id` in `Edge.attrs`.
- **Parity with `describe.edge_summary`:** unfiltered result row counts match `edge_summary["<dot-key>"]["out"]` for the same type Symbol origin (edge-row semantics, not distinct methods).
- **Docs + MCP contract aligned:** `NodeRecord.edge_summary`, `server.py` tool descriptions, `README.md`, `docs/AGENT-GUIDE.md`, and `docs/EDGE-NAVIGATION.md` state that `DECLARES.*` dot-keys are navigable; `OVERRIDDEN_BY.*` remains describe-only.
- **Hint templates updated:** `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS`, `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS`, and `TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS` prescribe the single-call dot-key recipe (partial reversal of HINTS-ROAD-SIGNS decision #11 for this family only).

## Principles (do not relitigate in review)

- **What you see in `edge_summary` for `DECLARES.*` is what you can request in `neighbors`.** No second-class affordance for the three type-level member rollups.
- **`edge_type` in results echoes the dot-key** (e.g. `DECLARES.DECLARES_CLIENT`), not the terminal stored label — avoids implying a direct hop from the class.
- **`via_id` lives in `attrs` only** — intermediate method Symbol id; `origin_id` remains the class (or type) the agent passed.
- **v1 constraints:** outbound (`direction="out"`) only; origin must be a **type** Symbol (`class`, `interface`, `enum`, `record`, `annotation`). Wrong direction or non-type origin → `success=False` with a clear `message` (not a bare Pydantic boundary error).
- **`OVERRIDDEN_BY` and `OVERRIDDEN_BY.*` stay rejected** at validation (describe-time virtual keys; [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165) is separate).
- **No ontology bump, no re-index** — reads existing `DECLARES` / `DECLARES_CLIENT` / `DECLARES_PRODUCER` / `EXPOSES` edges only.
- **Hints policy is narrowly reversed:** only the three `TPL_DESCRIBE_TYPE_*_VIA_MEMBERS` templates may emit `DECLARES.*` dot-keys. **HINTS-V3 invariant preserved:** rendered **empty** `neighbors` structural hints still must not contain composed dot-key labels (`_filter_neighbors_dotkey_hints` unchanged).
- **Flat `neighbors` behavior unchanged** when `edge_types` contains only stored `EdgeType` literals.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | `mcp_v2.py`, `kuzu_queries.py`, `mcp_hints.py`, `server.py`, docs, tests | **No** | Partitioning flat vs composed in `neighbors_v2`; attr projection parity with flat hop; `requested_edge_types` echo; MCP JSON schema for `edge_types`; hint / road-signs doc drift | `test_mcp_v2_compose.py`, `test_mcp_v2.py`, `test_mcp_hints.py` | — |

**Landing order:** **PR-1** only (single code PR).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Implementation shape | One PR (`feat(neighbors): DECLARES.* dot-key 2-hop traversal`) |
| `ComposedEdgeType` | New `Literal` in `mcp_v2.py` for the three keys; `_NEIGHBOR_EDGE_TYPES_ADAPTER` accepts `list[EdgeType \| ComposedEdgeType]` |
| Cypher home | New `KuzuGraph.member_edge_traversal_for(type_id, composed_key)` in `kuzu_queries.py`; **one module-level `(composed_key → rel)` tuple** shared by `member_edge_rollup_for` and traversal (refactor rollup to use it) |
| Merge order | Append **flat** edges first (per origin, flat label order), then **composed** edges (per origin, `composed_keys` request order); then apply `offset`/`limit` on the combined list |
| Terminal attrs | Project the same columns flat `neighbors_v2` uses; add `via_id` from intermediate `m.id` |
| `NodeFilter` | Applies to **terminal** node (Client / Producer / Route), same as flat hops |
| Mixed request | `["DECLARES", "DECLARES.DECLARES_CLIENT"]` runs both paths; merge then `offset`/`limit` on combined list |
| `requested_edge_types` | Echo deduped request list including dot-keys (not terminal labels) |
| Rejection UX | `OVERRIDDEN_BY*` → `ValidationError` at adapter (unchanged). Composed constraint violations → `NeighborsOutput(success=False, message=…)` |
| Road-signs #11 | Reversed for **describe** type-rollup templates only; see Principles |

---

# PR-1 — `DECLARES.*` dot-key traversal in `neighbors`

## File-by-file changes

### 1. `kuzu_queries.py`

- Extract a single module-level constant, e.g. `_MEMBER_EDGE_COMPOSED_REL_MAP: tuple[tuple[str, str], ...]`, with the three `(composed_key, rel)` pairs. Refactor `member_edge_rollup_for` to iterate this constant (no duplicated tuple literals).
- Add `member_edge_traversal_for(self, type_id: str, composed_key: str) -> list[dict[str, Any]]` iterating the same map.
- Cypher (per propose), parameterized `rel`:

```cypher
MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:{rel}]->(term)
RETURN m.id AS via_id, label(e) AS stored_edge_type,
       term.id AS other_id,
       e.confidence AS confidence, e.strategy AS strategy,
       e.match AS match, e.mechanism AS mechanism,
       e.annotation AS annotation, e.field_or_param AS field_or_param,
       e.source AS source, e.call_site_line AS call_site_line,
       e.call_site_byte AS call_site_byte, e.arg_count AS arg_count,
       e.resolved AS resolved
```

- Raise or return empty for unknown `composed_key` (internal use only).

### 2. `mcp_v2.py`

- Add:

```python
ComposedEdgeType = Literal[
    "DECLARES.DECLARES_CLIENT",
    "DECLARES.DECLARES_PRODUCER",
    "DECLARES.EXPOSES",
]
NeighborEdgeType = EdgeType | ComposedEdgeType  # if needed for typing
```

- Replace `_NEIGHBOR_EDGE_TYPES_ADAPTER` annotation with `list[NeighborEdgeType]` (or equivalent) so `OVERRIDDEN_BY` / `OVERRIDDEN_BY.*` still fail validation.
- Update module comment above `EdgeType` (lines ~39–41): composed `DECLARES.*` keys **are** valid `neighbors` arguments; `OVERRIDDEN_BY.*` remain describe-only.
- Update `NodeRecord.edge_summary` `Field(description=…)`:
  - Remove “do not pass … `DECLARES.*`” prohibition.
  - Keep “do not pass … `OVERRIDDEN_BY.*`” prohibition.
- Refactor `neighbors_v2`:
  1. `validate_python(edge_types)` on the expanded adapter.
  2. Partition into `flat_labels: list[EdgeType]` and `composed_keys: list[ComposedEdgeType]`.
  3. If any `composed_keys` and `direction != "out"`: return `NeighborsOutput(success=False, message="Composed edge types require direction=\"out\"", …)` (exact wording per propose).
  4. Per `origin_id`, if any `composed_keys`: load Symbol row; if `data.kind` not in `_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP`, return `success=False` with message like `Composed edge types (DECLARES.DECLARES_CLIENT) require a type Symbol origin` (include first offending key in message).
  5. **Flat path:** existing single-hop Cypher when `flat_labels` non-empty (unchanged logic).
  6. **Composed path:** for each `(origin_id, composed_key)`, call `g.member_edge_traversal_for(origin_id, composed_key)`; build `Edge` rows:
     - `edge_type` = dot-key string (not `stored_edge_type` from Cypher)
     - `attrs` = projected terminal edge attrs + `via_id`
     - `other` = terminal node via `_load_node_record` / `_node_ref_from_row` + `NodeFilter` on terminal kind
  7. Merge: **flat edges first**, then composed (per § Resolved design decisions); apply `offset`/`limit` on the combined list.
  8. `requested_edge_types` = `list(dict.fromkeys(edge_types))` from the validated request — **not** `list(labels)` (today's flat-only echo). Must include dot-keys when requested.
- Change `neighbors_v2` signature `edge_types` parameter type to `list[NeighborEdgeType]` (or keep `list[EdgeType]` only on the MCP wrapper — see `server.py`).

### 3. `server.py`

- `neighbors` tool: widen `edge_types` field type to `list[mcp_v2.EdgeType | mcp_v2.ComposedEdgeType]` (or shared alias).
- Update `describe` and `neighbors` `description=` strings: `DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` are valid `edge_types` for type Symbol origins (`out` only); `OVERRIDDEN_BY*` still not.
- Optional: extend `_INSTRUCTIONS` edge label sentence with the three dot-keys (keep concise).

### 4. `mcp_hints.py`

- Rewrite templates (propose § scope):

```text
TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS =
  "clients via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])"
TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS =
  "routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])"
TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS =
  "producers via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_PRODUCER'])"
```

- Update `MCP_HINTS_FIELD_DESCRIPTION`: describe-type rollup hints **may** recommend the three `DECLARES.*` dot-keys; **empty** `neighbors` structural hints still never use dot-keys.
- Do **not** remove `_filter_neighbors_dotkey_hints` or `_COMPOSED_DOT_KEY_PREFIXES` (still blocks accidental dot-keys in neighbors empty-hint path).

### 5. `docs/AGENT-GUIDE.md`

- Rewrite “Composed `edge_summary` keys” bullets: single-call `neighbors(..., ['DECLARES.DECLARES_CLIENT'])` (and producer / exposes analogs) as primary recipe; keep 2-hop atomic walk as alternative if useful.
- Update “Virtual keys … not valid `EdgeType`” paragraph: split `DECLARES.*` (navigable) vs `OVERRIDDEN_BY.*` (describe-only).

### 6. `docs/EDGE-NAVIGATION.md`

- Under `DECLARES_CLIENT`, `DECLARES_PRODUCER`, and `EXPOSES` **type_subject** rows: add dot-key one-liner, e.g. `neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])` alongside existing two-hop recipe.

### 7. `README.md`

- MCP tool table / `neighbors` row: note three optional composed `edge_types` for type Symbols; link propose or AGENT-GUIDE.
- No “Re-index required” callout change (none needed).

### 8. `tests/test_mcp_v2_compose.py`

- **Rename/split** `test_neighbors_rejects_overridden_by_and_dot_keys`:
  - `test_neighbors_still_rejects_overridden_by` — `OVERRIDDEN_BY` and `OVERRIDDEN_BY.DECLARES_CLIENT` (or similar) still `ValidationError`.
  - Remove dot-key rejection assertion from the old test.
- **Add** (names from propose — use fixture helpers like `test_describe_class_with_brownfield_clients_emits_composed_key` for origin discovery):

| # | Test name | Assert |
| --- | --- | --- |
| 1 | `test_neighbors_declares_dot_key_client` | Type with `DECLARES.DECLARES_CLIENT` rollup → Clients, `edge_type` dot-key, `attrs["via_id"]` set |
| 2 | `test_neighbors_declares_dot_key_producer` | Same for producer |
| 3 | `test_neighbors_declares_dot_key_exposes` | Same for routes |
| 4 | `test_neighbors_dot_key_mixed_with_flat` | Mixed types return both member Symbols and Clients |
| 5 | `test_neighbors_dot_key_inbound_rejected` | `direction="in"` → `success=False`, clear message |
| 6 | `test_neighbors_dot_key_method_origin_rejected` | Method id + dot-key → `success=False`, type-origin message |
| 7 | `test_neighbors_dot_key_count_matches_edge_summary` | `len(results)` == `edge_summary` out count (no limit) |
| 8 | `test_neighbors_still_rejects_overridden_by` | (if not merged with row 1) |

### 9. `tests/test_mcp_v2.py`

- Update `test_neighbors_rejects_composed_edge_summary_key`: after dot-keys are valid at the adapter, a **method** origin + `DECLARES.DECLARES_CLIENT` must return `success=False` with the type-origin message (not `ValidationError`). Align with `test_neighbors_dot_key_method_origin_rejected` in compose (one test may subsume the other — keep coverage, avoid duplicate names).

### 10. `tests/test_mcp_hints.py`

- Update `test_hints_describe_type_symbol_clients_via_members_emits`, `_routes_…`, `_producers_…` expected strings to match new templates.
- Ensure `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints` still passes (neighbors empty branch only).
- Parametrized template length test (`TPL_DESCRIBE_TYPE_*`) still ≤120 chars after rewrite.

## Tests for PR-1

Implement every name in the table above plus hint tests. Regression:

- Existing flat `neighbors_v2` tests in `tests/test_mcp_v2.py` / `test_mcp_v2_compose.py` unchanged behavior (except `test_neighbors_rejects_composed_edge_summary_key` expectation flip above).
- `test_neighbors_edge_type_adapter_accepts_overrides` unchanged.

## Definition of done (PR-1)

- [x] `neighbors_v2` accepts the three `DECLARES.*` dot-keys; flat edge types unchanged.
- [x] Composed results: dot-key `edge_type`, terminal `other`, `via_id` in `attrs`, full attr projection aligned with flat hops.
- [x] Type-only origin + `out` only enforced with `success=False` messages.
- [x] `OVERRIDDEN_BY` / `OVERRIDDEN_BY.*` still rejected at validation.
- [x] `edge_summary` field description and agent docs match behavior.
- [x] Three describe hint templates prescribe dot-key calls; `MCP_HINTS_FIELD_DESCRIPTION` updated.
- [x] All named tests green; `ruff` + full `pytest tests` (non-heavy) pass.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Extract `_MEMBER_EDGE_COMPOSED_REL_MAP` + traversal | `kuzu_queries.py` | Rollup refactored; manual Cypher returns rows with `via_id` |
| 2 | Add `ComposedEdgeType` + adapter | `mcp_v2.py` | Adapter accepts dot-keys; rejects `OVERRIDDEN_BY` |
| 3 | Partition + composed dispatch; fix `requested_edge_types` echo | `mcp_v2.py` | Unit tests 1–7 pass; echo includes dot-keys |
| 4 | Widen MCP `edge_types` + descriptions | `server.py` | Tool schema shows composed literals |
| 5 | Update hint templates + field description | `mcp_hints.py` | Hint tests pass; HV20 still passes |
| 6 | Docs pass | `docs/*.md`, `README.md` | AGENT-GUIDE / EDGE-NAVIGATION consistent |
| 7 | Split/add tests | `tests/test_mcp_v2_compose.py`, `tests/test_mcp_v2.py`, `tests/test_mcp_hints.py` | Full propose test list green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Count mismatch between `describe` rollup and `neighbors` traversal | High | Share rel map between `member_edge_rollup_for` and `member_edge_traversal_for`; `test_neighbors_dot_key_count_matches_edge_summary` |
| 2 | Agents pass dot-keys on method ids after `DECLARES` walk | Medium | Clear `success=False` message; test `test_neighbors_dot_key_method_origin_rejected` |
| 3 | HINTS-ROAD-SIGNS / HV20 conflict | Medium | Only change describe templates; keep `_filter_neighbors_dotkey_hints` on neighbors empty branch |
| 4 | HINTS-V4 ([#163](https://github.com/HumanBean17/java-codebase-rag/issues/163)) overlap | Low | v4 N1 “type + DECLARES → describe first” becomes less critical; document in PR body; do not implement v4 in this PR |
| 5 | MCP clients cache old `EdgeType` enum | Low | Document in README + AGENT-GUIDE; no compatibility shim |

# Out of scope

- `OVERRIDDEN_BY.*` dot-key traversal ([#165](https://github.com/HumanBean17/java-codebase-rag/issues/165))
- Per-method `NodeRef` edge-presence signals ([#167](https://github.com/HumanBean17/java-codebase-rag/issues/167))
- Inbound (`direction="in"`) composed traversals
- `SearchHit` / `find` schema changes
- HINTS-V4 success-path catalog (`propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md`)
- Ontology version bump / graph builder / re-index

# Whole-plan done definition

1. PR-1 merged; propose in `propose/completed/` with `Status: landed`.
2. Issue #162 acceptance criteria met for `DECLARES.*` family (not #165 / #167).
3. `pytest tests` and `ruff check .` pass on `master` without heavy gate.

# Tracking

- `PR-1`: landed ([#171](https://github.com/HumanBean17/java-codebase-rag/pull/171))
- Follow-up: [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) — single source of truth for composed dot-keys
