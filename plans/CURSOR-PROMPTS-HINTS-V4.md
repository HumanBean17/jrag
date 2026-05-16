# Cursor task prompts — HINTS-V4

Status: **active**. Plan:
[`plans/PLAN-HINTS-V4.md`](./PLAN-HINTS-V4.md). Propose:
[`propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md`](../propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md).

**Depends on:** NEIGHBORS-DOT-KEY ([#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)) on `master`.

**Propose lock:** Set `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md` `Status: locked` before opening PR-A.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-HINTS-V4-A — `neighbors` success-path catalog (N1a–N7)

**Branch:** `feat/hints-v4-neighbors-success` off `master`.
**Base:** `master` (with #171 merged).
**Plan section:** [`plans/PLAN-HINTS-V4.md`](./PLAN-HINTS-V4.md) § PR-A.
**PR title:** `feat(hints): v4 success-path neighbors road signs (N1a–N7)`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-V4.md`
- `@propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md`
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (priority cap §7.12)
- `@propose/completed/HINTS-V2-PROPOSE.md` (no per-row neighbors hints)
- `@propose/completed/HINTS-V3-PROPOSE.md` (empty neighbors; dot-key prohibition)
- `@propose/completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md` (dot-key context)
- `@mcp_hints.py`
- `@mcp_v2.py` (`neighbors_v2` payload — read only unless field missing)
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-HINTS-V4-A from `plans/PLAN-HINTS-V4.md`.

Confirm `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md` is **Status: locked** before merge.

## Scope

1. **`mcp_hints.py`**
   - Add v4 neighbors success templates N2–N7 (verbatim strings in plan).
   - N1a/N1b: reuse `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` and `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` — do not fork wording.
   - Implement `neighbors_success_hints(payload)` per propose trigger contract (single edge type, offset 0, homogeneous `other`, type subject for N1a/N1b, char cap 120).
   - Wire `generate_hints("neighbors", …)`: call success helper on non-empty `results`; keep empty + fuzzy paths unchanged.
   - **Critical:** `_filter_neighbors_dotkey_hints` applies to **empty structural pairs only** — success-path N1a/N1b must retain `DECLARES.*` dot-keys.
   - Module docstring: reference HINTS-V4 propose.

2. **`tests/test_mcp_hints.py`**
   - Implement every test name under **Tests for PR-A** in `plans/PLAN-HINTS-V4.md` (propose table + recommended N6).
   - Narrow `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints` to **empty** payloads only.
   - Add `test_hints_neighbors_success_may_emit_declares_dot_keys`.
   - Add `test_hints_all_v4_templates_under_120_chars` (parametrize templates + realistic ids).

## Out of scope (do NOT touch)

- `mcp_v2.py` (unless payload field genuinely missing — unexpected).
- `build_ast_graph.py`, `java_ontology.py`, `ONTOLOGY_VERSION`, `EDGE_SCHEMA`.
- `find` / `search` success hints (PR-B).
- `describe` / `resolve` catalog rows.
- `MCP_HINTS_FIELD_DESCRIPTION`, `README.md`, `server.py`.
- `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (PR-B appendix).
- Per-row hints; ontology bump.

## Deliverables

1. N1a–N7 success-path hints on matching non-empty `neighbors` payloads.
2. Dot-key filter split: empty structural never has dot-keys; success N1a/N1b may.
3. All PR-A named tests pass.

## Tests to run

```bash
.venv/bin/ruff check mcp_hints.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "hints_neighbors or hints_all_v4 or hv20"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION`
- `build_ast_graph.py` (unless accidental touch — revert)
- `TPL_FIND_SUCCESS` / `find_success_hints` (PR-B)
- `TPL_SEARCH_SUCCESS` (PR-B unless mistakenly added in A)

## Definition of done

- [ ] PR-A checklist in `plans/PLAN-HINTS-V4.md` satisfied.
- [ ] Propose **locked**.
- [ ] PR body: scope, plan + propose links, note lossy N1a/N1b, **no re-index**.
````

---

## PR-HINTS-V4-B — `find` success-path (+ optional S1, appendix)

**Branch:** `feat/hints-v4-find-success` off `master` (or rebase on PR-A merge).
**Base:** `master` after PR-A merged (or include PR-A commits if stacked).
**Plan section:** [`plans/PLAN-HINTS-V4.md`](./PLAN-HINTS-V4.md) § PR-B.
**PR title:** `feat(hints): v4 success-path find road signs (F1–F3)`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-V4.md`
- `@propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md`
- `@mcp_hints.py`
- `@tests/test_mcp_hints.py`
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (appendix only)

**Prompt:**

````
You are implementing PR-HINTS-V4-B from `plans/PLAN-HINTS-V4.md`.

PR-A (neighbors N1a–N7) is on `master`.

## Scope

1. **`mcp_hints.py`**
   - Add F1–F3 templates and `find_success_hints(payload)`.
   - Wire `generate_hints("find", …)` — success hints at `PRIORITY_LEAF_FOLLOWUP`; page-full stays `PRIORITY_META`.
   - Page-full gate: do not emit F-rows when `len(results) >= limit` and `has_more_results is True`.
   - `{id}` = `results[0]["id"]` when multiple matches (document in PR — not per-row).
   - **Optional:** S1 `search` single-hit → `describe(id='{symbol_id}')` only if plan reviewer approved; else skip S1 and its test.

2. **`propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`**
   - Add short v4 amendment paragraph (#163): second partial dot-key reversal for type-origin neighbors success path only.

3. **`tests/test_mcp_hints.py`**
   - `test_hints_find_route_success_emits_handler`
   - `test_hints_find_client_success_emits_http_calls`
   - `test_hints_find_producer_success_emits_async_calls`
   - `test_hints_search_single_hit_emits_describe` — only if S1 implemented

## Out of scope (do NOT touch)

- Graph builder, ontology, `mcp_v2.py` (find payload already has `kind`, `results`, `limit`, `has_more_results`).
- Neighbors success catalog changes except regressions.
- `MCP_HINTS_FIELD_DESCRIPTION` change.
- `README.md` / `server.py` unless explicitly requested by reviewer.

## Deliverables

1. F1–F3 on non-page-full find success payloads.
2. Appendix paragraph in HINTS-ROAD-SIGNS completed propose.
3. Optional S1 + test if shipped.

## Tests to run

```bash
.venv/bin/ruff check mcp_hints.py tests/test_mcp_hints.py propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "hints_find_route_success or hints_find_client or hints_find_producer or hints_search_single"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION`
- `build_ast_graph.py`
- Changes to `neighbors_success_hints` trigger table (PR-A owned)

## Landing hygiene (after PR-B merges)

- Move `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md` → `propose/completed/`
- Move `plans/PLAN-HINTS-V4.md` and this file → `plans/completed/`

## Definition of done

- [ ] PR-B checklist in plan satisfied.
- [ ] PR body: F1 uses first result id when multiple matches; no re-index.
````
