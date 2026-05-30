# Agent task prompts — `trace` tool (PR-TRACE-1a → PR-TRACE-4)

Status: **completed**. Plan:
[`plans/completed/PLAN-TRACE-TOOL.md`](PLAN-TRACE-TOOL.md); propose:
[`propose/active/TRACE-TOOL-PROPOSE.md`](../../propose/active/TRACE-TOOL-PROPOSE.md).

One prompt per PR. Copy the prompt verbatim into Cursor agent mode with the
listed `@-files` attached.

**Workflow per PR:**

1. Branch off `experimental` (all TRACE PRs target `experimental`, not `master`).
2. Paste the prompt; let the agent implement.
3. Run validation commands from the prompt.
4. Commit; open PR against `experimental`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No ontology bump; no graph schema changes; no re-index.
- No `git push` from the agent.
- All PRs target `experimental` — never `master`.
- If ambiguous, stop and ask — do not expand scope.

---

## PR-TRACE-1a — Core BFS engine

**Branch:** `feat/trace-core-bfs` off `experimental`.
**Base:** `experimental`.
**Plan section:** `plans/completed/PLAN-TRACE-TOOL.md` § PR-TRACE-1a.
**PR title:** `add trace tool core BFS engine (PR-TRACE-1a)`

**Attach (`@-files`):**

- `@plans/completed/PLAN-TRACE-TOOL.md` (PR-TRACE-1a section only)
- `@propose/active/TRACE-TOOL-PROPOSE.md` (§ "Signature", § "Result format", § "Core algorithm")
- `@mcp_v2.py` (read-only — import types only: `NodeFilter`, `EdgeFilter`, `NodeRef`, `_node_ref_from_row`, `_node_kind_from_id`)
- `@kuzu_queries.py` (read-only — reuse `g._rows` query pattern; do not modify)
- `@java_ontology.py` (read-only — reference for valid edge types, roles)
- `@tests/conftest.py` (read-only — use `kuzu_graph` session fixture)
- `@tests/test_mcp_v2.py` (read-only — follow test structure patterns)

**Prompt:**

````
You are implementing PR-TRACE-1a from `plans/completed/PLAN-TRACE-TOOL.md`.

Read the **PR-TRACE-1a** section and the propose § "Signature", "Result format", and
"Core algorithm" sections before writing code. If this prompt and the plan disagree,
the plan wins.

## Scope

Create `mcp_trace.py` (new file) with the core BFS traversal engine:

1. **Models** — `TraceEdge`, `TracePath`, `TraceStats`, `TraceOutput` as
   `pydantic.BaseModel` with `extra="forbid"`. Follow the propose § "Result format"
   field definitions exactly.
2. **`neighbors_batched` helper** — issues a single Cypher query per BFS hop for all
   frontier node IDs. Uses `g._rows` from `KuzuGraph` (do not modify `kuzu_queries.py`).
   Edge type expansion uses the same OR-of-scalar-equalities pattern as `neighbors_v2`
   in `mcp_v2.py`:
   ```python
   label_params = [f"l{i}" for i in range(len(flat_labels))]
   label_predicate = "(" + " OR ".join(f"label(e) = ${name}" for name in label_params) + ")"
   ```
3. **BFS engine** (`trace_v2` function) with:
   - `visited` set preventing cycle revisits
   - `edge_id_map` for `parent_edge_id` lookup
   - `total_nodes_discovered` tracking with `max_nodes_discovered` budget early-stop
   - Edge recording with `TraceEdge` (from_id, to_id, edge_type, hop, parent_edge_id, attrs)
   - `NodeFilter` hard gate (failing nodes excluded entirely from nodes dict and edges)
   - `EdgeFilter` pushdown (min_confidence, strategies, callee_declaring_role)
   - `include_unresolved` support for UnresolvedCallSite edges
4. **Path enumeration** — enumerate root-to-leaf paths through the DAG. Stop
   enumeration after 10 × `max_paths` candidates. Rank by: leaf role priority
   (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER) → min path confidence →
   path length (shorter first). Cap at `max_paths`.
5. **Input validation** — direction required, edge_types required and non-empty,
   max_depth clamped 1..5, max_nodes_discovered clamped 100..2000, unknown edge
   types return `success=False` with teaching message.
6. **Import contract** — import only stable types from `mcp_v2.py`:
   `NodeFilter`, `EdgeFilter`, `NodeRef`, `_node_ref_from_row`, `_node_kind_from_id`.
   Never import handler functions. Never modify `mcp_v2.py`.

Create `tests/test_mcp_trace.py` (new file) with all 23 tests named in the plan. Test #17 is `test_trace_prune_roles_param_accepted_noop` (confirms `prune_roles=[]` is accepted and produces an unpruned result; the full soft-gate vs hard-gate comparison test lands in PR-TRACE-1b as `test_trace_filter_vs_prune_roles`).

## Out of scope (do NOT touch)

- `mcp_v2.py` — no modifications (import types only).
- `kuzu_queries.py` — no modifications (use `g._rows` as-is).
- `server.py` — no tool registration yet (PR-TRACE-2).
- `build_ast_graph.py`, `java_index_flow_lancedb.py`, `java_ontology.py` — no changes.
- Pruning features (`prune_roles`, `fan_out_cap`, `collapse_trivial`, cross-service
  boundary) — these land in PR-TRACE-1b. Accept the parameters in the signature but
  treat `prune_roles=[]`, `fan_out_cap` as no-op (no pruning applied), `collapse_trivial`
  as ignored, and do not follow scaffolding edges for cross-service.
- `mcp_hints.py` — no changes (PR-TRACE-3).
- `skills/explore-codebase/SKILL.md` — no changes (PR-TRACE-3).
- Any files under `docs/` or `README.md` (PR-TRACE-4).

If you need to touch any of these, stop and ask.

## Deliverables

1. `mcp_trace.py` with `TraceEdge`, `TracePath`, `TraceStats`, `TraceOutput` models.
2. `neighbors_batched` helper function in `mcp_trace.py`.
3. `trace_v2` public handler function with full BFS engine.
4. `tests/test_mcp_trace.py` with all 23 named tests from the plan.
5. All tests pass; ruff clean; no regression on existing test suite.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge gate (CI on PR + `master`).

- `tests/test_mcp_trace.py` — exercises all new `trace_v2` code paths (BFS, budget, paths, validation, visited set, filters).

## Tests

Run:
```bash
.venv/bin/ruff check mcp_trace.py tests/test_mcp_trace.py
.venv/bin/python -m pytest tests/test_mcp_trace.py -v
.venv/bin/python -m pytest tests -v
```

Expected: all pass; no skips on `test_mcp_trace.py` tests; existing suite unchanged.

## Sentinel checks

Verify these return zero for files you created:
```bash
rg "from mcp_v2 import.*neighbors_v2|from mcp_v2 import.*search_v2|from mcp_v2 import.*describe_v2" mcp_trace.py
rg "import server" mcp_trace.py
rg "import build_ast_graph" mcp_trace.py
```

Verify `mcp_trace.py` does NOT exist before you create it:
```bash
ls mcp_trace.py 2>&1
```
Expected: file not found before implementation; exists after.

## Manual evidence

After implementation, spot-check BFS manually:
```bash
.venv/bin/python -c "
from kuzu_queries import KuzuGraph
import mcp_trace
g = KuzuGraph()  # uses default bank-chat path from conftest convention
out = mcp_trace.trace_v2(
    ids='sym:ChatManagementService#getAllChats',
    direction='out',
    edge_types=['CALLS'],
    max_depth=2,
    graph=g,
)
print('success:', out.success)
print('edges:', len(out.edges))
print('paths:', len(out.paths))
print('stats:', out.stats)
for e in out.edges[:5]:
    print(f'  hop={e.hop} {e.from_id} -[{e.edge_type}]-> {e.to_id}')
"
```

## Definition of Done

- [ ] `mcp_trace.py` exists with all four models and `trace_v2` handler
- [ ] `neighbors_batched` issues single Cypher query per hop
- [ ] BFS visited set prevents cycles; budget stops early; paths capped and ranked
- [ ] All 23 named tests pass in `tests/test_mcp_trace.py`
- [ ] `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests -v` green (no regression)
- [ ] No modifications to `mcp_v2.py`, `kuzu_queries.py`, `server.py`, `build_ast_graph.py`
- [ ] PR title: `add trace tool core BFS engine (PR-TRACE-1a)`
- [ ] Branch: `feat/trace-core-bfs` off `experimental`
````

---

## PR-TRACE-1b — Pruning, collapsing, and cross-service

**Branch:** `feat/trace-pruning` off `experimental` (after PR-TRACE-1a merged).
**Base:** `experimental` (with PR-TRACE-1a merged).
**Blocked on:** PR-TRACE-1a merged to `experimental`.
**Plan section:** `plans/completed/PLAN-TRACE-TOOL.md` § PR-TRACE-1b.
**PR title:** `add trace pruning collapsing cross-service (PR-TRACE-1b)`

**Attach (`@-files`):**

- `@plans/completed/PLAN-TRACE-TOOL.md` (PR-TRACE-1b section only)
- `@propose/active/TRACE-TOOL-PROPOSE.md` (§ "Server-side pruning", § "Cross-service traversal")
- `@mcp_trace.py` (from PR-TRACE-1a — this is the file you are extending)
- `@mcp_v2.py` (read-only — reference for `NodeFilter`, `EdgeFilter` semantics)
- `@tests/conftest.py` (read-only — use existing fixtures)
- `@tests/test_mcp_trace.py` (from PR-TRACE-1a — extend with new tests)

**Prompt:**

````
You are implementing PR-TRACE-1b from `plans/completed/PLAN-TRACE-TOOL.md`.

PR-TRACE-1a (core BFS engine) is already merged to `experimental`. This PR extends
`mcp_trace.py` with pruning, collapsing, and cross-service features. Read the
PR-TRACE-1b section and propose § "Server-side pruning" and "Cross-service traversal"
before writing code.

## Scope

Extend `mcp_trace.py` with four features:

1. **Role-based pruning** (`prune_roles`): soft gate in BFS loop. When a discovered
   node's role is in `prune_roles`, record the edge in the result but do NOT add the
   node to the next frontier. BFS does not continue through pruned nodes. Increment
   `stats.nodes_pruned_role`.

2. **Fan-out throttling** (`fan_out_cap`): per-node cap on candidate edges. When a
   node has more than `fan_out_cap` candidate edges (after NodeFilter/EdgeFilter),
   keep only the top-K sorted by:
   - Primary: edge confidence (highest first). For edges without confidence, use 0.0.
   - Tiebreaker: role priority (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER).
     Use the *callee* node's role for ranking.
   - For edges with equal confidence and equal role: alphabetically by callee FQN
     (deterministic).
   - **Scaffolding edges** (`DECLARES_CLIENT`, `DECLARES_PRODUCER`) are EXEMPT from
     the cap — they are traversal infrastructure, not signal.
   Increment `stats.nodes_pruned_fan_out`.

3. **Trivial chain collapsing** (`collapse_trivial`): post-BFS pass. Identify chains
   where intermediate node B has exactly 1 inbound CALLS edge and 1 outbound CALLS
   edge in the result set, AND B's role is OTHER or its declaring class role is
   SERVICE/COMPONENT. Merge A→B→C into A→C edge with:
   - `collapsed=True`
   - `collapsed_intermediates=[B.id]`
   - `attrs` from the lower-confidence edge
   - Remove B from `nodes` dict
   - **Recompute `parent_edge_id`**: any edge whose `parent_edge_id` referenced the
     removed B→C edge is updated to reference the collapsed A→C edge. Update
     `edge_id_map` accordingly.
   Increment `stats.edges_collapsed_trivial`.
   When `collapse_trivial=False`, skip this pass entirely.

4. **Cross-service boundary detection**:
   - When BFS encounters a node with outgoing `DECLARES_CLIENT` or `DECLARES_PRODUCER`
     edges, AND `HTTP_CALLS` or `ASYNC_CALLS` is in the user's `edge_types`:
   - Follow scaffolding edge to Client/Producer node (consume a hop). This is the only
     case where the engine follows edge types not in `edge_types`.
   - From Client/Producer, follow `HTTP_CALLS`/`ASYNC_CALLS` to downstream Route/endpoint.
   - Record the cross-service edge with `cross_service_boundary=True` and full attrs
     (`confidence`, `strategy`, `match`, `raw_uri`/`raw_topic`).
   - Include downstream Route/Producer node in `nodes` dict.
   - Do NOT add downstream node to frontier. BFS stops at the boundary.
   - Scaffolding edges are exempt from `fan_out_cap`.

5. **Stats fields**: populate `nodes_pruned_role`, `nodes_pruned_fan_out`,
   `edges_collapsed_trivial` in `TraceStats`.

Extend `tests/test_mcp_trace.py` with all 11 new tests from the plan. All 23
existing tests must still pass. Note: 1a's `test_trace_prune_roles_param_accepted_noop`
is replaced by 1b's `test_trace_filter_vs_prune_roles` (the 1a stub becomes obsolete
once real pruning logic exists). Total unique tests after both PRs: 33.

## Out of scope (do NOT touch)

- `mcp_v2.py`, `kuzu_queries.py`, `server.py` — no modifications.
- `build_ast_graph.py`, `java_ontology.py` — no changes.
- `mcp_hints.py` — no changes (PR-TRACE-3).
- `skills/explore-codebase/SKILL.md` — no changes (PR-TRACE-3).
- Any files under `docs/` or `README.md` (PR-TRACE-4).

If you need to touch any of these, stop and ask.

## Deliverables

1. `prune_roles` soft gate implemented in BFS loop.
2. `fan_out_cap` with confidence + role ranking implemented; scaffolding exemption.
3. `collapse_trivial` heuristic with `parent_edge_id` recomputation.
4. Cross-service boundary detection with scaffolding edge following.
5. `TraceStats` pruning/collapsing counters populated.
6. 11 new tests in `tests/test_mcp_trace.py`; all 33 unique tests pass.
7. Ruff clean; no regression.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge gate (CI on PR + `master`).

- `tests/test_mcp_trace.py` — exercises pruning, collapsing, and cross-service code paths plus all 1a core BFS tests.

## Tests

Run:
```bash
.venv/bin/ruff check mcp_trace.py tests/test_mcp_trace.py
.venv/bin/python -m pytest tests/test_mcp_trace.py -v
.venv/bin/python -m pytest tests -v
```

Expected: all 33 unique tests pass (23 from 1a — with `test_trace_prune_roles_param_accepted_noop` replaced by `test_trace_filter_vs_prune_roles` — plus 10 other new from 1b); existing suite unchanged.

## Sentinel checks

Verify no modifications to files outside scope:
```bash
git diff experimental -- mcp_v2.py kuzu_queries.py server.py build_ast_graph.py java_ontology.py
```
Expected: empty diff for all listed files.

## Manual evidence

After implementation, spot-check pruning:
```bash
.venv/bin/python -c "
from kuzu_queries import KuzuGraph
import mcp_trace
g = KuzuGraph()
# Test prune_roles
out = mcp_trace.trace_v2(
    ids='sym:ChatManagementService#getAllChats',
    direction='out',
    edge_types=['CALLS'],
    max_depth=3,
    prune_roles=['DTO', 'OTHER'],
    fan_out_cap=5,
    graph=g,
)
print('prune_roles stats:', out.stats.nodes_pruned_role, 'nodes pruned by role')
print('fan_out stats:', out.stats.nodes_pruned_fan_out, 'nodes pruned by cap')
print('edges:', len(out.edges), 'after pruning')
print('collapsed:', out.stats.edges_collapsed_trivial, 'trivial chains collapsed')
"
```

## Definition of Done

- [ ] `prune_roles` soft gate: edges recorded, frontier stops through pruned nodes
- [ ] `fan_out_cap` ranking: confidence primary, role tiebreaker, scaffolding exempt
- [ ] `collapse_trivial`: degree-1 chains collapsed with `collapsed=True` marker
- [ ] `parent_edge_id` consistent after collapsing
- [ ] Cross-service boundary: `cross_service_boundary=True`, downstream in `nodes`, not in frontier
- [ ] All 33 unique tests pass (23 from 1a minus 1 replaced + 11 from 1b)
- [ ] `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests -v` green
- [ ] No modifications to `mcp_v2.py`, `kuzu_queries.py`, `server.py`
- [ ] PR title: `add trace pruning collapsing cross-service (PR-TRACE-1b)`
- [ ] Branch: `feat/trace-pruning` off `experimental`
````

---

## PR-TRACE-2 — MCP tool registration

**Branch:** `feat/trace-mcp-registration` off `experimental` (after PR-TRACE-1b merged).
**Base:** `experimental` (with PR-TRACE-1b merged).
**Blocked on:** PR-TRACE-1b merged to `experimental`.
**Plan section:** `plans/completed/PLAN-TRACE-TOOL.md` § PR-TRACE-2.
**PR title:** `register trace as sixth MCP tool (PR-TRACE-2)`

**Attach (`@-files`):**

- `@plans/completed/PLAN-TRACE-TOOL.md` (PR-TRACE-2 section only)
- `@propose/active/TRACE-TOOL-PROPOSE.md` (§ "Agent tool selection" for description guidance)
- `@server.py`
- `@mcp_trace.py` (read-only — already shipped via PR-TRACE-1a/1b)
- `@mcp_v2.py` (read-only — reference for `asyncio.to_thread` pattern)
- `@tests/test_server.py` (read-only — follow registration test patterns)
- `@tests/test_mcp_trace.py` (extend with registration tests)

**Prompt:**

````
You are implementing PR-TRACE-2 from `plans/completed/PLAN-TRACE-TOOL.md`.

PR-TRACE-1a + 1b (`mcp_trace.py` with full BFS + pruning) is already merged to
`experimental`. This PR wires `trace` into the MCP surface in `server.py`.

## Scope

1. **`server.py`** — Add `import mcp_trace` at top-level. Update `_INSTRUCTIONS` to
   list **six** tools (`search`, `find`, `describe`, `neighbors`, `resolve`, `trace`).
   Add one clause for `trace`: multi-hop BFS with server-side pruning, direction +
   edge_types required. Register `@mcp.tool(name="trace", ...)` with:
   - Complete tool `description=` matching propose § "Agent tool selection" guidance —
     when to use `trace` vs `neighbors`, parameter semantics, result structure.
   - All parameters from the propose § "Signature" as `Field()` with descriptions.
   - `asyncio.to_thread` wiring to `mcp_trace.trace_v2`.

2. **`tests/test_mcp_trace.py`** — Add 2 registration tests:
   - `test_trace_registered_as_mcp_tool` — `create_mcp_server()` tool list includes `"trace"`.
   - `test_trace_tool_description_mentions_six_tools` — `_INSTRUCTIONS` contains `trace`.

## Out of scope (do NOT touch)

- `mcp_trace.py` — no changes (already complete from 1a+1b).
- `mcp_v2.py`, `kuzu_queries.py` — no changes.
- `build_ast_graph.py`, `java_ontology.py` — no changes.
- `mcp_hints.py` — no changes (PR-TRACE-3).
- `skills/explore-codebase/SKILL.md` — no changes (PR-TRACE-3).
- Any files under `docs/` or `README.md` (PR-TRACE-4).

If you need to touch any of these, stop and ask.

## Deliverables

1. `trace` registered in `create_mcp_server()` with complete description and parameter schema.
2. `_INSTRUCTIONS` updated to six tools including `trace`.
3. 2 registration tests added and passing.
4. Full suite green; ruff clean.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge gate (CI on PR + `master`).

- `tests/test_mcp_trace.py` — exercises registration tests + all existing trace tests.
- `tests/test_server.py` — existing server tests must not regress.

## Tests

Run:
```bash
.venv/bin/ruff check server.py tests/test_mcp_trace.py
.venv/bin/python -m pytest tests/test_mcp_trace.py tests/test_server.py -v
.venv/bin/python -m pytest tests -v
```

Expected: all pass; existing suite unchanged.

## Sentinel checks

Verify `_INSTRUCTIONS` mentions trace and six tools:
```bash
rg "trace" server.py | head -5
rg -c "search.*find.*describe.*neighbors.*resolve.*trace" server.py
```

Verify no changes to `mcp_trace.py`:
```bash
git diff experimental -- mcp_trace.py
```
Expected: empty diff.

## Manual evidence

After registration, verify tool is callable:
```bash
.venv/bin/python -c "
from server import create_mcp_server
srv = create_mcp_server()
tools = [t.name for t in srv._tool_manager._tools.values()]
print('tools:', tools)
assert 'trace' in tools, 'trace not registered'
print('ok: trace registered as 6th tool')
"
```

## Definition of Done

- [ ] `trace` callable via MCP protocol
- [ ] `_INSTRUCTIONS` lists six tools
- [ ] Tool description covers when to use trace vs neighbors, parameters, result structure
- [ ] `asyncio.to_thread` wiring correct
- [ ] 2 registration tests pass
- [ ] `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests -v` green
- [ ] No changes to `mcp_trace.py`, `mcp_v2.py`, `kuzu_queries.py`
- [ ] PR title: `register trace as sixth MCP tool (PR-TRACE-2)`
- [ ] Branch: `feat/trace-mcp-registration` off `experimental`
````

---

## PR-TRACE-3 — Cross-service integration + hints + skill

**Branch:** `feat/trace-hints-skill` off `experimental` (after PR-TRACE-1b merged).
**Base:** `experimental` (with PR-TRACE-1b merged).
**Blocked on:** PR-TRACE-1b merged to `experimental`. Independent of PR-TRACE-2.
**Plan section:** `plans/completed/PLAN-TRACE-TOOL.md` § PR-TRACE-3.
**PR title:** `add trace hints and skill integration (PR-TRACE-3)`

**Attach (`@-files`):**

- `@plans/completed/PLAN-TRACE-TOOL.md` (PR-TRACE-3 section only)
- `@propose/active/TRACE-TOOL-PROPOSE.md` (§ "Hint system updates", § "Skill decision tree update")
- `@mcp_hints.py`
- `@mcp_trace.py` (read-only — reference for `TraceOutput` shape)
- `@skills/explore-codebase/SKILL.md`
- `@tests/test_mcp_trace.py` (extend with hint and integration tests)
- `@tests/test_mcp_hints.py` (read-only — follow hint test patterns, extend if needed)

**Prompt:**

````
You are implementing PR-TRACE-3 from `plans/completed/PLAN-TRACE-TOOL.md`.

PR-TRACE-1a + 1b (`mcp_trace.py`) is merged to `experimental`. PR-TRACE-2 (server
registration) may or may not be merged — this PR is independent of it. This PR adds
hint generation and skill decision tree updates.

## Scope

1. **`mcp_hints.py`** — Extend `generate_hints` `output_kind` Literal to include
   `"trace"`. Add trace hint generation following the existing `_neighbors_*_structured_hints`
   pattern. Four hint templates:

   a. **Trace result drill-down hint**: when `trace` returns edges with `collapsed=True`
      or `stats` shows non-zero pruning counts, emit:
      `"trace pruned N edges. Use neighbors(id, direction, edge_types) on specific nodes for full detail."`

   b. **Trace budget hit hint**: when `stats.budget_hit=True`, emit:
      `"trace hit the node discovery budget (N nodes). Results are partial. Increase max_depth or add prune_roles and re-run."`

   c. **Cross-service boundary hint**: when `trace` discovers edges with
      `cross_service_boundary=True`, emit:
      `"Cross-service boundary: Client X calls Route Y (confidence=N). Use trace(route_id, 'out', ['EXPOSES','CALLS'], max_depth=4) to continue in the downstream service, or describe(route_id) for route details."`

   d. **Neighbors high fan-out hint**: when `neighbors` returns >8 CALLS edges for a
      single node, emit:
      `"High fan-out (N CALLS edges). Consider trace(id, 'out', ['CALLS'], prune_roles=['DTO','EXCEPTION','UTILITY'], fan_out_cap=5) for a pruned multi-hop view."`

2. **`skills/explore-codebase/SKILL.md`** — Update reasoning preamble to add `trace`:
   ```
   Q-class: <semantic | structured | inspect | walk | trace>
   Pick: <search|find|describe|neighbors|trace|resolve>  Why: <≤8 words>
   ```
   Add `trace` rows to the decision tree per propose § "Skill decision tree update":
   - "What happens when route R is called?" → `find(kind="route")` then `trace(route_id, "out", ["EXPOSES","CALLS"], max_depth=4)`
   - "Impact of changing method M" → `resolve` / `find` then `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)`
   - "Trace from X to database" → `trace(id, "out", ["CALLS"], max_depth=4, prune_roles=["DTO","EXCEPTION"])`
   - "What calls this across services?" → `trace(id, "out", ["CALLS","HTTP_CALLS","ASYNC_CALLS"], max_depth=5)`

   Add `trace` tool reference section with parameters, result structure, and
   when to use vs `neighbors` guidance.

3. **`tests/test_mcp_hints.py`** — Add 4 hint unit tests:
   - `test_hint_trace_budget_hit`
   - `test_hint_trace_pruned_edges`
   - `test_hint_trace_cross_service_boundary`
   - `test_hint_neighbors_high_fanout_mentions_trace`

4. **`tests/test_mcp_trace.py`** — Add 1 integration test:
   - `test_trace_bank_chat_cross_service_http_flow`

## Out of scope (do NOT touch)

- `mcp_trace.py` — no changes to the trace engine (complete from 1a+1b).
- `mcp_v2.py`, `kuzu_queries.py` — no changes.
- `server.py` — no changes (PR-TRACE-2 owns registration).
- `build_ast_graph.py`, `java_ontology.py` — no changes.
- Any files under `docs/` or `README.md` (PR-TRACE-4).

If you need to touch any of these, stop and ask.

## Deliverables

1. `generate_hints` supports `output_kind="trace"` with four hint templates.
2. `neighbors` high-fan-out hint mentions `trace`.
3. Skill preamble updated with `trace` Q-class.
4. Skill decision tree has four new `trace` rows.
5. 5 new tests pass; full suite green.
6. Ruff clean.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge gate (CI on PR + `master`).

- `tests/test_mcp_trace.py` — exercises new integration and hint tests.
- `tests/test_mcp_hints.py` — existing hint tests must not regress.

## Tests

Run:
```bash
.venv/bin/ruff check mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_trace.py tests/test_mcp_hints.py -v
.venv/bin/python -m pytest tests -v
```

Expected: all pass; existing suite unchanged.

## Sentinel checks

Verify hint generation includes trace:
```bash
rg '"trace"' mcp_hints.py | head -5
```

Verify no changes to trace engine:
```bash
git diff experimental -- mcp_trace.py
```
Expected: empty diff.

Verify skill preamble updated:
```bash
rg "trace" skills/explore-codebase/SKILL.md | head -10
```

## Manual evidence

After implementation, verify hints fire:
```bash
.venv/bin/python -c "
from mcp_hints import generate_hints
hints, advisories = generate_hints('trace', {
    'stats': {'budget_hit': True, 'total_nodes_discovered': 500, 'nodes_after_pruning': 120},
    'edges': [],
    'nodes': {},
})
print('budget hit hints:', [h.text for h in hints])
assert len(hints) > 0, 'no budget hit hint'
print('ok')
"
```

## Definition of Done

- [ ] `generate_hints("trace", ...)` produces hints for budget hit, pruning, cross-service
- [ ] `generate_hints("neighbors", ...)` high-fan-out mentions trace
- [ ] Skill preamble has `trace` Q-class
- [ ] Skill decision tree has four trace rows
- [ ] 5 new tests pass
- [ ] `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests -v` green
- [ ] No changes to `mcp_trace.py`, `mcp_v2.py`, `server.py`
- [ ] PR title: `add trace hints and skill integration (PR-TRACE-3)`
- [ ] Branch: `feat/trace-hints-skill` off `experimental`
````

---

## PR-TRACE-4 — Documentation

**Branch:** `feat/trace-docs` off `experimental` (after PR-TRACE-3 merged).
**Base:** `experimental` (with PR-TRACE-3 merged).
**Blocked on:** PR-TRACE-3 merged to `experimental`.
**Plan section:** `plans/completed/PLAN-TRACE-TOOL.md` § PR-TRACE-4.
**PR title:** `update docs for trace tool (PR-TRACE-4)`

**Attach (`@-files`):**

- `@plans/completed/PLAN-TRACE-TOOL.md` (PR-TRACE-4 section only)
- `@README.md`
- `@docs/AGENT-GUIDE.md`
- `@AGENTS.md`
- `@propose/active/TRACE-TOOL-PROPOSE.md` (read-only — context for documentation)

**Prompt:**

````
You are implementing PR-TRACE-4 from `plans/completed/PLAN-TRACE-TOOL.md`.

PR-TRACE-1a, 1b, 2, and 3 are merged to `experimental`. This PR is documentation-only.

## Scope

1. **`README.md`** — Update "five tools" → "six tools" throughout. Add `trace` row
   to the MCP tool table with purpose ("Multi-hop BFS traversal with pruning") and
   required args (`ids`, `direction`, `edge_types`). Update agent guide blurb.
   Update the "5-minute walkthrough" and "Wire into an MCP host" sections if they
   reference the tool count.

2. **`docs/AGENT-GUIDE.md`** — Add `trace` to tool reference section. Update navigation
   patterns to include trace workflows. Update reasoning preamble examples. Ensure
   the tool reference heading reflects six tools.

3. **`AGENTS.md`** — Update MCP tool count from five to six. Add `trace` to the file map
   table as `mcp_trace.py | Multi-hop BFS traversal engine (trace MCP tool)`.

4. **`propose/active/TRACE-TOOL-PROPOSE.md`** — Move to `propose/completed/TRACE-TOOL-PROPOSE.md`.
   Use `git mv` to preserve history.

## Out of scope (do NOT touch)

- Any Python source files (`mcp_trace.py`, `mcp_v2.py`, `server.py`, `mcp_hints.py`,
  `kuzu_queries.py`, `build_ast_graph.py`, `java_ontology.py`).
- Any test files.
- `skills/explore-codebase/SKILL.md` — already updated in PR-TRACE-3.
- `docs/CONFIGURATION.md` — no config surface changes.
- Any `.cursor/` or `.agents/` internal files.

If you need to touch any of these, stop and ask.

## Deliverables

1. README lists six MCP tools with `trace` in the tool table.
2. AGENT-GUIDE documents `trace` in tool reference with navigation patterns.
3. AGENTS.md reflects six tools and `mcp_trace.py` in file map.
4. Propose moved to `propose/completed/`.
5. No "five tools" references remain on agent-facing surfaces.

## Tests to run (iteration loop)

- *(none — docs-only change; CI test job passes but pytest is skipped.)*

## Tests

Run:
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: ruff clean (no Python changes); full suite green (no regressions from doc edits).

## Sentinel checks

Grep for stale "five tools" references:
```bash
rg -i "five tools" README.md docs/AGENT-GUIDE.md AGENTS.md
```
Expected: zero matches.

Grep for "six tools" as confirmation:
```bash
rg -i "six tools" README.md docs/AGENT-GUIDE.md AGENTS.md
```
Expected: matches in updated docs.

Verify propose moved:
```bash
ls propose/active/TRACE-TOOL-PROPOSE.md 2>&1
ls propose/completed/TRACE-TOOL-PROPOSE.md 2>&1
```
Expected: first returns "not found", second returns the file.

## Manual evidence

Verify tool table:
```bash
rg "trace" README.md | grep -i "tool\|purpose"
```

## Definition of Done

- [ ] README MCP tool table has `trace` row; intro says six tools
- [ ] AGENT-GUIDE has `trace` in tool reference; preamble updated
- [ ] AGENTS.md file map includes `mcp_trace.py`; six-tool count
- [ ] Propose moved to `propose/completed/TRACE-TOOL-PROPOSE.md`
- [ ] `rg -i "five tools"` returns zero on agent-facing docs
- [ ] `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests -v` green
- [ ] No Python source file changes
- [ ] PR title: `update docs for trace tool (PR-TRACE-4)`
- [ ] Branch: `feat/trace-docs` off `experimental`
````
