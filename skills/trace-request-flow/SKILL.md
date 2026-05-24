---
name: trace-request-flow
description: Follow a request from HTTP entry point through the call chain to persistence or async boundaries. Use when the user asks "trace POST /foo", "follow the request for X", or "what happens when X is called". Argument is a route id, METHOD /path string, or route identifier.
---

# /trace-request-flow — Follow a request end-to-end

## Argument contract

Single positional argument: a route identifier — `route:` id, `METHOD /path` string, or path fragment → `resolve(identifier=..., hint_kind="route")` or `find(kind="route", filter={path_prefix: ...})`.

## Steps

1. **Resolve route.**
   - If argument starts with `route:` or `r:`, use it directly.
   - Otherwise: `resolve(identifier=<arg>, hint_kind="route")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `find(kind="route", filter={path_prefix: <arg>})` and stop if still empty.
2. **Handler method.**
   `neighbors({ids: <route_id>, direction: "in", edge_types: ["EXPOSES"]})` → handler method `sym:` id.
3. **Walk call chain** (depth ≤ 4 on methods):
   `neighbors({ids: <method_id>, direction: "out", edge_types: ["CALLS"]})`.
   For each callee method:
   - If it is a SERVICE/COMPONENT method likely to delegate further, recurse one more hop.
   - If it is a REPOSITORY/MAPPER, classify as persistence and stop on that branch.
4. **Cross-service boundaries:**
   At methods with outbound clients: `neighbors(out, ["DECLARES_CLIENT"])` on method id
   → `neighbors(out, ["HTTP_CALLS"])` on each client id.
   At methods with async producers: `neighbors(out, ["DECLARES_PRODUCER"])` on method id
   → `neighbors(out, ["ASYNC_CALLS"])` on each producer id.
5. **Render ordered sequence.** Show the flow as:
   `Route → Handler → Service → ... → Repository / Client / Producer`
   with edge annotations at boundaries.

## Stop conditions

- Depth limit reached (≤ 4 from handler).
- No more `CALLS` edges to follow.
- All branches terminated at REPOSITORY, MAPPER, CLIENT, or PRODUCER endpoints.
- Cycle detected (method already in trace).

## Recursion limit

- Depth ≤ 4 from handler method.
- Maximum 10 `neighbors` calls total.

## Worked example

User: /trace-request-flow POST /chat/join
You: → resolve(identifier="POST /chat/join", hint_kind="route")
   → route:POST /chat/join
   → neighbors(in, ["EXPOSES"]) → sym:ChatController#joinOperator
   → neighbors(out, ["CALLS"]) → sym:ChatService#join
   → neighbors(out, ["CALLS"]) on ChatService#join → Repository#save
   → (optional) neighbors(out, ["DECLARES_CLIENT"]) → client ids
   → Render: POST /chat/join → ChatController#joinOperator → ChatService#join → Repository#save

## Out of scope

- Full noise-filtered call map (use `/mini-map` for single-method deep dives).
- Impact analysis beyond the forward path (use `/impact-of`).
