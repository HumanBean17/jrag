# TRACE-TOOL -- Multi-hop navigation shortcut

**Status**: active (experimental)
**Target branch**: `experimental` — not `master`. `trace` is an experiment; it ships on a dedicated branch and is not merged to `master` until validated in production.
**Author**: Dmitry + Computer
**Date**: 2026-05-25

---

## TL;DR

The v2 design locked in `neighbors` as the sole multi-hop primitive, requiring the agent to call it in a loop. In practice, agents drown during tracing: fan-out explosion (each CALLS hop produces 5-10 edges), no visited set (LLMs revisit nodes, follow cycles), low-signal edges dominate (getters, logging, framework plumbing), and context is consumed on traversal mechanics rather than understanding. The proposed `trace` tool is a **batched navigation shortcut** -- it does multi-hop BFS server-side in one call and returns paths/structure, not answers. The agent still interprets results. It is a sixth tool on the MCP surface, composing with the existing five.

**This is an experiment.** The tool ships on the `experimental` branch, not `master`. It will be merged to `master` only after validation: agents using `trace` must produce measurably better results on multi-hop questions than agents using `neighbors` loops, with no regression on single-hop queries. The validation criteria are defined in the "Experimental validation" section below.

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
  ids: str | list[str],              # seed node ids (single string normalized to list; echoed as seed_ids).
                                     # Differs from neighbors (single ID) — trace supports multi-seed for impact analysis.
  direction: Literal["in", "out"],   # REQUIRED -- no default (same discipline as neighbors)
  edge_types: list[EdgeType],        # REQUIRED -- stored edge labels only (no composed dot-keys)
  max_depth: int = 3,                # max BFS hops (clamped to 1..5)
  max_paths: int = 20,               # max paths/edges to return (hard cap on result size)
  max_nodes_discovered: int = 500,   # hard budget on nodes discovered before pruning (clamped 100..2000)
  filter?: NodeFilter,               # hard gate on discovered nodes (excluded entirely if failing)
  edge_filter?: EdgeFilter,          # edge attribute filtering (CALLS only, same as neighbors)
  prune_roles?: list[str],           # soft gate: edges recorded but frontier stops through these roles
  fan_out_cap?: int = 5,             # per-node fan-out limit: if a node has >N edges, keep only top-K
                                     # scaffolding edges (DECLARES_CLIENT/PRODUCER) are exempt from cap
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
  parent_edge_id: str | None           # ID of the incoming edge to from_id; null for seed edges. Enables tree reconstruction.
  collapsed: bool = False              # true if this edge was produced by collapsing a trivial chain
  collapsed_intermediates: list[str]   # node IDs of collapsed intermediates (empty if not collapsed)
  cross_service_boundary: bool = False # true if this edge crosses into another microservice (BFS stops here)
  attrs: dict[str, Any]               # edge attributes (confidence, strategy, match, etc.)

TracePath:
  edges: list[TraceEdge]               # ordered root-to-leaf edges
  leaf: NodeRef                        # terminal node (role available via leaf.role)

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

The trace engine is a **BFS traversal** that reuses the same Cypher query infrastructure as `neighbors_v2` (imports but does not modify types from `mcp_v2.py`). It runs server-side as a single blocking call.

**Query strategy: batched per hop.** The engine does NOT issue one Cypher query per frontier node — that would be O(frontier_size) queries per hop, blowing the latency target. Instead, it issues a single batched query per hop:

```cypher
MATCH (n)-[e]-(m)
WHERE n.id IN $frontier_ids
  AND (<edge label OR-predicate>)
RETURN n.id, e, m
```

This is one round-trip to Kuzu per hop, regardless of frontier size. The batched query is a new method on `KuzuGraph` (e.g., `neighbors_batched`), not a modification to existing `neighbors_v2` code. Frontier IDs are bound as a list parameter.

```
1. Initialize frontier = seed_ids, visited = {seed_ids}, total_discovered = 0,
   edge_id_map = {}  # maps edge_id -> TraceEdge (for parent_edge_id lookup)
2. For hop in range(max_depth):
   a. If total_discovered >= max_nodes_discovered:
      - Set stats.budget_hit = True, add advisory to result
      - Break loop
   b. Issue single batched Cypher query for all frontier nodes
      - Apply edge_filter pushdown (min_confidence, strategies, callee_declaring_role)
      - Group results by source node
   c. For each source node's discovered edges:
      - Apply NodeFilter (hard gate): nodes failing NodeFilter are excluded entirely
        (not in nodes dict, no edges recorded). NodeFilter is structural — kind, fqn,
        microservice, include/exclude roles.
      - Apply prune_roles (soft gate): nodes whose role is in prune_roles are NOT added
        to the next frontier (BFS doesn't traverse through them), but their edges ARE
        recorded in the result. The agent can see that a DTO was called but the trace
        doesn't continue through it.
      - Apply fan_out_cap: if node has >fan_out_cap candidate edges, keep top-K by:
        - Primary sort: confidence (highest first)
        - Tiebreaker: role priority (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER)
        - For structural edges without confidence: alphabetically by FQN (deterministic)
        - Scaffolding edges (DECLARES_CLIENT, DECLARES_PRODUCER) are EXEMPT from
          fan_out_cap — they are traversal infrastructure, not signal
      - For cross-service edges (HTTP_CALLS, ASYNC_CALLS):
        - Mark cross_service_boundary = True
        - Do NOT add downstream node to frontier (BFS stops at boundary)
      - For scaffolding edges (DECLARES_CLIENT, DECLARES_PRODUCER):
        - Only followed when HTTP_CALLS/ASYNC_CALLS is in edge_types
        - Add Client/Producer node to frontier (needed to reach cross-service edge)
        - Exempt from fan_out_cap (see above)
      - For multi-edge-type traversal (e.g., ["DECLARES", "CALLS"]):
        - Each edge type is queried in the same batched query
        - DECLARES from a type Symbol reaches its member methods; CALLS from those
          methods continues the trace. The engine handles 2-hop expansion internally
          — the agent does not need to issue separate calls per edge type.
      - total_discovered += len(discovered neighbors)
      - Record TraceEdge(from=node, to=neighbor, hop=hop,
        parent_edge_id=incoming_edge_id_for_node or None, attrs=...)
        - parent_edge_id: the ID of the edge that brought BFS to `node`
          (null for seed nodes at hop 0)
        - Store in edge_id_map for post-collapse consistency
   d. new_frontier = {neighbor.id for each discovered neighbor not in visited,
                      excluding cross-service boundary downstream nodes,
                      excluding prune_roles nodes}
   e. visited |= new_frontier
   f. frontier = new_frontier
3. If collapse_trivial:
   - Identify chains where intermediate node B has exactly 1 inbound and 1 outbound CALLS edge,
     and B's role is OTHER or its declaring class role is SERVICE/COMPONENT
   - Merge: edge A->B->C becomes A->C with attrs from the lower-confidence edge
   - Set collapsed=True and collapsed_intermediates=[B.id] on the merged edge
   - Remove intermediate nodes from nodes dict
   - Record stats.edges_collapsed_trivial
   - Recompute parent_edge_ids: any edge whose parent_edge_id referenced a removed edge
     is updated to reference the collapsed replacement edge. The edge_id_map is updated
     accordingly so subsequent lookups resolve correctly.
4. Build paths: enumerate root-to-leaf paths through the DAG
   - Stop enumeration after 10 × max_paths candidates (prevents exponential blowup
     on reconvergent DAGs with multiple seeds). Rank the candidates, return top max_paths.
   - Rank by: (a) leaf role priority (CONTROLLER > SERVICE > REPOSITORY > ...),
     (b) path confidence (min edge confidence), (c) path length (shorter first)
   - Cap at max_paths
5. Collect nodes dict, edges list, paths list, stats
6. Return TraceOutput
```

### Server-side pruning: the key differentiator

The `trace` tool's value is not "do what the agent could do but faster" -- it is **server-side pruning** that the agent cannot replicate without issuing dozens of tool calls.

**Role-based pruning (`prune_roles`)**: Nodes with roles like `DTO`, `EXCEPTION`, `UTILITY`, `OTHER` rarely carry meaningful traversal signal. A `SERVICE` method that calls `OrderDto#setTotal()` followed by `OrderRepository#save()` has one high-signal edge and one low-signal edge. Pruning DTOs means the agent sees the edge (the DTO was called) but the trace doesn't continue through it — the agent can still `neighbors` into the DTO if needed. This is the "soft gate" distinction from `NodeFilter`: `NodeFilter` is a hard gate (failing nodes are excluded entirely), `prune_roles` is a soft gate (failing nodes' edges are recorded but BFS doesn't traverse further through them).

**Fan-out throttling (`fan_out_cap`)**: When a node has 30 outgoing CALLS edges, the agent would have to inspect all 30 to find the 3 that matter. Fan-out cap keeps only the top-K — sorted by confidence (primary) with role priority as tiebreaker (CONTROLLER > SERVICE > REPOSITORY > CLIENT > OTHER) — so the traversal stays focused. The existing `EdgeFilter.callee_declaring_role` already lets agents pre-filter by role; the ranking does not duplicate that filter. The `stats` object reports how many edges were cut so the agent knows the cap fired. **Known v1 trade-off**: the static role tiebreaker can produce counterintuitive results — e.g., from a SERVICE node, a CONTROLLER callee ties ahead of a REPOSITORY callee at equal confidence. The full `edges` list is available for client-side re-ranking. Making priority relative to the source node's role is deferred to #240.

**Trivial chain collapsing (`collapse_trivial`)**: Wrapper/delegate patterns are common in Spring microservices. `OrderServiceImpl#createOrder` calls `orderValidator#validate` calls `ValidationHelper#doValidate` calls `RulesEngine#check`. The intermediate wrapper and helper add no semantic value. Collapsing these into `OrderServiceImpl -> RulesEngine` shortens paths and keeps the agent focused on the real flow.

**Cross-service boundary detection**: When BFS encounters a `Symbol` with outgoing `DECLARES_CLIENT` or `DECLARES_PRODUCER`, and `HTTP_CALLS`/`ASYNC_CALLS` is in the requested `edge_types`, the engine follows to the Client/Producer and then to the downstream Route — but **stops at the boundary**. The cross-service edge is recorded with `cross_service_boundary: True` and full attributes (`confidence`, `strategy`, `match`). The downstream Route is included in the `nodes` dict so the agent can see what service and endpoint is being called. The agent decides whether to continue tracing in the downstream service via a separate `trace` call.

### Edge type handling

`trace` accepts only **stored edge labels** (the 11 labels in `_EDGE_TYPES`). No composed dot-keys -- the engine handles multi-hop traversal internally. If the agent wants to trace from a type Symbol through its members, it passes `["DECLARES", "CALLS"]` and the engine does the 2-hop traversal automatically.

The engine expands `edge_types` into traversal predicates using the same OR-of-scalar-equalities pattern as `neighbors_v2`:

```python
# Same pattern as mcp_v2.py line 1872
label_params = [f"l{i}" for i in range(len(flat_labels))]
label_predicate = "(" + " OR ".join(f"label(e) = ${name}" for name in label_params) + ")"
```

Cross-service traversal is a boundary signal: when `HTTP_CALLS` or `ASYNC_CALLS` is in `edge_types`, the engine follows scaffolding edges to reach the cross-service edge, records it with `cross_service_boundary: True`, and stops. The downstream service is not traversed — the agent issues a separate `trace` call if needed.

### Composability

`trace` composes with the existing tools:

1. **locate** via `search` or `find` (same as today)
2. **trace** via `trace` (new -- gets the multi-hop structure)
3. **inspect** via `describe` on any node in the trace result (follow-up detail)
4. **drill** via `neighbors` on any node in the trace result (one-hop detail the trace skipped or pruned)
5. **resolve** for identifier-shaped lookups before tracing

The `nodes` dict in `TraceOutput` contains lightweight `NodeRef` objects (id, kind, fqn, role). For deeper inspection, the agent calls `describe(id)` on specific nodes. This preserves the GPS metaphor: `trace` maps the route, `describe` and `neighbors` provide street-level detail.

### Agent tool selection: `trace` vs `neighbors`

When the agent has a node ID and needs to navigate, it chooses between `neighbors` (one-hop) and `trace` (multi-hop with pruning). The decision is driven by **question intent**, not hop count. The following heuristics must be reflected in `_INSTRUCTIONS`, `skills/explore-codebase/SKILL.md`, and `mcp_hints.py`.

#### Decision table

| Agent intent | Tool | Rationale |
|---|---|---|
| "What does M call?" / "Who calls M?" | `neighbors` | 1-hop adjacency, agent wants full unfiltered result |
| "What implements T?" / "Where is T injected?" | `neighbors` | 1-hop structural exploration, single edge type |
| "List all routes/controllers/clients" | `find` + `neighbors` | Enumerate + one-hop detail |
| "What happens when POST /api/orders is called?" | `trace` | Multi-hop path question: route -> handler -> service -> repository |
| "Impact of changing X" | `trace` | Multi-hop breadth-first, needs pruning to stay focused |
| "Trace from controller to database" | `trace` | Named start/end implies multi-hop path |
| "What crosses service boundaries from X?" | `trace` | Cross-service is `trace`'s highest-value feature |
| "What's the call chain from A to B?" | `trace` | Multi-hop path with named endpoints |
| After `trace` returns pruned result | `neighbors` on specific nodes | Drill into edges that `trace` collapsed or pruned |
| After `neighbors` returns high fan-out (>8 CALLS edges) | `trace` with `prune_roles` + `fan_out_cap` | Switch strategy when `neighbors` result is too noisy |
| Agent is 3+ hops into a `neighbors` loop | `trace` from original seed | Escalation: stop drowning, batch the remaining traversal |

#### Reasoning preamble update

The current forced reasoning preamble in `SKILL.md` is:
```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

With `trace`, the preamble becomes:
```
Q-class: <semantic | structured | inspect | walk | trace>
Pick: <search|find|describe|neighbors|trace|resolve>  Why: <≤8 words>
```

The `trace` Q-class applies when: (a) the question implies a path or chain, (b) the agent needs to cross a service boundary, or (c) a `neighbors` loop has exceeded 2 hops without converging.

#### Hint system updates (`mcp_hints.py`)

PR-TRACE-3 must add the following `trace`-aware hints:

1. **Neighbors high fan-out hint**: When `neighbors` returns >8 CALLS edges for a single node, emit a hint: `"High fan-out (N CALLS edges). Consider trace(id, 'out', ['CALLS'], prune_roles=['DTO','EXCEPTION','UTILITY'], fan_out_cap=5) for a pruned multi-hop view."`

2. **Neighbors loop escalation hint**: When the same session issues 3+ consecutive `neighbors` calls with the same `edge_types` and direction, emit: `"You've issued N neighbors calls with the same edge type. Consider trace(seed_id, direction, edge_types, max_depth=4) to batch the traversal."` (This requires session-level call tracking, which the MCP server does not currently have. This may need to be a client-side hint in the skill rather than server-side.)

3. **Trace result drill-down hint**: When `trace` returns edges with `collapsed=True` or `stats` shows pruning fired, emit: `"trace pruned N edges. Use neighbors(id, direction, edge_types) on specific nodes for full detail."`

4. **Trace budget hit hint**: When `stats.budget_hit=True`, emit: `"trace hit the node discovery budget (N nodes). Results are partial. Increase max_depth or add prune_roles and re-run."`

5. **Cross-service boundary hint**: When `trace` discovers edges with `cross_service_boundary=True`, emit: `"Cross-service boundary: Client X calls Route Y (confidence=N). Use trace(route_id, 'out', ['EXPOSES','CALLS'], max_depth=4) to continue in the downstream service, or describe(route_id) for route details."`

#### Skill decision tree update

The decision tree in `skills/explore-codebase/SKILL.md` must be updated to include `trace` rows:

| User asks... | First step | Typical follow-up |
|---|---|---|
| "What happens when route R is called?" | `find(kind="route")` then `trace(route_id, "out", ["EXPOSES","CALLS"], max_depth=4)` | `describe` on key nodes |
| "Impact of changing method M" | `resolve` / `find` then `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)` | `describe` on callers |
| "Trace from X to database" | `trace(id, "out", ["CALLS"], max_depth=4, prune_roles=["DTO","EXCEPTION"])` | `neighbors` for pruned detail |
| "What calls this across services?" | `trace(id, "out", ["CALLS","HTTP_CALLS","ASYNC_CALLS"], max_depth=5)` | `trace` on downstream route_id if needed |

The existing `neighbors` rows remain unchanged. `trace` rows are additive — they cover the cases where the current table says "loop neighbors" or "no magic tool."

### Depth and budget control

- `max_depth` defaults to **3** and is clamped to 1..5.
- Depth 1 is equivalent to `neighbors` (no multi-hop benefit, but allows the pruning engine).
- Depth 3 covers most practical traces: controller -> service -> repository, or route -> handler -> client -> downstream route.
- Depth 5 is available for deep impact analysis but produces large results; the `max_paths` cap prevents runaway output.
- The engine stops early if the frontier is exhausted before `max_depth`.
- `max_nodes_discovered` defaults to **500** and is clamped to 100..2000. This is a **compute guardrail**, not an output guarantee. It counts nodes discovered *before* pruning — this is intentional because the cost is in the Cypher queries and BFS traversal, not in the output serialization. Aggressive `prune_roles` may result in fewer output nodes for the same budget. When the budget is hit, BFS stops mid-traversal and reports `stats.budget_hit = True` plus an advisory: `"trace stopped early: discovered {N} of ~{M} nodes before budget. Reduce max_depth or add prune_roles to focus."`

### Cross-service traversal: boundary signals, not seamless traversal

When BFS encounters a cross-service edge (`HTTP_CALLS` or `ASYNC_CALLS`), the engine **stops traversal at the boundary**. It does not follow into the downstream service. Instead, it records the cross-service edge as a **boundary signal** and includes enough data for the agent to decide whether to continue tracing in the downstream service.

#### Why not seamless traversal

Automatic cross-service traversal was initially proposed as "seamless" — the engine would follow `Symbol -> Client -> HTTP_CALLS -> Route -> EXPOSES handler` in a single call. This was rejected for three reasons:

1. **Context explosion**. A trace starting in `order-service` that follows into `payment-service` now returns two services' worth of paths. A depth-5 trace across 3 services could return hundreds of edges, defeating the pruning that `trace` exists to provide.

2. **Agent autonomy**. The GPS metaphor says the tool returns structure, the agent decides. Automatically crossing service boundaries is the GPS deciding the agent also needs to see what happens in the next city. The agent asked about one service — it should choose whether to follow into another.

3. **Hop budget waste**. Scaffolding edges (DECLARES_CLIENT, EXPOSES) would consume 2 of the 5 available hops just crossing the boundary. The agent gets fewer useful hops in both services.

#### Boundary signal behavior

When BFS discovers a cross-service edge during traversal:

1. **Record the edge** with `cross_service_boundary: True` and full attributes (`confidence`, `strategy`, `match`, `raw_uri`/`raw_topic`).
2. **Include the downstream node** (Route or Producer endpoint) in the `nodes` dict with its full `NodeRef` data (id, kind, fqn, microservice, etc.).
3. **Include the Client/Producer node** that owns the cross-service edge — this is the node the BFS was traversing from, so it's already in the result.
4. **Stop** — do not add the downstream Route to the frontier. Do not follow EXPOSES into the downstream handler. This branch ends at the boundary.
5. **Emit hint**: `"Cross-service boundary: Client X calls Route Y (confidence=0.85, strategy=URI_PATH_MATCH). Use trace(route_id, 'out', ['EXPOSES','CALLS'], max_depth=4) to continue tracing in the downstream service, or describe(route_id) for route details."`

The downstream Route's `NodeRef` includes its `fqn` (e.g., `payment-service:/api/payments:POST`), so the agent can see at a glance what service and endpoint is being called without issuing another tool call.

#### What the agent sees

Example: Agent calls `trace(id, "out", ["CALLS", "HTTP_CALLS"], max_depth=4)` from an `order-service` method:

```yaml
edges:
  - from_id: "sym:OrderServiceImpl#createOrder"
    to_id: "sym:OrderRepository#save"
    edge_type: "CALLS"
    hop: 1
    parent_edge_id: null               # seed edge (or edge that reached OrderServiceImpl)
    cross_service_boundary: false
    attrs: {confidence: 1.0}

  - from_id: "sym:OrderServiceImpl#createOrder"
    to_id: "client:PaymentClient"
    edge_type: "DECLARES_CLIENT"        # auto-followed to reach HTTP_CALLS
    hop: 2
    parent_edge_id: null               # same seed edge
    cross_service_boundary: false
    attrs: {}

  - from_id: "client:PaymentClient"
    to_id: "route:payment-service:/api/payments:POST"
    edge_type: "HTTP_CALLS"
    hop: 3
    parent_edge_id: "DECLARES_CLIENT:OrderServiceImpl->PaymentClient"  # edge that reached PaymentClient
    cross_service_boundary: true         # BFS stops here
    attrs: {confidence: 0.85, strategy: "URI_PATH_MATCH", match: "exact", raw_uri: "/api/payments"}

nodes:
  "route:payment-service:/api/payments:POST":
    id: "route:payment-service:/api/payments:POST"
    kind: "route"
    fqn: "payment-service:/api/payments:POST"
    microservice: "payment-service"
    ...

hints_structured:
  - text: "Cross-service boundary: PaymentClient calls payment-service:/api/payments:POST (confidence=0.85, strategy=URI_PATH_MATCH). Use trace(route_id, 'out', ['EXPOSES','CALLS'], max_depth=4) to continue tracing in the downstream service."
```

The agent now has a clear picture: `OrderServiceImpl` calls the repository (internal) and also calls `payment-service` via HTTP (cross-service boundary). It can choose to trace into `payment-service` or stop here.

#### DECLARES_CLIENT / DECLARES_PRODUCER handling

To reach the cross-service edge, BFS must first traverse from a Symbol to its Client/Producer via `DECLARES_CLIENT`/`DECLARES_PRODUCER`. These are **scaffolding edges** — they're followed only when `HTTP_CALLS` or `ASYNC_CALLS` is in the user's `edge_types`. They appear in the result with their actual edge type and consume a hop, but are not required to be in the user's `edge_types`.

This is the only case where the engine follows edge types not in `edge_types`: the scaffolding hop from Symbol to Client/Producer is necessary to reach the cross-service edge the agent asked for. The engine never follows edges into the downstream service.

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
- `kuzu_queries.py` is not modified (trace uses a new batched query method, not the per-node query).
- `mcp_v2.py` is not modified (trace imports `NodeFilter`, `EdgeFilter`, `Edge`, `NodeRef` types but does not change them).
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
| `test_trace_prune_roles` | With `prune_roles=["DTO"]`, DTO nodes' edges are recorded but DTO is not in frontier; BFS doesn't continue through DTO |
| `test_trace_fan_out_cap` | With `fan_out_cap=2`, a node with 8 outbound CALLS returns at most 2 edges |
| `test_trace_fan_out_cap_scaffolding_exempt` | Scaffolding edges (DECLARES_CLIENT) are not counted toward fan_out_cap; cross-service path preserved even when cap is tight |
| `test_trace_collapse_trivial` | Wrapper chain A->B->C where B has degree 2 is collapsed to A->C |
| `test_trace_collapse_trivial_disabled` | With `collapse_trivial=False`, wrapper chains are not collapsed |
| `test_trace_collapse_parent_edge_id_consistency` | After collapsing A->B->C to A->C, child edges of C that referenced B->C as parent_edge_id now reference the collapsed A->C edge |
| `test_trace_cross_service_http` | Traces from a method through DECLARES_CLIENT -> HTTP_CALLS; stops at Route boundary with `cross_service_boundary=True`; Route in nodes dict but not in frontier |
| `test_trace_cross_service_async` | Same for ASYNC_CALLS through Producer |
| `test_trace_max_paths_cap` | Result paths list does not exceed `max_paths` |
| `test_trace_budget_stops_early` | BFS stops when `max_nodes_discovered` is hit; `stats.budget_hit=True`; advisory message present |
| `test_trace_depth_1_equivalent_to_neighbors` | Depth 1 trace with no pruning returns same nodes as `neighbors` |
| `test_trace_stats_counts` | `stats.total_nodes_discovered`, `stats.nodes_pruned_role`, etc. are consistent with the edge set |
| `test_trace_empty_seed` | Empty seed ids returns `success=True, nodes={}, edges=[], paths=[]` |
| `test_trace_single_string_seed` | Single string `ids` is normalized to list; `seed_ids` echoed as list of one |
| `test_trace_multiple_seeds` | Multiple seed IDs produce a union of traces with shared visited set |
| `test_trace_invalid_edge_type` | Unknown edge type returns `success=False` with teaching message |
| `test_trace_direction_required` | Missing direction returns `success=False` |
| `test_trace_edge_types_required` | Empty edge_types returns `success=False` |
| `test_trace_max_depth_clamped` | `max_depth` values <1 clamped to 1, >5 clamped to 5 |
| `test_trace_budget_clamped` | `max_nodes_discovered` values <100 clamped to 100, >2000 clamped to 2000 |
| `test_trace_visited_set_no_cycles` | BFS does not revisit nodes even if cycles exist in the graph |
| `test_trace_filter_applied` | NodeFilter restricts discovered nodes (hard gate — excluded entirely) |
| `test_trace_filter_vs_prune_roles` | NodeFilter exclude_roles is harder than prune_roles: NodeFilter excludes nodes and edges; prune_roles records edges but stops frontier |
| `test_trace_edge_filter_calls` | EdgeFilter with `min_confidence` filters CALLS edges during traversal |
| `test_trace_include_unresolved` | UnresolvedCallSite edges are interleaved when `include_unresolved=True, edge_types=["CALLS"], direction="out"` |
| `test_trace_paths_root_to_leaf` | Each path starts at a seed and ends at a leaf with no further outbound edges in the result |
| `test_trace_overrides_interface_resolution` | Traces from interface method via OVERRIDES out, reaches implementation method; OVERRIDES works as standard edge type |
| `test_trace_cross_service_edge_attrs` | Cross-service boundary edges include `confidence`, `strategy`, `match` attributes and `cross_service_boundary=True` |
| `test_trace_cross_service_boundary_stops` | BFS does not follow past cross-service boundary; downstream Route appears in nodes but no EXPOSES/CALLS edges from it |
| `test_trace_parent_edge_id_seed_null` | Seed edges (hop 0) have `parent_edge_id: null` |
| `test_trace_parent_edge_id_chain` | Non-seed edges have `parent_edge_id` pointing to a valid edge in the result |

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

7. **Cross-service traversal: boundary-stop, not seamless** — **BFS stops at service boundaries.** When the engine encounters `HTTP_CALLS` or `ASYNC_CALLS` edges, it records them with `cross_service_boundary: True`, includes the downstream Route/Producer node in the result, but does not add it to the frontier. The agent decides whether to trace into the downstream service via a separate `trace` call. Scaffolding edges (`DECLARES_CLIENT`, `DECLARES_PRODUCER`) are followed only to reach the cross-service edge and appear in the result. See "Cross-service traversal: boundary signals, not seamless traversal" section for rationale.

8. **`collapsed` marker on TraceEdge** — **Yes.** Collapsed edges carry `collapsed: True` and `collapsed_intermediates: [node_ids]` so agents can detect shortcuts and drill down via `neighbors`.

9. **`max_nodes_discovered` counts pre-pruning** — **Intentional.** The budget is a compute guardrail limiting Cypher queries and BFS traversal cost, not an output size guarantee. The intent is documented explicitly in the Depth and budget control section.

10. **PR-TRACE-1 split** — **Split into PR-TRACE-1a (core BFS + budget + paths) and PR-TRACE-1b (pruning + collapsing + cross-service).** Core BFS correctness and pruning heuristics are different review surfaces.

11. **Flat edge list hierarchy** — **`parent_edge_id` on TraceEdge, not a full `tree` field.** A flat `edges` list requires agents to reconstruct the call tree from `from_id`/`to_id` pairs, which is cognitively expensive for 30+ edges. However, a full `tree` field duplicates structural data (tree nodes reference IDs in `nodes` dict, but the nesting itself is redundant with `from_id`/`to_id`). The lighter approach: each `TraceEdge` carries `parent_edge_id` (nullable, references the incoming edge to its `from_id`; null for seed edges). This is O(1) per edge, enables tree reconstruction when needed, and doesn't duplicate node payloads. A full nested `tree` field is deferred to v2 if agents demonstrate they need it — tracked in #240.

### Follow-up issues

- **#240** — trace tool: v2 enhancements (bidirectional traversal, richer collapse_trivial heuristic, configurable path ranking, configurable fan_out_cap ranking)
- **#241** — trace tool: CLI integration and legacy method deprecation

## Experimental validation

`trace` ships on the `experimental` branch. It is merged to `master` only when the following criteria are met:

### Quantitative criteria

1. **Multi-hop accuracy**: For a set of 10 multi-hop questions (3+ hops, mixed intra/cross-service), agents using `trace` produce correct answers in ≥80% of cases. The baseline is agents using `neighbors` loops on the same questions (currently ~40% based on production observation).

2. **Tool call reduction**: `trace` reduces tool calls by ≥50% for multi-hop questions compared to `neighbors` loops (measured on the same question set).

3. **No regression on single-hop**: For a set of 10 single-hop questions where agents currently use `neighbors`, introducing `trace` as an option does not degrade accuracy or increase tool calls. Agents must still pick `neighbors` for single-hop questions.

4. **Latency**: `trace` call latency is under 500ms for depth 3 on the `bank-chat-system` fixture, and under 2s for depth 5 on a large codebase (10K+ methods).

### Qualitative criteria

5. **Agent tool selection**: In ≥70% of multi-hop questions, the agent picks `trace` over a `neighbors` loop without manual prompting. This validates that the `_INSTRUCTIONS` and skill guidance are effective.

6. **Pruning quality**: In post-trace inspection, ≥80% of pruned edges (those cut by `prune_roles`, `fan_out_cap`, or `collapse_trivial`) are genuinely low-signal. Measured by human review of a random sample.

### Graduation process

1. All PR-TRACE PRs merge to `experimental`.
2. Run validation suite against `tests/bank-chat-system` and at least one real codebase.
3. If all criteria pass, open a PR to merge `experimental` into `master`.
4. If criteria fail, iterate on pruning heuristics and agent guidance, then re-run.

### Rollback plan

If `trace` causes regressions in production after merging to `master`:
- Remove the `trace` tool registration from `server.py` (one-line revert).
- `mcp_trace.py` remains in the tree but is no longer reachable.
- No re-index required.

## Out of scope

- **Answer engine.** `trace` returns paths and structure. It does not synthesize natural-language answers or recommendations.
- **Semantic ranking.** `trace` does not rank paths by semantic similarity to a query. It ranks by structural metrics (confidence, role, length).
- **Graph schema changes.** No new node kinds, edge types, or edge attributes.
- **Indexer changes.** No changes to `build_ast_graph.py` or the indexing pipeline.
- **Replacing `neighbors`.** `neighbors` remains the one-hop primitive. `trace` is a higher-level convenience for multi-hop patterns.
- **Visualization.** `trace` returns structured data. Rendering as a diagram, tree, or flowchart is the agent's job.
- **Composed edge types as input.** `trace` accepts only stored edge labels. Composed traversal (e.g., DECLARES.DECLARES_CLIENT) is handled internally by the BFS engine when the agent passes `["DECLARES", "DECLARES_CLIENT"]`.

## Sequencing / Follow-ups

All PR-TRACE PRs target the `experimental` branch. They do not merge to `master` until the experimental validation criteria are met.

**Branch topology:** PR-TRACE-1a is the base. 1b stacks on 1a (sequential — 1b extends the engine 1a builds). PR-TRACE-2 and PR-TRACE-3 each branch from 1b (parallel after the engine is complete). PR-TRACE-4 branches from 3 (needs hints and integration tests to be stable before documenting).

```
experimental ← 1a ← 1b ← 2
                    ← 3 ← 4
```

### PR-TRACE-1a -- `mcp_trace.py` core BFS engine

- Implement `TraceOutput`, `TraceEdge`, `TracePath`, `TraceStats` models.
- Implement `neighbors_batched` on `KuzuGraph` (single Cypher query per hop for all frontier nodes).
- Implement BFS traversal with visited set, edge type expansion, NodeFilter/EdgeFilter integration.
- Implement `max_nodes_discovered` budget with early-stop and advisory.
- Implement path enumeration with 10× `max_paths` enumeration cap and ranking.
- **Tests**: `test_trace_outbound_calls_depth_2`, `test_trace_inbound_callers_depth_2`, `test_trace_max_paths_cap`, `test_trace_budget_stops_early`, `test_trace_depth_1_equivalent_to_neighbors`, `test_trace_stats_counts`, `test_trace_empty_seed`, `test_trace_single_string_seed`, `test_trace_multiple_seeds`, `test_trace_invalid_edge_type`, `test_trace_direction_required`, `test_trace_edge_types_required`, `test_trace_max_depth_clamped`, `test_trace_budget_clamped`, `test_trace_visited_set_no_cycles`, `test_trace_filter_applied`, `test_trace_filter_vs_prune_roles`, `test_trace_edge_filter_calls`, `test_trace_include_unresolved`, `test_trace_paths_root_to_leaf`, `test_trace_overrides_interface_resolution`, `test_trace_parent_edge_id_seed_null`, `test_trace_parent_edge_id_chain`.

### PR-TRACE-1b -- pruning, collapsing, and cross-service

- Implement role-based pruning (`prune_roles`) as soft gate (edges recorded, frontier stops).
- Implement fan-out throttling (`fan_out_cap`) with confidence-based ranking + role tiebreaker; scaffolding edges exempt.
- Implement trivial chain collapsing (`collapse_trivial`) with `collapsed`/`collapsed_intermediates` markers on TraceEdge.
- Implement post-collapse `parent_edge_id` recomputation.
- Implement cross-service boundary detection (HTTP_CALLS, ASYNC_CALLS boundary-stop with `cross_service_boundary` marker) per the scaffolding edge rules.
- **Tests**: `test_trace_prune_roles`, `test_trace_fan_out_cap`, `test_trace_fan_out_cap_scaffolding_exempt`, `test_trace_collapse_trivial`, `test_trace_collapse_trivial_disabled`, `test_trace_collapse_parent_edge_id_consistency`, `test_trace_cross_service_http`, `test_trace_cross_service_async`, `test_trace_cross_service_edge_attrs`, `test_trace_cross_service_boundary_stops`.

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

The v2 "no trace tools" decision was made with good reason. `trace` ships as an **experiment** on the `experimental` branch to validate the hypothesis that server-side pruning helps agents without violating the GPS contract. If the experiment fails, the rollback is trivial: remove one tool registration, no re-index.

## Appendix B -- Comparison with `neighbors` loop

| Aspect | `neighbors` loop | `trace` |
|---|---|---|
| Calls for a 3-hop trace | 3-8 tool calls | 1 tool call |
| Visited set | Agent's responsibility (LLMs are bad at this) | Server-side (deterministic) |
| Fan-out control | Agent must filter manually | `fan_out_cap`, `prune_roles` |
| Cross-service | Multiple calls per boundary | Boundary signal with full attrs; separate `trace` per service |
| Trivial chain collapsing | Agent must detect and skip | `collapse_trivial` |
| Result structure | Flat edge lists per call | Structured paths + nodes dict |
| Context budget | High (each call returns full payloads) | Low (pruned, deduplicated) |
| Granularity | Full control per hop | Pruning may hide edges agent wants |
| Flexibility | Can change strategy per hop | Fixed strategy for entire trace |

The trade-off is clear: `trace` sacrifices per-hop control for efficiency. Agents that need per-hop reasoning should use `neighbors`. Agents that need a multi-hop overview should use `trace`.
