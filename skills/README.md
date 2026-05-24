# skills/ — Layer 3 navigation and workflow skills

High-level intents over the 5-tool MCP (`search` / `find` / `describe` / `neighbors` / `resolve`). Skills are agent-side prompt scaffolding — they are NOT a second MCP API and NOT CLI subcommands.

## Three-layer architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: SKILL.md in skills/ at project root.       │
│   Tier 1 = deterministic chains; Tier 2 = bounded workflows │
│   + /mini-map heuristics.                                    │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors, resolve                 │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

## Skill index

### Tier 1 — Navigation (deterministic MCP chains)

| Skill | Purpose |
| ----- | ------- |
| [`/nl`](nl/SKILL.md) | Natural-language search into the graph |
| [`/controllers`](controllers/SKILL.md) | List controller classes |
| [`/routes`](routes/SKILL.md) | List HTTP and messaging routes |
| [`/clients`](clients/SKILL.md) | List outbound HTTP clients |
| [`/producers`](producers/SKILL.md) | List outbound async producers |
| [`/callers`](callers/SKILL.md) | Who calls this method (in-process CALLS) |
| [`/callees`](callees/SKILL.md) | What this method calls (in-process CALLS) |
| [`/handlers`](handlers/SKILL.md) | Method that handles a route |
| [`/who-hits-route`](who-hits-route/SKILL.md) | All inbound paths to a route |
| [`/implements`](implements/SKILL.md) | Concrete classes implementing an interface |
| [`/injects`](injects/SKILL.md) | Where a type is injected |

### Tier 2 — Workflow (bounded multi-step)

| Skill | Purpose |
| ----- | ------- |
| [`/explain-feature`](explain-feature/SKILL.md) | Understand how a feature works end-to-end |
| [`/impact-of`](impact-of/SKILL.md) | What breaks if a symbol changes |
| [`/trace-request-flow`](trace-request-flow/SKILL.md) | Follow a request from entry to persistence |
| [`/mini-map`](mini-map/SKILL.md) | Noise-filtered call map for a method |

## Layout

```
skills/
  <skill-name>/
    SKILL.md          ← frontmatter (name + description) + markdown body
  README.md           ← this file
```

## Relationship to developer skills

Developer workflow skills (propose, pr-review, etc.) live in `.agents/skills/` — they are for contributors working **on** java-codebase-rag. Skills in this directory are for **consumers** using java-codebase-rag to explore their own codebases.

## Versioning

Skills are versioned lockstep with the MCP. When `NodeFilter` keys, `edge_filter` axes, `edge_types`, or `kind` values change, skills are updated in the same PR.
