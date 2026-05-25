# skills/ — RAG navigation skill for the java-codebase-rag MCP

A single self-contained skill (`explore-codebase`) that provides the complete operating manual for the 5-tool MCP (`search` / `find` / `describe` / `neighbors` / `resolve`). Skills are agent-side prompt scaffolding — they are **not** a second MCP API and **not** CLI subcommands.

## When to use

Load this skill when your agent needs to explore an indexed Java codebase: locate symbols, trace call chains, find HTTP/messaging routes, walk cross-service boundaries, or answer any structural question.

## Layout

```
skills/
  README.md                        ← this file
  explore-codebase/SKILL.md        ← complete MCP operating manual (standalone)
```

## What's inside `explore-codebase`

The skill is a single comprehensive prompt that includes:

- **Five-tool reference** — `search`, `find`, `describe`, `neighbors`, `resolve` with full argument shapes
- **Node kinds** — Symbol, Route, Client, Producer
- **Edge taxonomy** — stored edges, composed dot-keys, direction semantics
- **NodeFilter reference** — all filter keys by node kind, strict frame rules
- **Decision tree** — "user asks X → start with tool Y → follow up with Z"
- **Recovery playbook** — common failure modes and fixes
- **Navigation patterns** — 12 common intent-to-tool-chain mappings
- **Ontology glossary** — roles, capabilities, symbol kinds, frameworks, match types
- **Worked example** — end-to-end feature exploration

## Three-layer architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   "who calls X", "trace this route", "explain feature Y"     │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: explore-codebase SKILL.md                  │
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
