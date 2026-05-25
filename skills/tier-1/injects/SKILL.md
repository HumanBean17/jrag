---
name: injects
description: Show where a type is injected via dependency injection. Use when the user asks "where is X injected", "who injects X", "what depends on X via DI", or "find DI consumers of bean X". Argument is a type sym: id or identifier resolved via `resolve`. Uses the INJECTS edge (covers constructor, field, and setter injection).
---

# /injects — Where a type is injected via DI

## When to use

The user has a **type** Symbol (interface, abstract class, or concrete bean) and wants the call sites where it's injected via DI — Spring constructor / field / setter injection, Lombok `@RequiredArgsConstructor`, brownfield equivalents.

The edge is `Consumer —INJECTS→ Type`, so consumers are the *in-neighbors*.

## Tools used

`resolve` + `neighbors`. `search` only as a recovery fallback.

## Reasoning preamble (mandatory)

```
Q-class: walk  Pick: neighbors  Why: in-edges of INJECTS on type
```

## Argument contract

Single positional argument: a **type** Symbol id (`sym:...` preferred) OR an identifier-shaped string (FQN, simple class name).

## Steps

1. **Resolve.** If argument starts with `sym:`, use directly. Else `resolve(identifier=<arg>, hint_kind="symbol")`:
   - `one` → use `node.id`.
   - `many` → list candidates, stop.
   - `none` → `search(query=<arg>, limit=5)`; if still empty, stop and report.
2. **Walk INJECTS inbound.** Call `neighbors(ids=<type_sym_id>, direction="in", edge_types=["INJECTS"])`.
3. **Render.** For each row show consumer `fqn` + `microservice` + edge `attrs.mechanism` (e.g. `constructor`, `field`, `setter`, `lombok_required_args`) + `attrs.field_or_param` (the field or parameter name).

## Recovery

- Empty result but the type is clearly a bean: confirm resolved id is the type Symbol (`describe(<id>)` → `symbol_kind:"class"|"interface"`).
- For interface types, also check `/implements <id>` — if no implementor is annotated as a Spring bean, the type may not be DI-managed at all.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/injects OperatorAssignmentService`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="OperatorAssignmentService", hint_kind="symbol")
  → sym:com.bank.chat.assign.service.OperatorAssignmentService
Q-class: walk         Pick: neighbors Why: in-edges of INJECTS on type
→ neighbors(ids="sym:...", direction="in", edge_types=["INJECTS"])
  → sym:com.bank.chat.assign.api.AssignController    constructor  field_or_param=service
  → sym:com.bank.chat.assign.scheduler.HealthCheck   field        field_or_param=service
```

## Do not

- Do not fabricate `sym:` ids.
- Do not infer DI usage from training data — it's project-specific.

## Out of scope

- Concrete implementors of the type (use `/implements`).
- Who **calls methods on** an injected dependency (use `/callers` on the method id, not the type).
- Reverse direction — what does a class inject? Use `neighbors(<consumer_id>, "out", ["INJECTS"])` directly.

## Going deeper

`INJECTS` `attrs` schema (mechanism, field_or_param, qualifier when present) and DI-framework coverage (Spring, Lombok, brownfield) are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/injects`.
