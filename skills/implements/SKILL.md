---
name: implements
description: Show concrete classes that implement an interface. Use when the user asks "what implements X", "implementations of X", or "concrete types for interface X". Argument is a type sym: id or identifier.
---

# /implements — Concrete implementors of an interface

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a **type** Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it as the id. Otherwise, call `resolve(identifier=<arg>, hint_kind="symbol")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `search(query=<arg>, limit=5)` and stop if still empty.
2. **Implementors.** Call `neighbors` with `ids=<type_sym_id>`, `direction="in"`, `edge_types=["IMPLEMENTS"]`. Render each implementor's `fqn` + `microservice`.

## Worked example

User: /implements OperatorAssignmentService
You: → resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
   → sym:com.bank.chat.assign.service.OperatorAssignmentService (interface)
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["IMPLEMENTS"]})
   → returns concrete classes implementing the interface

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
