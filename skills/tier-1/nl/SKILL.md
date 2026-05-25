---
name: nl
description: Natural-language search into the java-codebase-rag graph index. Use when the user asks a fuzzy question like "find authentication code", "where is X handled", "show me code that does Y", or any concept search that doesn't start with a sym:/route:/client:/producer: id or a recognizable FQN. Argument is free-form text. Composes search → describe → optional neighbors.
---

# /nl — Natural-language search into the graph

## When to use

The user's request is **conceptual**, not identifier-shaped. Examples:

- "find authentication code"
- "where do we handle operator assignment?"
- "show me anything about chat escalation"

If the user gives a `sym:` / `route:` / `client:` / `producer:` id or a clear FQN, prefer `/callers`, `/handlers`, `/describe` (via `resolve`), etc.

## Tools used

`search`, `describe`, `neighbors` (rarely).

## Reasoning preamble (mandatory)

Before **each** MCP call, output one line:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

**Q-class taxonomy:**

- **semantic** — fuzzy NL → `search`
- **structured** — known role/kind/microservice/FQN-prefix listing → `find`
- **inspect** — get the full record + edge summary of a known id → `describe`
- **walk** — follow edges from a known id → `neighbors`

For `/nl` the first call is always `semantic` → `search`.

## Argument contract

Single positional argument: free-form text describing what to find.

## Steps

1. **Search.** Call `search(query=<arg>, limit=8)`. Each row has `symbol_id`, `microservice`, `symbol_kind`, `role`. Review for strong fit (role aligns with what the user wants, FQN looks plausible).
2. **Inspect top hit.** When the top result has a `symbol_id`, call `describe(id=<symbol_id>)` to get the full record and `edge_summary` (per-label `in`/`out` counts).
3. **Stop or walk.** If `describe` answers the question, stop. Otherwise pick the most relevant edge type from `edge_summary` and call `neighbors(ids=<id>, direction=<in|out>, edge_types=[<edge>])`. Single hop only — for deeper traces hand off to `/explain-feature` or `/trace-request-flow`.

## Recovery

- `search` returns empty: try `search(query=<arg>, table="all")`, then fall back to reading source files. If a known FQN fragment exists in `<arg>`, try `find(kind="symbol", filter={fqn_prefix:<fragment>})`.
- After two failed attempts on the same intent, stop and report the tool name, args, and response snippet.

## Worked example

User: `/nl operator assignment`
You:
```
Q-class: semantic  Pick: search  Why: NL query, no id
→ search(query="operator assignment", limit=8)
  → top hit: sym:com.bank.chat.assign.service.OperatorAssignmentService (role: SERVICE)
Q-class: inspect   Pick: describe Why: get edge_summary
→ describe(id="sym:com.bank.chat.assign.service.OperatorAssignmentService")
  → edge_summary: { CALLS: {in: 4, out: 12}, INJECTS: {in: 3, out: 1}, IMPLEMENTS: {in: 2} }
→ stop: caller has enough to ask "/implements OperatorAssignmentService" next
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files when MCP tools can provide the answer.
- Do not fabricate `symbol_id` values — they always come from `search` / `find` / `resolve`.
- Do not walk deeper than one hop in this skill — hand off to a Tier 2 skill.

## Out of scope

- Structured listing by role or kind (use `/controllers`, `/routes`, `/clients`, `/producers`).
- Identifier-shaped input where `resolve` would be more precise.
- Multi-hop traces (use `/explain-feature`, `/trace-request-flow`, `/impact-of`).

## Going deeper

The full operating manual (NodeFilter keys, edge taxonomy, recovery playbook, navigation patterns) lives in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/nl` — no need to read the guide first.
