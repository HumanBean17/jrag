# Cursor task prompts — HINTS-V3

Status: **completed** (landed [#160](https://github.com/HumanBean17/java-codebase-rag/pull/160)). Plan:
[`plans/completed/PLAN-HINTS-V3.md`](./PLAN-HINTS-V3.md). Propose:
[`propose/completed/HINTS-V3-PROPOSE.md`](../propose/completed/HINTS-V3-PROPOSE.md).

**Depends on:** SCHEMA-V2 **PR-A, PR-B, PR-C** merged to `master`.
**Propose lock:** Set `HINTS-V3-PROPOSE.md` `Status: locked` before opening the code PR.

One prompt: **PR-D** (= SCHEMA-V2 PR-D in sequence doc).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-HINTS-V3-D — EDGE_SCHEMA-driven empty `neighbors` hints

**Branch:** `feat/hints-v3-neighbors-empty` off `master` **after PR-SCHEMA-V2-C merged**.
**Base:** `master` at merge commit of PR-C.
**Plan section:** [`plans/completed/PLAN-HINTS-V3.md`](./PLAN-HINTS-V3.md) § PR-D.
**PR title:** `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`

**Attach (`@-files`):**

- `@plans/completed/PLAN-HINTS-V3.md`
- `@propose/completed/HINTS-V3-PROPOSE.md` (§3–§4, §6, Decisions §7)
- `@propose/completed/SCHEMA-V2-PROPOSE.md` (§3.12 preview — read only)
- `@java_ontology.py` (`EDGE_SCHEMA`, `FUZZY_STRATEGY_SET`)
- `@mcp_hints.py`
- `@mcp_v2.py` (`neighbors_v2`, `_load_node_record`)
- `@propose/completed/HINTS-V2-PROPOSE.md` (fuzzy hint — unchanged)
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (priority cap context)
- `@README.md`
- `@server.py` (optional neighbors description)
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-HINTS-V3-D from `plans/completed/PLAN-HINTS-V3.md` (**PR-D**).

SCHEMA-V2 PR-A/B/C are on `master`: post-flip `HTTP_CALLS` (Client→Route), `ASYNC_CALLS` (Producer→Route), `EDGE_SCHEMA` with 11 edges.

Confirm `propose/HINTS-V3-PROPOSE.md` is **Status: locked** before merge.

## Scope

1. **`mcp_hints.py`**
   - Delete `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.
   - Add four templates from propose §3.1 (verbatim strings).
   - Implement `neighbors_empty_hints(...)` and `typical_traversal_for(...)` per propose §3.2–3.3.
   - Import `EDGE_SCHEMA` from `java_ontology` — no edge-shape literals in this file (except tests).
   - Wire `generate_hints("neighbors", …)`: empty `results` + non-empty `requested_edge_types` → structural hints; non-empty → keep v2 fuzzy path only.
   - Post-filter: no dot-key edge labels in rendered hints.
2. **`mcp_v2.py`** — Extend neighbors hint payload: `requested_direction`, `origin_id`, `subject_record` from `_load_node_record` (§3.6). Multi-id: use first origin only.
3. **`java_ontology.py`** — Only if `member_only` missing from PR-A: add field + flags per propose §3.4.
4. **`README.md`** / **`server.py`** — Minimal neighbors-hints documentation.
5. **Tests** — Implement every `test_hints_hv*` name listed under **Tests for PR-D** in `plans/completed/PLAN-HINTS-V3.md`, including **`test_hints_neighbors_v2_empty_post_flip_method_http_calls`** (required — session graph must be post-flip). Update/remove `test_hints_neighbors_empty_with_edge_types_emits_kind_check` to reflect v3 (rename if needed per plan).

## Out of scope (do NOT touch)

- `build_ast_graph.py`, graph DDL, pass5/6, `ONTOLOGY_VERSION`.
- `EDGE_SCHEMA` endpoint changes (already flipped in SCHEMA PRs).
- `RouteCaller`, `find_route_callers`, producer find beyond what tests need.
- v1 `describe`/`find`/`resolve` catalog rows (except neighbors empty branch).
- New MCP tool parameters.
- Per-row neighbors hints.

## Deliverables

1. Wrong-kind / wrong-direction / type-level empty queries emit schema-driven hints (HV table).
2. Brownfield-resolver absence hint on empty results when `brownfield_resolver_sourced` (HV4, HV13, HV14).
3. v2 fuzzy hint still fires on non-empty fuzzy edges (HV16).
4. HV19 coverage test for all `EDGE_SCHEMA` edges.
5. `TPL_NEIGHBORS_EMPTY_KIND_CHECK` fully removed.

## Tests to run

```bash
.venv/bin/ruff check mcp_hints.py mcp_v2.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "hints_hv or neighbors"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION` changes
- `CREATE NODE TABLE Producer` / `HTTP_CALLS(FROM Symbol` in `build_ast_graph.py`
- `CallerInfo` reintroduction
- `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (must be deleted, not kept)

## Manual evidence (optional)

On a graph built after SCHEMA PR-C:

```bash
.venv/bin/python -c "
# Document one neighbors_v2 empty call returning WRONG_SUBJECT_KIND for method+HTTP_CALLS — paste JSON hints in PR body.
"
```

## Definition of Done

- [ ] PR-D plan definition of done satisfied.
- [ ] HINTS-V3 propose **locked**.
- [ ] PR title: `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`
- [ ] PR body: scope, plan + propose links, test commands, **no re-index** (query-time only).
````
