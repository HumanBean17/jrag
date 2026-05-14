# Plan Snippets

Use these snippets as copy-ready scaffolding.

## 1) Status header

```markdown
Status: **active (planning)**. This plan implements
[`propose/TOPIC-PROPOSE.md`](../propose/TOPIC-PROPOSE.md)
as a multi-PR sequence. This file is plan-only and does not implement code.
```

## 2) PR overview table

```markdown
## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-X1 | schema + extraction | 9 -> 10 | graph DDL vs writer drift; extraction edge cases | extraction + schema | prerequisite only |
| PR-X2 | matcher integration | none | ambiguous matches; query-layer churn | regression + continuity | PR-X1 |
| PR-X3 | MCP tool + docs | none | tool contract vs docs drift; operator confusion | tool filters + docs | PR-X1 |

Landing order: **X1 -> X2 -> X3**.
```

The **Areas of concern** cells are **review hints** (risks, coupling), not a filename allowlist and not the authority on what may be edited — use the per-PR **File-by-file changes** section for that.

## 3) Per-PR section skeleton

```markdown
# PR-X1 - <title>

## File-by-file changes

### 1. `build_ast_graph.py`
- Add schema DDL for new node/edge table.
- Wire create/drop lifecycle.

### 2. `tests/test_topic.py`
- Add targeted tests for extraction semantics.

## Tests for PR-X1
1. `test_case_one`
2. `test_case_two`

## Definition of done (PR-X1)
- Schema persists and is queryable.
- New tests pass with full suite.
```

## 4) Risk table

```markdown
# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Extraction/matcher drift | high | lock with parity regression tests |
| 2 | Tool contract drift | medium | explicit filter tests and DTO checks |
```

## 5) Out-of-scope contract

```markdown
# Out of scope

- Companion tools not required for this rollout.
- Adjacent schema redesign not required by this proposal.
- Runtime integrations outside current static-analysis scope.
```
