---
name: injects
description: Show where a type is injected via dependency injection. Use when the user asks "where is X injected", "who injects X", or "what depends on X via DI". Argument is a type sym: id or identifier.
---

# /injects — Where a type is injected

## Argument contract

Single positional argument: a **type** Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **Injection sites:**
   `neighbors({ids: <type_sym_id>, direction: "in", edge_types: ["INJECTS"]})`.
   Render each injection site's `fqn` + `microservice` + edge `attrs.mechanism` + `attrs.field_or_param`.

## Worked example

User: /injects OperatorAssignmentService
You: → resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
   → sym:com.bank.chat.assign.service.OperatorAssignmentService
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["INJECTS"]})
   → returns types/methods that inject OperatorAssignmentService
