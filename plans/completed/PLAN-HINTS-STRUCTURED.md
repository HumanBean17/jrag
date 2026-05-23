# Plan: HINTS-STRUCTURED (machine-parseable next-action objects)

Status: **completed**. This plan implemented
[`propose/HINTS-STRUCTURED-PROPOSE.md`](../propose/HINTS-STRUCTURED-PROPOSE.md)
(issue [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195) item 7).

Depends on: v1–v4 hint catalogs (all landed). No open PR dependencies.

## Goal

- Add a `StructuredHint` model and `hints_structured: list[StructuredHint]` field on all five MCP output models, providing agents with machine-parseable next-action objects alongside the existing `hints: list[str]` road signs.
- Refactor `generate_hints` to return `(list[str], list[StructuredHint])` without changing external API semantics — string hints remain backward-compatible.
- Map every existing template trigger to a structured equivalent; batch-placeholder and prose-only hints use `actionable=False`.
- All existing string-hint tests continue passing unchanged.

## Principles (do not relitigate in review)

- **Backward compatible** — `hints: list[str]` is unchanged; `hints_structured` is additive. Clients that ignore it continue working identically.
- **Same trigger logic** — both fields populated from the same triggers; same cap (5), same dedup, same priority ordering.
- **JSON-serializable args** — `StructuredHint.args` values must be JSON primitives (`str`, `int`, `float`, `bool`, `None`, `list`, `dict`). No `set`, `tuple`, or custom objects. Use `list` for arrays.
- **Single generate_hints function** — returns `(list[str], list[StructuredHint])`. Maintaining two parallel generators is fragile given the trigger complexity. The return-type change is internal to `mcp_hints.py` + `mcp_v2.py`; no external API break.
- **No graph I/O** — structured hints are pure functions of MCP payloads, same discipline as string hints.
- **No ontology bump, no re-index** — MCP response shape only.
- **Parity invariant** — for every output where `hints != []`, `len(hints_structured) <= len(hints)` (structured may omit entries with no meaningful tool reference, but never exceeds string count).
- **`actionable` semantics** — `True` = direct tool call with complete args; `False` = advisory/partial (agent fills missing values or uses as guidance).
- **No `hints_version` field** — out of scope.
- **String templates stay independent** — DRY derivation from structured hints risks losing concise human-readable format; revisit only if drift becomes a problem.
- **No `args` validation against MCP tool schemas** — args are advisory; strict validation couples hints to tool signature changes.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | `StructuredHint` model + `hints_structured` field on all 5 outputs + `generate_hints` return-type refactor + all 5 call sites in `mcp_v2.py` | **No** | Return-type change across `mcp_hints.py` + `mcp_v2.py` (5 call sites); `resolve_v2` uses `model_copy(update=…)` pattern; Pydantic serialization of `dict[str, Any]`; parity invariant | `test_structured_hint_*` (describe, find, resolve, neighbors, search); parity; cap; dedup; round-trip | — |

**Landing order:** PR-1 only (single PR recommended per propose).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Single vs dual generate function | Single `generate_hints` returning `(list[str], list[StructuredHint])` — keeps trigger logic unified |
| Batch-placeholder ids | `args.ids` populated from payload result ids when available (`actionable=True`); empty `[]` with `actionable=False` when ids cannot be extracted (meta-hints) |
| Prose-only hints | Include with `actionable=False`; agents that only process actionable hints filter; maintains parity with string hints |
| `actionable` flavors | Two flavors acknowledged: (1) incomplete args (batch-placeholder N2–N7), (2) advisory recommendation (weak-score, high-fanout). Both set `actionable=False`. |
| `hints_structured` field description | Own field description, referencing `hints` for human-readable form |
| String template independence | Kept independent in v1; no DRY derivation |
| `args` validation | No validation against MCP tool schemas in v1 |
| `resolve_v2` call site | Before: `model_copy(update={"hints": generate_hints(...)})`. After: `str_hints, struct_hints = generate_hints(...)` then `model_copy(update={"hints": str_hints, "hints_structured": [StructuredHint(...) for h in struct_hints]})` |
| Structured dedup key | `json.dumps(args, sort_keys=True)` — not `frozenset(args.items())` which breaks on nested dicts (`{"filter": {"path_prefix": …}}`) |
| Proposal lock timing | Proposal locks when this **plan PR** merges (plan-approved = propose locked). Implementation PR must not change propose scope without reopening. |

---

# PR-1 — StructuredHint model, field, generation refactor, tests

## File-by-file changes

### 1. `mcp_v2.py`

- Add `StructuredHint` Pydantic model near output model definitions (after `EdgeFilter`):
  ```python
  class StructuredHint(BaseModel):
      tool: Literal["search", "find", "describe", "neighbors", "resolve"]
      args: dict[str, Any]
      actionable: bool = True
  ```
- Add `hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)` to all five output models:
  - `SearchOutput` (line ~455)
  - `FindOutput` (line ~470)
  - `DescribeOutput` (line ~477)
  - `NeighborsOutput` (line ~488)
  - `ResolveOutput` (line ~558)
- Import `MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION` from `mcp_hints`.
- Update all 5 call sites to destructure the new return type:
  - `search_v2` (line ~928): `str_hints, struct_hints = generate_hints("search", hint_payload)`
  - `find_v2` (line ~1017): `str_hints, struct_hints = generate_hints("find", hint_payload)`
  - `describe_v2` (line ~1094): `str_hints, struct_hints = generate_hints("describe", {"success": True, "record": record.model_dump()})`
  - `resolve_v2` (line ~1418): Before: `out = out.model_copy(update={"hints": generate_hints("resolve", hint_payload)})`. After: `str_hints, struct_hints = generate_hints("resolve", hint_payload); out = out.model_copy(update={"hints": str_hints, "hints_structured": [StructuredHint(tool=h.tool, args=h.args, actionable=h.actionable) for h in struct_hints]})`.
  - `neighbors_v2` (line ~1952): `str_hints, struct_hints = generate_hints("neighbors", neigh_payload)`
- All error-path returns that pass `hints=[]` must also pass `hints_structured=[]` (no change needed — `default_factory=list` covers new field, but explicit `hints_structured=[]` is clearer for readability on error returns).

### 2. `mcp_hints.py`

- Add `MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION` constant (own description, referencing `hints`).
- Add `StructuredHint` import/re-export or define a lightweight version for internal use (Pydantic model lives in `mcp_v2.py`; `mcp_hints.py` should not import from `mcp_v2` to avoid circular deps — use a plain dataclass or typed dict internally, convert at call site). **Preferred:** define `StructuredHint` as a plain `NamedTuple` or simple class in `mcp_hints.py` for internal use; `mcp_v2.py`'s Pydantic `StructuredHint` model converts from it.
- Change `generate_hints` return type from `list[str]` to `tuple[list[str], list[StructuredHint]]`.
- Add structured-hint generation alongside each string hint emission:
  - **Describe type rollup:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]})` (and routes, producers variants).
  - **Describe method override axis:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDDEN_BY"]})` (and client/producer/routes in overriders variants).
  - **Describe method leaf follow-ups:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "out", "edge_types": ["DECLARES_CLIENT"]})` (and producer, EXPOSES variants).
  - **Describe route/client/producer:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "in", "edge_types": ["EXPOSES"]})` (and DECLARES_CLIENT, DECLARES_PRODUCER variants).
  - **Describe many CALLS:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]}, actionable=False)`.
  - **Find empty → resolve:** `StructuredHint(tool="resolve", args={"identifier": identifier, "hint_kind": kind})`.
  - **Find page full:** `StructuredHint(tool="find", args={"kind": kind, "filter": {}, "limit": limit}, actionable=False)`.
  - **Find success F1/F2/F3:** `StructuredHint(tool="neighbors", args={"ids": [node_id], "direction": "in"|"out", "edge_types": [...]})`.
  - **Resolve none → search:** `StructuredHint(tool="search", args={"query": identifier})`.
  - **Resolve none → find route:** `StructuredHint(tool="find", args={"kind": "route", "filter": {"path_prefix": seed}})`.
  - **Resolve none → find client:** `StructuredHint(tool="find", args={"kind": "client", "filter": {"target_service": seed}})`.
  - **Resolve many tighten:** `StructuredHint(tool="resolve", args={"identifier": "", "hint_kind": ""}, actionable=False)`.
  - **Neighbors empty structural (v3):** Build structured args directly from `EDGE_SCHEMA` data (not string parsing). Examples:
    - Row 1 wrong subject kind: `StructuredHint(tool="neighbors", args={"ids": [subject_id], "direction": correct_dir, "edge_types": [canonical_edge]}, actionable=True)` — `typical_traversals[role_key]` provides the canonical edge and direction from `EDGE_SCHEMA`.
    - Row 2 wrong direction: `StructuredHint(tool="neighbors", args={"ids": [subject_id], "direction": correct_dir, "edge_types": [edge]}, actionable=True)`.
    - Row 3 type-level requery: `StructuredHint(tool="neighbors", args={"ids": [subject_id], "direction": direction, "edge_types": [dot_key_edge]}, actionable=True)` — dot-key from `EDGE_SCHEMA` type_subject traversal.
  - **Neighbors success N1a/N1b:** `StructuredHint(tool="neighbors", args={"ids": [origin_id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]})` (and routes).
  - **Neighbors success N2/N3:** `StructuredHint(tool="neighbors", args={"ids": result_ids, "direction": "out", "edge_types": ["HTTP_CALLS"|"ASYNC_CALLS"]}, actionable=populated)` — populate `args.ids` from results when available.
  - **Neighbors success N4/N5/N6/N7:** Similar structured mapping with appropriate args and `actionable` flag.
  - **Neighbors fuzzy strategy:** `StructuredHint(tool="neighbors", args={}, actionable=False)`.
  - **Neighbors CALLS fanout/meta:** `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "out", "edge_types": ["CALLS"], "edge_filter": {}}, actionable=False)`.
  - **Search weak score:** `StructuredHint(tool="find", args={"kind": "symbol", "filter": {"role": "SERVICE"}}, actionable=False)`.
- Add `finalize_structured_hints` mirroring `finalize_hint_list` — dedupe by `(tool, json.dumps(args, sort_keys=True))` key (handles nested dicts like `{"filter": {"path_prefix": "…"}}`), keep highest priority, cap to 5.
- Internal `StructuredHint` representation: use a lightweight dataclass or `NamedTuple` with `tool: str`, `args: dict[str, Any]`, `actionable: bool`, `priority: int`.

### 3. `tests/test_mcp_hints.py`

- Add all named tests from propose § Tests / Validation table (see Tests section below).
- Add helper to assert structured hint shape: `_assert_structured_hint(hint, *, tool, args_subset=None, actionable=True)`.
- Add parity tests asserting `len(hints_structured) <= len(hints)` on payloads that generate non-empty hints.
- Regression: verify all existing string-hint tests pass unchanged.

### 4. `README.md`

- Add mention of `hints_structured` in the MCP tool reference section, under the existing `hints` paragraph (one or two sentences describing the new field and its relationship to `hints`).

## Tests for PR-1

1. `test_structured_hint_describe_type_rollup_clients`
2. `test_structured_hint_describe_type_rollup_routes`
3. `test_structured_hint_describe_type_rollup_producers`
4. `test_structured_hint_describe_method_overriders`
5. `test_structured_hint_describe_method_clients_in_overriders`
6. `test_structured_hint_describe_method_producers_in_overriders`
7. `test_structured_hint_describe_method_routes_in_overriders`
8. `test_structured_hint_describe_method_outbound_client`
9. `test_structured_hint_describe_method_outbound_producer`
10. `test_structured_hint_describe_method_inbound_route`
11. `test_structured_hint_describe_route_declaring`
12. `test_structured_hint_describe_client_declaring`
13. `test_structured_hint_describe_producer_declaring`
14. `test_structured_hint_find_route_handler`
15. `test_structured_hint_find_client_http_targets`
16. `test_structured_hint_find_producer_async_targets`
17. `test_structured_hint_find_empty_resolve`
18. `test_structured_hint_resolve_none_search`
19. `test_structured_hint_resolve_none_find_route`
20. `test_structured_hint_resolve_none_find_client`
21. `test_structured_hint_resolve_many_tighten`
22. `test_structured_hint_neighbors_empty_wrong_kind`
23. `test_structured_hint_neighbors_success_declares_dot_key_clients`
24. `test_structured_hint_neighbors_success_declares_dot_key_routes`
25. `test_structured_hint_neighbors_success_http_targets`
26. `test_structured_hint_neighbors_success_async_targets`
27. `test_structured_hint_neighbors_success_callers`
28. `test_structured_hint_neighbors_success_declaring_client`
29. `test_structured_hint_neighbors_success_declaring_producer`
30. `test_structured_hint_neighbors_success_handler`
31. `test_structured_hint_prose_only_not_actionable` (weak-score, high-fanout)
32. `test_structured_hints_cap_5`
33. `test_structured_hints_dedup`
34. `test_structured_hints_parity_with_string_hints`
35. `test_structured_hint_round_trip` (integration with `neighbors_v2`)
36. `test_structured_hint_describe_many_calls_not_actionable`

## Definition of done (PR-1)

- [ ] `StructuredHint` model defined in `mcp_v2.py`; `hints_structured` field on all 5 output models.
- [ ] `generate_hints` returns `(list[str], list[StructuredHint])` — all 5 call sites destructured.
- [ ] Every existing string-hint trigger has a structured equivalent.
- [ ] All named tests pass; parity invariant holds.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [ ] No `ONTOLOGY_VERSION` change; no re-index required.
- [ ] README mentions `hints_structured` under MCP tool reference `hints` paragraph.
- [ ] Propose status updated to **locked** (happens when this plan PR merges — plan approval = propose lock). Implementation PR must not change propose scope without reopening.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Define internal `StructuredHint` representation in `mcp_hints.py` | `mcp_hints.py` | Importable, JSON-serializable |
| 2 | Define Pydantic `StructuredHint` model + `hints_structured` field on all 5 outputs | `mcp_v2.py` | `SearchOutput.model_fields["hints_structured"]` exists for all 5 |
| 3 | Add `MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION` constant | `mcp_hints.py` | Imported by `mcp_v2.py` |
| 4 | Refactor `generate_hints` return type to `tuple[list[str], list[_Hint]]` | `mcp_hints.py` | All internal branches return both |
| 5 | Map describe triggers to structured hints | `mcp_hints.py` | Describe tests pass |
| 6 | Map find + resolve + search triggers to structured hints | `mcp_hints.py` | Find/resolve/search tests pass |
| 7 | Map neighbors triggers (empty + success + meta) to structured hints | `mcp_hints.py` | Neighbors tests pass |
| 8 | Add `finalize_structured_hints` (dedup + cap) | `mcp_hints.py` | Cap/dedup tests pass |
| 9 | Update all 5 call sites in `mcp_v2.py` to destructure tuple | `mcp_v2.py` | All handlers return both fields |
| 10 | Add error-path `hints_structured=[]` on all failure returns | `mcp_v2.py` | Failure paths serialize correctly |
| 11 | Add all named tests | `tests/test_mcp_hints.py` | All pass |
| 12 | Add parity invariant test | `tests/test_mcp_hints.py` | Pass |
| 13 | Add round-trip integration test | `tests/test_mcp_hints.py` | Pass |
| 14 | Update README `hints` paragraph | `README.md` | Mentions `hints_structured` |
| 15 | Update propose status to locked | `propose/HINTS-STRUCTURED-PROPOSE.md` | Status line changed |
| 16 | Full suite green | all | `ruff check .` + `pytest tests -v` pass |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Circular import between `mcp_hints.py` and `mcp_v2.py` | High | Define lightweight internal `_StructuredHint` in `mcp_hints.py`; Pydantic model in `mcp_v2.py` converts from it. `mcp_hints.py` does **not** import from `mcp_v2.py`. |
| 2 | `resolve_v2` call site uses `model_copy(update=…)` pattern | Medium | Update to include both `hints` and `hints_structured` in the update dict. |
| 3 | Error-path returns missing `hints_structured=[]` | Medium | `default_factory=list` covers it, but audit all `hints=[]` returns for explicit parity. |
| 4 | Structured args contain non-JSON-serializable values | Medium | Validate in `_StructuredHint` constructor; use `list` not `tuple`; no `set`. |
| 5 | Parity invariant violation (structured count > string count) | Low | `test_structured_hints_parity_with_string_hints` enforced. |
| 6 | Neighbors v3 empty structural hints — structured args ambiguity | Medium | Build args from `EDGE_SCHEMA` data directly (not string parsing); concrete examples added in file-by-file changes. |
| 7 | Batch-placeholder N2–N7 `args.ids` population inconsistent | Low | Document when `actionable=True` (ids populated from results) vs `actionable=False` (ids empty placeholder). |

# Out of scope

- Removing or deprecating `hints: list[str]` (kept indefinitely).
- Changing `neighbors_v2` argument parsing or `_coerce_ids()`.
- Reindexing or ontology changes.
- Per-row structured hints (output-level only).
- `hints_version` field.
- Conditioning on `attrs.match` / confidence values.
- FastMCP `ast.literal_eval` fallback.
- `MCP_HINTS_FIELD_DESCRIPTION` changes (frozen).
- Deriving string templates from structured hints (DRY reversal).
- `args` validation against MCP tool parameter schemas.
- `OVERRIDDEN_BY.*` dot-keys in neighbors hint emissions (describe-only discipline unchanged).

# Whole-plan done definition

1. All five MCP tools return `hints_structured: list[StructuredHint]` alongside `hints: list[str]`.
2. Every string-hint trigger has a structured equivalent; parity invariant holds.
3. `actionable=True` hints have complete, JSON-serializable args that map 1:1 to MCP tool calls.
4. `actionable=False` hints carry partial args for advisory/payload-dependent cases.
5. All named tests pass; full test suite green; no ontology bump or re-index required.
6. README documents the new field.

# Tracking

- `PR-1`: _pending_
