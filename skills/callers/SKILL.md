---
name: callers
description: Show who calls a method symbol (in-process CALLS). Use when the user asks "who calls X", "callers of X", or "what invokes X". Argument is a sym: id or identifier resolved via resolve.
---

# /callers — Show callers of a method symbol

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string (FQN fragment, method signature) → `resolve(identifier=..., hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to an HTTP route, use `/who-hits-route`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **In-process callers:**
   `neighbors({ids: <sym_id>, direction: "in", edge_types: ["CALLS"]})`.
   Render grouped by caller `fqn` + `microservice`.

## Worked example

User: /callers ChatController#joinOperator(JoinOperatorRequest)
You: → resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
   → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["CALLS"]})
   → returns CALLS edges from in-process callers

## Out of scope

- Callers of routes (use `/who-hits-route`).
- Recursive callers beyond depth 1 (use `/impact-of`).
