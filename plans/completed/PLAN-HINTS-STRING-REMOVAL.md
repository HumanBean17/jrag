# Plan: HINTS-STRING-REMOVAL

Status: **active**. This plan implements
[`propose/active/HINTS-STRING-REMOVAL-PROPOSE.md`](../../propose/active/HINTS-STRING-REMOVAL-PROPOSE.md)
as a single PR.

Depends on: none (all prerequisite work — `hints_structured` with `label` — is landed).

## Goal

- Remove the redundant `hints: list[str]` field from all five MCP tool output models.
- Add `reason: str` to `StructuredHint` — explains why a tool call hint was emitted.
- Add `advisories: list[str]` to all five output models — carries pure informational text with no tool call.
- Eliminate dual-emission maintenance burden and the parity test.
- Preserve all currently emitted advisory information — string-only hints move to `advisories`, tool-call hints gain `reason`.

## Principles (do not relitigate in review)

- **Three-field output model**: `hints_structured` (tool call suggestions only), `advisories` (pure informational text), no `hints`.
- **`hints_structured` is for tool calls only**: every entry has meaningful `tool` + `args`. `actionable=True` = call directly; `actionable=False` = partial, caller adjusts. No entries with empty `args` and no tool call behind them.
- **`advisories` is for pure text**: no tool invocation. Fuzzy strategy warnings, brownfield absence notes, role collision explanations. These are contextual education, not next actions.
- **`reason` explains why**: on a structured hint, `reason` says why the tool call is suggested (e.g. "results look weak"). Not a dumping ground for advisory text (that goes to `advisories`).
- **Breaking change is allowed**: AGENTS.md and the proposal explicitly state breaking changes are always allowed; no deprecation cycle.
- **No re-index, no ontology bump**: this is an output-only change.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Remove `hints` field, add `reason` to `StructuredHint`, add `advisories: list[str]`, reclassify pure-advisory structured hints as advisory strings, remove string templates and parity test, update docs | none | `generate_hints` return type stays a tuple but meaning changes (structured hints + advisories); reclassifying fuzzy strategy / role collision / brownfield absence from `hints_structured` to `advisories` removes them from the structured list; test assertions split between `out.hints_structured[i].reason` and `out.advisories`; `server.py` tool descriptions referencing `hints` | hint unit tests, parity removal, round-trip integration, reason-content tests, advisory-content tests, docs consistency | n/a |

Landing order: **PR-1 only** (single PR).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `reason` default | `reason: str = ""` — avoids forcing a reason on every hint; non-actionable hints are expected to carry one (test-enforced). |
| String template deletion | Delete entirely; git history preserves them. |
| `generate_hints` return type | `tuple[list[_StructuredHint], list[str]]` — first element is structured hints, second is advisories. **Note:** tuple order swaps from the current `tuple[list[str], list[_StructuredHint]]` (string first) to structured first. All callers must unpack accordingly. |
| `_to_structured_hints` conversion | Must forward `reason` from internal `_StructuredHint` to public `StructuredHint`. |
| `finalize_hint_list` | Delete — no longer needed. |
| Pure advisory text location | `advisories: list[str]` — separate field. Not forced into `hints_structured` with empty `args`. |
| `hints_structured` scope | Tool call suggestions only. No entries with empty `args` and no tool call behind them. |
| Reclassified hints | Fuzzy strategy, brownfield absence, role-filter fallback, role collision: currently pseudo-structured hints with empty `args` → move to `advisories`. |
| Unresolved sites | Stays in `hints_structured` — concrete `include_unresolved=True` call. |
| High fanout (neighbors) | Stays in `hints_structured` — concrete CALLS traversal with `edge_filter={}`. |
| High fanout (describe) | "many CALLS — consider filtering" → `advisories` (vague suggestion, not a concrete call). The existing structured hint (CALLS traversal) stays but its reason changes to something concrete or the structured hint is removed and replaced by the advisory. |

---

# PR-1 — Remove string hints, add reason + advisories

## File-by-file changes

### 1. `mcp_hints.py`

- Add `reason: str = ""` field to `_StructuredHint` NamedTuple.
- Remove `MCP_HINTS_FIELD_DESCRIPTION` constant.
- Remove `finalize_hint_list` function.
- Remove all `TPL_*` string template constants.
- Change `generate_hints` signature: return `tuple[list[_StructuredHint], list[str]]` (was `tuple[list[str], list[_StructuredHint]]`). First element is structured hints, second is advisory strings.
- Throughout `generate_hints`, replace `pairs: list[tuple[int, str]]` with `advisories: list[str]` for pure advisory text, and keep `struct_pairs` for structured hints only.
- For each hint that previously appended to `pairs`, classify:
  - **Tool call hint** (has meaningful `tool` + `args`) → keep in `struct_pairs`, add `reason=` from the former template text.
  - **Pure advisory** (no tool call, or empty `args` with no concrete invocation) → move to `advisories` as a plain string derived from the former template.
- Remove `_filter_neighbors_dotkey_hints` function (string-hint-specific).
- Remove `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS` cap constants.
- Remove `_append_find_success_hint` and `_append_neighbors_success_hint` helpers.
- Remove `find_success_hints` function (string-only); keep the structured-hint emission logic inline in `generate_hints`.
- Remove `neighbors_success_hints` function (string-only); keep `_neighbors_success_structured_hints`.
- Remove `neighbors_empty_hints` function (string-only); keep `_neighbors_empty_structured_hints`.
- Remove `neighbors_calls_fanout_hints` and `neighbors_calls_meta_hints` (string-only); integrate their logic inline (see classification below).
- Remove `_FIRST_NEIGHBORS_CALL_RE`, `_parse_first_traversal` — no longer needed.
- Keep all `LABEL_*` constants — still used for `_StructuredHint.label`.

**Classification of current hints:**

Hints that stay in `hints_structured` (tool call suggestions with `reason`):

| Current hint | After change |
| --- | --- |
| All describe type/method rollups | Structured hint + `reason` from former template |
| All resolve none/many hints | Structured hint + `reason` from former template |
| Search weak results | Structured hint (`find(role="SERVICE")`) + `reason="results look weak — narrow the query"` |
| Find empty resolve | Structured hint + `reason` |
| Find page full | Structured hint + `reason="result page full at N — narrow filter or paginate"` |
| Find success follow-ups | Structured hint + `reason` |
| Neighbors empty structural (rows 1-3) | Structured hint + `reason` from former template |
| Neighbors success follow-ups (N1a-N7) | Structured hint + `reason` |
| High fanout (neighbors) | Structured hint (`CALLS traversal + edge_filter={}`) + `reason="N CALLS — noisy axes are …"` |
| Unresolved sites (neighbors) | Structured hint (`include_unresolved=True`) + `reason="N CALLS shown; K unresolved sites"` |
| Describe "many CALLS" | Remove structured hint (vague suggestion). Move to `advisories`. |

Hints that move to `advisories` (pure informational text):

| Current hint | Advisory text |
| --- | --- |
| Fuzzy strategy | "some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row" |
| Brownfield absence (Row 4) | "edges on '{edge}' are emitted by the brownfield resolver — absence may mean unresolved" |
| Role-filter OTHER fallback | "0 CALLS matched callee_declaring_role filter but method has many callees — callee targets may be OTHER (interface/JDK); check target roles and adjust edge_filter" |
| NodeFilter.role collision | "NodeFilter.role filters the neighbor method's role (usually OTHER), not the callee's declaring type — use edge_filter={callee_declaring_role: 'SERVICE'} for CALLS stereotype projection" |
| Describe "many CALLS" | "many CALLS — consider filtering by target microservice" |

### 2. `mcp_v2.py`

- Add `reason: str = ""` field to `StructuredHint` model.
- Add `advisories: list[str]` field to `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`, `ResolveOutput`.
- Remove `hints: list[str]` field from all five output models.
- Remove `MCP_HINTS_FIELD_DESCRIPTION` import from `mcp_hints`.
- Update `_to_structured_hints` to forward `reason`.
- Update all five tool functions (`search_v2`, `find_v2`, `describe_v2`, `neighbors_v2`, `resolve_v2`):
  - Change `str_hints, raw_struct = generate_hints(...)` to `raw_struct, raw_advisories = generate_hints(...)`.
  - Remove `hints=str_hints` from all `*Output(...)` constructor calls.
  - Add `advisories=raw_advisories` to all `*Output(...)` constructor calls (success paths only).
  - Remove `hints=[]` from all error-path `*Output(...)` constructor calls.
- Update `_resolve_finalize_success` to remove `hints=str_hints` from `model_copy(update=...)`; add `advisories=raw_advisories`.

### 3. `server.py`

- Update MCP tool `_INSTRUCTIONS` / description strings that reference `"hints"`:
  - Line 344: change `hints` to `hints_structured` (tool call suggestions) and `advisories` (informational notes).
  - Line 393: same.
  - Line 429: same.
  - Lines 463-464: same.
  - Line 549: same.

### 4. `tests/test_mcp_hints.py`

- Remove `test_structured_hints_parity_with_string_hints`.
- Remove `_hints` helper function (returns string hints only).
- Remove `_structural_neighbors_hints` helper (filters string hints).
- Migrate all tests that assert on `out.hints` (string form):
  - Tool call hints → assert on `out.hints_structured[*].reason`.
  - Pure advisory content → assert on `out.advisories`.
- Update `test_hints_clean_outputs_empty` to assert on `hints_structured` and `advisories`.
- Remove string-template length tests (`test_hints_all_v4_templates_under_120_chars`, `test_hints_template_rendered_length_leq_120`) — replace with tests on `reason` and advisory char caps.
- Remove `test_hints_dedupe_collapses_identical_rendered_strings`, `test_hints_cap_drops_lowest_priority_over_five`, `test_hints_cap_same_priority_keeps_emission_order` — these tested `finalize_hint_list`.

### 5. `docs/AGENT-GUIDE.md`

- Update line 19 to remove reference to `hints` list.
- Document `hints_structured` with `reason` field.
- Document `advisories` field.

### 6. `README.md`

- No changes needed.

## Tests for PR-1

1. `test_structured_hints_cap_5` — existing, unchanged.
2. `test_structured_hints_dedup` — existing, unchanged.
3. `test_structured_hint_round_trip` — existing, updated to remove `out.hints` assertions.
4. `test_structured_hint_label_values` — existing, unchanged.
5. All `test_hints_*` tests — migrated from `out.hints` to `out.hints_structured[*].reason` or `out.advisories`.
6. `test_structured_hints_reason_content` — **new test**: verify `reason` carries expected text for key scenarios.
7. `test_structured_hints_reason_char_cap` — **new test**: all `reason` strings ≤ 120 chars.
8. `test_no_string_hints_field` — **new test**: verify no output model has `hints` field.
9. `test_advisories_content` — **new test**: verify advisory strings appear for fuzzy strategy, brownfield absence, role-filter fallback, role collision, describe many-CALLS.
10. `test_advisories_absent_when_no_pure_info` — **new test**: verify `advisories == []` for scenarios with only tool-call hints (e.g. describe type rollup, resolve one).
11. `test_structured_hints_no_empty_args` — **new test**: verify no structured hint has empty `args` unless it carries a concrete tool call (i.e. fuzzy strategy, role collision, etc. are NOT in `hints_structured`).
12. `test_advisories_char_cap` — **new test**: all advisory strings ≤ 200 chars.

## Definition of done (PR-1)

- `hints: list[str]` field absent from all five output models.
- `advisories: list[str]` field present on all five output models.
- `reason: str` field present on `StructuredHint` and `_StructuredHint`.
- `generate_hints` returns `tuple[list[_StructuredHint], list[str]]`.
- `MCP_HINTS_FIELD_DESCRIPTION`, all `TPL_*` constants, `finalize_hint_list` removed from `mcp_hints.py`.
- No structured hint has empty `args` without a concrete tool call behind it (fuzzy strategy, role collision → `advisories`).
- `server.py` tool descriptions reference `hints_structured` and `advisories`.
- Full test suite passes: `.venv/bin/python -m pytest tests -v`.
- Ruff clean: `.venv/bin/ruff check .`.
- No references to `hints: list[str]` in AGENT-GUIDE or server descriptions.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `reason` to `_StructuredHint`, change `generate_hints` to return `tuple[list[_StructuredHint], list[str]]` | `mcp_hints.py` | Return type changed; all callers updated |
| 2 | Remove `MCP_HINTS_FIELD_DESCRIPTION`, all `TPL_*`, `finalize_hint_list`, string-only helpers | `mcp_hints.py` | No string template constants or string-only functions remain |
| 3 | Classify hints: tool calls → `struct_pairs` with `reason`; pure advisory → `advisories` list | `mcp_hints.py` | Fuzzy strategy, brownfield absence, role-filter fallback, role collision, describe many-CALLS emit to `advisories` |
| 4 | Add `reason` to public `StructuredHint`, add `advisories: list[str]`, remove `hints` from all 5 output models | `mcp_v2.py` | All `*Output` models have `advisories`, lack `hints`; `_to_structured_hints` forwards `reason` |
| 5 | Update all 5 tool functions to unpack `(raw_struct, raw_advisories)` | `mcp_v2.py` | No `str_hints` variable; `advisories=raw_advisories` in constructors |
| 6 | Update `server.py` tool descriptions | `server.py` | All descriptions say `hints_structured` and `advisories` |
| 7 | Migrate test assertions from `out.hints` to `out.hints_structured[*].reason` or `out.advisories` | `tests/test_mcp_hints.py` | All tests pass |
| 8 | Remove parity test, string-only test helpers | `tests/test_mcp_hints.py` | Parity test removed; `_hints` helper removed |
| 9 | Add new tests (reason content, reason char cap, no hints field, advisory content, advisory char cap, no empty args in structured) | `tests/test_mcp_hints.py` | All new tests pass |
| 10 | Update `docs/AGENT-GUIDE.md` | `docs/AGENT-GUIDE.md` | `hints` list reference removed; `hints_structured` with `reason` and `advisories` documented |
| 11 | Run full validation | all | `ruff check .` clean; `pytest tests -v` passes |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Downstream consumers expecting `hints` field | low | Breaking changes are always allowed per AGENTS.md; `hints_structured` has been present since #209 |
| 2 | Reason text divergence from former templates | medium | Derive reason strings from template renderings at authoring time; verify via `reason` content tests |
| 3 | Advisory text lost during migration | low | New `test_advisories_content` test guarantees advisory strings survive |
| 4 | Misclassification: tool call moved to advisories or vice versa | medium | New `test_structured_hints_no_empty_args` catches structured hints with empty args that should be advisories |

# Out of scope

- Changing hint trigger logic or priority tiers.
- Adding new hint categories or triggers.
- Modifying the `_StructuredHint.priority` field semantics.
- Changing the `label` field behavior.
- Adding `reason` to `EDGE_SCHEMA` or any graph-layer changes.
- Index rebuild or ontology bump.

# Whole-plan done definition

1. `hints: list[str]` no longer exists on any output model.
2. `advisories: list[str]` carries all pure informational text.
3. `hints_structured` contains only tool call suggestions (no empty-args pseudo-hints).
4. `reason` explains why each structured hint was emitted.
5. Test suite passes with zero references to the old `hints` field.
6. `ruff check .` clean.

# Tracking

- `PR-1`: _pending_
