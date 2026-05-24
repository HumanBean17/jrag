---
name: controllers
description: List controller classes, optionally filtered by microservice. Use when the user asks "list controllers", "show me controllers in X", or "what controllers are there".
---

# /controllers — List controllers

## Argument contract

Optional positional argument: microservice name. Omit to list all controllers.

## Steps

1. **Find controllers.**
   - With microservice: `find(kind="symbol", filter={role: "CONTROLLER", microservice: <arg>})`.
   - Without microservice: `find(kind="symbol", filter={role: "CONTROLLER"})`.
2. **Render.** Show each result's `fqn`, `microservice`, and `id`.

## Worked example

User: /controllers chat-core
You: → find(kind="symbol", filter={role: "CONTROLLER", microservice: "chat-core"})
   → returns controller symbols in chat-core microservice
   → e.g. sym:com.bank.chat.core.api.ChatController

User: /controllers
You: → find(kind="symbol", filter={role: "CONTROLLER"})
   → returns all controller symbols across all microservices
