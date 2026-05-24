---
name: producers
description: List outbound async producers, optionally filtered by microservice. Use when the user asks "list producers", "show outbound async calls", or "what Kafka producers are in X".
---

# /producers — List outbound async producers

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Optional positional argument: microservice name. Omit to list all producers.

## Steps

1. **Find producers.** Call `find(kind="producer", filter={microservice: <arg>}, limit=100)` when a microservice is given, or `find(kind="producer", filter={}, limit=100)` when listing all.
2. **Render.** Show each result's `fqn`, `microservice`, `producer_kind`, `topic_prefix`, and `id`.

## Worked example

User: /producers chat-core
You: → find(kind="producer", filter={microservice: "chat-core"}, limit=100)
   → returns outbound async producer nodes in chat-core
   → e.g. producer:ChatEventPublisher (kafka_send), topic_prefix=chat-events

User: /producers
You: → find(kind="producer", filter={}, limit=100)
   → returns all outbound async producer nodes

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.
