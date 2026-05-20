# Agent workflow

## Investigate before editing

For any non-trivial change, read the relevant doc first instead of
inferring from code:

- Behaviour / public surface → `README.md`.
- Brownfield assumptions, role/capability tuning → `CODEBASE_REQUIREMENTS.md`.
- In-flight design proposes → **`propose/*.md` at the root of `propose/`**
  (not under `propose/completed/`). **List or search** for current names.
- Why current design exists → `propose/completed/` and `plans/completed/`.
- Testing philosophy → `tests/README.md`.
- In-flight multi-PR scope → **`plans/*.md` at the root of `plans/`**
  (not under `plans/completed/`). Rule files are not a live catalog; **list
  or search `plans/`** for active `PLAN-*.md` / `CURSOR-PROMPTS-*.md`.
  Finished plans and prompt templates → `plans/completed/`.

## Propose-then-implement culture

The repo has a strong "propose then implement" culture
(`propose/`, `plans/`). For non-trivial features:

1. Drop a short markdown propose under `propose/` describing scope,
   schema impact, reindex requirement, and tests touched.
2. For multi-PR efforts, add a matching `plans/PLAN-<topic>.md` with
   per-PR sections, then `plans/CURSOR-PROMPTS-<topic>.md` with the
   per-PR agent execution prompts (Cursor and Claude Code).
3. Reference the propose / plan from the PR description.
4. Move propose into `propose/completed/` (or plan into
   `plans/completed/`) once the *whole* effort is landed — not after
   each PR.

Skip this for clearly-bounded fixes (one-file bugs, doc edits, test
loosening). Use judgement.

## Per-PR task contract (agent handoffs)

When you're given a per-PR task prompt from `plans/CURSOR-PROMPTS-*.md`:

- **Scope is binding.** The "Out of scope (do NOT touch)" list is a
  hard constraint, not a guideline. Sentinel grep patterns the prompt
  lists must return zero on `git diff master..HEAD`.
- **Implement in the listed order.** Do not reshape the PR or roll
  multiple PRs together.
- **Match named tests verbatim.** When the plan §4 table lists
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

- Respect `.claude/rules/breaking-changes.md`: no compatibility
  shims, no deprecation cycles.
- One source of truth for ontology values lives in
  `java_ontology.py`. Don't sprinkle role / capability / client-kind /
  strategy / match string literals across other modules.
- Schema changes that affect the Lance index or Kuzu graph need a
  matching update to the README "Re-index required" callout. Bump
  `ontology_version` when enrichment semantics change. The current
  version is **14**.
- Brownfield is a first-class surface: any new auto-detection
  (route, role, capability, http client, async producer) must
  compose with the matching `BrownfieldOverrides` layer. Last writer
  wins (outermost layer overrides earlier ones), with one explicit
  exception: caller-side `HTTP_CALLS` / `ASYNC_CALLS` use option-(b)
  *replacement* rather than union when any brownfield layer fires
  on a method (single network packet → single edge). See
  `plans/completed/PLAN-TIER1B-COMPLETION.md` § "Caller-side composition
  divergence".
- Kuzu's Python binder rejects `dict` for `MAP` columns. Store all
  map-shaped graph_meta data (`routes_by_framework`,
  `routes_by_layer`, `http_calls_by_strategy`,
  `async_calls_by_strategy`, etc.) as `STRING` JSON blobs and decode
  in `kuzu_queries.meta()`.
- `server.py` is a stdio MCP server: anything reachable from a tool
  handler must not write to **stdout** (that's the JSON-RPC
  transport). Diagnostics go to stderr.
- Tool `description=` strings and `_INSTRUCTIONS` in `server.py` are
  read by LLM clients to choose tools — treat them as part of the
  contract, not freeform docs.

## Validate

- `.venv/bin/ruff check .` — fix or justify warnings.
- `.venv/bin/python -m pytest tests -v` — must pass without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
  Expect skips only where tests document env gating (see `tests/README.md`).
  Each plan may add tests; match the active plan if it cites a count.
- Exception for isolated automation workflow changes: if edits are limited to
  `automation/cursor_propose_only/**` (plus optional docs references to that
  workflow), targeted validation is enough:
  - `.venv/bin/ruff check .`
  - `.venv/bin/python -m pytest automation/cursor_propose_only/tests -q`
- For schema or ranking work, also run with
  `JAVA_CODEBASE_RAG_RUN_HEAVY=1` locally (slow; downloads models).
- For graph builder changes, also rebuild a fixture and inspect
  `java-codebase-rag meta` (or `GraphMetaOutput` from the same helper) to confirm new counters wire up:
  ```bash
  rm -rf /tmp/check && .venv/bin/python build_ast_graph.py \
    --source-root tests/bank-chat-system --kuzu-path /tmp/check --verbose
  ```

## Commit and PR

- Commit messages: present tense, imperative, lowercase first word,
  matching existing style (e.g. `fixed call graph review D6`,
  `applied fixes for call graph layer`).
- One logical change per commit when feasible.
- Branch names:
  - `cursor/<topic>` — Cursor-agent work
  - `feat/<topic>` — landed-feature work (e.g. `feat/b2b-http-async-edges`)
  - `plan/<name>` — in-progress plan / propose drafts
  - `chore/<topic>` — repo hygiene (docs, tooling, deps)
- PR body should reference any propose / plan it implements, list
  user-visible behaviour changes, and call out reindex / env-var /
  ontology bumps explicitly.
- Never push directly to `master`.

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
- Don't introduce a parallel `*Overrides` class when extending
  brownfield support. `BrownfieldOverrides` already holds route,
  role, capability, http client, and async producer dicts — extend
  it in place.
