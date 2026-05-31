# Plan: TRACE-TOOL-V2 — Tree output, richer pruning, bidirectional traversal

Status: **active (planning)**. This plan implements
[`propose/active/TRACE-TOOL-V2-PROPOSE.md`](../../propose/active/TRACE-TOOL-V2-PROPOSE.md)
as a single PR on the `experimental` branch.

Depends on: v1 trace tool (shipped via PR-TRACE-1a through PR-TRACE-4, all merged to `experimental`).

## Goal

- Replace the flat `edges` + `paths` output with a nested **tree** format that agents can reason about directly without reconstructing parent-child relationships.
- Make the `collapse_trivial` heuristic configurable so agents can collapse SERVICE intermediates (Facade→Service→Repository pattern) and tune chain-length thresholds.
- Introduce **source-relative** fan-out ranking so that edge relevance reflects the role of the node BFS is traversing from, not a global static priority.
- Add **bidirectional** traversal (`direction="both"`) so impact analysis ("who depends on X and what does X call?") requires one call instead of two.
- Add `min_result_nodes` post-pruning floor to prevent aggressive pruning from producing empty results.

## Principles (do not relitigate in review)

- **Breaking API change is allowed.** The `edges` and `paths` fields on `TraceOutput` are removed and replaced by `tree` and `ranked_leaves`. No backward compatibility shim.
- **Single PR.** The four enhancements are tightly coupled (tree format change affects tests, tool registration, and hints). Splitting requires maintaining parallel output formats with no benefit.
- **No graph schema changes.** No new node kinds, edge types, or edge attributes. No ontology bump. No re-index.
- **Collapsed intermediates stay accessible.** Unlike v1 (which removed collapsed nodes from `nodes` dict), v2 retains them in `nodes` so agents can `describe` or `neighbors` them. They are simply not nested in the tree structure.
- **Tree format is strictly better.** No fallback to flat format. If token cost is a concern, reduce `max_depth` or `max_paths`.
- **Same `edge_types` for both directions.** If agents need different edge types per direction, they issue two unidirectional calls.
- **`min_result_nodes` retry is at most one retry.** Doubled `fan_out_cap` is still clamped by `max_nodes_discovered`. If still below target, returns what it has with an advisory.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-TRACE-V2 | Tree output, configurable collapse, source-relative ranking, bidirectional traversal, `min_result_nodes` retry | none | BFS→tree conversion correctness (children nesting, collapsed reparenting); bidirectional shared visited set; source-relative priority table drift from VALID_ROLES; hint function migration from flat edges to tree walk; breaking API surface in server.py | `tests/test_mcp_trace.py` (all 40 v1 tests updated + 21 new tests) | — |

Single PR. No sub-PR dependencies.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `tree` token cost on deep traces | No fallback to flat format. Reduce `max_depth` or `max_paths` if needed. |
| `min_result_nodes` retry budget | One retry with doubled `fan_out_cap`. No configurable retry count. |
| Bidirectional `edge_types` per direction | Same `edge_types` for both directions. Issue two unidirectional calls if per-direction types are needed. |
| `collapse_roles` interaction with `prune_roles` | Independent. A role can be in one, both, or neither. Default: `collapse_roles=["OTHER"]`, `prune_roles` unset. |
| Collapsed intermediate accessibility | Retained in `nodes` dict (unlike v1 which removed them). Not nested in tree but accessible standalone. |
| Bidirectional shared visited set | Yes — nodes discovered in "out" are not re-visited in "in" (and vice versa). |
| Bidirectional duplicate node handling | Node appears once in `nodes`. In tree, appears under direction discovered first; second direction produces a leaf TreeNode with its `edge_from_parent.direction`. |
| Source-relative priority fallback | Unknown source role falls back to existing static `_ROLE_PRIORITY`. Zero-config improvement. |

---

# PR-TRACE-V2 — Tree output, richer pruning, bidirectional traversal

## File-by-file changes

### 1. `mcp_trace.py`

**Models (breaking change):**
- Remove `TraceEdge`, `TracePath`.
- Add `TreeNode` model: `id`, `edge_from_parent: EdgeFromParent | None`, `children: list[TreeNode]`, `collapsed: bool = False`, `collapsed_intermediates: list[str]`.
- Add `EdgeFromParent` model: `direction: Literal["in", "out"]`, `edge_type: str`, `hop: int`, `confidence: float | None`, `cross_service_boundary: bool = False`, `attrs: dict[str, Any]`.
- Add `RankedLeaf` model: `node_id: str`, `depth: int`, `leaf_role: str | None`, `score: float`.
- Update `TraceOutput`: remove `edges: list[TraceEdge]` and `paths: list[TracePath]`; add `tree: list[TreeNode]` and `ranked_leaves: list[RankedLeaf]`.

**Source-relative fan-out ranking:**
- Add `_SOURCE_RELATIVE_PRIORITY: dict[str, dict[str, int]]` table keyed by source node role → target role priority. All role strings must be members of `VALID_ROLES` from `java_ontology.py` (startup assertion validates this).
- Refactor `_fan_out_sort_key` to accept `source_role: str | None` and use source-relative priority when the source role is in the table, falling back to static `_ROLE_PRIORITY` otherwise.
- Wire `source_role` from the current BFS frontier node's role in the main loop.

**Configurable collapse heuristic:**
- Add parameters to `trace_v2`: `collapse_roles: list[str] | None = None` (default `["OTHER"]`), `collapse_min_chain_length: int = 1` (default `1`, matches v1).
- Refactor `_collapse_trivial_chains` to accept `collapse_roles` set and `collapse_min_chain_length`. Role check uses configurable set instead of hardcoded `("OTHER", None)`.
- Change collapse behavior: retain collapsed intermediates in `nodes` dict (v1 removed them).

**Bidirectional traversal:**
- Extend `direction` type to `Literal["in", "out", "both"]`.
- When `direction="both"`, run two independent BFS passes (in + out) from the same seed set with a shared visited set. Merge results:
  - `nodes` dict is a union (deduplicated by node ID).
  - `tree` has seed at root with children from both directions. Directionality preserved via `edge_from_parent.direction`.
  - `stats` aggregates both traversals.
  - `ranked_leaves` merges and re-ranks from both directions.
  - Duplicate nodes (discovered in both directions) appear under the direction that discovered them first; second direction produces a leaf TreeNode.

**`min_result_nodes` retry:**
- Add parameter `min_result_nodes: int = 0`.
- When `min_result_nodes > 0` and initial BFS produces fewer result nodes than target, re-run with `fan_out_cap * 2` (one retry, still clamped by `max_nodes_discovered`). If still below target, return what it has with an advisory.

**BFS-to-tree conversion:**
- Add `_build_tree` helper that converts the BFS edge list into nested `TreeNode` structure, handling collapsed intermediates and cross-service boundary metadata.
- Add `_build_ranked_leaves` helper that replaces `_enumerate_paths`, producing `RankedLeaf` objects from the tree leaves.

**Internal refactoring:**
- BFS loop still builds an internal flat edge representation during traversal, then converts to tree post-BFS (after collapse pass).
- `_collapse_trivial_chains` operates on the internal flat representation, then `_build_tree` converts the collapsed flat structure to nested tree.
- `_edge_attrs_for_row`, `_neighbors_batched`, `_load_node_record`, `_node_matches_filter` are unchanged.

### 2. `server.py`

- Update `trace` tool registration:
  - Extend `direction` parameter type to `Literal["in", "out", "both"]`.
  - Add parameters: `collapse_roles: list[str] | None`, `collapse_min_chain_length: int`, `min_result_nodes: int`.
  - Update `description=` string to document `direction="both"`, `collapse_roles`, `collapse_min_chain_length`, `min_result_nodes`, and the new `tree` / `ranked_leaves` output format.
  - Update result description from "`edges` list" / "`paths`" to "`tree` (nested TreeNodes)" / "`ranked_leaves`".
- Update `_INSTRUCTIONS` to reflect the new output format.
- Wire new parameters to `mcp_trace.trace_v2` call.

### 3. `mcp_hints.py`

- Refactor `_trace_structured_hints`:
  - Replace `edges = list(payload.get("edges") or [])` with `tree = list(payload.get("tree") or [])`.
  - Cross-service boundary detection: walk tree nodes checking `node.edge_from_parent.cross_service_boundary` instead of filtering `[e for e in edges if e.get("cross_service_boundary")]`.
  - Pruned/collapsed drill-down hint: check `TreeNode.collapsed` on tree nodes instead of `e.get("collapsed")` on edges.
  - Extract `to_id` / `from_id` from `TreeNode.id` and `TreeNode.edge_from_parent.attrs` instead of flat edge `to_id` / `from_id`.
  - Budget hit and stats-dependent hints unchanged (stats fields are the same).

### 4. `tests/test_mcp_trace.py`

- Update all 40 existing v1 tests to use new output format:
  - `result.edges[...]` → walk `result.tree` children.
  - `result.paths` → `result.ranked_leaves`.
  - `e.collapsed` → `tree_node.collapsed`.
  - `e.cross_service_boundary` → `tree_node.edge_from_parent.cross_service_boundary`.
  - `e.parent_edge_id` → `tree_node.edge_from_parent` (seed nodes have `edge_from_parent=None`).
  - `e.attrs` → `tree_node.edge_from_parent.attrs`.
  - `e.from_id` / `e.to_id` → parent `TreeNode.id` / child `TreeNode.id`.
- Remove tests that test removed concepts: `test_trace_parent_edge_id_seed_null`, `test_trace_parent_edge_id_chain`, `test_trace_collapse_parent_edge_id_consistency`.
- Replace with new tree-specific tests (see test list below).
- Update `test_trace_bank_chat_cross_service_http_flow` to build `trace_payload` with `tree` and `ranked_leaves` instead of `edges` and `paths`.

### 5. `docs/AGENT-GUIDE.md`

- Update `trace` tool reference to document `tree` output, `ranked_leaves`, `direction="both"`, `collapse_roles`, `collapse_min_chain_length`, `min_result_nodes`.
- Update example payloads to show tree structure.

### 6. `skills/explore-codebase/SKILL.md`

- Update `trace` decision tree rows to mention `direction="both"` for impact analysis.
- Update tool reference to document new parameters and output format.

## Tests for PR-TRACE-V2

### Updated v1 tests (assert on `tree` / `ranked_leaves` instead of `edges` / `paths`)

1. `test_trace_outbound_calls_depth_2` — walk `result.tree` children, assert depth-2 nesting
2. `test_trace_inbound_callers_depth_2` — same pattern for inbound tree
3. `test_trace_max_paths_cap` — `len(result.ranked_leaves) <= max_paths`
4. `test_trace_budget_stops_early` — walk `result.tree`; stats assertions unchanged
5. `test_trace_depth_1_equivalent_to_neighbors` — single-level `result.tree` children
6. `test_trace_stats_counts` — unchanged (stats fields are the same)
7. `test_trace_empty_seed` — `result.tree == []` and `result.ranked_leaves == []`
8. `test_trace_single_string_seed` — `result.seed_ids` assertion unchanged
9. `test_trace_multiple_seeds` — `len(result.tree) >= N` seeds
10. `test_trace_invalid_edge_type` — `result.success == False` unchanged
11. `test_trace_direction_required` — `result.success == False` unchanged
12. `test_trace_edge_types_required` — `result.success == False` unchanged
13. `test_trace_max_depth_clamped` — unchanged (tests parameter clamping)
14. `test_trace_budget_clamped` — unchanged (tests parameter clamping)
15. `test_trace_visited_set_no_cycles` — tree walk; assert no duplicate node IDs
16. `test_trace_filter_applied` — tree walk; assert excluded nodes absent
17. `test_trace_filter_vs_prune_roles` — tree walk; assert pruned-role nodes appear as leaves (no children)
18. `test_trace_edge_filter_calls` — tree walk; assert filtered edges absent
19. `test_trace_include_unresolved` — tree walk; assert unresolved nodes present
20. `test_trace_paths_root_to_leaf` — `result.ranked_leaves`; assert each leaf has a tree path from seed
21. `test_trace_overrides_interface_resolution` — tree walk; assert OVERRIDES edges present in `edge_from_parent`
22. `test_trace_prune_roles` — tree walk; assert pruned nodes are leaves
23. `test_trace_fan_out_cap` — tree walk; assert `len(children) <= cap`
24. `test_trace_fan_out_cap_scaffolding_exempt` — tree walk; assert scaffolding children present despite cap
25. `test_trace_collapse_trivial` — `tree_node.collapsed`; assert intermediates in `nodes` dict
26. `test_trace_collapse_trivial_disabled` — tree walk; assert no `collapsed=True` nodes
27. `test_trace_cross_service_http` — tree walk; assert `edge_from_parent.cross_service_boundary=True`
28. `test_trace_cross_service_async` — same pattern for async
29. `test_trace_cross_service_edge_attrs` — `tree_node.edge_from_parent.attrs`
30. `test_trace_cross_service_boundary_stops` — tree walk; assert boundary node has no children
31. `test_trace_cross_service_seamless_http` — tree walk; assert children exist past boundary
32. `test_trace_cross_service_seamless_async` — same pattern for async
33. `test_trace_cross_service_seamless_respects_budget` — stats unchanged; tree walk
34. `test_trace_cross_service_seamless_exposes_as_scaffolding` — tree walk; assert EXPOSES followed as scaffolding
35. `test_trace_registered_as_mcp_tool` — unchanged (tests registration)
36. `test_trace_tool_description_mentions_six_tools` — unchanged (tests description string)
37. `test_trace_bank_chat_cross_service_http_flow` — tree walk; full flow assertion on nested structure; payload uses `tree` and `ranked_leaves`

### Removed v1 tests (replaced by new tree-specific tests)

38. ~~`test_trace_parent_edge_id_seed_null`~~ → replaced by `test_trace_tree_seed_no_edge_from_parent`
39. ~~`test_trace_parent_edge_id_chain`~~ → replaced by `test_trace_tree_edge_from_parent_chain`
40. ~~`test_trace_collapse_parent_edge_id_consistency`~~ → replaced by `test_trace_tree_collapse_children_reparented`

### New tests (v2 features)

41. `test_trace_tree_root_is_seed` — tree root node matches seed ID
42. `test_trace_tree_seed_no_edge_from_parent` — seed nodes have `edge_from_parent=None` (replaces `test_trace_parent_edge_id_seed_null`)
43. `test_trace_tree_edge_from_parent_chain` — non-root nodes have `edge_from_parent` with valid edge_type, hop, and direction (replaces `test_trace_parent_edge_id_chain`)
44. `test_trace_tree_edge_from_parent_direction` — `edge_from_parent.direction` is set ("in" or "out") for all non-root nodes
45. `test_trace_tree_children_nested` — children are nested TreeNodes, not flat
46. `test_trace_tree_collapsed_node` — collapsed intermediates carry `collapsed=True` and `collapsed_intermediates`
47. `test_trace_tree_collapse_intermediates_in_nodes` — collapsed intermediate node IDs exist in `nodes` dict
48. `test_trace_tree_collapse_children_reparented` — after collapsing A→B→C, C appears as child of A in tree (replaces `test_trace_collapse_parent_edge_id_consistency`)
49. `test_trace_ranked_leaves_capped` — `ranked_leaves` does not exceed `max_paths`
50. `test_trace_ranked_leaves_scores` — leaves are sorted by descending score
51. `test_trace_collapse_roles_custom` — `collapse_roles=["OTHER","SERVICE"]` collapses SERVICE intermediates
52. `test_trace_collapse_roles_default` — default `collapse_roles` only collapses OTHER
53. `test_trace_collapse_min_chain_length_2` — `collapse_min_chain_length=2` skips single-intermediate collapses
54. `test_trace_fan_out_source_relative_service` — from SERVICE node, REPOSITORY callee outranks CONTROLLER at equal confidence
55. `test_trace_fan_out_source_relative_controller` — from CONTROLLER node, SERVICE callee outranks REPOSITORY at equal confidence
56. `test_trace_fan_out_source_relative_fallback` — unknown source role falls back to static priority
57. `test_trace_min_result_nodes_retry` — `min_result_nodes=10` triggers fan-out cap retry when initial result < 10
58. `test_trace_min_result_nodes_disabled` — `min_result_nodes=0` (default) does not retry
59. `test_trace_bidirectional_basic` — `direction="both"` returns tree with both in and out children from seed
60. `test_trace_bidirectional_shared_visited` — nodes discovered in "out" are not re-visited in "in"
61. `test_trace_bidirectional_stats_aggregated` — stats aggregate both directions
62. `test_trace_bidirectional_ranked_leaves_merged` — `ranked_leaves` includes leaves from both directions

## Definition of done (PR-TRACE-V2)

- `TraceOutput.tree` contains nested `TreeNode` objects; `TraceOutput.ranked_leaves` contains `RankedLeaf` objects.
- `TraceOutput.edges` and `TraceOutput.paths` no longer exist on the model.
- `collapse_roles` and `collapse_min_chain_length` parameters accepted and affect collapse behavior.
- `direction="both"` runs two BFS passes, merges results, shared visited set.
- Source-relative priority produces different ranking from static priority for at least SERVICE and CONTROLLER source roles.
- `min_result_nodes` triggers retry when initial result is below target.
- `_trace_structured_hints` in `mcp_hints.py` consumes `tree` and `ranked_leaves` instead of `edges` and `paths`.
- `server.py` tool registration updated for new parameters and output format.
- All 37 updated v1 tests + 22 new tests pass (59 total in `test_mcp_trace.py`).
- `.venv/bin/ruff check .` clean on all changed files.
- `.venv/bin/python -m pytest tests -v` green (no regression on other test files).
- `docs/AGENT-GUIDE.md` and `skills/explore-codebase/SKILL.md` updated for tree output and new parameters.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Define `TreeNode`, `EdgeFromParent`, `RankedLeaf` models; update `TraceOutput` | `mcp_trace.py` | Models validate with pydantic; `TraceOutput` has `tree` and `ranked_leaves` fields |
| 2 | Add `_SOURCE_RELATIVE_PRIORITY` table with `VALID_ROLES` assertion | `mcp_trace.py` | Table loads; assertion passes |
| 3 | Refactor `_fan_out_sort_key` to accept `source_role` and use source-relative priority | `mcp_trace.py` | Unit-callable with source role; falls back to static when unmapped |
| 4 | Refactor `_collapse_trivial_chains` to accept `collapse_roles` and `collapse_min_chain_length`; retain intermediates in `nodes` | `mcp_trace.py` | Configurable roles + chain length; intermediates accessible in `nodes` |
| 5 | Implement `_build_tree` helper (flat edges → nested TreeNodes) | `mcp_trace.py` | Tree nesting matches BFS parent-child structure |
| 6 | Implement `_build_ranked_leaves` helper (tree → RankedLeaf list with scoring) | `mcp_trace.py` | Leaves sorted by descending score; capped at `max_paths` |
| 7 | Add `collapse_roles`, `collapse_min_chain_length`, `min_result_nodes` parameters to `trace_v2` | `mcp_trace.py` | Parameters accepted; defaults match v1 behavior |
| 8 | Wire `source_role` from frontier node into fan-out sort key call | `mcp_trace.py` | Source-relative ranking active in BFS loop |
| 9 | Implement `min_result_nodes` retry logic (one retry with doubled fan_out_cap) | `mcp_trace.py` | Retry fires when below target; advisory emitted |
| 10 | Extend `direction` to accept `"both"`; implement bidirectional BFS with shared visited set and merge | `mcp_trace.py` | `direction="both"` returns merged tree; stats aggregated |
| 11 | Replace `edges`/`paths` output construction with `_build_tree`/`_build_ranked_leaves` in main return path | `mcp_trace.py` | `TraceOutput` has `tree` + `ranked_leaves`; no `edges` or `paths` |
| 12 | Update `server.py` tool registration: new params, updated description, `direction="both"` | `server.py` | Tool callable via MCP with all new parameters |
| 13 | Refactor `_trace_structured_hints` to consume `tree`/`ranked_leaves` | `mcp_hints.py` | All four hint templates fire correctly on tree-based payload |
| 14 | Update all v1 tests to use tree/ranked_leaves assertions | `tests/test_mcp_trace.py` | All 37 updated tests pass |
| 15 | Add 22 new tests for v2 features | `tests/test_mcp_trace.py` | All 22 new tests pass |
| 16 | Update `docs/AGENT-GUIDE.md` trace reference | `docs/AGENT-GUIDE.md` | Tree format documented; new parameters listed |
| 17 | Update `skills/explore-codebase/SKILL.md` trace reference | `skills/explore-codebase/SKILL.md` | Tree format documented; `direction="both"` in decision tree |
| 18 | Ruff + full suite | repo | CI-equivalent local pass |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | BFS-to-tree conversion produces incorrect nesting (children assigned to wrong parent) | high | `test_trace_tree_children_nested` validates nesting structure; `test_trace_tree_edge_from_parent_chain` validates parent chain |
| 2 | Bidirectional shared visited set causes missed nodes in second direction | high | `test_trace_bidirectional_shared_visited` validates coverage; stats aggregation test catches undercounting |
| 3 | Source-relative priority table drifts from `VALID_ROLES` in `java_ontology.py` | medium | Startup assertion validates all keys/values are in `VALID_ROLES` |
| 4 | `_trace_structured_hints` migration from flat edges to tree walk misses edge cases | medium | `test_trace_bank_chat_cross_service_http_flow` integration test covers cross-service hints end-to-end |
| 5 | Collapsed intermediate retention in `nodes` dict increases token cost on heavy-collapse traces | low | Nodes are deduplicated; collapsed intermediates are small entries. Acceptable tradeoff for accessibility. |
| 6 | `min_result_nodes` retry with doubled cap still hits `max_nodes_discovered` clamp | low | By design — advisory emitted. The doubled cap is a suggestion, not a guarantee. |
| 7 | Existing tool tests (`test_server.py`, `test_mcp_v2.py`) break due to import changes | low | No changes to `mcp_v2.py` exports; `mcp_trace.py` changes are internal. Only `server.py` import of `mcp_trace.TraceOutput` is affected (same type, new fields). |

# Out of scope

- Configurable path ranking (`rank_by` parameter) — defer until production evidence shows agents re-ranking client-side.
- Adaptive escalation / neighbors loop detection — requires server-side session state.
- Per-codebase tuning of `prune_roles` and `fan_out_cap` via YAML config.
- CLI integration (`java-codebase-rag trace` command) — tracked separately in #241.
- Answer engine / semantic ranking — `trace` returns structure, not answers.
- Graph schema changes, new node kinds, edge types, or edge attributes.
- Indexer changes (`build_ast_graph.py`, `java_index_flow_lancedb.py`).
- Changes to `neighbors`, `search`, `find`, `describe`, `resolve`.
- Changes to `kuzu_queries.py`.
- Ontology version bump.
- Any changes to `master` branch — all work targets `experimental`.

# Whole-plan done definition

1. `TraceOutput` returns `tree` (nested `TreeNode` objects) and `ranked_leaves` instead of `edges` and `paths`.
2. `collapse_roles` and `collapse_min_chain_length` parameters control collapse heuristic.
3. Source-relative fan-out ranking active for SERVICE and CONTROLLER source roles; fallback to static for unknown roles.
4. `direction="both"` produces merged bidirectional tree with shared visited set.
5. `min_result_nodes` triggers fan-out cap retry when initial result is below target.
6. `_trace_structured_hints` consumes tree/ranked_leaves; all four hint templates fire correctly.
7. `server.py` tool registration updated for all new parameters and output format.
8. All 37 updated v1 tests + 22 new tests pass (59 total).
9. Full `pytest tests -v` green (no regression).
10. `docs/AGENT-GUIDE.md` and `skills/explore-codebase/SKILL.md` updated.
11. All work on `experimental` branch — not merged to `master`.

# Tracking

- `PR-TRACE-V2`: _pending_
