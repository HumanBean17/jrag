# Plan: `trace` tool — multi-hop navigation shortcut

Status: **active (planning)**. This plan implements
[`propose/active/TRACE-TOOL-PROPOSE.md`](../../propose/active/TRACE-TOOL-PROPOSE.md)
as a multi-PR sequence on the `experimental` branch.

Depends on: none (additive MCP tool; reads existing graph).

## Goal

- Ship a **sixth MCP tool**, `trace`, as a batched BFS traversal shortcut that returns pruned multi-hop path structure in a single call.
- Eliminate the agent drowning pattern: fan-out explosion, no visited set, low-signal edge domination, context consumed on graph-walking mechanics.
- Preserve the GPS metaphor: `trace` returns paths (structure), not answers. The agent still interprets results.
- Validate experimentally on `experimental` branch before merging to `master` (criteria in propose § "Experimental validation").

## Principles (do not relitigate in review)

- **Server-side pruning is the value.** `trace` is not "neighbors but faster" — role-based pruning, fan-out throttling, trivial chain collapsing, and cross-service boundary detection are things the agent cannot replicate without dozens of tool calls.
- **Boundary-stop, not seamless traversal.** BFS stops at service boundaries. The downstream Route/Producer is included in `nodes` but not in the frontier. The agent decides whether to continue.
- **No graph schema changes.** No new node kinds, edge types, or edge attributes. No ontology bump. No re-index.
- **`neighbors` remains the one-hop primitive.** `trace` is optional; agents that reason well over multi-hop can still use `neighbors` loops.
- **New module only.** `mcp_trace.py` is the implementation. `mcp_v2.py`, `kuzu_queries.py`, and `build_ast_graph.py` are not modified (trace imports types but does not change them).
- **Experimental branch.** All PRs target `experimental`, not `master`. Graduation requires meeting the validation criteria in the propose.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Depends on |
| --- | --- | --- | --- | --- | --- |
| PR-TRACE-1a | Core BFS engine: `mcp_trace.py` models, batched query, BFS with visited set, budget, path enumeration | none | BFS correctness (visited set, cycle handling, budget early-stop); batched Cypher query parity with existing per-node query; `parent_edge_id` consistency; path enumeration cap | `tests/test_mcp_trace.py` (new file) | — |
| PR-TRACE-1b | Pruning, collapsing, cross-service: `prune_roles`, `fan_out_cap`, `collapse_trivial`, cross-service boundary detection | none | Soft-gate vs hard-gate semantics; fan-out ranking stability; trivial-chain heuristic false positives; scaffolding edge exemption; post-collapse `parent_edge_id` recomputation | `tests/test_mcp_trace.py` (extends) | PR-TRACE-1a |
| PR-TRACE-2 | MCP registration: `server.py` tool wiring, `_INSTRUCTIONS` update | none | Tool description contract (LLM reads this); parameter schema accuracy; `asyncio.to_thread` wiring; import path | `tests/test_server.py` (extends) + `tests/test_mcp_trace.py` (e2e) | PR-TRACE-1b |
| PR-TRACE-3 | Cross-service integration + hints + skill: `mcp_hints.py`, `skills/explore-codebase/SKILL.md` | none | Hint text quality (LLM-parseable); skill decision tree ambiguity; cross-service fixture coverage | `tests/test_mcp_hints.py` (extends) + `tests/test_mcp_trace.py` (integration) | PR-TRACE-1b |
| PR-TRACE-4 | Documentation: `README.md`, `docs/AGENT-GUIDE.md`, propose → completed | none | "Five tools" → "six tools" sweep consistency; propose archive | doc review | PR-TRACE-3 |

Landing order: **1a → 1b → 2 / 3 (parallel after 1b) → 4**.

```
experimental ← 1a ← 1b ← 2
                    ← 3 ← 4
```

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `collapse_trivial` heuristic | Degree-1 (1 in + 1 out in result set), role OTHER or declaring-class role SERVICE/COMPONENT. No configurability for v1. |
| `fan_out_cap` ranking | Confidence primary, role tiebreaker (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER). Scaffolding edges exempt. |
| Bidirectional traversal | No for v1. Agent issues two calls. |
| Path ranking | Leaf role priority > min path confidence > path length (shorter first). Fixed for v1. |
| Memory/cost budget | Hard `max_nodes_discovered` (default 500, clamped 100–2000). Counts pre-pruning (intentional: compute guardrail, not output guarantee). |
| Cross-service traversal | Boundary-stop. BFS records the edge, includes downstream node in result, stops frontier. Agent decides. |
| `collapsed` marker | Yes — `collapsed: True` + `collapsed_intermediates: [node_ids]` on `TraceEdge`. |
| Flat edge hierarchy | `parent_edge_id` on `TraceEdge`, not a full `tree` field. Enables O(1) tree reconstruction per edge. |
| PR split | 1a (core BFS + budget + paths) then 1b (pruning + collapsing + cross-service). Different review surfaces. |
| Import contract | Trace imports `NodeFilter`, `EdgeFilter`, `NodeRef`, `_node_ref_from_row`, `_node_kind_from_id` from `mcp_v2.py` — not the propose's `Edge` type. `TraceEdge` is a new model defined in `mcp_trace.py` (different shape: includes `hop`, `parent_edge_id`, `collapsed`, `cross_service_boundary`). The propose's `Edge` is the `neighbors` result type and does not apply to trace. |

---

# PR-TRACE-1a — Core BFS engine

## File-by-file changes

### 1. `mcp_trace.py` (new file)

- **Models**: `TraceEdge`, `TracePath`, `TraceStats`, `TraceOutput` — all `pydantic.BaseModel` with `extra="forbid"`.
- **`neighbors_batched` helper**: issues a single Cypher query per BFS hop for all frontier node IDs (reuses `g._rows` pattern from `kuzu_queries.py`; does not modify `KuzuGraph`).
- **`trace_v2` function**: public handler with the propose § "Signature" parameters.
- **BFS engine**:
  1. Initialize frontier = seed_ids, visited = {seed_ids}, edge_id_map = {}.
  2. Per hop: batched Cypher query, apply `NodeFilter` (hard gate), apply `EdgeFilter` pushdown, record `TraceEdge` with `parent_edge_id`.
  3. Track `total_nodes_discovered`; stop early if `max_nodes_discovered` hit.
  4. Build `TraceStats` with counts.
  5. Enumerate root-to-leaf paths with 10× `max_paths` cap; rank by leaf role priority → min path confidence → path length.
  6. Return `TraceOutput`.
- **Edge type expansion**: same OR-of-scalar-equalities Cypher pattern as `neighbors_v2` in `mcp_v2.py`.
- **Input validation**: direction required, edge_types required and non-empty, max_depth clamped 1..5, max_nodes_discovered clamped 100..2000.
- **Types imported from `mcp_v2.py`**: `NodeFilter`, `EdgeFilter`, `NodeRef`, `_node_ref_from_row`, `_node_kind_from_id`. No modifications to `mcp_v2.py`.
- **Types imported from `kuzu_queries.py`**: `KuzuGraph` (read-only usage of `g._rows`). No modifications to `kuzu_queries.py`.

### 2. `tests/test_mcp_trace.py` (new file)

- All tests use the bank-chat `kuzu_graph` session fixture from `conftest.py`.
- Tests listed below in **Tests for PR-TRACE-1a**.

## Tests for PR-TRACE-1a

1. `test_trace_outbound_calls_depth_2` — traces from a controller method via CALLS out, depth 2, returns edges at hop 0 and hop 1.
2. `test_trace_inbound_callers_depth_2` — traces from a repository method via CALLS in, depth 2, returns caller chain.
3. `test_trace_max_paths_cap` — result paths list does not exceed `max_paths`.
4. `test_trace_budget_stops_early` — BFS stops when `max_nodes_discovered` is hit; `stats.budget_hit=True`; advisory message present.
5. `test_trace_depth_1_equivalent_to_neighbors` — depth 1 trace with no pruning returns same nodes as `neighbors` for same seed + edge types.
6. `test_trace_stats_counts` — `stats.total_nodes_discovered`, `stats.nodes_after_pruning`, `stats.edges_after_pruning` are consistent with the edge set.
7. `test_trace_empty_seed` — empty seed ids returns `success=True, nodes={}, edges=[], paths=[]`.
8. `test_trace_single_string_seed` — single string `ids` is normalized to list; `seed_ids` echoed as list of one.
9. `test_trace_multiple_seeds` — multiple seed IDs produce a union of traces with shared visited set.
10. `test_trace_invalid_edge_type` — unknown edge type returns `success=False` with teaching message.
11. `test_trace_direction_required` — missing direction returns `success=False`.
12. `test_trace_edge_types_required` — empty edge_types returns `success=False`.
13. `test_trace_max_depth_clamped` — `max_depth` values <1 clamped to 1, >5 clamped to 5.
14. `test_trace_budget_clamped` — `max_nodes_discovered` values <100 clamped to 100, >2000 clamped to 2000.
15. `test_trace_visited_set_no_cycles` — BFS does not revisit nodes even if cycles exist in the graph.
16. `test_trace_filter_applied` — `NodeFilter` restricts discovered nodes (hard gate — excluded entirely from nodes dict and edges).
17. `test_trace_prune_roles_param_accepted_noop` — `prune_roles=[]` is accepted and produces a full unpruned result (soft-gate parameter wired but no-op until pruning logic lands in 1b).
18. `test_trace_edge_filter_calls` — `EdgeFilter` with `min_confidence` filters CALLS edges during traversal.
19. `test_trace_include_unresolved` — `UnresolvedCallSite` edges are interleaved when `include_unresolved=True, edge_types=["CALLS"], direction="out"`.
20. `test_trace_paths_root_to_leaf` — each path starts at a seed and ends at a leaf with no further outbound edges in the result.
21. `test_trace_overrides_interface_resolution` — traces from interface method via OVERRIDES out, reaches implementation method.
22. `test_trace_parent_edge_id_seed_null` — seed edges (hop 0) have `parent_edge_id: null`.
23. `test_trace_parent_edge_id_chain` — non-seed edges have `parent_edge_id` pointing to a valid edge in the result.

## Definition of done (PR-TRACE-1a)

- `trace_v2` callable directly from Python with a `KuzuGraph` instance.
- BFS traversal is correct: visited set prevents cycles, budget stops early, path enumeration is capped.
- All 23 tests pass.
- `.venv/bin/ruff check .` clean on `mcp_trace.py` and `tests/test_mcp_trace.py`.
- `.venv/bin/python -m pytest tests/test_mcp_trace.py -v` green.
- Full `pytest tests -v` green (no regression on existing tests).
- No changes to `mcp_v2.py`, `kuzu_queries.py`, `server.py`, or `build_ast_graph.py`.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Define `TraceEdge`, `TracePath`, `TraceStats`, `TraceOutput` models | `mcp_trace.py` | Models validate with pydantic; importable |
| 2 | Implement `neighbors_batched` Cypher helper | `mcp_trace.py` | Single query returns all neighbors for frontier list |
| 3 | Implement BFS core loop with visited set, edge recording, `parent_edge_id` | `mcp_trace.py` | Manual test: `trace_v2` on bank-chat returns edges at multiple hops |
| 4 | Implement `max_nodes_discovered` budget with early-stop + advisory | `mcp_trace.py` | Budget hit produces `stats.budget_hit=True` |
| 5 | Implement path enumeration with cap + ranking | `mcp_trace.py` | Paths list ≤ `max_paths`, ranked by role/confidence/length |
| 6 | Implement input validation (direction required, edge_types required, clamping) | `mcp_trace.py` | Invalid inputs return `success=False` |
| 7 | Add test file with all 23 tests | `tests/test_mcp_trace.py` | `pytest tests/test_mcp_trace.py -v` green |
| 8 | Ruff + full suite | repo | CI-equivalent local pass |

---

# PR-TRACE-1b — Pruning, collapsing, and cross-service

## File-by-file changes

### 1. `mcp_trace.py`

- **Role-based pruning** (`prune_roles`): soft gate — edges to pruned-role nodes are recorded in result, but the node is not added to the next frontier. BFS stops traversing through it.
- **Fan-out throttling** (`fan_out_cap`): per-node cap on candidate edges. Ranking: confidence (highest first), role priority tiebreaker (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER). Scaffolding edges (`DECLARES_CLIENT`, `DECLARES_PRODUCER`) are exempt from cap.
- **Trivial chain collapsing** (`collapse_trivial`): detect chains where intermediate node B has exactly 1 inbound + 1 outbound CALLS edge in the result, and B's role is OTHER or declaring-class role is SERVICE/COMPONENT. Merge A→B→C into A→C with `collapsed=True`, `collapsed_intermediates=[B.id]`. Remove B from nodes dict.
- **Post-collapse `parent_edge_id` recomputation**: update any edge whose `parent_edge_id` referenced a removed edge to reference the collapsed replacement. Update `edge_id_map`.
- **Cross-service boundary detection**: when BFS encounters `DECLARES_CLIENT`/`DECLARES_PRODUCER` followed by `HTTP_CALLS`/`ASYNC_CALLS` (only when `HTTP_CALLS`/`ASYNC_CALLS` is in `edge_types`), follow scaffolding edges to reach cross-service edge, record it with `cross_service_boundary=True`, include downstream Route/Producer node in `nodes` dict, stop frontier at boundary. Scaffolding edges consume a hop but are not required to be in `edge_types`.
- **Stats updates**: `nodes_pruned_role`, `nodes_pruned_fan_out`, `edges_collapsed_trivial` in `TraceStats`.

### 2. `tests/test_mcp_trace.py`

- Extend with tests listed below.

## Tests for PR-TRACE-1b

1. `test_trace_prune_roles` — with `prune_roles=["DTO"]`, DTO nodes' edges are recorded but DTO is not in frontier; BFS doesn't continue through DTO.
2. `test_trace_fan_out_cap` — with `fan_out_cap=2`, a node with 8 outbound CALLS returns at most 2 edges from that node.
3. `test_trace_fan_out_cap_scaffolding_exempt` — scaffolding edges (`DECLARES_CLIENT`) are not counted toward `fan_out_cap`; cross-service path preserved even when cap is tight.
4. `test_trace_collapse_trivial` — wrapper chain A→B→C where B has degree 2 is collapsed to A→C with `collapsed=True`.
5. `test_trace_collapse_trivial_disabled` — with `collapse_trivial=False`, wrapper chains are not collapsed.
6. `test_trace_collapse_parent_edge_id_consistency` — after collapsing A→B→C to A→C, child edges of C that referenced B→C as `parent_edge_id` now reference the collapsed A→C edge.
7. `test_trace_cross_service_http` — traces from a method through `DECLARES_CLIENT` → `HTTP_CALLS`; stops at Route boundary with `cross_service_boundary=True`; Route in `nodes` dict but not in frontier.
8. `test_trace_cross_service_async` — same for `ASYNC_CALLS` through Producer.
9. `test_trace_cross_service_edge_attrs` — cross-service boundary edges include `confidence`, `strategy`, `match` attributes and `cross_service_boundary=True`.
10. `test_trace_cross_service_boundary_stops` — BFS does not follow past cross-service boundary; downstream Route appears in `nodes` but no `EXPOSES`/`CALLS` edges from it.
11. `test_trace_filter_vs_prune_roles` — upgrade from 1a stub: `NodeFilter` exclude_roles removes nodes and edges entirely; `prune_roles` records edges but stops frontier. Test both on same seed with different configs.

## Definition of done (PR-TRACE-1b)

- All pruning features work: `prune_roles` soft gate, `fan_out_cap` with ranking, `collapse_trivial` with intermediates, cross-service boundary-stop.
- `stats` object reports accurate pruning/collapsing counts.
- `parent_edge_id` is consistent after collapsing.
- All 11 new tests pass + all 23 tests from PR-TRACE-1a still pass (33 unique total: 1a's `test_trace_prune_roles_param_accepted_noop` is replaced by 1b's `test_trace_filter_vs_prune_roles`).
- `.venv/bin/ruff check .` clean.
- Full `pytest tests -v` green.
- No changes to `mcp_v2.py`, `kuzu_queries.py`, `server.py`.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Implement `prune_roles` soft gate in BFS loop | `mcp_trace.py` | Pruned nodes' edges recorded, frontier stops |
| 2 | Implement `fan_out_cap` with confidence + role ranking | `mcp_trace.py` | Capped nodes produce ≤ cap edges; stats report count |
| 3 | Implement scaffolding edge exemption + cross-service boundary detection | `mcp_trace.py` | Cross-service edges have `cross_service_boundary=True`; frontier stops |
| 4 | Implement `collapse_trivial` heuristic + post-collapse `parent_edge_id` recomputation | `mcp_trace.py` | Collapsed chains produce single edge with `collapsed=True` |
| 5 | Wire pruning stats into `TraceStats` | `mcp_trace.py` | All stat fields populated correctly |
| 6 | Add tests 1–11 | `tests/test_mcp_trace.py` | `pytest tests/test_mcp_trace.py -v` green |
| 7 | Ruff + full suite | repo | CI-equivalent local pass |

---

# PR-TRACE-2 — `server.py` tool registration

## File-by-file changes

### 1. `server.py`

- **`_INSTRUCTIONS`**: update to list **six** tools (`search`, `find`, `describe`, `neighbors`, `resolve`, `trace`). Add one clause for `trace` (multi-hop BFS with pruning, direction + edge_types required).
- **Tool registration**: add `@mcp.tool(name="trace", …)` after the `resolve` tool:

```python
async def trace(
    ids: str | list[str] = Field(description="Seed node IDs (single string or list)"),
    direction: Literal["in", "out"] = Field(description="Traversal direction: in (callers/dependents) or out (callees/dependencies)"),
    edge_types: list[str] = Field(description="Edge types to traverse (stored labels only: CALLS, IMPLEMENTS, etc.)"),
    max_depth: int = Field(default=3, description="Max BFS hops (1-5, default 3)"),
    max_paths: int = Field(default=20, description="Max root-to-leaf paths to return"),
    max_nodes_discovered: int = Field(default=500, description="Node discovery budget before pruning (100-2000)"),
    filter: dict | str | None = Field(default=None, description="NodeFilter as JSON object or string"),
    edge_filter: dict | str | None = Field(default=None, description="EdgeFilter for CALLS edges (min_confidence, strategies, etc.)"),
    prune_roles: list[str] | None = Field(default=None, description="Roles to prune (edges recorded, frontier stops)"),
    fan_out_cap: int | None = Field(default=5, description="Per-node edge cap (scaffolding edges exempt)"),
    collapse_trivial: bool = Field(default=True, description="Collapse wrapper chains"),
    include_unresolved: bool = Field(default=False, description="Include UnresolvedCallSite edges"),
) -> mcp_trace.TraceOutput:
    return await asyncio.to_thread(mcp_trace.trace_v2, ...)
```

- **Import**: add `import mcp_trace` at top-level.
- **Tool `description=`**: complete description matching the propose § "Agent tool selection" guidance — when to use `trace` vs `neighbors`, parameter semantics, result structure.

### 2. `tests/test_mcp_trace.py`

- Add end-to-end test: call trace through MCP tool registration (if test infrastructure supports it) or verify registration indirectly.

## Tests for PR-TRACE-2

1. `test_trace_registered_as_mcp_tool` — `create_mcp_server()` tool list includes `"trace"`.
2. `test_trace_tool_description_mentions_six_tools` — `_INSTRUCTIONS` contains `trace` and lists six tools.

## Definition of done (PR-TRACE-2)

- `trace` callable via MCP protocol.
- `_INSTRUCTIONS` lists six tools.
- Tool 2 tests pass.
- Full suite green.
- No changes to `mcp_v2.py`, `kuzu_queries.py`, `build_ast_graph.py`.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `import mcp_trace` to `server.py` | `server.py` | Import resolves |
| 2 | Update `_INSTRUCTIONS` to six tools | `server.py` | Grep confirms `trace` in instructions |
| 3 | Register `@mcp.tool(name="trace")` with description + params | `server.py` | Tool appears in MCP tool list |
| 4 | Wire `asyncio.to_thread` to `mcp_trace.trace_v2` | `server.py` | End-to-end call works |
| 5 | Add registration tests | `tests/test_mcp_trace.py` | Tests green |
| 6 | Ruff + full suite | repo | CI-equivalent local pass |

---

# PR-TRACE-3 — Cross-service integration + hints + skill

## File-by-file changes

### 1. `mcp_hints.py`

- Extend `generate_hints` `output_kind` Literal to include `"trace"`.
- Add `generate_hints("trace", payload)` with four server-side hint templates:
  1. **Neighbors high fan-out hint**: when `neighbors` returns >8 CALLS edges, emit `"High fan-out (N CALLS edges). Consider trace(id, 'out', ['CALLS'], prune_roles=['DTO','EXCEPTION','UTILITY'], fan_out_cap=5) for a pruned multi-hop view."`
  2. **Trace result drill-down hint**: when `trace` returns edges with `collapsed=True` or `stats` shows pruning fired, emit `"trace pruned N edges. Use neighbors(id, direction, edge_types) on specific nodes for full detail."`
  3. **Trace budget hit hint**: when `stats.budget_hit=True`, emit `"trace hit the node discovery budget (N nodes). Results are partial. Increase max_depth or add prune_roles and re-run."`
  4. **Cross-service boundary hint**: when `trace` discovers edges with `cross_service_boundary=True`, emit `"Cross-service boundary: Client X calls Route Y (confidence=N). Use trace(route_id, 'out', ['EXPOSES','CALLS'], max_depth=4) to continue in the downstream service."`
  - **Neighbors loop escalation hint** (5th from propose): client-side only (requires session tracking the MCP server doesn't have) — document in skill, not in `mcp_hints.py`.
- Add corresponding `_trace_*_structured_hints` helper functions following the existing `_neighbors_*_structured_hints` pattern.

### 2. `skills/explore-codebase/SKILL.md`

- Update reasoning preamble to add `trace` Q-class:

```
Q-class: <semantic | structured | inspect | walk | trace>
Pick: <search|find|describe|neighbors|trace|resolve>  Why: <≤8 words>
```

- Add `trace` rows to the decision tree per propose § "Skill decision tree update":

| User asks... | First step | Typical follow-up |
| --- | --- | --- |
| "What happens when route R is called?" | `find(kind="route")` then `trace(route_id, "out", ["EXPOSES","CALLS"], max_depth=4)` | `describe` on key nodes |
| "Impact of changing method M" | `resolve` / `find` then `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)` | `describe` on callers |
| "Trace from X to database" | `trace(id, "out", ["CALLS"], max_depth=4, prune_roles=["DTO","EXCEPTION"])` | `neighbors` for pruned detail |
| "What calls this across services?" | `trace(id, "out", ["CALLS","HTTP_CALLS","ASYNC_CALLS"], max_depth=5)` | `trace` on downstream route_id if needed |

- Document `trace` tool reference: parameters, result structure, when to use vs `neighbors`.

### 3. `tests/test_mcp_trace.py`

- Add integration tests against `tests/bank-chat-system` for cross-service flows.

## Tests for PR-TRACE-3

1. `test_hint_trace_budget_hit` — `generate_hints("trace", {"stats": {"budget_hit": True, ...}})` returns advisory hint.
2. `test_hint_trace_pruned_edges` — `generate_hints("trace", {"stats": {"edges_collapsed_trivial": 3, ...}, "edges": [...]})` returns drill-down hint.
3. `test_hint_trace_cross_service_boundary` — `generate_hints("trace", {"edges": [{"cross_service_boundary": True, ...}], "nodes": {...}})` returns cross-service hint with downstream route_id.
4. `test_hint_neighbors_high_fanout_mentions_trace` — `generate_hints("neighbors", {"edges": [...8+ CALLS edges...]})` includes trace recommendation.
5. `test_trace_bank_chat_cross_service_http_flow` — integration: trace from a bank-chat method that has HTTP_CALLS; verify cross-service boundary detected.

## Definition of done (PR-TRACE-3)

- `generate_hints` produces trace-aware hints for all four server-side scenarios.
- `neighbors` high-fan-out hint mentions `trace`.
- Skill decision tree and preamble include `trace`.
- All 5 tests pass + full suite green.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Extend `generate_hints` Literal to include `"trace"` | `mcp_hints.py` | `generate_hints("trace", ...)` does not raise |
| 2 | Implement four trace hint templates | `mcp_hints.py` | Each hint fires on its trigger condition |
| 3 | Add neighbors high-fan-out hint | `mcp_hints.py` | >8 CALLS edges produces trace recommendation |
| 4 | Update skill preamble + decision tree | `skills/explore-codebase/SKILL.md` | `trace` Q-class present; decision table rows added |
| 5 | Add hint + integration tests | `tests/test_mcp_trace.py`, `tests/test_mcp_hints.py` | Tests green |
| 6 | Ruff + full suite | repo | CI-equivalent local pass |

---

# PR-TRACE-4 — Documentation

## File-by-file changes

### 1. `README.md`

- Update "five tools" → "six tools" throughout.
- Add `trace` row to MCP tool table with purpose and required args.
- Update agent guide blurb.

### 2. `docs/AGENT-GUIDE.md`

- Add `trace` to tool reference section.
- Update navigation patterns to include trace workflows.
- Update reasoning preamble examples.

### 3. `docs/CONFIGURATION.md`

- No changes expected (no config surface changes).

### 4. `propose/active/TRACE-TOOL-PROPOSE.md`

- Move to `propose/completed/TRACE-TOOL-PROPOSE.md`.

## Tests for PR-TRACE-4

- Doc-only PR. Validation: grep for "five tools" returns zero hits on agent-facing surfaces.

## Definition of done (PR-TRACE-4)

- All agent-facing docs list six MCP tools.
- Propose archived to `propose/completed/`.
- No "five tools" references remain in README, AGENT-GUIDE, AGENTS.md.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Update README tool table + intro | `README.md` | `trace` row present; "six tools" |
| 2 | Update AGENT-GUIDE tool reference + patterns | `docs/AGENT-GUIDE.md` | `trace` documented; preamble updated |
| 3 | Move propose to completed | `propose/active/` → `propose/completed/` | File moved |
| 4 | Grep sweep for "five tools" | all docs | Zero hits on agent-facing surfaces |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | BFS correctness: cycle handling or visited set bug causes infinite loop or missed nodes | high | Dedicated cycle test (`test_trace_visited_set_no_cycles`); depth clamping as hard safety net; budget as double safety net |
| 2 | Batched Cypher query returns different results than per-node queries | high | `test_trace_depth_1_equivalent_to_neighbors` parity test against existing `neighbors_v2` output |
| 3 | Pruning false positives: `fan_out_cap` or `collapse_trivial` drops edges the agent needs | medium | Full `edges` list available for client-side re-ranking; `stats` reports pruning counts; agent can drill via `neighbors` |
| 4 | Cross-service scaffolding edge handling is subtle (follow only when HTTP_CALLS/ASYNC_CALLS in edge_types, exempt from fan_out_cap) | medium | Dedicated tests for scaffolding exemption + boundary-stop; integration test against bank-chat cross-service flow |
| 5 | `parent_edge_id` inconsistency after collapsing | medium | Dedicated test (`test_trace_collapse_parent_edge_id_consistency`); recomputation step in collapse algorithm |
| 6 | `mcp_trace.py` imports from `mcp_v2.py` create coupling | low | Import only stable types (`NodeFilter`, `EdgeFilter`, `NodeRef`, helpers); never import handler functions. Document in module docstring. |
| 7 | PR-TRACE-2 or PR-TRACE-3 merge before PR-TRACE-1b | medium | State landing order in PR bodies; 2/3 branch from 1b, not 1a |
| 8 | Tool description in `server.py` drifts from propose spec | low | PR-TRACE-2 includes description contract test; review checklist item |

# Out of scope

- Answer engine — `trace` returns structure, not natural-language answers.
- Semantic ranking — `trace` ranks by structural metrics, not query similarity.
- Graph schema changes, new node kinds, new edge types, new edge attributes.
- Indexer changes (`build_ast_graph.py`, `java_index_flow_lancedb.py`).
- Replacing `neighbors` — it remains the one-hop primitive.
- Bidirectional traversal (deferred to #240).
- Configurable fan_out_cap ranking or path ranking (deferred to #240).
- CLI `java-codebase-rag trace` command (deferred to #241).
- Visualization / diagram rendering.
- Composed edge types as input (engine handles multi-hop expansion internally).
- Ontology version bump.
- Any changes to `master` branch — all work targets `experimental`.

# Whole-plan done definition

1. `trace` is registered as the sixth MCP tool and callable via MCP protocol.
2. BFS engine with visited set, budget, path enumeration works correctly (23 core tests pass).
3. Pruning (role-based, fan-out, trivial chain) and cross-service boundary detection work correctly (11 additional tests pass; 33 unique total).
4. Hint system produces trace-aware hints for budget hit, pruning, cross-service boundary, and neighbors high-fan-out.
5. Skill decision tree and preamble include `trace` as a first-class tool choice.
6. All agent-facing docs list six MCP tools.
7. Propose archived to `propose/completed/`.
8. No regression on existing tool tests (`test_mcp_v2.py`, `test_server.py`, etc.).
9. All work on `experimental` branch — not merged to `master` until experimental validation criteria are met.

# Tracking

- `PR-TRACE-1a`: _pending_
- `PR-TRACE-1b`: _pending_
- `PR-TRACE-2`: _pending_
- `PR-TRACE-3`: _pending_
- `PR-TRACE-4`: _pending_
