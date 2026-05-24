---
name: explain-feature
description: Understand how a feature works end-to-end by tracing from entry points through call chains. Use when the user asks "how does X work", "explain feature X", or "walk me through X". Argument is a free-form feature description.
---

# /explain-feature — Understand a feature end-to-end

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: free-form text describing the feature or concept to explain.

## Steps

1. **Locate entry points.** Call `search(query=<arg>, limit=8)`. Pick top 1–3 hits with strong `symbol_id` fit (role, `symbol_kind` alignment).
2. **Inspect each hit.** Call `describe(id=<symbol_id>)` for each hit. Read `edge_summary` to understand the node's connectivity.
3. **Walk with bounded neighbors.** For each inspected node, call `neighbors` with **small** `edge_types` sets per step:
   - Methods: call `neighbors` with `direction="out"`, `edge_types=["CALLS"]` for in-process flow.
   - Boundaries: call `neighbors` for `EXPOSES` (route handlers), `DECLARES_CLIENT` → `HTTP_CALLS` (outbound HTTP), `DECLARES_PRODUCER` → `ASYNC_CALLS` (async).
   - Type wiring: call `neighbors` for `IMPLEMENTS`, `INJECTS` when relevant.
4. **Render.** Synthesize findings into a narrative: entry points → key methods → data flow → cross-service boundaries.

## Stop conditions

- Maximum 3 hops from any entry point.
- Stop when you can answer the user's question.
- Do not prefetch unrelated subgraphs.

## Recursion limit

- Depth ≤ 3 from each entry point.
- Maximum 10 `neighbors` calls total.

## Worked example

User: /explain-feature operator assignment
You: → search(query="operator assignment", limit=8)
   → hit: sym:com.bank.chat.assign.service.OperatorAssignmentService
   → describe(id="sym:...") → edge_summary shows CALLS, INJECTS
   → neighbors(out, ["CALLS"]) → shows delegation to repository and other services
   → neighbors(in, ["IMPLEMENTS"]) → shows concrete implementations
   → synthesize: "OperatorAssignmentService is an interface with two implementations.
     The controller calls it via DI. It delegates to OperatorRepository for persistence..."

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Exact impact analysis (use `/impact-of`).
- Full request flow tracing (use `/trace-request-flow`).
- Noise-filtered call maps (use `/mini-map`).
