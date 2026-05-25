---
name: explain-feature
description: Understand how a feature works end-to-end by locating entry points and tracing call chains with bounded depth. Use when the user asks "how does X work", "explain feature X", "walk me through Y", or "show me the flow of Z". Argument is free-form feature/concept text. Composes search → describe → bounded neighbors walks.
---

# /explain-feature — Understand a feature end-to-end

## When to use

The user wants a **narrative explanation** of how something works: entry points, the call chain to persistence/async boundaries, and any cross-service hops. The input is fuzzy (a feature name, not an id).

For a more focused single-method noise-filtered map use `/mini-map`. For a known route end-to-end use `/trace-request-flow`.

## Tools used

`search`, `describe`, `neighbors`. No `find` — the input is fuzzy.

## Reasoning preamble (mandatory)

Before **each** MCP call:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

**Q-class taxonomy:** `semantic` (`search`), `structured` (`find`/`resolve`), `inspect` (`describe`), `walk` (`neighbors`).

This skill typically uses `semantic → inspect → walk → walk`.

## Argument contract

Single positional argument: free-form text describing the feature or concept to explain.

## Steps

1. **Locate entry points.** Call `search(query=<arg>, limit=8)`. Pick top 1–3 hits with strong `symbol_id` fit (role aligns with what the user wants — `CONTROLLER` for HTTP features, `SERVICE` for business logic, etc.).
2. **Inspect each hit.** Call `describe(id=<symbol_id>)` for each. Read `edge_summary` (per-label `in`/`out` counts) and `role` to choose which edges to walk next.
3. **Walk with bounded `neighbors`.** Use *small* `edge_types` sets per call:
   - **Methods:** `neighbors(ids=<sym>, direction="out", edge_types=["CALLS"])` for in-process flow.
   - **At controller/handler boundaries:** `neighbors(ids=<route>, direction="in", edge_types=["EXPOSES"])` to find the handler from a route, or vice versa.
   - **At outbound HTTP/async:** `neighbors(out, ["DECLARES_CLIENT"])` then `neighbors(out, ["HTTP_CALLS"])`; or `neighbors(out, ["DECLARES_PRODUCER"])` then `neighbors(out, ["ASYNC_CALLS"])`.
   - **Type wiring:** `neighbors(in, ["IMPLEMENTS"])` to see who realizes an interface; `neighbors(in, ["INJECTS"])` to see who depends on a type.
4. **Render.** Synthesize a narrative: entry points → key methods → data flow → cross-service boundaries. Cite each claim with the `sym:` / `route:` id you walked.

## Stop conditions

- Maximum 3 hops from any entry point.
- Stop as soon as you can answer the user's question.
- Do not pre-fetch unrelated subgraphs.
- After two empty/failed `neighbors` calls on the same node, stop walking that branch.

## Recursion limit

- Depth ≤ 3 from each entry point.
- Maximum 10 `neighbors` calls total across the whole skill invocation.
- If the explanation needs more, hand off to `/trace-request-flow` (route-anchored, depth 4) or `/impact-of` (reverse direction).

## Worked example

User: `/explain-feature operator assignment`
You:
```
Q-class: semantic  Pick: search  Why: NL feature name
→ search(query="operator assignment", limit=8)
  → sym:com.bank.chat.assign.service.OperatorAssignmentService  (interface, SERVICE)
  → sym:com.bank.chat.assign.api.AssignController                (CONTROLLER)
Q-class: inspect   Pick: describe Why: edge_summary on interface
→ describe(id="sym:...OperatorAssignmentService")
  → edge_summary { IMPLEMENTS.in: 2, INJECTS.in: 3, CALLS.in: 4 }
Q-class: walk      Pick: neighbors Why: find concrete implementors
→ neighbors(ids="sym:...OperatorAssignmentService", direction="in", edge_types=["IMPLEMENTS"])
  → RoundRobin..., Weighted... (2 strategies)
Q-class: walk      Pick: neighbors Why: trace inbound from controller
→ neighbors(ids="sym:...AssignController", direction="out", edge_types=["CALLS"])
  → AssignController → OperatorAssignmentService#assign → OperatorRepository#save
Synthesize: "Operator assignment has two strategies (RoundRobin, Weighted)
behind an interface. Triggered via AssignController. Persists via OperatorRepository..."
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files when MCP can answer.
- Do not skip MCP calls and guess.
- Do not fabricate ids — always obtain them from `search` / `find` / `resolve`.
- Do not walk all edge types at once — small `edge_types` sets per call.

## Out of scope

- Exact impact analysis (use `/impact-of`).
- Route-anchored end-to-end trace (use `/trace-request-flow`).
- Noise-filtered single-method call map (use `/mini-map`).

## Going deeper

Edge taxonomy, the locate→inspect→walk workflow, and the recovery playbook are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/explain-feature`.
