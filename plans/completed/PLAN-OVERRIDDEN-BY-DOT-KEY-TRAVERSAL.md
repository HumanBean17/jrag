# Plan: OVERRIDDEN-BY-DOT-KEY-TRAVERSAL

Status: **completed** (PR-1 landed in [#189](https://github.com/HumanBean17/java-codebase-rag/pull/189)). Source propose:
[`propose/completed/OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE.md`](../../propose/completed/OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE.md)
(issue [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165)).

Depends on: **landed** [`plans/completed/PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md`](./PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md)
(PR [#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)) and stored `[:OVERRIDES]`
materialization (ontology 13+). No further graph-builder prerequisite.

## Goal

- **`neighbors` accepts four `OVERRIDDEN_BY*` composed dot-keys** as `edge_types` values and executes a single composed Cypher walk per key per origin, using **stored** `[:OVERRIDES]` for the dispatch hop (not `IMPLEMENTS|EXTENDS` + `signature` in the read path).
- **Parity with `describe.edge_summary` (merge-blocking):** for each override-axis key `K` on a qualifying method origin, unfiltered `len(neighbors(..., [K]).results)` equals `edge_summary[K]["out"]` from `override_axis_rollup_for` (edge-row semantics, not distinct terminal ids).
- **Axis symmetry with #162:** what `describe` advertises on method Symbols for `OVERRIDDEN_BY*` is what `neighbors` accepts; `DECLARES.*` behavior from #171 stays unchanged (type origin, `out` only).
- **Docs + MCP contract aligned:** remove describe-only rejection for `OVERRIDDEN_BY*`; document stored `OVERRIDES` direction equivalence; update hint templates from two-call workaround to single-call dot-keys.

## Principles (do not relitigate in review)

- **What you see in `edge_summary` for `OVERRIDDEN_BY*` is what you can request in `neighbors`.** Same principle as #162 for the override-axis family only.
- **`edge_type` in results echoes the dot-key** (e.g. `OVERRIDDEN_BY.DECLARES_CLIENT`), not the terminal stored label.
- **`via_id` in `attrs` only** for composed `OVERRIDDEN_BY.*` keys (overrider method id); base `OVERRIDDEN_BY` has no `via_id` (`other` is the overrider).
- **Traversal uses stored `[:OVERRIDES]`** for the dispatch hop; do not duplicate `override_axis_rollup_for` signature Cypher in `neighbors` unless a fixture proves stored-edge gaps.
- **v1 constraints:** override-axis dot-keys require **method** Symbol origin (`kind = 'method'`, not `constructor`, no `static` modifier); **`direction="out"` only** on the virtual axis. Wrong axis, direction, or static/constructor → `success=False` with an axis-specific `message` (not silent empty).
- **Fail-fast on mixed composed families:** if the request lists both `DECLARES.*` and `OVERRIDDEN_BY.*`, reject the **entire** `neighbors` call when the origin cannot satisfy **both** partitions (always fails on a single origin id). Error text must name the failing axis (method vs type), not reuse the DECLARES-only string for override failures.
- **Flat `OVERRIDES` stays** a one-hop `EdgeType`; agents may still use `neighbors(..., ['OVERRIDES'])` (stored direction; virtual `OVERRIDDEN_BY` with `out` is the declaration→implementations mental model).
- **`edge_filter` incompatible** with any composed key (existing `_edgefilter_applicability_error` path; no change to rule).
- **No ontology bump, no re-index** — read-path only over existing `OVERRIDES` and terminal edges.
- **Rollup ↔ traversal parity is merge-blocking** — not optional regression coverage.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | `kuzu_queries.py`, `mcp_v2.py`, `mcp_hints.py`, `server.py`, docs (`AGENT-GUIDE`, `EDGE-NAVIGATION`, README), optional `java_ontology.py` doc strings, tests | **No** | Axis-split composed gates in `neighbors_v2`; parity with `override_axis_rollup_for`; base-key attrs vs composed terminal projection; hint template reversal; `ComposedEdgeType` / registry drift ([#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) partial) | `test_mcp_v2_compose.py`, `test_mcp_hints.py`, `test_mcp_v2.py` (if adapter tests move) | — |

**Landing order:** **PR-1** only (single code PR).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Implementation shape | One PR (`feat(neighbors): OVERRIDDEN_BY.* dot-key traversal`) |
| `ComposedEdgeType` | Extend `Literal` in `mcp_v2.py` with four override-axis keys; import allowlist from `kuzu_queries._OVERRIDE_AXIS_COMPOSED_REL_MAP` (or shared module) so types and traversal cannot drift |
| Cypher home | `KuzuGraph.override_axis_traversal_for(method_id, composed_key)` in `kuzu_queries.py`, parallel to `member_edge_traversal_for` |
| Dispatch hop | `(decl)<-[:OVERRIDES]-(mover)` — aligns with stored-edge tests already proving id parity for base hop |
| Registry | `_OVERRIDE_AXIS_COMPOSED_REL_MAP: tuple[tuple[str, str \| None], ...]` — `None` rel for base `OVERRIDDEN_BY` |
| Merge order | Unchanged from #171: **flat** edges first (per origin, flat label order), then **composed** edges (per origin, request order within axis: member keys then override keys, or preserve single `composed_keys` request order — pick one and keep stable in tests) |
| Terminal attrs | Composed: same column projection as `member_edge_traversal_for` + `via_id`; base `OVERRIDDEN_BY`: minimal attrs like flat `OVERRIDES` on `direction='in'` |
| `NodeFilter` | Terminal nodes for composed keys; overrider methods for base `OVERRIDDEN_BY` |
| Stored vs virtual direction | Document: `neighbors(decl, 'out', ['OVERRIDDEN_BY'])` ≈ `neighbors(decl, 'in', ['OVERRIDES'])` on same declaration method |
| #172 scope | This PR may export override-axis map from `kuzu_queries` and consume in `mcp_v2`; full DECLARES+OVERRIDE consolidation can follow immediately after |

---

# PR-1 — `OVERRIDDEN_BY.*` dot-key traversal in `neighbors`

## File-by-file changes

### 1. `kuzu_queries.py`

- Add module-level registry:

```python
_OVERRIDE_AXIS_COMPOSED_REL_MAP: tuple[tuple[str, str | None], ...] = (
    ("OVERRIDDEN_BY", None),
    ("OVERRIDDEN_BY.DECLARES_CLIENT", "DECLARES_CLIENT"),
    ("OVERRIDDEN_BY.DECLARES_PRODUCER", "DECLARES_PRODUCER"),
    ("OVERRIDDEN_BY.EXPOSES", "EXPOSES"),
)
_OVERRIDE_AXIS_COMPOSED_REL_BY_KEY: dict[str, str | None] = dict(_OVERRIDE_AXIS_COMPOSED_REL_MAP)
```

- Export `OVERRIDE_AXIS_COMPOSED_EDGE_TYPES` (or equivalent frozenset) for `mcp_v2` partition logic.
- Add `override_axis_traversal_for(self, method_id: str, composed_key: str) -> list[dict[str, Any]]`:
  - **Base** (`rel is None`):

```cypher
MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol)
RETURN mover.id AS other_id
```

  - **Composed** (untyped `[e]` + `label(e) = $rel`, same binder pattern as `member_edge_traversal_for`):

```cypher
MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol)-[e]->(term)
WHERE label(e) = $rel
RETURN mover.id AS via_id, label(e) AS stored_edge_type,
       term.id AS other_id,
       e.confidence AS confidence, e.strategy AS strategy,
       e.match AS match, e.mechanism AS mechanism,
       e.annotation AS annotation, e.field_or_param AS field_or_param,
       e.source AS source, e.call_site_line AS call_site_line,
       e.call_site_byte AS call_site_byte, e.arg_count AS arg_count,
       e.resolved AS resolved
```

- Return `[]` for unknown `composed_key` (internal only).
- **Do not** change `override_axis_rollup_for` counting rules.

### 2. `mcp_v2.py`

- Extend `ComposedEdgeType` with four override-axis literals (seven total composed keys).
- Build `_COMPOSED_EDGE_TYPES` from `get_args(ComposedEdgeType)` (unchanged pattern).
- Add `_MEMBER_COMPOSED_EDGE_TYPES` and `_OVERRIDE_COMPOSED_EDGE_TYPES` frozensets (import override keys from `kuzu_queries` registry).
- Update module comment (~L39–41): both `DECLARES.*` (type) and `OVERRIDDEN_BY.*` (method) are valid `neighbors` composed keys.
- Update `NodeRecord.edge_summary` `Field(description=…)`:
  - Remove “do not pass … `OVERRIDDEN_BY.*`” prohibition.
  - State method-origin + `out` requirement for override-axis keys; keep `DECLARES.*` type-origin wording.
- Refactor `neighbors_v2` composed handling:
  1. Partition `composed_keys` into `declares_composed` and `override_composed` by registry prefix.
  2. **Before Cypher per origin:** for each non-empty partition, validate origin:
     - `declares_composed` non-empty → origin must be type Symbol (`_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP`); else `success=False` with message naming **type** axis and first offending key.
     - `override_composed` non-empty → origin must be non-static **method** Symbol (`_METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP` + static/constructor gate matching `override_axis_rollup_for`); else `success=False` with message naming **method** axis.
     - If **both** partitions non-empty on the same origin, both checks run; **any** failure rejects the whole request (fail-fast).
  3. `direction != "out"` when any composed key (either family) → existing message (unchanged).
  4. Composed traversal loops:
     - `declares_composed` → `g.member_edge_traversal_for(...)` (unchanged).
     - `override_composed` → `g.override_axis_traversal_for(...)`; base key builds `Edge` without `via_id`; composed keys use `_neighbor_edge_attrs(row)` (includes `via_id`).
  5. `edge_type` on results = dot-key string for both families.
- Remove adapter rejection of `OVERRIDDEN_BY` / `OVERRIDDEN_BY.*` (they become valid `ComposedEdgeType` values).

### 3. `server.py`

- `neighbors` / `describe` tool `description=` strings: `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.DECLARES_PRODUCER`, `OVERRIDDEN_BY.EXPOSES` are valid `edge_types` for **method** Symbol origins (`out` only on virtual axis); `DECLARES.*` remains type-origin.
- Optional `_INSTRUCTIONS` edge-label sentence: mention both composed families briefly.

### 4. `mcp_hints.py`

- Rewrite override-axis describe templates (remove `then neighbors(overrider_ids` two-hop workaround):

```text
TPL_DESCRIBE_METHOD_OVERRIDERS =
  "overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY'])"
TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS =
  "clients in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.DECLARES_CLIENT'])"
TPL_DESCRIBE_METHOD_PRODUCERS_IN_OVERRIDERS =
  "producers in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.DECLARES_PRODUCER'])"
TPL_DESCRIBE_METHOD_ROUTES_IN_OVERRIDERS =
  "routes in overriders: neighbors(['{id}'],'out',['OVERRIDDEN_BY.EXPOSES'])"
```

- Update `MCP_HINTS_FIELD_DESCRIPTION`: describe-method override-axis hints **may** recommend `OVERRIDDEN_BY.*` dot-keys.
- **Do not** relax `_filter_neighbors_dotkey_hints` on **empty** structural `neighbors` hints (`test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints` still applies there only).

### 5. `docs/AGENT-GUIDE.md`

- Split composed-edge section: `DECLARES.*` (type, `out`) vs `OVERRIDDEN_BY.*` (method, `out`).
- Primary recipe: single-call `neighbors(..., ['OVERRIDDEN_BY.DECLARES_CLIENT'])` on interface/abstract method ids.
- Document stored vs virtual direction equivalence for base key.
- Remove “OVERRIDDEN_BY* describe-only / not valid edge_types” bullets.

### 6. `docs/EDGE-NAVIGATION.md`

- Under override-axis / method-subject rows (if present): add dot-key one-liners alongside stored `OVERRIDES` recipes.

### 7. `README.md`

- MCP `neighbors` row: note four optional override-axis composed `edge_types` for method Symbols; link AGENT-GUIDE.
- No “Re-index required” callout change.

### 8. `java_ontology.py` (optional)

- If `EDGE_SCHEMA` / `type_subject` strings still say `OVERRIDDEN_BY*` is describe-only, update to navigable dot-key wording. **No** `ontology_version` bump.

### 9. `tests/test_mcp_v2_compose.py`

| # | Test name | Assert |
| --- | --- | --- |
| 1 | `test_neighbors_overridden_by_dot_key_returns_overriders` | Base key → method `NodeRef`s; ids match `neighbors(..., 'in', ['OVERRIDES'])` on same origin |
| 2 | `test_neighbors_overridden_by_dot_key_declares_client` | `ChatAssignmentPort.requestAssignment` (bank-chat) → Clients + `via_id`; terminal kind |
| 3 | `test_neighbors_overridden_by_dot_key_declares_producer` | `override_axis_rollup_smoke` `AbstractProducerApi.publish` |
| 4 | `test_neighbors_overridden_by_dot_key_exposes` | smoke `AbstractApi.handle` |
| 5 | `test_neighbors_overridden_by_dot_key_count_matches_edge_summary` | `len(results)` == `describe.edge_summary[key].out` (bank-chat + smoke) |
| 6 | `test_neighbors_overridden_by_dot_key_type_origin_rejected` | type id + override dot-key → `success=False`, method-axis message |
| 7 | `test_neighbors_mixed_composed_families_on_type_rejected` | type id + `["DECLARES.DECLARES_CLIENT", "OVERRIDDEN_BY.DECLARES_CLIENT"]` → whole request fails |
| 8 | `test_neighbors_mixed_composed_families_on_method_rejected` | method id + same mixed list → whole request fails |
| 9 | `test_neighbors_overridden_by_dot_key_static_method_rejected` | static method on smoke or bank-chat → `success=False` |
| 10 | `test_neighbors_overridden_by_dot_key_inbound_rejected` | `direction="in"` + override dot-key |
| 11 | `test_neighbors_accepts_overridden_by_dot_keys` | adapter accepts all four keys (replaces `test_neighbors_still_rejects_overridden_by`) |
| 12 | `test_neighbors_overridden_by_rollup_traversal_parity_blocking` | parametrized over all four keys + fixtures; `len(neighbors) == edge_summary.out` |

- **Remove/rename:** `test_neighbors_still_rejects_overridden_by` → `test_neighbors_accepts_overridden_by_dot_keys`.
- **Update:** `test_describe_interface_method_with_annotated_impl_emits_rollup` — drop `ValidationError` expectation; optionally assert `neighbors` accepts `OVERRIDDEN_BY`.
- Reuse existing helpers: `_dispatch_down_override_method_ids`, `override_axis_graph` fixture, `ChatAssignmentPort.requestAssignment` discovery pattern.

### 10. `tests/test_mcp_hints.py`

- Update `test_hints_describe_method_*_in_overriders` expected strings to new single-call templates.
- **Add** `test_hints_describe_method_overridden_by_declares_client_emits_dot_key` — positive describe hint prescribes `neighbors(['{id}'],'out',['OVERRIDDEN_BY.DECLARES_CLIENT'])`.
- Keep `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints` passing (empty structural neighbors branch only).
- Parametrized template length tests for `TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS` still ≤120 chars after rewrite.

## Tests for PR-1

Implement every name in the table above. Regression:

- All existing `DECLARES.*` dot-key tests from #171 remain green.
- `test_overrides_stored_neighbors_in_matches_override_axis_impl_ids` unchanged (base stored-hop parity).
- Full non-heavy suite: `.venv/bin/python -m pytest tests -v`.

Focused during development:

```bash
.venv/bin/python -m pytest tests/test_mcp_v2_compose.py tests/test_mcp_hints.py -v
```

## Definition of done (PR-1)

- [ ] `neighbors_v2` accepts four `OVERRIDDEN_BY*` dot-keys; `DECLARES.*` and flat `EdgeType` behavior unchanged.
- [ ] Axis-specific origin gates with fail-fast mixed-family rejection and distinct error messages.
- [ ] Composed override results: dot-key `edge_type`, terminal `other`, `via_id` in `attrs` (composed only); terminal attr projection aligned with member composed path.
- [ ] **Merge-blocking** parity tests green on bank-chat `requestAssignment` and `override_axis_rollup_smoke`.
- [ ] `NodeRecord.edge_summary` description and agent docs match behavior.
- [ ] Four describe override-axis hint templates use single-call dot-keys; no `then neighbors(overrider_ids` in those templates.
- [ ] `ruff` + full `pytest tests` (non-heavy) pass.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add override-axis registry + `override_axis_traversal_for` | `kuzu_queries.py` | Manual Cypher on smoke fixture returns rows with `via_id` for composed keys |
| 2 | Extend `ComposedEdgeType`; axis partition + gates | `mcp_v2.py` | Mixed-family and wrong-origin tests 6–9 pass |
| 3 | Wire override traversal loop + base-key `Edge` shape | `mcp_v2.py` | Tests 1–4 pass |
| 4 | Parity tests (merge-blocking) | `tests/test_mcp_v2_compose.py` | Tests 5, 12 green before docs/hints |
| 5 | Widen MCP descriptions | `server.py` | Tool schema/docs mention override dot-keys |
| 6 | Hint templates + field description | `mcp_hints.py` | Hint tests pass; grep finds no `then neighbors(overrider_ids` in `TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS` |
| 7 | Agent docs | `docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, `README.md` | Consistent with #162 symmetry |
| 8 | Remove old rejection test; adapter acceptance | `tests/test_mcp_v2_compose.py` | Test 11 replaces `test_neighbors_still_rejects_overridden_by` |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Count mismatch between `override_axis_rollup_for` and traversal | **High (merge-blocking)** | Stored `[:OVERRIDES]` dispatch hop; `test_neighbors_overridden_by_rollup_traversal_parity_blocking` on bank-chat + smoke |
| 2 | Stored `OVERRIDES` incomplete vs signature walk | High | Existing `test_overrides_stored_neighbors_in_matches_override_axis_impl_ids`; block merge if parity fails — do not re-embed rollup Cypher without fixture proof |
| 3 | Mixed `DECLARES.*` + `OVERRIDDEN_BY.*` on one id confuses agents | Medium | Fail-fast whole-request rejection; tests 7–8 |
| 4 | Wrong error message (type string for override failure) | Medium | Axis-specific messages in implementation checklist; test 6 |
| 5 | Hint HV20 regression | Medium | Only change describe success-path templates; keep `_filter_neighbors_dotkey_hints` on empty neighbors structural hints |
| 6 | `ComposedEdgeType` / registry drift | Low | Import override allowlist from `kuzu_queries`; note #172 follow-up for DECLARES map |
| 7 | Agents confuse `OVERRIDDEN_BY` `out` vs `OVERRIDES` `in` | Low | Document equivalence in AGENT-GUIDE |

# Out of scope

- Changes to `override_axis_rollup_for` counting rules or builder `OVERRIDES` materialization
- Inbound (`direction="in"`) override-axis dot-keys
- Composed keys for override-axis `HTTP_CALLS` / `ASYNC_CALLS` (three-hop)
- Per-method `NodeRef` edge-presence signals ([#167](https://github.com/HumanBean17/java-codebase-rag/issues/167))
- `search` / `find` schema changes
- Full [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) registry consolidation beyond override-axis export (DECLARES map follow-up OK in same PR if zero scope creep)
- Ontology version bump / re-index
- Moving propose to `completed/` (reviewer may do on merge)

# Whole-plan done definition

1. PR-1 merged; propose moved to `propose/completed/` with `Status: landed`.
2. Issue #165 acceptance criteria met: four `OVERRIDDEN_BY*` keys navigable with rollup ↔ traversal parity.
3. `pytest tests` and `ruff check .` pass on `master` without heavy gate.

# Tracking

- `PR-1`: _pending_
