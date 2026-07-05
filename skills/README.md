# skills/ — RAG navigation skills for java-codebase-rag

Two self-contained skills for navigating indexed Java codebases — one per
**surface** (MCP server vs `jrag` CLI). Skills are agent-side prompt scaffolding
— they are **not** a second MCP API and **not** CLI subcommands.

## Surfaces (PR-JRAG-5)

`java-codebase-rag install` picks one of two surfaces:

- **`--surface mcp`** (default) — registers the stdio MCP server (5 tools:
  `search` / `find` / `describe` / `neighbors` / `resolve`) and deploys the
  **`explore-codebase`** skill + **`explorer-rag-enhanced`** subagent.
- **`--surface cli`** — deploys the **`explore-codebase-cli`** skill +
  **`explorer-rag-cli`** subagent, documenting the `jrag` console-script shell
  vocabulary (one command per engineering intent; no MCP entry registered).

Pick one surface per project — running both strands the agent in two
vocabularies.

## Layout

```
skills/
  README.md                          ← this file
  explore-codebase/SKILL.md          ← complete MCP operating manual (mcp surface)
  explore-codebase-cli/SKILL.md      ← `jrag` CLI operating manual (cli surface; PR-JRAG-5)
```

## `explore-codebase` (MCP surface)

The comprehensive MCP operating manual. Includes:

- **Five-tool reference** — `search`, `find`, `describe`, `neighbors`, `resolve` with full argument shapes
- **Node kinds** — Symbol, Route, Client, Producer
- **Edge taxonomy** — stored edges, composed dot-keys, direction semantics
- **NodeFilter reference** — all filter keys by node kind, strict frame rules
- **Decision tree** — "user asks X → start with tool Y → follow up with Z"
- **Recovery playbook** — common failure modes and fixes
- **Navigation patterns** — 12 common intent-to-tool-chain mappings
- **Ontology glossary** — roles, capabilities, symbol kinds, frameworks, match types

## `explore-codebase-cli` (CLI surface; PR-JRAG-5)

The operating manual for the `jrag` CLI — same graph underneath, but the
agent drives shell commands (`jrag callers`, `jrag inspect`, `jrag search`,
…). Internalizes resolve so every `<query>` command is "names in, names out".

Includes: command groups (orientation / locate / listings / traversal /
inspection), common flags, resolve-first contract, traversal reference,
ontology glossary, recovery playbook, workflow patterns.

## Relationship to `docs/AGENT-GUIDE.md` and `agents/`

`docs/AGENT-GUIDE.md` is the **single source of truth** for the MCP operating manual. Three delivery mechanisms all carry the same MCP content:

| Mechanism | How to use |
| --------- | ---------- |
| **`docs/AGENT-GUIDE.md`** copy-paste block | Paste the `BEGIN`/`END` block into your project's `AGENTS.md` / `CLAUDE.md`. Always-on. Best for hosts without skill or subagent loading. |
| **`explore-codebase` skill** | Loaded on demand by hosts with skill discovery (Claude Code, Qwen Code, Cursor). One skill to rule them all. (MCP surface.) |
| **`agents/explorer-rag-enhanced.md`** subagent | Copy into your project's `.claude/agents/` for Claude Code subagent discovery. The agent combines RAG graph navigation with file-system search. (MCP surface.) |

For the CLI surface, the parallel pair is **`explore-codebase-cli`** (skill) +
**`agents/explorer-rag-cli.md`** (subagent) — driven via the `jrag` shell CLI
rather than the MCP tools.

Do not mix multiple mechanisms on the same agent — duplicate context confuses tool selection.

## Relationship to developer skills

Developer workflow skills (propose-doc-author, cursor-task-prompt, cursor-pr-review, etc.) live in `.agents/skills/` — they are for contributors working **on** java-codebase-rag. Skills under `skills/` are for **consumers** using java-codebase-rag to explore their own codebases.
