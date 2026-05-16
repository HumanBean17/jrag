# Cursor task prompts — Lossless-permissive `NodeFilter` (PR-N1)

Status: **completed** (reference). Implements
[`plans/PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md`](./PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md)
and
[`propose/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md`](../propose/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md).

One prompt for the single PR. Copy the **Prompt** block into Cursor agent mode, attach the listed `@-files`, and execute. If the prompt disagrees with the plan, **the plan wins**.

**Universal rules:**

- Use repo `.venv/bin/python` and `.venv/bin/ruff` only (see `AGENTS.md`).
- Do not push from the agent; you open the PR.
- If a change would touch files outside the PR’s allowlist, stop and ask.

---

## PR-N1 — Loud-fail `NodeFilter` (forbid extras + kind applicability)

**Branch:** `feat/lossless-nodefilter` off `master`.

**Base:** `master`.

**Plan section:** `plans/PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md` § **PR-N1 - Loud-fail `NodeFilter`**.

**Estimated diff size:** ~4 files, ~200–350 LOC (mostly `mcp_v2.py` + tests).

**Attach (`@-files`):**

- `@plans/PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md`
- `@propose/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md`
- `@mcp_v2.py`
- `@server.py`
- `@README.md` (MCP tool reference §4 — `NodeFilter` notes only, minimal edit)
- `@tests/test_mcp_v2.py`
- `@tests/conftest.py` (session `kuzu_graph` fixture — read-only unless you must extend fixtures; prefer not)

**Prompt:**

````
You are implementing PR-N1 from `plans/PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md`.

Read the plan’s **PR-N1** section and the **Resolved design decisions** table in full before coding. The plan is the source of truth.

## Scope

- **`mcp_v2.py`**
  - Set `NodeFilter.model_config = ConfigDict(extra="forbid")` (Pydantic v2); import `ConfigDict` as needed.
  - Implement helpers per plan: populated-field detection (treat `exclude_roles: []` as absent), `_nodefilter_inapplicable_fields`, `_nodefilter_applicability_error` with **stable** field ordering in messages for tests.
  - Applicable field sets must match the plan table (aligned with `_node_matches_filter` / `_symbol_where_from_filter`; `source_layer` is client-only).
  - **`find_v2`:** after `NodeFilter` is built, if applicability error → `FindOutput(success=False, message=err)` before any graph query. Catch filter `ValidationError` from unknown keys and return `FindOutput(success=False, …)` (same style as today’s broad `except Exception` path — do not leak raw trace to success paths).
  - **`search_v2`:** effective kind is always **symbol** for applicability. Fail before iterating hits when filter is invalid.
  - **`neighbors_v2`:** wrap **only** filter coerce + `model_validate` (+ any upfront filter-only logic) so `ValidationError` from bad filter keys returns `NeighborsOutput(success=False, message=…)`. **Do not** wrap `@validate_call` failures for missing `direction`, bad `edge_types`, wrong literals — those must still `raise ValidationError`. In the neighbor loop, on first `other_kind` where populated fields include any not applicable to that kind, return `NeighborsOutput(success=False, message=…)` and stop (no silent skip).
- **`server.py`:** Update `_INSTRUCTIONS` and the `Field(description=…)` for `search` / `find` / `neighbors` `filter` parameters — remove “irrelevant keys ignored per kind”; state unknown keys and cross-kind populated fields fail with `success=False` and `message`.
- **`README.md`:** Minimal update under §4 **`NodeFilter` notes:** consistent with `server.py` (one short bullet on strict schema + kind applicability; keep JSON string fallback note).

## Out of scope (do NOT touch)

- `describe_v2`, graph builder, `java_ontology.py`, Kuzu schema, Lance indexer, `search.query` / ranking, new MCP tools, ontology bump, `JAVA_CODEBASE_RAG_RUN_HEAVY` paths.
- Any fixture Java under `tests/bank-chat-system/` (fixture data only).
- Structured `hints` on tool outputs (#120).
- Drive-by refactors outside the files in **Scope**.

## Deliverables

1. `NodeFilter` rejects unknown top-level keys; no silent drop.
2. `find_v2`, `search_v2`, `neighbors_v2` return `success=False` with teaching `message` when populated fields are not applicable to the effective / neighbor kind (per plan).
3. `neighbors_v2` filter `ValidationError` → `success=False`; `validate_call` argument errors still raise `ValidationError`.
4. `exclude_roles: []` does not count as populated for applicability.
5. `_coerce_filter` JSON-string path unchanged; regressions for JSON string filters stay green.
6. Replace **`test_find_silent_ignore_irrelevant_filter_keys`** with a test that expects **`success=False`** for cross-kind populated fields (name per plan, e.g. `test_find_cross_kind_filter_fields_return_failure`).
7. Add the remaining tests named in the plan § “Tests for PR-N1” (unknown keys find/search/neighbors; cross-kind find/search/neighbors; `test_neighbors_validate_call_still_raises`).
8. `README.md` + `server.py` public strings match the new contract.

## Tests to run (iteration loop)

Run only these files during local iteration; **full** `pytest tests` (with `JAVA_CODEBASE_RAG_RUN_HEAVY` unset or `0`) is the merge gate once CI runs on the PR and on `master`.

- `tests/test_mcp_v2.py` — exercises `find_v2` / `search_v2` / `neighbors_v2`, filter coercion, and graph-backed neighbor paths for applicability.

## Tests

Run:

```bash
cd /path/to/java-enterprise-codebase-rag
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_v2.py -v
.venv/bin/python -m pytest tests -v
```

Expected: ruff clean; `tests/test_mcp_v2.py` all pass; full `tests` green with only documented skips (no new skips). New or renamed tests should cover at minimum the nine scenarios listed in the plan § “Tests for PR-N1” items 1–9 plus listed regressions.

## Sentinel checks

Run from repo root on `git diff master..HEAD` (or inspect working tree before commit):

```bash
git diff master --name-only
```

Expected: only `mcp_v2.py`, `server.py`, `README.md`, `tests/test_mcp_v2.py` (and this prompts file **only if** you were asked to update tracking — otherwise do **not** commit prompts).

```bash
rg "irrelevant keys ignored" .
```

Expected: **zero** matches after your `server.py` edit.

```bash
rg "test_find_silent_ignore" tests/test_mcp_v2.py || true
```

Expected: **zero** — old test name removed or renamed per plan.

```bash
rg "extra.?=.?[\"']forbid[\"']|extra=\"forbid\"|ConfigDict\\(extra=.forbid" mcp_v2.py
```

Expected: at least one match on `NodeFilter`.

## Manual evidence

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_v2.py -v
.venv/bin/python -m pytest tests -v
```

Optional smoke (direct Python): call `find_v2("symbol", {"typo_key": 1}, graph=...)` and assert `success is False` (exact message text is flexible but must be agent-readable).

## Definition of Done

- [ ] Plan PR-N1 file-by-file checklist satisfied (`mcp_v2.py`, `server.py`, `README.md`, `tests/test_mcp_v2.py`).
- [ ] Sentinel greps above pass.
- [ ] PR title: **`enforce lossless NodeFilter (forbid extras + kind applicability)`** (or equivalent imperative, lowercase-first-word style per repo convention).
- [ ] Branch: **`feat/lossless-nodefilter`** (or `cursor/lossless-nodefilter` if you prefer agent prefix — one PR, one branch).
- [ ] PR body references `plans/PLAN-LOSSLESS-PERMISSIVE-NODEFILTER.md` and `propose/LOSSLESS-PERMISSIVE-NODEFILTER-PROPOSE.md`; states **no ontology bump / no re-index**; lists test command and that full `tests` passed.
````
