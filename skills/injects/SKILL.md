---
name: injects
description: Show where a type is injected via dependency injection. Use when the user asks "where is X injected", "who injects X", or "what depends on X via DI". Argument is a type sym: id or identifier.
---

# /injects — Where a type is injected

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
2. **Injection sites.** Call `neighbors` with `ids=<type_sym_id>`, `direction="in"`, `edge_types=["INJECTS"]`. Render each injection site's `fqn` + `microservice` + edge `attrs.mechanism` + `attrs.field_or_param`.

## Worked example

User: /injects OperatorAssignmentService
You: → resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
   → sym:com.bank.chat.assign.service.OperatorAssignmentService
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["INJECTS"]})
   → returns types/methods that inject OperatorAssignmentService

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
