---
name: handlers
description: Show the method that handles an HTTP or messaging route. Use when the user asks "what handles X route", "handler for POST /foo", or "which method handles this endpoint". Argument is a route: id or route identifier.
---

# /handlers — Show handler method for a route

## Argument contract

Single positional argument: a **route** id (`route:...` preferred) OR an identifier-shaped string (path, METHOD /path) → `resolve(identifier=..., hint_kind="route")`.

## Steps

1. **Resolve.** If the argument starts with `route:` or `r:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="route")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `find(kind="route", filter={path_prefix: <arg>})` and stop if still empty.
2. **Handler method:**
   `neighbors({ids: <route_id>, direction: "in", edge_types: ["EXPOSES"]})`.
   Render the handler method `fqn` + `microservice`.

## Worked example

User: /handlers route:POST /chat/join
You: → neighbors({ids: "route:POST /chat/join", direction: "in", edge_types: ["EXPOSES"]})
   → returns the handler method Symbol that exposes this route

User: /handlers POST /chat/join
You: → resolve(identifier="POST /chat/join", hint_kind="route")
   → route:POST /chat/join
   → neighbors({ids: "route:...", direction: "in", edge_types: ["EXPOSES"]})
   → returns handler method
