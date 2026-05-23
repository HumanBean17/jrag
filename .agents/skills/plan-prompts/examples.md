# Plan Prompts Examples

## Example status header

```markdown
# Cursor task prompts — <topic> (PR-X1 -> PR-X3)

Status: **active**. One prompt per PR; each prompt is self-contained.
```

## Example PR section skeleton

```markdown
## PR-X1 — schema + extraction

**Branch:** `feat/topic-x1` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-TOPIC.md` § PR-X1.

**Attach (`@-files`):**
- `@plans/PLAN-TOPIC.md`
- `@build_ast_graph.py`
- `@tests/test_topic.py`
```

## Example hard guardrail block

```markdown
## Out of scope (do NOT touch)
- Any MCP tool work (belongs to PR-X3).
- Any brownfield override logic (belongs to PR-X2).
- Any ontology bump beyond what PR-X1 declares.

If you need to touch these areas, stop and ask.
```

## Example deliverables + iteration subset + tests

```markdown
## Deliverables
1. Add schema DDL for `Client` + relation table.
2. Wire create/drop lifecycle.
3. Add extraction tests for declared client rows.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge gate (CI on PR + `master`).

- `tests/test_client_node_extraction.py` — exercises new `Client` rows and extraction.
- `tests/test_ast_graph_build.py` — schema and graph build paths touched by DDL wiring.

## Tests
Run: `.venv/bin/python -m pytest tests/test_client_node_extraction.py tests/test_ast_graph_build.py -v`
Expected: all tests pass.

## Sentinel checks
- `rg "list_clients|find_client_callers" server.py` should return no new matches in PR-X1.
```
