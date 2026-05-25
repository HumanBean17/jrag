# skills/ — RAG navigation skills for the java-codebase-rag MCP

Two self-contained skills for navigating indexed Java codebases via the 5-tool MCP (`search` / `find` / `describe` / `neighbors` / `resolve`). Skills are agent-side prompt scaffolding — they are **not** a second MCP API and **not** CLI subcommands.

## Which skill to use

| Skill | When to use | Size |
| ----- | ----------- | ---- |
| **`explore-codebase`** | Full operating manual: complete tool reference, edge taxonomy, argument shapes, recovery playbook, broad exploratory analysis | ~320 lines |
| **`navigate-codebase`** | Targeted tracing: "how does X flow", "trace the call chain for Y", "what happens when Z is called". Uses RAG-first strategy with depth discipline to prevent drowning during multi-hop walks | ~230 lines |

Both skills are standalone — an agent can load either one without the other. For a broad exploration session (onboarding onto a service, understanding a feature end-to-end), use `explore-codebase`. For a focused trace question where the agent already knows roughly what it's looking for, use `navigate-codebase`.

## Layout

```
skills/
  README.md                        ← this file
  explore-codebase/SKILL.md        ← complete MCP operating manual
  navigate-codebase/SKILL.md       ← RAG-first tracing skill
```

## What's inside each skill

### `explore-codebase`

The comprehensive operating manual. Includes:

- **Five-tool reference** — `search`, `find`, `describe`, `neighbors`, `resolve` with full argument shapes
- **Node kinds** — Symbol, Route, Client, Producer
- **Edge taxonomy** — stored edges, composed dot-keys, direction semantics
- **NodeFilter reference** — all filter keys by node kind, strict frame rules
- **Decision tree** — "user asks X → start with tool Y → follow up with Z"
- **Recovery playbook** — common failure modes and fixes
- **Navigation patterns** — 12 common intent-to-tool-chain mappings
- **Ontology glossary** — roles, capabilities, symbol kinds, frameworks, match types
- **Worked example** — end-to-end feature exploration

### `navigate-codebase`

The tactical tracing skill. Includes:

- **Core strategy** — RAG-first, graph-for-precision
- **4 navigation rules** — search before walking, always filter, depth discipline, hypothesis-driven hops
- **Anti-patterns** — the drowning patterns to avoid (open-ended loops, bare walks, ignoring edge_summary)
- **Quick reference** — essential tool args, NodeFilter keys, recovery table
- **Worked example** — side-by-side correct vs drowning approach

## Three-layer architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   "who calls X", "trace this route", "explain feature Y"     │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: explore-codebase / navigate-codebase       │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors, resolve                 │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

## Relationship to `docs/AGENT-GUIDE.md`

`explore-codebase` and `docs/AGENT-GUIDE.md` are **alternatives** covering the same ground. Pick one:

- **`explore-codebase` skill** — loaded on demand by hosts with skill discovery (Claude Code, Qwen Code, Cursor). One skill to rule them all.
- **AGENT-GUIDE block** — paste the `BEGIN`/`END` copy-paste block into your project's `AGENTS.md` / `CLAUDE.md`. Always-on. Best for hosts without skill loading.

Do not mix the two — duplicate context confuses tool selection.

## Relationship to developer skills

Developer workflow skills (propose-doc-author, cursor-task-prompt, cursor-pr-review, etc.) live in `.agents/skills/` — they are for contributors working **on** java-codebase-rag. Skills under `skills/` are for **consumers** using java-codebase-rag to explore their own codebases.
