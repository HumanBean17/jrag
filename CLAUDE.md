# CLAUDE.md

Entry point for **Claude Code** (and other tools that read this file) working
on this repo. Cursor agents should use [`AGENTS.md`](AGENTS.md) (which also
summarizes both hosts). Detailed modular rules live under `.claude/rules/`.

## Modular rules

See @.claude/rules/project-overview.md for the project map and file roles.

See @.claude/rules/agent-workflow.md for investigate → propose → implement → validate workflow.

See @.claude/rules/breaking-changes.md for the no-back-compat policy.

See @.claude/rules/python-venv-only.md for the `.venv/bin` requirement.

## Where to look

- `README.md` — feature surface, env vars, ranking, capabilities,
  MCP tool list (`search` / `find` / `describe` / `neighbors` / `resolve`;
  response `hints` + pagination echo — see README),
  CLI ops (`java-codebase-rag --help`), and "Re-index required" callouts.
  **`ontology_version` is currently 14** (`EDGE_SCHEMA` in `java_ontology.py`).
- [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) — operator guide for the CLI.
- `CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and tuning map.
- **`propose/`** — in-flight work is **`propose/*.md`** at the root (not under `completed/`).
  **`propose/completed/`** — landed proposes. **List or search** the tree; no catalog here.
- **`plans/`** — `PLAN-*.md` and per-PR **`CURSOR-PROMPTS-*.md`** (agent execution prompts for
  Cursor and Claude Code). Top-level files are active efforts; **`plans/completed/`** holds templates.
- **`tests/README.md`** — testing philosophy, CI merge gate, iteration subset convention.
- **`.claude/skills/`** — maintainer skills (`propose`, `plan-project-scope`, `plan-prompts`,
  `pr-review`, `pr-open`, `java-codebase-explore`). Canonical copies; Cursor mirrors under `.cursor/skills/`.

Read README and docs directly. Do not rely on this file to mirror them.

## Hard rules

1. **No backward-compatibility obligation** — `.claude/rules/breaking-changes.md`.
2. **Propose-then-implement** for non-trivial features (`propose/`, `plans/`).
3. **Don't overfit to `tests/bank-chat-system/`** — assert invariants, not exact counts.
4. **`server.py` is stdio MCP** — no stdout from tool handlers; diagnostics to stderr.
5. **Ontology literals** — single source in `java_ontology.py` (`VALID_*` sets).
6. **Brownfield overrides are first-class** — compose with `BrownfieldOverrides`; see
   `plans/completed/PLAN-TIER1B-COMPLETION.md` for caller-side HTTP/ASYNC replacement rule.
7. **Schema changes require reindex** — README callout + `ontology_version` bump when semantics change.

## Kuzu Cypher pitfalls

When adding or editing Cypher run against Kuzu (`kuzu_queries.py`, `mcp_v2.py`, etc.):

- **Do not filter relationship types with** `label(e) IN $list` **in** `WHERE`. Prefer
  **OR of scalar equalities** (`label(e) = $p OR label(e) = $q …`) with bound parameters.
- **Typed union patterns** like `-[e:CALLS|HTTP_CALLS]->` are only safe if every column you
  `RETURN` from `e` exists on **all** relationship types in the schema. Otherwise use untyped
  `[e]` plus explicit label filtering, or split queries.

## Workflow

- Branch from `master`. Names: `feat/<topic>`, `plan/<name>`, `chore/<topic>`, `cursor/<topic>`.
- Commit messages: present tense, imperative, lowercase first word.
- Open a PR; never push to `master`.
- Run `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` before pushing.
- Automation-only changes under `automation/cursor_propose_only/**`: targeted pytest on that package is enough.
- Heavy tests: `JAVA_CODEBASE_RAG_RUN_HEAVY=1` (see `tests/README.md`).

## Per-PR agent task contract

When executing a section from `plans/CURSOR-PROMPTS-*.md`:

- Treat **Out of scope** as binding; sentinel `rg` patterns must be zero on `git diff master..HEAD`.
- Implement deliverables in order; match named `test_*` when listed.
- Include manual evidence and iteration pytest subset per the prompt; reviewers use the **`pr-review`** skill.

## Environment (Claude Code)

- Python 3.11+ with `.venv` at repo root; use **only** `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/ruff`.
- Install editable: `pip install -e .` so `java-codebase-rag` is on `PATH`.
- MCP: copy `mcp.json.example` to `.mcp.json` or `claude mcp add` per README §3.

### Hello-world verification

```bash
rm -rf /tmp/check && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/check/code_graph.kuzu --verbose
.venv/bin/java-codebase-rag meta \
  --source-root tests/bank-chat-system --index-dir /tmp/check
```

The MCP server is stdio-based — invoked by Claude Code / Claude Desktop, not as a long-running dev server.
