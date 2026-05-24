---
name: nl
description: Natural-language search into the graph. Use when the user asks a fuzzy question like "find authentication code", "where is X handled", or "show me Y". Argument is free-form text.
---

# /nl — Natural-language to graph navigation

## Argument contract

Single positional argument: free-form text describing what to find.

## Steps

1. **Search.**
   `search(query=<arg>, limit=8)` — review results for strong `symbol_id` fit (role, `symbol_kind`, `microservice` alignment).
2. **Inspect top hit.**
   When a result has a `symbol_id`, call `describe(id=<symbol_id>)` to get the full record and `edge_summary`.
3. **Stop or walk.**
   If the describe answers the question, stop. Otherwise use `neighbors` with relevant `edge_types` from the `edge_summary`.

## Worked example

User: /nl operator assignment
You: → search(query="operator assignment", limit=8)
   → top hit: sym:com.bank.chat.assign.service.OperatorAssignmentService
   → describe(id="sym:com.bank.chat.assign.service.OperatorAssignmentService")
   → returns full record with edge_summary showing CALLS, INJECTS edges
   → agent can now walk with neighbors if needed

## Out of scope

- Structured listing by role or kind (use `/controllers`, `/routes`, etc.).
- Identifier-shaped input where `resolve` would be more precise.
