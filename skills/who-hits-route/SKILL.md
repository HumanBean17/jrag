---
name: who-hits-route
description: Show all inbound paths to an HTTP or messaging route (HTTP_CALLS, ASYNC_CALLS, and EXPOSES). Use when the user asks "who calls this endpoint", "what hits this route", or "all callers of this route". Argument is a route: id or route identifier.
---

# /who-hits-route — All inbound paths to a route

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
2. **All inbound.** Call `neighbors` with `ids=<route_id>`, `direction="in"`, `edge_types=["HTTP_CALLS", "ASYNC_CALLS", "EXPOSES"]`. Render grouped by edge type:
   - `EXPOSES` → handler method Symbol
   - `HTTP_CALLS` → Client nodes (with `attrs.match`, `attrs.confidence`)
   - `ASYNC_CALLS` → Producer nodes (with `attrs.match`, `attrs.confidence`)

## Worked example

User: /who-hits-route POST /chat/join
You: → resolve(identifier="POST /chat/join", hint_kind="route")
   → route:POST /chat/join
   → neighbors({ids: "route:...", direction: "in", edge_types: ["HTTP_CALLS", "ASYNC_CALLS", "EXPOSES"]})
   → returns EXPOSES from handler method + HTTP_CALLS from clients + ASYNC_CALLS from producers

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate route ids — always obtain them from `resolve` or `find`.

## Out of scope

- In-process callers of a method (use `/callers`).
