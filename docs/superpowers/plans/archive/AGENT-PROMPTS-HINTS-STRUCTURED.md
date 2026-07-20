<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Agent task prompts — HINTS-STRUCTURED

Status: **active**. Plan:
[`plans/PLAN-HINTS-STRUCTURED.md`](./PLAN-HINTS-STRUCTURED.md). Propose:
[`propose/HINTS-STRUCTURED-PROPOSE.md`](../propose/HINTS-STRUCTURED-PROPOSE.md).

**Depends on:** v1–v4 hint catalogs (all landed) on `master`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-HINTS-STRUCTURED-1 — StructuredHint model, generation refactor, all tests

**Branch:** `feat/hints-structured` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-HINTS-STRUCTURED.md`](./PLAN-HINTS-STRUCTURED.md) § PR-1.
**PR title:** `feat(hints): add machine-parseable hints_structured field to all MCP outputs`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-STRUCTURED.md`
- `@propose/HINTS-STRUCTURED-PROPOSE.md`
- `@mcp_hints.py`
- `@mcp_v2.py`
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-HINTS-STRUCTURED-1 from `plans/PLAN-HINTS-STRUCTURED.md`.

## Scope

1. **`mcp_hints.py`**
   - Define an internal lightweight `_StructuredHint` representation (dataclass or NamedTuple with `tool`, `args`, `actionable`, `priority`). This must NOT import from `mcp_v2.py` (circular dependency).
   - Add `MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION` constant.
   - Change `generate_hints` return type to `tuple[list[str], list[_StructuredHint]]`.
   - Add structured-hint emission alongside EVERY existing string hint emission in all branches:
     - describe: type rollups (clients/routes/producers via members), method override axis, method leaf follow-ups, route/client/producer declaring, many CALLS (actionable=False)
     - find: empty→resolve, page full (actionable=False), success F1/F2/F3
     - resolve: none→search, none→find route, none→find client, many tighten (actionable=False)
     - neighbors: empty structural (actionable=False, build args from `EDGE_SCHEMA` data — NOT string parsing; e.g. wrong-kind → `{"ids": [subject_id], "direction": correct_dir, "edge_types": [canonical_edge]}`, type-level requery → `{"ids": [subject_id], "direction": direction, "edge_types": [dot_key_edge]}`), success N1a–N7, fuzzy strategy (actionable=False), CALLS fanout/meta (actionable=False)
     - search: weak score (actionable=False)
   - Add `finalize_structured_hints` — dedupe by `(tool, json.dumps(args, sort_keys=True))` (NOT `frozenset(args.items())` — breaks on nested dicts like `{"filter": {"path_prefix": …}}`), keep highest priority, cap to 5.
   - Batch-placeholder hints (N2–N7): populate `args.ids` from payload result ids when available → `actionable=True`; empty `[]` → `actionable=False`.
   - All `args` values must be JSON-serializable primitives. Use `list` not `tuple`. No `set`.

2. **`mcp_v2.py`**
   - Add Pydantic `StructuredHint` model: `tool: Literal["search","find","describe","neighbors","resolve"]`, `args: dict[str, Any]`, `actionable: bool = True`.
   - Add `hints_structured: list[StructuredHint] = Field(default_factory=list, description=MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION)` to SearchOutput, FindOutput, DescribeOutput, NeighborsOutput, ResolveOutput.
   - Import `MCP_HINTS_STRUCTURED_FIELD_DESCRIPTION` from `mcp_hints`.
   - Update all 5 `generate_hints` call sites to destructure the tuple:
     ```python
     str_hints, raw_struct = generate_hints("search", hint_payload)
     struct_hints = [StructuredHint(**h._asdict()) for h in raw_struct]  # or similar conversion
     ```
   - Update `resolve_v2`'s `model_copy(update=…)`. Before: `model_copy(update={"hints": generate_hints(...)}).` After:
     ```python
     str_hints, raw_struct = generate_hints("resolve", hint_payload)
     struct_hints = [StructuredHint(tool=h.tool, args=h.args, actionable=h.actionable) for h in raw_struct]
     out = out.model_copy(update={"hints": str_hints, "hints_structured": struct_hints})
     ```
   - Ensure all error-path returns that pass `hints=[]` also pass `hints_structured=[]` (or rely on `default_factory=list`).

3. **`tests/test_mcp_hints.py`**
   - Implement every named test under **Tests for PR-1** in `plans/PLAN-HINTS-STRUCTURED.md`.
   - Add `_assert_structured_hint` helper.
   - Add parity test: for every payload where `hints != []`, `len(hints_structured) <= len(hints)`.
   - Add round-trip test: build structured hint args into actual MCP call (`neighbors_v2` on `kuzu_graph`).

4. **`README.md`**
   - Add 1–2 sentences about `hints_structured` under the existing MCP tool reference `hints` paragraph (§4).

5. **`propose/HINTS-STRUCTURED-PROPOSE.md`**
   - Update status line from `Proposal — not yet implemented.` to `Proposal — locked.`.

## Out of scope (do NOT touch)

- `build_ast_graph.py`, `java_ontology.py`, `ONTOLOGY_VERSION`, `EDGE_SCHEMA`.
- `hints: list[str]` removal or deprecation.
- `_coerce_ids()` changes.
- Per-row structured hints.
- `hints_version` field.
- `MCP_HINTS_FIELD_DESCRIPTION` changes.
- Deriving string templates from structured hints.
- `args` validation against MCP tool parameter schemas.

## Deliverables

1. `hints_structured` field on all 5 outputs with correct Pydantic serialization.
2. `generate_hints` returns both string and structured hints from unified trigger logic.
3. All named tests pass; parity invariant holds; round-trip integration test passes.
4. README mentions `hints_structured`.
5. Propose locked.

## Tests to run

```bash
.venv/bin/ruff check mcp_hints.py mcp_v2.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "structured"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION`
- `build_ast_graph.py`
- `MCP_HINTS_FIELD_DESCRIPTION` (must not change)
- `hints: list[str]` field removal (must still exist)

## Definition of done

- [ ] PR-1 checklist in `plans/PLAN-HINTS-STRUCTURED.md` satisfied.
- [ ] Propose status updated to **locked** (happens when plan PR merges — plan approval = propose lock; do NOT lock in implementation PR).
- [ ] PR body: scope, plan + propose links, **no re-index**, **backward compatible**.
- [ ] Round-trip `neighbors_v2` integration test passes.
````
