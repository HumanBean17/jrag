> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: HINTS-V3 (EDGE_SCHEMA-driven empty `neighbors` hints)

Status: **completed** (landed [#160](https://github.com/HumanBean17/java-codebase-rag/pull/160)). This plan implemented
[`propose/completed/HINTS-V3-PROPOSE.md`](../propose/completed/HINTS-V3-PROPOSE.md).

Depends on:

- [`propose/completed/SCHEMA-V2-PROPOSE.md`](../propose/completed/SCHEMA-V2-PROPOSE.md) — `EDGE_SCHEMA`, post-flip endpoints, `brownfield_resolver_sourced`, `typical_traversals`, `BROWNFIELD_RESOLVER_STRATEGY_SET` (PR-A–C on `master`).
- **Code PR-D** runs only after SCHEMA-V2 **PR-A, PR-B, PR-C** are merged.
- **Propose gate:** `HINTS-V3-PROPOSE.md` merged to `master` before SCHEMA-V2 **PR-A** starts (may stay GitHub `draft`).
- **Lock gate:** `HINTS-V3-PROPOSE.md` `Status: locked` before **PR-D** merges.

Sequence reference: [`docs/PROPOSES-ORDER.md`](../docs/PROPOSES-ORDER.md).

## Goal

- **PR-D (single code PR):** Delete `TPL_NEIGHBORS_EMPTY_KIND_CHECK`; add four EDGE_SCHEMA-driven empty-result templates; implement `neighbors_empty_hints()` + `typical_traversal_for()`; extend `neighbors_v2` hint payload with `subject_record` and `requested_direction`; wire `generate_hints("neighbors", …)` empty branch; keep v2 fuzzy hint on non-empty results unchanged.
- Agents holding the wrong node kind or direction for `HTTP_CALLS` / `ASYNC_CALLS` (post-flip) get actionable traversals sourced from `EDGE_SCHEMA`, not a generic kind check.

## Principles (do not relitigate in review)

- **`EDGE_SCHEMA` is the only edge-shape knowledge in `mcp_hints.py`.** No edge-name or Client→Route literals outside tests.
- **One template per mismatch dimension:** alien kind, wrong direction, type-level requery, brownfield-resolver absence (four templates).
- **Per-edge evaluation order:** alien kind → wrong direction → type-level (`member_only`); first match wins for rows 1–3.
- **Brownfield row 4** may co-fire with structural rows; deduped once per output across edges.
- **Fuzzy vs brownfield empty hints are disjoint:** v2 `TPL_NEIGHBORS_FUZZY_STRATEGY` only on **non-empty** results; row 4 only on **empty** when `brownfield_resolver_sourced`.
- **`PRIORITY_META` for all new templates** — same tier as deleted v1 empty template.
- **No dot-key edge labels in hint text** — post-filter + test (v2 invariant).
- **No ontology bump** — query-time only; re-index already required by SCHEMA v14.
- **Multi-id `neighbors`:** hints use **`origins[0]`** only (document in `MCP_HINTS_FIELD_DESCRIPTION` if needed).

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-D | `mcp_hints.py` + `mcp_v2.py` neighbors payload + tests + minimal README | **No** | Subject node label resolution; `member_only` coverage; traversal placeholder `{id}`; cap interaction with multi-edge requests | `tests/test_mcp_hints.py` HV1–HV20 | Nothing — requires SCHEMA PR-C |

**Landing order:** **PR-D** after SCHEMA **PR-C** on `master`.

**Merge gates:** This file + [`plans/completed/AGENT-PROMPTS-HINTS-V3.md`](./AGENT-PROMPTS-HINTS-V3.md) before PR-D code merges (by analogy with SCHEMA Decision 29).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Implementation PR | Same as SCHEMA-V2 PR-D — one PR titled `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`. |
| Deleted template | `TPL_NEIGHBORS_EMPTY_KIND_CHECK` — no alias. |
| Subject kind source | Node label from `subject_record` (`Symbol`, `Client`, `Route`, `Producer`), not `symbol_kind` alone. |
| Type-level requery | `Symbol` with `symbol_kind ∈ _TYPE_SYMBOL_KINDS` and `EdgeSpec.member_only=True`. |
| `member_only` | Prefer landed in SCHEMA PR-A on `EdgeSpec`; PR-D adds only if PR-A omitted. |
| Wrong direction rule | Subject matches **opposite** endpoint for requested `in`/`out`. |
| Coverage test HV19 | ∃ synthetic `(edge, subject_label, direction)` per `EDGE_SCHEMA` edge triggering row 1–3 or row 4 — not ∀ empty queries. |
| v1/v2 catalogs | Unchanged except neighbors empty branch. |

---

# PR-D — kind/direction-aware empty-result hints

## File-by-file changes

### 1. `mcp_hints.py`

- Delete `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.
- Add verbatim templates from propose §3.1:
  - `TPL_NEIGHBORS_WRONG_SUBJECT_KIND`
  - `TPL_NEIGHBORS_WRONG_DIRECTION`
  - `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY`
  - `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED`
- Import `EDGE_SCHEMA` from `java_ontology` (not a copy).
- Add helpers:
  - `_subject_node_label(subject_record: dict) -> str` — map row to `Symbol`/`Client`/`Route`/`Producer`.
  - `typical_traversal_for(edge: str, role_key: str, *, subject_id: str, direction: str) -> str` — select from `EdgeSpec.typical_traversals`, substitute `{id}`, `{direction}`, `{edge}` placeholders without embedding edge literals beyond schema output.
  - `neighbors_empty_hints(subject_record, requested_edge_types, requested_direction) -> list[tuple[int, str]]` per propose §3.2–3.3.
- Update `generate_hints` `neighbors` branch:
  - When `results` empty and `requested_edge_types` non-empty: call `neighbors_empty_hints`, merge pairs before fuzzy check.
  - When `results` non-empty: existing fuzzy path unchanged (`TPL_NEIGHBORS_FUZZY_STRATEGY`).
- Post-filter rendered hints: reject any hint containing a dot-key edge label pattern used for composed rollups (reuse v2 approach / test).
- Module docstring: reference HINTS-V3 propose.

### 2. `mcp_v2.py`

- In `neighbors_v2`, build hint payload per propose §3.6:
  - `requested_direction`: echo `direction` param.
  - `origin_id`: first origin when `ids` is a list.
  - `subject_record`: `_load_node_record(g, origin_id, kind)` using resolved kind from id prefix / `_resolve_node_kind`.
- Pass payload into `generate_hints("neighbors", neigh_payload)`.

### 3. `java_ontology.py` (only if PR-A skipped `member_only`)

- Add `member_only: bool = False` on `EdgeSpec` and set flags per propose §3.4.

### 4. `README.md` (minimal)

- MCP v2 hints paragraph: neighbors empty results may emit EDGE_SCHEMA-driven structural hints; link HINTS-V3 propose.

### 5. `server.py` (optional, minimal)

- One line on `neighbors` tool description: empty results may include traversal hints (no new parameters).

### 6. `tests/test_mcp_hints.py`

- Pure `generate_hints("neighbors", …)` tests with crafted `subject_record` + empty `results`.
- HV19: parametrized over `EDGE_SCHEMA` keys.
- HV16: non-empty Client + fuzzy strategy — `neighbors_empty_hints` not invoked.
- Dot-key invariant test on rendered output.
- Optional `neighbors_v2` round-trip on post-flip graph (session `kuzu_graph` after SCHEMA PR-C) for HV2/HV6 — fail loud if fixture lacks post-flip shape.

## Tests for PR-D

Name tests `test_hints_hv{N}_*` matching propose §6 / §4 rows:

1. `test_hints_hv1_type_level_declares_client_requery`
2. `test_hints_hv2_method_http_calls_wrong_subject_kind`
3. `test_hints_hv3_method_async_calls_wrong_subject_kind`
4. `test_hints_hv4_producer_empty_async_out_brownfield_only`
5. `test_hints_hv5_producer_async_calls_wrong_direction`
6. `test_hints_hv6_client_http_calls_wrong_direction`
7. `test_hints_hv7_route_http_calls_wrong_direction`
8. `test_hints_hv8_method_exposes_empty_no_structural_hint`
9. `test_hints_hv9_method_declares_client_empty_no_structural_hint`
10. `test_hints_hv10_class_http_calls_wrong_subject_kind`
11. `test_hints_hv11_method_overrides_empty_no_structural_hint`
12. `test_hints_hv12_annotation_extends_empty_no_structural_hint` — assert per PR-A `EXTENDS` / `member_only` lock
13. `test_hints_hv13_client_empty_http_brownfield_only`
14. `test_hints_hv14_producer_empty_async_brownfield_only`
15. `test_hints_hv15_multi_edge_http_only_wrong_kind_for_http`
16. `test_hints_hv16_client_nonempty_http_fuzzy_hint_unchanged`
17. `test_hints_hv17_class_exposes_type_level_requery`
18. `test_hints_hv18_route_declares_wrong_subject_kind`
19. `test_hints_hv19_edge_schema_coverage_exists_trigger_per_edge`
20. `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints`
21. `test_hints_neighbors_empty_kind_check_template_removed` — grep/template absent
22. `test_hints_neighbors_v2_empty_post_flip_method_http_calls` — integration round-trip on post-flip graph (**required** once SCHEMA PR-C is on `master`; fail loud if session fixture lacks Client→Route shape)

**Regression:** `test_hints_neighbors_fuzzy_strategy_*` and v1 neighbors tests still pass; update `test_hints_neighbors_empty_with_edge_types_emits_kind_check` → expect new template family (rename to reflect v3 behavior).

## Definition of done (PR-D)

- [ ] `TPL_NEIGHBORS_EMPTY_KIND_CHECK` deleted; four v3 templates wired.
- [ ] `neighbors_empty_hints` follows fixed evaluation order; row 4 deduped.
- [ ] `neighbors_v2` passes `subject_record` + `requested_direction`.
- [ ] All named HV tests pass; HV19 coverage holds.
- [ ] HINTS-V3 propose `Status: locked` before merge.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.
- [ ] No `ONTOLOGY_VERSION` change.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Templates + helpers + `neighbors_empty_hints` | `mcp_hints.py` | Unit tests HV1–HV18 pass |
| 2 | Wire `generate_hints` empty branch | `mcp_hints.py` | Empty payloads emit hints |
| 3 | Neighbors payload | `mcp_v2.py` | subject_record loaded |
| 4 | `member_only` if missing | `java_ontology.py` | HV1/HV17 pass |
| 5 | HV19 + dot-key + regression | `tests/test_mcp_hints.py` | Full PR-D tests green |
| 6 | Docs | `README.md`, `server.py` | Copy matches behavior |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | PR-D merged before post-flip schema | High | Block on SCHEMA PR-C; HV2/HV3 integration tests |
| 2 | Missing `typical_traversals` key | Medium | HV19; PR-A contract |
| 3 | False-positive structural hints | Medium | HV8, HV9, HV11; three-step order |
| 4 | Hint noise under 5-cap | Low | Meta tier; HV15 bounded |
| 5 | `member_only` wrong on HTTP/ASYNC | Medium | Never True on Client/Producer endpoint edges |
| 6 | Fuzzy + brownfield duplicate | Low | Principle 4; HV4 vs HV16 |

# Out of scope

- New MCP tools or `neighbors` parameters (`direction` stays `in`|`out` only).
- Hints on `find` / `resolve` / `describe` (existing families stay).
- Per-row neighbors hints; confidence-based hints.
- Localization; hint caching.
- `EDGE_SCHEMA` / graph builder changes (except `member_only` backfill).
- Ontology version bump or re-index.

# Whole-plan done definition

1. Empty `neighbors` with wrong kind/direction/type-level subject emits EDGE_SCHEMA-driven hints per HV table.
2. `TPL_NEIGHBORS_EMPTY_KIND_CHECK` gone; v2 fuzzy hint unchanged on non-empty.
3. `neighbors_v2` supplies `subject_record` and `requested_direction` to hint generator.
4. HV19 passes; no dot-key labels in recommendations.
5. `propose/HINTS-V3-PROPOSE.md` moved to `propose/completed/` when PR-D merges.

# Tracking

- Artefacts: landed ([#160](https://github.com/HumanBean17/java-codebase-rag/pull/160))
- `PR-D`: _completed_

## Cursor handoff

[`plans/completed/AGENT-PROMPTS-HINTS-V3.md`](./AGENT-PROMPTS-HINTS-V3.md)
