# Plan: MCP Filter Frame — typed query language migration

Status: **completed — shipped via PR-FRAME-1 → PR-FRAME-3** (merged 2026-05).
This plan implemented
[`propose/completed/MCP-FILTER-FRAME-PROPOSE.md`](../../propose/completed/MCP-FILTER-FRAME-PROPOSE.md)
as a 3-PR sequence. Per-PR Cursor prompts:
[`AGENT-PROMPTS-MCP-FILTER-FRAME.md`](AGENT-PROMPTS-MCP-FILTER-FRAME.md).

Depends on: **none** (builds on already-shipped #122 — `extra="forbid"` +
per-kind applicability validation).

## Goal

- Lock the MCP V2 surface as a **typed query language** with strict
  structured predicates and one `search.query` carve-out.
- Resolve the 3 vocabulary-audit items from Appendix A (rename
  `client_method` → `http_method`; document `source_layer` ≠ `role`;
  document `target_service` ≠ `target_microservice`).
- Implement the 7 frame-edge decisions from §3.4 in code + tests.
- Ship lightweight local counters for the revisit-trigger (§3.4.6).
- Refresh all tool descriptions to teach the contract.

## Principles (do not relitigate in review)

- **Frame is strict.** Every input field maps to one stored attribute;
  inapplicable input is loud failure. `search.query` is the only
  permissive surface.
- **No users, no version ceremony.** Breaking renames ship in place.
  No deprecation aliases.
- **EdgeType stays closed.** Dot-keys are read-only output signals
  (PR #89 decision #11). Not revisited.
- **`resolve` is a separate propose.** Named in the frame, not designed
  here.
- **Builds on PR #89 invariants.** Four primitives, closed EdgeType,
  rollup dot-keys read-only, `_coerce_filter` lossless.
- **No ontology bump.** All 3 PRs modify MCP filter vocabulary, tool
  descriptions, and validation — no graph schema or enrichment changes.
  No reindex required.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-FRAME-1 | Vocabulary renames (Appendix A audit) | none | 4–5 | filter rename + cross-kind alignment | prerequisite only |
| PR-FRAME-2 | Lock 7 frame-edge decisions in code | none | 4–5 | wildcard, `describe(fqn=…)`, semantics | PR-FRAME-1 |
| PR-FRAME-3 | Local counters + tool-description refresh | none | 3–4 | counter + description smoke | PR-FRAME-2 |

Landing order: **FRAME-1 → FRAME-2 → FRAME-3**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `client_method` vs `http_method` | Rename `client_method` → `http_method`. Same HTTP-method concept on both sides (route=server, client=caller). `_NODEFILTER_APPLICABLE_FIELDS` lists `http_method` under both `route` and `client`. |
| `source_layer` vs `role` | Keep both. `source_layer` on Client tracks which brownfield layer produced the declaration (`builtin`, `layer_a_meta`, `layer_b_ann`, `layer_c_source`, `layer_b_fqn`). `role` on Symbol tracks architectural stereotype (`CONTROLLER`, `SERVICE`, …). Different concepts, correct to have different names. Document the distinction. |
| `target_service` vs `target_microservice` | Keep `target_service`. More general (allows future non-microservice targets). Document the distinction from `microservice`. |
| Wildcard rejection | `fqn_prefix` (and any future prefix-match field) rejects values containing `*` or `?`. Error message hints `search(query=…)`. |
| `describe(fqn=…)` | Additive parameter, accepted only when the node is a Symbol. Routes and Clients accept `id` only. Multi-microservice FQN collisions return the first match with a hint about `microservice` co-parameter for disambiguation (deferred to `resolve`). |
| Multi-value semantics | Within-field OR, cross-field AND. Already exercised by `symbol_kinds`; no new multi-value fields added in this plan. Locked via test. |
| Negation predicates | `exclude_roles` stays. Appendix A audit did not flag additional `exclude_*` mirrors as needed now. Pattern is available for future use. |
| Empty-filter semantics | `filter={}` / `filter=None` = no predicate, full result set. Pagination is the safety net. Already works; locked via test. |
| Revisit-trigger | N=3 legitimate workflows with no clean analog within 6 months reopens the frame. Tracked by local stderr counter, not product telemetry. |
| Identifier-resolution fallback | `search` + `describe`-per-candidate is the documented pattern until `resolve` ships. Noted in tool descriptions. |

---

# PR-FRAME-1 — Vocabulary renames (Appendix A audit)

## File-by-file changes

### 1. `mcp_v2.py`

- **Rename `NodeFilter.client_method` → `NodeFilter.http_method` (the field is already named `http_method` for route; this makes it cross-kind).**
  Wait — the Route-side field is already called `http_method` and the
  Client-side field is `client_method`. After the rename both are
  `http_method`. But Pydantic forbids two fields with the same name.
  The resolution: remove the old `client_method` field. The single
  `http_method` field now applies to both `route` and `client` kinds.
- Update `_NODEFILTER_APPLICABLE_FIELDS`:
  - `"route"` tuple already has `"http_method"` — keep.
  - `"client"` tuple: replace `"client_method"` with `"http_method"`.
- Update `_node_matches_filter`:
  - `kind == "route"` branch: no change (already uses `f.http_method`).
  - `kind == "client"` branch: change `f.client_method` → `f.http_method`.
    Both branches compare against `row.get("method")`.
- Update `find_v2` `kind == "client"` path:
  - Change `method=nf.client_method` → `method=nf.http_method` in the
    `g.list_clients(...)` call.
- **No changes to `_symbol_where_from_filter`** (symbol-only, unaffected).

### 2. `server.py`

- Tool descriptions don't reference `client_method` by name today.
  Verify via grep; update if needed.

### 3. `docs/AGENT-GUIDE.md`

- Update the per-kind field table:
  - Change `client_kind, target_service, target_path_prefix, client_method`
    to `client_kind, target_service, target_path_prefix, http_method`.
  - Note that `http_method` now applies to both `route` and `client`.
- Document the `source_layer` vs `role` distinction in the NodeFilter
  notes section.
- Document the `target_service` vs `microservice` distinction.

### 4. `README.md`

- If the README references `client_method` in the MCP tool reference or
  NodeFilter notes, update to `http_method`.

### 5. `tests/test_mcp_v2.py`

- Existing tests that don't use `client_method` are unaffected.
- Add / update tests for cross-kind `http_method` filtering.

## Tests for PR-FRAME-1

1. `test_http_method_field_applies_to_route_kind` — `find(kind="route",
   filter={"http_method": "POST"})` returns only POST routes.
2. `test_http_method_field_applies_to_client_kind` — `find(kind="client",
   filter={"http_method": "POST"})` returns only POST clients.
3. `test_http_method_field_inapplicable_to_symbol` — `find(kind="symbol",
   filter={"http_method": "POST"})` returns `success=False` with
   applicability error.
4. `test_nodefilter_rejects_old_client_method_field` — `NodeFilter(
   client_method="POST")` raises Pydantic `ValidationError` (field no
   longer exists; `extra="forbid"`).
5. `test_nodefilter_applicability_table_covers_all_fields` — existing
   test, must still pass (field count unchanged: `client_method` removed,
   `http_method` now covers both route + client).

## Definition of done (PR-FRAME-1)

- `grep -rn 'client_method' mcp_v2.py server.py docs/AGENT-GUIDE.md`
  returns zero.
- `NodeFilter` has 16 fields (was 17: one `http_method` replaces the
  pair of `http_method` + `client_method`; wait — actually the old
  `http_method` was route-only, and `client_method` was client-only,
  both existed as separate fields. After the merge, there is one
  `http_method` covering both. So field count drops from 17 to 16).
- `_NODEFILTER_APPLICABLE_FIELDS` lists `http_method` under both
  `route` and `client`.
- Full test suite passes.
- `docs/AGENT-GUIDE.md` updated with new field table + vocabulary
  distinction notes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Remove `client_method` field from `NodeFilter`; `http_method` stays | `mcp_v2.py` | Pydantic model has 16 fields |
| 2 | Update `_NODEFILTER_APPLICABLE_FIELDS["client"]` | `mcp_v2.py` | `http_method` listed under both `route` and `client` |
| 3 | Update `_node_matches_filter` client branch | `mcp_v2.py` | Uses `f.http_method` not `f.client_method` |
| 4 | Update `find_v2` client path | `mcp_v2.py` | `method=nf.http_method` |
| 5 | Update docs | `docs/AGENT-GUIDE.md`, `README.md` | No `client_method` references remain |
| 6 | Add/update tests | `tests/test_mcp_v2.py` | 5 tests listed above pass |

---

# PR-FRAME-2 — Lock the 7 frame-edge decisions in code

## File-by-file changes

### 1. `mcp_v2.py`

- **Wildcard rejection (§3.4.1).** Add a validation helper
  `_validate_no_wildcards(nf: NodeFilter) -> str | None` that checks
  prefix-match fields (`fqn_prefix`, `path_prefix`, `target_path_prefix`)
  for `*` or `?` characters. Returns an error message hinting at
  `search(query=…)` when found. Call from `find_v2`, `search_v2`, and
  `neighbors_v2` after applicability validation, before querying.
- **`describe(fqn=…)` (§3.4.2).** Extend `describe_v2` to accept an
  optional `fqn: str | None = None` parameter.
  - When `fqn` is provided and `id` is not, look up the symbol by
    exact FQN match: `MATCH (s:Symbol) WHERE s.fqn = $fqn RETURN s.id
    LIMIT 2`. If exactly one result, proceed as normal. If zero, return
    `success=False, message="No Symbol found for fqn=…"`. If >1, return
    the first with a message hint: "multiple symbols share this FQN;
    pass microservice= to disambiguate, or use search()".
  - When both `id` and `fqn` are provided, `id` wins (fqn is ignored).
  - When neither is provided, return `success=False`.
  - Route and Client kinds are `id`-only; `fqn` on a non-Symbol ID
    is silently ignored (the ID already resolved the kind).
- **Multi-value semantics (§3.4.3).** No code change needed — already
  exercised by `symbol_kinds` (within-field OR) and cross-field AND.
  Lock via test only.
- **Negation predicates (§3.4.4).** No new `exclude_*` mirrors. Lock
  via test that `exclude_roles` works correctly on the fixture.
- **Empty-filter semantics (§3.4.5).** Already works. Lock via explicit
  test showing `find(kind="client", filter={})` returns non-empty
  results.
- **Revisit-trigger doc (§3.4.6).** Add a module-level docstring block
  in `mcp_v2.py` documenting the frame contract and the N=3 / 6-month
  revisit trigger, referencing the propose.
- **Identifier-resolution fallback doc (§3.4.7).** No code change in
  `mcp_v2.py`; this is a tool-description update (deferred to server.py
  in this PR or PR-FRAME-3).

### 2. `server.py`

- Add `fqn: str | None = Field(default=None, ...)` parameter to the
  `describe` MCP tool. Pass it through to `describe_v2`.
- Update `describe` tool description to mention `fqn` as an alternative
  to `id` for Symbol nodes.

### 3. `kuzu_queries.py`

- May not need changes if the FQN lookup is done inline in
  `describe_v2` via `g._rows(...)`. Prefer inlining over adding a
  dedicated helper to keep the change minimal.

### 4. `tests/test_mcp_v2.py`

- New tests for the 7 decisions.

## Tests for PR-FRAME-2

1. `test_wildcard_in_fqn_prefix_rejected` — `find(kind="symbol",
   filter={"fqn_prefix": "com.foo.*"})` returns `success=False` with
   message hinting at `search`.
2. `test_wildcard_in_path_prefix_rejected` — `find(kind="route",
   filter={"path_prefix": "/api/*"})` returns `success=False`.
3. `test_wildcard_in_target_path_prefix_rejected` — `find(kind="client",
   filter={"target_path_prefix": "/api/*"})` returns `success=False`.
4. `test_wildcard_question_mark_in_fqn_prefix_rejected` — `fqn_prefix=
   "com.foo.?"` rejected.
5. `test_describe_by_fqn_returns_symbol` — `describe_v2(fqn=<known
   symbol FQN>)` returns `success=True` with the correct record.
6. `test_describe_by_fqn_unknown_returns_error` — `describe_v2(fqn=
   "com.nonexistent.Foo")` returns `success=False`.
7. `test_describe_by_fqn_id_takes_precedence` — when both `id` and
   `fqn` are passed, `id` wins.
8. `test_describe_by_fqn_requires_id_or_fqn` — calling `describe_v2()`
   with neither returns `success=False`.
9. `test_multi_value_symbol_kinds_or_semantics` — `find(kind="symbol",
   filter={"symbol_kinds": ["class", "interface"]})` returns results
   with either kind (OR within field).
10. `test_cross_field_and_semantics` — `find(kind="symbol",
    filter={"microservice": "<known>", "role": "CONTROLLER"})` returns
    only results matching both.
11. `test_exclude_roles_negation_predicate` — `find(kind="symbol",
    filter={"exclude_roles": ["CONTROLLER"]})` returns no CONTROLLER.
12. `test_empty_filter_returns_full_result_set` — `find(kind="client",
    filter={})` returns results.
13. `test_find_symbol_empty_filter_returns_results` — `find(kind=
    "symbol", filter={})` returns non-empty (existing test, verify
    still passes).

## Definition of done (PR-FRAME-2)

- Wildcard values in `fqn_prefix`, `path_prefix`, `target_path_prefix`
  produce `success=False` with a hint message.
- `describe(fqn=<exact FQN>)` works for Symbol nodes, returns error
  for unknown FQNs.
- All 13 tests pass.
- Module docstring documents the frame contract.
- Full test suite passes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `_validate_no_wildcards` helper | `mcp_v2.py` | Helper returns error message for `*` or `?` in prefix fields |
| 2 | Wire wildcard validation into `find_v2`, `search_v2`, `neighbors_v2` | `mcp_v2.py` | Returns `success=False` before querying |
| 3 | Add `fqn` parameter to `describe_v2` | `mcp_v2.py` | FQN lookup → Symbol, with disambiguation hint |
| 4 | Add `fqn` to `describe` MCP tool in `server.py` | `server.py` | Tool schema includes `fqn` |
| 5 | Add frame contract docstring | `mcp_v2.py` | Module-level docstring references propose §1 and revisit trigger |
| 6 | Add tests | `tests/test_mcp_v2.py` | 13 tests listed above pass |

---

# PR-FRAME-3 — Lightweight local counters + tool-description refresh

## File-by-file changes

### 1. `mcp_v2.py`

- Add a lightweight counter module (or inline counter) for fail-loud
  events. Shape: a module-level `_FailLoudCounter` class or a simple
  `dict[str, int]` behind a lock, incremented whenever
  `_nodefilter_applicability_error` or `_validate_no_wildcards` fires.
- Add a `_log_fail_loud(category: str)` helper that increments the
  counter and emits a structured one-line stderr log:
  `[filter-frame] fail-loud category=<cat> count=<N>`.
- Wire into existing fail paths (applicability errors, wildcard
  rejections, unknown filter keys).
- Expose counter state via a non-tool function (e.g.
  `filter_frame_counters() -> dict[str, int]`) for internal
  diagnostics; not a new MCP tool.

### 2. `server.py`

- **Full tool-description refresh.** Update `description=` strings for
  all four tools to teach the contract:
  - `search`: `query` is opaque text (NL or code), ranked. `filter`
    follows strict-frame rules (symbol-only applicability). Wildcards
    in prefix fields rejected.
  - `find`: strict structured lookup. Per-kind applicable fields listed
    inline. Wildcards rejected. Empty filter = all nodes of that kind.
  - `describe`: accepts `id` (any kind) or `fqn` (Symbol only). For
    identifier-shaped lookups without an exact ID/FQN, use
    `search(query=…)` + `describe` per candidate.
  - `neighbors`: required `direction` + `edge_types`. Filter applies to
    neighbor endpoint. Mixed-kind neighborhoods fail on first
    inapplicable row.
- Add the identifier-resolution fallback note (§3.4.7) to `describe`
  and `search` descriptions.

### 3. `docs/AGENT-GUIDE.md`

- Update the NodeFilter field table to reflect the `http_method`
  cross-kind alignment (if not already done in PR-FRAME-1; verify).
- Add a "strict frame contract" section or update the existing filter
  notes to document: no wildcards, no DSL in `search.query`, per-kind
  applicable fields.
- Document the identifier-resolution fallback pattern.

### 4. `tests/test_mcp_v2.py` (or new `tests/test_filter_frame_counters.py`)

- Test that fail-loud events increment the counter.
- Test that counter state is accessible.

## Tests for PR-FRAME-3

1. `test_fail_loud_counter_increments_on_applicability_error` — fire
   a cross-kind filter, check counter > 0.
2. `test_fail_loud_counter_increments_on_wildcard_rejection` — fire
   a wildcard prefix, check counter > 0.
3. `test_fail_loud_counter_categories_are_distinct` — verify
   `applicability` and `wildcard` are separate counter keys.
4. `test_fail_loud_counter_survives_multiple_calls` — counter
   accumulates across calls within one process.

## Definition of done (PR-FRAME-3)

- All four tool descriptions updated in `server.py`.
- `docs/AGENT-GUIDE.md` updated with frame contract and fallback
  pattern.
- Local counter increments on every fail-loud event.
- 4 counter tests pass.
- Full test suite passes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `_FailLoudCounter` or counter dict + `_log_fail_loud` | `mcp_v2.py` | Counter increments on stderr |
| 2 | Wire counter into existing fail paths | `mcp_v2.py` | Every `success=False` from applicability / wildcard / unknown key logs |
| 3 | Expose `filter_frame_counters()` | `mcp_v2.py` | Callable from tests |
| 4 | Refresh tool descriptions | `server.py` | All 4 tools updated |
| 5 | Update agent guide | `docs/AGENT-GUIDE.md` | Frame contract + fallback documented |
| 6 | Add counter tests | `tests/test_mcp_v2.py` | 4 tests pass |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | `client_method` rename in PR-FRAME-1 touches docs and tests widely | low | No users; `grep -rn client_method` sentinel in DoD catches stragglers. |
| 2 | `describe(fqn=…)` multi-microservice FQN collisions | medium | Return first match with disambiguation hint. `resolve` (future) handles properly. |
| 3 | Wildcard rejection may surprise agents that have learned `fqn_prefix="com.x.*"` | medium | Error message hints at `search(query=…)`. Tool descriptions teach the contract. Counter tracks frequency. |
| 4 | Tool-description refresh in PR-FRAME-3 is a large diff in `server.py` | low | Descriptions are strings, not logic. Test suite validates tool behavior, not description text. |
| 5 | Counter adds minimal runtime overhead per fail-loud event | low | Counter is a dict + lock, no I/O beyond one stderr line. No persistence. |
| 6 | `describe(fqn=…)` parameter makes the tool schema 2-parameter where it was 1-parameter | low | `fqn` is optional, defaulting to `None`. Existing callers passing `id` are unaffected. |

# Out of scope

- **`resolve` tool.** Named in the frame, designed in its own propose.
  Not part of this plan.
- **New multi-value filter fields** (e.g. `microservices: list[str]`).
  The within-field OR semantics are locked; adding new list-typed fields
  is a separate decision.
- **Product telemetry / observability stack.** Counter is local
  process state, not shipped metrics.
- **`EdgeType` changes.** Closed set from PR #89. Not revisited.
- **Dot-key traversal hints** (PR #120 family). Separate concern.
- **Ontology or graph schema changes.** Nothing in this plan touches
  Kuzu DDL, enrichment, or Lance schema.
- **`kuzu_queries.py` refactoring.** `list_clients` parameter stays
  `method=` (it's an internal API, not user-facing vocabulary).

# Whole-plan done definition

1. `NodeFilter` has 16 fields. `client_method` does not exist.
   `http_method` applies to both `route` and `client`.
2. Wildcard values in prefix-match fields produce `success=False` with
   a hint.
3. `describe(fqn=<exact FQN>)` works for Symbols.
4. Tool descriptions in `server.py` teach the strict-frame contract.
5. `docs/AGENT-GUIDE.md` documents per-kind applicable fields, the
   frame contract, and the identifier-resolution fallback.
6. Local counter tracks fail-loud events on stderr.
7. All new tests pass. Full suite green.
8. `propose/MCP-FILTER-FRAME-PROPOSE.md` moved to
   `propose/completed/` after PR-FRAME-3 merges.

# Tracking

- `PR-FRAME-1`: merged
- `PR-FRAME-2`: merged
- `PR-FRAME-3`: merged (#133)
