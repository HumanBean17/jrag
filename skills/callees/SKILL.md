---
name: callees
description: Show what a method symbol calls (in-process CALLS). Use when the user asks "what does X call", "callees of X", or "what does X invoke". Argument is a sym: id, or an identifier resolved via resolve.
---

# /callees — Show callees of a method symbol

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string (FQN fragment, method signature) → `resolve(identifier=..., hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to an HTTP route, use `/who-hits-route`. For outbound Feign/HTTP from a method, see optional step 3 below.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **In-process callees:**
   `neighbors({ids: <sym_id>, direction: "out", edge_types: ["CALLS"]})`.
   Render grouped by edge type; show callee `fqn` + `microservice`.
3. **Optional — outbound HTTP (only when user asks about cross-service calls):**
   `neighbors({ids: <sym_id>, direction: "out", edge_types: ["DECLARES_CLIENT"]})`
   → for each client id:
   `neighbors({ids: <client_id>, direction: "out", edge_types: ["HTTP_CALLS"]})`.
   (Async: `DECLARES_PRODUCER` → `ASYNC_CALLS` on producer ids.)

## Worked example

User: /callees ChatController#joinOperator(JoinOperatorRequest)
You: → resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
   → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids: "sym:...", direction: "out", edge_types: ["CALLS"]})
   → returns CALLS edges to in-process service methods
   → (optional step 3) neighbors(out, ["DECLARES_CLIENT"]) on sym id, then HTTP_CALLS from client ids

## Out of scope

- Recursive callees beyond depth 1 (use `/trace-request-flow` or `/mini-map`).
- Noisy CALLS subgraphs on service methods (prefer `/mini-map`; fall back here if the map is too thin).
- Filtering by microservice (compose with `/controllers` if needed).
