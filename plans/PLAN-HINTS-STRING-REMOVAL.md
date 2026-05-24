# Plan: HINTS-STRING-REMOVAL

Status: **active**. This plan implements
[`propose/HINTS-STRING-REMOVAL-PROPOSE.md`](../propose/HINTS-STRING-REMOVAL-PROPOSE.md)
as a single PR.

Depends on: none (all prerequisite work — `hints_structured` with `label` — is landed).

## Goal

- Remove the redundant `hints: list[str]` field from all five MCP tool output models.
- Consolidate advisory text into a new `reason: str` field on `StructuredHint`.
- Eliminate dual-emission maintenance burden and the parity test.

## Principles (do not relitigate in review)

- **Single hint mechanism**: `hints_structured` is the only hint field after this change.
- **`reason` defaults to `""`**: backward-compatible; actionable hints may have an empty reason (they already carry `tool` + `args`). Non-actionable hints **should** carry a reason — enforced at test time, not by schema.
- **Breaking change is allowed**: AGENTS.md and the proposal explicitly state breaking changes are always allowed; no deprecation cycle.
- **No re-index, no ontology bump**: this is an output-only change.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Remove `hints` field, add `reason` to `StructuredHint`, remove string templates and parity test, update docs | none | `generate_hints` return type change ripples to all five tool handlers; test assertions migrating from `out.hints` to `out.hints_structured[i].reason`; `server.py` tool descriptions referencing `hints` | hint unit tests, parity removal, round-trip integration, docs consistency | n/a |

Landing order: **PR-1 only** (single PR).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `reason` default | `reason: str = ""` — avoids forcing a reason on every hint; non-actionable hints are expected to carry one (test-enforced). |
| String template deletion | Delete entirely; git history preserves them. |
| `generate_hints` return type | `list[_StructuredHint]` — no tuple wrapper. |
| `_to_structured_hints` conversion | Must forward `reason` from internal `_StructuredHint` to public `StructuredHint`. |
| `finalize_hint_list` | Delete — no longer needed. |

---

# PR-1 — Remove string hints, add reason to StructuredHint

## File-by-file changes

### 1. `mcp_hints.py`

- Add `reason: str = ""` field to `_StructuredHint` NamedTuple.
- Remove `MCP_HINTS_FIELD_DESCRIPTION` constant.
- Remove `finalize_hint_list` function.
- Remove all `TPL_*` string template constants.
- Change `generate_hints` signature: return `list[_StructuredHint]` instead of `tuple[list[str], list[_StructuredHint]]`.
- Remove all `pairs` / `finalize_hint_list` calls throughout `generate_hints`; keep only `struct_pairs` logic.
- For each hint that previously appended to `pairs`, extract the advisory text into a `reason=` kwarg on the corresponding `_StructuredHint`.
  - Example: the search weak-results hint currently does `pairs.append((PRIORITY_META, TPL_SEARCH_WEAK))` and `struct_pairs.append(_StructuredHint("find", ..., LABEL_WEAK_RESULTS))`. After change: `struct_pairs.append(_StructuredHint("find", ..., LABEL_WEAK_RESULTS, reason="results look weak — narrow the query or try find with a role filter"))`.
  - The `reason` text should be derived from the former template rendering (substitute any format variables as needed).
- Remove `_filter_neighbors_dotkey_hints` function (string-hint-specific).
- Remove `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS` cap constants (they applied to rendered string length; structured hints use the cap from `finalize_structured_hints`).
- Remove `_append_find_success_hint` and `_append_neighbors_success_hint` helpers.
- Remove `find_success_hints` function (string-only); keep the structured-hint emission logic inline in `generate_hints`.
- Remove `neighbors_success_hints` function (string-only); keep `_neighbors_success_structured_hints`.
- Remove `neighbors_empty_hints` function (string-only); keep `_neighbors_empty_structured_hints`.
- Remove `neighbors_calls_fanout_hints` and `neighbors_calls_meta_hints` (string-only); keep the structured meta hint logic inline in `generate_hints`.
- Remove `_FIRST_NEIGHBORS_CALL_RE`, `_parse_first_traversal` — these were used to parse string templates into structured hint args; now unnecessary.
- Keep all `LABEL_*` constants — they are still used for `_StructuredHint.label`.

### 2. `mcp_v2.py`

- Add `reason: str = ""` field to `StructuredHint` model.
- Remove `hints: list[str]` field from `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`, `ResolveOutput`.
- Remove `MCP_HINTS_FIELD_DESCRIPTION` import from `mcp_hints`.
- Update `_to_structured_hints` to forward `reason`.
- Update all five tool functions (`search_v2`, `find_v2`, `describe_v2`, `neighbors_v2`, `resolve_v2`):
  - Change `str_hints, raw_struct = generate_hints(...)` to `raw_struct = generate_hints(...)`.
  - Remove `hints=str_hints` from all `*Output(...)` constructor calls.
  - Remove `hints=[]` from all error-path `*Output(...)` constructor calls.
- Remove all `hints=[]` from early-return error paths in `search_v2`, `find_v2`, `describe_v2`, `neighbors_v2`, `resolve_v2`, `_resolve_finalize_success`.

### 3. `server.py`

- Update MCP tool `_INSTRUCTIONS` / description strings that reference `"hints"`:
  - Line 344: change `hints` (advisory next-step strings) to `hints_structured` (advisory next-step objects with tool, args, actionable, label, reason).
  - Line 393: same.
  - Line 429: same.
  - Lines 463-464: same.
  - Line 549: same.

### 4. `tests/test_mcp_hints.py`

- Remove `test_structured_hints_parity_with_string_hints` (line 2589–2638).
- Remove `_hints` helper function (line 41-43) — it returns string hints only.
- Remove `_structural_neighbors_hints` helper (lines 426-433) — it filters string hints.
- Migrate all tests that assert on `out.hints` (string form) to assert on `out.hints_structured` instead:
  - Tests checking for template presence in `out.hints` → check `any(h.reason and "<substring>" in h.reason for h in out.hints_structured)`.
  - Tests checking `out.hints == []` → check `out.hints_structured == []` (or equivalently, no hint with a specific tool).
  - Tests using `_hints(output_kind, payload)` → use `_struct(output_kind, payload)` instead.
- Update `test_hints_clean_outputs_empty` to assert on `hints_structured`.
- Remove string-template length tests (`test_hints_all_v4_templates_under_120_chars`, `test_hints_template_rendered_length_leq_120`) — these validated string template rendering; replace with a test that all `reason` strings are ≤ 120 chars.
- Remove `test_hints_dedupe_collapses_identical_rendered_strings`, `test_hints_cap_drops_lowest_priority_over_five`, `test_hints_cap_same_priority_keeps_emission_order` — these tested `finalize_hint_list` which is deleted; the structured equivalents already exist (`test_structured_hints_dedup`, `test_structured_hints_cap_5`).
- Update `_resolve_finalize_success` in `mcp_v2.py` to remove `hints=str_hints` from `model_copy(update=...)`.

### 5. `docs/AGENT-GUIDE.md`

- Update line 19 to remove reference to `hints` list and describe only `hints_structured`.
- Document the `reason` field on structured hints.

### 6. `README.md`

- No changes needed (README does not detail the `hints` field explicitly; the five-tool table links to AGENT-GUIDE).

## Tests for PR-1

1. `test_structured_hints_cap_5` — existing, unchanged.
2. `test_structured_hints_dedup` — existing, unchanged.
3. `test_structured_hint_round_trip` — existing, updated to remove `out.hints` assertions.
4. `test_structured_hint_label_values` — existing, unchanged.
5. All `test_hints_*` tests — migrated from `out.hints` to `out.hints_structured[*].reason`.
6. `test_structured_hints_reason_content` — **new test**: verify `reason` field carries expected text for key scenarios (describe type rollup, describe method overriders, search weak, find empty, resolve none, neighbors empty structural).
7. `test_structured_hints_reason_char_cap` — **new test**: verify all emitted `reason` strings are ≤ 120 chars.
8. `test_no_string_hints_field` — **new test**: verify `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`, `ResolveOutput` have no `hints` field.

## Definition of done (PR-1)

- `hints: list[str]` field absent from all five output models.
- `reason: str` field present on `StructuredHint` and `_StructuredHint`.
- `generate_hints` returns `list[_StructuredHint]`, not a tuple.
- `MCP_HINTS_FIELD_DESCRIPTION`, all `TPL_*` constants, `finalize_hint_list` removed from `mcp_hints.py`.
- `server.py` tool descriptions reference `hints_structured`, not `hints`.
- Full test suite passes: `.venv/bin/python -m pytest tests -v`.
- Ruff clean: `.venv/bin/ruff check .`.
- No references to `hints: list[str]` in AGENT-GUIDE or server descriptions.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `reason` to `_StructuredHint`, change `generate_hints` return type | `mcp_hints.py` | `generate_hints` returns `list[_StructuredHint]`; all callers updated in same step |
| 2 | Remove `MCP_HINTS_FIELD_DESCRIPTION`, all `TPL_*`, `finalize_hint_list`, string-only helpers | `mcp_hints.py` | No string template constants or string-only functions remain |
| 3 | Add `reason` to public `StructuredHint`, remove `hints` from all 5 output models | `mcp_v2.py` | All `*Output` models lack `hints` field; `_to_structured_hints` forwards `reason` |
| 4 | Update all 5 tool functions to use new `generate_hints` signature | `mcp_v2.py` | No `str_hints` variable; no `hints=str_hints` in any constructor |
| 5 | Update `server.py` tool descriptions | `server.py` | All descriptions say `hints_structured` |
| 6 | Migrate test assertions from `out.hints` to `out.hints_structured` | `tests/test_mcp_hints.py` | All tests pass |
| 7 | Remove parity test, string-only test helpers | `tests/test_mcp_hints.py` | `test_structured_hints_parity_with_string_hints` removed; `_hints` helper removed |
| 8 | Update `docs/AGENT-GUIDE.md` | `docs/AGENT-GUIDE.md` | No `hints` list reference; `hints_structured` documented with `reason` |
| 9 | Run full validation | all | `ruff check .` clean; `pytest tests -v` passes |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Downstream consumers expecting `hints` field | low | Breaking changes are always allowed per AGENTS.md; `hints_structured` has been present since #209 |
| 2 | Reason text divergence from former templates | medium | Derive reason strings from the same template renderings at authoring time; verify via new `reason` content tests |

# Out of scope

- Changing hint trigger logic or priority tiers.
- Adding new hint categories or triggers.
- Modifying the `_StructuredHint.priority` field semantics.
- Changing the `label` field behavior.
- Adding `reason` to `EDGE_SCHEMA` or any graph-layer changes.
- Index rebuild or ontology bump.

# Whole-plan done definition

1. `hints: list[str]` no longer exists on any output model.
2. All advisory text lives in `reason` on `StructuredHint`.
3. Test suite passes with zero references to the old `hints` field.
4. `ruff check .` clean.

# Tracking

- `PR-1`: _pending_
