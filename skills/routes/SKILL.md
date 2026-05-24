---
name: routes
description: List HTTP and messaging routes, optionally filtered by microservice. Use when the user asks "list routes", "show me endpoints", or "what routes are in X".
---

# /routes — List HTTP and messaging routes

## Argument contract

Optional positional argument: microservice name. Omit to list all routes.

## Steps

1. **Find routes.**
   - With microservice: `find(kind="route", filter={microservice: <arg>})`.
   - Without microservice: `find(kind="route", filter={})`.
2. **Render.** Show each result's `fqn` (HTTP method + path), `microservice`, `framework`, and `id`.

## Worked example

User: /routes chat-assign
You: → find(kind="route", filter={microservice: "chat-assign"})
   → returns routes in chat-assign microservice
   → e.g. route:POST /chat/assign, route:GET /chat/status

User: /routes
You: → find(kind="route", filter={})
   → returns all routes across all microservices
