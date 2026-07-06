# skills/ — RAG navigation skills for java-codebase-rag

Two self-contained skills for navigating an indexed Java codebase — one per
**surface**. Skills are agent-side prompt scaffolding, **not** a second MCP API
and **not** CLI subcommands.

## Surfaces

`java-codebase-rag install` picks one surface; **one per project** (running both
strands the agent in two vocabularies):

- **`--surface mcp`** (default) — registers the stdio MCP server (`search` /
  `find` / `describe` / `neighbors` / `resolve`) and deploys the
  **`explore-codebase`** skill + **`explorer-rag-enhanced`** agent.
- **`--surface cli`** — deploys the **`explore-codebase-cli`** skill +
  **`explorer-rag-cli`** agent, driving the `jrag` console-script (one command
  per intent; no MCP entry).

## Layout

```
skills/
  README.md                       ← this file (dev-only; not shipped)
  explore-codebase/SKILL.md       ← MCP operating manual
  explore-codebase-cli/SKILL.md   ← `jrag` CLI operating manual
```

## Relationship to `docs/` and `agents/`

`docs/AGENT-GUIDE.md` is the **source of truth** for the MCP manual. Pick **one**
delivery mechanism per agent (mixing confuses tool selection): the copy-paste
block (into `AGENTS.md`/`CLAUDE.md`), the `explore-codebase` skill, or the
`explorer-rag-enhanced` subagent (`.claude/agents/`). The CLI surface parallels
this: `explore-codebase-cli` skill + `explorer-rag-cli` agent, via `jrag`.

Developer workflow skills live in `.agents/skills/` (contributors working **on**
java-codebase-rag); `skills/` here is for **consumers**.
