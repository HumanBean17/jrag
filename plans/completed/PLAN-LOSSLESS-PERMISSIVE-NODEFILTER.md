# Plan: Lossless-permissive `NodeFilter` contract

Status: **completed**. This plan implemented
[`propose/completed/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md`](../../propose/completed/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md).

Depends on: **none** (orthogonal to graph/Kuzu work such as upstream #119).

## Goal

- **Unknown `filter` keys** fail at parse time (no silent drop from typos or nested `kind` mistakes).
- **Cross-kind populated fields** fail with `success=False` and a **teaching** `message` that names offending fields and the **applicable field names** for the effective node kind (per proposal).
- **Lossless wire shapes** stay supported: `_coerce_filter` JSON-string → dict path unchanged; empty `{}` / `None` (where allowed) unchanged; `exclude_roles: []` treated as absent (same as `None`) for applicability and push-down.
- **`search.query` semantics** unchanged; only `filter` contract tightens.
- **`neighbors_v2`** returns `success=False` for invalid **filter** contract (including `ValidationError` from `NodeFilter`), matching `find_v2` / `search_v2` — without swallowing `@validate_call` argument errors (direction / `edge_types` / casing) that should continue to raise `ValidationError`.

## Principles (do not relitigate in review)

- **Bright line (#122):** lossless normalization (e.g. JSON string vs dict for the same object) is OK; **lossy** silent drops are not.
- **No ontology bump, no re-index** — MCP validation only (`README` may get a one-line contract note if tool docs change).
- **No backward-compat shims** for agents that relied on ignored extras or inert cross-kind fields; those calls become explicit errors.
- **Applicability is derived from `_node_matches_filter` + `_symbol_where_from_filter`** — one source of truth for which `NodeFilter` fields affect which kind; do not duplicate divergent allowlists in `server.py` beyond user-facing copy.

## PR breakdown - overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-N1 | `NodeFilter` `extra="forbid"`; applicability helper + wiring in `find_v2`, `search_v2`, `neighbors_v2`; uniform `success=False` for filter `ValidationError` on neighbors; README + MCP `Field` descriptions + `_INSTRUCTIONS` if needed | No | `mcp_v2.py`, `server.py`, `README.md` (minimal), `tests/test_mcp_v2.py` | `tests/test_mcp_v2.py` | — |

Landing order: **PR-N1 only**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Unknown keys | `ConfigDict(extra="forbid")` on `NodeFilter` (Pydantic v2). |
| `ValidationError` vs `success=False` | **Wrap filter-related `ValidationError`** into the same structured tool failure as applicability errors for **all three** tools. **Do not** change `@validate_call` behavior for bad `direction` / `edge_types` / literal casing on `neighbors_v2` (keep raising `ValidationError` for those). Implementation: narrow `try`/`except` around `_coerce_filter` + `NodeFilter.model_validate` (and applicability check) so argument validation still propagates. |
| `neighbors_v2` mixed neighbor kinds | When evaluating each neighbor `other_kind`, if any **populated** field on `NodeFilter` is **not applicable** to `other_kind`, return **`NeighborsOutput(success=False, message=…)` immediately** (first offending neighbor wins). Do not silently skip edges. **Vacuous success:** if there are zero neighbor rows before any applicability check, empty results with a symbol-only filter are acceptable (no neighbor row triggered the check); document as a known residual if reviewers care — proposal prioritizes loud failure when a row is actually evaluated. |
| “Populated” for applicability | `None` absent; for `list[str]` fields, **empty list = absent** (matches proposal and existing `_symbol_where_from_filter` / `_node_matches_filter` list handling). |
| Applicable field sets | **Symbol:** `microservice`, `module`, `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds`. **Route:** `microservice`, `module`, `http_method`, `path_prefix`, `framework`. **Client:** `microservice`, `module`, `source_layer`, `client_kind`, `target_service`, `target_path_prefix`, `client_method`. (Align with current `_node_matches_filter` / `_symbol_where_from_filter`; `source_layer` is client-only in match logic today.) |
| `search_v2` effective kind | Always **symbol**-shaped post-filter rows (per proposal). |

---

# PR-N1 - Loud-fail `NodeFilter` (forbid extras + kind applicability)

## File-by-file changes

### 1. `mcp_v2.py`

- Add `model_config = ConfigDict(extra="forbid")` to `NodeFilter`.
- Add a small helper, e.g. `_populated_nodefilter_fields(nf: NodeFilter) -> set[str]` (field names with non-absent values per principles above).
- Add `_nodefilter_inapplicable_fields(kind: Literal["symbol","route","client"], nf: NodeFilter) -> list[str]` returning sorted field names that are populated but not allowed for `kind` (empty list = filter is applicable to that kind for all populated keys).
- Add `_nodefilter_applicability_error(kind: Literal["symbol","route","client"], nf: NodeFilter) -> str | None` building the teaching `message`: list offending fields + list applicable names for that `kind` (stable ordering for tests).
- **`find_v2`:** after `nf` is built, if `err := _nodefilter_applicability_error(kind, nf)` then `return FindOutput(success=False, message=err)` before graph queries.
- **`search_v2`:** after `nf` is built, if `nf` and `err := _nodefilter_applicability_error("symbol", nf)` then return `SearchOutput(success=False, message=err)` before iterating hits.
- **`neighbors_v2`:** refactor so filter coerce + `model_validate` + **optional** upfront checks live in an inner scope that catches `ValidationError` and returns `NeighborsOutput(success=False, message=…)` with a parsed agent-readable string; in the per-neighbor loop, after `other_kind` / `other_rec` are known, if `errs := _nodefilter_inapplicable_fields(other_kind, nf)` is non-empty when `nf` is not `None`, return `NeighborsOutput(success=False, message=…)` and stop. Preserve existing `except ValidationError: raise` only for errors **outside** the filter pipeline (or split try blocks so filter errors never hit the re-raise path).

### 2. `server.py`

- Update `_INSTRUCTIONS` and `Field(description=…)` for `find` / `search` / `neighbors` **filter** parameters: replace “irrelevant keys ignored per kind” with language that unknown keys error and cross-kind populated fields error with `success=False` + `message` (keep JSON-object vs JSON-string guidance).

### 3. `README.md`

- Minimal MCP tool reference delta: same contract as `server.py` (one short paragraph or bullet under `find` / `search` / `neighbors` filter docs if those sections duplicate the strings — avoid large doc rewrites).

### 4. `tests/test_mcp_v2.py`

- **Replace** `test_find_silent_ignore_irrelevant_filter_keys` (it asserts the old bug): new test expects `success=False` and `message` mentioning inapplicable field(s) for `kind="symbol"` when using route-only `path_prefix` (or rename to `test_find_cross_kind_filter_fields_return_failure` and same assertions).
- Add `test_find_unknown_filter_key_returns_failure` — bogus top-level key → `success=False` (wrapped message) or assert `ValidationError` is not raised to callers of `find_v2` (prefer `success=False` on the output model).
- Add `test_find_symbol_only_field_with_kind_client_returns_failure` — e.g. `fqn_prefix` + `find_v2("client", …)`.
- Add `test_find_client_only_field_with_kind_symbol_returns_failure` — e.g. `client_kind` + `find_v2("symbol", …)`.
- Add `test_search_unknown_filter_key_returns_failure` (monkeypatch `run_search` to cheap rows).
- Add `test_search_cross_kind_filter_returns_failure` — e.g. `path_prefix` or `client_kind` on `search_v2` (symbol applicability).
- Keep / assert regressions: `test_find_symbol_empty_filter_handles_non_declaration_symbol_kinds`, `test_find_symbol_by_role` or explicit `test_find_symbol_fqn_prefix_still_honored` if not redundant; `test_search_filter_accepts_json_string`, `test_neighbors_filter_accepts_json_string`.
- Add `test_neighbors_filter_unknown_key_returns_failure` and `test_neighbors_filter_cross_kind_on_neighbor_returns_failure` — use fixture graph helpers (`_method_id_with_calls`, etc.) to force a neighbor whose kind makes a populated field inapplicable (e.g. `path_prefix` with a symbol neighbor, or `fqn_prefix` with a client neighbor). Assert **`success=False`** and **no** `ValidationError` for filter unknown keys.
- Add `test_neighbors_validate_call_still_raises` — quick smoke that invalid `edge_types` / missing `direction` still `pytest.raises(ValidationError)` (unchanged).

## Tests for PR-N1

1. `test_find_cross_kind_filter_fields_return_failure` (replaces former silent-ignore test; exact name negotiable but must assert loud failure).
2. `test_find_unknown_filter_key_returns_failure`
3. `test_find_symbol_only_field_with_kind_client_returns_failure`
4. `test_find_client_only_field_with_kind_symbol_returns_failure`
5. `test_search_unknown_filter_key_returns_failure`
6. `test_search_cross_kind_filter_returns_failure`
7. `test_neighbors_filter_unknown_key_returns_failure`
8. `test_neighbors_filter_cross_kind_on_neighbor_returns_failure`
9. `test_neighbors_validate_call_still_raises`
10. Regression carries: `test_find_symbol_empty_filter_handles_non_declaration_symbol_kinds`, `test_search_filter_accepts_json_string`, `test_neighbors_filter_accepts_json_string`, existing role / `fqn_prefix` coverage as appropriate.

## Definition of done (PR-N1)

- [ ] `NodeFilter` rejects unknown keys; `find_v2` / `search_v2` / `neighbors_v2` never silently ignore them.
- [ ] Cross-kind populated fields produce `success=False` with teaching messages for `find` + `search`; `neighbors` fails on first neighbor row where applicability breaks.
- [ ] `neighbors_v2` filter `ValidationError` is wrapped to `success=False`; `@validate_call` errors still raise.
- [ ] `exclude_roles: []` does not trigger applicability errors.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] `.venv/bin/python -m pytest tests/test_mcp_v2.py -v` (or full `tests -v` per `AGENTS.md`) passes.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `ConfigDict(extra="forbid")` to `NodeFilter` | `mcp_v2.py` | Unknown key raises `ValidationError` at `model_validate` |
| 2 | Implement populated-field + applicability helpers | `mcp_v2.py` | Unit-testable pure functions; docstring notes alignment with `_node_matches_filter` |
| 3 | Wire `find_v2` / `search_v2` applicability + wrap filter `ValidationError` | `mcp_v2.py` | New find/search tests pass |
| 4 | Refactor `neighbors_v2` try/except boundaries; per-neighbor applicability; wrap filter `ValidationError` | `mcp_v2.py` | New neighbors tests pass; validate_call tests still raise |
| 5 | Update MCP descriptions + `_INSTRUCTIONS` | `server.py` | Wording matches new contract |
| 6 | README minimal filter contract note | `README.md` | Matches server copy |
| 7 | Replace/add tests per table above | `tests/test_mcp_v2.py` | All green |

---

## Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | `neighbors_v2` accidentally wraps non-filter `ValidationError` | Medium | Narrow `except ValidationError` to the filter parse block only; keep outer `raise` for `validate_call`. |
| 2 | Applicability set drifts from `_node_matches_filter` after a future edit | Medium | Code comment + single helper used by tools; consider a one-line assertion in tests that symbol applicability includes `fqn_prefix`. |
| 3 | Agents relied on silent ignore | Low (intentional break) | Message lists applicable fields; README + tool `description` teach the contract. |

## Out of scope

- Full #117 strict / permissive / hybrid vocabulary frame.
- New tools, `NodeFilter` field renames, cross-kind aliasing.
- `search.query` ranking / fuzzy behavior.
- `EdgeType` / `find.kind` literals / Kuzu schema.
- `describe_v2` (no `filter` today).
- Structured `hints` field on outputs (#120) — messages should remain plain string until that work lands.

## Whole-plan done definition

1. Proposal behavior (items 1–3 + `_coerce_filter` preserved) is implemented in `mcp_v2.py` and covered by tests.
2. Public MCP strings (`server.py`, optional `README.md`) match the new contract.
3. Ruff + pytest (at least `tests/test_mcp_v2.py`, preferably full `tests`) pass.

## Tracking

- `PR-N1`: _done_
- Per-PR Cursor prompts: [`plans/AGENT-PROMPTS-LOSSLESS-PERMISSIVE-NODEFILTER.md`](./AGENT-PROMPTS-LOSSLESS-PERMISSIVE-NODEFILTER.md)
