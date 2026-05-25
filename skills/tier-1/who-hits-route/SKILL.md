---
name: who-hits-route
description: Show all inbound paths to a route — cross-service HTTP/async callers plus the local handler. Use when the user asks "who calls this endpoint", "who hits this route", "what services call POST /foo", or "all inbound to route X". Argument is a route: id or route identifier. Combines HTTP_CALLS, ASYNC_CALLS, and EXPOSES in one neighbors call.
---

# /who-hits-route — All inbound paths to a route

## When to use

The user has a **route** and wants every inbound edge: cross-service HTTP callers (`HTTP_CALLS` from Client nodes), async producers (`ASYNC_CALLS` from Producer nodes), and the local handler method (`EXPOSES` from a Symbol).

This is the *cross-service* counterpart to `/callers` (which only handles in-process method-to-method).

## Tools used

`resolve` (when argument isn't already `route:`) + `neighbors`. `find` only as recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: all inbound edges of a route
```

## Argument contract

Single positional argument: a **route** id (`route:...` preferred) OR an identifier-shaped string (`/chat/join`, `POST /chat/join`).

## Steps

1. **Resolve.** If argument starts with `route:` or `r:`, use directly. Else `resolve(identifier=<arg>, hint_kind="route")`:
   - `one` → use `node.id`.
   - `many` → list candidates, stop.
   - `none` → `find(kind="route", filter={path_prefix:<arg>})`; if still empty, stop and report.
2. **Walk all inbound.** Call `neighbors(ids=<route_id>, direction="in", edge_types=["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])`.
3. **Render grouped by edge type:**
   - `EXPOSES` → handler method Symbol (always exactly one in well-formed projects).
   - `HTTP_CALLS` → Client nodes. Each row carries `attrs.match` (path/method match strength) and `attrs.confidence`.
   - `ASYNC_CALLS` → Producer nodes. Same `attrs.match` / `attrs.confidence` columns.

## Recovery

- Empty result on a known route: the route exists but has no callers indexed. Confirm with `describe(<route_id>)` (`edge_summary` will show the same).
- Low `attrs.confidence` rows: those are probabilistic matches (path-template inference). Report but flag.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/who-hits-route POST /chat/join`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped route arg
→ resolve(identifier="POST /chat/join", hint_kind="route")
  → status=one  id=route:POST /chat/join
Q-class: walk         Pick: neighbors Why: all inbound edges of route
→ neighbors(ids="route:POST /chat/join", direction="in",
            edge_types=["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])
  → EXPOSES    : sym:com.bank.chat.core.api.ChatController#joinOperator   (chat-core)
  → HTTP_CALLS : client:com.bank.gateway.client.ChatClient#join  (gateway)  match=exact  confidence=1.0
  → ASYNC_CALLS: (none)
```

## Do not

- Do not pass `HTTP_CALLS`/`ASYNC_CALLS` on a bare method `sym:` — those edges originate at Client/Producer nodes, never methods.
- Do not fabricate `route:` ids.

## Out of scope

- In-process callers of a method (use `/callers`).
- Following the request forward from the handler (use `/trace-request-flow`).
- The handler alone (use `/handlers`).

## Going deeper

`HTTP_CALLS` / `ASYNC_CALLS` schema (always Client→Route / Producer→Route, never one-hop from method) and the `attrs.match` semantics are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/who-hits-route`.
