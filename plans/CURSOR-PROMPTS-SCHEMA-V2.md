# Cursor task prompts — SCHEMA-V2 (PR-A → PR-D)

Per-PR delegation prompts derived from
[`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md). Each section is one hand-off —
copy the block for the PR you are implementing.

**Landing order:** **A → B → C → D**. Do not start the next PR until the prior one
is merged to `master`.

**Merge gate (Decision 29):** This file and `PLAN-SCHEMA-V2.md` must exist on
`master` before **PR-A** code merges.

**Common rules for all prompts:**

- The plan is the source of truth. If this prompt and
  [`plans/PLAN-SCHEMA-V2.md`](./PLAN-SCHEMA-V2.md) disagree, the plan wins.
- Do **not** modify files outside the deliverables list. Sentinel greps in each
  prompt must return **zero** on `git diff master..HEAD` for forbidden patterns.
- No backward-compat shims (`CallerInfo` alias, dual edges, v13 soft-migration).
- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- Run `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` before
  opening the PR (heavy tests optional unless you touch indexer e2e).
- PR description must include: scope sentence, manual evidence (exact command from
  prompt), link to plan section, grep enumeration (PR-B/C), re-index callout.
- Do not push from the agent; you handle `git push` and `gh pr create`.

---

## PR-A — `EDGE_SCHEMA` + doc generator + ontology v14 (no DDL flips)

**Branch:** `feat/schema-v2-edge-schema` off latest `master`  
**Plan section:** [`plans/PLAN-SCHEMA-V2.md` § PR-A](./PLAN-SCHEMA-V2.md#pr-a--edge_schema--doc-generator--ontology-v14-no-ddl-flips)  
**Propose:** [`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md) §6 PR-A

### Scope

Implement PR-A only: schema infrastructure + `ONTOLOGY_VERSION` 14 + generated docs.
**Do not** flip `HTTP_CALLS` / `ASYNC_CALLS` endpoints or add `Producer` DDL.

### Out of scope (do NOT touch)

- `build_ast_graph.py` rel endpoint changes (`FROM Client`, `Producer` table).
- `HttpCallRow` / `pass5` / `pass6` behaviour.
- `kuzu_queries.find_route_callers`, `trace_request_flow`, `pr_analysis` Cypher.
- `mcp_v2.py` / `server.py` tool surfaces (except if a test import requires none).
- `mcp_hints.py` hints v3 templates.
- `propose/HINTS-V3-PROPOSE.md` (does not exist yet).

**Sentinel (must be zero on diff):**

```bash
git diff master..HEAD | grep -E 'FROM Client TO Route|FROM Producer TO Route|DECLARES_PRODUCER|ProducerRow|RouteCaller'
```

### Deliverables

See plan § PR-A file list. Headline:

1. `java_ontology.py` — `EdgeAttr`, `EdgeSpec`, `EDGE_SCHEMA` (10 edges, **current**
   Symbol→Route for HTTP/ASYNC), `BROWNFIELD_RESOLVER_STRATEGY_SET`.
2. `scripts/generate_edge_navigation.py` + committed `docs/EDGE-NAVIGATION.md`.
3. `tests/test_schema_consistency.py`, `tests/test_edge_navigation_doc.py`.
4. `ast_java.py` — `ONTOLOGY_VERSION = 14`.
5. `README.md`, `docs/AGENT-GUIDE.md` — version **14** + re-index note (shape changes in B/C).
6. `build_ast_graph.py` — export DDL tuple for tests only (no endpoint edits).

### Tests (verbatim names)

- `test_schema_ddl_endpoints_match_edge_schema_*` (all 10 edges)
- `test_edge_navigation_doc_matches_generator_output`
- `test_generate_edge_navigation_check_detects_drift`
- `test_kuzu_graph_get_raises_when_graph_ontology_too_old`

### Manual evidence (paste in PR description)

```bash
.venv/bin/python scripts/generate_edge_navigation.py --check
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_schema_consistency.py tests/test_edge_navigation_doc.py tests/test_kuzu_queries.py::test_kuzu_graph_get_raises_when_graph_ontology_too_old -v
python -c "from java_ontology import EDGE_SCHEMA; from ast_java import ONTOLOGY_VERSION; assert ONTOLOGY_VERSION==14; assert len(EDGE_SCHEMA)==10; assert EDGE_SCHEMA['HTTP_CALLS'].src=='Symbol'"
```

### PR title

`feat(schema): add EDGE_SCHEMA, edge navigation doc generator, bump ontology to v14`

---

## PR-B — flip `HTTP_CALLS` to `Client → Route`

**Branch:** `feat/schema-v2-http-calls-client-origin` off latest `master` (post PR-A)  
**Plan section:** [`plans/PLAN-SCHEMA-V2.md` § PR-B](./PLAN-SCHEMA-V2.md#pr-b--flip-http_calls-to-client--route)  
**Propose:** [`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md) §6 PR-B

### Scope

Flip HTTP caller edges and all HTTP downstream traversals/APIs. Async may remain
one-hop `Symbol-[:ASYNC_CALLS]` until PR-C; document if `trace_request_flow` is
HTTP-only two-hop in this PR.

### Out of scope (do NOT touch)

- `Producer` node, `DECLARES_PRODUCER`, `ASYNC_CALLS` endpoint flip.
- `find(kind="producer")`, `resolve(hint_kind="producer")`.
- Hints v3 / `EDGE_SCHEMA`-driven neighbor templates (PR-D).
- `GraphMeta` `producers_total` counters.

**Sentinel (must be zero on diff):**

```bash
git diff master..HEAD | grep -E 'MATCH \(s:Symbol\)-\[:HTTP_CALLS\]|symbol_id=member\.node_id|HttpCallRow\([^)]*symbol_id|FROM Producer TO Route|DECLARES_PRODUCER'
```

### Deliverables

See plan § PR-B. Headline:

1. `EDGE_SCHEMA["HTTP_CALLS"]` → `Client`/`Route`; regen `docs/EDGE-NAVIGATION.md`.
2. `build_ast_graph.py` — DDL, `HttpCallRow.client_id`, pass5/6, `_CREATE_HTTP_CALL`.
3. `kuzu_queries.py` — `RouteCaller`, `find_route_callers`, trace inbound HTTP, impact HTTP.
4. `pr_analysis.py` — two-hop HTTP.
5. MCP/doc/test updates per plan; **grep enumeration required** (below).

### Grep enumeration (mandatory in PR description)

```bash
grep -rn 'HTTP_CALLS\|ASYNC_CALLS' --include='*.py' --include='*.md' .
```

Account for **every** line: changed, unchanged with justification, or N/A.

### Tests (minimum set — add others if plan lists more)

- `test_neighbors_client_outbound_http_calls_returns_routes`
- `test_two_http_clients_on_one_method_produce_two_edges`
- `test_unresolved_client_has_no_http_calls_out_edge`
- `test_cross_service_http_trace_four_hop`
- `test_pr_analysis_finds_routes_via_declares_client`
- `test_find_route_callers_returns_route_caller_with_client_node_id`
- `test_trace_request_flow_inbound_includes_caller_node_id`
- `test_schema_ddl_endpoints_match_edge_schema_http_calls`

### Manual evidence

```bash
rm -rf /tmp/schema-v2-http && .venv/bin/python build_ast_graph.py \
  --source-root tests/fixtures/http_caller_smoke \
  --kuzu-path /tmp/schema-v2-http/code_graph.kuzu --verbose 2>/dev/null | tail -5
.venv/bin/python -c "
import kuzu
db=kuzu.Database('/tmp/schema-v2-http/code_graph.kuzu')
c=kuzu.Connection(db)
r=c.execute('MATCH (c:Client)-[e:HTTP_CALLS]->() RETURN count(e) AS n').get_as_df()
assert int(r['n'][0])>0, r
print('HTTP_CALLS from Client OK', r['n'][0])
"
.venv/bin/python -m pytest tests/test_call_edges_e2e.py tests/test_kuzu_queries.py tests/test_pr_analysis.py tests/test_brownfield_clients.py -v -k 'http or route_caller or declares_client' --tb=short
```

### PR title

`feat(schema): HTTP_CALLS originates from Client, not Symbol`

---

## PR-C — Producer node + `DECLARES_PRODUCER` + flip `ASYNC_CALLS`

**Branch:** `feat/schema-v2-producer-async-calls` off latest `master` (post PR-B)  
**Plan section:** [`plans/PLAN-SCHEMA-V2.md` § PR-C](./PLAN-SCHEMA-V2.md#pr-c--producer-node--declares_producer--flip-async_calls)  
**Propose:** [`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md) §6 PR-C

### Scope

Producer node, async edge flip, GraphMeta, MCP producer parity, describe rollups,
async doc sweep. Complete any deferred async two-hop from PR-B (`trace_request_flow`,
impact, `find_route_callers` async).

### Out of scope (do NOT touch)

- Hints v3 EDGE_SCHEMA templates (PR-D).
- `Consumer` node.
- Composed `DECLARES.HTTP_CALLS` / `DECLARES.ASYNC_CALLS` describe rollups.

**Sentinel (must be zero on diff):**

```bash
git diff master..HEAD | grep -E 'MATCH \(s:Symbol\)-\[:ASYNC_CALLS\]|AsyncCallRow\([^)]*symbol_id|FROM Symbol TO Route.*ASYNC_CALLS|CallerInfo'
```

### Deliverables

See plan § PR-C. Headline:

1. Producer DDL + pass5 materialization + `DECLARES_PRODUCER` + `ASYNC_CALLS` flip.
2. `EDGE_SCHEMA` entries 11; regen docs.
3. `GraphMeta` `producers_total`, `declares_producer_total`.
4. `mcp_v2` / `server` — `find`/`resolve` producer; `EdgeType` `DECLARES_PRODUCER`.
5. Describe rollups + `pr_analysis` async two-hop.
6. Async grep enumeration (same command as PR-B).

### Tests (minimum set)

- `test_neighbors_method_out_declares_producer_then_async_calls`
- `test_route_inbound_async_callers_are_producer_nodes`
- `test_two_producers_on_one_method_distinct_edges`
- `test_unresolved_producer_node_without_async_calls_out`
- `test_cross_service_async_trace_four_hop`
- `test_mixed_http_client_and_async_producer_same_method`
- `test_find_kind_producer_filter_by_producer_kind`
- `test_resolve_hint_kind_producer_status_none_suggests_find`
- `test_describe_type_includes_declares_declares_producer_rollup`
- `test_graph_meta_includes_producers_total`

### Manual evidence

```bash
rm -rf /tmp/schema-v2-full && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/schema-v2-full/code_graph.kuzu --verbose 2>/dev/null | tail -8
.venv/bin/java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/schema-v2-full 2>/dev/null | grep -E 'ontology_version|producers_total|clients_total'
.venv/bin/python -m pytest tests/test_call_edges_e2e.py tests/test_mcp_v2.py tests/test_kuzu_queries.py -v -k 'producer or async_calls or declares_producer' --tb=short
```

### PR title

`feat(schema): introduce Producer node and route ASYNC_CALLS through it`

---

## PR-D — hints v3 (blocked)

**Do not start** until [`propose/HINTS-V3-PROPOSE.md`](../propose/HINTS-V3-PROPOSE.md)
exists on the branch you branch from.

**Branch:** `feat/hints-v3-edge-schema` off latest `master` (post PR-C)  
**Plan section:** [`plans/PLAN-SCHEMA-V2.md` § PR-D](./PLAN-SCHEMA-V2.md#pr-d--hints-v3-edge_schema-driven-empty-result-hints)  
**Propose:** `propose/HINTS-V3-PROPOSE.md` (prerequisite)

### Scope

Implement hints v3 per **HINTS-V3-PROPOSE** (not SCHEMA-V2 propose). Consume
`EDGE_SCHEMA` / `typical_traversals`; replace generic wrong-subject neighbor hints.

### Out of scope (do NOT touch)

- Graph builder / DDL / `ONTOLOGY_VERSION`.
- `HttpCallRow` / `ProducerRow` / pass5/6 (unless propose explicitly requires).

**Sentinel (must be zero on diff):**

```bash
git diff master..HEAD | grep -E 'ONTOLOGY_VERSION|CREATE REL TABLE|HttpCallRow|ProducerRow|build_ast_graph'
```

### Deliverables

See HINTS-V3 propose + plan § PR-D when unblocked.

### Tests (from plan § PR-D)

- `test_hints_neighbors_wrong_subject_symbol_http_calls_suggests_declares_client`
- `test_hints_neighbors_wrong_subject_class_declares_client_suggests_declares_members`
- `test_hints_neighbors_wrong_direction_producer_async_calls`
- `test_hints_neighbors_fuzzy_strategy_layer_c_source_still_emits`
- `test_hints_neighbors_edge_schema_typical_traversal_in_hint`

### PR title

`feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`
