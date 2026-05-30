# AGENTS.md

Canonical agent instructions for Cursor, Claude Code, and other
agentic tools working on this repo. Cursor reads this file at the project
root (and nested `AGENTS.md` in subdirectories when working there).

Project skills and tooling live under **`.agents/`** (tracked in git).
Create local symlinks if your editor expects the legacy paths:
`ln -s .agents .cursor` and `ln -s .agents .claude` (both are
gitignored).

### Two audiences, two skill trees

| Directory | Audience | Purpose |
|-----------|----------|---------|
| **`.agents/skills/`** (`.claude/skills/`, `.cursor/skills/`) | Agents **developing** this repo | propose, plan-prompts, pr-open, pr-review |
| **`skills/explore-codebase/`** (project root) | Agents **using** this tool on their own codebase | /explore-codebase — complete MCP operating manual |

`.agents/` skills are loaded by the agent working *on* java-codebase-rag source
code. `skills/` is shipped to consumers — it instructs an agent to call the
MCP tools (`search`, `find`, `describe`, `neighbors`, `resolve`, `trace`) against an
indexed Java codebase. Do not mix the two: never import consumer skills into
`.agents/skills/` or vice versa.

This repo is a **self-contained stdio MCP server** that serves semantic
+ structural search over a Java codebase. It is a Python project (the
indexer and server). It is **not** a Java project — the
`tests/bank-chat-system/` tree is fixture data, not code to modify.

Treat README and the markdown docs as the source of truth for
behaviour, schemas, env vars, ranking, edges, tool defaults, and
ontology. **Do not copy that content here** — read those files directly
when needed.

## Where to look

- `README.md` — pip-first landing page: install, 5-minute walkthrough on the
  bank-chat fixture, MCP host wiring (Claude Code / Claude Desktop), the
  six-tool cheat sheet (`search` / `find` / `describe` / `neighbors` / `resolve` / `trace`),
  and the CLI cheat sheet. Pointers out to other docs for depth.
- [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) — environment
  variables, full `.java-codebase-rag.yml` reference, **graph layer**
  (node kinds, edges, capabilities, ranking, "Re-index required"
  callouts), brownfield overrides, ignore patterns. The current
  `ontology_version` is **15** (`EDGE_SCHEMA` in `java_ontology.py`;
  material `OVERRIDES` Symbol→Symbol edges: subtype instance method →
  supertype declaration with matching `signature`, one
  `IMPLEMENTS`/`EXTENDS` hop; valid `neighbors` `EdgeType`).
  Earlier ontology bumps are described inline in that doc's §3
  callouts list.
- [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) —
  operator guide for the `java-codebase-rag` CLI (`init` / `increment` /
  `reprocess` / `erase`, `meta`, `tables`, `diagnose-ignore`,
  `analyze-pr`; hidden `refresh` alias → `reprocess` — see that doc).
- `docs/CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and per-file map of
  what to edit when a target tree doesn't match defaults.
- `tests/README.md` — testing philosophy.
- **`skills/explore-codebase/`** — user-facing skill shipped to java-codebase-rag consumers. Single self-contained operating manual for the 6-tool MCP. Developer workflow skills live in **`.agents/skills/`**, not here.
- **`propose/`** — design proposes. **In-flight** proposes live in
  **`propose/active/`**. **`propose/completed/`** — landed work and rationale.
  **List or search this tree** for current filenames; do not rely on enumerated
  copies here.
- **`plans/`** — longer-form multi-PR plans (`PLAN-*.md`) and
  **`AGENT-PROMPTS-*.md`** for per-PR agent handoffs. Active plans live in
  **`plans/active/`**. **`plans/completed/`** — finished plans and completed
  prompt sets (templates). **Open the directory**; don't cache a mental file
  list from here.

## File map (top of repo)

| File | Role |
|------|------|
| `server.py` | MCP stdio server. Every `@mcp.tool` lives here. |
| `search_lancedb.py` | Vector / hybrid / graph-expanded search; ranking. |
| `build_ast_graph.py` | Tree-sitter → Kuzu graph builder (full rebuild). Owns `pass1`–`pass6` (`pass5` emits `HTTP_CALLS` / `ASYNC_CALLS` caller edges; `pass6_match_edges` resolves cross-service / intra-service / ambiguous / phantom / unresolved match outcomes — ontology 7). |
| `kuzu_queries.py` | Read-only Cypher helpers used by the server. Includes `meta()` decoder for the Kuzu MAP-as-STRING JSON-blob columns. |
| `ast_java.py` | Tree-sitter Java parsing, role/capability inference, `_string_value_atoms` helper (shared by route/client/producer extractors), `_collect_outgoing_calls` for caller-side detection. |
| `graph_enrich.py` | `module` / `microservice` resolution, `BrownfieldOverrides` (route + role + capability + http client + async producer), meta-annotation walk, `resolve_routes_for_method` / `resolve_http_client_for_method` / `resolve_async_producer_for_method`. |
| `java_ontology.py` | Source of truth for `VALID_ROLES`, `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`, `VALID_HTTP_CALL_STRATEGIES`, `VALID_ASYNC_CALL_STRATEGIES`, `VALID_HTTP_CALL_MATCHES`. |
| `chunk_heuristics.py` | Query-time chunk hints (no AST / no re-index). |
| `mcp_hints.py` | MCP v2 road-sign `hints` catalog (`generate_hints`; locked v1 templates in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`). |
| `mcp_trace.py` | Multi-hop BFS traversal engine (`trace` MCP tool). |
| `index_common.py` | Embedding config (no CocoIndex dep). |
| `java_index_flow_lancedb.py` | CocoIndex flow (used by `java-codebase-rag init` / `increment` / `reprocess` / `erase`). |
| `java_index_v1_common.py` | Shared file walker / exclude patterns. |
| `path_filtering.py` | Layered ignore patterns (`.gitignore`-style; PR-C / B5). Reused by indexer + graph build. |
| `pr_analysis.py` | `java-codebase-rag analyze-pr` helpers (PR-B / B4) — diff parsing, hunk-to-symbol mapping. |
| `mcp.json.example` | Template for `.mcp.json`. |

## Test layout

- `tests/conftest.py` — session-scoped Kuzu graph fixture.
- `tests/bank-chat-system/` — deterministic Java corpus (fixture, not production model).
- `tests/fixtures/call_graph_smoke/` — mini Maven tree calibrated against the call-graph resolver.
- `tests/fixtures/brownfield_route_stubs/` — `@CodebaseRoute` / `@CodebaseRoutes` source stubs (PR-A3).
- `tests/fixtures/brownfield_client_stubs/` — `@CodebaseHttpClient` / `@CodebaseHttpClients` / `@CodebaseProducer` / `@CodebaseProducers` source stubs (PR-D2).
- `tests/fixtures/http_caller_smoke/` — Feign + RestTemplate + KafkaTemplate + WebClient + StreamBridge fixture for caller-side detection (PR-D1).
- Heavy e2e tests gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

## Breaking changes and compatibility

- **Breaking changes are always allowed.** Do not keep compatibility with
  prior versions, external consumers, or hypothetical “users” of this
  repo unless the current task explicitly asks for a compatibility layer.
- Prefer straightforward removals and schema or API updates over
  deprecation periods, dual code paths, shims, or version branching unless
  there is a clear, stated need in the task at hand.

## Python environment

- Use only the repository `.venv/bin/python` for Python commands (repo root).
- Use only `.venv/bin/pip` for package install and dependency commands.
- Do not use system `python`, `python3`, `pip`, or `pip3` for this repo
  unless you have explicitly activated `.venv` and that is what those
  resolve to.
- When running tests, linters, or scripts, invoke the `.venv/bin`
  executables directly.
- Examples:
  - `.venv/bin/python -m pytest tests -q`
  - `.venv/bin/ruff check .`
  - `.venv/bin/pip install -r requirements.txt`

## Investigate before editing

For any non-trivial change, read the relevant doc first instead of
inferring from code:

- Behaviour / public surface → `README.md`.
- Brownfield assumptions, role/capability tuning → `docs/CODEBASE_REQUIREMENTS.md`.
- In-flight design proposes → **`propose/active/`**.
  **List or search** for current names.
- Why current design exists → `propose/completed/` and `plans/completed/`.
- Testing philosophy → `tests/README.md`.
- In-flight multi-PR scope → **`plans/active/`**.
  **List or search** for active `PLAN-*.md` / `AGENT-PROMPTS-*.md`.
  Finished plans and prompt templates → `plans/completed/`.

## Propose-then-implement culture

The repo has a strong "propose then implement" culture (`propose/`,
`plans/`). For non-trivial features:

1. Drop a short markdown propose under `propose/active/` describing scope,
   schema impact, reindex requirement, and tests touched.
2. For multi-PR efforts, add a matching `plans/active/PLAN-<topic>.md` with
   per-PR sections, then `plans/active/AGENT-PROMPTS-<topic>.md` with the
   per-PR agent task prompts.
3. Reference the propose / plan from the PR description.
4. Move propose into `propose/completed/` (or plan into
   `plans/completed/`) once the *whole* effort is landed — not after
   each PR.

Skip this for clearly-bounded fixes (one-file bugs, doc edits, test
loosening). Use judgement.

## Per-PR agent task contract

When you're given a per-PR task prompt from `plans/AGENT-PROMPTS-*.md`
(or a completed prompt file in `plans/completed/` as a structural
template):

- **Scope is binding.** The "Out of scope (do NOT touch)" list is a
  hard constraint, not a guideline. Sentinel grep patterns the prompt
  lists must return zero on `git diff master..HEAD`.
- **Implement in the listed order.** Do not reshape the PR or roll
  multiple PRs together.
- **Match named tests verbatim.** When the plan lists
  `test_<scenario>_<expected>`, that is the exact name to use. If you
  add, drop, or rename tests, update the plan/prompt text in the same
  change so reviewers are not chasing a stale list.
- **No drive-by lint fixes.** Removing an unused `import` in a file
  the PR doesn't otherwise touch is still a scope leak. If a file
  isn't in the deliverables list, don't touch it.
- **PR description must include**: scope statement, manual evidence
  (with the exact command from the prompt), and any intentional design
  divergences from sibling PRs called out explicitly so the reviewer
  doesn't flag them as bugs.

## Editing rules

- No compatibility shims or deprecation cycles (see **Breaking changes**
  above).
- One source of truth for ontology values lives in `java_ontology.py`.
  Don't sprinkle role / capability / client-kind / strategy / match
  string literals across other modules. Current valid sets: `VALID_ROLES`,
  `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`, `VALID_HTTP_CALL_STRATEGIES`,
  `VALID_ASYNC_CALL_STRATEGIES`, `VALID_HTTP_CALL_MATCHES`,
  `VALID_ROUTE_FRAMEWORKS`, `VALID_ROUTE_KINDS`, `VALID_PRODUCER_KINDS`,
  `VALID_RESOLVE_REASONS`, `VALID_UNRESOLVED_CALL_REASONS`.
- Schema changes that affect the Lance index or Kuzu graph need a
  matching update to the README "Re-index required" callout. Bump
  `ontology_version` when enrichment semantics change (currently **15**).
- Brownfield is a first-class surface: any new auto-detection (route,
  role, capability, http client, async producer) must compose with the
  matching `BrownfieldOverrides` layer. Last writer wins (outermost layer
  overrides earlier ones), with one explicit exception: caller-side
  `HTTP_CALLS` / `ASYNC_CALLS` use option-(b) *replacement* rather than
  union when any brownfield layer fires on a method (single network packet
  → single edge). See `plans/completed/PLAN-TIER1B-COMPLETION.md` §
  "Caller-side composition divergence".
- Kuzu's Python binder rejects `dict` for `MAP` columns. Store all
  map-shaped graph_meta data (`routes_by_framework`, `routes_by_layer`,
  `http_calls_by_strategy`, `async_calls_by_strategy`, etc.) as `STRING`
  JSON blobs and decode in `kuzu_queries.meta()`.
- `server.py` is a stdio MCP server: anything reachable from a tool
  handler must not write to **stdout** (that's the JSON-RPC transport).
  Diagnostics go to stderr.
- Tool `description=` strings and `_INSTRUCTIONS` in `server.py` are
  read by LLM clients to choose tools — treat them as part of the
  contract, not freeform docs.
- Don't overfit to the `tests/bank-chat-system/` fixture. It is a
  deterministic corpus, not a model of production. Assert on invariants,
  not exact counts. Don't special-case the fixture in production code.
- Don't introduce a parallel `*Overrides` class when extending brownfield
  support. `BrownfieldOverrides` already holds route, role, capability,
  http client, and async producer dicts — extend it in place.

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

## Validate

- `.venv/bin/ruff check .` — fix or justify warnings.
- `.venv/bin/python -m pytest tests -v` — must pass without
  `JAVA_CODEBASE_RAG_RUN_HEAVY`. Expect skips only where tests document
  env gating (see `tests/README.md`). Each plan may add tests; match the
  active plan if it cites a count.
- Exception for isolated automation workflow changes: if edits are limited to
  `automation/cursor_propose_only/**` (plus optional docs references to that
  workflow), targeted validation is enough:
  - `.venv/bin/ruff check .`
  - `.venv/bin/python -m pytest automation/cursor_propose_only/tests -q`
- For schema or ranking work, also run with
  `JAVA_CODEBASE_RAG_RUN_HEAVY=1` locally (slow; downloads models).
- For graph builder changes, also rebuild a fixture and inspect
  `java-codebase-rag meta` (or `GraphMetaOutput` from the same helper) to
  confirm new counters wire up:
  ```bash
  rm -rf /tmp/check && .venv/bin/python build_ast_graph.py \
    --source-root tests/bank-chat-system \
    --kuzu-path /tmp/check/code_graph.kuzu --verbose
  ```

## Commit and PR

- Branch from `master`. Branch names:
  - `cursor/<topic>` — cursor-agent work
  - `feat/<topic>` — landed-feature work (e.g. `feat/b2b-http-async-edges`)
  - `plan/<name>` — in-progress plan / propose drafts
  - `chore/<topic>` — repo hygiene (docs, tooling, deps)
- Commit messages: present tense, imperative, lowercase first word,
  matching existing style (e.g. `fixed call graph review D6`,
  `applied fixes for call graph layer`).
- One logical change per commit when feasible.
- Always open a PR; never push directly to `master`.
- PR body should reference any propose / plan it implements, list
  user-visible behaviour changes, and call out reindex / env-var /
  ontology bumps explicitly.

## Don't

- Don't run `gh auth status` or otherwise inspect credentials.
- Don't widen the public surface "just in case" — every new tool,
  env var, or schema column adds a re-index burden on users.
- Don't special-case the `tests/bank-chat-system/` fixture in
  production code. If a test needs it, the test is wrong (see
  `tests/README.md`).
- Don't tighten loose test assertions (`>= 1`, `len(...) >= N`,
  `key in result`) into exact counts to chase a number — they are
  intentionally loose.
- Don't add a hard dependency on `cocoindex` outside
  `java_index_flow_lancedb.py` / the `java-codebase-rag` lifecycle (`init` /
  `increment` / `reprocess` / `erase`) path.

## Cursor Cloud specific instructions

This is a self-contained Python project — no external services
(no Postgres, Kafka, Docker) are needed. All storage (Kuzu, LanceDB,
CocoIndex state) is embedded/file-based.

### Environment

- Python 3.11+ with `.venv` at repo root. The update script creates
  the venv and installs deps if missing.
- `.venv/bin` must be on `PATH` for CLI tests
  (`test_java_codebase_rag_cli.py` uses
  `shutil.which("java-codebase-rag")`). The update script handles
  this via `~/.bashrc`.
- The package must be installed in **editable mode**
  (`pip install -e .`) so the `java-codebase-rag` CLI entry point
  is registered. The update script handles this.

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
Claude Code) directly.
