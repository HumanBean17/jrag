---
name: callers
description: Show in-process callers of a method symbol via CALLS edges. Use when the user asks "who calls X", "callers of X", "what invokes X method", or "find usages of method Y". Argument is a sym: id or an identifier resolved via `resolve`. For inbound HTTP/async traffic to a route use /who-hits-route; for recursive impact analysis use /impact-of.
---

# /callers — In-process callers of a method symbol

## When to use

The user wants the **direct in-process callers** of a method. CALLS edges are method-Symbol → method-Symbol within the indexed graph (no cross-service HTTP/async — those are different edges).

## Tools used

`resolve` (when argument isn't already a `sym:` id) + `neighbors`. `search` only as a recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: in-edges of CALLS on method
```

If you call `resolve` first, that one uses `Q-class: structured` (id-shaped input).

**Q-class taxonomy:** `semantic` (`search`), `structured` (`find` / `resolve` for id-shaped strings), `inspect` (`describe`), **`walk`** (`neighbors`).

## Argument contract

Single positional argument: a method **symbol** id (`sym:...` preferred) OR an identifier-shaped string (FQN fragment, `Class#method`, signature).

This skill is for **method symbols**. For inbound traffic to an HTTP/async **route**, use `/who-hits-route`.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it directly.
   Else call `resolve(identifier=<arg>, hint_kind="symbol")`.
   - `status="one"` → use `node.id`.
   - `status="many"` → list `candidates` and stop for user pick.
   - `status="none"` → call `search(query=<arg>, limit=5)`; if still empty, stop and report.
2. **Walk inbound CALLS.** Call `neighbors(ids=<sym_id>, direction="in", edge_types=["CALLS"])`.
3. **Render.** Group by caller `fqn` + `microservice`. Show count when one caller has multiple call sites (the edge rows include `call_site_line`).

## Recovery

- `neighbors` returns empty but `describe(<sym_id>)` shows `CALLS.in > 0`: re-run with the exact `id` from `describe`; the symbol you resolved may be the wrong overload.
- Resolved id is for a **type** Symbol, not a method: use `/implements` or `/injects` instead.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/callers ChatController#joinOperator(JoinOperatorRequest)`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
  → status=one  id=sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
Q-class: walk         Pick: neighbors Why: in-edges of CALLS on method
→ neighbors(ids="sym:...", direction="in", edge_types=["CALLS"])
  → 3 callers in chat-core, 1 in chat-assign
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files when MCP can answer.
- Do not fabricate `sym:` ids — always obtain them from `resolve` / `find` / `search`.
- Do not pass `HTTP_CALLS` or `ASYNC_CALLS` to `neighbors` on a bare method `sym:` — those edges live on Client/Producer nodes.

## Out of scope

- Callers of a **route** (use `/who-hits-route`).
- Recursive callers beyond depth 1 (use `/impact-of`).
- Outbound callees (use `/callees`).

## Going deeper

CALLS edge details (call-site columns, what `attrs.resolved=false` means after ontology 15) and the full recovery playbook live in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/callers`.
