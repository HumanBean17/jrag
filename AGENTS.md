# AGENTS.md

Entry point for Cursor CLI agents (and other agentic tools) working
on this repo. Detailed guidance lives in `.cursor/rules/*.mdc` —
those files are auto-loaded by Cursor. This file is a flat summary
for tools that don't read `.cursor/rules/`.

## Where to look

- `README.md` — feature surface, env vars, ranking, capabilities,
 MCP tool list (now `search` / `find` / `describe` / `neighbors`),
 CLI ops (`user-rag --help`), and "Re-index required" callouts.
 **`ontology_version` is currently 11** (async brownfield route merge + `Client` nodes; see README callouts).
- [`docs/USER-RAG-CLI.md`](./docs/USER-RAG-CLI.md) — operator guide for the `user-rag` CLI (refresh, meta, tables, diagnose-ignore, analyze-pr).
- `CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and tuning map.
- `propose/` and `plans/` (plus their `completed/` subdirs) —
  in-flight scope and the rationale behind current design.
  - Active proposes: `TIER2-INCREMENTAL-REBUILD-PROPOSE.md` (Kuzu
    diff-driven rebuild), `RANKING-MICROSERVICE-PROPOSE.md`,
    `ENHANCED-ROLE-RECOGNITION-PROPOSE.md`,
    `REFRESH-CODE-INDEX-AUTO-MODE-PROPOSE.md` (paired with TIER2 —
    decision engine for incremental vs full),
    `DEFERRED-REST-CLIENT-MIGRATION-PROPOSE.md`, `PRODUCT-VISION.md`.
  - Active plans: `PLAN-POST-TIER1B-FOLLOWUPS.md` (PR-E1/PR-E2 —
    deferred catches collected from PR-D1/D2/D3 reviews).
  - Completed (Tier 1 + Tier 1B): `propose/completed/TIER1-COMPLETION-PROPOSE.md`,
    `propose/completed/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`,
    `plans/completed/PLAN-TIER1-COMPLETION.md`,
    `plans/completed/PLAN-TIER1B-COMPLETION.md`,
    `plans/completed/CURSOR-PROMPTS-TIER1.md`,
    `plans/completed/CURSOR-PROMPTS-TIER1B.md`. The two CURSOR-PROMPTS
    files are kept as reference templates for future per-PR Cursor work.
  - Older completed: `propose/completed/CALL-GRAPH-PROPOSE.md`,
    `propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md` (four-tool MCP + `user-rag` CLI),
    `plans/completed/PLAN-CALL-GRAPH.md`,
    `plans/completed/PLAN-CAPABILITIES-MODEL.md`,
    `plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES.md`,
    `plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`,
    `plans/completed/PLAN-COCOINDEX-SYMLINK-FIX.md`,
    `plans/completed/PLAN-ENUM-ANNOTATION-FIXES.md`,
    `plans/completed/PLAN-REMOTE-PROJECT-INDEXING.md`,
    `propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`,
    `plans/completed/PLAN-LIST-CLIENTS-MCP-TOOL.md`,
    `plans/completed/CURSOR-PROMPTS-LIST-CLIENTS-MCP-TOOL.md`. Read these
    when you need the *why* behind current code.
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
5. **Single source of truth** for roles, capabilities, client kinds,
   call strategies, and call match outcomes is `java_ontology.py`.
   No string literals sprinkled elsewhere. Current valid sets:
   `VALID_ROLES`, `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`,
   `VALID_HTTP_CALL_STRATEGIES`, `VALID_ASYNC_CALL_STRATEGIES`,
   `VALID_HTTP_CALL_MATCHES`.
6. **Brownfield overrides are first-class.** Annotation-driven
   `BrownfieldOverrides` (route, role, capability, http client, async
   producer) plus their `@CodebaseRoute` / `@CodebaseClient` /
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

## Workflow

- Branch from `master`. Branch names: `cursor/<topic>` (CLI work),
  `plan/<name>` (in-progress propose), `feat/<topic>` and
  `chore/<topic>` for landed-feature work.
- Commit messages: present tense, imperative, lowercase first word.
- Always open a PR; never push to `master`.
- Run `ruff check .` and `pytest tests -v` before pushing.

## Per-PR Cursor task contract

When picking up a per-PR Cursor task prompt (e.g. one of the entries
under `plans/CURSOR-PROMPTS-<topic>.md`; see
`plans/completed/CURSOR-PROMPTS-TIER1B.md` as the canonical reference
template):

- Treat the prompt's **Out of scope** list as binding. Sentinel grep
  patterns in the prompt must return zero on `git diff master..HEAD`.
- Implement deliverables in the listed order; don't reshape the PR.
- Match the prompt's expected test count and named tests verbatim.
- PR description must include: scope statement, manual evidence
  (with the exact command from the prompt), test count, and
  intentional design divergences flagged.
- No drive-by lint fixes (unused imports, formatting nits in
  unrelated files). They violate the per-PR scope contract even when
  they look harmless.
