# TRACE-TOOL-V2 -- Trace tool enhancements: tree output, richer pruning, bidirectional traversal

## Status
Proposal -- not yet implemented.

## Problem Statement

The `trace` tool shipped on `experimental` (PR-TRACE-1a through PR-TRACE-4) with conservative defaults validated in the v1 proposal (`propose/completed/TRACE-TOOL-PROPOSE.md`). Four concrete shortcomings emerged during review and production use:

1. **Flat output forces agents to reconstruct trees.** The `edges` list with `parent_edge_id` is structurally correct but cognitively expensive for LLMs consuming 30+ edges. Agents must iterate the flat list, build a parent-child map, and mentally reconstruct the call tree -- every single trace call. This is the same "context consumed on mechanics" problem that motivated `trace` over `neighbors` loops.

2. **The `collapse_trivial` heuristic is too blunt.** The degree-1 rule (exactly 1 inbound + 1 outbound CALLS, role is OTHER) collapses genuine wrapper chains but misses Spring delegate patterns where the intermediate role is `SERVICE` (e.g., `OrderFacade -> OrderService -> OrderRepository`). It also over-collapses when the intermediate carries meaningful annotations (`@Transactional`, `@Async`). Production traces on the bank-chat fixture show false negatives (uncollapsed delegates) more often than false positives.

3. **Fan-out ranking ignores source-node context.** The static `_ROLE_PRIORITY` dict (`CONTROLLER=5 > SERVICE=4 > ...`) ranks all edges identically regardless of where BFS is traversing from. From a `SERVICE` node, a `REPOSITORY` callee is more relevant than a `CONTROLLER` callee at equal confidence. The static tiebreaker produces counterintuitive ordering in cross-cutting traces.

4. **Unidirectional traces require paired calls.** Impact analysis ("who depends on X and what does X call?") requires one `trace(..., direction="in")` and one `trace(..., direction="out")` on the same seed. The agent must merge two result sets client-side, track visited sets across calls, and reconcile overlapping edges. This is the same "agent drowning" pattern that `trace` was designed to eliminate.

### Concrete examples

**Flat output problem:**
```
Agent calls trace(controller_id, "out", ["EXPOSES", "CALLS"], max_depth=3)
→ Returns 24 edges with parent_edge_id
→ Agent prompt: "Reconstruct the call tree from this flat edge list"
→ Agent spends 500+ tokens building the tree in its reasoning
→ vs. receiving a nested tree directly and reasoning about the flow
```

**Fan-out ranking problem:**
```
From OrderService#createOrder (SERVICE role):
  - calls PaymentClient (CLIENT, confidence=0.9) → kept
  - calls OrderRepository#save (REPOSITORY, confidence=0.9) → kept
  - calls OrderController#getStatus (CONTROLLER, confidence=0.9) → kept (static priority=5)
  - calls MetricsReporter#record (OTHER, confidence=0.9) → pruned (static priority=1)

With source-relative priority (from SERVICE):
  REPOSITORY=5 > CLIENT=4 > SERVICE=3 > CONTROLLER=2 > OTHER=1
  → OrderRepository#save would be top-ranked, not OrderController#getStatus
```

**Bidirectional problem:**
```
Question: "What is the blast radius of changing OrderService#createOrder?"
Agent must:
1. trace(id, "in", ["CALLS", "OVERRIDES"]) → who depends on me
2. trace(id, "out", ["CALLS", "HTTP_CALLS"]) → what do I depend on
3. Merge the two trees, noting shared nodes
→ 2 calls + client-side merge vs. 1 call with a unified tree
```

## Proposed Solution

### Enhancement 1: Tree output format (replaces flat `edges` + `paths`)

**Breaking change.** The current `edges` list and `paths` list are replaced with a single `tree` field. The `ranked_leaves` field preserves the ranked-path signal that `paths` currently provides.

```yaml
TraceOutput:
  success: bool
  seed_ids: list[str]
  direction: str                       # "in", "out", or "both"
  edge_types: list[str]
  actual_depth: int
  nodes: dict[str, NodeRef]            # unchanged
  tree: list[TreeNode]                 # NEW: replaces edges + paths
  ranked_leaves: list[RankedLeaf]      # NEW: replaces ranked paths
  stats: TraceStats                    # unchanged
  message: str | None
  advisories: list[str]

TreeNode:
  id: str                              # node ID (matches key in nodes dict)
  edge_from_parent: EdgeFromParent | None  # null for seed nodes
  children: list[TreeNode]             # nested children (empty list for leaves)
  collapsed: bool = False              # true if this node was produced by collapsing
  collapsed_intermediates: list[str]   # node IDs of collapsed intermediates (retained in nodes dict)

EdgeFromParent:
  direction: Literal["in", "out"]      # traversal direction that produced this edge
  edge_type: str
  hop: int
  cross_service_boundary: bool = False
  attrs: dict[str, Any]

RankedLeaf:
  node_id: str                         # leaf node ID
  depth: int                           # path length (number of edges from seed)
  leaf_role: str | None                # for ranking transparency
  score: float                         # composite ranking score
```

**Why this replaces rather than adds:**
- Keeping both `tree` + `edges` duplicates structural data (edges are derivable from tree traversal).
- Keeping both `tree` + `paths` duplicates the leaf-ranking signal (paths exist to surface ranked leaves).
- The `tree` format is what agents actually consume. The flat format was a v1 compromise to avoid token-cost concerns on large traces. Re-scoping as a replacement eliminates the redundancy.
- `ranked_leaves` is a lightweight summary (node_id + score) that preserves the "which paths matter most" signal without duplicating the full edge lists.

**Collapsed intermediates accessibility.** Unlike v1 (which removed collapsed nodes from the `nodes` dict), v2 **retains** collapsed intermediate nodes in `nodes` so agents can inspect them via `describe` or `neighbors`. The `collapsed_intermediates` list on the `TreeNode` references valid keys in `nodes`. The intermediate is simply removed from the tree structure (its children are not nested under it) but remains accessible as a standalone entry.

**Token budget.** A nested tree with `TreeNode` objects reuses node IDs from the shared `nodes` dict -- node payloads are not duplicated at each nesting level. Empirical: a depth-3 trace on the bank-chat fixture produces ~30 TreeNodes vs ~24 TraceEdges (the tree format is slightly larger due to nesting structure but eliminates the `paths` list).

### Enhancement 2: Configurable `collapse_trivial` heuristic

Replace the hardcoded degree-1 rule with a configurable heuristic via new parameters:

```yaml
trace(
  ...
  collapse_trivial: bool = True,             # unchanged -- master toggle
  collapse_roles: list[str] | None = None,   # NEW: roles eligible for collapse (default: ["OTHER"])
  collapse_min_chain_length: int = 1,        # NEW: minimum chain length to collapse (default: 1, = current behavior)
)
```

**`collapse_roles`** (default `["OTHER"]`): Which roles are considered "trivial" intermediates. Extending to `["OTHER", "SERVICE"]` collapses the `Facade -> Service -> Repository` pattern where the SERVICE intermediate adds no semantic value. The default is conservative (same as v1).

**`collapse_min_chain_length`** (default `1`): Minimum number of consecutive trivial intermediates required before collapsing fires. Setting to `2` prevents collapsing single-intermediate delegates (which may carry meaningful annotations like `@Transactional`) while still collapsing longer wrapper chains. The default `1` matches v1 behavior.

The degree-1 rule (exactly 1 inbound + 1 outbound CALLS in the result set) remains the structural criterion -- only the role filter and minimum length are configurable.

### Enhancement 3: Source-relative fan-out ranking

Replace the static `_ROLE_PRIORITY` dict with a **source-relative** priority that shifts based on the source node's role. The priority table is built from `VALID_ROLES` (via `java_ontology.py`) to avoid duplicating role string literals across modules:

```python
# Coupled to VALID_ROLES in java_ontology.py — when roles are added/removed there,
# this table must be updated. The keys are source-node roles; the inner dicts map
# target-node roles to priority values (higher = more relevant from that source).
_SOURCE_RELATIVE_PRIORITY: dict[str, dict[str, int]] = {
    "CONTROLLER": {"SERVICE": 5, "REPOSITORY": 4, "CLIENT": 3, "OTHER": 2, "CONTROLLER": 1},
    "SERVICE": {"REPOSITORY": 5, "CLIENT": 4, "SERVICE": 3, "OTHER": 2, "CONTROLLER": 1},
    "REPOSITORY": {"CLIENT": 4, "SERVICE": 3, "OTHER": 2, "REPOSITORY": 1, "CONTROLLER": 1},
    # fallback for unmapped source roles: static priority
}
```

All role strings in this table must be members of `VALID_ROLES`. A startup assertion validates this so the table cannot silently drift from the ontology.

When the source node's role is not in `_SOURCE_RELATIVE_PRIORITY`, the existing static `_ROLE_PRIORITY` is used as fallback. This is a zero-config change -- the behavior improves automatically without new parameters.

**Post-pruning budget** (`min_result_nodes`): When aggressive pruning (`prune_roles`, `fan_out_cap`) would reduce the result below `min_result_nodes`, the engine relaxes the fan-out cap incrementally until the target is met. This prevents the common failure mode where a trace returns 2 nodes because 48 were pruned.

```yaml
trace(
  ...
  min_result_nodes: int = 0,  # NEW: post-pruning floor (0 = disabled, default = no floor)
)
```

When `min_result_nodes > 0` and the initial BFS produces fewer result nodes than the target, the engine re-runs with `fan_out_cap * 2` (up to one retry). If still below target, returns what it has with an advisory. This is a soft target, not a guarantee.

### Enhancement 4: Bidirectional traversal

Add `"both"` as a valid `direction` value:

```yaml
direction: Literal["in", "out", "both"]  # was: Literal["in", "out"]
```

When `direction="both"`, the engine runs two independent BFS traversals (in + out) from the same seed set, then merges the results into a single tree. The seed node is the root of both directions -- inbound edges form the "in" subtree, outbound edges form the "out" subtree.

```python
# Pseudocode
if direction == "both":
    in_result = _bfs(seed_ids, "in", edge_types, ...)
    out_result = _bfs(seed_ids, "out", edge_types, ...)
    return _merge_bidirectional(seed_ids, in_result, out_result)
```

**Merge semantics:**
- The `nodes` dict is a union of both traversals (deduplicated by node ID).
- The `tree` has the seed at the root with a single `children` list containing nodes from both directions. Directionality is preserved via `edge_from_parent.direction` on each child -- agents can distinguish "who calls me" (`direction="in"`) from "what do I call" (`direction="out"`).
- `stats` aggregates both traversals (nodes discovered = in + out, etc.).
- `ranked_leaves` merges and re-ranks leaves from both directions.
- If the same node is discovered in both directions, it appears once in `nodes` and once in the tree (in whichever direction it was first discovered; the other direction records the edge but does not duplicate the node).

**Shared visited set.** Both BFS passes share a single visited set so that nodes discovered in the "out" direction are not re-visited in the "in" direction (and vice versa). This prevents redundant exploration of the same subgraph.

**Edge type mapping.** The same `edge_types` list is used for both directions. This is correct because most stored edge labels are direction-agnostic in the graph schema (CALLS has a natural direction, but BFS handles directionality via the Cypher query pattern). If an edge type is only meaningful in one direction (e.g., EXPOSES is always Route -> Handler), the BFS in the other direction will simply find no edges of that type -- no special filtering needed.

## Scope

### What this proposal changes

1. **`mcp_trace.py`**: Replace `TraceEdge`, `TracePath` with `TreeNode`, `EdgeFromParent`, `RankedLeaf`. Refactor `_collapse_trivial_chains` to use configurable heuristic. Refactor `_fan_out_sort_key` to use source-relative priority. Add bidirectional BFS merge logic. Add `min_result_nodes` retry logic.
2. **`server.py`**: Update `trace` tool registration to reflect new parameters (`collapse_roles`, `collapse_min_chain_length`, `min_result_nodes`, `direction="both"`).
3. **`mcp_hints.py`**: Update `_trace_structured_hints` to consume `tree` and `ranked_leaves` instead of `edges` and `paths`. The hint at line 526 reads `payload.get("edges")` -- this must be rewritten to walk the tree structure. Cross-service boundary detection (line 571) must traverse tree nodes checking `edge_from_parent.cross_service_boundary` instead of iterating a flat `edges` list. Pruned/collapsed drill-down hint (line 557) must check `TreeNode.collapsed` on tree nodes instead of `e.get("collapsed")` on edges. The `_high_fanout_trace_hint` function (used by `neighbors` and `describe` paths) is unaffected -- it does not read trace output fields.
4. **Breaking API change**: `TraceOutput.edges` and `TraceOutput.paths` are removed. `TraceOutput.tree` and `TraceOutput.ranked_leaves` are added.
4. **No graph schema changes**: No new node kinds, edge types, or edge attributes.
5. **No re-index required**: The tool reads the existing graph.
6. **No ontology bump**: No changes to `java_ontology.py`.

### What this proposal does NOT change

- `neighbors` is unchanged.
- `search`, `find`, `describe`, `resolve` are unchanged.
- `kuzu_queries.py` is not modified.
- `mcp_v2.py` types (`NodeFilter`, `EdgeFilter`, `NodeRef`) are unchanged.
- `mcp_hints.py` hint functions for non-trace tools (`_high_fanout_trace_hint` used by `neighbors`/`describe`) are unchanged -- only `_trace_structured_hints` is updated.
- Indexer, graph builder, CLI are unchanged.

## Schema / Ontology / Re-index impact

- **Ontology bump**: Not required. No new edge types or node kinds.
- **Re-index required**: No. The tool reads the existing graph.
- **Config/tool surface changes**: `trace` tool signature changes (breaking). Three new optional parameters (`collapse_roles`, `collapse_min_chain_length`, `min_result_nodes`). `direction` accepts `"both"`. Output format changes (tree replaces edges + paths).

## Tests / Validation

### Updated unit tests

All 40 existing v1 tests in `tests/test_mcp_trace.py` must be updated. Every test that asserts on `result.edges` or `result.paths` changes to assert on `result.tree` and `result.ranked_leaves`. The table below lists each existing test and the required change:

| Existing v1 test | Required change |
|---|---|
| `test_trace_outbound_calls_depth_2` | `result.edges[hop=0/1]` → walk `result.tree` children, assert depth-2 nesting |
| `test_trace_inbound_callers_depth_2` | Same pattern for inbound tree |
| `test_trace_max_paths_cap` | `len(result.paths)` → `len(result.ranked_leaves)` |
| `test_trace_budget_stops_early` | `result.edges` → `result.tree` walk; stats assertions unchanged |
| `test_trace_depth_1_equivalent_to_neighbors` | `result.edges` → single-level `result.tree` children |
| `test_trace_stats_counts` | Unchanged (stats fields are the same) |
| `test_trace_empty_seed` | `result.edges == []` → `result.tree == []`; `result.paths == []` → `result.ranked_leaves == []` |
| `test_trace_single_string_seed` | `result.seed_ids` assertion unchanged |
| `test_trace_multiple_seeds` | `result.edges` → `len(result.tree) >= N` seeds |
| `test_trace_invalid_edge_type` | `result.success == False` unchanged |
| `test_trace_direction_required` | `result.success == False` unchanged |
| `test_trace_edge_types_required` | `result.success == False` unchanged |
| `test_trace_max_depth_clamped` | Unchanged (tests parameter clamping, not output format) |
| `test_trace_budget_clamped` | Unchanged (tests parameter clamping, not output format) |
| `test_trace_visited_set_no_cycles` | `result.edges` → tree walk; assert no duplicate node IDs |
| `test_trace_filter_applied` | `result.edges` → tree walk; assert excluded nodes absent |
| `test_trace_filter_vs_prune_roles` | `result.edges` → tree walk; assert pruned-role nodes appear as leaves (no children) |
| `test_trace_edge_filter_calls` | `result.edges` → tree walk; assert filtered edges absent |
| `test_trace_include_unresolved` | `result.edges` → tree walk; assert unresolved nodes present |
| `test_trace_paths_root_to_leaf` | `result.paths` → `result.ranked_leaves`; assert each leaf has a tree path from seed |
| `test_trace_overrides_interface_resolution` | `result.edges` → tree walk; assert OVERRIDES edges present |
| `test_trace_parent_edge_id_seed_null` | **Removed.** `parent_edge_id` no longer exists. Replaced by `test_trace_tree_seed_no_edge_from_parent` (new) |
| `test_trace_parent_edge_id_chain` | **Removed.** `parent_edge_id` no longer exists. Replaced by `test_trace_tree_edge_from_parent_chain` (new) |
| `test_trace_prune_roles` | `result.edges` → tree walk; assert pruned nodes are leaves |
| `test_trace_fan_out_cap` | `result.edges` → tree walk; assert `len(children) <= cap` |
| `test_trace_fan_out_cap_scaffolding_exempt` | `result.edges` → tree walk; assert scaffolding children present despite cap |
| `test_trace_collapse_trivial` | `result.edges[i].collapsed` → `tree_node.collapsed`; assert intermediates in `nodes` dict |
| `test_trace_collapse_trivial_disabled` | `result.edges` → tree walk; assert no `collapsed=True` nodes |
| `test_trace_collapse_parent_edge_id_consistency` | **Removed.** `parent_edge_id` no longer exists. Replaced by `test_trace_tree_collapse_children_reparented` (new) |
| `test_trace_cross_service_http` | `result.edges` → tree walk; assert `edge_from_parent.cross_service_boundary=True` |
| `test_trace_cross_service_async` | Same pattern for async |
| `test_trace_cross_service_edge_attrs` | `edges[i].attrs` → `tree_node.edge_from_parent.attrs` |
| `test_trace_cross_service_boundary_stops` | `result.edges` → tree walk; assert boundary node has no children |
| `test_trace_cross_service_seamless_http` | `result.edges` → tree walk; assert children exist past boundary |
| `test_trace_cross_service_seamless_async` | Same pattern for async |
| `test_trace_cross_service_seamless_respects_budget` | Stats unchanged; tree walk instead of edges |
| `test_trace_cross_service_seamless_exposes_as_scaffolding` | `result.edges` → tree walk; assert EXPOSES followed as scaffolding |
| `test_trace_registered_as_mcp_tool` | Unchanged (tests registration, not output) |
| `test_trace_tool_description_mentions_six_tools` | Unchanged (tests description string) |
| `test_trace_bank_chat_cross_service_http_flow` | `result.edges` → tree walk; full flow assertion on nested structure |

New tests (v2 features):

| Test name | Asserts |
|-----------|---------|
| `test_trace_tree_root_is_seed` | Tree root node matches seed ID |
| `test_trace_tree_seed_no_edge_from_parent` | Seed nodes have `edge_from_parent=None` (replaces `test_trace_parent_edge_id_seed_null`) |
| `test_trace_tree_edge_from_parent_chain` | Non-root nodes have `edge_from_parent` with valid edge_type, hop, and direction (replaces `test_trace_parent_edge_id_chain`) |
| `test_trace_tree_edge_from_parent_direction` | `edge_from_parent.direction` is set ("in" or "out") for all non-root nodes |
| `test_trace_tree_children_nested` | Children are nested TreeNodes, not flat |
| `test_trace_tree_collapsed_node` | Collapsed intermediates carry `collapsed=True` and `collapsed_intermediates` |
| `test_trace_tree_collapse_intermediates_in_nodes` | Collapsed intermediate node IDs exist in `nodes` dict (accessible for describe/neighbors) |
| `test_trace_tree_collapse_children_reparented` | After collapsing A→B→C, C appears as child of A in tree (replaces `test_trace_collapse_parent_edge_id_consistency`) |
| `test_trace_ranked_leaves_capped` | `ranked_leaves` does not exceed `max_paths` |
| `test_trace_ranked_leaves_scores` | Leaves are sorted by descending score |
| `test_trace_collapse_roles_custom` | `collapse_roles=["OTHER","SERVICE"]` collapses SERVICE intermediates |
| `test_trace_collapse_roles_default` | Default `collapse_roles` only collapses OTHER |
| `test_trace_collapse_min_chain_length_2` | `collapse_min_chain_length=2` skips single-intermediate collapses |
| `test_trace_fan_out_source_relative_service` | From SERVICE node, REPOSITORY callee outranks CONTROLLER at equal confidence |
| `test_trace_fan_out_source_relative_controller` | From CONTROLLER node, SERVICE callee outranks REPOSITORY at equal confidence |
| `test_trace_fan_out_source_relative_fallback` | Unknown source role falls back to static priority |
| `test_trace_min_result_nodes_retry` | `min_result_nodes=10` triggers fan-out cap retry when initial result < 10 |
| `test_trace_min_result_nodes_disabled` | `min_result_nodes=0` (default) does not retry |
| `test_trace_bidirectional_basic` | `direction="both"` returns tree with both in and out children from seed |
| `test_trace_bidirectional_shared_visited` | Nodes discovered in "out" are not re-visited in "in" |
| `test_trace_bidirectional_stats_aggregated` | Stats aggregate both directions |
| `test_trace_bidirectional_ranked_leaves_merged` | `ranked_leaves` includes leaves from both directions |

### Regression

- All existing v1 tests updated for new output format. No behavioral regression in traversal logic.
- `ruff check` clean on all changed files.
- Existing tool tests (`test_mcp_v2.py`, `test_server.py`) must pass unchanged.

## Resolved Decisions

1. **`tree` token cost on deep traces** -- No fallback to flat format. The tree format is strictly better for LLM consumption. If token cost is a concern, reduce `max_depth` or `max_paths`.

2. **`min_result_nodes` retry budget** -- One retry with doubled `fan_out_cap`. No configurable retry count. If the doubled cap still produces too few nodes, the graph genuinely has few reachable nodes and more retries won't help.

3. **Bidirectional `edge_types` per direction** -- Same `edge_types` for both directions. If agents need different edge types per direction, they issue two unidirectional calls (same as today).

4. **`collapse_roles` interaction with `prune_roles`** -- Independently configured. A role can be in one, both, or neither list. Default: `collapse_roles=["OTHER"]`, `prune_roles` unset.

## Out of scope

- **Configurable path ranking (`rank_by` parameter)** -- The v1 issue mentions `rank_by: "default" | "shortest_first" | "highest_confidence_first"`. Defer until production evidence shows agents re-ranking client-side. The `ranked_leaves` scores make client-side re-ranking trivial.
- **Adaptive escalation / neighbors loop detection** -- Session-level call tracking for hint emission. Requires server-side session state that the MCP server does not currently maintain. Defer.
- **Per-codebase tuning of `prune_roles` and `fan_out_cap`** -- YAML-configurable defaults for these parameters. The current per-call configuration is sufficient for v2.
- **CLI integration** -- `java-codebase-rag trace` command reusing the engine. Tracked separately in #241.
- **Answer engine / semantic ranking** -- `trace` returns structure, not answers.

## Sequencing / Follow-ups

**Single PR** -- these changes are tightly coupled (tree output format change affects all downstream tests and the tool registration). Splitting into separate PRs would require maintaining both old and new output formats in parallel, which adds complexity without benefit given breaking changes are allowed.

```
experimental ← TRACE-V2 (single PR)
```

**PR-TRACE-V2**: All four enhancements in one PR:
1. Replace `edges`/`paths` with `tree`/`ranked_leaves` in `TraceOutput`.
2. Add `collapse_roles`, `collapse_min_chain_length` to `trace_v2` signature.
3. Implement source-relative fan-out ranking and `min_result_nodes` retry.
4. Implement `direction="both"` bidirectional BFS merge.
5. Update all v1 tests to new output format; add new tests listed above.
6. Update `server.py` tool registration and description.
7. Update `mcp_hints.py` `_trace_structured_hints` to consume `tree` and `ranked_leaves` instead of `edges` and `paths`.
8. Update `docs/AGENT-GUIDE.md` and `skills/explore-codebase/SKILL.md` for tree output.

**Post-merge follow-ups:**
- Production telemetry on `direction="both"` usage to validate the single edge_types-per-direction decision.
- Revisit `rank_by` parameter if agents consistently re-rank `ranked_leaves` client-side.
- Move completed v1 proposal + this v2 proposal to `propose/completed/` after graduation to `master`.
