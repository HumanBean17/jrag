---
name: clients
description: List outbound HTTP clients, optionally filtered by microservice. Use when the user asks "list clients", "show outbound HTTP calls", or "what Feign clients are in X".
---

# /clients — List outbound HTTP clients

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Optional positional argument: microservice name. Omit to list all clients.

## Steps

1. **Find clients.** Call `find(kind="client", filter={microservice: <arg>}, limit=100)` when a microservice is given, or `find(kind="client", filter={}, limit=100)` when listing all.
2. **Render.** Show each result's `fqn`, `microservice`, `client_kind`, `target_service`, and `id`.
3. **Narrow if needed.** When results are broad, add `client_kind` or `target_service` to the filter.

## Worked example

User: /clients chat-core
You: → find(kind="client", filter={microservice: "chat-core"}, limit=100)
   → returns outbound HTTP client nodes in chat-core
   → e.g. client:ChatServiceClient (feign_method), target_service=chat-service

User: /clients
You: → find(kind="client", filter={}, limit=100)
   → returns all outbound HTTP client nodes

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
