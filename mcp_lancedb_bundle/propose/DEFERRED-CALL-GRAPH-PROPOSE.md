# Deferred call-graph layer — evolution proposal

Status: **not implemented**. This document captures the design so the next
iteration can pick it up without re-deriving the shape.

## Why this is needed

Behavioural trace queries (`"what happens when a client message arrives"`,
`"reassignment when operator goes offline"`) fail to surface the true
orchestration chunks because the signal

> `ChatManagementService.enqueue` → `DistributionChunkService.pickEligibleOperator` → `OperatorAssignedProcessor.onOperatorAssigned`

is **not in the index at all** today. The Kuzu graph currently models only
`EXTENDS`, `IMPLEMENTS`, `INJECTS`. No amount of lexical/symbol/role
reweighting can recover a behavioural chain that spans classes which don't
reference each other's names in chunk text. Only explicit `CALLS` edges carry
that structural fact.

## Principle: pure evolution, not rework

Nothing that exists is thrown away. Everything below is additive.

### What stays exactly as-is
- LanceDB tables, `JavaLanceChunk` schema, CocoIndex flow.
- Kuzu DB file, existing node table `Symbol`, existing edges `EXTENDS`,
  `IMPLEMENTS`, `INJECTS`.
- `build_ast_graph.py`'s two-pass structure (collect → resolve).
- All MCP tools (`codebase_search`, `trace_flow`, `find_injectors`, …).
  Their signatures don't change; only additions.
- Ranking heuristics (`_role_weight`, `_symbol_bonus`,
  `_attach_neighbor_context`).

### What gets added on top

1. **`ast_java.py` — third visitor pass.**
   For every `method_declaration` / `constructor_declaration` body, traverse
   and collect every `method_invocation` node. Record
   `(caller_fqn, receiver_expr_text, callee_simple_name, arg_count, line_number)`.
   Same tree-sitter style as the existing walker. ~60 lines.

2. **`build_ast_graph.py` — third resolution pass.**
   Turn the unresolved call sites into Kuzu `CALLS` edges. Reuse the existing
   name-resolution algorithm:
   - receiver-expression variable → field/param type → FQN (best effort).
   - for unresolved calls (JDK / lib / dynamic dispatch), create "phantom"
     Symbol nodes with `resolved = false`, same pattern already used for
     unresolved supertypes.
   - overloads: disambiguate by `arg_count` when possible; otherwise emit
     one edge per matching overload (graph stays a multigraph).
   ~80 lines.

   New edge schema:
   ```
   CALLS {
     call_site_line INT,
     call_site_byte INT,
     arg_count      INT,
     resolved       BOOL
   }
   ```

3. **`kuzu_queries.py` — two new helpers + whitelist.**
   - `find_callers(fqn, depth=1, limit=100)` — inbound `CALLS` closure.
   - `find_callees(fqn, depth=1, limit=100)` — outbound `CALLS` closure.
   - Add `CALLS` to the edge-type whitelist of `graph_neighbors` and
     `impact_analysis` so existing tools transparently benefit.
   - `trace_flow` gains optional `follow_calls: bool = True` — when set,
     the staged walk adds `CALLS` alongside `INJECTS`. Default true once
     stable; default false during initial rollout.
   ~40 lines.

4. **`search_lancedb.py` — one extra edge type in graph expansion.**
   `_graph_expand_merge` already walks the Kuzu graph from seed FQNs.
   Add `CALLS` to the traversal. RRF formula, scoring, tie-breakers all
   unchanged. ~10 lines.

5. **`server.py` — two thin MCP tools.**
   - `find_callers(fqn, depth, limit)`
   - `find_callees(fqn, depth, limit)`
   - `trace_flow` exposes `follow_calls` param.
   ~15 lines.

## Impact on indexing / storage

- **LanceDB: untouched.** No reindex, no schema change, no new column.
- **Kuzu: forced rebuild.** `build_ast_graph.py` re-run produces the new edge
  table. Existing clients keep working if they don't use `CALLS`.
- **Rebuild cost:** ~15–30% slower graph build on this codebase (call-site
  collection is cheap; resolution adds one more pass over the receiver-type
  table that we already have in memory).

## Ranking / retrieval wins we expect

- Query 4 (`"callback when operator is assigned"`):
  `graph_expand=true` walks `CALLS` from `ChatManagementService.enqueue` and
  pulls `OperatorAssignedProcessor.onOperatorAssigned` / `JoinOperatorController`
  into the fused window regardless of lexical overlap.
- Query 5 (`"operator unavailable / reassignment"`):
  `trace_flow(..., follow_calls=True)` surfaces the actual reassignment
  path (`OperatorSessionService` → `DistributionChunkService.reassign...`)
  instead of `SessionStatus` enum / policy configuration noise.
- Queries 1–3 already work; they neither regress nor benefit much.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Overload resolution imprecision | Multigraph is fine for BFS; consumers already dedupe by FQN. |
| Lambdas / method references | Resolve as best-effort; fall back to phantom node (`resolved=false`). |
| Graph size blow-up (CALLS is O(N_methods · avg_calls)) | Index-only fields; Kuzu handles millions of edges trivially on this codebase size. Cap per-query traversal via existing `limit`/`depth` params. |
| External calls (JDK, Spring, libs) | Phantom `resolved=false` nodes; callers can filter them out via an optional `exclude_external=True` flag on the query helpers. |

## Effort estimate

- Implementation: ~1 focused working day.
- Validation on the 5-query benchmark: ~0.5 day.
- Documentation update (`README.md` — new tools, new edge type, `follow_calls`):
  ~1 hour.

## Non-goals (explicit)

- **Data-flow analysis** (which field a call mutates). Out of scope; needs
  a proper semantic analysis pass, not tree-sitter.
- **Framework-level callbacks** (Spring event listeners, Kafka listeners,
  HTTP routing). These are already partially covered by role tagging
  (`CONTROLLER`, `COMPONENT`) and by `trace_flow` stage ordering.
- **Cross-service calls over Feign/HTTP.** Separate layer — tracked via
  `FEIGN_CLIENT` role + URL-path indexing, not via `CALLS`.
