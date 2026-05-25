# skills/ — Layer 3 navigation and workflow skills

High-level intents over the 5-tool MCP (`search` / `find` / `describe` / `neighbors` / `resolve`). Skills are agent-side prompt scaffolding — they are **not** a second MCP API and **not** CLI subcommands.

## Pick the tier you need

Skills are organized by tier — load only what you use.

```
skills/
  tier-1/   ← Navigation. 11 single-purpose skills.
  tier-2/   ← Workflow. 4 multi-step skills that compose Tier 1 with bounds.
```

- **Just want to list controllers/routes/clients?** Tier 1 is enough — `skills/tier-1/controllers`, `skills/tier-1/routes`, etc.
- **Need to trace a request, explain a feature, or analyze blast radius?** Tier 2 — `skills/tier-2/trace-request-flow`, etc.
- **Don't want skills at all?** Copy the block in `docs/AGENT-GUIDE.md` between `<!-- BEGIN java-codebase-rag MCP guide -->` and `<!-- END … -->` into your project's `AGENTS.md` / `CLAUDE.md`. Skills and the guide are **alternatives**, not complements — pick one.

## Three-layer architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: SKILL.md files in skills/tier-1/ and       │
│   skills/tier-2/.                                            │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors, resolve                 │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

## Tier 1 — Navigation (deterministic MCP chains)

11 single-purpose skills. Each one is one MCP call (sometimes preceded by a `resolve`).

| Skill | Purpose | One-shot tool chain |
| ----- | ------- | ------------------- |
| [`/nl`](tier-1/nl/SKILL.md) | Natural-language search into the graph | `search` → `describe` |
| [`/controllers`](tier-1/controllers/SKILL.md) | List controller classes | `find(kind="symbol", role="CONTROLLER")` |
| [`/routes`](tier-1/routes/SKILL.md) | List HTTP and messaging routes | `find(kind="route")` |
| [`/clients`](tier-1/clients/SKILL.md) | List outbound HTTP clients | `find(kind="client")` |
| [`/producers`](tier-1/producers/SKILL.md) | List outbound async producers | `find(kind="producer")` |
| [`/callers`](tier-1/callers/SKILL.md) | Who calls this method (in-process CALLS) | `resolve` → `neighbors(in, CALLS)` |
| [`/callees`](tier-1/callees/SKILL.md) | What this method calls (in-process CALLS) | `resolve` → `neighbors(out, CALLS)` |
| [`/handlers`](tier-1/handlers/SKILL.md) | Method that handles a route | `resolve` → `neighbors(in, EXPOSES)` |
| [`/who-hits-route`](tier-1/who-hits-route/SKILL.md) | All inbound paths to a route | `resolve` → `neighbors(in, [HTTP_CALLS, ASYNC_CALLS, EXPOSES])` |
| [`/implements`](tier-1/implements/SKILL.md) | Concrete classes implementing an interface | `resolve` → `neighbors(in, IMPLEMENTS)` |
| [`/injects`](tier-1/injects/SKILL.md) | Where a type is injected | `resolve` → `neighbors(in, INJECTS)` |

## Tier 2 — Workflow (bounded multi-step)

4 multi-step skills. Each one composes Tier 1 calls with explicit depth, recursion, and stop conditions.

| Skill | Purpose | Shape |
| ----- | ------- | ----- |
| [`/explain-feature`](tier-2/explain-feature/SKILL.md) | Understand how a feature works end-to-end | `search` → `describe` → bounded `neighbors` walks |
| [`/impact-of`](tier-2/impact-of/SKILL.md) | What breaks if a symbol changes | `resolve` → `describe` → recursive inbound `neighbors` ≤ depth 2 |
| [`/trace-request-flow`](tier-2/trace-request-flow/SKILL.md) | Follow a request from entry to persistence | `resolve(route)` → handler → forward CALLS walk ≤ depth 4 + boundary hops |
| [`/mini-map`](tier-2/mini-map/SKILL.md) | Noise-filtered call map for a hot method | `resolve` → `edge_filter`'d CALLS + skill heuristics ≤ depth 4 |

## Layout

```
skills/
  README.md              ← this file
  tier-1/
    nl/SKILL.md
    controllers/SKILL.md
    routes/SKILL.md
    clients/SKILL.md
    producers/SKILL.md
    callers/SKILL.md
    callees/SKILL.md
    handlers/SKILL.md
    who-hits-route/SKILL.md
    implements/SKILL.md
    injects/SKILL.md
  tier-2/
    explain-feature/SKILL.md
    impact-of/SKILL.md
    trace-request-flow/SKILL.md
    mini-map/SKILL.md
```

## SKILL.md structure

Every SKILL.md is self-sufficient — load one skill, get a single working scaffolded prompt:

1. **Frontmatter** (`name` + `description`) — used by Claude Code / Cursor / Qwen Code for auto-discovery.
2. **When to use** — concrete triggers and when to prefer a different skill.
3. **Tools used** — exactly which of `search` / `find` / `describe` / `neighbors` / `resolve` this skill calls.
4. **Reasoning preamble** — the mandatory `Q-class: <semantic|structured|inspect|walk>` line before each MCP call, with the taxonomy defined inline.
5. **Argument contract** — what the skill takes.
6. **Steps** — exact MCP calls with parameters.
7. **Recovery / stop conditions / recursion limit** (Tier 2: required; Tier 1: short).
8. **Worked example** — end-to-end on the `tests/bank-chat-system` fixture.
9. **Do not / Out of scope** — guardrails and pointers to neighboring skills.
10. **Going deeper** — pointer to `docs/AGENT-GUIDE.md` for the full reference.

## Versioning

Skills are versioned lockstep with the MCP. When `NodeFilter` keys, `edge_filter` axes, `edge_types`, or `kind` values change, skills are updated in the same PR. The static validator (`tests/test_agent_skills_static.py`) checks every SKILL.md against the live MCP allowlists.

## Relationship to `docs/AGENT-GUIDE.md`

Skills and `docs/AGENT-GUIDE.md` are **alternatives**. Pick one:

- **Skills** — load on demand by name. Lower context cost per query. Best for hosts that natively support skills (Claude Code, Cursor, Qwen Code).
- **AGENT-GUIDE block** — paste once into your project's `AGENTS.md` / `CLAUDE.md`. Always-on. Best for hosts without skill loading, or when you want one persistent guide for everything.

Do not mix the two — duplicate context confuses tool selection. The static validator (`TestAgentGuideConsistency`) verifies the AGENT-GUIDE copy-paste block does not reference `skills/`.

## Relationship to developer skills

Developer workflow skills (propose-doc-author, cursor-task-prompt, cursor-pr-review, etc.) live in `.agents/skills/` — they are for contributors working **on** java-codebase-rag. Skills under `skills/` are for **consumers** using java-codebase-rag to explore their own codebases.
