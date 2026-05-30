# TRACE-TOOL -- Multi-hop navigation shortcut

**Status**: active
**Author**: Dmitry + Computer
**Date**: 2026-05-25

---

## TL;DR

The v2 design locked in `neighbors` as the sole multi-hop primitive, requiring the agent to call it in a loop. In practice, agents drown during tracing: fan-out explosion (each CALLS hop produces 5-10 edges), no visited set (LLMs revisit nodes, follow cycles), low-signal edges dominate (getters, logging, framework plumbing), and context is consumed on traversal mechanics rather than understanding. The proposed `trace` tool is a **batched navigation shortcut** -- it does multi-hop BFS server-side in one call and returns paths/structure, not answers. The agent still interprets results. It is a sixth tool on the MCP surface, composing with the existing five.

## Problem Statement

### The drowning problem

The v2 use-case validation (MCP-API-V2-REDESIGN-PROPOSE.md section 7) identified 5 of 20 use cases as "agent-driven MCP loop" -- exactly the questions that *should* require multi-hop reasoning. The design principle was correct: the GPS returns adjacency, the agent navigates. But in production, agents fail on these loops for four structural reasons:

**1. Fan-out explosion.** A typical `CONTROLLER` method calls 5-8 `SERVICE` methods. Each of those calls 3-6 more. By depth 2, the frontier is 15-48 nodes. The agent must issue 15-48 `neighbors` calls, each returning up to 25 edges. A "trace from this route to the database" query can require 20+ tool calls before the agent sees a `REPOSITORY`.

**2. No visited set.** LLMs do not maintain a visited set in working memory. They revisit nodes, follow cycles (A calls B calls A via callback/interface), and re-traverse already-explored branches. A 4-hop trace degrades into 10-15 redundant calls.

**3. Low-signal edges dominate.** A `SERVICE` method's CALLS include getters (`getName()`), logging (`log.info()`), framework plumbing (`validate()`), and DTO construction alongside the one meaningful delegation to a `REPOSITORY`. The agent must issue a `describe` or inspect FQNs for each neighbor to filter noise, multiplying calls further.

**4. Context consumed on mechanics.** Each `neighbors` call returns `Edge` objects with full `NodeRef` payloads. After 8 calls, the agent has spent 4,000+ tokens on edge lists and has not yet started reasoning about the flow. The agent's context budget is dominated by graph-walking bookkeeping.

### Concrete example

Question: "What happens when POST /api/orders is called?"

Ideal agent behavior: 2-3 tool calls, get the full path from controller to repository.

Actual agent behavior today:
1. `find(kind="route", filter={path_prefix: "/api/orders", http_method: "POST"})` -- 1 call
2. `neighbors(route_id, "in", ["EXPOSES"])` -- get handler method -- 1 call
3. `neighbors(handler_id, "out", ["CALLS"])` -- returns 8 edges, 3 are noise -- 1 call
4. Agent inspects each callee, describes 3 noise nodes, re-calls neighbors on the real service -- 3-4 calls
5. `neighbors(service_id, "out", ["CALLS"])` -- returns 6 edges, 2 are noise -- 1 call
6. Agent continues filtering... -- 2-3 more calls
7. Total: 10-13 calls, agent is confused about which path matters

With `trace`:
1. `find(kind="route", filter={...})` -- 1 call
2. `trace(route_id, "out", ["EXPOSES", "CALLS"], max_depth=4)` -- 1 call, returns pruned path tree
3. Total: 2 calls, agent sees the full path structure and reasons about it

## Proposed Solution

### Signature

```yaml
trace(
  ids: str | list[str],              # seed node ids (single string normalized to list; echoed as seed_ids)
  direction: Literal["in", "out"],   # REQUIRED -- no default (same discipline as neighbors)
  edge_types: list[EdgeType],        # REQUIRED -- stored edge labels only (no composed dot-keys)
  max_depth: int = 3,                # max BFS hops (clamped to 1..5)
  max_paths: int = 20,               # max paths/edges to return (hard cap on result size)
  max_nodes_discovered: int = 500,   # hard budget on nodes discovered before pruning (clamped 100..2000)
  filter?: NodeFilter,               # filter on discovered nodes (same schema as neighbors)
  edge_filter?: EdgeFilter,          # edge attribute filtering (CALLS only, same as neighbors)
  prune_roles?: list[str],           # roles to prune from traversal (e.g. ["DTO", "EXCEPTION", "UTILITY"])
  fan_out_cap?: int = 5,             # per-node fan-out limit: if a node has >N edges, keep only top-K
  collapse_trivial?: bool = True,    # collapse wrapper chains (A.calls(B).calls(C) where B is trivial)
  include_unresolved?: bool = False,  # include UnresolvedCallSite edges (CALLS out only)
) -> TraceOutput
```

### Result format

```yaml
TraceOutput:
  success: bool
  seed_ids: list[str]                  # echoed from request
  direction: str                       # echoed
  edge_types: list[str]                # echoed
  actual_depth: int                    # depth actually traversed (may be < max_depth if frontier exhausted)
  nodes: dict[str, NodeRef]            # id -> NodeRef for all discovered nodes
  edges: list[TraceEdge]               # the filtered edge set
  paths: list[TracePath]               # ranked root-to-leaf paths (up to max_paths)
  stats: TraceStats                    # traversal statistics
  message: str | None
  advisories: list[str]
  hints_structured: list[StructuredHint]

TraceEdge:
  from_id: str
  to_id: str
  edge_type: str
  hop: int                             # BFS depth where discovered (0-indexed from seeds)
  collapsed: bool = False              # true if this edge was produced by collapsing a trivial chain
  collapsed_intermediates: list[str]   # node IDs of collapsed intermediates (empty if not collapsed)
  attrs: dict[str, Any]               # edge attributes (confidence, strategy, match, etc.)

TracePath:
  edges: list[TraceEdge]               # ordered root-to-leaf edges
  leaf: NodeRef                        # terminal node
  leaf_role: str | None                # leaf node role (for quick filtering)

TraceStats:
  total_nodes_discovered: int          # before pruning
  total_edges_discovered: int          # before pruning
  budget_hit: bool                     # true if BFS stopped early due to max_nodes_discovered
  budget_limit: int                    # the max_nodes_discovered value used
  nodes_pruned_role: int               # nodes dropped by prune_roles
  nodes_pruned_fan_out: int            # nodes dropped by fan_out_cap
  edges_collapsed_trivial: int         # edges merged by collapse_trivial
  nodes_after_pruning: int             # final count in result
  edges_after_pruning: int             # final count in result
```

### Core algorithm

The trace engine is a **BFS traversal** that reuses the same Cypher query infrastructure as `neighbors_v2` (via `KuzuGraph.neighbor_calls_for_symbol` and the generic label-predicate match in `mcp_v2.py`). It runs server-side as a single blocking call.

```
1. Initialize frontier = seed_ids, visited = {seed_ids}, total_discovered = 0
2. For hop in range(max_depth):
   a. If total_discovered >= max_nodes_discovered:
      - Set stats.budget_hit = True, add advisory to result
      - Break loop
   b. For each node in frontier:
      - Query neighbors via existing KuzuGraph methods
      - Apply edge_filter pushdown (min_confidence, strategies, callee_declaring_role)
      - Apply NodeFilter on discovered nodes
      - Apply prune_roles: skip nodes whose role is in prune_roles
      - Apply fan_out_cap: if node has >fan_out_cap edges, keep top-K by:
        - Primary sort: confidence (highest first)
        - Tiebreaker: role priority (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER)
        - For structural edges without confidence: alphabetically by FQN (deterministic)
      - total_discovered += len(discovered neighbors)
      - Record TraceEdge(from=node, to=neighbor, hop=hop, attrs=...)
   c. new_frontier = {neighbor.id for each discovered neighbor not in visited}
   d. visited |= new_frontier
   e. frontier = new_frontier
3. If collapse_trivial:
   - Identify chains where intermediate node B has exactly 1 inbound and 1 outbound CALLS edge,
     and B's role is OTHER or its declaring class role is SERVICE/COMPONENT
   - Merge: edge A->B->C becomes A->C with attrs from the lower-confidence edge
   - Set collapsed=True and collapsed_intermediates=[B.id] on the merged edge
   - Remove intermediate nodes from nodes dict
   - Record stats.edges_collapsed_trivial
4. Build paths: enumerate root-to-leaf paths through the DAG
   - Rank by: (a) leaf role priority (CONTROLLER > SERVICE > REPOSITORY > ...),
     (b) path confidence (min edge confidence), (c) path length (shorter first)
   - Cap at max_paths
5. Collect nodes dict, edges list, paths list, stats
6. Return TraceOutput
```

### Server-side pruning: the key differentiator

The `trace` tool's value is not "do what the agent could do but faster" -- it is **server-side pruning** that the agent cannot replicate without issuing dozens of tool calls.

**Role-based pruning (`prune_roles`)**: Nodes with roles like `DTO`, `EXCEPTION`, `UTILITY`, `OTHER` rarely carry meaningful traversal signal. A `SERVICE` method that calls `OrderDto#setTotal()` followed by `OrderRepository#save()` has one high-signal edge and one low-signal edge. Pruning DTOs at traversal time means the agent never sees the noise.

**Fan-out throttling (`fan_out_cap`)**: When a node has 30 outgoing CALLS edges, the agent would have to inspect all 30 to find the 3 that matter. Fan-out cap keeps only the top-K — sorted by confidence (primary) with role priority as tiebreaker (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER) — so the traversal stays focused. The existing `EdgeFilter.callee_declaring_role` already lets agents pre-filter by role; the ranking does not duplicate that filter. The `stats` object reports how many edges were cut so the agent knows the cap fired. **Known v1 trade-off**: the static role tiebreaker can produce counterintuitive results — e.g., from a SERVICE node, a CONTROLLER callee ties ahead of a REPOSITORY callee at equal confidence. The full `edges` list is available for client-side re-ranking. Making priority relative to the source node's role is deferred to #240.

**Trivial chain collapsing (`collapse_trivial`)**: Wrapper/delegate patterns are common in Spring microservices. `OrderServiceImpl#createOrder` calls `orderValidator#validate` calls `ValidationHelper#doValidate` calls `RulesEngine#check`. The intermediate wrapper and helper add no semantic value. Collapsing these into `OrderServiceImpl -> RulesEngine` shortens paths and keeps the agent focused on the real flow.

**Cross-service seamless traversal**: When BFS encounters a `Symbol` with outgoing `DECLARES_CLIENT` or `DECLARES_PRODUCER`, and `HTTP_CALLS`/`ASYNC_CALLS` is in the requested `edge_types`, the engine follows through: `method -> Client -> HTTP_CALLS -> Route -> EXPOSES <- handler method`. This is a 4-hop traversal across service boundaries that would require 4 separate `neighbors` calls today. The engine follows it in one step because it has the full graph in Kuzu. Cross-service edges carry `confidence`, `strategy`, and `match` attributes so the agent can assess reliability.

### Edge type handling

`trace` accepts only **stored edge labels** (the 11 labels in `_EDGE_TYPES`). No composed dot-keys -- the engine handles multi-hop traversal internally. If the agent wants to trace from a type Symbol through its members, it passes `["DECLARES", "CALLS"]` and the engine does the 2-hop traversal automatically.

The engine expands `edge_types` into traversal predicates using the same OR-of-scalar-equalities pattern as `neighbors_v2`:

```python
# Same pattern as mcp_v2.py line 1872
label_params = [f"l{i}" for i in range(len(flat_labels))]
label_predicate = "(" + " OR ".join(f"label(e) = ${name}" for name in label_params) + ")"
```

Cross-service traversal is implicit: when `HTTP_CALLS` or `ASYNC_CALLS` is in `edge_types`, the engine follows the full chain through Client/Producer nodes and Route nodes, collecting the intermediate edges as part of the same BFS.

### Composability

`trace` composes with the existing tools:

1. **locate** via `search` or `find` (same as today)
2. **trace** via `trace` (new -- gets the multi-hop structure)
3. **inspect** via `describe` on any node in the trace result (follow-up detail)
4. **drill** via `neighbors` on any node in the trace result (one-hop detail the trace skipped or pruned)
5. **resolve** for identifier-shaped lookups before tracing

The `nodes` dict in `TraceOutput` contains lightweight `NodeRef` objects (id, kind, fqn, role). For deeper inspection, the agent calls `describe(id)` on specific nodes. This preserves the GPS metaphor: `trace` maps the route, `describe` and `neighbors` provide street-level detail.

### Depth and budget control

- `max_depth` defaults to **3** and is clamped to 1..5.
- Depth 1 is equivalent to `neighbors` (no multi-hop benefit, but allows the pruning engine).
- Depth 3 covers most practical traces: controller -> service -> repository, or route -> handler -> client -> downstream route.
- Depth 5 is available for deep impact analysis but produces large results; the `max_paths` cap prevents runaway output.
- The engine stops early if the frontier is exhausted before `max_depth`.
- `max_nodes_discovered` defaults to **500** and is clamped to 100..2000. This is a **compute guardrail**, not an output guarantee. It counts nodes discovered *before* pruning — this is intentional because the cost is in the Cypher queries and BFS traversal, not in the output serialization. Aggressive `prune_roles` may result in fewer output nodes for the same budget. When the budget is hit, BFS stops mid-traversal and reports `stats.budget_hit = True` plus an advisory: `"trace stopped early: discovered {N} of ~{M} nodes before budget. Reduce max_depth or add prune_roles to focus."`

### Cross-service traversal

When `HTTP_CALLS` or `ASYNC_CALLS` is in `edge_types`, the BFS engine follows cross-service edges seamlessly:

1. At a `Symbol` node with `DECLARES_CLIENT` -> `Client` -> `HTTP_CALLS` -> `Route`, the engine records:
   - `Symbol --DECLARES_CLIENT--> Client` (hop N)
   - `Client --HTTP_CALLS--> Route` (hop N+1)
   - The Route's `EXPOSES` handler is discovered at hop N+2 (if `EXPOSES` is in `edge_types`)
2. Cross-service edges carry their full attribute set: `confidence`, `strategy`, `match`, `raw_uri`/`raw_topic`.
3. The `stats` object reports cross-service hops separately so the agent knows when it crossed a service boundary.

This is the highest-value feature of `trace`: a 4-hop cross-service trace that would require 8-12 `neighbors` calls today is a single `trace` call.

#### Cross-service edge-following rules

The engine follows cross-service edges when `HTTP_CALLS` or `ASYNC_CALLS` is in the user's `edge_types`. Internally, it also follows **scaffolding edges** that connect the user's edge types across node kinds. These scaffolding edges are *not* required to be in `edge_types` — the engine follows them automatically:

| Trigger (user-specified `edge_types` includes) | Scaffolding edges followed internally | Target node kind |
|---|---|---|
| `HTTP_CALLS` | `DECLARES_CLIENT` (outbound from Symbol to Client) | `client` |
| `HTTP_CALLS` | `EXPOSES` (inbound from Route to handler Symbol) | `symbol` |
| `ASYNC_CALLS` | `DECLARES_PRODUCER` (outbound from Symbol to Producer) | `producer` |
| `ASYNC_CALLS` | `EXPOSES` (inbound from Route to handler Symbol) | `symbol` |

**All scaffolding edges appear in the result's `edges` list** with their actual edge type (e.g., `DECLARES_CLIENT`, `EXPOSES`). They are not hidden from the agent. They count toward `max_nodes_discovered` and `stats.total_edges_discovered` like any other edge.

**`hop` numbering**: Scaffolding edges consume hop slots. A `Symbol --DECLARES_CLIENT(hop 2)--> Client --HTTP_CALLS(hop 3)--> Route` sequence uses two hops, not one. This means cross-service traces reach `max_depth` faster — the agent should account for this when choosing depth. The advisory system warns when a cross-service boundary was detected but `max_depth` was exhausted before the downstream handler was reached.

**Example**: Agent calls `trace(id, "out", ["CALLS", "HTTP_CALLS"], max_depth=4)`:
- Hop 0: seed Symbol —CALLS--> callee Symbols
- Hop 1: callee Symbols —CALLS--> deeper Symbols (some have DECLARES_CLIENT)
- Hop 2: Symbol —DECLARES_CLIENT--> Client (scaffolding, auto-followed)
- Hop 3: Client —HTTP_CALLS--> Route (user-requested edge type)
- Hop 4 would be needed for Route —EXPOSES--> handler, but `max_depth=4` is exhausted. Advisory: `"cross-service boundary detected at hop 3 but max_depth=4 exhausted before downstream handler. Increase max_depth to 5 or use neighbors on the discovered Route."`

## Scope

### What this proposal changes

1. **New MCP tool**: `trace` registered in `server.py` alongside `search`, `find`, `describe`, `neighbors`, `resolve`.
2. **New module**: `mcp_trace.py` containing the BFS engine, pruning logic, and output types.
3. **No graph schema changes**: The engine reads the existing Kuzu graph. No new node kinds, edge types, or edge attributes.
4. **No re-index required**: The tool operates on the existing graph structure.
5. **No ontology bump**: No changes to `java_ontology.py`.

### What this proposal does NOT change

- `neighbors` remains the one-hop primitive. `trace` is optional; agents that reason well over multi-hop can still use `neighbors` loops.
- `search`, `find`, `describe`, `resolve` are untouched.
- `kuzu_queries.py` is not modified (trace reuses its existing query methods).
- `mcp_v2.py` is not modified (trace reuses `NodeFilter`, `EdgeFilter`, `Edge`, `NodeRef` types).
- No changes to the indexer, graph builder, or CLI.

## Schema / Ontology / Re-index impact

- **Ontology bump**: None. No new edge types or node kinds.
- **Re-index required**: No. The tool reads the existing graph.
- **Config/tool surface changes**: One new `trace` tool registration in `server.py`. The `mcp_trace.py` module is additive.
- **MCP surface**: 6 tools total (was 5).

## Tests / Validation

### Unit tests (`tests/test_mcp_trace.py`)

| Test name | Asserts |
|-----------|---------|
| `test_trace_outbound_calls_depth_2` | Traces from a controller method via CALLS out, depth 2, returns edges at hop 0 and hop 1 |
| `test_trace_inbound_callers_depth_2` | Traces from a repository method via CALLS in, depth 2, returns caller chain |
| `test_trace_prune_roles` | With `prune_roles=["DTO"]`, DTO nodes are excluded from traversal |
| `test_trace_fan_out_cap` | With `fan_out_cap=2`, a node with 8 outbound CALLS returns at most 2 edges |
| `test_trace_collapse_trivial` | Wrapper chain A->B->C where B has degree 2 is collapsed to A->C |
| `test_trace_cross_service_http` | Traces from a method through DECLARES_CLIENT -> HTTP_CALLS -> Route -> EXPOSES handler across services |
| `test_trace_cross_service_async` | Same for ASYNC_CALLS through Producer |
| `test_trace_max_paths_cap` | Result paths list does not exceed `max_paths` |
| `test_trace_budget_stops_early` | BFS stops when `max_nodes_discovered` is hit; `stats.budget_hit=True`; advisory message present |
| `test_trace_depth_1_equivalent_to_neighbors` | Depth 1 trace with no pruning returns same nodes as `neighbors` |
| `test_trace_stats_counts` | `stats.total_nodes_discovered`, `stats.nodes_pruned_role`, etc. are consistent with the edge set |
| `test_trace_empty_seed` | Empty seed ids returns `success=True, nodes={}, edges=[], paths=[]` |
| `test_trace_invalid_edge_type` | Unknown edge type returns `success=False` with teaching message |
| `test_trace_direction_required` | Missing direction returns `success=False` |
| `test_trace_edge_types_required` | Empty edge_types returns `success=False` |
| `test_trace_visited_set_no_cycles` | BFS does not revisit nodes even if cycles exist in the graph |
| `test_trace_filter_applied` | NodeFilter restricts discovered nodes |
| `test_trace_edge_filter_calls` | EdgeFilter with `min_confidence` filters CALLS edges during traversal |
| `test_trace_include_unresolved` | UnresolvedCallSite edges are interleaved when `include_unresolved=True, edge_types=["CALLS"], direction="out"` |
| `test_trace_paths_root_to_leaf` | Each path starts at a seed and ends at a leaf with no further outbound edges in the result |
| `test_trace_cross_service_edge_attrs` | Cross-service edges include `confidence`, `strategy`, `match` attributes |

### Integration validation

- Run `trace` against `tests/bank-chat-system` fixture for representative flows.
- Compare `trace` output against equivalent `neighbors` loop results to verify structural correctness.
- Verify `trace` call latency is under 500ms for depth 3 on the fixture graph.

### Regression

- Existing tool tests (`test_mcp_v2.py`, `test_server.py`) must pass unchanged.
- `ruff check` clean on all new code.

## Resolved Decisions

These questions were resolved during review (PR #234). Deferred items are tracked as follow-up issues.

1. **`collapse_trivial` heuristic** — **Degree-1, no configurability.** The heuristic (1 in + 1 out in the result set, role is OTHER or declaring-class role is SERVICE/COMPONENT) is conservative enough for v1. No `trivial_chain_min_length` parameter. If production data shows false positives or missed chains, a richer heuristic will be proposed in #240.

2. **`fan_out_cap` ranking** — **Confidence primary, role as tiebreaker.** Pure role-based ranking would deprioritize cross-cutting concerns (logging, security, metrics) that have low-confidence edges but are architecturally important. The existing `EdgeFilter.callee_declaring_role` already provides role filtering; the ranking should not duplicate it. Tiebreaker: role priority (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER).

3. **Bidirectional traversal** — **No for v1. Agent issues two calls.** Bidirectional BFS would require specifying which edge types follow which direction, turning the signature into a mini query language. Two unidirectional `trace` calls + client-side merge is simpler and composable. If the two-call pattern proves pervasive in production, add `"both"` direction — tracked in #240.

4. **Path ranking** — **Fixed ranking for v1.** Leaf role priority > min path confidence > path length (shorter first). The `max_paths` cap and full `edges` list in the output let agents re-rank client-side. If real usage shows consistent client-side re-ranking, a `rank_by` parameter can be added — tracked in #240.

5. **Memory/cost budget** — **Hard `max_nodes_discovered` budget.** Default 500, clamped to 100–2000. A depth-5 trace on a large codebase can discover thousands of nodes before pruning. The BFS stops early when the budget is hit and reports it via `stats.budget_hit` + an advisory message. The `max_paths` cap limits output but not computation — the budget is the safety net.

6. **Legacy `find_callers`/`find_callees`** — **Coexist for v1.** These methods serve the CLI, not the MCP surface — different consumers. Once the trace engine is proven on MCP, add a `java-codebase-rag trace` CLI command reusing the engine, then deprecate legacy methods — tracked in #241.

7. **Cross-service scaffolding edges** — **Always followed when `HTTP_CALLS`/`ASYNC_CALLS` is in `edge_types`, always visible in output.** Scaffolding edges (`DECLARES_CLIENT`, `DECLARES_PRODUCER`, `EXPOSES`) do not need to be in the user's `edge_types`. They appear in the `edges` list with their actual edge type. They consume `hop` slots. Advisories fire when `max_depth` is exhausted mid-cross-service traversal.

8. **`collapsed` marker on TraceEdge** — **Yes.** Collapsed edges carry `collapsed: True` and `collapsed_intermediates: [node_ids]` so agents can detect shortcuts and drill down via `neighbors`.

9. **`max_nodes_discovered` counts pre-pruning** — **Intentional.** The budget is a compute guardrail limiting Cypher queries and BFS traversal cost, not an output size guarantee. The intent is documented explicitly in the Depth and budget control section.

10. **PR-TRACE-1 split** — **Split into PR-TRACE-1a (core BFS + budget + paths) and PR-TRACE-1b (pruning + collapsing + cross-service).** Core BFS correctness and pruning heuristics are different review surfaces.

### Follow-up issues

- **#240** — trace tool: v2 enhancements (bidirectional traversal, richer collapse_trivial heuristic, configurable path ranking, configurable fan_out_cap ranking)
- **#241** — trace tool: CLI integration and legacy method deprecation

## Out of scope

- **Answer engine.** `trace` returns paths and structure. It does not synthesize natural-language answers or recommendations.
- **Semantic ranking.** `trace` does not rank paths by semantic similarity to a query. It ranks by structural metrics (confidence, role, length).
- **Graph schema changes.** No new node kinds, edge types, or edge attributes.
- **Indexer changes.** No changes to `build_ast_graph.py` or the indexing pipeline.
- **Replacing `neighbors`.** `neighbors` remains the one-hop primitive. `trace` is a higher-level convenience for multi-hop patterns.
- **Visualization.** `trace` returns structured data. Rendering as a diagram, tree, or flowchart is the agent's job.
- **Composed edge types as input.** `trace` accepts only stored edge labels. Composed traversal (e.g., DECLARES.DECLARES_CLIENT) is handled internally by the BFS engine when the agent passes `["DECLARES", "DECLARES_CLIENT"]`.

## Sequencing / Follow-ups

### PR-TRACE-1a -- `mcp_trace.py` core BFS engine

- Implement `TraceOutput`, `TraceEdge`, `TracePath`, `TraceStats` models.
- Implement BFS traversal with visited set, edge type expansion, NodeFilter/EdgeFilter integration.
- Implement `max_nodes_discovered` budget with early-stop and advisory.
- Implement path enumeration and ranking.
- **Tests**: `test_trace_outbound_calls_depth_2`, `test_trace_inbound_callers_depth_2`, `test_trace_max_paths_cap`, `test_trace_budget_stops_early`, `test_trace_depth_1_equivalent_to_neighbors`, `test_trace_stats_counts`, `test_trace_empty_seed`, `test_trace_invalid_edge_type`, `test_trace_direction_required`, `test_trace_edge_types_required`, `test_trace_visited_set_no_cycles`, `test_trace_filter_applied`, `test_trace_edge_filter_calls`, `test_trace_include_unresolved`, `test_trace_paths_root_to_leaf`.

### PR-TRACE-1b -- pruning, collapsing, and cross-service

- Implement role-based pruning (`prune_roles`).
- Implement fan-out throttling (`fan_out_cap`) with confidence-based ranking + role tiebreaker.
- Implement trivial chain collapsing (`collapse_trivial`) with `collapsed`/`collapsed_intermediates` markers on TraceEdge.
- Implement cross-service traversal (HTTP_CALLS, ASYNC_CALLS seamless hop-through) per the scaffolding edge rules.
- **Tests**: `test_trace_prune_roles`, `test_trace_fan_out_cap`, `test_trace_collapse_trivial`, `test_trace_cross_service_http`, `test_trace_cross_service_async`, `test_trace_cross_service_edge_attrs`.

### PR-TRACE-2 -- `server.py` tool registration

- Register `trace` tool in `create_mcp_server()` with description and parameter schema.
- Wire to `mcp_trace.trace_v2()` via `asyncio.to_thread`.
- Update `_INSTRUCTIONS` string to mention the sixth tool.
- **Tests**: tool registration test, end-to-end trace call through MCP.

### PR-TRACE-3 -- cross-service integration + hints

- Cross-service integration tests against `tests/bank-chat-system`.
- Add `generate_hints("trace", ...)` road signs in `mcp_hints.py`:
  - When trace discovers cross-service edges, hint to `describe` the downstream route.
  - When trace hits the fan-out cap, advisory text noting pruning occurred and suggesting `neighbors` for full detail.
- Update `skills/explore-codebase/` to document the `trace` tool.
- **Tests**: cross-service tests, hint generation tests.

### PR-TRACE-4 -- README and documentation

- Update `README.md` tool reference with `trace` description and examples.
- Update `docs/CONFIGURATION.md` if any config surface changes.
- Move this proposal to `propose/completed/`.

---

## Appendix A -- Justification for overriding the v2 "no trace tools" decision

The v2 design (section 2, decision 2) states: "No `trace_*` tools. The agent walks via `neighbors` in a loop and decides its own stop condition." This was the correct design at the time given the constraints. The `trace` tool proposed here does **not** violate the v2 design principles. Here is the point-by-point justification:

| v2 Principle | How `trace` Respects It |
|---|---|
| "The GPS does not tell you where to go." | `trace` returns **paths** (structure), not answers. It tells you "these roads exist between here and there." The agent still decides what the path means. |
| "Edges over nodes." | `trace` returns edges with full attributes, same contract as `neighbors`. Cross-service edges carry `confidence`, `strategy`, `match`. |
| "Required-by-default for hot params." | `direction` and `edge_types` are required on `trace`, same discipline as `neighbors`. |
| "Small defaults." | `max_paths=20`, `max_depth=3`, `fan_out_cap=5`, `max_nodes_discovered=500`. Every parameter has a conservative default. |
| "No magic." | `trace` does not "figure out the strategy." The agent specifies direction, edge types, depth, and pruning. The engine does BFS, not reasoning. |
| "Optional." | `trace` is additive. `neighbors` loops still work. Agents that can do multi-hop reasoning are not forced to use `trace`. |

The key difference from the rejected v1 `trace_flow` tool: `trace_flow` was a **stage-based role waterfall** with hardcoded CONTROLLER -> SERVICE -> REPOSITORY progression. It encoded the agent's intent into the tool. The proposed `trace` is a **generic BFS shortcut** with the same edge-type-aware contract as `neighbors`. It does not encode intent; it batches mechanics.

What changed between v2 design (2026-05-07) and now (2026-05-25): production experience with the 5-tool surface on real microservice codebases showed that the `neighbors` loop approach works for 80% of queries (1-2 hop) but degrades severely for the remaining 20% (3+ hops, cross-service). The agent drowning pattern is consistent and reproducible. A batched shortcut that preserves the GPS metaphor is the right fix.

## Appendix B -- Comparison with `neighbors` loop

| Aspect | `neighbors` loop | `trace` |
|---|---|---|
| Calls for a 3-hop trace | 3-8 tool calls | 1 tool call |
| Visited set | Agent's responsibility (LLMs are bad at this) | Server-side (deterministic) |
| Fan-out control | Agent must filter manually | `fan_out_cap`, `prune_roles` |
| Cross-service | Multiple calls per boundary | Seamless in one call |
| Trivial chain collapsing | Agent must detect and skip | `collapse_trivial` |
| Result structure | Flat edge lists per call | Structured paths + nodes dict |
| Context budget | High (each call returns full payloads) | Low (pruned, deduplicated) |
| Granularity | Full control per hop | Pruning may hide edges agent wants |
| Flexibility | Can change strategy per hop | Fixed strategy for entire trace |

The trade-off is clear: `trace` sacrifices per-hop control for efficiency. Agents that need per-hop reasoning should use `neighbors`. Agents that need a multi-hop overview should use `trace`.
