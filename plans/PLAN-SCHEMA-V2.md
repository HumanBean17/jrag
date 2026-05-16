# Plan: SCHEMA-V2 (edge navigation schema + caller-side nodes)

Status: **active (planning)**. This plan implements
[`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md).

Depends on: **none** for PR-A–C. **PR-D** depends on
[`propose/HINTS-V3-PROPOSE.md`](../propose/HINTS-V3-PROPOSE.md) existing as a
draft in the same review cycle (Decision 30 — file not present yet; do not merge
PR-D until it lands).

## Goal

- **Navigation principle enforced in the graph:** `HTTP_CALLS` and `ASYNC_CALLS`
  connect the nodes whose data they carry (`Client → Route`, `Producer → Route`),
  not declaring `Symbol` nodes.
- **Single schema source of truth:** `EDGE_SCHEMA` in `java_ontology.py` drives
  DDL↔ontology CI checks, generated `docs/EDGE-NAVIGATION.md`, and (PR-D) hints v3.
- **Downstream APIs match traversals:** `find_route_callers`, `trace_request_flow`,
  impact-analysis expansion, `pr_analysis`, and MCP wrappers return **caller-side**
  nodes (`RouteCaller`), not declaring symbols.
- **Producer parity:** new `Producer` node + `DECLARES_PRODUCER`, MCP
  `find(kind="producer")` / `resolve(hint_kind="producer")`, GraphMeta counters,
  type-level `describe` rollups.
- **Hard re-index:** `ONTOLOGY_VERSION` **13 → 14** in PR-A; full rebuild required;
  v13 graphs refuse to mount after PR-B/C ship.

## Principles (do not relitigate in review)

- **Edges connect the nodes the edge is about.** Declarer linkage is always
  `DECLARES_*` from method `Symbol` → caller-side node.
- **Replace, never dual-edge.** No `HTTP_CALLS_LEGACY`, no Symbol→Route coexistence.
- **No back-compat aliases.** `CallerInfo` is removed; `RouteCaller` is the only
  caller shape. Per `.cursor/rules/breaking-changes.mdc`.
- **PR-A ships infra without endpoint flips.** `EDGE_SCHEMA` in PR-A reflects the
  **pre-flip** graph (10 rel types; `HTTP_CALLS` / `ASYNC_CALLS` still
  `Symbol → Route`). PR-B/C update schema entries when endpoints change.
- **`brownfield_resolver_sourced`** (not `brownfield_sourced`): True iff edge carries
  `strategy ∈ BROWNFIELD_RESOLVER_STRATEGY_SET` (union of `FUZZY_STRATEGY_SET` and
  primary/annotation resolver strategies — Decision 28).
- **No `Consumer` node.** Callee side stays `EXPOSES(Symbol → Route)` (Decision 21).
- **No composed `DECLARES.HTTP_CALLS` rollups.** Type-level signal is
  `DECLARES.DECLARES_CLIENT` / `DECLARES.DECLARES_PRODUCER` only (Decision 25).
- **Hints v3 is mandatory before v2 graph ships to agents without guidance.** PR-D
  is blocked on `HINTS-V3-PROPOSE.md`; do not merge PR-A–C to `master` without a
  tracked plan to land PR-D in the same cycle (Decision 30).
- **Grep-enumeration contract.** PR-B and PR-C PR descriptions must paste output of:
  `grep -rn 'HTTP_CALLS\|ASYNC_CALLS' --include='*.py' --include='*.md' .`
  with every match accounted for (updated or explicitly N/A).

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-A | `EDGE_SCHEMA`, doc generator, DDL↔schema CI, `BROWNFIELD_RESOLVER_STRATEGY_SET`, v14 bump (no graph shape change) | **13 → 14** (version only; shape unchanged until B/C) | Dataclass/API design in `java_ontology.py`; generator determinism; stale-index gate wording | `test_schema_consistency`, `test_edge_navigation_doc`, ontology gate | B, C, D |
| PR-B | Flip `HTTP_CALLS` to `Client → Route`; pass5/6 + writers; HTTP downstream queries + docs | **Re-index required** (shape) | `pass6` member lookup via `Client.member_id`; `HttpCallRow` keying; Cypher grep completeness | `test_call_edges_e2e` UC1/5–8/9, `test_kuzu_queries` RouteCaller, `test_pr_analysis`, brownfield HTTP | C, D |
| PR-C | `Producer` node, `DECLARES_PRODUCER`, flip `ASYNC_CALLS`; GraphMeta; MCP producer parity; async docs + describe rollups | **Re-index required** (shape) | Producer id synthesis vs multi-`@CodebaseProducer`; async pass5 materialization (today only HTTP gets `Client` rows); `EdgeType` / `find` / `resolve` surface | `test_call_edges_e2e` UC12–18, `test_mcp_v2` producer, describe rollups, GraphMeta | D |
| PR-D | Hints v3 driven by `EDGE_SCHEMA` | **No** (query-time) | Template churn; wrong-subject-kind vs fuzzy hint priority; import cycle `mcp_hints` ↔ `java_ontology` | `test_mcp_hints` UC2/3/10/15/17/22 + fuzzy regression | **Blocked:** `HINTS-V3-PROPOSE.md` |

**Landing order:** **A → B → C → D**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `EDGE_SCHEMA` home | `java_ontology.py` (11 entries after PR-C; 10 in PR-A) |
| `HTTP_CALLS` endpoints | `Client → Route` (PR-B) |
| `ASYNC_CALLS` endpoints | `Producer → Route` (PR-C) |
| Producer `id` prefix | `p:` + SHA1 of `microservice\|member_fqn\|producer_kind\|topic` (parallel `c:`) |
| `HttpCallRow` / `AsyncCallRow` keys | `client_id` / `producer_id` (replace `symbol_id`) |
| Caller API type | `RouteCaller` replaces `CallerInfo` |
| `find_route_callers` query | `(s)-[:DECLARES_CLIENT\|DECLARES_PRODUCER]->(n)-[e:HTTP_CALLS\|ASYNC_CALLS]->(r)` |
| Impact trace HTTP branch | Three-hop via `DECLARES_CLIENT\|DECLARES_PRODUCER` (PR-B HTTP; PR-C completes async) |
| Type describe rollups | Add `DECLARES.DECLARES_PRODUCER`, `OVERRIDDEN_BY.DECLARES_PRODUCER` (PR-C) |
| MCP `find` / `resolve` | `kind="producer"`, `hint_kind="producer"` using `VALID_PRODUCER_KINDS` (PR-C) |
| Doc generator | `scripts/generate_edge_navigation.py`; committed `docs/EDGE-NAVIGATION.md`; `--check` in CI |
| DDL invariant | Compare parsed `(FROM, TO)` only — attribute drift does not fail CI |
| Exploration skill | `docs/skills/java-codebase-explore.md` — HTTP sweep PR-B, async PR-C |

---

# PR-A — `EDGE_SCHEMA` + doc generator + ontology v14 (no DDL flips)

## File-by-file changes

### 1. `java_ontology.py`

- Add `NodeKind`, `Cardinality`, `EdgeAttr`, `EdgeSpec` dataclasses (frozen) per propose §3.1.
- Add `EDGE_SCHEMA: dict[str, EdgeSpec]` with **10 entries** matching **current** DDL:
  `EXTENDS`, `IMPLEMENTS`, `INJECTS`, `DECLARES`, `OVERRIDES`, `CALLS`, `EXPOSES`,
  `DECLARES_CLIENT`, `HTTP_CALLS` (`Symbol → Route`), `ASYNC_CALLS` (`Symbol → Route`).
  Populate `attrs` from existing DDL columns in `build_ast_graph.py` (`_SCHEMA_*`).
  Fill `purpose` and `typical_traversals` per propose Appendix A (pre-flip wording for
  HTTP/ASYNC).
- Add `BROWNFIELD_RESOLVER_STRATEGY_SET: frozenset[str]` = union of
  `FUZZY_STRATEGY_SET`, `VALID_HTTP_CALL_STRATEGIES`, `VALID_ASYNC_CALL_STRATEGIES`,
  brownfield layer literals (`layer_*`), and codebase resolver literals
  (`codebase_client`, `codebase_producer`, `codebase_route`, …) — **define once here**,
  no scattered strategy strings in `mcp_hints.py` for PR-D.
- Export new symbols in `__all__`.
- `NodeKind` in PR-A: `Symbol`, `Route`, `Client` only (add `Producer` in PR-C).

### 2. `scripts/generate_edge_navigation.py` (new)

- Read `EDGE_SCHEMA`; write `docs/EDGE-NAVIGATION.md` with banner
  `generated from java_ontology.EDGE_SCHEMA — do not edit`.
- Emit summary table + per-edge sections (`Endpoints`, `Attributes`, `Purpose`,
  `Typical traversals`) in `EDGE_SCHEMA` declaration order (not dict iteration).
- CLI: default write; `--check` compares committed file and exits **1** on drift.

### 3. `docs/EDGE-NAVIGATION.md` (new, generated)

- Commit generator output (PR-A initial version reflects pre-flip schema).

### 4. `build_ast_graph.py`

- **No endpoint changes.** Add a module-level tuple
  `_EDGE_DDL_FOR_SCHEMA_CHECK: tuple[str, ...]` listing the ten `_SCHEMA_*` rel DDL
  strings (or import-friendly constants) so tests can parse them without reaching into
  private layout — keep DDL strings the single write site.

### 5. `ast_java.py`

- Bump `ONTOLOGY_VERSION` **13 → 14** with comment:
  `SCHEMA-V2: EDGE_SCHEMA + caller-side navigation (HTTP/ASYNC endpoint flips in follow-up PRs)`.

### 6. `README.md`

- Update **Re-index required** block: current version **14**; note PR-A bumps version only;
  **full rebuild mandatory after PR-B and PR-C** (graph shape). One sentence placeholder
  per follow-up PR (HTTP flip / Producer+ASYNC flip).

### 7. `docs/AGENT-GUIDE.md`

- Bump ontology version sentence (§ front matter / graph assumptions) to **14**.

### 8. `kuzu_queries.py`

- No behaviour change. Ensure stale-graph error message references `_ONTOLOGY_VERSION`
  (14 after bump). Comment that v13 indexes are unsupported post–SCHEMA-V2 rollout.

### 9. `.github/workflows/test.yml` (optional if pytest covers `--check`)

- If not covered by pytest alone: add step
  `.venv/bin/python scripts/generate_edge_navigation.py --check` after tests.
  Prefer pytest wrapper in `test_edge_navigation_doc.py` to avoid workflow-only drift.

### 10. `tests/test_schema_consistency.py` (new)

- Helper `_parse_rel_endpoints(ddl: str) -> tuple[str, str]` via regex
  `FROM (\w+) TO (\w+)`.
- One test per rel table: `test_schema_ddl_endpoints_match_edge_schema_<EDGE_NAME>`.
- Parametrize over `EDGE_SCHEMA` keys that have DDL in `build_ast_graph.py`.

### 11. `tests/test_edge_navigation_doc.py` (new)

- `test_edge_navigation_doc_matches_generator_output`
- `test_generate_edge_navigation_check_detects_drift` (mutate temp copy or use
  subprocess `--check` against a stale buffer)

### 12. `tests/test_kuzu_queries.py`

- Update `test_kuzu_graph_get_raises_when_graph_ontology_too_old` to use
  `ONTOLOGY_VERSION - 1` (still valid after bump to 14).

## Tests for PR-A

1. `test_schema_ddl_endpoints_match_edge_schema_extends` (and siblings for all 10 edges)
2. `test_edge_navigation_doc_matches_generator_output`
3. `test_generate_edge_navigation_check_detects_drift`
4. `test_kuzu_graph_get_raises_when_graph_ontology_too_old` (existing; assert v14 gate)

## Definition of done (PR-A)

- [ ] `EDGE_SCHEMA` has 10 entries; DDL `(src,dst)` matches for each.
- [ ] `docs/EDGE-NAVIGATION.md` generated; hand-edit fails `--check`.
- [ ] `ONTOLOGY_VERSION == 14`; README + AGENT-GUIDE mention v14.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [ ] Manual: ` .venv/bin/python scripts/generate_edge_navigation.py --check`

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add dataclasses + `EDGE_SCHEMA` (10 edges, current endpoints) | `java_ontology.py` | Import succeeds; attrs match DDL |
| 2 | Add `BROWNFIELD_RESOLVER_STRATEGY_SET` | `java_ontology.py` | Superset of fuzzy + resolver strategies |
| 3 | Implement generator + commit doc | `scripts/…`, `docs/EDGE-NAVIGATION.md` | `--check` passes |
| 4 | Expose DDL tuple for tests | `build_ast_graph.py` | Tests can import/compare |
| 5 | Bump ontology version + README/AGENT-GUIDE | `ast_java.py`, docs | v14 documented |
| 6 | Add consistency + doc tests | `tests/test_schema_*.py` | pytest green |

---

# PR-B — flip `HTTP_CALLS` to `Client → Route`

## File-by-file changes

### 1. `java_ontology.py`

- Update `EDGE_SCHEMA["HTTP_CALLS"]`: `src="Client"`, `dst="Route"`, `typical_traversals`
  per propose Appendix A (post-flip).
- Regenerate `docs/EDGE-NAVIGATION.md` (`python scripts/generate_edge_navigation.py`).

### 2. `build_ast_graph.py`

- `_SCHEMA_HTTP_CALLS`: `FROM Client TO Route`.
- `_CREATE_HTTP_CALL`: `MATCH (c:Client {id: $cid}), (r:Route {id: $rid}) CREATE (c)-[:HTTP_CALLS …]`.
- `HttpCallRow`: rename `symbol_id` → `client_id`.
- `pass5_imperative_edges`: append `HttpCallRow` with `client_id=cid` (not `member.node_id`).
  Dedupe keys `(client_id, route_id)` instead of `(member.node_id, route_id)`.
- `pass6_match_edges`: resolve `member` via `clients_by_id[row.client_id].member_id`
  (and feign hint loop via `client_hints_by_member` unchanged logic).
- `_write_graph`: pass `cid` to `_CREATE_HTTP_CALL`.
- README re-index bullet: “PR-B: `HTTP_CALLS` originates from `Client`.”

### 3. `kuzu_queries.py`

- Remove `CallerInfo`; add `RouteCaller` dataclass:
  - `caller_node_id: str`
  - `caller_node_kind: Literal["client", "producer"]` (producer unused until PR-C but type exists)
  - `caller_microservice: str`
  - `declaring_symbol_id: str`
  - `confidence: float`
  - `match: str`
  - HTTP-only optional fields on client branch: `target_service`, `client_kind`, `method`, `path`
    (populate from `Client` node in query RETURN).
- `find_route_callers`: two-hop HTTP-only query:
  ```cypher
  MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid})
  RETURN c.id, c.microservice, c.target_service, c.client_kind, c.method, c.path,
         s.id AS declaring_symbol_id, e.confidence, e.match
  ```
  Map to `RouteCaller(caller_node_kind="client", ...)`.
- `trace_request_flow` **inbound**: replace one-hop Symbol←Route with:
  ```cypher
  MATCH (entry:Route {id: $rid})<-[e:HTTP_CALLS]-(c:Client)<-[:DECLARES_CLIENT]-(caller:Symbol)
  OPTIONAL MATCH (origin:Symbol)-[:CALLS*0..{hops}]->(caller)
  RETURN c.id AS caller_node_id, 'client' AS caller_node_kind,
         caller.id AS declaring_symbol_id, caller.fqn AS declaring_symbol_fqn, ...
  ```
  (Keep async branch on Symbol←Route until PR-C, or stub empty — **prefer leaving async
  one-hop only in PR-B** and completing in PR-C to avoid half-migrated trace output;
  document in PR-B description if trace stays HTTP-only two-hop until PR-C.)
- Impact-analysis expansion (`~1335`): HTTP branch three-hop:
  `(root)-[:DECLARES]->(m1)-[:DECLARES_CLIENT]->(c)-[e:HTTP_CALLS]->(rt)`.
  Return `caller_node_id` / `caller_node_kind` in stage metadata if surfaced (extend
  `_ingest_flow_row` payload as needed).
- Update `__all__` exports.

### 4. `pr_analysis.py`

- Route reachability query (~436): two-hop HTTP (+ keep async one-hop until PR-C):
  ```cypher
  MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid})
  ```

### 5. `mcp_v2.py` / `server.py`

- Any typed wrappers exposing `find_route_callers` / trace shapes: use `RouteCaller` fields
  in JSON payloads if exposed beyond `kuzu_queries`.
- Tool `description=` strings: HTTP caller navigation mentions `Client` + `DECLARES_CLIENT`
  (not `Symbol`→`HTTP_CALLS`).

### 6. Docs (HTTP sweep only)

- `README.md` — architecture / graph edge bullets for `HTTP_CALLS`.
- `docs/AGENT-GUIDE.md` — edge table row “Cross-service”, traversal recipes, `describe`
  notes for method-level `HTTP_CALLS` (now wrong direct edge — point to PR-D hint or interim
  “use DECLARES_CLIENT”).
- `docs/skills/java-codebase-explore.md` — caller-side HTTP patterns (4-hop trace).

### 7. Tests

- `tests/test_call_edges_e2e.py` — update Cypher assumptions; add/rename:
- `tests/test_brownfield_clients.py` — client-anchored edges.
- `tests/test_pr_analysis.py` — two-hop HTTP.
- `tests/test_kuzu_queries.py` — `RouteCaller` shape tests.
- `tests/test_mcp_v2.py`, `tests/test_mcp_v2_compose.py` — if they assert caller_symbol_id.
- `tests/test_client_hint_recovery.py` — `test_find_route_callers_*` expectations.

## Tests for PR-B

1. `test_neighbors_client_outbound_http_calls_returns_routes` — UC1
2. `test_two_http_clients_on_one_method_produce_two_edges` — UC5
3. `test_unresolved_client_has_no_http_calls_out_edge` — UC6
4. `test_cross_service_http_trace_four_hop` — UC8
5. `test_pr_analysis_finds_routes_via_declares_client` — UC9 (HTTP)
6. `test_find_route_callers_returns_route_caller_with_client_node_id`
7. `test_trace_request_flow_inbound_includes_caller_node_id` — UC23 partial
8. `test_schema_ddl_endpoints_match_edge_schema_http_calls` — post-flip Client→Route

## Definition of done (PR-B)

- [ ] No production `MATCH (s:Symbol)-[:HTTP_CALLS]` remain (grep contract).
- [ ] `EDGE_SCHEMA["HTTP_CALLS"].src == "Client"`; doc regenerated.
- [ ] E2E rebuild: `build_ast_graph.py` on `tests/fixtures/http_caller_smoke` shows
  `HTTP_CALLS` from `c:` ids.
- [ ] pytest green; PR description includes full grep enumeration.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Flip DDL + `EDGE_SCHEMA` + regen doc | `build_ast_graph.py`, `java_ontology.py`, `docs/…` | schema test passes |
| 2 | `HttpCallRow.client_id` + pass5/6 + writer | `build_ast_graph.py` | e2e HTTP edges from clients |
| 3 | `RouteCaller` + `find_route_callers` | `kuzu_queries.py` | feign caller test passes |
| 4 | Trace + impact + pr_analysis | `kuzu_queries.py`, `pr_analysis.py` | grep clean |
| 5 | MCP/docs/tests | `mcp_v2.py`, `server.py`, `tests/*`, `docs/*` | pytest green |

---

# PR-C — `Producer` node + `DECLARES_PRODUCER` + flip `ASYNC_CALLS`

## File-by-file changes

### 1. `java_ontology.py`

- Extend `NodeKind` with `Producer`.
- Add `EDGE_SCHEMA["DECLARES_PRODUCER"]` (`Symbol → Producer`).
- Update `EDGE_SCHEMA["ASYNC_CALLS"]`: `src="Producer"`, `dst="Route"`.
- Regenerate `docs/EDGE-NAVIGATION.md`.

### 2. `build_ast_graph.py`

- `_SCHEMA_PRODUCER` node table per propose §3.2 (fields grounded in `AsyncProducerHint` /
  `AsyncCallRow` — **no** HTTP-only columns).
- `_SCHEMA_DECLARES_PRODUCER`, `_SCHEMA_ASYNC_CALLS` (`FROM Producer TO Route`).
- `_drop_all` / `_create_schema`: include Producer + new rel; drop order: rels before
  `Producer` node.
- Dataclasses: `ProducerRow`, `DeclaresProducerRow`, `ProducerExtractionStats` (or extend
  `ClientExtractionStats` pattern with `producer_stats` on `GraphTables`).
- `_producer_id(microservice, member_fqn, producer_kind, topic) -> p:…` parallel `_client_id`.
- `pass5_imperative_edges`: for `call.channel == "async"`, materialize `ProducerRow` +
  `DeclaresProducerRow` (mirror HTTP client block); `AsyncCallRow(producer_id=pid, ...)`.
- `pass6_match_edges`: `producer_hints_by_member` analogous to clients; member via
  `producers_by_id[row.producer_id].member_id`.
- `_CREATE_PRODUCER`, `_CREATE_DECLARES_PRODUCER`, `_CREATE_ASYNC_CALL` (Producer→Route).
- `GraphMeta` DDL + `_write_meta`: `producers_total`, `declares_producer_total` (propose §3.6).
- `counts_json` keys: `producers`, `declares_producer`.

### 3. `kuzu_queries.py`

- `find_route_callers`: add async branch to same `RouteCaller` list (producer fields:
  `topic`, `broker`, `producer_kind`).
- `trace_request_flow` inbound: async two-hop via `DECLARES_PRODUCER` (complete UC23).
- Impact-analysis: async three-hop via `DECLARES_PRODUCER`.
- `list_clients` pattern → add `list_producers` / query helper if needed for MCP find.
- `member_edge_rollup_for`: add `("DECLARES.DECLARES_PRODUCER", "DECLARES_PRODUCER")`.
- `override_axis_rollup_for`: add `OVERRIDDEN_BY.DECLARES_PRODUCER` parallel client.

### 4. `mcp_v2.py`

- `EdgeType`: add `"DECLARES_PRODUCER"`.
- `find_v2`: `kind: Literal[..., "producer"]`; producer Cypher + `NodeFilter` fields
  (`producer_kind`, `topic` — mirror client filter naming in ontology).
- `resolve_v2`: `hint_kind="producer"` branch + `_PRODUCER_RESOLVE_RETURN`.
- `_node_kind_from_id` / `_resolve_node_kind`: `p:` / `producer:` prefixes.
- `NodeRef` / describe payloads: include producer kind in `data`.

### 5. `server.py`

- Extend `find` / `resolve` tool `Literal` kinds and descriptions for `producer`.

### 6. `mcp_hints.py` (minimal for PR-C)

- Extend resolve/find template branches for `hint_kind="producer"` / `kind="producer"`
  (locked strings; full EDGE_SCHEMA hints remain PR-D).

### 7. `pr_analysis.py`

- Complete UC9 async arm: `DECLARES_PRODUCER` → `ASYNC_CALLS`.

### 8. Docs (async sweep)

- `README.md`, `docs/AGENT-GUIDE.md`, `docs/skills/java-codebase-explore.md` — async /
  `ASYNC_CALLS` / Producer navigation (grep contract).

### 9. Tests

- Extend `tests/fixtures/brownfield_client_stubs` or add producer stub fixture if bank
  corpus lacks multi-producer cases.
- `tests/test_call_edges_e2e.py`, `tests/test_mcp_v2.py`, `tests/test_kuzu_queries.py`,
  `tests/test_ast_graph_build.py` (meta counters), brownfield producer tests (mirror
  `test_brownfield_clients.py` patterns).

## Tests for PR-C

1. `test_neighbors_method_out_declares_producer_then_async_calls` — UC12
2. `test_route_inbound_async_callers_are_producer_nodes` — UC13
3. `test_two_producers_on_one_method_distinct_edges` — UC14
4. `test_unresolved_producer_node_without_async_calls_out` — UC15
5. `test_cross_service_async_trace_four_hop` — UC16
6. `test_mixed_http_client_and_async_producer_same_method` — UC18
7. `test_find_kind_producer_filter_by_producer_kind`
8. `test_resolve_hint_kind_producer_status_none_suggests_find`
9. `test_describe_type_includes_declares_declares_producer_rollup`
10. `test_graph_meta_includes_producers_total`
11. `test_schema_ddl_endpoints_match_edge_schema_declares_producer`
12. `test_schema_ddl_endpoints_match_edge_schema_async_calls`

## Definition of done (PR-C)

- [ ] 11 `EDGE_SCHEMA` entries; Producer node populated on rebuild.
- [ ] No `MATCH (s:Symbol)-[:ASYNC_CALLS]` in production code (grep contract).
- [ ] `find(kind="producer")` and `resolve(hint_kind="producer")` work on fixture.
- [ ] `meta` reports `producers_total` / `declares_producer_total`.
- [ ] pytest green; manual meta check on bank-chat-system rebuild.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Producer DDL + rows + pass5 materialization | `build_ast_graph.py` | producer nodes in graph |
| 2 | Flip ASYNC_CALLS + pass6 + writers | `build_ast_graph.py` | e2e async from `p:` |
| 3 | GraphMeta counters | `build_ast_graph.py`, meta readers | CLI `meta` shows totals |
| 4 | Query + rollup + pr_analysis | `kuzu_queries.py`, `pr_analysis.py` | grep clean |
| 5 | MCP find/resolve + docs + tests | `mcp_v2.py`, `server.py`, `tests/*`, `docs/*` | pytest green |

---

# PR-D — hints v3 (`EDGE_SCHEMA`-driven empty-result hints)

**Blocked** until [`propose/HINTS-V3-PROPOSE.md`](../propose/HINTS-V3-PROPOSE.md) exists.
Implement per that propose; summary here is contractual only.

## File-by-file changes (expected)

### 1. `propose/HINTS-V3-PROPOSE.md` (prerequisite, separate PR)

- Kind/direction-aware `neighbors` empty-result templates reading `EDGE_SCHEMA` +
  `typical_traversals`; supersede generic `TPL_NEIGHBORS_EMPTY_KIND_CHECK` for
  wrong-subject cases (UC2, UC3, UC17, UC22).

### 2. `mcp_hints.py`

- Import `EDGE_SCHEMA`, `NodeKind` from `java_ontology.py` (no hardcoded edge shapes).
- New template family + wiring in `generate_hints("neighbors", …)` using subject kind
  from payload (`symbol` | `route` | `client` | `producer`).
- Preserve fuzzy-strategy hint (v2 regression).

### 3. `mcp_v2.py`

- Pass subject node kind into neighbors hint payload (from `describe` / id prefix / graph).

### 4. `tests/test_mcp_hints.py`

- Named scenarios for UC2, UC3, UC10, UC15, UC17, UC22 + fuzzy regression.

## Tests for PR-D

1. `test_hints_neighbors_wrong_subject_symbol_http_calls_suggests_declares_client` — UC3
2. `test_hints_neighbors_wrong_subject_class_declares_client_suggests_declares_members` — UC2
3. `test_hints_neighbors_wrong_direction_producer_async_calls` — UC17
4. `test_hints_neighbors_client_edge_summary_nonzero_out` — UC10 (payload-only or e2e)
5. `test_hints_neighbors_fuzzy_strategy_layer_c_source_still_emits` — v2 regression
6. `test_hints_neighbors_edge_schema_typical_traversal_in_hint` — UC22

## Definition of done (PR-D)

- [ ] `HINTS-V3-PROPOSE.md` landed; PR references it.
- [ ] Wrong-subject `HTTP_CALLS` / `ASYNC_CALLS` queries emit actionable hints.
- [ ] No hardcoded `Symbol → Route` strings in `mcp_hints.py` for edge shape.
- [ ] pytest green.

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Missed `Symbol-[:HTTP_CALLS\|ASYNC_CALLS]` Cypher site | High | Mandatory grep paste in PR-B/C descriptions; code review checklist |
| 2 | `pass6` breaks feign recovery when `HttpCallRow` loses `symbol_id` | High | Reuse `Client.member_id`; keep `client_hints_by_member`; test UC6/7 on smoke fixture |
| 3 | PR-A version bump without shape change confuses operators | Medium | README explicitly: rebuild required after B+C, not after A alone |
| 4 | Trace/impact half-migrated in PR-B (async still one-hop) | Medium | Document in PR-B; complete in PR-C; add integration test in PR-C |
| 5 | Producer field copy-paste from Client adds wrong columns | Medium | PR-C review against `AsyncProducerHint` only (Decision 21/§3.2 table) |
| 6 | v2 ships without PR-D → silent `[]` on method `HTTP_CALLS` | High | Decision 30 gate; do not tag release until D merges |
| 7 | Doc/skill drift | Low | Regenerate `EDGE-NAVIGATION.md` each schema PR; exploration skill in B/C grep |
| 8 | DDL↔schema CI noise on attribute edits | Low | Invariant is endpoints-only; document in test module docstring |

# Out of scope

- `NODE_SCHEMA`, `Consumer` node, `@CodebaseConsumer` stub support.
- Materialized composite edges (`SYMBOL_HTTP_CALLS`).
- Multi-target single Client/Producer node.
- Codegen of Kuzu DDL from `EDGE_SCHEMA` (manual DDL + CI check only).
- `HINTS-V3` design detail (lives in separate propose).
- Ontology version **15** or backward compatibility for v13 graphs.
- Changes to `tests/bank-chat-system/` Java sources except via dedicated fixture stubs.
- Issue #147-style CI grep for every `strategy=` literal (optional chore).

# Whole-plan done definition

1. All four PRs merged **A → B → C → D**; `ONTOLOGY_VERSION == 14`; `EDGE_SCHEMA` has **11** entries.
2. `docs/EDGE-NAVIGATION.md` matches generator; `scripts/generate_edge_navigation.py --check` passes in CI.
3. Full re-index documented; `java-codebase-rag meta` shows producer counters and client-anchored HTTP edges on a rebuilt bank-chat-system index.
4. `grep -rn 'HTTP_CALLS\|ASYNC_CALLS' --include='*.py' .` shows only `Client`/`Producer`-anchored patterns (and schema definitions), no `Symbol` endpoints.
5. Agents get v3 hints for wrong-subject `neighbors` (PR-D).
6. Move [`propose/SCHEMA-V2-PROPOSE.md`](../propose/SCHEMA-V2-PROPOSE.md) to `propose/completed/` and this plan to `plans/completed/` when **D** lands.

# Tracking

- `PR-A`: _pending_
- `PR-B`: _pending_
- `PR-C`: _pending_
- `PR-D`: _blocked on HINTS-V3-PROPOSE.md_
