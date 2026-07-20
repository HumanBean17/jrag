> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Agent task prompts — DESCRIBE-HINTS-STRUCTURAL

Status: **active**. Plan:
[`plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`](./PLAN-DESCRIBE-HINTS-STRUCTURAL.md). Propose:
[`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](../propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md).

**Depends on:** describe rollups, stored `OVERRIDES`, v4 find-success templates, `hints_structured` dual-output — all landed on `master`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-DESCRIBE-STRUCTURAL-1 — All 10 describe structural rows, helpers, tests

**Branch:** `feat/describe-hints-structural` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`](./PLAN-DESCRIBE-HINTS-STRUCTURAL.md) § PR-1.
**PR title:** `feat(hints): describe structural hints — IMPLEMENTS/INJECTS wiring + method road signs`

**Attach (`@-files`):**

- `@plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`
- `@propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`
- `@mcp_hints.py`
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-DESCRIBE-STRUCTURAL-1 from `plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`.

## Scope

1. **`mcp_hints.py`**
   - Add `_in_count(edge_summary, key) -> int` helper (symmetric to existing `_out_count` at ~line 248). Same defensive null/type checks. Reads `cell.get("in", 0)`.
   - Add `_type_rollup_would_emit(edge_summary) -> bool` helper. Returns `True` when any of `DECLARES.DECLARES_CLIENT`, `DECLARES.EXPOSES`, `DECLARES.DECLARES_PRODUCER` has `_out_count > 0`.
   - Add 7 new template constants after the existing `TPL_DESCRIBE_*` block (~line 125):
     ```python
     TPL_DESCRIBE_TYPE_IMPLEMENTORS = "implementors: neighbors(['{id}'],'in',['IMPLEMENTS'])"
     TPL_DESCRIBE_TYPE_IMPLEMENTS = "implements: neighbors(['{id}'],'out',['IMPLEMENTS'])"
     TPL_DESCRIBE_TYPE_DEPENDENCIES = "dependencies: neighbors(['{id}'],'out',['INJECTS'])"
     TPL_DESCRIBE_TYPE_INJECTORS = "injectors: neighbors(['{id}'],'in',['INJECTS'])"
     TPL_DESCRIBE_METHOD_OUTBOUND_CALLS = "outbound calls: neighbors(['{id}'],'out',['CALLS'])"
     TPL_DESCRIBE_METHOD_SUPER_DECL = "super declaration: neighbors(['{id}'],'out',['OVERRIDES'])"
     TPL_DESCRIBE_METHOD_UNRESOLVED = "unresolved: neighbors(['{id}'],'out',['CALLS'],include_unresolved=True)"
     ```
   - **Refactor `is_type` block (lines 1056–1081):** Remove the early `return` at line 1081. After the existing rollup hints, add tier-1 structural hints gated by `not _type_rollup_would_emit(edge_summary)`:
     - **Row A:** `decl_kind == "interface"` and `_in_count(edge_summary, "IMPLEMENTS") > 0`
     - **Row B:** `decl_kind == "class"` and `_out_count(edge_summary, "IMPLEMENTS") > 0`
     - **Row C:** `decl_kind == "class"` and `role == "SERVICE"` and `_out_count(edge_summary, "INJECTS") > 0`
     - **Row D:** `decl_kind in {"interface", "class"}` and `_in_count(edge_summary, "INJECTS") > 0`
     - Then `return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))`.
   - **Add I/J inside client/producer blocks (lines 1034–1047):** Before each existing `return`, add:
     - `kind == "client"`: if `_out_count(edge_summary, "HTTP_CALLS") > 0` → append row I (reuse `TPL_FIND_SUCCESS_HTTP_TARGETS` + structured)
     - `kind == "producer"`: if `_out_count(edge_summary, "ASYNC_CALLS") > 0` → append row J (reuse `TPL_FIND_SUCCESS_ASYNC_TARGETS` + structured)
   - **Add E/G/H inside `is_method` block (lines 1083–1138):** After existing leaf follow-ups (DECLARES_CLIENT, DECLARES_PRODUCER, EXPOSES) and before the `CALLS >= 10` meta row:
     - **Row E:** `1 <= CALLS.out <= 9` AND (`role != "OTHER"` OR `CALLS.out >= 3`)
     - **Row G:** `OVERRIDES.out > 0` AND no OVERRIDDEN_BY axis key has `out > 0` (inline gate: `not any(_out_count(edge_summary, k) > 0 for k in ["OVERRIDDEN_BY"] + [k for k in (edge_summary or {}) if k.startswith("OVERRIDDEN_BY.")])`)
     - **Row H:** `int(record.data.unresolved_call_sites_total or 0) > 0`
   - Every new row appends to BOTH `pairs` (string) and `struct_pairs` (structured) using the dual-list pattern. All use `PRIORITY_LEAF_FOLLOWUP`.

2. **`tests/test_mcp_hints.py`**
   - Implement every named test under **Tests for PR-1** in `plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md` (21 tests total: 12 string + 9 structured).
   - Add Cypher-query helper functions for each new test that needs a specific node from the fixture.
   - Use `pytest.skip()` in helpers when the fixture lacks a matching node.
   - Relax `test_hints_describe_client_always_declaring_method`: `assert out.hints == [want]` → `assert want in out.hints`.
   - Relax `test_hints_describe_producer_always_declaring_method`: same.
   - Relax structured counterparts similarly.
   - Add 7 new `(template, kwargs)` entries to the `test_hints_all_v4_templates_under_120_chars` parametrize list.

3. **`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`**
   - Move to `propose/completed/` in this PR (implementation PR = propose completion).

## Out of scope (do NOT touch)

- `build_ast_graph.py`, `java_ontology.py`, `ONTOLOGY_VERSION`, `EDGE_SCHEMA`.
- `mcp_v2.py`, `server.py`, output model changes.
- `hints: list[str]` field removal or deprecation.
- Tier 3 rows (F/K/L) — tracked in #191.
- `EXTENDS` describe row.
- `docs/AGENT-GUIDE.md`, `README.md` doc updates.
- Any graph re-query or `neighbors` tool changes.

## Deliverables

1. 10 new hint rows (A–E, G–J) with string + structured parity in `mcp_hints.py`.
2. `_in_count` and `_type_rollup_would_emit` helpers.
3. `is_type` block refactored from early-return to fallthrough with suppression gate.
4. 21 named tests passing.
5. Existing test regressions relaxed and passing.
6. Char-cap parametrization updated.
7. Proposal moved to `propose/completed/`.

## Tests to run

After each implementation step:

```bash
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k describe
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION`
- `build_ast_graph.py`
- `mcp_v2.py`
- `java_ontology.py`
- `server.py`

## Definition of done

- [ ] PR-1 checklist in `plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md` satisfied.
- [ ] All 21 named tests pass.
- [ ] Full test suite green; ruff clean.
- [ ] Proposal moved to `propose/completed/`.
- [ ] PR body: scope, plan + propose links, **no ontology bump**, **no re-index**.
````
