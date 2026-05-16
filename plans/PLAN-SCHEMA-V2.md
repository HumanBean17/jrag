# Plan: SCHEMA-V2 (edge navigation schema, HTTP/ASYNC caller-side flips, Producer node)

Status: **active (planning)**. This plan implements
[`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md).

Depends on:

- [`propose/HINTS-V3-PROPOSE.md`](../propose/HINTS-V3-PROPOSE.md) **merged to `master`** before **PR-A** implementation starts (SCHEMA-V2 Decision 30; GitHub PR may stay `draft`).
- [`docs/PROPOSES-ORDER.md`](../docs/PROPOSES-ORDER.md) for lock/merge sequence across proposes and code PRs.
- **PR-D (hints v3)** is specified in [`plans/PLAN-HINTS-V3.md`](./PLAN-HINTS-V3.md) — not repeated here beyond overview/tracking.

## Goal

- **PR-A:** Introduce `EDGE_SCHEMA` / `EdgeSpec` / `EdgeAttr` in `java_ontology.py` for all **10 current** edges (pre-flip endpoints); add `BROWNFIELD_RESOLVER_STRATEGY_SET`; generate `docs/EDGE-NAVIGATION.md` with CI `--check`; DDL↔schema invariant tests; bump `ONTOLOGY_VERSION` **13 → 14** and document re-index (no graph endpoint flips yet).
- **PR-B:** Flip `HTTP_CALLS` to `Client → Route` in schema, DDL, pass5/pass6, and every Python consumer; reshape `find_route_callers` / `trace_request_flow` / impact-analysis expansion to two-hop caller-side traversals (`RouteCaller` replaces `CallerInfo`); HTTP doc sweep.
- **PR-C:** Add `Producer` node + `DECLARES_PRODUCER`; flip `ASYNC_CALLS` to `Producer → Route`; `GraphMeta` producer counters; MCP `find`/`resolve` producer parity; type-level `describe` `DECLARES_PRODUCER` rollups; async doc sweep.
- After **PR-C**, agents can navigate caller-side HTTP/async without Symbol-bypass edges; **PR-D** (separate plan) restores empty-`neighbors` guidance.

## Principles (do not relitigate in review)

- **Edges connect the nodes whose data the edge is about.** Caller-side metadata lives on `Client` / `Producer`; traversals go `Symbol -[:DECLARES_*]-> caller_node -[:HTTP_CALLS|ASYNC_CALLS]-> Route`.
- **Single replacement, no dual edges.** No `HTTP_CALLS_LEGACY`, no Symbol→Route coexistence.
- **`EDGE_SCHEMA` in `java_ontology.py` is canonical** for endpoints, attrs metadata, `typical_traversals`, and `brownfield_resolver_sourced`. DDL strings in `build_ast_graph.py` are checked against it (src/dst only in CI v1 of invariant).
- **Generated docs, not hand-written.** `docs/EDGE-NAVIGATION.md` is produced by `scripts/generate_edge_navigation.py`; `--check` in CI.
- **Breaking API reshape is allowed.** `CallerInfo` → `RouteCaller`; MCP/query outputs return caller-side node ids. No back-compat aliases.
- **One ontology bump, one re-index.** v14 lands in PR-A; README + `docs/AGENT-GUIDE.md` updated in PR-A; graph built at v14 must be fully reprocessed after the sequence completes.
- **Hints v3 is not optional for the sequence.** HINTS-V3 propose on `master` before PR-A; PR-D locked propose before PR-D merges (see HINTS plan).
- **Brownfield composition unchanged** except caller-side edge anchoring: HTTP/ASYNC brownfield layers still compose per Tier-1B option-(b) replacement on methods.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-A | `EDGE_SCHEMA`, doc generator, CI invariants, `BROWNFIELD_RESOLVER_STRATEGY_SET`, v14 bump (pre-flip DDL) | **13 → 14** | `typical_traversals` map shape (hints PR-A contract); strategy-set union completeness; v13 refusal gate timing | `test_schema_consistency.py`, `test_edge_navigation_doc.py`, ontology gate | PR-B/C/D |
| PR-B | `HTTP_CALLS` flip + downstream Cypher/API + HTTP docs | Uses v14 (no second bump) | `grep` completeness for `HTTP_CALLS`; `HttpCallRow` key change; MCP/PR-analysis two-hop queries | `test_call_edges_e2e.py`, `test_kuzu_queries.py`, `test_pr_analysis.py`, brownfield HTTP | PR-C/D |
| PR-C | `Producer`, `DECLARES_PRODUCER`, `ASYNC_CALLS` flip, GraphMeta, MCP producer parity, describe rollups, async docs | Uses v14 | Producer field grounding vs `Client` copy-paste; pass5 materialization order; `find`/`resolve` kind union expansion | `test_call_edges_e2e.py`, `test_brownfield_clients.py`, `test_mcp_v2.py`, `test_client_node_extraction.py`, describe rollups | PR-D |
| PR-D | Hints v3 (empty `neighbors`) | **No** (query-time) | See [`plans/PLAN-HINTS-V3.md`](./PLAN-HINTS-V3.md) | `test_mcp_hints.py` HV* | PR-A/B/C |

**Landing order:** **PR-A → PR-B → PR-C → PR-D** (PR-D plan is separate; no parallel code PRs).

**Merge gates (artefacts):** This file + [`plans/CURSOR-PROMPTS-SCHEMA-V2.md`](./CURSOR-PROMPTS-SCHEMA-V2.md) must land before **PR-A** code merges (SCHEMA-V2 Decision 29).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `EDGE_SCHEMA` size in v2 | **11** entries after PR-C (`DECLARES_PRODUCER` added); PR-A ships **10** (no Producer table yet). |
| `typical_traversals` | **PR-A contract:** role-keyed `dict[str, str]` per HINTS-V3 §3.5 (`type_subject`, `member_subject`, `alien_subject`, …). SCHEMA-V2 propose Appendix A `tuple[str, …]` examples are **illustrative only** — implementation and hints v3 use the dict shape. |
| `member_only` on `EdgeSpec` | Hint-only (not in DDL CI). **PR-A:** `True` on `DECLARES_CLIENT`, `EXPOSES`, `OVERRIDES`, `CALLS` only (10-edge schema). **PR-C:** add `DECLARES_PRODUCER` to `EDGE_SCHEMA` and set `member_only=True` when that edge lands. If PR-A omits the field entirely, PR-D may add it. |
| `brownfield_resolver_sourced` | Renamed from `brownfield_sourced`; True iff edge carries resolver `strategy` ∈ `BROWNFIELD_RESOLVER_STRATEGY_SET`. |
| `BROWNFIELD_RESOLVER_STRATEGY_SET` | `FUZZY_STRATEGY_SET` ∪ non-fuzzy resolver strategies used on edges today (`codebase_route`, `codebase_client`, `codebase_producer`, `layer_*`, pass5/6 HTTP/async strategy literals — lock members in PR-A from `grep` of `strategy=` / ontology sets). |
| `HttpCallRow` / `AsyncCallRow` | PR-B: `client_id` replaces `symbol_id` on HTTP rows. PR-C: `producer_id` replaces `symbol_id` on async rows. |
| Producer `id` | `p:<stable_hash>` parallel to Client `c:<stable_hash>`; exact hash inputs locked in PR-C implementation (member + kind + topic + dispatch site). |
| `find_route_callers` | Returns `list[RouteCaller]`; two-hop Cypher; includes `declaring_symbol_id` back-ref. |
| Type-level `describe` | PR-C adds `DECLARES.DECLARES_PRODUCER` + `OVERRIDDEN_BY.DECLARES_PRODUCER`; **no** composed `DECLARES.HTTP_CALLS` rollups. |
| Consumer node | **Out of scope** (Decision 21). |
| `NODE_SCHEMA` / DDL codegen | Out of scope; manual DDL + CI check only. |

---

# PR-A — `EDGE_SCHEMA` + doc generator + ontology v14 (no flips)

## File-by-file changes

### 1. `java_ontology.py`

- Add `NodeKind`, `Cardinality`, `EdgeAttr`, `EdgeSpec` dataclasses.
- Add `EDGE_SCHEMA: dict[str, EdgeSpec]` populated for **10** edges with **current** endpoints:
  - `HTTP_CALLS`: `Symbol → Route` (pre-flip)
  - `ASYNC_CALLS`: `Symbol → Route` (pre-flip)
  - No `DECLARES_PRODUCER` entry yet (PR-C).
- Add `brownfield_resolver_sourced: bool` on `EdgeSpec`.
- Add `member_only: bool = False` on `EdgeSpec` (default False). **PR-A only:** set `True` on `DECLARES_CLIENT`, `EXPOSES`, `OVERRIDES`, `CALLS` (not `DECLARES_PRODUCER` — that edge does not exist until PR-C).
- Add `typical_traversals: dict[str, str]` per edge (role keys per HINTS-V3 §3.5 — **not** the tuple shape in SCHEMA propose Appendix A). Post-flip traversals for HTTP/ASYNC may describe target shape; update strings again in PR-B/C when endpoints flip.
- Add `BROWNFIELD_RESOLVER_STRATEGY_SET: frozenset[str]` (Decision 28).
- Export new symbols in `__all__`.

### 2. `build_ast_graph.py`

- **No endpoint flips** in PR-A.
- Optionally refactor existing `_SCHEMA_*` DDL constants to sit beside schema imports; ensure strings remain `Symbol→Route` for HTTP/ASYNC.
- No `Producer` node table.

### 3. `scripts/generate_edge_navigation.py` (new)

- Read `EDGE_SCHEMA`; write `docs/EDGE-NAVIGATION.md` (banner: generated — do not edit).
- CLI: default write; `--check` compares to committed file (nonzero exit on drift).
- Stable key ordering: `EDGE_SCHEMA` declaration order, not dict iteration order.

### 4. `docs/EDGE-NAVIGATION.md` (new, generated)

- Commit generator output.

### 5. `ast_java.py`

- `ONTOLOGY_VERSION = 14`.

### 6. `kuzu_queries.py`

- Keep version gate at `ast_java.ONTOLOGY_VERSION` (already imports it).
- Ensure error message mentions full reprocess when graph `<` required version.

### 7. `README.md`

- Update "Re-index required" current version to **14**.
- Add callout: v14 introduces `EDGE_SCHEMA`; PR-B flips `HTTP_CALLS`; PR-C adds `Producer` and flips `ASYNC_CALLS` (one full reprocess after upgrading).

### 8. `docs/AGENT-GUIDE.md`

- Bump ontology version sentence (line ~15 area).

### 9. `.github/workflows/test.yml` (if no existing hook)

- Add step: `.venv/bin/python scripts/generate_edge_navigation.py --check` (or document running via pytest that shells out — prefer explicit CI step).

### 10. `tests/test_schema_consistency.py` (new)

- Parse `CREATE REL TABLE` DDL in `build_ast_graph.py` for `(src_label, dst_label)` per edge.
- Assert `EDGE_SCHEMA[name].src/dst` matches DDL for every edge present in both.
- Assert `EDGE_SCHEMA` keys match the DDL edge set (10 edges in PR-A).

### 11. `tests/test_edge_navigation_doc.py` (new)

- Generator output matches committed `docs/EDGE-NAVIGATION.md`.
- `--check` fails when doc is stale (temp write + compare).

### 12. `tests/test_kuzu_queries.py`

- Extend or add stale-graph refusal test: graph with `ontology_version=13` refuses open when `ONTOLOGY_VERSION==14`.

## Tests for PR-A

1. `test_schema_consistency_all_ddl_endpoints_match_edge_schema`
2. `test_schema_consistency_http_calls_pre_flip_symbol_to_route`
3. `test_schema_consistency_async_calls_pre_flip_symbol_to_route`
4. `test_edge_navigation_doc_matches_generator_output`
5. `test_edge_navigation_doc_check_mode_detects_drift`
6. `test_kuzu_graph_refuses_ontology_version_below_required`
7. `test_edge_schema_member_only_flags_on_method_level_edges` — `DECLARES_CLIENT`, `EXPOSES`, `OVERRIDES`, `CALLS` True; no `DECLARES_PRODUCER` key in PR-A schema; `HTTP_CALLS`/`ASYNC_CALLS` False at pre-flip.

## Definition of done (PR-A)

- [ ] `EDGE_SCHEMA` + `BROWNFIELD_RESOLVER_STRATEGY_SET` in `java_ontology.py`.
- [ ] `ONTOLOGY_VERSION` is **14**; README + AGENT-GUIDE call out v14 + re-index.
- [ ] `docs/EDGE-NAVIGATION.md` generated; CI/check passes.
- [ ] DDL↔schema tests green; no HTTP/ASYNC endpoint flips.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green (no heavy gate).

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Dataclasses + `EDGE_SCHEMA` (10 edges, pre-flip) | `java_ontology.py` | Importable; member_only + traversals populated |
| 2 | `BROWNFIELD_RESOLVER_STRATEGY_SET` | `java_ontology.py` | Superset of `FUZZY_STRATEGY_SET` + resolver literals |
| 3 | Doc generator + committed doc | `scripts/…`, `docs/EDGE-NAVIGATION.md` | `--check` passes |
| 4 | Ontology bump + docs | `ast_java.py`, `README.md`, `docs/AGENT-GUIDE.md` | Version 14 documented |
| 5 | Consistency + doc tests | `tests/test_schema_consistency.py`, `tests/test_edge_navigation_doc.py`, `tests/test_kuzu_queries.py` | All PR-A tests pass |
| 6 | CI hook | `.github/workflows/test.yml` | Generator check runs on PRs |

---

# PR-B — flip `HTTP_CALLS` to `Client → Route` + downstream API

## File-by-file changes

### 1. `java_ontology.py`

- Update `EDGE_SCHEMA["HTTP_CALLS"]`: `src="Client"`, `dst="Route"`.
- Update `typical_traversals` for HTTP_CALLS to post-flip canonical strings.

### 2. `build_ast_graph.py`

- DDL: `HTTP_CALLS(FROM Client TO Route, …)`.
- `HttpCallRow`: rename `symbol_id` → `client_id` (or replace field — no compat).
- `pass5_imperative_edges` / `pass6_match_edges`: emit `HTTP_CALLS` from **Client** id, not Symbol.
- Ensure `DECLARES_CLIENT` emitted when Client rows materialized (if not already paired).

### 3. `kuzu_queries.py`

- Replace `CallerInfo` with `RouteCaller` dataclass:
  - `caller_node_id`, `caller_node_kind` (`Literal["client","producer"]` — producer branch wired in PR-C),
  - `caller_microservice`, `declaring_symbol_id`, `confidence`, `match`,
  - HTTP fields from Client node: `target_service`, `raw_uri` / path fields as applicable.
- `find_route_callers`: two-hop Cypher
  `MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route …)`.
- `trace_request_flow` inbound: two-hop via Client; output rows include `caller_node_id`, `caller_node_kind`, `declaring_symbol_id`, `declaring_symbol_fqn`.
- Impact-analysis expansion in `KuzuGraph` cross-service flow stage (Cypher using `HTTP_CALLS|ASYNC_CALLS` on changed symbols): three-hop through Client; surface caller node id on impacted route rows. Locate via `grep HTTP_CALLS|ASYNC_CALLS` in `kuzu_queries.py` — do not rely on line numbers.
- Remove all `Symbol-[HTTP_CALLS]->Route` patterns.

### 4. `pr_analysis.py`

- Route reachability query (~436): two-hop via `DECLARES_CLIENT` + `HTTP_CALLS` (keep `ASYNC_CALLS` direct until PR-C).

### 5. `mcp_v2.py`

- Update types wrapping `find_route_callers` / flow trace outputs.
- Any hard-coded neighbor examples in docstrings referencing Symbol→Route HTTP.

### 6. `server.py`

- Tool descriptions if they mention Symbol-level HTTP_CALLS.

### 7. `search_lancedb.py` / graph expansion (if any)

- `grep HTTP_CALLS` — update every Python match (PR description lists all paths).

### 8. Docs (HTTP sweep)

- `README.md`, `docs/AGENT-GUIDE.md`, `docs/skills/java-codebase-explore.md` — HTTP_CALLS traversal examples only.
- Regenerate `docs/EDGE-NAVIGATION.md` via script after schema change.

### 9. Tests / fixtures

- Update expectations in `tests/test_call_edges_e2e.py`, `tests/test_brownfield_clients.py`, `tests/test_pr_analysis.py`, `tests/test_mcp_v2.py`, `tests/test_mcp_v2_compose.py`, `tests/test_client_hint_recovery.py`, `tests/fixtures/**` only where HTTP caller shape requires it.

## Tests for PR-B

1. `test_call_edges_client_outbound_http_calls_returns_routes` — SCHEMA UC1
2. `test_call_edges_method_two_http_clients_two_routes` — UC5
3. `test_call_edges_cross_service_http_four_hop` — UC8 (DECLARES_CLIENT → HTTP_CALLS → EXPOSES)
4. `test_pr_analysis_changed_methods_finds_routes_via_declares_client` — UC9 HTTP leg
5. `test_find_route_callers_returns_route_caller_client_node` — `RouteCaller.caller_node_kind == "client"`
6. `test_trace_request_flow_inbound_includes_caller_node_id`
7. `test_schema_consistency_http_calls_post_flip_client_to_route`
8. `test_describe_client_edge_summary_includes_http_calls_out` — UC10

**PR-B PR description must include** output of:
`grep -rn 'HTTP_CALLS' --include='*.py' --include='*.md' .`
with every hit accounted for (fixed or justified).

## Definition of done (PR-B)

- [ ] No `Symbol-[HTTP_CALLS]->Route` in production Python.
- [ ] `CallerInfo` removed; `RouteCaller` used everywhere.
- [ ] Named tests pass; schema consistency test updated for Client→Route.
- [ ] HTTP doc sweep complete; EDGE-NAVIGATION regenerated.
- [ ] Default pytest + ruff green.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Schema + DDL flip | `java_ontology.py`, `build_ast_graph.py` | pass6 emits Client-anchored edges |
| 2 | Row type + pass5/6 | `build_ast_graph.py` | `HttpCallRow.client_id` |
| 3 | Query/API reshape | `kuzu_queries.py`, `pr_analysis.py` | Two-hop HTTP only |
| 4 | MCP surface | `mcp_v2.py`, `server.py` | Outputs match RouteCaller |
| 5 | Tests + fixtures | `tests/**` | PR-B tests green |
| 6 | Docs + regen | `README.md`, `docs/*`, `docs/EDGE-NAVIGATION.md` | Grep enumeration in PR body |

---

# PR-C — `Producer` node + `ASYNC_CALLS` flip + GraphMeta + MCP parity

## File-by-file changes

### 1. `java_ontology.py`

- Add `Producer` to `NodeKind`.
- Add `EDGE_SCHEMA["DECLARES_PRODUCER"]`, update `ASYNC_CALLS` to `Producer → Route`.
- Set `member_only=True` on `DECLARES_PRODUCER` (edge lands in this PR).
- `typical_traversals` for async edges/post-flip producer traversals.

### 2. `build_ast_graph.py`

- `CREATE NODE TABLE Producer(...)` per propose §3.2 (fields grounded in `AsyncProducerHint` / `AsyncCallRow`).
- `CREATE REL TABLE DECLARES_PRODUCER(FROM Symbol TO Producer, …)`.
- DDL: `ASYNC_CALLS(FROM Producer TO Route, …)`.
- `ProducerRow` dataclass + `tables.producer_rows`; materialize in `pass5_imperative_edges` alongside clients.
- `AsyncCallRow`: `producer_id` replaces `symbol_id`.
- pass6: emit async edges from Producer ids.
- `GraphMeta` / client_stats parallel: `producers_total`, `declares_producer_total` on node + DDL + insert payload.
- Regenerate meta JSON fields in `server.py` mapping if needed.

### 3. `kuzu_queries.py`

- `find_route_callers` / `trace_request_flow` / impact analysis: include `DECLARES_PRODUCER` + `ASYNC_CALLS` two-hop branch; `caller_node_kind="producer"` with `topic`/`broker` from node.
- `find` producers query helper (parallel `find_clients`).

### 4. `pr_analysis.py`

- Extend route reachability for changed symbols: async leg uses `DECLARES_PRODUCER` + `ASYNC_CALLS` two-hop (HTTP leg already two-hop from PR-B).

### 5. `mcp_v2.py`

- Extend `Literal` unions: `"producer"` on `find`, `resolve`, `_node_kind_from_id`, filters.
- `find_v2(kind="producer")`, `resolve(..., hint_kind="producer")` using `VALID_PRODUCER_KINDS`.
- `_load_node_record` for Producer.

### 6. `server.py`

- `GraphMetaOutput`: optional `producers_total` / `declares_producer_total` if exposed in meta tool (match `build_ast_graph` counters).

### 7. `kuzu_queries.py` — `describe` rollups (type-level `edge_summary` path)

- Add `("DECLARES.DECLARES_PRODUCER", "DECLARES_PRODUCER")` to type rollup set.
- Add `OVERRIDDEN_BY.DECLARES_PRODUCER` parallel to client axis.

### 8. Docs (async sweep)

- `README.md`, `docs/AGENT-GUIDE.md`, exploration skill — ASYNC_CALLS / Producer navigation.
- Regenerate `docs/EDGE-NAVIGATION.md` (11 edges).

### 9. Tests / fixtures

- `tests/test_call_edges_e2e.py`, `tests/test_brownfield_clients.py` (producer stubs), `tests/test_mcp_v2.py`, `tests/test_ast_graph_build.py`, `tests/test_client_node_extraction.py` (meta counters pattern).

## Tests for PR-C

1. `test_call_edges_declares_producer_then_async_calls_to_topic` — UC12
2. `test_call_edges_topic_inbound_async_calls_lists_producers` — UC13
3. `test_call_edges_method_two_producers_two_topics` — UC14
4. `test_call_edges_unresolved_producer_empty_async_out` — UC15
5. `test_call_edges_cross_service_async_four_hop` — UC16
6. `test_call_edges_method_mixed_http_client_and_async_producer` — UC18
7. `test_find_kind_producer_returns_producer_nodes`
8. `test_resolve_hint_kind_producer`
9. `test_describe_type_rollups_include_declares_producer`
10. `test_graph_meta_counts_producers_and_declares_producer`
11. `test_schema_consistency_async_calls_post_flip_producer_to_route`
12. `test_find_route_callers_includes_producer_callers` — async branch on `RouteCaller`

**PR-C PR description must include** grep for `ASYNC_CALLS` / `Producer` in `*.md` and remaining `*.py` hits.

## Definition of done (PR-C)

- [ ] `EDGE_SCHEMA` has **11** edges; Producer table live; ASYNC_CALLS from Producer.
- [ ] GraphMeta counters wired; MCP find/resolve producer parity.
- [ ] Type-level describe rollups include DECLARES_PRODUCER axis.
- [ ] Named tests pass; EDGE-NAVIGATION regenerated.
- [ ] Default pytest + ruff green.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Producer DDL + rows + DECLARES_PRODUCER | `build_ast_graph.py` | Producers in graph |
| 2 | ASYNC_CALLS flip + AsyncCallRow | `build_ast_graph.py`, `java_ontology.py` | pass6 from Producer |
| 3 | Queries + RouteCaller async | `kuzu_queries.py`, `pr_analysis.py` | Full two-hop async |
| 4 | MCP find/resolve + meta | `mcp_v2.py`, `server.py` | producer kind works |
| 5 | Describe rollups | `kuzu_queries.py` | DECLARES_PRODUCER rollups |
| 6 | Tests + docs | `tests/**`, `docs/**` | PR-C tests + async grep |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Missed `HTTP_CALLS`/`ASYNC_CALLS` Cypher site | High | PR-B/C grep enumeration in PR bodies; schema consistency tests |
| 2 | Agents need extra hop (UC8/UC16) | Medium | Document 4-hop traces; PR-D hints suggest canonical traversals |
| 3 | v14 index opened mid-sequence with mixed code | Medium | PR-A gate refuses v13; ship B+C quickly; README says full reprocess once at end |
| 4 | `typical_traversals` stale after flip | Medium | PR-B/C update schema + regen doc; HV19 in hints plan |
| 5 | Producer fields wrong vs async reality | Medium | PR-C review against `AsyncProducerHint`; no HTTP-only fields |
| 6 | `RouteCaller` consumers missed | Medium | `grep CallerInfo` sentinel in PR-B |
| 7 | PLAN/prompts not landed before code | Medium | Decision 29 — artefact PR first |

# Out of scope

- `Consumer` node, `NODE_SCHEMA`, DDL codegen from `EDGE_SCHEMA`.
- Multi-target Client/Producer nodes; materialized composite edges.
- Hints v3 implementation (PR-D — [`plans/PLAN-HINTS-V3.md`](./PLAN-HINTS-V3.md)).
- Ontology **15** or second re-index.
- Ranking / incremental rebuild proposes (`RANKING-MICROSERVICE`, `TIER2-INCREMENTAL-REBUILD`).
- Special-casing `tests/bank-chat-system/` in production code.

# Whole-plan done definition

1. Graph at ontology **14** with `HTTP_CALLS: Client→Route`, `ASYNC_CALLS: Producer→Route`, `DECLARES_PRODUCER` populated.
2. `EDGE_SCHEMA`, generated `docs/EDGE-NAVIGATION.md`, and DDL pass CI invariants.
3. `find_route_callers` / `trace_request_flow` / PR-analysis / impact analysis use caller-side two-hop traversals.
4. MCP `find`/`resolve` support `producer`; type-level `describe` exposes DECLARES_PRODUCER rollups.
5. PR-D (hints v3) landed per HINTS plan — empty wrong-kind `neighbors` queries are guided.
6. `propose/SCHEMA-V2-PROPOSE.md` moved to `propose/completed/` when **PR-D** merges (whole user-visible effort done).

# Tracking

- Artefacts (`PLAN-SCHEMA-V2`, `CURSOR-PROMPTS-SCHEMA-V2`, `PLAN-HINTS-V3`, `CURSOR-PROMPTS-HINTS-V3`): _pending_
- `PR-A`: _pending_
- `PR-B`: _pending_
- `PR-C`: _pending_
- `PR-D`: _see PLAN-HINTS-V3_

## Cursor handoff

[`plans/CURSOR-PROMPTS-SCHEMA-V2.md`](./CURSOR-PROMPTS-SCHEMA-V2.md) — PR-A/B/C only.

[`plans/CURSOR-PROMPTS-HINTS-V3.md`](./CURSOR-PROMPTS-HINTS-V3.md) — PR-D after PR-C on `master`.
