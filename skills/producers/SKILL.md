---
name: producers
description: List outbound async producers, optionally filtered by microservice. Use when the user asks "list producers", "show outbound async calls", or "what Kafka producers are in X".
---

# /producers — List outbound async producers

## Argument contract

Optional positional argument: microservice name. Omit to list all producers.

## Steps

1. **Find producers.**
   - With microservice: `find(kind="producer", filter={microservice: <arg>}, limit=100)`.
   - Without microservice: `find(kind="producer", filter={}, limit=100)`.
2. **Render.** Show each result's `fqn`, `microservice`, `producer_kind`, `topic_prefix`, and `id`.

## Worked example

User: /producers chat-core
You: → find(kind="producer", filter={microservice: "chat-core"}, limit=100)
   → returns outbound async producer nodes in chat-core
   → e.g. producer:ChatEventPublisher (kafka_send), topic_prefix=chat-events

User: /producers
You: → find(kind="producer", filter={}, limit=100)
   → returns all outbound async producer nodes
