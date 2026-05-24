---
name: impact-of
description: Analyze what breaks if a symbol changes. Use when the user asks "what breaks if I change X", "impact of changing X", or "who depends on X". Argument is a sym: id or identifier.
---

# /impact-of — What breaks if this changes

## Argument contract

Single positional argument: a Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **Inspect.**
   `describe(id=<sym_id>)` → read `edge_summary` and `role` to understand the node's position.
3. **Recursive inbound walk** (depth ≤ 2):
   `neighbors({ids: <sym_id>, direction: "in", edge_types: ["CALLS", "INJECTS", "IMPLEMENTS", "EXTENDS"]})`.
   For each inbound neighbor that is a method symbol:
   `neighbors({ids: <caller_id>, direction: "in", edge_types: ["CALLS", "INJECTS", "IMPLEMENTS", "EXTENDS"]})`.
4. **Route/client impact** (when applicable):
   - If the symbol is a method with routes: `neighbors(out, ["EXPOSES"])` → then `neighbors(in, ["HTTP_CALLS", "ASYNC_CALLS"])` on route ids for callers outside the codebase.
   - If the symbol declares clients: `neighbors(out, ["DECLARES_CLIENT"])` → `neighbors(out, ["HTTP_CALLS"])` for affected downstream services.
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

## Out of scope

- Exact line-level change impact (use `git diff` + source reading).
- Noise-filtered call maps (use `/mini-map`).
