---
name: impact-of
description: Analyze what breaks if a symbol changes by walking inbound edges recursively with bounded depth. Use when the user asks "what breaks if I change X", "impact of changing X", "who depends on X", or "blast radius of modifying Y". Argument is a sym: id or identifier resolved via `resolve`. Covers CALLS, INJECTS, IMPLEMENTS, EXTENDS plus route/client impact when applicable.
---

# /impact-of — What breaks if this changes

## When to use

The user wants the **blast radius** of changing a symbol: who calls it, who injects it, who implements it, and (for methods on routes / methods declaring clients) what crosses service boundaries.

This is the reverse direction of `/trace-request-flow`. For a *forward* request walk use that skill instead.

## Tools used

`resolve`, `describe`, `neighbors`. `search` only as a recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

This skill typically uses `structured → inspect → walk → walk ...`.

## Argument contract

Single positional argument: a Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple name) → `resolve(identifier=..., hint_kind="symbol")`.

## Steps

1. **Resolve.** If argument starts with `sym:`, use directly. Else `resolve(identifier=<arg>, hint_kind="symbol")` (`one`/`many`/`none` handling per Tier 1 skills).
2. **Inspect.** Call `describe(id=<sym_id>)`. Read `edge_summary` and `role`.
3. **Recursive inbound walk (depth ≤ 2).** Call `neighbors(ids=<sym_id>, direction="in", edge_types=["CALLS","INJECTS","IMPLEMENTS","EXTENDS"])`. For each inbound neighbor that is a **method** symbol, repeat the same call on that id (one more hop only).
4. **Route/client impact** (when applicable):
   - If the symbol is a method that handles a route: `neighbors(<sym_id>, "out", ["EXPOSES"])` to find the route, then `neighbors(<route_id>, "in", ["HTTP_CALLS","ASYNC_CALLS"])` for callers outside the codebase. Each row is a Client or Producer in another service.
   - If the symbol declares clients: `neighbors(<sym_id>, "out", ["DECLARES_CLIENT"])`, then `neighbors(<client_id>, "out", ["HTTP_CALLS"])` for affected downstream services.
5. **Render impact list.** Deduplicate. Group as:
   - **Direct (depth 1):** callers, injectors, implementors, extenders.
   - **Transitive (depth 2):** callers-of-callers, injectors-of-callers.
   - **Cross-service:** route / client impact when applicable.
   - Cite each entry by `sym:` / `route:` / `client:` id.

## Stop conditions

- Depth limit reached (≤ 2 hops on the inbound walk).
- No more inbound edges to follow.
- Cycle detected (node already in impact set).
- After two empty/failed `neighbors` calls in a row, stop walking that branch.

## Recursion limit

- Depth ≤ 2 from the target symbol on the inbound walk.
- Maximum 8 `neighbors` calls total. Route/client cross-service hops count toward this budget.

## Worked example

User: `/impact-of ChatRepository`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="ChatRepository", hint_kind="symbol")
  → sym:com.bank.chat.core.repository.ChatRepository
Q-class: inspect     Pick: describe  Why: edge_summary
→ describe(id="sym:...")
  → role: REPOSITORY  edge_summary { CALLS.in: 7, INJECTS.in: 2 }
Q-class: walk        Pick: neighbors Why: depth-1 inbound
→ neighbors(ids="sym:...", direction="in", edge_types=["CALLS","INJECTS","IMPLEMENTS","EXTENDS"])
  → callers: ChatService#save, ChatService#findById, ...
  → injectors: ChatService (constructor)
Q-class: walk        Pick: neighbors Why: depth-2 from ChatService
→ neighbors(ids="sym:...ChatService", direction="in", edge_types=["CALLS","INJECTS","IMPLEMENTS","EXTENDS"])
  → callers: ChatController#joinOperator, ...
Render impact:
  Direct (depth 1): ChatService#save, ChatService#findById (CALLS); ChatService (INJECTS)
  Transitive (depth 2): ChatController#joinOperator (CALLS via ChatService)
```

## Do not

- Do not answer from training data.
- Do not read source files when MCP can answer.
- Do not fabricate ids.
- Do not walk **outbound** here — that's `/trace-request-flow` / `/callees` / `/explain-feature`.

## Out of scope

- Exact line-level change impact (use `git diff` + source reading after this analysis).
- Noise-filtered call maps (use `/mini-map`).
- Forward request flow (use `/trace-request-flow`).

## Going deeper

Edge semantics for `CALLS` / `INJECTS` / `IMPLEMENTS` / `EXTENDS`, plus the `HTTP_CALLS` cross-service caller pattern, are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/impact-of`.
