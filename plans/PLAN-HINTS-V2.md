# Plan: Hints v2 (`resolve` + neighbors fuzzy-strategy)

Status: **active (planning)**. This plan implements
[`propose/HINTS-V2-PROPOSE.md`](../propose/HINTS-V2-PROPOSE.md).

Depends on: **none** (v1 hints on `search` / `find` / `describe` / `neighbors` and the
`resolve` tool are already landed on `master`).

## Goal

- **PR-A:** Add `hints: list[str]` and `resolved_identifier: str | None` to
  `ResolveOutput`; implement the four locked resolve templates in `mcp_hints.py`;
  wire `resolve_v2` so success-true responses populate echoed fields and hint payload
  (including route/client seeds); cover every resolve use case in §4 with named tests
  plus one `resolve_v2` round-trip.
- **PR-B:** Add `FUZZY_STRATEGY_SET` to `java_ontology.py`; extend the `neighbors`
  branch of `generate_hints` to emit one meta-tier hint when any result edge carries a
  fuzzy `attrs.strategy`; cover UC6–UC10 and UC17 with named tests plus one
  `neighbors_v2` round-trip on a graph that exposes a fuzzy edge.

## Principles (do not relitigate in review)

- **Add rules; do not change v1 shape.** Existing four outputs keep their `hints`
  contract. v2 only adds `ResolveOutput` fields and new catalog rows.
- **Strategy over confidence.** Neighbors fuzzy signal uses categorical
  `attrs.strategy ∈ FUZZY_STRATEGY_SET`, not a confidence threshold.
- **Cap discipline unchanged.** ≤5 hints, dedupe by rendered string, v1 §7.12 priority
  (v2 rows are **meta-tier**, lowest).
- **No per-result hints on `neighbors`.** One fuzzy-strategy hint per output max;
  agents read per-row `attrs.strategy` in the same payload.
- **Pure hint generation.** `generate_hints` reads only its payload dict (echoed output
  fields + call-site plumbing); no graph I/O, no LLM.
- **120-char drop-on-overflow.** No truncation or ellipsis on resolve templates; overlong
  rendered strings are omitted (UC2b).
- **Concrete filter seeds only.** Route/client `status: none` hints require non-empty
  `path_prefix_seed` / `target_service_seed`; suppress when parsing yields nothing.
- **Additive for clients.** No deprecation shims; clients that ignore `hints` are
  unchanged.
- **No ontology / re-index.** Query-time MCP surface only; `ontology_version` stays
  **13**.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-A | `ResolveOutput` + resolve catalog + `resolve_v2` plumbing (`resolved_identifier`, seeds, `hints`) | **No** | Payload plumbing vs `find_v2` hybrid; wildcard/overflow suppression; `success=False` vs validation-`none`; template string drift vs real `search`/`find` params | Resolve unit payloads (§4 UC1–UC5, UC16), `generate_hints("resolve", …)`, `resolve_v2` round-trip | PR-B |
| PR-B | `FUZZY_STRATEGY_SET` in `java_ontology.py`, neighbors fuzzy template + `_any_fuzzy_strategy`, README/server copy | **No** | Set drift vs brownfield pipeline (issue #147); empty-results vs fuzzy both meta-tier; `attrs` shape on `Edge` dumps | Crafted neighbors payloads (UC6–UC10, UC17), regression UC11, `neighbors_v2` round-trip | PR-A |

**Landing order:** **PR-A → PR-B**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `resolved_identifier` | Set on every `success=True` response to post-validation trimmed identifier; `None` on `success=False`. |
| Resolve hints fire on | `status: none` and `status: many` only; `status: one` → `hints: []`. |
| `hint_kind` default | `None` → symbol branch (`search(query=…)` template). |
| Wildcards in identifier | `*` / `?` in `resolved_identifier` suppress resolve hints (UC2c); do not emit `search(query='*')`. Unified success assembler sets echo + runs `generate_hints` on wildcard paths too. |
| `FUZZY_STRATEGY_SET` location | `java_ontology.py`; `mcp_hints.py` imports it. Locked members per propose §3.2. |
| `layer_b_ann` vs `layer_b_fqn` | `_ann` is primary (not in fuzzy set); `_fqn` is fuzzy. |
| Truncated candidate cap | Hint says `{n} candidates` with `n = len(candidates)`; no `truncated` flag on output (§5 carve-out). |
| Issue #147 CI invariant | **Out of scope for this plan** unless already on `master`; land separately or as a small follow-up chore PR that classifies every `resolution_strategy=` literal against ontology sets. |
| v1 catalog | Unchanged except new rows; locked strings in propose Appendix A. |

---

# PR-A — `resolve` hints

## File-by-file changes

### 1. `mcp_v2.py`

- Extend `ResolveOutput` with:
  - `resolved_identifier: str | None = None` — post-validation trimmed identifier;
    `None` when `success=False`.
  - `hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)`.
- Preserve `extra="forbid"`.
- **Refactor: unified success assembler** — Today `resolve_v2` wildcard identifiers
  (`*` / `?`) early-return via `_resolve_build_output([])` and never set echo fields.
  Replace scattered success returns with one helper (e.g.
  `_resolve_finalize_success(trimmed, hint_kind, matches, graph) -> ResolveOutput`) that:
  1. Builds `status` / `node` / `candidates` / `message` (same semantics as today).
  2. Always sets `resolved_identifier=trimmed` on `success=True` (wildcard, empty-match,
     one, many — all paths).
  3. Builds the hint payload and sets `hints=generate_hints("resolve", payload)`.
  Wildcard `status: none` must **not** bypass the assembler; hints stay `[]` via
  `generate_hints` suppression (UC2c), not a separate shortcut.
- **Hint payload on every success path** (including wildcard and empty-match):
  - Echo: `status`, `resolved_identifier`, `candidates`.
  - Plumbed (not on model): `hint_kind` (request param — today only used for search
    routing), optional `path_prefix_seed`, `target_service_seed`.
  - Seeds: compute on every success return using existing parsers
    (`_resolve_parse_route_method_path`, `_resolve_parse_microservice_route`, and client
    `target` / `target path` split consistent with `_resolve_client_candidates`) — match
    propose §3.1.2; parser logic stays here, not in `mcp_hints.py`.
- `success=False` paths (validation error, exceptions): `hints=[]`,
  `resolved_identifier=None` (Decision §7.15 — validation rejection gets no v2 hint).

### 2. `mcp_hints.py`

- Extend `generate_hints` `output_kind` `Literal` to include `"resolve"`.
- Add verbatim templates and constants from propose Appendix A:
  `TPL_RESOLVE_NONE_TRY_SEARCH`, `TPL_RESOLVE_NONE_TRY_FIND_ROUTE`,
  `TPL_RESOLVE_NONE_TRY_FIND_CLIENT`, `TPL_RESOLVE_MANY_TIGHTEN`,
  `_RESOLVE_HINT_MAX_CHARS = 120`, `_RESOLVE_WILDCARDS`.
- Implement `output_kind == "resolve"` branch per propose Appendix A wire-up
  (`status: one` → `[]`; `many` → tighten when `len(candidates) > 1`; `none` →
  kind-specific templates with suppression rules).
- All v2 resolve hints use `PRIORITY_META`.

### 3. `server.py` (minimal)

- Update `resolve` tool `description=` to mention successful responses may include
  advisory `hints` (same tone as other v2 tools). No stdout changes.

### 4. `README.md`

- Extend MCP v2 **response extras** paragraph: `resolve` returns `hints` on success;
  document `resolved_identifier` echo; link to
  [`propose/HINTS-V2-PROPOSE.md`](../propose/HINTS-V2-PROPOSE.md) for v2 catalog
  (keep v1 link for the original four tools).

## Tests for PR-A

Pure `generate_hints("resolve", …)` tests (craft payloads; no DB required unless noted):

1. `test_hints_resolve_status_one_emits_empty` — UC1
2. `test_hints_resolve_status_none_symbol_suggests_search` — UC2
3. `test_hints_resolve_status_none_symbol_drop_on_overflow` — UC2b (identifier length
   so rendered hint > 120)
4. `test_hints_resolve_status_none_symbol_wildcard_suppressed` — UC2c
5. `test_hints_resolve_status_none_route_suggests_find` — UC3
6. `test_hints_resolve_status_none_route_no_seed_suppressed` — UC3b
7. `test_hints_resolve_status_none_client_suggests_find` — UC4
8. `test_hints_resolve_status_none_client_no_seed_suppressed` — UC4b
9. `test_hints_resolve_status_many_emits_tighten` — UC5 / UC16 (`n` in substring)
10. `test_hints_resolve_status_many_truncated_cap_wording` — UC16b (`n=10` wording)
11. `test_hints_resolve_payload_missing_identifier_suppressed` — UC16c

Integration:

12. `test_hints_resolve_v2_round_trip` — `resolve_v2` against `kuzu_graph`: assert
    `hints` present for a known `status: none` symbol identifier and empty for a known
    `status: one`; assert `resolved_identifier` echoed on success (including wildcard
    `status: none` with `hints == []`); assert validation failure yields `hints == []`
    and `resolved_identifier is None`. Reuse existing discovery helpers in
    `tests/test_mcp_hints.py` (`_route_id`, `_client_id`, symbol/route Cypher patterns)
    — fail loud with `pytest.fail` if fixture data missing; no unconditional `skip`.

Optional hygiene (same PR if quick):

13. `test_hints_resolve_templates_rendered_length_leq_120` — parametrize resolve
    templates with realistic placeholders.

**Assertion style:** presence + key substrings (e.g. `search(query=`, `find(kind='route'`,
`candidates — tighten`), not whole-string equality — per propose Risk §8.

## Definition of done (PR-A)

- [ ] `ResolveOutput` exposes `hints` and `resolved_identifier` per contract.
- [ ] All four resolve rules implemented; `status: one` and validation-failure paths
  emit `hints: []`.
- [ ] Named tests above exist and pass.
- [ ] README + `server.py` resolve copy mention `hints`.
- [ ] `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` green.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add model fields | `mcp_v2.py` | Pydantic schema validates |
| 2 | Implement resolve branch + templates | `mcp_hints.py` | Unit tests 1–11 pass |
| 3 | Add unified success assembler; thread `trimmed`, `hint_kind`, seeds into payload on every `success=True` path (wildcard included) | `mcp_v2.py` | Wildcard + lookup paths echo `resolved_identifier`; round-trip passes |
| 4 | Docs / server description | `README.md`, `server.py` | Copy matches behavior |
| 5 | Add tests | `tests/test_mcp_hints.py` | All PR-A tests green |

---

# PR-B — neighbors fuzzy-strategy hint

## File-by-file changes

### 1. `java_ontology.py`

- Add `FUZZY_STRATEGY_SET` frozenset (locked contents per propose §3.2):
  `layer_c_source`, `layer_b_fqn`, `phantom`, `chained_receiver`,
  `overload_ambiguous`, `implicit_super`.
- Export in `__all__`.

### 2. `mcp_hints.py`

- Import `FUZZY_STRATEGY_SET` from `java_ontology`.
- Add `TPL_NEIGHBORS_FUZZY_STRATEGY` (verbatim propose Appendix A).
- Add `_any_fuzzy_strategy(edges: list[dict[str, Any]]) -> bool` inspecting
  `edge["attrs"]["strategy"]`.
- Extend existing `neighbors` branch: when `results` non-empty and any fuzzy strategy
  present, append one `PRIORITY_META` fuzzy hint (after empty-result check; UC11
  unchanged when `results` empty).
- Update module docstring to reference v2 propose for resolve + neighbors additions.

### 3. `README.md`

- Extend MCP v2 hints paragraph: neighbors may emit fuzzy-strategy meta hint; point to
  v2 propose for catalog detail.

### 4. `server.py` (optional, minimal)

- If `neighbors` tool description does not already mention strategy attrs, add one line
  that `attrs.strategy` on edges indicates resolution quality (no new tool params).

## Tests for PR-B

Pure `generate_hints("neighbors", …)` (craft `results` with `attrs.strategy`):

1. `test_hints_neighbors_fuzzy_strategy_layer_c_source_emits` — UC6
2. `test_hints_neighbors_fuzzy_strategy_annotation_absent` — UC7
3. `test_hints_neighbors_fuzzy_strategy_calls_phantom_emits` — UC8
4. `test_hints_neighbors_declares_no_strategy_attrs_empty` — UC9
5. `test_hints_neighbors_multi_origin_fuzzy_emits_once` — UC10 (single hint string)
6. `test_hints_neighbors_layer_a_meta_no_fuzzy_hint` — UC17

**UC11 regression (existing v1 test — do not add a duplicate):** after PR-B, re-run
`test_hints_neighbors_empty_with_edge_types_emits_kind_check` unchanged; only touch it if
branch ordering breaks the empty-result path.

Integration:

7. `test_hints_neighbors_fuzzy_strategy_neighbors_v2_round_trip` — call `neighbors_v2`
   on a graph edge known to carry a fuzzy `strategy`. Reuse or extend helpers in
   `tests/test_mcp_hints.py` (`_method_declares_client`, `_class_symbol_id`, Cypher
   discovery for `e.strategy IN FUZZY_STRATEGY_SET`) or Tier-2 `call_graph_smoke` session
   for `phantom` / `implicit_super` on `CALLS`. Fail loud if fixture lacks a fuzzy edge;
   no unconditional `pytest.skip`.

## Definition of done (PR-B)

- [ ] `FUZZY_STRATEGY_SET` lives in `java_ontology.py` and is imported by `mcp_hints.py`.
- [ ] Fuzzy hint fires at most once per neighbors output; coexists with empty-result hint
  only on disjoint conditions.
- [ ] Named tests + round-trip pass.
- [ ] README updated; v1 neighbors behavior unchanged when no fuzzy strategies.
- [ ] `ruff` + default `pytest tests -v` green.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add ontology set | `java_ontology.py` | Importable constant |
| 2 | Template + `_any_fuzzy_strategy` + neighbors branch | `mcp_hints.py` | Unit tests 1–6 pass |
| 3 | Round-trip neighbors test | `tests/test_mcp_hints.py` | Test 7 passes |
| 4 | README / optional server | `README.md`, `server.py` | Docs match |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Resolve hint duplicates generic `message` prose | Low | Intentional dual channel; hint embeds verbatim `resolved_identifier` (Decision §7.22). Tests use substrings, not full equality. |
| 2 | Missing payload plumbing → silent `hints: []` | Medium | Unified success assembler; `test_hints_resolve_payload_missing_identifier_suppressed` + round-trip asserts `resolved_identifier` on success (wildcard included). |
| 2b | Wildcard early-return bypasses assembler | Medium | PR-A refactor: no direct `_resolve_build_output([])` return without `resolved_identifier` + `generate_hints`. |
| 3 | Fuzzy hint noise in brownfield-heavy repos | Low | Meta-tier drops first under cap; single terse template. |
| 4 | `FUZZY_STRATEGY_SET` drifts from pipeline literals | Medium | Issue #147 follow-up; document locked set in ontology; code review checks new strategies. |
| 5 | PR-B merged before PR-A | Low | Enforce landing order; PR-B does not touch `ResolveOutput`. |
| 6 | Template/param drift (`search`, `find`, `neighbors`) | Medium | Reuse existing v1 patterns; round-trip tests import tool signatures where v1 already does. |

# Out of scope

- Ontology version bump or re-index.
- Per-row hints on `neighbors` or `ResolveCandidate`.
- Confidence-threshold hints; distinguishing `phantom` vs `layer_c_source` in rendered text.
- `truncated: bool` on `ResolveOutput`.
- Hints for `status: none` from validation rejection (`success=False`).
- Changes to v1 search/find/describe catalog rows (except neighbors branch extension in PR-B).
- Structured `next_actions`, `hints_version`, LLM-generated hints.
- Issue #147 CI classification invariant (unless explicitly added as a separate PR).
- Special-casing `tests/bank-chat-system/` in production hint logic.

# Whole-plan done definition

1. `resolve` success responses expose `hints` + `resolved_identifier` per propose §3.1;
   resolve catalog covered by named tests and round-trip.
2. `neighbors` emits the fuzzy-strategy meta hint when any result edge has
   `attrs.strategy ∈ FUZZY_STRATEGY_SET`; ontology set is the single source of truth.
3. README documents v2 behavior; v1 hints on the original four tools are unchanged.
4. Default test suite green without heavy env vars.
5. `propose/HINTS-V2-PROPOSE.md` moved to `propose/completed/` (whole effort landed).

# Tracking

- `PR-A`: _pending_
- `PR-B`: _pending_
- `#147` (strategy classification CI invariant): _pending_ chore — out of PR-A/B scope;
  file after PR-B or in parallel.

## Cursor handoff

Per-PR execution prompts:
[`plans/CURSOR-PROMPTS-HINTS-V2.md`](CURSOR-PROMPTS-HINTS-V2.md).
