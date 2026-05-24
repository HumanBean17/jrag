---
name: callers
description: Show who calls a method symbol (in-process CALLS). Use when the user asks "who calls X", "callers of X", or "what invokes X". Argument is a sym: id or identifier resolved via resolve.
---

# /callers — Show callers of a method symbol

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string (FQN fragment, method signature) → `resolve(identifier=..., hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to an HTTP route, use `/who-hits-route`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it as the id. Otherwise, call `resolve(identifier=<arg>, hint_kind="symbol")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `search(query=<arg>, limit=5)` and stop if still empty.
2. **In-process callers.** Call `neighbors` with `ids=<sym_id>`, `direction="in"`, `edge_types=["CALLS"]`. Render grouped by caller `fqn` + `microservice`.

## Worked example

User: /callers ChatController#joinOperator(JoinOperatorRequest)
You: → resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
   → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids: "sym:...", direction: "in", edge_types: ["CALLS"]})
   → returns CALLS edges from in-process callers

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Callers of routes (use `/who-hits-route`).
- Recursive callers beyond depth 1 (use `/impact-of`).
