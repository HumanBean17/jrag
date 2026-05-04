# AGENTS.md

Entry point for Cursor CLI agents (and other agentic tools) working
on this repo. Detailed guidance lives in `.cursor/rules/*.mdc` —
those files are auto-loaded by Cursor. This file is a flat summary
for tools that don't read `.cursor/rules/`.

## Where to look

- `README.md` — feature surface, env vars, ranking, capabilities,
  tool list, "Re-index required" callouts.
- `CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and tuning map.
- `propose/` and `plans/` (plus their `completed/` subdirs) —
  in-flight scope and the rationale behind current design.
- `tests/README.md` — testing philosophy.

Read these directly. Don't rely on rule files to mirror them.

## Hard rules

1. **No backward-compatibility obligation** —
   `.cursor/rules/breaking-changes.mdc`. Prefer removals and schema
   updates over shims.
2. **Propose-then-implement** for non-trivial features. Drop a short
   markdown propose under `propose/`, reference it from the PR, move
   it to `propose/completed/` once landed.
3. **Don't overfit to the `tests/bank-chat-system/` fixture.** It is
   a deterministic corpus, not a model of production. Assert on
   invariants, not exact counts. Don't special-case the fixture in
   production code.
4. **`server.py` is stdio MCP.** Nothing reachable from a tool
   handler may write to stdout. Diagnostics go to stderr.
5. **Single source of truth** for roles and capabilities is
   `java_ontology.py`. No string literals sprinkled elsewhere.
6. **Schema changes require a reindex** — update the README
   "Re-index required" callout and bump `ontology_version` when
   enrichment semantics change.

## Workflow

- Branch from `master`. Branch names: `cursor/<topic>` (CLI work),
  `plan/<name>` (in-progress propose).
- Commit messages: present tense, imperative, lowercase first word.
- Always open a PR; never push to `master`.
- Run `ruff check .` and `pytest tests -v` before pushing.
