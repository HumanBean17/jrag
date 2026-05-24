---
name: clients
description: List outbound HTTP clients, optionally filtered by microservice. Use when the user asks "list clients", "show outbound HTTP calls", or "what Feign clients are in X".
---

# /clients — List outbound HTTP clients

## Argument contract

Optional positional argument: microservice name. Omit to list all clients.

## Steps

1. **Find clients.**
   - With microservice: `find(kind="client", filter={microservice: <arg>}, limit=100)`.
   - Without microservice: `find(kind="client", filter={}, limit=100)`.
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
