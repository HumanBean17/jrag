# skills/ — RAG navigation skill for the java-codebase-rag MCP

One self-contained skill for navigating indexed Java codebases via the 5-tool MCP (`search` / `find` / `describe` / `neighbors` / `resolve`). Skills are agent-side prompt scaffolding — they are **not** a second MCP API and **not** CLI subcommands.

## Layout

```
skills/
  README.md                        ← this file
  explore-codebase/SKILL.md        ← complete MCP operating manual
```

## `explore-codebase`

The comprehensive operating manual. Includes:

- **Five-tool reference** — `search`, `find`, `describe`, `neighbors`, `resolve` with full argument shapes
- **Node kinds** — Symbol, Route, Client, Producer
- **Edge taxonomy** — stored edges, composed dot-keys, direction semantics
- **NodeFilter reference** — all filter keys by node kind, strict frame rules
- **Decision tree** — "user asks X → start with tool Y → follow up with Z"
- **Recovery playbook** — common failure modes and fixes
- **Navigation patterns** — 12 common intent-to-tool-chain mappings
- **Ontology glossary** — roles, capabilities, symbol kinds, frameworks, match types

## Relationship to `docs/AGENT-GUIDE.md` and `agents/`

`docs/AGENT-GUIDE.md` is the **single source of truth** for the MCP operating manual. Three delivery mechanisms all carry the same content:

| Mechanism | How to use |
| --------- | ---------- |
| **`docs/AGENT-GUIDE.md`** copy-paste block | Paste the `BEGIN`/`END` block into your project's `AGENTS.md` / `CLAUDE.md`. Always-on. Best for hosts without skill or subagent loading. |
| **`explore-codebase` skill** | Loaded on demand by hosts with skill discovery (Claude Code, Qwen Code, Cursor). One skill to rule them all. |
| **`agents/explorer-rag-enhanced.md`** subagent | Copy into your project's `.claude/agents/` for Claude Code subagent discovery. The agent combines RAG graph navigation with file-system search. |

Do not mix multiple mechanisms on the same agent — duplicate context confuses tool selection.

## Relationship to developer skills

Developer workflow skills (propose-doc-author, cursor-task-prompt, cursor-pr-review, etc.) live in `.agents/skills/` — they are for contributors working **on** java-codebase-rag. Skills under `skills/` are for **consumers** using java-codebase-rag to explore their own codebases.
