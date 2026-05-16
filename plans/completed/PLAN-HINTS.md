# Plan: Hints (road signs) + stored `OVERRIDES` edges

Status: **completed** (landed). This plan implemented
[`propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`](../propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md).

Depends on: **none** (strict `find` frame and `resolve` tool are already landed per the propose open-links section).

## Goal

- **PR-A:** Materialize `[:OVERRIDES]` in the Kuzu graph as a real `Symbol`→`Symbol` relationship (subtype overriding method → supertype declared method, signature match), expose it as a valid `neighbors(edge_types=…)` `EdgeType`, bump `ontology_version`, and prove **equivalence** with the two arms of `KuzuGraph.override_axis_rollup_for` via `neighbors` traversals.
- **PR-B:** Add top-level `hints: list[str]` to all MCP V2 tool outputs (`SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`), echo `limit` / `offset` on `FindOutput` and `SearchOutput`, implement the **locked v1 template catalog** in a dedicated pure module, and cover the contract with named tests (dedupe, cap, priority, kind gates, error paths, pagination echo, structural search signal).

## Principles (do not relitigate in review)

- Hints are **road signs**: ≤120 rendered chars, ≤5 unique rendered strings per output, templated (not LLM), **no graph I/O** in hint generation — pure function of the already-built response object.
- **Triggers** may use dot-keys and rollup counts in `edge_summary`; **emissions** use only real tool names and **`EdgeType` literals** (never dot-keys, never paraphrased edge labels).
- **`success=False`:** `hints` is always `[]`; `limit` / `offset` on find/search are `None` (pagination triggers do not fire).
- **Dedupe before cap:** collapse identical **rendered** strings; cap drops **lowest priority** rows when >5 remain (priority order locked in the propose §7.12).
- **PR-A is the breaking graph/ontology slice**; **PR-B is additive** on the agent-visible JSON (new optional fields defaulting to empty / `None`).
- **v1 catalog is locked** in the propose (§3.3 + Appendix A); new templates require a propose amendment.
- **Stored-edge path for `OVERRIDES`:** no `neighbors` special-case that runs parallel virtual Cypher for the same label; if materialization is abandoned later, that is a **new** propose.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-A | Kuzu schema + graph builder emission + `EdgeType` / docs + server instruction strings for `OVERRIDES` traversability | **Yes** (`ast_java.py` `ONTOLOGY_VERSION` 12→13) | Signature matching must mirror `override_axis_rollup_for`; diamond / multiply-inherited hierarchies; `DROP TABLE` order; rel columns minimal vs future attrs | Equivalence (in/out vs rollup id sets), schema round-trip, `EdgeType` validation matrix, deterministic edge set | PR-B |
| PR-B | `mcp_hints.py`, output models, handler wiring, README MCP section for hints + pagination echo | **No** (inherits PR-A graph for override hints only) | `search_v2` post-filter semantics for “full page”; find empty → `resolve` wording vs tool schema; template/schema drift | Per-catalog-row fixtures, cap/dedupe/priority, kind gate, char cap sweep, error paths, pagination echo | PR-A graph semantics (land PR-A first) |

**Landing order:** **PR-A → PR-B**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `OVERRIDES` direction | `(subtype_method)-[:OVERRIDES]->(supertype_declared_method)` unified rule per propose §6 (covers both rollup arms). |
| `OVERRIDDEN_BY` | Remains **virtual** describe-time rollup only; not an `EdgeType`. |
| `edge_summary` doc string | Update **only** the carve-out for `OVERRIDES`: it becomes a traversable stored label; dot-keys and `OVERRIDDEN_BY*` stay non-`EdgeType`. |
| Pagination echo | `FindOutput` + `SearchOutput` echo `limit`/`offset`; `None` means absent; hints never read request kwargs. |
| Search “weak results” hint | `len(results)==limit` **after** server-side trimming + `(max_score-min_score) < 0.1*max_score` when `limit` is non-`None`. |
| Hint module | New `mcp_hints.py`; single `generate_hints(output_kind, payload)` entry (exact signature up to implementer as long as contract holds). |

---

# PR-A — Stored `OVERRIDES` edges + ontology bump

## File-by-file changes

### 1. `build_ast_graph.py`

- Add `_SCHEMA_OVERRIDES` = `CREATE REL TABLE OVERRIDES(FROM Symbol TO Symbol)` (or equivalent minimal column set consistent with nearby rels — **prefer zero extra columns** like `DECLARES` unless a property is required for debugging).
- Register schema creation alongside other `CREATE REL TABLE` statements; extend `_drop_all` to `DROP TABLE IF EXISTS OVERRIDES` in an order that respects foreign keys (mirror other `Symbol`→`Symbol` rels).
- In the relationship write pass (same area as other `CREATE (…)-[:REL]->(…)` batch logic — **do not** put edge creation in `graph_enrich.py`; enrichment does not create rel tables per propose):
  - For each instance method `A` (non-static), declared on type `T_A`, walk transitive **supertype** side via existing `IMPLEMENTS`/`EXTENDS` edges (direction must match how `override_axis_rollup_for` “up” arm finds `parent` types).
  - For each ancestor type’s declared method `B` with **same `signature`** and `B.id != A.id`, insert **one** `OVERRIDES` edge `A -> B` (dedupe within builder).
- Ensure idempotence / deterministic ordering where the builder already sorts keys for other rels (symmetry test in propose §6).

### 2. `ast_java.py`

- Bump `ONTOLOGY_VERSION` **12 → 13** (single source of truth today for graph meta; see `tests/test_call_edges_e2e.py::test_ontology_version_matches_graph_meta`).

### 3. `java_index_flow_lancedb.py` / `build_ast_graph.py` GraphMeta insert

- Any code path that writes `GraphMeta` / embeds `ontology_version` into the index must pick up the new constant (typically automatic import of `ONTOLOGY_VERSION` — verify no hard-coded `12`).

### 4. `mcp_v2.py`

- Extend `EdgeType` with `"OVERRIDES"` (alphabetical placement preferred if the file already sorts literals).
- Update `NodeRecord.edge_summary` field description: **`OVERRIDES` dot-key rollup vs stored `OVERRIDES` edge** — clarify that the **virtual rollup key** in `edge_summary` (if still present) is still not a neighbors argument, while the **stored** relationship label `OVERRIDES` **is** valid in `neighbors(edge_types=…)` (tighten wording to avoid reader confusion between rollup dict key name and rel label).

### 5. `server.py`

- Update MCP / `_INSTRUCTIONS` text that currently states `OVERRIDES` is not valid for `neighbors` (grep for `OVERRIDES` in tool descriptions) so agents learn the **post–PR-A** rule: stored `OVERRIDES` is allowed; `OVERRIDDEN_BY` and dot-keys remain invalid.

### 6. `README.md`

- Bump documented `ontology_version` to **13** with a **Re-index required** callout: full graph rebuild (and any steps the README already lists for ontology bumps).

### 7. `kuzu_queries.py` (optional hygiene only)

- **Do not** change rollup semantics unless needed for correctness: `override_axis_rollup_for` remains the reference for equivalence tests. If counting `OVERRIDES` virtual rollup via stored edges reduces drift, that is an **optional** follow-up inside PR-A — only if it simplifies maintenance without new risk.

## Tests for PR-A

1. `test_overrides_stored_neighbors_in_matches_override_axis_impl_ids` — supertype method id: `neighbors(..., direction="in", edge_types=["OVERRIDES"])` id set equals `impl_ids` from the down-arm query embedded in (or shared with) `override_axis_rollup_for` logic.
2. `test_overrides_stored_neighbors_out_matches_override_axis_decl_ids` — subtype method id: `neighbors(..., direction="out", edge_types=["OVERRIDES"])` id set equals `decl_ids` from the up-arm.
3. `test_overrides_rel_schema_round_trips` — built fixture DB lists `OVERRIDES` in schema / accepts COPY or MATCH counts >0 where expected.
4. `test_neighbors_edge_type_adapter_accepts_overrides` — `TypeAdapter` / `neighbors_v2` accepts `["OVERRIDES"]`.
5. `test_neighbors_rejects_overridden_by_and_dot_keys` — existing negative tests updated if wording changed; still reject `OVERRIDDEN_BY`, `DECLARES.DECLARES_CLIENT`, etc.
6. `test_overrides_edge_set_deterministic_double_build` — two builds from same source: identical multiset of `(src,dst)` pairs (or sorted pair list equality).

**Fixture strategy:** extend `tests/fixtures/override_axis_rollup_smoke` usage in `tests/test_mcp_v2_compose.py` (session graph) **or** add a small dedicated test file if imports get heavy — either is fine if suite time stays reasonable.

## Definition of done (PR-A)

- [ ] Fresh `build_ast_graph.py` run produces non-empty `OVERRIDES` edges wherever the rollup previously reported non-zero override-axis keys.
- [ ] `ontology_version` in graph meta is **13** after rebuild.
- [ ] `neighbors_v2` works for `OVERRIDES` in both directions per equivalence tests.
- [ ] README + server copy no longer claim `OVERRIDES` is unusable in `neighbors`.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green (no heavy gate unless you touch gated paths).

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add rel table + drop stmt + create pattern | `build_ast_graph.py` | Schema DDL runs on empty DB |
| 2 | Implement directed override walk + edge insert + dedupe | `build_ast_graph.py` | Bank fixture + override-axis fixture show expected MATCH counts |
| 3 | Bump ontology constant | `ast_java.py` | Meta read shows 13 |
| 4 | Admit `EdgeType` + fix `NodeRecord` description | `mcp_v2.py` | Pydantic accepts new literal |
| 5 | Fix server / README agent copy | `server.py`, `README.md` | Grep shows consistent story |
| 6 | Add equivalence + negative + determinism tests | `tests/...` | New tests green |

---

# PR-B — `hints`, pagination echo, `mcp_hints.py` catalog

## File-by-file changes

### 1. `mcp_hints.py` (new)

- Implement `generate_hints(output_kind: Literal["search","find","describe","neighbors"], payload: …) -> list[str]` (payload typing: `BaseModel` instances or `dict` — choose one style and stick to it for testability).
- Encode the **v1 catalog** exactly as in the propose **Appendix A** (canonical string literals — resolve any §3.3 vs Appendix drift in favor of **Appendix A** during implementation if a mismatch is found; update the propose in the same PR if strings change).
- Implement **priority tiers** (propose §7.12): after rendered dedupe, if >5 hints remain, drop from the **lowest priority tier** upward until ≤5.
- Centralize **max length 120** validation (assert in dev / unit test sweep; do not silently truncate unless the propose explicitly allows — it prefers dropping templates that cannot render within 120).

### 2. `mcp_v2.py`

- Add `hints: list[str] = Field(default_factory=list, description=…)` to `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput` with the **normative** description block from propose §3.1.
- Add `limit: int | None = None`, `offset: int | None = None` to `SearchOutput` and `FindOutput` with the documented semantics.
- In `search_v2`, `find_v2`, `describe_v2`, `neighbors_v2` success paths: populate echo fields from the handler’s validated request parameters; compute `hints = generate_hints(...)`.
- Ensure **every** early `success=False` return constructs outputs with `hints=[]` and `limit=offset=None` for find/search.
- **Describe** path: pass enough structured inputs to hint code to apply **kind gates** (type Symbol vs method Symbol vs route vs client) per §3.3.

### 3. `server.py` (only if needed)

- If tool `description=` strings should mention `hints` / pagination echo for LLM clients, update minimally; avoid stdout noise (stdio MCP rule unchanged).

### 4. `README.md`

- Document `hints`, pagination echo fields, and advisory semantics in the MCP v2 section (short, links mentally to the propose for catalog details).

## Tests for PR-B

Use **verbatim** names below (adjust only if pytest collection would collide; if renamed, update this plan in the same PR).

1. `test_hints_describe_type_symbol_clients_via_members_emits`
2. `test_hints_describe_type_symbol_routes_via_members_emits`
3. `test_hints_describe_method_overriders_emits` *(requires PR-A graph in fixture)*
4. `test_hints_describe_method_clients_in_overriders_emits` *(requires PR-A)*
5. `test_hints_describe_method_declares_client_emits`
6. `test_hints_describe_method_exposes_emits`
7. `test_hints_describe_method_many_calls_emits`
8. `test_hints_describe_route_always_declaring_method`
9. `test_hints_describe_client_always_declaring_method`
10. `test_hints_find_empty_identifier_filter_suggests_resolve`
11. `test_hints_find_page_full_emits_narrow_or_paginate`
12. `test_hints_neighbors_empty_with_edge_types_emits_kind_check`
13. `test_hints_search_weak_structural_signal_emits`
14. `test_hints_search_dominant_top_no_weak_hint`
15. `test_hints_search_limit_none_never_emits_weak_hint`
16. `test_hints_dedupe_collapses_identical_rendered_strings`
17. `test_hints_cap_drops_lowest_priority_over_five`
18. `test_hints_kind_gate_method_payload_ignores_type_only_rollups`
19. `test_hints_clean_outputs_empty`
20. `test_hints_error_path_success_false_empty`
21. `test_find_output_pagination_echo_round_trip`
22. `test_search_output_pagination_echo_round_trip`
23. `test_hints_pagination_none_skips_page_derived_hints`
24. `test_hints_template_rendered_length_leq_120` — parametrized over all v1 templates with realistic placeholders.

## Definition of done (PR-B)

- [ ] All four outputs include `hints` (possibly empty); find/search include pagination echo fields per contract.
- [ ] Hint generation imports **no** `KuzuGraph` / no `._rows` / no search rerankers.
- [ ] Catalog + cap + dedupe + priority covered by named tests above.
- [ ] `ruff` + default `pytest tests -v` green.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add models + echo plumbing | `mcp_v2.py` | pydantic schema includes new fields |
| 2 | Implement catalog + priority + dedupe | `mcp_hints.py` | pure unit tests pass without DB |
| 3 | Wire into handlers | `mcp_v2.py` | integration tests with graph where needed |
| 4 | README / optional server description | `README.md`, `server.py` | docs match behavior |
| 5 | Add/extend tests | `tests/test_mcp_hints.py` (new) or split | all named tests exist and pass |

---

## Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Builder `OVERRIDES` set drifts from `override_axis_rollup_for` | High | Lock **equivalence tests** (same id sets, both directions) in PR-A; share helper queries where possible. |
| 2 | Confusion between `edge_summary["OVERRIDES"]` rollup key and `EdgeType` `OVERRIDES` | Medium | Tighten `NodeRecord` description + README; hints never emit dot-keys. |
| 3 | Search “full page” false positives/negatives after ranking changes | Medium | Tests use **crafted** `SearchOutput` payloads where possible; one integration test optional. |
| 4 | Template strings drift from real `resolve` / `neighbors` signatures | Medium | PR-B test that imports tool signatures or a lightweight snapshot assert on parameter names the templates mention. |
| 5 | PR-B merged before PR-A | High | **Enforce landing order**; hint tests that need stored edges are skipped only if you add an explicit gate (prefer **no skip** — land order instead). |

## Out of scope

- Structured `next_actions`, per-row hints, LLM-generated hints, i18n, `hints_version` field.
- Pre-fetched multi-hop walk payloads inside `describe`.
- `neighbors` pagination echo (separate propose).
- Additional v1 cross-tool hints beyond **find → resolve** (propose §7.16).
- `HTTP_CALLS` / `ASYNC_CALLS` / `IMPLEMENTS` / `EXTENDS` hint rows (v2 candidates per propose §5).
- Special-casing `tests/bank-chat-system/` inside hint logic (tests may use it as data only).

## Whole-plan done definition

1. Graph rebuilds at `ontology_version` **13** include traversable `OVERRIDES` edges proven equivalent to existing rollup arms.
2. MCP v2 outputs expose `hints` + find/search pagination echo per contract; v1 catalog fully covered by tests.
3. README documents ontology bump + re-index requirement and the new response fields.

## Tracking

- `PR-A`: **merged**
- `PR-B`: **merged**

## Cursor handoff

Per-PR execution prompts: [`plans/completed/CURSOR-PROMPTS-HINTS.md`](CURSOR-PROMPTS-HINTS.md)
(structure aligned with completed `plans/completed/CURSOR-PROMPTS-*.md` handoffs).
