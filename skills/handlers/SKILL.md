---
name: handlers
description: Show the method that handles an HTTP or messaging route. Use when the user asks "what handles X route", "handler for POST /foo", or "which method handles this endpoint". Argument is a route: id or route identifier.
---

# /handlers — Show handler method for a route

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a **route** id (`route:...` preferred) OR an identifier-shaped string (path, METHOD /path) → `resolve(identifier=..., hint_kind="route")`.

## Steps

1. **Resolve.** If the argument starts with `route:` or `r:`, use it directly. Otherwise, call `resolve(identifier=<arg>, hint_kind="route")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `find(kind="route", filter={path_prefix: <arg>})` and stop if still empty.
2. **Handler method.** Call `neighbors` with `ids=<route_id>`, `direction="in"`, `edge_types=["EXPOSES"]`. Render the handler method `fqn` + `microservice`.

## Worked example

User: /handlers route:POST /chat/join
You: → neighbors({ids: "route:POST /chat/join", direction: "in", edge_types: ["EXPOSES"]})
   → returns the handler method Symbol that exposes this route

User: /handlers POST /chat/join
You: → resolve(identifier="POST /chat/join", hint_kind="route")
   → route:POST /chat/join
   → neighbors({ids: "route:...", direction: "in", edge_types: ["EXPOSES"]})
   → returns handler method

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate route ids — always obtain them from `resolve` or `find`.
