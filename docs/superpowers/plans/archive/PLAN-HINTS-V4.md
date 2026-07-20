<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: HINTS-V4 (success-path road signs)

Status: **completed** (PR-A [#175](https://github.com/HumanBean17/java-codebase-rag/pull/175), PR-B [#176](https://github.com/HumanBean17/java-codebase-rag/pull/176)). This plan implements
[`propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md`](../propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md)
(issue [#163](https://github.com/HumanBean17/java-codebase-rag/issues/163)).

Depends on: **NEIGHBORS-DOT-KEY-TRAVERSAL** landed ([#171](https://github.com/HumanBean17/java-codebase-rag/pull/171);
[`propose/completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md`](../propose/completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md)).
`neighbors_v2` already echoes `subject_record`, `origin_id`, `requested_edge_types`, `requested_direction`, and `offset` — no `mcp_v2.py` payload work required.

## Goal

- **PR-A:** Add v4 **non-empty** `neighbors` success-path catalog (N1a–N7): output-level follow-ups keyed on homogeneous endpoint kinds, single requested edge type, `offset == 0`, and (for N1a/N1b) type Symbol `subject_record`.
- **PR-B:** Add v4 **`find`** success-path catalog (F1–F3); optional **S1** `search` single-hit row; short **HINTS-ROAD-SIGNS** appendix traceability paragraph for the second partial dot-key emission reversal.
- Agents chaining `neighbors(class, DECLARES)` → dot-key clients/routes → `HTTP_CALLS` / handler see road signs **without** calling `describe` first.

## Principles (do not relitigate in review)

- **Output-level only** — extends v2: no per-row hints on `Edge` / `NodeRef` / `SearchHit`; no confidence / `attrs.match` gates on success rows.
- **No graph I/O** — hints are pure functions of MCP payload dicts (`.model_dump()` shapes already built in `mcp_v2.py`).
- **Single edge type** — `len(requested_edge_types) == 1`; multi-edge requests get no v4 success hints (agent-composed queries).
- **Homogeneous endpoints** — mixed `other.kind` (or mixed method **and** type Symbols on one page) → silence for that template row. **Method + constructor** Symbols together are homogeneous (both ∈ `_METHOD_SYMBOL_KINDS`).
- **Multi-origin `neighbors`** — success hints use echoed `origin_id` / `subject_record` for **`origins[0]` only** (v3 empty-hint parity); no v4-specific multi-origin payload field.
- **`subject_record` is a flat Kuzu row** — `neighbors_v2` passes `_load_node_record` output (top-level `kind: "class" | …`), **not** `NodeRecord.model_dump()`. N1a/N1b type gate must match v3: `_subject_node_label == "Symbol"` and top-level `subject_record["kind"] in _TYPE_SYMBOL_KINDS`.
- **Pagination** — success hints require `offset == 0` (mirror v3 empty suppression).
- **Priority** — all v4 rows use `PRIORITY_LEAF_FOLLOWUP` (2); beat v2 fuzzy and v3 empty (`PRIORITY_META` 1) in cap contests; N1a + N1b may co-fire (both priority 2).
- **Dot-key partial reversal (neighbors success only)** — N1a/N1b reuse `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` / `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` verbatim (`{id}` = `origin_id`). N2/N3 accept flat **or** composed `DECLARES.*` triggers. **v3 empty structural hints** still never emit dot-keys — apply `_filter_neighbors_dotkey_hints` to **empty-branch pairs only**, not success-path pairs.
- **N1a/N1b always co-fire** when homogeneous method/constructor targets hold — lossy vs `edge_summary` counts; do not add combined `or` line (exceeds 120 chars).
- **N1a/N1b vs describe** — intentional duplicate road signs across tools.
- **`MCP_HINTS_FIELD_DESCRIPTION` unchanged** — already documents describe dot-keys + empty-neighbors dot-key prohibition; success-path neighbors dot-keys are an implementation amendment only (see PR-B appendix).
- **No ontology bump, no re-index** — query-time hint logic only.
- **No `mcp_v2.py` changes** unless a test round-trip exposes a missing payload field (not expected post-#171).

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-A | `mcp_hints.py` neighbors success + `tests/test_mcp_hints.py` N* + dot-key filter split | **No** | `_filter_neighbors_dotkey_hints` scope; N1a/N1b flat `subject_record` gate (must match v3, not describe `data.kind`); required `neighbors_v2` round-trip; cap co-fire with fuzzy | `test_hints_neighbors_*` (propose table) | #171 |
| PR-B | `mcp_hints.py` find (+ optional search S1); appendix note; F* (+ optional S1) tests | **No** | F1 uses `results[0].id` only when `len(results) > 1`; page-full meta vs F-row priority; HV20 must stay empty-only for dot-keys | `test_hints_find_*`, optional `test_hints_search_*` | PR-A optional |

**Landing order:** **PR-A → PR-B** (PR-B may rebase on PR-A; can collapse to one PR if reviewer prefers — keep sections separable for review).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Implementation surface | `mcp_hints.py` only (+ tests; PR-B adds optional appendix in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`) |
| N1a/N1b templates | Import/reference describe constants — do not fork strings |
| N2/N3 edge triggers | `DECLARES_CLIENT` **or** `DECLARES.DECLARES_CLIENT`; `DECLARES_PRODUCER` **or** `DECLARES.DECLARES_PRODUCER` |
| N7 trigger | `DECLARES.EXPOSES`, `direction=='out'`, all `other.kind=='route'` |
| Rejected N1 combined `or` line | Exceeds 120 chars; N1a + N1b only |
| `find` multi-match | F-rows emit once with `results[0]["id"]` — not per-row |
| Search S1 | **Defer to PR-B optional** — default skip unless traces warrant |
| N6 test | `test_hints_neighbors_async_calls_in_producers_emits_declares_producer` (locked in propose) |
| `subject_record` shape | Flat Kuzu row from `_load_node_record` — reuse v3 type-subject gate (`subject_record.get("kind") in _TYPE_SYMBOL_KINDS`), not nested `data.kind` |
| N1a round-trip | **Required** `test_hints_neighbors_v2_declares_success_emits_dot_key_clients` on `kuzu_graph` |
| Propose lock | Locked in planning PR [#174](https://github.com/HumanBean17/java-codebase-rag/pull/174); must remain locked before PR-A **merge** |
| `EDGE_SCHEMA.type_subject` / #172 | Out of scope — v3 empty hints may still show legacy two-hop strings |
| `IMPLEMENTS` / `EXTENDS` / generic `CALLS` success | Deferred (v1 §5); N4 is the only `CALLS` success row |

---

# PR-A — `neighbors` success-path catalog (N1a–N7)

## File-by-file changes

### 1. `mcp_hints.py`

- Module docstring: add v4 amendment pointer to `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md`.
- Add verbatim success templates (flat literals only where not aliasing describe):
  - **N1a/N1b:** emit using existing `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` / `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` with `{id}` = `payload["origin_id"]` (fallback: first result `origin_id` if needed — prefer echoed `origin_id`).
  - **N2:** `TPL_NEIGHBORS_SUCCESS_HTTP_TARGETS` = `HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])`
  - **N3:** `TPL_NEIGHBORS_SUCCESS_ASYNC_TARGETS` = `async targets: neighbors(producer_ids,'out',['ASYNC_CALLS'])`
  - **N4:** `TPL_NEIGHBORS_SUCCESS_CALLERS` = `callers: neighbors(handler_ids,'in',['CALLS'])`
  - **N5:** `TPL_NEIGHBORS_SUCCESS_DECLARING_CLIENT` = `declaring method: neighbors(client_ids,'in',['DECLARES_CLIENT'])`
  - **N6:** `TPL_NEIGHBORS_SUCCESS_DECLARING_PRODUCER` = `declaring method: neighbors(producer_ids,'in',['DECLARES_PRODUCER'])`
  - **N7:** `TPL_NEIGHBORS_SUCCESS_HANDLER` = `handler: neighbors(route_ids,'in',['EXPOSES'])`
- Add helpers (names illustrative; match repo style):
  - `_neighbors_success_subject_is_type(subject_record) -> bool` — same as v3 type-level detection: `_subject_node_label(subject_record) == "Symbol"` and `str(subject_record.get("kind") or "") in _TYPE_SYMBOL_KINDS` (flat Kuzu row; do **not** require nested `data.kind`).
  - `_neighbors_results_homogeneous(results, *, endpoint_kind: str | None, symbol_kinds: frozenset[str] | None) -> bool` — every `results[i]["other"]` matches predicate; for method rows use `other["symbol_kind"] in _METHOD_SYMBOL_KINDS` when present.
  - `neighbors_success_hints(payload: dict[str, Any]) -> list[tuple[int, str]]` — evaluate N1a–N7 in fixed order; each rendered string `len <= 120` or drop; all pairs at `PRIORITY_LEAF_FOLLOWUP`.
- **`generate_hints` `neighbors` branch:**
  1. Collect `empty_pairs` from `neighbors_empty_hints` when `not results and edge_labels and offset == 0` (unchanged).
  2. When `results` and `offset == 0`: `success_pairs = neighbors_success_hints(payload)`.
  3. When `results` and fuzzy strategy present: append `TPL_NEIGHBORS_FUZZY_STRATEGY` (unchanged).
  4. `return finalize_hint_list(_filter_neighbors_dotkey_hints(empty_pairs) + success_pairs + meta_pairs)` — **do not** filter success_pairs.
- Trigger contract (all must hold per row): `success`, non-empty `results`, `offset == 0`, exactly one `requested_edge_types` entry, homogeneous `other`, direction match, char cap.

| ID | `requested_edge_types[0]` | `requested_direction` | Homogeneous `other` |
| --- | --- | --- | --- |
| N1a | `DECLARES` | `out` | method/constructor Symbols + type subject |
| N1b | `DECLARES` | `out` | same payload as N1a |
| N2 | `DECLARES_CLIENT` or `DECLARES.DECLARES_CLIENT` | `out` | `kind == "client"` |
| N3 | `DECLARES_PRODUCER` or `DECLARES.DECLARES_PRODUCER` | `out` | `kind == "producer"` |
| N4 | `EXPOSES` | `in` | method/constructor Symbols |
| N5 | `HTTP_CALLS` | `in` | `kind == "client"` |
| N6 | `ASYNC_CALLS` | `in` | `kind == "producer"` |
| N7 | `DECLARES.EXPOSES` | `out` | `kind == "route"` |

### 2. `tests/test_mcp_hints.py`

- Extend `_neighbors_hint_payload` with `origin_id` and `offset` defaults (`offset=0`).
- Add `_type_subject_record(node_id, decl_kind="class")` helper returning a **flat Kuzu-shaped** dict (`{"id": node_id, "kind": decl_kind}` — same shape as v3 HV tests and production `subject_record`).
- Add synthetic `results[]` builders with `other: {kind, id, symbol_kind?}` matching `Edge.model_dump()`.
- Implement every named test from the propose § Tests table (PR-A subset).
- **Required:** `test_hints_neighbors_v2_declares_success_emits_dot_key_clients` — `neighbors_v2` on session `kuzu_graph` (type Symbol → `DECLARES` out → non-empty methods); asserts N1a in output hints (guards flat vs NodeRecord test footgun).
- **Update `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints`** — docstring/assertion scope: **empty structural hints only**; add `test_hints_neighbors_success_may_emit_declares_dot_keys` for N1a/N1b on synthetic flat `subject_record`.

## Tests for PR-A

Implement **verbatim** names from the propose:

1. `test_hints_neighbors_declares_methods_emits_dot_key_clients`
2. `test_hints_neighbors_declares_methods_emits_dot_key_routes`
3. `test_hints_neighbors_declares_client_homogeneous_emits_http_calls`
4. `test_hints_neighbors_declares_dot_key_client_homogeneous_emits_http_calls`
5. `test_hints_neighbors_declares_producer_homogeneous_emits_async_calls`
6. `test_hints_neighbors_declares_dot_key_producer_homogeneous_emits_async_calls`
7. `test_hints_neighbors_declares_dot_key_exposes_homogeneous_emits_handler`
8. `test_hints_neighbors_exposes_in_methods_emits_calls`
9. `test_hints_neighbors_http_calls_in_clients_emits_declares_client`
10. `test_hints_neighbors_async_calls_in_producers_emits_declares_producer`
11. `test_hints_neighbors_mixed_endpoint_kinds_silent`
12. `test_hints_neighbors_offset_suppresses_success_hints`
13. `test_hints_neighbors_success_beats_fuzzy_in_cap`
14. `test_hints_neighbors_v2_declares_success_emits_dot_key_clients` — **required** `neighbors_v2` round-trip
15. `test_hints_all_v4_templates_under_120_chars` — parametrize new templates + N1a/N1b via describe constants + N7; realistic id substitution
16. `test_hints_neighbors_success_may_emit_declares_dot_keys` — HV20 complement

**Regression:** all `test_hints_hv*`, `test_hints_neighbors_fuzzy_*`, v1 describe/find tests unchanged except HV20 scope clarification.

## Definition of done (PR-A)

- [x] N1a–N7 wired; empty vs success dot-key filter split correct.
- [x] All named PR-A tests pass.
- [x] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [x] No `ONTOLOGY_VERSION` / graph / `mcp_v2.py` changes.
- [x] Propose remains **locked** (done in #174).
- [x] `test_hints_neighbors_v2_declares_success_emits_dot_key_clients` passes.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Success templates + `neighbors_success_hints` | `mcp_hints.py` | N2–N7 unit triggers pass |
| 2 | N1a/N1b via describe template aliases + type subject gate | `mcp_hints.py` | Tests 1–2 pass |
| 3 | Wire `generate_hints` + dot-key filter split | `mcp_hints.py` | Mixed/offset/fuzzy cap tests pass |
| 4 | Synthetic payload tests + HV20 split | `tests/test_mcp_hints.py` | Full PR-A table green |
| 5 | `neighbors_v2` round-trip + char-cap | `tests/test_mcp_hints.py` | Round-trip + `test_hints_all_v4_templates_under_120_chars` pass |

---

# PR-B — `find` success-path (+ optional search S1, appendix)

## File-by-file changes

### 1. `mcp_hints.py`

- Add find success templates:
  - **F1:** `TPL_FIND_SUCCESS_HANDLER` = `handler: neighbors(['{id}'],'in',['EXPOSES'])`
  - **F2:** `TPL_FIND_SUCCESS_HTTP_TARGETS` = `HTTP targets: neighbors(['{id}'],'out',['HTTP_CALLS'])`
  - **F3:** `TPL_FIND_SUCCESS_ASYNC_TARGETS` = `async targets: neighbors(['{id}'],'out',['ASYNC_CALLS'])`
- Add `find_success_hints(payload) -> list[tuple[int, str]]`:
  - Gates: `success`, `len(results) > 0`, not page-full (`not (limit set and len(results) >= limit and has_more_results)`), `kind` match, `{id}` = `results[0]["id"]`, char cap.
- Extend `generate_hints` `find` branch: merge `find_success_hints` pairs **before** `finalize_hint_list` (page-full meta at priority 1; F-rows at priority 2 win cap).
- **Optional S1:** `TPL_SEARCH_SUCCESS_DESCRIBE` = `inspect: describe(id='{symbol_id}')`; fire only when `len(results)==1` and top hit has `symbol_id`; priority 2; skip if char cap exceeded.

### 2. `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`

- Short appendix paragraph: **v4 amendment (#163)** — second partial dot-key emission reversal: success-path `neighbors` on type Symbol origins may recommend `DECLARES.DECLARES_CLIENT` / `DECLARES.DECLARES_PRODUCER` / `DECLARES.EXPOSES`; v3 empty structural hints unchanged; `OVERRIDDEN_BY.*` remains describe-only.

### 3. `tests/test_mcp_hints.py`

1. `test_hints_find_route_success_emits_handler`
2. `test_hints_find_client_success_emits_http_calls`
3. `test_hints_find_producer_success_emits_async_calls`
4. `test_hints_search_single_hit_emits_describe` — **only if S1 ships**

## Definition of done (PR-B)

- [x] F1–F3 wired; page-full + empty-resolve behavior unchanged.
- [x] Named find tests pass; optional S1 test if implemented (S1 deferred).
- [x] Appendix paragraph added to `HINTS-ROAD-SIGNS-PROPOSE.md`.
- [x] Full test suite green; no ontology/README requirement unless reviewer asks for README mention.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Find templates + `find_success_hints` | `mcp_hints.py` | F1–F3 tests pass |
| 2 | Optional S1 + search branch | `mcp_hints.py` | S1 test if shipped |
| 3 | Appendix traceability | `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` | Paragraph merged |
| 4 | Find (+ search) tests | `tests/test_mcp_hints.py` | PR-B tests green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Success-path dot-keys filtered by `_filter_neighbors_dotkey_hints` | High | Filter empty branch only; `test_hints_neighbors_success_may_emit_declares_dot_keys` |
| 2 | Wrong `subject_record` shape in tests vs production | **High** | Flat Kuzu fixtures (`{"id", "kind": "class"}`); **required** `neighbors_v2` round-trip test |
| 3 | N1a/N1b noise when type has no clients/routes | Low | Accepted lossy design (Open Q1); document in PR body |
| 4 | N4 empty `CALLS` follow-up loops | Low | Ship advisory hint; remove only if traces show harm (Open Q2) |
| 5 | F1 misleading when `len(results) > 1` | Medium | Document `results[0]` only in PR description (Open Q4) |
| 6 | Cap drops fuzzy but not success | Low | `test_hints_neighbors_success_beats_fuzzy_in_cap` |
| 7 | HV20 false failure after PR-A | Medium | Narrow HV20 to empty payloads only |

# Out of scope

- Per-row hints; `resolve(status='one')` hints; `describe` catalog changes.
- `IMPLEMENTS` / `EXTENDS` generic success chains; `EDGE_SCHEMA` / graph builder / `type_subject` (#172).
- `structured next_actions`; `hints_version` field.
- Conditioning success hints on `attrs.match` / confidence.
- Member-batch `DECLARES` → `member_ids` → flat edges as primary teaching path.
- `OVERRIDDEN_BY.*` dot-keys in any hint emission.
- Ontology bump and re-index.
- Changing `MCP_HINTS_FIELD_DESCRIPTION` (frozen per propose).

# Whole-plan done definition

1. Non-empty `neighbors` at `offset==0` with single edge type emits N* follow-ups per homogeneous endpoint rules; N1a/N1b use describe dot-key strings on type origins.
2. v3 empty structural hints still never contain dot-keys; success-path dot-keys not filtered.
3. `find` success emits F1–F3 when not page-full; optional S1 for single-hit `search`.
4. All propose-named tests pass; char-cap sweep includes v4 templates.
5. `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md` moved to `propose/completed/`; this plan + prompts moved to `plans/completed/` after PR-B lands.

# Tracking

- `PR-A`: _landed ([#175](https://github.com/HumanBean17/java-codebase-rag/pull/175))_
- `PR-B`: _landed ([#176](https://github.com/HumanBean17/java-codebase-rag/pull/176))_

## Cursor handoff

[`plans/completed/AGENT-PROMPTS-HINTS-V4.md`](./AGENT-PROMPTS-HINTS-V4.md)
