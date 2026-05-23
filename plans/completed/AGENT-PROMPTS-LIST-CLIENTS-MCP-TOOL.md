# Agent task prompts — list_clients MCP tool (PR-LC1 → PR-LC3)

Status: **completed** (LC1–LC3 landed). Source plan:
`plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`.

Use these prompts in landing order: **LC1 -> LC2 -> LC3**.
If prompt text conflicts with plan text, the plan wins.

---

## PR-LC1 — Client schema + extraction + persistence

**Branch:** `feat/list-clients-lc1-client-schema` off `master`.
**Base:** `master`.
**Plan section:** `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md` § `PR-LC1 - Client schema + extraction + persistence`.
**Estimated diff size:** ~4-5 files, ~350-700 LOC.

**Attach (`@-files`):**
- `@plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`
- `@propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`
- `@README.md`
- `@build_ast_graph.py`
- `@ast_java.py`
- `@graph_enrich.py`
- `@java_ontology.py`
- `@tests/README.md`

**Prompt:**

````
You are implementing PR-LC1 from `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`.
Read the PR-LC1 section first. The plan is the source of truth.

## Scope
- Add `Client` node table and `DECLARES_CLIENT` edge (`Symbol -> Client`).
- Persist outbound declarations independently from `Route`.
- Include source/brownfield `@CodebaseClient` declarations and synthesized Feign method declarations.
- Add graph_meta client counters (totals + by-kind JSON-string map).
- Bump ontology version 9 -> 10.

## Out of scope (do NOT touch)
- pass6 hint-recovery retargeting (PR-LC2).
- MCP tool surface (`list_clients`) and DTO/tool registration (PR-LC3).
- Async producer node/tool expansion and companion client tools.
- Compatibility shims for old schema.

If you need to touch out-of-scope items, stop and ask.

## Deliverables
1. `build_ast_graph.py`: add Client/DECLARES_CLIENT schemas and create/drop wiring.
2. `build_ast_graph.py`: add row dataclasses + `GraphTables` collections and writers.
3. `build_ast_graph.py`: add client graph_meta counters using STRING JSON pattern.
4. `ast_java.py`: ensure outbound extraction emits stable Client payload.
5. `ast_java.py`: synthesize Feign method outbound client declarations.
6. `graph_enrich.py`: use `resolve_http_client_for_method` output as canonical source and stamp `source_layer`.
7. `java_ontology.py` (+ ontology source): version bump to 10 and valid client-kind alignment.
8. Add `tests/test_client_node_extraction.py` with plan-defined 6 tests.

## Tests
Run:
- `ruff check .`
- `pytest tests/test_client_node_extraction.py -v`
- `pytest tests -v`

Expected: targeted tests pass; full suite passes.

## Sentinel checks
- `rg "list_clients|ClientRowDto|ClientsListOutput|@mcp.tool\\(name=\"list_clients\"" server.py kuzu_queries.py`
  - must show no PR-LC3 surface additions.
- `rg "pass6|hint recovery|DECLARES_CLIENT.*pass6|Client.*pass6" build_ast_graph.py kuzu_queries.py`
  - must show no PR-LC2 retargeting work.

## Manual evidence
- Rebuild graph:
  - `python build_ast_graph.py --source-root tests/bank-chat-system --kuzu-path /tmp/check_lc1 --verbose`
- Verify:
  - ontology version is 10;
  - client counters exist in `graph_meta`;
  - `Client` and `DECLARES_CLIENT` rows are queryable.

## Definition of Done
- [ ] All deliverables shipped and test-covered.
- [ ] `ruff check .` passes.
- [ ] Targeted and full tests pass.
- [ ] No PR-LC2/PR-LC3 scope leakage.
- [ ] PR title: `feat: add Client node and DECLARES_CLIENT graph persistence (PR-LC1)`.
- [ ] Branch: `feat/list-clients-lc1-client-schema`.
````

---

## PR-LC2 — pass6 hint recovery migration to Client

**Branch:** `feat/list-clients-lc2-pass6-client-hints` off `feat/list-clients-lc1-client-schema` (or `master` if LC1 merged).
**Base:** PR-LC1 merged (or LC1 branch).
**Plan section:** `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md` § `PR-LC2 - pass6 hint recovery migration to Client`.
**Estimated diff size:** ~2-3 files, ~180-350 LOC.

**Attach (`@-files`):**
- `@plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`
- `@propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`
- `@README.md`
- `@build_ast_graph.py`
- `@kuzu_queries.py`
- `@tests/README.md`

**Prompt:**

````
You are implementing PR-LC2 from `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`.
Read the PR-LC2 section first. The plan is the source of truth.

## Scope
- Retarget pass6 hint recovery from caller `http_consumer` routes to caller `Client` declarations.
- Keep match outcomes and semantics unchanged.
- Preserve `HTTP_CALLS(Symbol -> Route)` meaning.
- Add focused regression tests for parity and continuity.

## Out of scope (do NOT touch)
- LC1 schema extraction/persistence redesign except minimal bug fixes.
- LC3 MCP tool/DTO/docs (`list_clients`).
- Route-tool redesign or companion tools.

If you need to touch out-of-scope items, stop and ask.

## Deliverables
1. `build_ast_graph.py`: pass6 hint lookup via caller member -> `DECLARES_CLIENT` -> `Client`.
2. `build_ast_graph.py`: keep outcome labels/logic stable.
3. `kuzu_queries.py`: helper query adjustments for hint lookup (if needed).
4. Add `tests/test_client_hint_recovery.py` with plan-defined 4 tests.
5. Ensure known Feign cross-service resolution and `find_route_callers` continuity still hold.

## Tests
Run:
- `ruff check .`
- `pytest tests/test_client_hint_recovery.py -v`
- `pytest tests -v`

Expected: targeted tests pass; full suite passes.

## Sentinel checks
- `rg "@mcp.tool\\(name=\"list_clients\"|ClientRowDto|ClientsListOutput" server.py kuzu_queries.py`
  - must show no LC3 MCP surface.
- `rg "CREATE NODE TABLE Client|CREATE REL TABLE DECLARES_CLIENT|ontology_version\\s*=\\s*10" build_ast_graph.py java_ontology.py`
  - no broad LC1 reshaping.

## Manual evidence
- Rebuild graph:
  - `python build_ast_graph.py --source-root tests/bank-chat-system --kuzu-path /tmp/check_lc2 --verbose`
- Capture:
  - pass6 still resolves known Feign call path correctly;
  - `find_route_callers` still returns expected caller;
  - missing-hint flow still falls back to unresolved/phantom behavior.

## Definition of Done
- [ ] All deliverables shipped and test-covered.
- [ ] `ruff check .` passes.
- [ ] Targeted and full tests pass.
- [ ] Match semantics unchanged except hint-source retarget.
- [ ] PR title: `feat: retarget pass6 hint recovery to Client declarations (PR-LC2)`.
- [ ] Branch: `feat/list-clients-lc2-pass6-client-hints`.
````

---

## PR-LC3 — list_clients MCP tool + query surface + docs

**Branch:** `feat/list-clients-lc3-mcp-surface` off `feat/list-clients-lc2-pass6-client-hints` (or latest merged predecessor).
**Base:** PR-LC2 merged (or LC2 branch).
**Plan section:** `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md` § `PR-LC3 - list_clients MCP tool + query surface + docs`.
**Estimated diff size:** ~3-4 files, ~220-450 LOC.

**Attach (`@-files`):**
- `@plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`
- `@propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`
- `@README.md`
- `@server.py`
- `@kuzu_queries.py`
- `@tests/test_mcp_tools.py`
- `@tests/README.md`

**Prompt:**

````
You are implementing PR-LC3 from `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`.
Read the PR-LC3 section first. The plan is the source of truth.

## Scope
- Add first-class MCP outbound discovery surface:
  - query helper in `kuzu_queries.py`;
  - `list_clients` MCP tool in `server.py`;
  - DTOs/output contract;
  - docs in `README.md`.
- Filter semantics parallel to route-listing ergonomics:
  `microservice`, `client_kind`, `target_service`, `path_prefix`, `method`, `limit`.
- Empty-match behavior is success with empty list.

## Out of scope (do NOT touch)
- LC1 schema/extraction redesign (except critical bug fix).
- LC2 pass6 hint-recovery logic.
- Companion client/producer tools not in this plan.
- Route API contract redesign.

If you need to touch out-of-scope items, stop and ask.

## Deliverables
1. `kuzu_queries.py`: add client-list query helper with optional filters and deterministic ordering.
2. `server.py`: add `ClientRowDto` and `ClientsListOutput`.
3. `server.py`: register `@mcp.tool(name="list_clients")`.
4. `server.py`: enforce limit default/bounds (`100`, bounded `1..500`) and method normalization per repo conventions.
5. `README.md`: document `list_clients`, outbound/inbound split, and reindex/ontology callout consistency.
6. Add `tests/test_list_clients.py` with plan-defined 8 tests.
7. Update `tests/test_mcp_tools.py` (or equivalent) for tool registration smoke.

## Tests
Run:
- `ruff check .`
- `pytest tests/test_list_clients.py -v`
- `pytest tests/test_mcp_tools.py -v`
- `pytest tests -v`

Expected: new tool tests pass; full suite passes.

## Sentinel checks
- `rg "pass6|hint recovery|DECLARES_CLIENT.*pass6|http_consumer" build_ast_graph.py kuzu_queries.py`
  - no LC2 logic rewrite.
- `rg "get_client_by_path|find_client_callers|find_client_target_route|list_async_producers|Producer node" server.py kuzu_queries.py README.md`
  - no out-of-plan tool expansion.

## Manual evidence
- Build/rebuild graph if needed, then call `list_clients` with:
  - no filters;
  - each filter independently;
  - combined filters;
  - empty-result query;
  - limit edge cases (`0`, `1`, `500`, `501`).
- Capture stable ordering and success/empty response contract.

## Definition of Done
- [ ] All deliverables shipped and test-covered.
- [ ] `ruff check .` passes.
- [ ] Targeted and full tests pass.
- [ ] README public contract updated consistently.
- [ ] PR title: `feat: add list_clients MCP tool and client query surface (PR-LC3)`.
- [ ] Branch: `feat/list-clients-lc3-mcp-surface`.
````
