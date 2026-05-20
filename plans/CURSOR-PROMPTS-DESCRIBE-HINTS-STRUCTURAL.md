# Cursor task prompts — DESCRIBE-HINTS-STRUCTURAL

Status: **active**. Plan:
[`plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`](./PLAN-DESCRIBE-HINTS-STRUCTURAL.md). Propose:
[`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](../propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md).

**Depends on:** v1 describe rollups + v4 find HTTP/async strings on `master`; propose PR [#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192) merged before implementation PR.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-DHS1 — describe structural + method-body hints (A–J)

**Branch:** `feat/describe-hints-structural` off `master`.
**Base:** `master` (with #192 merged).
**Plan section:** [`plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`](./PLAN-DESCRIBE-HINTS-STRUCTURAL.md) § PR-DHS1.
**PR title:** `feat(hints): describe structural IMPLEMENTS/INJECTS and method-body road signs`

**Attach (`@-files`):**

- `@plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`
- `@propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (priority cap §7.12)
- `@propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md` (reuse F1 HTTP/async strings)
- `@mcp_hints.py`
- `@mcp_v2.py` (`describe_v2` payload — read only)
- `@tests/test_mcp_hints.py`
- `@docs/AGENT-GUIDE.md` (optional)

**Prompt:**

````
You are implementing PR-DHS1 from `plans/PLAN-DESCRIBE-HINTS-STRUCTURAL.md`.

Confirm `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md` is merged via #192 before you merge implementation.

## Scope

1. **`mcp_hints.py`**
   - Add `_in_count`, `_type_rollup_would_emit`, `_override_axis_would_emit`, `_symbol_role`.
   - Add seven `TPL_DESCRIBE_*` templates (A,B,C,D,E,G,H) verbatim from the plan table.
   - **Refactor `generate_hints("describe")`:** remove early returns that prevent stacking on `client`, `producer`, and type Symbols (~689–719). Append tier-1 A–D when `not _type_rollup_would_emit`; append I/J on client/producer when `HTTP_CALLS.out` / `ASYNC_CALLS.out` > 0 using existing `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS`.
   - Method branch: append E/G/H per propose gates; keep existing rollups, override-axis, integration, and `CALLS >= 10` rows.
   - All new rows at `PRIORITY_LEAF_FOLLOWUP` (2); drop rendered strings > 120 chars.
   - Module docstring: reference DESCRIBE-HINTS-STRUCTURAL propose.
   - Optional: one sentence in `MCP_HINTS_FIELD_DESCRIPTION` for structural IMPLEMENTS/INJECTS describe hints.

2. **`tests/test_mcp_hints.py`**
   - Implement every test name under **Tests for PR-DHS1** in the plan (verbatim names).
   - Add kuzu helpers for stable bank nodes (`ChatAssignmentPort`, `RegexComplianceScanner`, `DistributionService` or Cypher equivalents).
   - Relax `test_hints_describe_client_always_declaring_method` and `test_hints_describe_producer_always_declaring_method` to `want in out.hints`.
   - Extend `test_hints_template_rendered_length_leq_120` with new template tuples.
   - `test_hints_describe_type_skips_tier1_when_rollups`: assert no tier-1 **substrings** on controller with rollups (do not assert row A absent on a class).

3. **Docs (optional)**
   - `docs/AGENT-GUIDE.md`: describe workflow note for interface → IMPLEMENTS.in → hint A; link #191 for tier 3.
   - `README.md`: one line under hints/describe if a bullet already exists.

4. **Propose lifecycle**
   - Move `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md` → `propose/completed/` in this PR.

## Out of scope (do NOT touch)

Sentinel — must be **zero** matches on `git diff master..HEAD`:

- `build_ast_graph.py`
- `java_ontology.py`
- `ONTOLOGY_VERSION`
- `EDGE_SCHEMA`
- `server.py`
- `kuzu_queries.py`
- `mcp_v2.py` (unless describe payload field genuinely missing)
- `TPL_NEIGHBORS_` success-path changes
- `find_success_hints` / `neighbors_success_hints` behavior changes
- Tier 3 templates F/K/L (`CALLS.in` callers, `DECLARES.out` members, `EXTENDS`)

Also out of scope:

- Ontology bump or re-index callouts beyond “not required”
- Widening **C** beyond `role == "SERVICE"`
- New dot-key describe emissions

## Deliverables

1. Describe emits A–J when gated; tier-1 suppressed when type rollups would emit.
2. Client/producer may stack declaring + HTTP/async hints.
3. All named tests pass; char-cap parametrize extended.

## Validation (run and paste in PR)

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k describe
.venv/bin/python -m pytest tests -v
```

Manual spot-check (optional evidence):

```bash
.venv/bin/python -c "
from tests.conftest import *  # noqa — use session graph in pytest instead if easier
"
# Prefer: pytest -k 'interface_implementors or client_http_targets' -v
```

## Definition of done

- [ ] Plan PR-DHS1 checklist complete
- [ ] Propose moved to `propose/completed/`
- [ ] No sentinel files in diff
- [ ] PR description: scope, validation commands, note SERVICE gate for C and tier 3 deferred to #191

````
