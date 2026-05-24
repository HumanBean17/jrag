---
name: trace-request-flow
description: Follow a request from HTTP entry point through the call chain to persistence or async boundaries. Use when the user asks "trace POST /foo", "follow the request for X", or "what happens when X is called". Argument is a route id, METHOD /path string, or route identifier.
---

# /trace-request-flow — Follow a request end-to-end

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a route identifier — `route:` id, `METHOD /path` string, or path fragment → `resolve(identifier=..., hint_kind="route")` or `find(kind="route", filter={path_prefix: ...})`.

## Steps

1. **Resolve route.** If argument starts with `route:` or `r:`, use it directly. Otherwise, call `resolve(identifier=<arg>, hint_kind="route")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `find(kind="route", filter={path_prefix: <arg>})` and stop if still empty.
2. **Handler method.** Call `neighbors` with `ids=<route_id>`, `direction="in"`, `edge_types=["EXPOSES"]` to get the handler method `sym:` id.
3. **Walk call chain** (depth ≤ 4 on methods). Call `neighbors` with `ids=<method_id>`, `direction="out"`, `edge_types=["CALLS"]`. For each callee method:
   - If it is a SERVICE/COMPONENT method likely to delegate further, recurse one more hop.
   - If it is a REPOSITORY/MAPPER, classify as persistence and stop on that branch.
4. **Cross-service boundaries.** At methods with outbound clients: call `neighbors` with `direction="out"`, `edge_types=["DECLARES_CLIENT"]` on method id, then call `neighbors` with `direction="out"`, `edge_types=["HTTP_CALLS"]` on each client id. At methods with async producers: call `neighbors` with `direction="out"`, `edge_types=["DECLARES_PRODUCER"]` on method id, then call `neighbors` with `direction="out"`, `edge_types=["ASYNC_CALLS"]` on each producer id.
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

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate route/symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Full noise-filtered call map (use `/mini-map` for single-method deep dives).
- Impact analysis beyond the forward path (use `/impact-of`).
