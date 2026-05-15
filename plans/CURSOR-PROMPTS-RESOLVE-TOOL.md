# Cursor task prompts — `resolve` tool (PR-RESOLVE-1 → PR-RESOLVE-2)

Status: **active**. Plan:
[`PLAN-RESOLVE-TOOL.md`](PLAN-RESOLVE-TOOL.md); propose:
[`propose/RESOLVE-TOOL-PROPOSE.md`](../propose/RESOLVE-TOOL-PROPOSE.md).

One prompt per PR. Copy the prompt verbatim into Cursor agent mode with the
listed `@-files` attached.

**Workflow per PR:**

1. Branch off `master` (or off merged PR-RESOLVE-1 for PR-RESOLVE-2).
2. Paste the prompt; let the agent implement.
3. Run validation commands from the prompt.
4. Commit; open PR with scope + manual evidence from the prompt.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No ontology bump; no graph schema changes.
- No `git push` from the agent.
- If ambiguous, stop and ask — do not expand scope.

---

## PR-RESOLVE-1 — Implement `resolve`

**Branch:** `feat/resolve-tool` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-RESOLVE-TOOL.md` § PR-RESOLVE-1.
**PR title:** `add resolve mcp tool for identifier-shaped lookups`

**Attach (`@-files`):**

- `@plans/PLAN-RESOLVE-TOOL.md` (PR-RESOLVE-1 section only)
- `@propose/RESOLVE-TOOL-PROPOSE.md`
- `@java_ontology.py`
- `@mcp_v2.py`
- `@server.py`
- `@tests/conftest.py`
- `@tests/test_mcp_v2.py`
- `@kuzu_queries.py` (read-only — reuse query patterns; do not add scope unless necessary)

**Prompt:**

````
You are implementing PR-RESOLVE-1 from `plans/PLAN-RESOLVE-TOOL.md`.

Read the **PR-RESOLVE-1** section and `propose/RESOLVE-TOOL-PROPOSE.md` §3–§6
before writing code. If this prompt and the plan disagree, the plan wins.

## Scope

Ship the fifth MCP tool `resolve(identifier, hint_kind?)` end-to-end:

1. **`java_ontology.py`** — `VALID_RESOLVE_REASONS` + `ResolveReason` Literal (§3.4).
2. **`mcp_v2.py`** — `ResolveCandidate`, `ResolveOutput`, `resolve_v2`, candidate
   generators for symbol/route/client, dedupe by `node.id`, ranking (reason priority →
   specificity → `node.id`), cap K=10, status decision (§3.6), invariant guard.
   Malformed empty/whitespace → `success=False`, `status="none"`,
   `message` starts `"Invalid identifier:"`.
3. **`server.py`** — register `@mcp.tool(name="resolve")` with a complete
   description (identifier-shaped, three statuses, search fallback on none).
   Minimal `_INSTRUCTIONS` mention of `resolve` is OK.
4. **`tests/conftest.py`** — `kuzu_graph_fqn_collision_smoke` fixture.
5. **`tests/test_mcp_v2.py`** — all tests named in the plan § "Tests for PR-RESOLVE-1"
   (exact function names).

Reuse `_node_ref_from_row`, `_node_kind_from_id`, and `g._rows` patterns from
existing handlers. Prefer bank-chat `kuzu_graph`, `kuzu_graph_fqn_collision_smoke`
for UC3, `kuzu_graph_route_extraction_smoke` for routes. Use FakeGraph stubs where
the plan allows (dedupe test, cross-kind if needed).

## Out of scope (do NOT touch)

- PR-RESOLVE-2 description sweep (do not remove "until resolve ships" from
  `search`/`describe` tool descriptions — that is the next PR).
- `describe_v2` hint_message text changes.
- `docs/AGENT-GUIDE.md`, `README.md` (except if you accidentally need a one-line
  import — you should not).
- `build_ast_graph.py`, `java_index_flow_lancedb.py`, ontology_version.
- Renaming `_resolve_node_kind`.
- Wildcard support in identifiers.

Sentinel (must be zero on `git diff master..HEAD` for these patterns in files
you were allowed to touch for *behavioral* fallback removal — N/A for RESOLVE-1;
but do not edit AGENT-GUIDE/README in this PR):

- Do not delete fallback wording from sibling tools in `server.py` (RESOLVE-2).

## Deliverables

Match `plans/PLAN-RESOLVE-TOOL.md` § PR-RESOLVE-1 definition of done.

## Validation

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_v2.py -v -k resolve
.venv/bin/python -m pytest tests -v
```

Manual spot-check (stderr ok):

```bash
.venv/bin/python -c "
from kuzu_queries import KuzuGraph
import mcp_v2
from tests.conftest import TESTS_DIR
from pathlib import Path
import os
# optional: only if you have session db path handy
"
```

PR body must include: scope statement, pytest evidence (commands above), note
that ontology_version is unchanged and PR-RESOLVE-2 will sweep descriptions.
````

---

## PR-RESOLVE-2 — Tool-description sweep

**Branch:** `feat/resolve-tool-docs` off `master` (after PR-RESOLVE-1 merged).
**Base:** `master`.
**Plan section:** `plans/PLAN-RESOLVE-TOOL.md` § PR-RESOLVE-2.
**Blocked on:** PR-RESOLVE-1 merged to `master`.
**PR title:** `point agent docs at resolve for identifier lookups`

**Attach (`@-files`):**

- `@plans/PLAN-RESOLVE-TOOL.md` (PR-RESOLVE-2 section only)
- `@propose/RESOLVE-TOOL-PROPOSE.md` (§3.7, §6 checklist)
- `@server.py`
- `@mcp_v2.py` (hint_message only)
- `@docs/AGENT-GUIDE.md`
- `@README.md`
- `@tests/test_mcp_v2.py`

**Prompt:**

````
You are implementing PR-RESOLVE-2 from `plans/PLAN-RESOLVE-TOOL.md`.

PR-RESOLVE-1 (`resolve` tool) is already on master. This PR is **docs and
agent-facing prose only** plus test expectation updates — no new resolve logic.

## Scope

1. **`server.py`** — Remove pre-`resolve` fallback wording from `search`,
   `describe`, `find`, `neighbors` tool descriptions and `_INSTRUCTIONS`.
   Add `resolve` to the inventory. Point identifier-shaped workflows at `resolve`.
2. **`mcp_v2.py`** — Update `describe_v2` FQN-collision `hint_message` to
   recommend `resolve(identifier=..., hint_kind='symbol')` instead of
   find/search multi-call patterns.
3. **`docs/AGENT-GUIDE.md`** — Five-tool surface; rewrite identifier resolution
   section (drop "pre-resolve" framing).
4. **`README.md`** — Five tools in intro; add `resolve` row to MCP tool table.
5. **`tests/test_mcp_v2.py`** —
   - Rename/update `test_describe_by_fqn_duplicate_returns_first_with_disambiguation_hint`
     → `test_describe_by_fqn_duplicate_hint_points_to_resolve` (or keep name but
     update assertions per plan).
   - Add `test_server_tool_descriptions_no_pre_resolve_fallback` per plan.

## Out of scope (do NOT touch)

- `resolve_v2` implementation / candidate generators (already shipped).
- `java_ontology.py`, `build_ast_graph.py`, graph builder, indexer.
- Changing `describe(fqn=…)` first-match behavior.
- Adding `microservice` to `describe`.

Sentinel grep — must return **no matches** on `git diff master..HEAD` for:

```
until a dedicated `resolve` tool exists
until `resolve` ships
describe` on promising candidates
describe` per candidate
```

(Allow `search` mentioned inside `resolve` tool description as the fallback
for `status="none"`.)

## Validation

```bash
.venv/bin/ruff check .
grep -En 'per.candidate|until.*resolve|promising candidates' server.py docs/AGENT-GUIDE.md README.md mcp_v2.py || true
.venv/bin/python -m pytest tests/test_mcp_v2.py -v -k 'resolve or describe_by_fqn or tool_descriptions_no_pre_resolve'
.venv/bin/python -m pytest tests -v
```

PR body: scope statement, grep output, pytest commands, explicit note that this
PR must not merge before PR-RESOLVE-1 (if branched early, say so).
````
