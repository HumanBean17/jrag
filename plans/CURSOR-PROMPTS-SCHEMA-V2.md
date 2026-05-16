# Cursor task prompts — SCHEMA-V2

Status: **active (planning)**. Plan:
[`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md). Propose:
[`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md). Sequence:
[`docs/PROPOSES-ORDER.md`](../docs/PROPOSES-ORDER.md).

One prompt per code PR (**PR-A / PR-B / PR-C**). PR-D (hints) is in
[`plans/CURSOR-PROMPTS-HINTS-V3.md`](./CURSOR-PROMPTS-HINTS-V3.md).

**Landing order:** PR-SCHEMA-V2-A → PR-SCHEMA-V2-B → PR-SCHEMA-V2-C. Do not start the next PR until the previous is merged to `master`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- Nothing reachable from MCP tool handlers may write to **stdout**.
- If ambiguous versus the plan, stop and ask — do not expand scope.
- Do not push git unless the user explicitly asked.
- Confirm [`propose/HINTS-V3-PROPOSE.md`](../propose/HINTS-V3-PROPOSE.md) is on `master` before starting **PR-A** implementation.

---

## PR-SCHEMA-V2-A — `EDGE_SCHEMA` + v14 (no flips)

**Branch:** `feat/schema-v2-edge-schema` off `master`.
**Base:** `master` (with HINTS-V3 propose merged).
**Plan section:** [`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md) § PR-A.
**PR title:** `feat(schema): add EDGE_SCHEMA to java_ontology, generate docs/EDGE-NAVIGATION.md, bump ontology to v14`

**Attach (`@-files`):**

- `@plans/PLAN-SCHEMA-V2.md` (PR-A only)
- `@propose/SCHEMA-V2-PROPOSE.md` (§3.1, §3.5–§3.6, §6 PR-A, Appendix A, Decisions 6–9, 28–29, 31)
- `@propose/HINTS-V3-PROPOSE.md` (§3.4–§3.5 `member_only` / `typical_traversals` — read only)
- `@java_ontology.py`
- `@build_ast_graph.py` (DDL constants — do not flip endpoints)
- `@ast_java.py`
- `@kuzu_queries.py` (version gate ~326)
- `@README.md` (Re-index section)
- `@docs/AGENT-GUIDE.md`

**Prompt:**

````
You are implementing PR-SCHEMA-V2-A from `plans/PLAN-SCHEMA-V2.md` (the **PR-A** section).

Read PR-A **File-by-file changes** and **Tests for PR-A** before coding. Plan wins over this prompt; propose supplies locked shapes.

## Scope

1. **`java_ontology.py`** — `EdgeAttr`, `EdgeSpec`, `EDGE_SCHEMA` for **10 edges** with **pre-flip** `HTTP_CALLS`/`ASYNC_CALLS` (`Symbol→Route`). Include `brownfield_resolver_sourced`; **`typical_traversals: dict[str, str]`** per HINTS-V3 §3.5 (SCHEMA propose Appendix A tuples are illustrative only). **`member_only`:** set `True` on `DECLARES_CLIENT`, `EXPOSES`, `OVERRIDES`, `CALLS` only — **not** `DECLARES_PRODUCER` (PR-C). Add `BROWNFIELD_RESOLVER_STRATEGY_SET` (union of `FUZZY_STRATEGY_SET` + resolver strategies used on edges today); **enumerate every member in the PR body**.
2. **`scripts/generate_edge_navigation.py`** + committed **`docs/EDGE-NAVIGATION.md`** with `--check` mode.
3. **`ast_java.py`** — `ONTOLOGY_VERSION = 14`.
4. **`README.md`** + **`docs/AGENT-GUIDE.md`** — v14 re-index callout (PR-B/C consequences one sentence each).
5. **Tests** — Every `test_*` name under **Tests for PR-A** in the plan (verbatim). New files: `tests/test_schema_consistency.py`, `tests/test_edge_navigation_doc.py`.
6. **CI** — Run generator `--check` in `.github/workflows/test.yml` (or equivalent documented hook).

## Out of scope (do NOT touch)

- Flipping `HTTP_CALLS` / `ASYNC_CALLS` endpoints; `Producer` node; `DECLARES_PRODUCER`.
- `mcp_hints.py`, `neighbors_empty_hints`, hints v3 templates.
- `CallerInfo` / `RouteCaller`, `find_route_callers` Cypher changes.
- `HttpCallRow` / pass6 emit changes.
- `mcp_v2.py` producer `kind` (PR-C).
- Drive-by refactors outside listed files.

## Deliverables

1. `EDGE_SCHEMA` is source of truth for 10 edges; DDL consistency tests pass.
2. Generated edge navigation doc + CI check.
3. Ontology 14 documented; v13 graphs refused by gate test.
4. All PR-A named tests pass.

## Tests to run

```bash
.venv/bin/ruff check java_ontology.py build_ast_graph.py scripts/generate_edge_navigation.py tests/test_schema_consistency.py tests/test_edge_navigation_doc.py
.venv/bin/python -m pytest tests/test_schema_consistency.py tests/test_edge_navigation_doc.py tests/test_kuzu_queries.py -v -k "ontology_version or stale"
.venv/bin/python scripts/generate_edge_navigation.py --check
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches outside PR-A scope)

- `Producer` / `DECLARES_PRODUCER` / `FROM Producer` in `build_ast_graph.py`
- `RouteCaller` / `caller_node_kind`
- `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` (hints PR-D)
- `neighbors_empty_hints`
- `FROM Client TO Route` on `HTTP_CALLS` DDL (flip is PR-B)

## Definition of Done

- [ ] PR-A plan definition of done satisfied.
- [ ] PR title matches plan.
- [ ] PR body: scope, links to plan + propose, test commands, **re-index required (v14)**.
````

---

## PR-SCHEMA-V2-B — `HTTP_CALLS` Client → Route

**Branch:** `feat/schema-v2-http-calls-client-route` off `master` **after PR-A merged**.
**Base:** `master` at merge commit of PR-A.
**Plan section:** [`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md) § PR-B.
**PR title:** `feat(schema): HTTP_CALLS originates from Client, not Symbol`

**Attach (`@-files`):**

- `@plans/PLAN-SCHEMA-V2.md` (PR-B)
- `@propose/SCHEMA-V2-PROPOSE.md` (§3.3–§3.4, §3.7, §3.10, §4 HTTP UCs, PR-B §6)
- `@java_ontology.py`
- `@build_ast_graph.py`
- `@kuzu_queries.py`
- `@pr_analysis.py`
- `@mcp_v2.py`
- `@server.py`
- `@tests/test_call_edges_e2e.py`
- `@tests/test_kuzu_queries.py`
- `@tests/test_pr_analysis.py`
- `@tests/test_brownfield_clients.py`
- `@tests/test_mcp_v2.py`
- `@tests/test_mcp_v2_compose.py`
- `@tests/test_client_hint_recovery.py`

**Prompt:**

````
You are implementing PR-SCHEMA-V2-B from `plans/PLAN-SCHEMA-V2.md` (**PR-B**).

PR-A is on `master` (`EDGE_SCHEMA`, ontology 14, doc generator). Do not re-land PR-A work.

## Scope

1. Flip **`HTTP_CALLS`** to `Client→Route` in `EDGE_SCHEMA`, Kuzu DDL, pass5/6 emission, and **every** Python `grep HTTP_CALLS` site.
2. **`HttpCallRow`**: key edges by `client_id`, not `symbol_id`.
3. **`kuzu_queries.py`**: remove `CallerInfo`; add **`RouteCaller`**; reshape `find_route_callers`, `trace_request_flow` inbound, impact-analysis expansion (§3.7).
4. **`pr_analysis.py`**: two-hop HTTP route reachability.
5. **`mcp_v2.py` / `server.py`**: output types and descriptions aligned with `RouteCaller`.
6. **HTTP doc sweep** — `README.md`, `docs/AGENT-GUIDE.md`, `docs/skills/java-codebase-explore.md`; regenerate `docs/EDGE-NAVIGATION.md`.
7. **Tests** — All **Tests for PR-B** names in the plan (verbatim).

## Out of scope (do NOT touch)

- `Producer`, `ASYNC_CALLS` flip, `DECLARES_PRODUCER` (PR-C).
- `mcp_hints.py` / hints v3 (PR-D).
- `find(kind="producer")` / `resolve(hint_kind="producer")` (PR-C).
- Second ontology bump.
- `TPL_NEIGHBORS_*` template changes.

## PR body requirement

Paste `grep -rn 'HTTP_CALLS' --include='*.py' --include='*.md' .` and account for every hit.

## Tests to run

```bash
.venv/bin/ruff check build_ast_graph.py kuzu_queries.py pr_analysis.py mcp_v2.py
.venv/bin/python -m pytest tests/test_call_edges_e2e.py tests/test_kuzu_queries.py tests/test_pr_analysis.py tests/test_client_hint_recovery.py -v
.venv/bin/python scripts/generate_edge_navigation.py --check
```

Before PR open: `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v`.

## Sentinel checks (zero on diff)

- `CallerInfo` (use `RouteCaller` only)
- `Symbol)-[e:HTTP_CALLS]` / `Symbol)-[:HTTP_CALLS]` in production queries
- `Producer` / `DECLARES_PRODUCER`
- `neighbors_empty_hints`

## Definition of Done

- [ ] PR-B plan DoD + grep enumeration in PR description.
- [ ] PR title: `feat(schema): HTTP_CALLS originates from Client, not Symbol`
````

---

## PR-SCHEMA-V2-C — Producer + `ASYNC_CALLS` flip

**Branch:** `feat/schema-v2-producer-async-calls` off `master` **after PR-B merged**.
**Base:** `master` at merge commit of PR-B.
**Plan section:** [`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md) § PR-C.
**PR title:** `feat(schema): introduce Producer node and route ASYNC_CALLS through it`

**Attach (`@-files`):**

- `@plans/PLAN-SCHEMA-V2.md` (PR-C)
- `@propose/SCHEMA-V2-PROPOSE.md` (§3.2, §3.3–§3.4, §3.6–§3.9, §4 async UCs, PR-C §6)
- `@graph_enrich.py` (`AsyncProducerHint`)
- `@java_ontology.py`
- `@build_ast_graph.py`
- `@kuzu_queries.py`
- `@pr_analysis.py`
- `@mcp_v2.py`
- `@server.py`
- `@tests/test_call_edges_e2e.py`
- `@tests/test_brownfield_clients.py`
- `@tests/test_mcp_v2.py`
- `@tests/test_ast_graph_build.py`
- `@tests/test_client_node_extraction.py`

**Prompt:**

````
You are implementing PR-SCHEMA-V2-C from `plans/PLAN-SCHEMA-V2.md` (**PR-C**).

PR-B is on `master` (HTTP_CALLS from Client). Do not revert HTTP shape.

## Scope

1. **`Producer` node** + **`DECLARES_PRODUCER`** + **`ASYNC_CALLS` `Producer→Route`** in schema, DDL, pass5 materialization, pass6 emission.
2. **`AsyncCallRow.producer_id`**; Producer fields from `AsyncProducerHint` / dispatch metadata (propose §3.2 table — no HTTP-only copy-paste).
3. **`GraphMeta`**: `producers_total`, `declares_producer_total`; wire `server.py` meta output if applicable.
4. **`kuzu_queries.py`**: async two-hop in `find_route_callers`, `trace_request_flow`, impact analysis; producer branch on `RouteCaller`.
5. **`pr_analysis.py`**: async two-hop route reachability (`DECLARES_PRODUCER` + `ASYNC_CALLS`; HTTP already two-hop from PR-B).
6. **`mcp_v2.py`**: `find(kind="producer")`, `resolve(hint_kind="producer")`, `_load_node_record` for Producer.
7. **`java_ontology.py`**: `DECLARES_PRODUCER` in `EDGE_SCHEMA` with `member_only=True`.
8. **Type-level `describe` rollups**: `DECLARES.DECLARES_PRODUCER`, `OVERRIDDEN_BY.DECLARES_PRODUCER`.
9. **Async doc sweep** + regenerate `docs/EDGE-NAVIGATION.md` (11 edges).
10. **Tests** — All **Tests for PR-C** in the plan (verbatim).

## Out of scope (do NOT touch)

- `mcp_hints.py` / PR-D hints v3.
- `Consumer` node.
- Ontology 15 / second re-index bump.
- Ranking or incremental index proposes.

## PR body requirement

`grep -rn 'ASYNC_CALLS\|Producer\|DECLARES_PRODUCER' --include='*.py' --include='*.md' .` — account for every hit.

## Tests to run

```bash
.venv/bin/ruff check build_ast_graph.py kuzu_queries.py mcp_v2.py
.venv/bin/python -m pytest tests/test_call_edges_e2e.py tests/test_brownfield_clients.py tests/test_mcp_v2.py tests/test_ast_graph_build.py -v
rm -rf /tmp/schema-v2-c && .venv/bin/python build_ast_graph.py --source-root tests/fixtures/http_caller_smoke --kuzu-path /tmp/schema-v2-c --verbose
```

Before PR open: `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v`.

## Sentinel checks (zero on diff)

- `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` / `neighbors_empty_hints`
- `Symbol)-[e:ASYNC_CALLS]` in production emit/query paths
- `ONTOLOGY_VERSION = 15`

## Definition of Done

- [ ] PR-C plan DoD + async grep in PR description.
- [ ] PR title matches plan.
- [ ] Note: **PR-D (hints)** is next — do not implement hints in this PR.
````
