---
name: routes
description: List HTTP and messaging routes, optionally filtered by microservice. Use when the user asks "list routes", "show me endpoints", or "what routes are in X".
---

# /routes — List HTTP and messaging routes

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Optional positional argument: microservice name. Omit to list all routes.

## Steps

1. **Find routes.** Call `find(kind="route", filter={microservice: <arg>})` when a microservice is given, or `find(kind="route", filter={})` when listing all.
2. **Render.** Show each result's `fqn` (HTTP method + path), `microservice`, `framework`, and `id`.

## Worked example

User: /routes chat-assign
You: → find(kind="route", filter={microservice: "chat-assign"})
   → returns routes in chat-assign microservice
   → e.g. route:POST /chat/assign, route:GET /chat/status

User: /routes
You: → find(kind="route", filter={})
   → returns all routes across all microservices

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
