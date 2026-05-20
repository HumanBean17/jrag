# CLAUDE-CODE-PARITY — first-class Claude Code maintainer agent config

**Status**: implemented (this propose documents the landed work).

**Author**: Agent (feat/claude-code-migration)

**Date**: 2026-05-20

---

## Problem Statement

The java-codebase-rag **MCP product** is host-agnostic (README documents Claude Code MCP setup), but **maintainer ergonomics** are Cursor-only:

- Always-on rules live under `.cursor/rules/*.mdc` (Cursor frontmatter).
- Workflow skills live under `.cursor/skills/` (propose, plan, review, PR open).
- `AGENTS.md` points exclusively at Cursor.
- Per-PR handoffs (`plans/CURSOR-PROMPTS-*.md`) work in Claude prompts but nothing tells Claude Code to load repo conventions.
- Optional automation (`automation/cursor_propose_only/`) documents `cursor-agent` only.

Contributors using Claude Code get README + raw plans without the same guardrails Cursor agents receive.

## Proposed Solution

Add a **dual-host** layout:

| Layer | Cursor | Claude Code (canonical for shared prose) |
|-------|--------|------------------------------------------|
| Entry | `AGENTS.md` | `CLAUDE.md` + `AGENTS.md` (dual-host) |
| Rules | `.cursor/rules/*.mdc` | `.claude/rules/*.md` |
| Skills | `.cursor/skills/*` (optional copy to `~/.cursor/skills/`) | `.claude/skills/*` |
| Settings | (IDE defaults) | `.claude/settings.json` |
| Per-PR prompts | `plans/CURSOR-PROMPTS-*.md` | Same files (host-neutral bodies; `@` attachments work) |
| Subagents | — | `.claude/agents/implementer.md`, `reviewer.md` |
| Exploration | `docs/skills/java-codebase-explore.md` | `.claude/skills/java-codebase-explore/SKILL.md` (`@` import) |

**Naming:** Keep `CURSOR-PROMPTS-*` filenames in v1; skills/docs say "agent execution prompts" for both hosts.

**No sync script** in v1 — accept short-term duplication between `.cursor/` and `.claude/` with cross-links in `AGENTS.md` / `CLAUDE.md`.

## Scope

- `CLAUDE.md` at repo root
- `.claude/rules/` (four files ported from `.cursor/rules/`)
- `.claude/settings.json` (venv, ruff, pytest, gh allowlist)
- `.claude/skills/` (five maintainer skills + java-codebase-explore wrapper)
- `.claude/agents/` (implementer, reviewer)
- `AGENTS.md`, `README.md`, `tests/README.md` dual-host wording
- `.github/workflows/test.yml` — `.claude/**` in docs-only path filter
- `automation/cursor_propose_only/README.md` — example `claude -p` invocations

## Schema / Ontology / Re-index impact

- Ontology bump: **not required**
- Re-index required: **no**
- MCP / tool surface: **unchanged**

## Tests / Validation

- Docs-only PRs: CI `test` job green (pytest skipped).
- No production `*.py` changes except copying `create_pr.py` under `.claude/skills/pr-open/`.
- Manual smoke: open repo in Claude Code; confirm skills; run one `plans/CURSOR-PROMPTS-*` PR block.

## Out of scope

- Deleting or replacing `.cursor/`
- Renaming `CURSOR-PROMPTS-*` → `AGENT-PROMPTS-*`
- Perplexity `.zip` repackaging of `java-codebase-explore`
- Renaming `automation/cursor_propose_only/` package
- MCP server or indexer behavior changes

## Sequencing

| PR | Deliverables |
|----|----------------|
| PR-1 | `CLAUDE.md`, `.claude/rules/`, `.claude/settings.json`, `AGENTS.md`, README |
| PR-2 | `.claude/skills/*` (5 skills), `tests/README.md`, CI path filter |
| PR-3 | `java-codebase-explore` skill, `.claude/agents/*` |
| PR-4 | Automation README `claude -p` examples |

Land as one branch (`feat/claude-code-migration`) or split per table; move this file to `propose/completed/` when merged.
