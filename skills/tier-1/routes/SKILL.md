---
name: routes
description: List HTTP and messaging routes in the indexed Java codebase, optionally filtered by microservice. Use when the user asks "list routes", "show me endpoints", "list REST APIs", "what HTTP routes are in X", or "list Kafka listeners". Returns Route nodes (both HTTP and async). Argument is an optional microservice name.
---

# /routes — List HTTP and messaging routes

## When to use

The user wants a **list** of routes: HTTP endpoints (`@GetMapping` etc., or brownfield `@CodebaseHttpRoute`) and async inbound topics (`@KafkaListener`, `@RabbitListener`, or brownfield `@CodebaseAsyncRoute`). Optionally scoped to one microservice.

## Tools used

`find` only.

## Reasoning preamble (mandatory)

```
Q-class: structured  Pick: find  Why: route kind + optional microservice
```

**Q-class taxonomy reminder:** `semantic` (NL → `search`), **`structured`** (`find`), `inspect` (`describe`), `walk` (`neighbors`).

## Argument contract

Optional positional argument: microservice name. Omit to list all routes.

**`microservice` value note:** Not validated by the MCP — invalid name returns an empty list. Verify with `find(kind="microservice", filter={})` if results look wrong.

## Steps

1. **Find.** Call:
   - With microservice: `find(kind="route", filter={microservice:<arg>})`
   - Without: `find(kind="route", filter={})`
2. **Render.** For each row show `fqn` (HTTP method + path, or async topic), `microservice`, `framework` (e.g. `spring_mvc`, `spring_kafka`, `codebase_http`), and `id`. Group by microservice if no filter.

## Optional filters to narrow

- `path_prefix:"/chat"` — HTTP routes under a path
- `topic_prefix:"chat-events"` — async topics under a prefix
- `framework:"spring_mvc"` — only HTTP routes
- `framework:"spring_kafka"` — only Kafka listeners

These compose with `microservice`. Brownfield-annotated routes have `framework` values starting with `codebase_`.

## Recovery

- Empty with a microservice argument: re-run without the filter; if non-empty, the name is wrong.
- Empty without any filter: confirm the index is built (`describe(id="meta:index")` if available, else ask the user to re-run `java-codebase-rag init`).
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/routes chat-assign`
You:
```
Q-class: structured  Pick: find  Why: route listing for one microservice
→ find(kind="route", filter={microservice:"chat-assign"})
  → route:POST /chat/assign        microservice=chat-assign  framework=spring_mvc
  → route:GET /chat/status         microservice=chat-assign  framework=spring_mvc
  → route:chat-events.assigned     microservice=chat-assign  framework=spring_kafka
```

## Do not

- Do not enumerate routes from training-data Spring conventions — the route set is per-project.
- Do not read source files when `find` can answer.
- Do not call `search` for this — it's a structured listing.

## Out of scope

- The handler method for a route (use `/handlers`).
- All inbound paths to a route (use `/who-hits-route`).
- Following a route end-to-end (use `/trace-request-flow`).

## Going deeper

Full route schema (HTTP vs async, framework values, brownfield-annotated routes) is in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/routes`.
