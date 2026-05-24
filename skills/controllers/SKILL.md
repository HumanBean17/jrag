---
name: controllers
description: List controller classes, optionally filtered by microservice. Use when the user asks "list controllers", "show me controllers in X", or "what controllers are there".
---

# /controllers — List controllers

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Optional positional argument: microservice name. Omit to list all controllers.

## Steps

1. **Find controllers.** Call `find(kind="symbol", filter={role: "CONTROLLER", microservice: <arg>})` when a microservice is given, or `find(kind="symbol", filter={role: "CONTROLLER"})` when listing all.
2. **Render.** Show each result's `fqn`, `microservice`, and `id`.

## Worked example

User: /controllers chat-core
You: → find(kind="symbol", filter={role: "CONTROLLER", microservice: "chat-core"})
   → returns controller symbols in chat-core microservice
   → e.g. sym:com.bank.chat.core.api.ChatController

User: /controllers
You: → find(kind="symbol", filter={role: "CONTROLLER"})
   → returns all controller symbols across all microservices

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
