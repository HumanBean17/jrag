---
name: who-hits-route
description: Show all inbound paths to an HTTP or messaging route (HTTP_CALLS, ASYNC_CALLS, and EXPOSES). Use when the user asks "who calls this endpoint", "what hits this route", or "all callers of this route". Argument is a route: id or route identifier.
---

# /who-hits-route — All inbound paths to a route

## Argument contract

Single positional argument: a **route** id (`route:...` preferred) OR an identifier-shaped string (path, METHOD /path) → `resolve(identifier=..., hint_kind="route")`.

## Steps

1. **Resolve.** If the argument starts with `route:` or `r:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="route")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `find(kind="route", filter={path_prefix: <arg>})` and stop if still empty.
2. **All inbound:**
   `neighbors({ids: <route_id>, direction: "in", edge_types: ["HTTP_CALLS", "ASYNC_CALLS", "EXPOSES"]})`.
   Render grouped by edge type:
   - `EXPOSES` → handler method Symbol
   - `HTTP_CALLS` → Client nodes (with `attrs.match`, `attrs.confidence`)
   - `ASYNC_CALLS` → Producer nodes (with `attrs.match`, `attrs.confidence`)

## Worked example

User: /who-hits-route POST /chat/join
You: → resolve(identifier="POST /chat/join", hint_kind="route")
   → route:POST /chat/join
   → neighbors({ids: "route:...", direction: "in", edge_types: ["HTTP_CALLS", "ASYNC_CALLS", "EXPOSES"]})
   → returns EXPOSES from handler method + HTTP_CALLS from clients + ASYNC_CALLS from producers

## Out of scope

- In-process callers of a method (use `/callers`).
