---
name: callees
description: Show what a method symbol calls (in-process CALLS). Use when the user asks "what does X call", "callees of X", "what does X invoke", or "what methods does Y use". Argument is a sym: id, or an identifier resolved via `resolve`. For noisy method bodies prefer /mini-map; for outbound HTTP/async see optional step 3 below.
---

# /callees — In-process callees of a method symbol

## When to use

The user wants the **direct in-process callees** of a method. CALLS edges only — outbound HTTP/async are reached via `DECLARES_CLIENT` / `DECLARES_PRODUCER` then `HTTP_CALLS` / `ASYNC_CALLS` (see optional step 3).

If the method is a hot SERVICE/COMPONENT with > ~30 raw callees, prefer `/mini-map`.

## Tools used

`resolve`, `neighbors`. Optional second `neighbors` chain for HTTP/async. `search` only as a recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: out-edges of CALLS on method
```

**Q-class taxonomy:** `semantic` (`search`), `structured` (`find`/`resolve`), `inspect` (`describe`), **`walk`** (`neighbors`).

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string → `resolve(identifier=..., hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to a route use `/who-hits-route`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it directly. Else `resolve(identifier=<arg>, hint_kind="symbol")` (`one`/`many`/`none` handling per `/callers`).
2. **In-process callees.** Call `neighbors(ids=<sym_id>, direction="out", edge_types=["CALLS"])`.
   Group by callee `fqn` + `microservice`. Mark rows where `attrs.resolved=false` (known-external receivers — JDK/Spring/etc.).
3. **Optional — outbound HTTP/async** (only when the user asks about cross-service calls):
   - HTTP: `neighbors(ids=<sym_id>, direction="out", edge_types=["DECLARES_CLIENT"])` → for each Client id, `neighbors(ids=<client_id>, direction="out", edge_types=["HTTP_CALLS"])`.
   - Async: `neighbors(ids=<sym_id>, direction="out", edge_types=["DECLARES_PRODUCER"])` → for each Producer id, `neighbors(ids=<producer_id>, direction="out", edge_types=["ASYNC_CALLS"])`.

## Noise hint

After ontology 15, true receiver-failure call sites are **not** on `CALLS` — they are `UnresolvedCallSite` nodes (reachable via `include_unresolved=True` on the same `neighbors` call). `attrs.resolved=false` on a `CALLS` row means the callee is known but external (JDK/Spring/Lombok/etc.).

For noisy bodies, prefer one of:
- `/mini-map <sym_id>` — handles accessor/JDK filtering + DELEGATES/PERSISTS/READS labels.
- Pre-filter inline: `neighbors(ids=<sym_id>, direction="out", edge_types=["CALLS"], edge_filter={callee_declaring_role:"SERVICE"})`.

## Recovery

- Empty result but `describe.edge_summary` shows `CALLS.out > 0`: re-resolve — wrong overload.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/callees ChatController#joinOperator(JoinOperatorRequest)`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
  → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
Q-class: walk         Pick: neighbors Why: out-edges of CALLS on method
→ neighbors(ids="sym:...", direction="out", edge_types=["CALLS"])
  → 6 in-process callees (service + repository)
  → (optional) neighbors(out, ["DECLARES_CLIENT"]) → 1 client → HTTP_CALLS → 1 route
```

## Do not

- Do not pass `HTTP_CALLS` or `ASYNC_CALLS` to `neighbors` on a bare method `sym:` — those edges live on Client/Producer nodes (decision #13 in the locked agent-skills propose).
- Do not fabricate `sym:` ids.
- Do not read source files when MCP can answer.

## Out of scope

- Recursive callees beyond depth 1 (use `/trace-request-flow` or `/mini-map`).
- Noise-filtered call maps (use `/mini-map`; fall back here only if the map is too thin).
- Filtering by microservice (compose with `/controllers` + `/callees` per result).

## Going deeper

The full edge-filter axes (`callee_declaring_role`, `min_confidence`, `dedup_calls`, `include_unresolved`) and the `DECLARES_CLIENT → HTTP_CALLS` rationale live in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/callees`.
