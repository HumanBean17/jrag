# AGENTS.md

Entry point for **agentic tools** working on this repo (Cursor CLI, Claude Code, and others).

| Host | Primary entry | Rules | Skills |
|------|---------------|-------|--------|
| **Claude Code** | [`CLAUDE.md`](CLAUDE.md) | [`.claude/rules/`](.claude/rules/) | [`.claude/skills/`](.claude/skills/) (canonical) |
| **Cursor** | This file | [`.cursor/rules/*.mdc`](.cursor/rules/) | [`.cursor/skills/`](.cursor/skills/) (mirror; copy to `~/.cursor/skills/` if needed) |

Detailed guidance for Cursor also lives in `.cursor/rules/*.mdc` (auto-loaded by Cursor). Claude Code loads `CLAUDE.md`, `.claude/rules/`, and `.claude/skills/` automatically. **Canonical skill prose** is under `.claude/skills/`; keep `.cursor/skills/` in sync when editing workflow skills.

## Where to look

- `README.md` — feature surface, env vars, ranking, capabilities,
  MCP tool list (`search` / `find` / `describe` / `neighbors` / `resolve`;
  response `hints` + pagination echo — see README),
  CLI ops (`java-codebase-rag --help`), and "Re-index required" callouts.
  **`ontology_version` is currently 14** (`EDGE_SCHEMA` in `java_ontology.py`; v14 re-index required; HTTP/ASYNC caller-side endpoint flips ship in SCHEMA-V2 PR-B/C — see README graph section and `docs/EDGE-NAVIGATION.md`).
- [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) — operator guide for the `java-codebase-rag` CLI (`init` / `increment` / `reprocess` / `erase`, `meta`, `tables`, `diagnose-ignore`, `analyze-pr`; hidden `refresh` alias → `reprocess` — see that doc).
- `CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and tuning map.
- **`propose/`** — design proposes. **In-flight** work is **`propose/*.md`**
  (markdown at the root of `propose/` only, not under `completed/`).
  **`propose/completed/`** — landed proposes and rationale. **List or search**
  the tree for current filenames; entrypoint docs are not maintained as a
  catalog.
- **`plans/`** — multi-PR plans (`PLAN-*.md`) and per-PR agent execution prompts
  (`CURSOR-PROMPTS-*.md` — used by Cursor and Claude Code). Top-level files here
  are active or staged multi-PR efforts; **`plans/completed/`** holds finished plans
  and completed prompt sets (reference templates for future work).
- `tests/README.md` — testing philosophy.

Read these directly. Don't rely on rule files to mirror them.

## Hard rules

1. **No backward-compatibility obligation** —
   `.claude/rules/breaking-changes.md` (Cursor: `.cursor/rules/breaking-changes.mdc`).
   Prefer removals and schema updates over shims.
2. **Propose-then-implement** for non-trivial features. Drop a short
   markdown propose under `propose/`, reference it from the PR, move
   it to `propose/completed/` once landed.
3. **Don't overfit to the `tests/bank-chat-system/` fixture.** It is
   a deterministic corpus, not a model of production. Assert on
   invariants, not exact counts. Don't special-case the fixture in
   production code.
4. **`server.py` is stdio MCP.** Nothing reachable from a tool
   handler may write to stdout. Diagnostics go to stderr.
5. **Single source of truth** for roles, capabilities, client kinds,
   call strategies, and call match outcomes is `java_ontology.py`.
   No string literals sprinkled elsewhere. Current valid sets:
   `VALID_ROLES`, `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`,
   `VALID_HTTP_CALL_STRATEGIES`, `VALID_ASYNC_CALL_STRATEGIES`,
   `VALID_HTTP_CALL_MATCHES`.
6. **Brownfield overrides are first-class.** Annotation-driven
   `BrownfieldOverrides` (route, role, capability, http client, async
   producer) plus their `@CodebaseRoute` / `@CodebaseHttpClient` /
   `@CodebaseProducer` source-stub equivalents must keep working — they
   are the only path to making this tool usable on legacy codebases.
   New auto-detection logic must compose with brownfield (last layer
   wins), never replace it. See
   `plans/completed/PLAN-TIER1B-COMPLETION.md` § "Caller-side composition
   divergence" for the one intentional exception (caller-side option-b
   replacement rule for HTTP_CALLS / ASYNC_CALLS).
7. **Schema changes require a reindex** — update the README
   "Re-index required" callout and bump `ontology_version` when
   enrichment semantics change.

## Kuzu Cypher pitfalls

When adding or editing Cypher run against Kuzu (for example in
`kuzu_queries.py`, `mcp_v2.py`, or any `KuzuGraph._rows` caller):

- **Do not filter relationship types with** `label(e) IN $list` **or**
  `label(e) IN ["A","B"]` **in** `WHERE`. On supported versions this can
  be ignored or wrong; prefer **OR of scalar equalities**
  (`label(e) = $p OR label(e) = $q …`) with bound parameters, after
  validating labels against an allowlist (see `neighbors_v2` in
  `mcp_v2.py`).
- **Typed union patterns** like `-[e:CALLS|HTTP_CALLS]->` are only safe if
  every column you `RETURN` from `e` exists on **all** of those
  relationship types in the graph schema. Otherwise prefer untyped `[e]`
  plus explicit label filtering, or split queries.

## Workflow

- Branch from `master`. Branch names: `cursor/<topic>` (Cursor-agent work),
  `plan/<name>` (in-progress propose), `feat/<topic>` and
  `chore/<topic>` for landed-feature work.
- Commit messages: present tense, imperative, lowercase first word.
- Always open a PR; never push to `master`.
- Run `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` before pushing.
- Exception for isolated automation-only changes: if edits are limited to
  `automation/cursor_propose_only/**` (plus optional docs references to that
  workflow), full `tests -v` is not required. Run:
  - `.venv/bin/ruff check .`
  - `.venv/bin/python -m pytest automation/cursor_propose_only/tests -q`
- Heavy indexer tests: `JAVA_CODEBASE_RAG_RUN_HEAVY=1` (see `tests/README.md`).

## Per-PR agent task contract

When picking up a per-PR task prompt (from `plans/` or
`plans/completed/`, files matching `CURSOR-PROMPTS-*.md`; use any
completed prompt file in `plans/completed/` as a structural template
when you need one):

- Treat the prompt's **Out of scope** list as binding. Sentinel grep
  patterns in the prompt must return zero on `git diff master..HEAD`.
- Implement deliverables in the listed order; don't reshape the PR.
- Match named tests verbatim when the prompt lists `test_*` names; if
  the test set changes, update the prompt/plan text in the same change.
- PR description must include: scope statement, manual evidence (with
  the exact command from the prompt), and intentional design
  divergences flagged.
- No drive-by lint fixes (unused imports, formatting nits in
  unrelated files). They violate the per-PR scope contract even when
  they look harmless.

## Environment

This is a self-contained Python project — no external services
(no Postgres, Kafka, Docker) are needed. All storage (Kuzu, LanceDB,
CocoIndex state) is embedded/file-based.

### Setup

- Python 3.11+ with `.venv` at repo root.
- Use only `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/ruff`.
- Install editable: `pip install -e .` so `java-codebase-rag` is on `PATH`.

### Running checks

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Heavy (CocoIndex + LanceDB e2e) tests are gated behind
`JAVA_CODEBASE_RAG_RUN_HEAVY=1` and download the embedding model on
first run. They are not required for normal development.

### Hello-world verification

Build the Kuzu graph from the test fixture and inspect it:

```bash
rm -rf /tmp/check && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/check/code_graph.kuzu --verbose
.venv/bin/java-codebase-rag meta \
  --source-root tests/bank-chat-system --index-dir /tmp/check
```

The MCP server (`server.py`) is stdio-based and is not started as a
long-running dev server — it is invoked by MCP hosts (Claude Desktop,
Claude Code, Cursor) directly.
