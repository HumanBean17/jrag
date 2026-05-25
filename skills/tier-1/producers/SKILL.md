---
name: producers
description: List outbound async producers in the indexed Java codebase, optionally filtered by microservice. Use when the user asks "list producers", "show outbound async calls", "what Kafka producers are in X", or "list message senders". Returns Producer nodes — KafkaTemplate / StreamBridge call sites and brownfield-annotated producers. Argument is an optional microservice name.
---

# /producers — List outbound async producers

## When to use

The user wants a **list** of outbound async call sites: `KafkaTemplate.send`, `StreamBridge.send`, or brownfield `@CodebaseProducer`. Optionally scoped to one microservice. Symmetric counterpart to `/clients` for async.

## Tools used

`find` only.

## Reasoning preamble (mandatory)

```
Q-class: structured  Pick: find  Why: producer kind + optional microservice
```

**Q-class taxonomy reminder:** `semantic` (`search`), **`structured`** (`find`), `inspect` (`describe`), `walk` (`neighbors`).

## Argument contract

Optional positional argument: microservice name. Omit to list all producers.

**`microservice` value note:** Not validated; invalid name returns empty.

## Steps

1. **Find.** Call:
   - With microservice: `find(kind="producer", filter={microservice:<arg>}, limit=100)`
   - Without: `find(kind="producer", filter={}, limit=100)`
2. **Render.** For each row show `fqn`, `microservice`, `producer_kind` (e.g. `kafka_send`, `stream_bridge`, `codebase_producer`), `topic_prefix` (when known), and `id`.

## Recovery

- Empty with a microservice argument: re-run without the filter to confirm the microservice name.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/producers chat-core`
You:
```
Q-class: structured  Pick: find  Why: producer listing for one microservice
→ find(kind="producer", filter={microservice:"chat-core"}, limit=100)
  → producer:com.bank.chat.core.publisher.ChatEventPublisher  kafka_send  topic_prefix=chat-events
  → producer:com.bank.chat.core.publisher.AuditPublisher       kafka_send  topic_prefix=audit
```

## Do not

- Do not infer producers from training data — they are project-specific.
- Do not read source files when `find` can answer.
- Do not call `search` for this.

## Out of scope

- Outbound HTTP (use `/clients`).
- The downstream async **route** a producer targets (use `neighbors(producer_id, "out", ["ASYNC_CALLS"])`).
- Who declares a producer (use `neighbors(producer_id, "in", ["DECLARES_PRODUCER"])`).

## Going deeper

The full `Producer` schema (every `producer_kind` value, `topic_prefix` semantics, brownfield-annotated producers) and the `DECLARES_PRODUCER → ASYNC_CALLS` pattern are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/producers`.
