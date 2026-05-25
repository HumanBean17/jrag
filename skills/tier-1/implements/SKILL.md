---
name: implements
description: Show concrete classes that implement an interface or abstract type. Use when the user asks "what implements X", "implementations of X", "concrete types for interface X", or "subclasses of X". Argument is a type sym: id or identifier resolved via `resolve`. Uses the IMPLEMENTS edge (also follow EXTENDS for class hierarchy).
---

# /implements — Concrete implementors of a type

## When to use

The user has an **interface** (or abstract class) Symbol and wants its concrete implementors. The edge is `Concrete —IMPLEMENTS→ Interface`, so implementors are the *in-neighbors*.

For class-hierarchy parents/children use `EXTENDS` instead — see optional step 3.

## Tools used

`resolve` + `neighbors`. `search` only as a recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: in-edges of IMPLEMENTS on type
```

## Argument contract

Single positional argument: a **type** Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple class name).

## Steps

1. **Resolve.** If argument starts with `sym:`, use directly. Else `resolve(identifier=<arg>, hint_kind="symbol")`:
   - `one` → use `node.id`.
   - `many` → list candidates, stop.
   - `none` → `search(query=<arg>, limit=5)`; if still empty, stop and report.
2. **Walk IMPLEMENTS inbound.** Call `neighbors(ids=<type_sym_id>, direction="in", edge_types=["IMPLEMENTS"])`. Each row is a concrete implementor.
3. **Optional — also follow EXTENDS** (when the user asks for the *full* subtype tree): `neighbors(ids=<type_sym_id>, direction="in", edge_types=["IMPLEMENTS","EXTENDS"])`.
4. **Render.** Show each implementor's `fqn` + `microservice`. Note if any are themselves interfaces (rare — multi-level interfaces).

## Recovery

- Empty result but the type is clearly an interface used in the codebase: confirm the resolved id is the **type** Symbol, not a method on it. `describe(<id>)` should show `symbol_kind: "interface"` or `"class"`.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/implements OperatorAssignmentService`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
  → status=one  id=sym:com.bank.chat.assign.service.OperatorAssignmentService  (interface)
Q-class: walk         Pick: neighbors Why: in-edges of IMPLEMENTS on type
→ neighbors(ids="sym:...", direction="in", edge_types=["IMPLEMENTS"])
  → sym:com.bank.chat.assign.service.RoundRobinOperatorAssignmentService     (chat-assign)
  → sym:com.bank.chat.assign.service.WeightedOperatorAssignmentService       (chat-assign)
```

## Do not

- Do not enumerate implementors from training data — they are project-specific.
- Do not fabricate `sym:` ids.

## Out of scope

- Where the type is injected (use `/injects`).
- Per-method overrides — `IMPLEMENTS` is **type→type**; the per-method counterpart is `OVERRIDES` on a method id (composed key `OVERRIDDEN_BY.IMPLEMENTS`/`OVERRIDDEN_BY.EXTENDS` walks both directions).

## Going deeper

The `IMPLEMENTS` vs `EXTENDS` vs `OVERRIDES` distinction and the composed `OVERRIDDEN_BY.*` navigation are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/implements`.
