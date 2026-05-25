---
name: clients
description: List outbound HTTP clients in the indexed Java codebase, optionally filtered by microservice. Use when the user asks "list clients", "show outbound HTTP calls", "what Feign clients exist in X", or "what HTTP calls does this service make". Returns Client nodes — feign methods, RestTemplate/WebClient call sites, brownfield-annotated outbound calls. Argument is an optional microservice name.
---

# /clients — List outbound HTTP clients

## When to use

The user wants a **list** of outbound HTTP call sites: `@FeignClient` methods, `RestTemplate`/`WebClient`/`HttpClient` call expressions, or brownfield `@CodebaseHttpClient`. Optionally scoped to one microservice.

## Tools used

`find` only.

## Reasoning preamble (mandatory)

```
Q-class: structured  Pick: find  Why: client kind + optional microservice
```

**Q-class taxonomy reminder:** `semantic` (`search`), **`structured`** (`find`), `inspect` (`describe`), `walk` (`neighbors`).

## Argument contract

Optional positional argument: microservice name. Omit to list all clients.

**`microservice` value note:** Not validated; invalid name returns empty.

## Steps

1. **Find.** Call:
   - With microservice: `find(kind="client", filter={microservice:<arg>}, limit=100)`
   - Without: `find(kind="client", filter={}, limit=100)`
2. **Render.** For each row show `fqn`, `microservice`, `client_kind` (e.g. `feign_method`, `rest_template`, `web_client`, `http_call`, `codebase_http`), `target_service` (when known), and `id`.
3. **Narrow if needed.** When results are broad, add `client_kind` or `target_service` to the filter.

## Optional filters

- `client_kind:"feign_method"` — only declared Feign methods
- `client_kind:"http_call"` — call-site clients (RestTemplate/WebClient/raw HttpClient)
- `target_service:"chat-service"` — only clients pointing at one target

## Recovery

- Empty with a microservice argument: re-run without the filter to confirm the microservice name.
- Many results: narrow with `client_kind` or `target_service`.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/clients chat-core`
You:
```
Q-class: structured  Pick: find  Why: client listing for one microservice
→ find(kind="client", filter={microservice:"chat-core"}, limit=100)
  → client:com.bank.chat.core.client.ChatServiceClient#assign  feign_method  target=chat-service
  → client:com.bank.chat.core.client.NotifyClient#send         feign_method  target=notify-service
  → ... 7 more
```

## Do not

- Do not infer clients from training data — they are project-specific.
- Do not read source files when `find` can answer.
- Do not call `search` for this — it's a structured listing.

## Out of scope

- The downstream **route** a client targets (use `neighbors(client_id, "out", ["HTTP_CALLS"])`, or `/trace-request-flow`).
- Async outbound (use `/producers`).
- Who declares a client (use `neighbors(client_id, "in", ["DECLARES_CLIENT"])`).

## Going deeper

The full `Client` schema (every `client_kind` value, when `target_service` is set, brownfield-annotated clients) and the `DECLARES_CLIENT → HTTP_CALLS` pattern are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/clients`.
