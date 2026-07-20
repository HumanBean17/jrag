> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: `list_clients` MCP tool + `Client` graph node

Status: **completed — shipped via PR-LC1 → PR-LC3** (merged 2026-05). Pairs with
[`propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md`](../propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md).

Depends on: brownfield annotations v2 (outbound client declarations separated
from `Route` rows), merged before LC1.

## Goal

Restore and improve outbound discovery after the v2 split by introducing a
first-class outbound declaration surface:

- New graph node table `Client` plus relation `DECLARES_CLIENT` (`Symbol -> Client`).
- New MCP tool `list_clients` with filter semantics parallel to `list_routes`.
- Pass6 hint-recovery retargeted from caller `http_consumer` routes to
  caller-declared `Client` rows.
- Tests and docs proving that `find_route_callers`/`HTTP_CALLS` behavior
  remains intact while outbound declarations move out of `Route`.

## Principles (do not relitigate in review)

- `HTTP_CALLS` remains `Symbol -> Route`; `Client` is caller-side declaration
  metadata, not a replacement for call edges.
- One `Client` row per `@CodebaseClient` declaration plus synthesized Feign
  interface-method rows post-v2.
- Brownfield composition remains first-class and follows the existing
  `BrownfieldOverrides` model; do not introduce parallel override systems.
- Tool contract is additive: empty filters return `success=True`, `clients=[]`.
- Ontology/versioning/docs consistency is mandatory for schema-enrichment
  changes.

## PR breakdown - overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-LC1** | Graph schema + extraction + persistence (`Client`, `DECLARES_CLIENT`) | 9 -> 10 | 4-5 | extraction + schema + deterministic id | v2 prerequisite only |
| **PR-LC2** | Resolver integration (pass6 hint recovery reads `Client`) + non-regression of `HTTP_CALLS` behavior | none | 2-3 | pass6 matching regression + route-caller continuity | PR-LC1 |
| **PR-LC3** | MCP surface (`list_clients`), Kuzu query helpers, DTOs, docs | none | 3-4 | tool filters + empty/limit behavior + docs | PR-LC1 |

Landing order: **LC1 -> LC2 -> LC3**.

## Resolved design decisions from the proposal

| Topic | Decision |
| --- | --- |
| `Client.target_service` typing | Keep as `STRING` (same practicality as `Route.microservice`) for v1. |
| Match outcome location | Keep outcome on `HTTP_CALLS` edges; do not duplicate onto `Client`. |
| Tool naming | Ship `list_clients` (not `list_http_clients`) to align with `@CodebaseClient`. |
| Imperative auto-emission | For v1, keep imperative call-site `Client` rows opt-in via `@CodebaseClient`; no broad auto-synthesis beyond Feign method synthesis. |

---

# PR-LC1 - `Client` schema + extraction + persistence

**Goal:** persist outbound declaration rows independently from `Route`, including
Feign synthesized declarations and source/brownfield `@CodebaseClient` rows.

## File-by-file changes

### 1. `build_ast_graph.py` - schema + writers

- Add node table DDL:
  - `Client(id, client_kind, target_service, path, path_template, path_regex, method, member_fqn, member_id, microservice, module, filename, start_line, end_line, resolved, source_layer)`.
- Add relation table DDL:
  - `DECLARES_CLIENT(FROM Symbol TO Client, confidence DOUBLE, strategy STRING)`.
- Wire both into create/drop lifecycle.
- Add row dataclasses + `GraphTables` collections for `Client` nodes and
  `DECLARES_CLIENT` rows.
- Add graph_meta counters (at minimum totals + by-kind map) using existing
  STRING-JSON pattern for map-shaped values.

### 2. `ast_java.py` - extraction support

- Ensure extraction returns outbound declaration payloads needed to form stable
  `Client` rows (method, path/path_template, target_service, resolved, origin).
- Synthesize Feign method declarations into outbound client declarations even
  without explicit `@CodebaseClient`.
- Preserve existing string/path normalization contracts used by route matching.

### 3. `graph_enrich.py` - composition source

- Re-use `resolve_http_client_for_method` composition output as the canonical
  per-method outbound declaration set feeding `Client` rows.
- Stamp `source_layer` according to the layer that produced the winning row
  (`layer_a_meta`, `layer_b_ann`, `layer_b_fqn`, `layer_c_source`, `builtin`).

### 4. `java_ontology.py` and versioning

- Confirm/extend client-kind validity set for `Client.client_kind` values.
- Bump ontology version to **10** (per proposal acceptance criteria).

## Tests for PR-LC1

New file: `tests/test_client_node_extraction.py` (target ~6 tests):

1. `test_client_rows_emitted_for_codebase_client_annotations`
2. `test_client_rows_synthesized_for_feign_methods`
3. `test_declares_client_edge_targets_client_id`
4. `test_client_id_is_deterministic_across_rebuilds`
5. `test_client_source_layer_reflects_winning_override_layer`
6. `test_client_schema_persisted_and_queryable`

## Definition of done (PR-LC1)

- `Client` and `DECLARES_CLIENT` tables exist and are populated.
- Ontology version bumped 9 -> 10 and reflected in metadata.
- Deterministic id contract implemented and test-locked.
- Full tests green with new extraction suite.

---

# PR-LC2 - pass6 hint recovery migration to `Client`

**Goal:** switch matcher hint source from caller `http_consumer` route rows to
caller-declared `Client` rows, without changing downstream match semantics.

## File-by-file changes

### 1. `build_ast_graph.py` - pass6 hint lookup retarget

- Update pass6 hint recovery path to:
  - resolve caller member -> `DECLARES_CLIENT` -> `Client`.
  - read `path` / `target_service` / `method` from `Client`.
- Keep matcher outcome semantics unchanged (`cross_service`,
  `intra_service`, `ambiguous`, `phantom`, `unresolved`).
- Keep `HTTP_CALLS(Symbol -> Route)` edge generation/meaning unchanged.

### 2. `kuzu_queries.py` (if helper updates are needed)

- Add/adjust helper query used by pass6 for looking up client hints via
  `member_id` or declaring symbol id.
- Keep read-only helpers aligned with `meta()` JSON decode behavior.

## Tests for PR-LC2

New file: `tests/test_client_hint_recovery.py` (target ~4 tests):

1. `test_pass6_uses_client_hints_for_feign_resolution`
2. `test_cross_service_match_outcome_unchanged_after_client_migration`
3. `test_find_route_callers_still_returns_expected_feign_caller`
4. `test_missing_client_hint_falls_back_to_existing_unresolved_or_phantom_flow`

Fixture expectation (bank-chat-system, after v2 + one Feign `@CodebaseClient`):

- chat-core -> chat-assign Feign call resolves to correct `http_endpoint`.
- `find_route_callers` continuity is preserved.

## Definition of done (PR-LC2)

- pass6 no longer depends on caller `http_consumer` route hints.
- Regression behavior remains stable for existing call-edge consumers.
- Targeted pass6 regression tests pass and full suite stays green.

---

# PR-LC3 - `list_clients` MCP tool + query surface + docs

**Goal:** expose outbound declarations through a first-class MCP tool with
filters symmetric to current route-listing ergonomics.

## File-by-file changes

### 1. `kuzu_queries.py` - client listing query helper

- Add query helper for listing `Client` rows with optional filters:
  `microservice`, `client_kind`, `target_service`, `path_prefix`, `method`,
  `limit`.
- Ensure deterministic ordering and limit enforcement.

### 2. `server.py` - DTOs + tool registration

- Add `ClientRowDto` and `ClientsListOutput`.
- Register `@mcp.tool(name="list_clients", ...)`.
- Validate/normalize filter inputs:
  - method normalization (if repository conventions currently do so),
  - safe default `limit=100`, bounded `1..500`.
- Empty results return success with empty list (not an error).

### 3. `README.md` - public-surface docs

- Document new `list_clients` tool in MCP tool list and usage notes.
- Add "Re-index required" / ontology callout for the `Client` schema addition.
- Clarify directionality split:
  - `list_routes` = inbound exposures,
  - `list_clients` = outbound declarations.

## Tests for PR-LC3

New file: `tests/test_list_clients.py` (target ~8 tests):

1. `test_list_clients_returns_rows`
2. `test_list_clients_filter_microservice`
3. `test_list_clients_filter_client_kind`
4. `test_list_clients_filter_target_service`
5. `test_list_clients_filter_path_prefix`
6. `test_list_clients_filter_method`
7. `test_list_clients_empty_result_is_success_with_empty_clients`
8. `test_list_clients_limit_bounds_and_clamping_behavior`

Existing tool-suite test update:

- register/visibility smoke in `tests/test_mcp_tools.py` (or equivalent).

## Definition of done (PR-LC3)

- `list_clients` tool is registered and callable.
- Filter behavior is test-covered and stable.
- Docs updated to reflect the new outbound discovery entry point.

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Feign synthesis and `@CodebaseClient` composition diverge, causing duplicate/missing `Client` rows | high | Source-layer stamping + deterministic-id tests + layer-specific fixtures |
| 2 | pass6 migration changes match outcomes unintentionally | high | LC2 regression tests assert outcome parity and known cross-service path |
| 3 | Tool filters drift from route-tool ergonomics | medium | LC3 filter-by-filter tests + strict DTO contract |
| 4 | Ontology bump/docs missed for schema change | medium | LC1 done criteria require version+README callout checks |
| 5 | Overreach into out-of-scope companion tools (`get_client_by_path`, etc.) | low | Explicitly defer all companion tools to follow-up proposals |

# Out of scope (for this plan series)

- `get_client_by_path`, `find_client_callers`, `find_client_target_route`.
- `Producer` node and `list_async_producers`.
- YAML schema redesign beyond existing brownfield override capabilities.
- Route-centric tool redesign (`find_route_callers` remains route-centric).

# Whole-plan done definition

1. PR-LC1/LC2/LC3 merged in order.
2. Graph contains `Client` nodes and `DECLARES_CLIENT` edges with deterministic ids.
3. pass6 hint recovery reads `Client` declaration metadata.
4. `list_clients` tool available with full filter coverage.
5. `README.md` and ontology/reindex callouts updated consistently.
6. `ruff check .` and `pytest tests -v` pass for each PR.

# Tracking

- `PR-LC1`: merged (Client schema + extraction + persistence).
- `PR-LC2`: merged (pass6 hint recovery on `Client`).
- `PR-LC3`: merged (`list_clients` MCP tool + query surface + docs/tests).
