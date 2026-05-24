---
name: impact-of
description: Analyze what breaks if a symbol changes. Use when the user asks "what breaks if I change X", "impact of changing X", or "who depends on X". Argument is a sym: id or identifier.
---

# /impact-of — What breaks if this changes

## MCP required

This skill requires the **java-codebase-rag** MCP server (tools: `search`, `find`, `describe`, `neighbors`, `resolve`).

**You MUST call these MCP tools to answer.** Do not answer from training data, file browsing, or general knowledge. Each MCP call must be preceded by the reasoning preamble:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <tool>  Why: <reason>
```

For the full operating manual (NodeFilter keys, edge taxonomy, argument shapes, recovery playbook), read `docs/AGENT-GUIDE.md`.

## Argument contract

Single positional argument: a Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it as the id. Otherwise, call `resolve(identifier=<arg>, hint_kind="symbol")`. On status `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `search(query=<arg>, limit=5)` and stop if still empty.
2. **Inspect.** Call `describe(id=<sym_id>)`. Read `edge_summary` and `role` to understand the node's position.
3. **Recursive inbound walk** (depth ≤ 2). Call `neighbors` with `ids=<sym_id>`, `direction="in"`, `edge_types=["CALLS", "INJECTS", "IMPLEMENTS", "EXTENDS"]`. For each inbound neighbor that is a method symbol, call `neighbors` again with `ids=<caller_id>`, `direction="in"`, `edge_types=["CALLS", "INJECTS", "IMPLEMENTS", "EXTENDS"]`.
4. **Route/client impact** (when applicable):
   - If the symbol is a method with routes: call `neighbors` with `direction="out"`, `edge_types=["EXPOSES"]`, then call `neighbors` with `direction="in"`, `edge_types=["HTTP_CALLS", "ASYNC_CALLS"]` on route ids for callers outside the codebase.
   - If the symbol declares clients: call `neighbors` with `direction="out"`, `edge_types=["DECLARES_CLIENT"]`, then call `neighbors` with `direction="out"`, `edge_types=["HTTP_CALLS"]` for affected downstream services.
5. **Render impact list.** Deduplicate results. Group by:
   - Direct callers/injectors (depth 1)
   - Transitive dependents (depth 2)
   - Route-level impact (external callers)

## Stop conditions

- Depth limit reached (≤ 2 hops).
- No more inbound edges to walk.
- Cycle detected (node already in impact set).

## Recursion limit

- Depth ≤ 2 from the target symbol.
- Maximum 8 `neighbors` calls total.

## Worked example

User: /impact-of ChatRepository
You: → resolve(identifier="ChatRepository", hint_kind="symbol")
   → sym:com.bank.chat.core.repository.ChatRepository
   → describe(id="sym:...") → edge_summary shows CALLS in, INJECTS in
   → neighbors(in, ["CALLS", "INJECTS", "IMPLEMENTS", "EXTENDS"])
   → callers: ChatService#save, ChatService#findById
   → injectors: ChatService (constructor injection)
   → neighbors(in, ["CALLS", "INJECTS"]) on ChatService
   → callers of ChatService: ChatController methods
   → impact: ChatRepository → ChatService → ChatController (depth 2)

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files directly when MCP tools can provide the answer.
- Do not skip MCP calls and guess at results.
- Do not fabricate symbol ids — always obtain them from `resolve`, `find`, or `search`.

## Out of scope

- Exact line-level change impact (use `git diff` + source reading).
- Noise-filtered call maps (use `/mini-map`).
