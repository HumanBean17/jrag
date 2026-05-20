# Cursor task prompts — OVERRIDDEN-BY-DOT-KEY-TRAVERSAL

Status: **active**. Plan:
[`plans/PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md`](./PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md). Propose:
[`propose/OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE.md`](../propose/OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE.md).

**Depends on:** landed `DECLARES.*` dot-key traversal ([#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)); stored `[:OVERRIDES]` edges.

One prompt: **PR-1** (single implementation PR).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-OVERRIDDEN-BY-1 — `OVERRIDDEN_BY.*` dot-key traversal

**Branch:** `feat/overridden-by-dot-key-traversal` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md`](./PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md) § PR-1.
**PR title:** `feat(neighbors): navigate OVERRIDDEN_BY.* composed edge types in one call`

**Attach (`@-files`):**

- `@plans/PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md`
- `@propose/OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE.md`
- `@kuzu_queries.py` (`override_axis_rollup_for`, `member_edge_traversal_for` — mirror for override traversal)
- `@mcp_v2.py` (`neighbors_v2`, `ComposedEdgeType`, `NodeRecord.edge_summary`)
- `@mcp_hints.py` (`TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS`, `MCP_HINTS_FIELD_DESCRIPTION`)
- `@server.py` (`neighbors` / `describe` tool descriptions)
- `@docs/AGENT-GUIDE.md`
- `@docs/EDGE-NAVIGATION.md`
- `@README.md` (MCP tool table only)
- `@tests/test_mcp_v2_compose.py`
- `@tests/test_mcp_hints.py`
- `@tests/fixtures/override_axis_rollup_smoke/` (fixture corpus)

**Prompt:**

````
You are implementing PR-OVERRIDDEN-BY-1 from `plans/PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md`.

## Scope

1. **`kuzu_queries.py`** — Add `_OVERRIDE_AXIS_COMPOSED_REL_MAP` and `override_axis_traversal_for` per propose (stored `[:OVERRIDES]` dispatch hop; base key returns overrider method ids without `via_id`; composed keys return terminal rows with full attr projection + `via_id`). Export override composed key allowlist for `mcp_v2`. Do **not** change `override_axis_rollup_for`.

2. **`mcp_v2.py`**
   - Extend `ComposedEdgeType` with four `OVERRIDDEN_BY*` literals.
   - Partition composed keys into member (`DECLARES.*`) vs override (`OVERRIDDEN_BY.*`) registries.
   - Refactor `neighbors_v2`: axis-specific origin gates; **fail-fast** when mixed families cannot both apply to the same origin; `direction="out"` for any composed key.
   - Override traversal: `override_axis_traversal_for`; `edge_type` = dot-key; composed `attrs` include `via_id`.
   - Update `NodeRecord.edge_summary` description (both composed families navigable with correct origin constraints).

3. **`server.py`** — Update `describe` / `neighbors` tool descriptions for override-axis dot-keys.

4. **`mcp_hints.py`** — Rewrite `TPL_DESCRIBE_METHOD_OVERRIDERS`, `TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS`, `TPL_DESCRIBE_METHOD_PRODUCERS_IN_OVERRIDERS`, `TPL_DESCRIBE_METHOD_ROUTES_IN_OVERRIDERS` to single-call `neighbors(..., ['OVERRIDDEN_BY.*'])`. Update `MCP_HINTS_FIELD_DESCRIPTION`. **Do not** remove `_filter_neighbors_dotkey_hints`.

5. **Docs** — `docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, minimal `README.md` neighbors row; document `OVERRIDDEN_BY` `out` vs stored `OVERRIDES` `in` equivalence.

6. **Tests** — Implement every test name under **Tests for PR-1** in the plan:
   - Replace `test_neighbors_still_rejects_overridden_by` with `test_neighbors_accepts_overridden_by_dot_keys`.
   - Merge-blocking: `test_neighbors_overridden_by_rollup_traversal_parity_blocking` (all four keys, bank-chat `ChatAssignmentPort.requestAssignment` + `override_axis_rollup_smoke`).
   - Update `test_describe_interface_method_with_annotated_impl_emits_rollup` (no `ValidationError` on `OVERRIDDEN_BY`).
   - Add `test_hints_describe_method_overridden_by_declares_client_emits_dot_key`; update existing describe-method overrider hint tests.
   - Grep regression: no `then neighbors(overrider_ids` in the four `TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS` templates.

## Out of scope (do NOT touch)

- `build_ast_graph.py`, `java_ontology.py` `ONTOLOGY_VERSION` / ontology bump, graph DDL, enrichment passes
- `override_axis_rollup_for` counting logic changes
- Inbound composed `direction="in"` for override-axis keys
- Per-method `NodeRef` signals ([#167](https://github.com/HumanBean17/java-codebase-rag/issues/167))
- `search` / `find` result shape changes
- Moving propose to `completed/` (reviewer may do on merge)
- Edits under `tests/bank-chat-system/` fixture sources

## Deliverables

1. `neighbors(['<interface_method_id>'], 'out', ['OVERRIDDEN_BY.DECLARES_CLIENT'])` returns Client `NodeRef`s with `via_id` = overrider method id.
2. **Parity:** unfiltered `len(results)` == `describe(...).record.edge_summary[key]["out"]` for each override-axis key (merge-blocking).
3. `DECLARES.*` behavior unchanged; mixed-family wrong-origin returns `success=False` for the whole request.
4. Docs + hints aligned with “what you see in edge_summary is what you can request” for `OVERRIDDEN_BY*`.

## Tests to run

```bash
.venv/bin/ruff check kuzu_queries.py mcp_v2.py mcp_hints.py server.py tests/test_mcp_v2_compose.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_v2_compose.py tests/test_mcp_hints.py -v -k "overridden_by or override_axis or hints_describe_method or parity_blocking or mixed_composed"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION` assignment changes in `java_ontology.py`
- `ontology_version` bump in `build_ast_graph.py`
- New graph pass files or `pass7` references
- `then neighbors(overrider_ids` in `mcp_hints.py` `TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS` templates
- Edits under `tests/bank-chat-system/` fixture sources

## Definition of done

- All plan PR-1 checklist items checked.
- Parity tests green **before** merge (not deferred).
- PR description: scope, link to propose #165, manual evidence (one `describe` + `neighbors` dot-key on `ChatAssignmentPort.requestAssignment`), note stored-`OVERRIDES` dispatch hop (not signature Cypher in read path).

## Manual evidence (paste in PR)

```bash
KUZU=/tmp/ob-dotkey-evidence/code_graph.kuzu
rm -rf /tmp/ob-dotkey-evidence
.venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path "$KUZU" --verbose
# Resolve ChatAssignmentPort.requestAssignment method id (Cypher or test helper), then:
# describe(mid) → note OVERRIDDEN_BY.DECLARES_CLIENT out count
# neighbors_v2(mid, direction='out', edge_types=['OVERRIDDEN_BY.DECLARES_CLIENT']) → len(results) matches
```

Compare counts to `describe.edge_summary` for all four override-axis keys on that method.

````
