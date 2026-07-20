<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Generated-Source Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated Java sources a first-class, tagged, filterable dimension — fully retrievable, with no ranking penalty — by detecting them (content-based, file-level), tagging both the Lance chunk index and the graph `Symbol` nodes, surfacing the tag on every search/edge result, and adding `exclude_generated` / `generated_only` filters mirroring the existing `role` / `exclude_roles` pair.

**Architecture:** A single shared, config-aware per-file classifier in `graph_enrich.py` decides whether a file is generated and by which generator family. The Lance flow computes it once per file and stores it on chunks; the graph flow computes it once per file and stores it on `Symbol` nodes. Both read paths surface the tag and accept the same two boolean filters, threaded through the Lance SQL predicate builder and the Kùzu Cypher + Python post-filter exactly as `role` / `exclude_roles` are today.

**Tech Stack:** Python 3, CocoIndex + LanceDB (vector index), Kùzu/LadybugDB (graph), tree-sitter-java AST (`ast_java.py`), FastMCP + pydantic (MCP tools), PyYAML (config), pytest.

## Global Constraints

- **Python env:** use `.venv/bin/python` and `.venv/bin/pip` (repo root) only — never system `python`/`pip`. Editable install only; if `jrag`/`java-codebase-rag` serve stale behavior while pytest passes, run `.venv/bin/pip install -e .`.
- **Test indexes:** erase stale manual indexes first (`rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`); tests build a fresh index in a temp dir; never commit an index under `tests/`.
- **Backward compatibility:** every new Lance column is added to `JAVA_ENRICHED_COLUMNS` (`search_lancedb.py:29`) so the schema-presence guard at `search_lancedb.py:543` SELECTs it only when present — old indexes without the column keep working with no migration beyond a reprocess.
- **Ontology version:** adding columns/properties is an extraction/enrichment semantic change — bump `ONTOLOGY_VERSION` (`ast_java.py:87`, currently `17`) to `18`. This drives re-indexing. Bump once (Task 2).
- **Mirror existing patterns exactly:** detection mirrors `module_for_path`/`microservice_for_path` (per-file shared helpers in `graph_enrich.py`); tagging mirrors the `capabilities` column addition; filtering mirrors `role` / `exclude_roles` end-to-end through BOTH the Lance SQL path and the Kùzu Cypher + Python post-filter path.
- **Equal-treatment default:** generated sources are NEVER ranked down or excluded from graph fan-out by default. Only the opt-in `exclude_generated` / `generated_only` filters change behavior. Do not touch `_ROLE_SCORE_WEIGHTS` or scoring.
- **Plans carry design, not code:** cite signatures, data shapes, and field names; do not write method bodies, algorithms, or test/impl code.

---

## File Structure

| File | Responsibility | Touched in |
|---|---|---|
| `graph_enrich.py` | Shared per-file classifier `classify_java_file`, config dataclass + loader | Task 1; consumed in 2, 3 |
| `java_index_flow_lancedb.py` | Lance chunk schema + write path | Task 2 |
| `search_lancedb.py` (write/col side) | `JAVA_ENRICHED_COLUMNS` registry | Task 2 |
| `ast_java.py` | `ONTOLOGY_VERSION` bump | Task 2 |
| `build_ast_graph.py` | Graph `Symbol` schema + node write path | Task 3 |
| `search_lancedb.py` (filter/read side) | `_build_extra_predicates`, `run_search`, CLI flags, hint printer | Task 5 |
| `mcp_v2.py` | `NodeFilter`, applicability map, Cypher + post-filter, `SearchHit`, `_row_to_search_hit`, `NodeRecord`/`find` projections | Tasks 4, 5 |
| `graph_types.py` | `NodeRef`, `_node_ref_from_row` | Task 4 |
| `ladybug_queries.py` | graph-side `Symbol` RETURN projections | Task 4 |
| `docs/` | operator + agent docs | Task 6 |
| `tests/` | per-task fixtures + tests | each task |

---

## Task 1: Detection core + extensible config

**Files:**
- Modify: `graph_enrich.py` (add `GeneratedDetectionConfig` dataclass ~line 228 area, `load_generated_detection` cached loader mirroring `_load_config_microservice_roots:113-141`, and `classify_java_file` near `module_for_path:1481`).
- Test: `tests/test_generated_detection.py` (create).
- Test fixtures: `tests/fixtures/generated_samples/` (create: one `.java` per generator family + one hand-written).

**Interfaces:**
- Consumes: `JavaFileAst` (defined `ast_java.py:389` — note `source_bytes` is an `int` byte COUNT, not raw bytes) and its `all_types: list[TypeDecl]`; each `TypeDecl.annotations: list[AnnotationRef]` where `AnnotationRef` (`ast_java.py:241`) has `.name`, `.qualified`, and `.arguments: dict[str,str]` (a `@Generated(value="org.openapitools...")` already parses to `arguments["value"]`). Consumes raw file `source: bytes` for header-banner detection (not derivable from `JavaFileAst`).
- Produces:
  - `GeneratedDetectionConfig` dataclass (frozen, `field(default_factory=...)`): `header_patterns: list[str]`, `annotation_patterns: list[str]`, `force_fqns: set[str]`, `exclude_fqns: set[str]`.
  - `load_generated_detection(project_root: str | Path | None) -> GeneratedDetectionConfig` — `@lru_cache(maxsize=64)`-per-root, reads `generated_detection` from the first matching `CONFIG_FILENAMES` (`graph_enrich.py:89`); returns empty config when section absent or `project_root is None`; stderr-warns + drops on malformed entries (mirror `_load_config_microservice_roots`).
  - `classify_java_file(source: bytes, ast: JavaFileAst, *, config: GeneratedDetectionConfig | None = None, project_root: str | Path | None = None) -> tuple[bool, str | None]` — returns `(generated, generated_by)`. `generated_by` is a lowercased family slug from the v1 set (`openapi`, `jsonschema2pojo`, `protobuf`, `mapstruct`, `wsimport`, `querydsl`, `jooq`, `immutables`, `autovalue`) or `None`.

**Detection behavior (design, not code):**
- A file is `generated=True` if ANY of:
  1. Any type in `ast.all_types` carries an annotation whose simple name is `Generated` (or `javax`/`jakarta.annotation.processing.Generated` / `org.immutables.Generated` / `lombok.Generated` / `com.squareup.javapoet...` — the v1 marker set, verified at impl time) — `generated_by` inferred from the annotation's `arguments["value"]`/`arguments["comments"]` when it names a known generator, else `None`.
  2. The file header (a bounded prefix of `source`, e.g. first 4 KB before the first type) matches a built-in generator banner pattern (OpenAPI, jsonschema2pojo, protobuf, wsimport, MapStruct default headers) — `generated_by` = matched family.
  3. A type's FQN is in `config.force_fqns`.
- A file is forced `generated=False` if a type's FQN is in `config.exclude_fqns` (wins over 1–3).
- `config.header_patterns` / `config.annotation_patterns` extend the built-in sets (treated as additional markers).

- [ ] **Step 1: Write failing tests** — scenarios + expected results:
  - `test_openapi_annotation` — source+ast where a type has `@Generated(value="org.openapitools.codegen...")` → `(True, "openapi")`.
  - `test_javax_generated_no_value` — `@Generated` with no value → `(True, None)`.
  - `test_jsonschema2pojo_header` — source bytes with a jsonschema2pojo banner, ast with no `@Generated` → `(True, "jsonschema2pojo")`.
  - `test_protobuf_header` — protobuf-generated banner → `(True, "protobuf")`.
  - `test_handwritten_is_not_generated` — plain hand-written type, no markers → `(False, None)`.
  - `test_config_force_fqn` — `GeneratedDetectionConfig(force_fqns={"com.example.Model"})`, ast with that FQN, no markers → `(True, None)`.
  - `test_config_exclude_fqn_wins` — `@Generated` present but FQN in `exclude_fqns` → `(False, None)`.
  - `test_config_extra_header_pattern` — custom `header_patterns` matches an internal codegen banner → `(True, None)`.
- [ ] **Step 2: Run tests, verify FAIL** — `pytest tests/test_generated_detection.py -v` → FAIL (names undefined).
- [ ] **Step 3: Implement** — add `GeneratedDetectionConfig`, `load_generated_detection`, and `classify_java_file` per the Produces contracts and detection behavior above. Verify exact marker strings per family against real generator output (annotate the v1 marker table with a short comment of the verified strings).
- [ ] **Step 4: Run tests, verify PASS** — `pytest tests/test_generated_detection.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add graph_enrich.py tests/test_generated_detection.py tests/fixtures/generated_samples/` → `git commit -m "feat(detection): classify generated Java sources (content + extensible config)"`.

---

## Task 2: Tag Lance chunks + ontology bump

**Files:**
- Modify: `java_index_flow_lancedb.py` — `JavaLanceChunk` dataclass (`:260-282`), `process_java_file` row construction (`:444-466`; `rel` at `:427`, `content_bytes` at `:428`).
- Modify: `search_lancedb.py` — `JAVA_ENRICHED_COLUMNS` (`:29-42`).
- Modify: `ast_java.py` — `ONTOLOGY_VERSION` (`:87`): `17` → `18`.
- Test: `tests/test_lancedb_generated_column.py` (create).

**Interfaces:**
- Consumes: `classify_java_file` (Task 1), the parsed `JavaFileAst` already produced inside `process_java_file` before the chunk/enrich loop, and `content_bytes`.
- Produces: `JavaLanceChunk` gains two scalar fields (no `LanceType` override needed — plain scalars): `generated: bool` and `generated_by: str | None`. CocoIndex derives the Lance write schema from the dataclass (`java_index_flow_lancedb.py:593-602`), so no separate schema edit. Read side: `"generated"` and `"generated_by"` appended to `JAVA_ENRICHED_COLUMNS` (the presence guard at `:543` auto-SELECTs them only on indexes that have them).

- [ ] **Step 1: Write failing test** — scenario + expected: index a fixture with one generated file (e.g. an OpenAPI DTO) and one hand-written file; query the Lance java table directly; assert every chunk of the generated file has `generated == True` and `generated_by == "openapi"`, every chunk of the hand-written file has `generated == False`/`None`, and the columns exist in the table schema.
- [ ] **Step 2: Run test, verify FAIL** — `pytest tests/test_lancedb_generated_column.py -v` → FAIL (fields absent).
- [ ] **Step 3: Implement** — (a) bump `ONTOLOGY_VERSION` to `18`; (b) add `generated`/`generated_by` fields to `JavaLanceChunk`; (c) in `process_java_file`, compute the classification ONCE after the AST is parsed and `content_bytes` is available, then pass both values into the `JavaLanceChunk(...)` constructor in the row loop (do NOT thread through `ChunkEnrichment` — it is a per-file value, like `module`/`microservice`); (d) append `"generated"`, `"generated_by"` to `JAVA_ENRICHED_COLUMNS`.
- [ ] **Step 4: Run test, verify PASS** — `pytest tests/test_lancedb_generated_column.py -v` → PASS. Also run a search smoke (`search_lancedb`) against the fixture to confirm no regression.
- [ ] **Step 5: Commit** — `git add java_index_flow_lancedb.py search_lancedb.py ast_java.py tests/test_lancedb_generated_column.py` → `git commit -m "feat(index): tag Lance chunks generated/generated_by; bump ontology to 18"`.

---

## Task 3: Tag graph Symbol nodes

**Files:**
- Modify: `build_ast_graph.py` — `_SCHEMA_NODE` DDL (`:2901`), `_NODE_COLUMNS` (`:3138`), `_node_row` defaults (`:3083`), `_SET_SYMBOL_BY_ID` (`:3155`), `_write_nodes_impl` type loop (`:3236-3268`, resolver call `:3238`), pass-1 per-file site (`:1031-1077`, where `module_for_path`/`microservice_for_path` are called at `:1060-1061`), `_load_existing_types` (`:584-631`, mirror `type_role_by_node_id` seed at `:631`).
- Test: `tests/test_graph_generated_node.py` (create).

**Interfaces:**
- Consumes: `classify_java_file` (Task 1), `TypeDecl`/`JavaFileAst` and raw file bytes available in pass 1.
- Produces: the `Symbol` node table gains columns `generated BOOLEAN` and `generated_by STRING`; persisted via the bulk-COPY path and the incremental `_SET_SYMBOL_BY_ID` upsert. A new `tables.type_generated_by_node_id: dict[str, tuple[bool, str]]` field (on `GraphTables`, mirror `type_role_by_node_id` at `:467`) carries the per-type classification from pass 1 to `_write_nodes_impl`.

**Stub discipline (critical):** `loaded_from_db` stubs (`:3243-3252`) build a bare `TypeDecl(name, kind, fqn)` with NO annotations, so re-detection from a stub decl is wrong. Mirror the `type_role_by_node_id` seed pattern: seed `type_generated_by_node_id` in `_load_existing_types` (reading the persisted `generated`/`generated_by` columns) so preserved stubs retain their stored values, exactly as preserved stubs retain `role`.

- [ ] **Step 1: Write failing tests** — scenarios + expected:
  - Build a graph from a fixture with a generated type → query the `Symbol` node → assert `generated == True`, `generated_by` set.
  - A hand-written type in the same fixture → `generated == False`.
  - Incremental rebuild (re-run on an already-built graph with one file changed) → a preserved (unchanged) generated type retains its `generated`/`generated_by` (stub path).
- [ ] **Step 2: Run tests, verify FAIL** — `pytest tests/test_graph_generated_node.py -v` → FAIL (columns absent).
- [ ] **Step 3: Implement** — (a) add `generated BOOLEAN, generated_by STRING` to `_SCHEMA_NODE`; append `"generated"`, `"generated_by"` to `_NODE_COLUMNS`; add `generated`/`generated_by` defaults to `_node_row`; append the two SET clauses to `_SET_SYMBOL_BY_ID`; (b) add `type_generated_by_node_id` to `GraphTables`; (c) in pass 1, compute `classify_java_file(...)` once per file and seed `type_generated_by_node_id` for each type in the file; (d) in `_write_nodes_impl` type loop, read from `type_generated_by_node_id` for the persisted/stub value (else from a fresh `classify_java_file` call) and pass `generated=`/`generated_by=` into `_node_row(...)`; (e) seed `type_generated_by_node_id` in `_load_existing_types`.
- [ ] **Step 4: Run tests, verify PASS** — `pytest tests/test_graph_generated_node.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add build_ast_graph.py tests/test_graph_generated_node.py` → `git commit -m "feat(graph): tag Symbol nodes generated/generated_by"`.

---

## Task 4: Surface the tag on every result

**Files:**
- Modify: `mcp_v2.py` — `SearchHit` model (`:466`), `_row_to_search_hit` (`:610`), `_load_node_record` symbol projection (`:683-684`), `find(symbol)` RETURN projection (`:1003-1004`).
- Modify: `graph_types.py` — `NodeRef` model (`:36`), `_node_ref_from_row` (`:128`).
- Modify: `ladybug_queries.py` — `_symbol_return_for` (`:209`), `_SYM_COLS` (`:299`), inline INJECTS `s_proj`/`t_proj` (`:1110`, `:1117`).
- Modify: `search_lancedb.py` — CLI hint printer (`:1206-1214`).
- Test: `tests/test_generated_surface.py` (create).

**Interfaces:**
- Consumes: the new Lance columns (Task 2) and graph `Symbol` properties (Task 3).
- Produces: `SearchHit`, `NodeRef`, and `NodeRecord.data` each gain `generated: bool | None` and `generated_by: str | None` fields; graph RETURN projections emit `s.generated AS generated, s.generated_by AS generated_by` so the row populators can read them; CLI output prints ` | generated` / ` | generated:<by>` when set.

- [ ] **Step 1: Write failing tests** — scenarios + expected: against a fixture with a generated type, (a) `search` returns a `SearchHit` for a generated chunk with `generated=True`/`generated_by` set; (b) `find(symbol)` returns a `NodeRef` with the fields; (c) `describe` includes `generated`/`generated_by` in `NodeRecord.data`; (d) `neighbors` endpoint `NodeRef` carries them; (e) CLI `search` output prints the `generated` hint.
- [ ] **Step 2: Run tests, verify FAIL** — `pytest tests/test_generated_surface.py -v` → FAIL (fields absent).
- [ ] **Step 3: Implement** — add the two fields to `SearchHit`, `NodeRef`; populate them in `_row_to_search_hit` and `_node_ref_from_row` from the row; add `n.generated AS generated, n.generated_by AS generated_by` to the `_load_node_record` and `find(symbol)` projections; add `s.generated`/`s.generated_by` to `ladybug_queries.py` projections; add the CLI hint line mirroring the `role` hint block.
- [ ] **Step 4: Run tests, verify PASS** — `pytest tests/test_generated_surface.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add mcp_v2.py graph_types.py ladybug_queries.py search_lancedb.py tests/test_generated_surface.py` → `git commit -m "feat(mcp): surface generated/generated_by on search/find/describe/neighbors"`.

---

## Task 5: `exclude_generated` / `generated_only` filters

**Files:**
- Modify: `search_lancedb.py` — `_build_extra_predicates` signature (`:74-86`) + predicate block (insert after `:117`), `run_search` signature (`:936`) + kwargs call (`:988`), CLI flags (`:1116`) + pass-through (`:1162`).
- Modify: `mcp_v2.py` — `NodeFilter` (`:193`), `_NODEFILTER_APPLICABLE_FIELDS["symbol"]` (`:274-275`), `_symbol_where_from_filter` (`:651`), `_node_matches_filter` symbol block (`:780`).
- Test: `tests/test_generated_filter.py` (create).

**Interfaces:**
- Consumes: the `generated` Lance column (Task 2) and graph `Symbol.generated` (Task 3).
- Produces: two boolean filter params threaded through BOTH engines, exactly mirroring `role`/`exclude_roles`:
  - Lance SQL: `exclude_generated=True` → predicate `(generated IS NULL OR generated = false)`; `generated_only=True` → `generated = true` (each guarded `if flag and "generated" in columns`).
  - Kùzu Cypher (`_symbol_where_from_filter`): the same two predicates against `s.generated`.
  - Python post-filter (`_node_matches_filter`): same two checks on `row.get("generated")` for route/client/producer lists and neighbor endpoints.
  - `NodeFilter` gains `generated_only: bool = False` and `exclude_generated: bool = False` (declared — `NodeFilter` is `extra="forbid"`); both listed in `_NODEFILTER_APPLICABLE_FIELDS["symbol"]`.
  - CLI gains `--exclude-generated` and `--generated-only` (`action="store_true"`).
- Note: do NOT add the flags to the scoring `skip_role_weight` line (`:992`) — they are not rank-affecting.

- [ ] **Step 1: Write failing tests** — scenarios + expected (fixture with one generated + one hand-written type):
  - `run_search(..., exclude_generated=True)` → no generated chunks returned; hand-written present.
  - `run_search(..., generated_only=True)` → only generated chunks returned.
  - default (neither flag) → both returned.
  - `find(symbol, filter=NodeFilter(exclude_generated=True))` → generated `NodeRef` excluded (Cypher path).
  - `find(symbol, filter=NodeFilter(generated_only=True))` → only generated (post-filter path).
- [ ] **Step 2: Run tests, verify FAIL** — `pytest tests/test_generated_filter.py -v` → FAIL (params rejected by `extra="forbid"` / undefined).
- [ ] **Step 3: Implement** — mirror the `exclude_roles` end-to-end trace exactly: Lance predicate builder + `run_search` + CLI; `NodeFilter` field + applicability tuple + Cypher builder + Python post-filter.
- [ ] **Step 4: Run tests, verify PASS** — `pytest tests/test_generated_filter.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add search_lancedb.py mcp_v2.py tests/test_generated_filter.py` → `git commit -m "feat(search): exclude_generated / generated_only filters"`.

---

## Task 6: Docs — supersede manual-ignore guidance

**Files:**
- Modify: `docs/CODEBASE_REQUIREMENTS.md` (`:62-66`, `:444-466` — replace "ignore generated manually" with "generated sources are auto-tagged; filter with `exclude_generated`").
- Modify: `docs/CONFIGURATION.md` — document the `generated_detection` YAML section (mirror the `role_overrides` doc).
- Modify: `docs/JAVA-CODEBASE-RAG-CLI.md` — document the reprocess requirement for `generated`/`generated_by` on existing indexes, and the `--exclude-generated` / `--generated-only` flags.
- Modify: `docs/AGENT-GUIDE.md` — agent guidance: generated types now carry a `generated` tag on results; use `exclude_generated` when you want hand-written code only; generated sources are included by default.
- Test: no unit test; verify `java-codebase-rag --help` (or the search CLI help) lists the new flags, and that the docs render.

- [ ] **Step 1: Update `CODEBASE_REQUIREMENTS.md`** — remove the "add `**/generated/**` yourself" guidance; state generated sources are detected + tagged and filterable.
- [ ] **Step 2: Update `CONFIGURATION.md`** — add a `generated_detection` subsection with the four keys (`header_patterns`, `annotation_patterns`, `force_fqns`, `exclude_fqns`) and an example.
- [ ] **Step 3: Update `JAVA-CODEBASE-RAG-CLI.md`** — reprocess note + the two CLI flags.
- [ ] **Step 4: Update `AGENT-GUIDE.md`** — the tag + filter guidance for agents.
- [ ] **Step 5: Verify + commit** — confirm CLI help shows the flags; `git add docs/` → `git commit -m "docs: generated-source detection, tagging, and filtering"`.

---

## Self-Review (run after writing)

1. **Code scan:** no method bodies / algorithms / test-or-impl code — only signatures, data shapes, field names, behavior descriptions. ✓
2. **Self-containment:** each task's Consumes/Produces carry the full contract; no "see spec" pushes. ✓ (Task 1 defines the classifier contract consumed by 2 & 3; Task 2/3 define the stored shapes consumed by 4/5.)
3. **Spec coverage:** detect (T1) ✓, tag both indexes (T2 Lance, T3 graph) ✓, surface (T4) ✓, filter (T5) ✓, migration/reprocess (T2 ontology bump + T6 doc) ✓, extensible config (T1) ✓.
4. **Placeholder scan:** marker strings are explicitly "verified at impl time" (an open question in the spec, not a placeholder) with the v1 family set named; no TBD/TODO.
5. **Type consistency:** `generated: bool` / `generated_by: str | None` used uniformly across `JavaLanceChunk`, `Symbol`, `SearchHit`, `NodeRef`, `NodeRecord`; `classify_java_file(...) -> tuple[bool, str | None]` consistent in T1, T2, T3; `type_generated_by_node_id: dict[str, tuple[bool, str]]` consistent in T3.

## Open question carried from spec

Exact `@Generated`/header marker strings per generator family are verified at Task 1 implementation time (annotation FQNs + banner regexes). Some families (certain jsonschema2pojo / older protobuf configs) emit only a header comment and no `@Generated` — the design accounts for both signal types.
