---
name: implements
description: Show concrete classes that implement an interface. Use when the user asks "what implements X", "implementations of X", or "concrete types for interface X". Argument is a type sym: id or identifier.
---

# /implements — Concrete implementors of an interface

## Argument contract

Single positional argument: a **type** Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **Implementors:**
   `neighbors({ids: <type_sym_id>, direction: "in", edge_types: ["IMPLEMENTS"]})`.
   Render each implementor's `fqn` + `microservice`.

## Worked example

User: /implements OperatorAssignmentService
You: → resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
   → sym:com.bank.chat.assign.service.OperatorAssignmentService (interface)
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["IMPLEMENTS"]})
   → returns concrete classes implementing the interface
