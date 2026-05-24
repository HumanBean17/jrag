---
name: nl
description: Natural-language search into the graph. Use when the user asks a fuzzy question like "find authentication code", "where is X handled", or "show me Y". Argument is free-form text.
---

# /nl — Natural-language to graph navigation

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: free-form text describing what to find.

## Steps

1. **Search.** Call `search(query=<arg>, limit=8)`. Review results for strong `symbol_id` fit (role, `symbol_kind`, `microservice` alignment).
2. **Inspect top hit.** When a result has a `symbol_id`, call `describe(id=<symbol_id>)` to get the full record and `edge_summary`.
3. **Stop or walk.** If the describe answers the question, stop. Otherwise call `neighbors` with relevant `edge_types` from the `edge_summary`.

## Worked example

User: /nl operator assignment
You: → search(query="operator assignment", limit=8)
   → top hit: sym:com.bank.chat.assign.service.OperatorAssignmentService
   → describe(id="sym:com.bank.chat.assign.service.OperatorAssignmentService")
   → returns full record with edge_summary showing CALLS, INJECTS edges
   → agent can now walk with neighbors if needed

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Structured listing by role or kind (use `/controllers`, `/routes`, etc.).
- Identifier-shaped input where `resolve` would be more precise.
