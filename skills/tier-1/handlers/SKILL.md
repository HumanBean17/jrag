---
name: handlers
description: Show the method that handles an HTTP or messaging route via the EXPOSES edge. Use when the user asks "what handles X route", "handler for POST /foo", "which method handles this endpoint", or "find the controller method for path Y". Argument is a route: id or a route identifier (path, METHOD /path) resolved via `resolve`.
---

# /handlers — Handler method for a route

## When to use

The user has a **route** (path, METHOD /path, async topic, or `route:` id) and wants the method `sym:` that handles it. The edge is `Symbol —EXPOSES→ Route`, so the handler is the *in-neighbor* of the route.

## Tools used

`resolve` (when argument isn't already `route:`) + `neighbors`. `find` only as recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: in-edges of EXPOSES on route
```

If `resolve` runs first, that one is `Q-class: structured`.

## Argument contract

Single positional argument: a **route** id (`route:...` preferred) OR an identifier-shaped string (path like `/chat/join` or `METHOD /path`).

## Steps

1. **Resolve.** If argument starts with `route:` or `r:`, use directly. Else `resolve(identifier=<arg>, hint_kind="route")`:
   - `status="one"` → use `node.id`.
   - `status="many"` → list candidates, stop.
   - `status="none"` → `find(kind="route", filter={path_prefix:<arg>})`; if still empty, stop and report.
2. **Walk EXPOSES inbound.** Call `neighbors(ids=<route_id>, direction="in", edge_types=["EXPOSES"])`.
3. **Render.** Show the handler method `fqn` + `microservice` + (parent class via `describe` if useful).

## Recovery

- Multiple EXPOSES neighbors (rare): possible duplicate mapping across frameworks. Report all; let the user disambiguate.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/handlers POST /chat/join`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped route arg
→ resolve(identifier="POST /chat/join", hint_kind="route")
  → status=one  id=route:POST /chat/join
Q-class: walk         Pick: neighbors Why: in-edges of EXPOSES on route
→ neighbors(ids="route:POST /chat/join", direction="in", edge_types=["EXPOSES"])
  → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)  microservice=chat-core
```

## Do not

- Do not fabricate `route:` ids — always obtain them from `resolve` or `find`.
- Do not read source files when MCP can answer.

## Out of scope

- All inbound paths to a route — cross-service `HTTP_CALLS` + async `ASYNC_CALLS` + handler `EXPOSES` (use `/who-hits-route`).
- Following the request through the call graph (use `/trace-request-flow`).

## Going deeper

`EXPOSES` semantics (always Symbol→Route, one-to-one in well-formed projects) and the full `find(kind="route")` filter set are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/handlers`.
