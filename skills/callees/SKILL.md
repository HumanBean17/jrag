---
name: callees
description: Show what a method symbol calls (in-process CALLS). Use when the user asks "what does X call", "callees of X", or "what does X invoke". Argument is a sym: id, or an identifier resolved via resolve.
---

# /callees — Show callees of a method symbol

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string (FQN fragment, method signature) → `resolve(identifier=..., hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to an HTTP route, use `/who-hits-route`. For outbound Feign/HTTP from a method, see optional step 3 below.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it as the id. Otherwise, call `resolve(identifier=<arg>, hint_kind="symbol")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `search(query=<arg>, limit=5)` and stop if still empty.
2. **In-process callees.** Call `neighbors` with `ids=<sym_id>`, `direction="out"`, `edge_types=["CALLS"]`. Render grouped by edge type; show callee `fqn` + `microservice`.
3. **Optional — outbound HTTP (only when user asks about cross-service calls).** Call `neighbors` with `ids=<sym_id>`, `direction="out"`, `edge_types=["DECLARES_CLIENT"]`. For each client id, call `neighbors` with `ids=<client_id>`, `direction="out"`, `edge_types=["HTTP_CALLS"]`. (Async: `DECLARES_PRODUCER` → `ASYNC_CALLS` on producer ids.)

## Worked example

User: /callees ChatController#joinOperator(JoinOperatorRequest)
You: → resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
   → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids: "sym:...", direction: "out", edge_types: ["CALLS"]})
   → returns CALLS edges to in-process service methods
   → (optional step 3) neighbors(out, ["DECLARES_CLIENT"]) on sym id, then HTTP_CALLS from client ids

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Recursive callees beyond depth 1 (use `/trace-request-flow` or `/mini-map`).
- Noisy CALLS subgraphs on service methods (prefer `/mini-map`; fall back here if the map is too thin).
- Filtering by microservice (compose with `/controllers` if needed).
