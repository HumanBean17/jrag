# Cursor task prompts — Hints (road signs) + stored `OVERRIDES`

Status: **active**. Plan: [`plans/PLAN-HINTS.md`](./PLAN-HINTS.md). Propose:
[`propose/HINTS-ROAD-SIGNS-PROPOSE.md`](../propose/HINTS-ROAD-SIGNS-PROPOSE.md).

One prompt per PR. Copy the fenced **Prompt** block into Cursor agent mode with the
listed `@-files` attached.

**Landing order:** PR-HINTS-A → PR-HINTS-B. Do not start PR-HINTS-B until PR-HINTS-A
is merged to `master`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only (repo venv).
- Nothing reachable from MCP tool handlers may write to **stdout** (`server.py` stdio rule).
- If ambiguous versus the plan, stop and ask — do not expand scope.
- Do not push git from the agent unless the user explicitly asked.

---

## PR-HINTS-A — Stored `OVERRIDES` edges + ontology bump

**Branch:** `feat/hints-stored-overrides` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-HINTS.md`](./PLAN-HINTS.md) § PR-A — Stored `OVERRIDES` edges + ontology bump.
**PR title:** `materialize OVERRIDES rel and bump ontology to 13`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS.md` (PR-A section + principles + cross-PR risks)
- `@propose/HINTS-ROAD-SIGNS-PROPOSE.md` (§6 PR-A, §7.17)
- `@build_ast_graph.py`
- `@ast_java.py`
- `@kuzu_queries.py` (`override_axis_rollup_for` — reference only unless optional hygiene is justified)
- `@mcp_v2.py` (`EdgeType`, `NodeRecord.edge_summary` description)
- `@server.py` (describe tool description prose around override-axis keys)
- `@README.md` (ontology + re-index callouts)
- `@tests/test_mcp_v2_compose.py`
- `@tests/test_call_edges_e2e.py`
- `@tests/conftest.py` (if session graph / builders need adjustment)
- `@java_index_flow_lancedb.py` (read first — only if a hard-coded ontology `12` must become `ONTOLOGY_VERSION`-driven)

**Prompt:**

````
You are implementing PR-HINTS-A from `plans/PLAN-HINTS.md` (the **PR-A** section).

Read the PR-A **File-by-file changes** and **Tests for PR-A** before coding. If this
prompt and the plan disagree, the plan wins; the propose fills background only.

## Scope

1. **`build_ast_graph.py`** — Add `OVERRIDES` as `Symbol`→`Symbol` `CREATE REL TABLE`
   (minimal columns, same spirit as `DECLARES` unless you must add a column). Wire
   `_drop_all` / schema init. In the relationship-write pass (not `graph_enrich.py`),
   emit `(A)-[:OVERRIDES]->(B)` for subtype instance methods overriding supertype
   declared methods with matching `signature`, following the unified directed rule in
   the plan / propose §6 (covers both `override_axis_rollup_for` arms). Dedupe;
   deterministic ordering consistent with the rest of the builder.
2. **`ast_java.py`** — Bump `ONTOLOGY_VERSION` **12 → 13**. Grep for hard-coded `12`
   in graph-meta / index paths and fix if any slipped in.
3. **`mcp_v2.py`** — Add `"OVERRIDES"` to `EdgeType`. Tighten `NodeRecord.edge_summary`
   description: stored `OVERRIDES` **is** valid for `neighbors(edge_types=…)`;
   `OVERRIDDEN_BY*` and dot-keys remain invalid there; clarify rollup dict key vs rel
   label if both read "OVERRIDES".
4. **`server.py`** — Update `describe` tool `description=` (and any `_INSTRUCTIONS`
   lines) so they no longer imply `OVERRIDES` cannot be used in `neighbors` — only
   virtual / rollup keys stay non-`EdgeType`.
5. **`README.md`** — Document ontology **13**, re-index required, and what changed
   (stored `OVERRIDES` traversability + bump).
6. **Tests** — Implement every test named in the plan **Tests for PR-A** list (exact
   `def test_*` names). Prefer `tests/fixtures/override_axis_rollup_smoke` and/or
   session fixtures; add a new test module only if compose file size warrants it.

## Out of scope (do NOT touch)

- `mcp_hints.py`, hint fields, pagination echo on find/search (PR-HINTS-B).
- Changing `override_axis_rollup_for` semantics unless required for correctness
  (plan marks optional hygiene — default **leave unchanged**).
- `neighbors_v2` special-casing virtual Cypher for `OVERRIDES` instead of stored edges.
- Special-casing `tests/bank-chat-system/` in production code.
- Drive-by refactors outside files needed for PR-A.

If you need to touch `java_index_flow_lancedb.py`, only for wiring that hard-codes
ontology **12** — stop and verify against `ONTOLOGY_VERSION` import instead.

## Deliverables

1. Graph builds include traversable `OVERRIDES` edges; `DROP`/schema order is correct.
2. `ONTOLOGY_VERSION` / graph meta reads **13** after rebuild.
3. `neighbors_v2(..., edge_types=["OVERRIDES"])` works in and out per equivalence tests.
4. Agent-facing prose matches post–PR-A rules.
5. All PR-A named tests exist and pass.

## Tests to run (iteration loop)

Run only these files during local iteration; CI `test` on the PR + `master` is the
merge gate (full `pytest tests` with `JAVA_CODEBASE_RAG_RUN_HEAVY` unset/`0` when
Python/sources change). The list below is for speed only.

- `tests/test_mcp_v2_compose.py` — neighbors / describe / override-axis smoke against Kuzu.
- `tests/test_call_edges_e2e.py` — `ontology_version` matches `ONTOLOGY_VERSION` invariant.
- `tests/test_ast_graph_build.py` — graph build / meta smoke if your schema DDL or GraphMeta path touches shared asserts.

## Tests

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: full suite green; no unexpected skips beyond documented env gates.
New PR-A tests must not require `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

## Sentinel checks

```bash
rg -n "ONTOLOGY_VERSION = 1[23]" ast_java.py
rg -n "CREATE REL TABLE OVERRIDES" build_ast_graph.py
rg -n "OVERRIDES" server.py mcp_v2.py | head -n 50
```

Expect: `ONTOLOGY_VERSION = 13` exactly once in `ast_java.py`; `OVERRIDES` rel DDL
present; server `describe` text must **not** claim stored `OVERRIDES` is unusable in
`neighbors` (rollup/dot-key carve-out may remain for virtual keys).

```bash
# Allowed paths for PR-A (see `plans/PLAN-HINTS.md` PR-A file-by-file). Anything
# else printed here is a red flag — confirm against that list or trim scope before merge.
git diff master..HEAD --name-only | rg -v '^(tests/|build_ast_graph\.py|ast_java\.py|java_index_flow_lancedb\.py|kuzu_queries\.py|mcp_v2\.py|server\.py|README\.md)$' || true
```

Interpretation: empty output means every changed path is in the allowlist. If a path
appears, it is **not** automatically a mistake (`kuzu_queries.py` is allowed only for
optional hygiene called out in PLAN-HINTS PR-A; `java_index_flow_lancedb.py` only for
hard-coded ontology wiring) — but anything **outside** the regex (for example
`graph_enrich.py`, `search_lancedb.py`, `propose/`) needs either a plan amendment or a
revert; do not silently expand PR-A.

## Manual evidence

Rebuild a small fixture graph and show `OVERRIDES` exists (adapt paths to your machine):

```bash
rm -rf /tmp/hints-pr-a && mkdir -p /tmp/hints-pr-a && \
  .venv/bin/python build_ast_graph.py \
  --source-root tests/fixtures/override_axis_rollup_smoke \
  --kuzu-path /tmp/hints-pr-a/code_graph.kuzu --verbose
```

Then a one-liner or small script: `MATCH … [:OVERRIDES] …` count `> 0` where the
fixture expects overrides; paste command + key line in the PR body.

## Definition of Done

- [ ] PR-A plan definition of done satisfied (`plans/PLAN-HINTS.md`).
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [ ] PR title: `materialize OVERRIDES rel and bump ontology to 13`
- [ ] Branch: `feat/hints-stored-overrides`
- [ ] PR body: scope, link to plan + propose, manual evidence command, ontology/re-index callout.
````

---

## PR-HINTS-B — `hints`, pagination echo, v1 catalog

**Branch:** `feat/mcp-v2-hints` off `master` **after PR-HINTS-A is merged**.
**Base:** `master` at merge commit of PR-HINTS-A.
**Plan section:** [`plans/PLAN-HINTS.md`](./PLAN-HINTS.md) § PR-B — `hints`, pagination echo, `mcp_hints.py` catalog.
**PR title:** `add MCP v2 hints and find/search pagination echo`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS.md` (PR-B section + principles §7.12 priority)
- `@propose/HINTS-ROAD-SIGNS-PROPOSE.md` (§3, Appendix A — canonical template strings)
- `@mcp_hints.py` (create)
- `@mcp_v2.py` (outputs + handler wiring)
- `@server.py` (optional: tool `description=` updates for `hints` / pagination — minimal)
- `@README.md` (MCP v2 response fields)
- `@tests/test_mcp_hints.py` (create, or split per plan)

**Prompt:**

````
You are implementing PR-HINTS-B from `plans/PLAN-HINTS.md` (the **PR-B** section).

PR-HINTS-A is already on `master` (stored `OVERRIDES`, `EdgeType` includes it,
ontology 13). Do not re-land builder/schema work here.

Read **Appendix A** in `propose/HINTS-ROAD-SIGNS-PROPOSE.md` for verbatim v1 template
strings. If §3.3 and Appendix A disagree, Appendix A wins; if you change strings,
update the propose in the same PR.

## Scope

1. **`mcp_hints.py` (new)** — Pure `generate_hints(output_kind, payload) -> list[str]`
   (exact signature may vary; must stay **no I/O**, no graph, no LLM). Implement the
   locked v1 catalog, **dedupe by rendered string**, then **cap to ≤5** using priority
   order in propose §7.12 / plan principles. Enforce road-sign discipline (≤120 chars
   rendered per template via tests).
2. **`mcp_v2.py`** — Add `hints` to `SearchOutput`, `FindOutput`, `DescribeOutput`,
   `NeighborsOutput` with the normative Field description from the propose §3.1. Add
   `limit`/`offset` echo to `FindOutput` and `SearchOutput` (`None` on `success=False`).
   Wire every success path to set echoes + `hints`; every `success=False` path sets
   `hints=[]` and `limit=offset=None` for find/search.
3. **`README.md`** — Document `hints` and pagination echo briefly under MCP v2.
4. **`server.py`** — Only if needed for LLM-facing tool descriptions; keep stdout clean.
5. **Tests** — Implement **all** `test_*` names listed under **Tests for PR-B** in
   `plans/PLAN-HINTS.md` (verbatim names). Prefer crafted pydantic payloads where the
   scenario does not need a DB; use `kuzu_graph` / fixtures where integration is
   required.

## Out of scope (do NOT touch)

- `build_ast_graph.py` schema / `OVERRIDES` edge emission (PR-HINTS-A only).
- Bumping `ONTOLOGY_VERSION` again unless you discover a missed hard-code from PR-A
  (unlikely — stop and ask).
- New MCP tools, structured `next_actions`, per-row hints, i18n, `hints_version`.
- Extra cross-tool hint rows beyond the locked `find` empty → `resolve` row.
- `neighbors` pagination echo.
- Widening unrelated tool descriptions beyond hints/pagination mentions.

## Deliverables

1. New `mcp_hints.py` with catalog + priority + dedupe + cap.
2. Extended output models + handler wiring in `mcp_v2.py`.
3. README (and optional `server.py`) updates aligned with behavior.
4. All PR-B named tests from the plan exist and pass.

## Tests to run (iteration loop)

Run only these files during local iteration; CI `test` is the merge gate (full
`pytest tests` for code changes).

- `tests/test_mcp_hints.py` — pure hint catalog, cap, dedupe, priority, char limits, error paths.
- `tests/test_mcp_v2.py` — only if you adjust shared v2 helpers used by multiple tools.
- `tests/test_mcp_v2_compose.py` — describe / find / search / neighbors integration against session Kuzu.

## Tests

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: full suite green; default run does not require heavy env vars.

## Sentinel checks

```bash
rg "KuzuGraph|_rows\\(|SentenceTransformer|run_search" mcp_hints.py || true
```

Expect **no matches** in `mcp_hints.py` (hint generation stays pure).

```bash
rg -n "hints" mcp_v2.py | head -n 40
```

Verify success constructors set `hints` and failure paths default empty list.

```bash
git diff master..HEAD --name-only | rg 'build_ast_graph\\.py' && exit 1 || true
```

Expect: **no** `build_ast_graph.py` in PR-B diff (stop if graph builder appears).

## Manual evidence

Optional quick REPL (adapt ids from your local graph):

```bash
.venv/bin/python -c "
from kuzu_queries import KuzuGraph
from mcp_v2 import describe_v2
g = KuzuGraph('tests/bank-chat-system/.kuzu')
# pick any sym id from your DB
out = describe_v2(id='sym:dummy', graph=g)
print('fields', getattr(out, 'hints', None))
"
```

Replace with a real id from `tests/bank-chat-system` after confirming the field
shape; paste one successful `describe` / `find` JSON snippet showing `hints` and
echoed `limit`/`offset` in the PR body.

## Definition of Done

- [ ] PR-B plan definition of done satisfied.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [ ] PR title: `add MCP v2 hints and find/search pagination echo`
- [ ] Branch: `feat/mcp-v2-hints`
- [ ] PR body: scope, link to plan + propose Appendix A, note PR-HINTS-A dependency merged.
````
