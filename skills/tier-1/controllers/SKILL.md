---
name: controllers
description: List controller classes in the indexed Java codebase, optionally filtered by microservice. Use when the user asks "list controllers", "show me controllers in X", "what REST entry points exist", or "give me all @RestController classes". Returns Symbol nodes with role=CONTROLLER. Argument is an optional microservice name.
---

# /controllers — List controller classes

## When to use

The user wants a **list** of controller-stereotype classes (Spring `@RestController`, `@Controller`, JAX-RS resources, or brownfield-annotated equivalents). Optionally scoped to one microservice.

## Tools used

`find` only.

## Reasoning preamble (mandatory)

Before the MCP call, output one line:

```
Q-class: structured  Pick: find  Why: known role + optional microservice
```

**Q-class taxonomy reminder:** `semantic` (NL → `search`), **`structured`** (role/kind/microservice → `find`), `inspect` (→ `describe`), `walk` (→ `neighbors`).

## Argument contract

Optional positional argument: microservice name. Omit to list all controllers across all microservices.

**`microservice` value note:** Not validated by the MCP. An invalid name silently returns an empty list. If results are empty, verify the name with `find(kind="microservice", filter={})` first.

## Steps

1. **Find.** Call:
   - With microservice: `find(kind="symbol", filter={role:"CONTROLLER", microservice:<arg>})`
   - Without: `find(kind="symbol", filter={role:"CONTROLLER"})`
2. **Render.** For each row show `fqn`, `microservice`, and `id`. Group by microservice when no filter was given.

## Recovery

- Empty result with a microservice argument: re-run without the microservice filter; if non-empty, the microservice name is wrong. Look up the canonical names via `find(kind="microservice", filter={})`.
- Empty result without a microservice argument: likely no `CONTROLLER` role assigned. Try `find(kind="symbol", filter={symbol_kind:"class", fqn_prefix:"<package>"})` and inspect with `describe` to see `role`.
- After two failed attempts on the same intent, stop and report.

## Worked example

User: `/controllers chat-core`
You:
```
Q-class: structured  Pick: find  Why: role+microservice listing
→ find(kind="symbol", filter={role:"CONTROLLER", microservice:"chat-core"})
  → sym:com.bank.chat.core.api.ChatController          microservice=chat-core
  → sym:com.bank.chat.core.api.OperatorController      microservice=chat-core
```

User: `/controllers`
You:
```
Q-class: structured  Pick: find  Why: role listing, no scope
→ find(kind="symbol", filter={role:"CONTROLLER"})
  → grouped by microservice: chat-core (2), chat-assign (1), ...
```

## Do not

- Do not answer from training data — controllers vary by project.
- Do not read source files when `find` can answer the question.
- Do not call `search` for this — it's a structured listing, not a fuzzy query.

## Out of scope

- HTTP route enumeration (use `/routes`).
- The handler method for a specific route (use `/handlers`).
- Listing controllers + their routes together (compose `/controllers` then `/routes` per service).

## Going deeper

Full `NodeFilter` reference (all valid keys for `find`, including `role`, `symbol_kind`, `framework`, `fqn_prefix`) and the role taxonomy live in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/controllers`.
