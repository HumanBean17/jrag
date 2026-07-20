<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# HINTS-V3 — kind- and direction-aware empty-result hints driven by EDGE_SCHEMA

**Status**: **completed** — landed in [#160](https://github.com/HumanBean17/java-codebase-rag/pull/160) (plan: [`plans/completed/PLAN-HINTS-V3.md`](../../plans/completed/PLAN-HINTS-V3.md))
**Author**: Dmitriy Teriaev
**Date**: 2026-05-16

## TL;DR

- Replace the single generic empty-neighbors template `TPL_NEIGHBORS_EMPTY_KIND_CHECK = "0 results — check if the requested edge_types apply to this kind"` with a small family of kind- and direction-aware templates driven by `EDGE_SCHEMA` (introduced in `propose/completed/SCHEMA-V2-PROPOSE.md` §3.4).
- Each template fires by inspecting the subject node kind, the requested `direction`, and the requested `edge_types` against `EDGE_SCHEMA[edge].src` / `.dst` / `.typical_traversals` — no hardcoded edge-shape literals in `mcp_hints.py`.
- New emit-side input: hints v3 reads `EdgeSpec.brownfield_resolver_sourced` (backed by `BROWNFIELD_RESOLVER_STRATEGY_SET` from SCHEMA-V2 PR-A) to fire a distinct *"absence may mean unresolved, not absent"* hint on empty results. That complements (does not replace) the v2 `TPL_NEIGHBORS_FUZZY_STRATEGY` hint on non-empty results.
- **Propose gate** (SCHEMA-V2 Decision 30): merged to `master` ([#154](https://github.com/HumanBean17/java-codebase-rag/pull/154)) before SCHEMA-V2 PR-A. **Code** ships in SCHEMA-V2 PR-D after PR-A–C are on `master`.
- Re-index is already required by SCHEMA-V2 (`ONTOLOGY_VERSION` 13 → 14); HINTS-V3 does not bump it again.
- Goes away: `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (deleted). Stays: every existing v1/v2 template (DESCRIBE rollups, FIND, RESOLVE, fuzzy-strategy hint).
- Non-obvious constraint: hints v3 must never recommend a dot-key edge label as a `neighbors()` argument (carry-over from v2 propose §7.x). All template recommendations are checked against the canonical edge list.

## §1 — Frame

> Hints v3 is a thin translator from `EDGE_SCHEMA` to natural-language nudges. It owns no edge knowledge of its own.

The v1 empty-neighbors template is a placeholder: it tells the agent "your kind might be wrong" but doesn't tell it which kind, which direction, or what to call instead. SCHEMA-V2 makes that information mechanically derivable — `EDGE_SCHEMA[e].src`, `.dst`, `.typical_traversals` answer "what kinds attach to this edge?" and "what's the canonical traversal from a wrong-kind subject?". HINTS-V3 is what consumes that data at empty-result time.

The frame rules out three temptations:

- Hand-written per-edge templates ("for HTTP_CALLS, suggest DECLARES_CLIENT") — that's the bug v1 has. Knowledge lives in `EDGE_SCHEMA`, hints render it.
- Reasoning about why the result is empty (graph state, indexing, ranking) — out of scope for structural templates. Hints v3 handles structurally-impossible queries (wrong kind, wrong direction, type-vs-method-level) plus a separate brownfield-resolver-absence template (see Principle 8).
- Cross-edge composition planning (multi-hop suggestions beyond the single canonical traversal stored in `EDGE_SCHEMA`) — out of scope. One canonical traversal per (subject role, edge) tuple, sourced from `typical_traversals`.

## §2 — Design principles

1. **`EDGE_SCHEMA` is the only source of edge-shape knowledge.** No edge name, src/dst kind, or traversal string appears as a literal in `mcp_hints.py` outside of test fixtures.
2. **One template per dimension of mismatch.** Subject-kind mismatch, direction mismatch, type-vs-method-level mismatch, and brownfield-resolver absence are four distinct templates, not one polymorphic one.
3. **Hints v3 never recommends a dot-key edge label.** Generator output is filtered against the canonical edge list before emission.
4. **Fuzzy vs brownfield-resolver hints are branch-exclusive on the same `neighbors` call.** The v2 fuzzy hint fires only on **non-empty** results (per-row `strategy` in `FUZZY_STRATEGY_SET`). The v3 brownfield-absence hint fires only on **empty** results when `EdgeSpec.brownfield_resolver_sourced=True`. They never co-fire on one call.
5. **Templates carry the canonical traversal verbatim from `EDGE_SCHEMA[e].typical_traversals`.** No re-rendering or string editing of the traversal inside `mcp_hints.py`. Role-keyed entries (e.g. `type_subject`) are selected by a small helper; keys are defined in SCHEMA-V2 PR-A.
6. **Empty-result structural hints use `PRIORITY_META=1`.** Same priority as the v1 `TPL_NEIGHBORS_EMPTY_KIND_CHECK` and v2 `TPL_NEIGHBORS_FUZZY_STRATEGY` neighbours hints they sit beside. No new priority constant.
7. **Structural templates never fire on "graph happens to have no rows".** When kind, direction, and member-level are all correct, an empty result yields **no structural hint** (HV8, HV9, HV11).
8. **Brownfield-resolver absence is not a structural mismatch.** When kind and direction are correct but `brownfield_resolver_sourced=True`, empty results may still emit `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` — that is intentional (HV4, HV13, HV14) and does not violate Principle 7.

## §3 — The proposed surface

### §3.1 — Templates added

```python
# Replaces TPL_NEIGHBORS_EMPTY_KIND_CHECK.

TPL_NEIGHBORS_WRONG_SUBJECT_KIND = (
    "0 results — '{edge}' connects {src_kind} → {dst_kind}; "
    "this is a {subject_kind}. Try: {canonical_traversal}"
)

TPL_NEIGHBORS_WRONG_DIRECTION = (
    "0 results — '{edge}' is {src_kind} → {dst_kind}; "
    "you requested direction='{requested_dir}'. Try direction='{correct_dir}'."
)

TPL_NEIGHBORS_TYPE_LEVEL_REQUERY = (
    "0 results — '{edge}' lives on methods, not on {subject_kind}. "
    "Try: {canonical_traversal}"
)

TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED = (
    "edges on '{edge}' are emitted by the brownfield resolver — "
    "absence here may mean unresolved (no matching annotation/target), "
    "not absent from the codebase"
)
```

The fuzzy-strategy template `TPL_NEIGHBORS_FUZZY_STRATEGY` from v2 stays unchanged. `TPL_NEIGHBORS_EMPTY_KIND_CHECK` is deleted.

### §3.2 — Generator entry point

Hints v3 adds one generator function consumed by `neighbors`:

```python
def neighbors_empty_hints(
    *,
    subject_record: dict[str, Any],         # origin node row (see §3.6)
    requested_edge_types: list[str],
    requested_direction: Literal["in", "out"],
) -> list[tuple[int, str]]:
    """Emit at most one structural mismatch hint per requested edge, plus a
    brownfield-resolver hint if any requested edge is brownfield-resolved.
    Returns scored hints; caller merges with other sources and applies finalize_hint_list.
    """
```

The function:

1. Reads `EDGE_SCHEMA[edge]` for each requested edge type.
2. Resolves the subject's **node label** (`Symbol`, `Client`, `Route`, `Producer`) from `subject_record` (not `symbol_kind` alone).
3. For each edge, evaluates structural templates in **fixed order** (first match wins):
   - **Alien kind** — subject label matches **neither** `EdgeSpec.src` **nor** `EdgeSpec.dst` → `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` with `canonical_traversal` from `typical_traversals` (role key chosen by helper; see Decision 8).
   - **Wrong direction** — subject label matches the endpoint for the **opposite** direction (`out` expects `src`, `in` expects `dst`; opposite means matches `dst` on `out` or `src` on `in`) → `TPL_NEIGHBORS_WRONG_DIRECTION` with `correct_dir` set to the direction where the subject is a valid endpoint.
   - **Type-level requery** — subject is a `Symbol` with `symbol_kind ∈ _TYPE_SYMBOL_KINDS` and `EdgeSpec.member_only=True` → `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` with `canonical_traversal` from `typical_traversals["type_subject"]`.
4. Assigns `PRIORITY_META` to every structural template from step 3.
5. If any requested edge has `EdgeSpec.brownfield_resolver_sourced=True`, emits `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` once (deduped across edges) at `PRIORITY_META`. This runs even when step 3 emitted nothing (HV13/HV14).

The function does **not** emit the v2 fuzzy-strategy hint — that path stays in the existing non-empty-result branch (per-row `strategy`).

### §3.3 — Hint emission rules summary

| Order | Trigger | Template | Priority |
|---|---|---|---|
| 1 | Subject node label matches neither `EdgeSpec.src` nor `EdgeSpec.dst` | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` | `PRIORITY_META` |
| 2 | Subject label matches the opposite endpoint for the requested direction | `TPL_NEIGHBORS_WRONG_DIRECTION` | `PRIORITY_META` |
| 3 | Subject is a type-level `Symbol` and `EdgeSpec.member_only=True` | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` | `PRIORITY_META` |
| (parallel) | Any requested edge has `EdgeSpec.brownfield_resolver_sourced=True` on an empty result | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` | `PRIORITY_META` |

At most one of rows 1–3 fires per requested edge. Row 4 can co-fire with any of 1–3 (deduped once per output).

### §3.4 — Required `EdgeSpec` field additions (preview)

For hints v3 to do its job without literal edge-shape knowledge, `EdgeSpec` (defined in SCHEMA-V2 §3.4 / Appendix A) must carry one bit not strictly required by SCHEMA-V2 alone:

- `member_only: bool` — default `False`. Set `True` when the edge is only meaningfully queried from **method-level** `Symbol` rows (`symbol_kind ∈ {method, constructor}`), and a **type-level** `Symbol` (`class`, `interface`, `enum`, `record`, `annotation`) should get `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` instead of a kind-mismatch hint. Set on: `DECLARES_CLIENT`, `DECLARES_PRODUCER`, `EXPOSES`, `OVERRIDES`, `CALLS`. **Do not** set on post-flip `HTTP_CALLS` / `ASYNC_CALLS` (`Client`/`Producer` endpoints) — method subjects asking those edges hit row 1 (`WRONG_SUBJECT_KIND`) with the `member_subject` traversal from `typical_traversals`.

`member_only` is hint-engine-only. The DDL-consistency CI check does not assert it. Prefer landing the field in SCHEMA-V2 PR-A; PR-D adds it if PR-A does not.

### §3.5 — `typical_traversals` shape (PR-A contract)

SCHEMA-V2 PR-A finalizes `typical_traversals` as a **mapping** from subject-role key to traversal string, for example:

```python
typical_traversals={
    "type_subject": "neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['{edge}'])",
    "member_subject": "neighbors(['{id}'],'out',['DECLARES_CLIENT']) then neighbors(client_ids,'out',['HTTP_CALLS'])",
    "alien_subject": "...",  # per-edge; used by WRONG_SUBJECT_KIND
}
```

Hints v3 selects the key via a small helper (`type_subject` / `member_subject` / default). PR-A populates every edge; test HV19 asserts coverage.

### §3.6 — Neighbors hint payload (PR-D wiring)

Today `neighbors_v2` passes only `results` and `requested_edge_types` into `generate_hints`. PR-D **must** extend the payload:

```python
neigh_payload = {
    "success": True,
    "results": [...],
    "requested_edge_types": list(labels),
    "requested_direction": direction,       # Literal["in", "out"]
    "origin_id": origins[0],                # first origin when ids is a list
    "subject_record": <loaded node row>,    # from _load_node_record(g, origin_id, kind)
}
```

- **`direction`**: `neighbors` already requires `in` | `out` (no `any`); the generator mirrors that.
- **Multi-id requests**: when `ids` is a list, hint generation uses **`origins[0]`** only. Structural hints describe that subject; aggregated `results` may include hops from other origins (pre-existing behaviour). Document in `MCP_HINTS_FIELD_DESCRIPTION` if needed.
- **`generate_hints("neighbors", …)`** calls `neighbors_empty_hints` when `results` is empty and `requested_edge_types` is non-empty; merges returned pairs before `finalize_hint_list`.

### §3.7 — What does NOT change

- `TPL_DESCRIBE_*` family (v1): unchanged.
- `TPL_FIND_*`, `TPL_RESOLVE_*`, `TPL_SEARCH_WEAK`: unchanged.
- `TPL_NEIGHBORS_FUZZY_STRATEGY` (v2): unchanged.
- Priority constants (except empty-neighbours structural hints stay at `PRIORITY_META`), `finalize_hint_list`, `MCP_HINTS_FIELD_DESCRIPTION`: unchanged except PR-D may append one sentence on multi-id hint subject.
- The 5-hint output cap: unchanged.
- No new MCP tool arguments.

## §4 — Use-case re-walk

Each row references the SCHEMA-V2 use-case re-walk (§4 of that propose) where applicable. New rows are HV-prefixed.

| # | Use case | Subject | Request | Pre-v3 hint | Post-v3 hint |
|---|---|---|---|---|---|
| HV1 (= SCHEMA-V2 UC2) | Class-level subject, asks `DECLARES_CLIENT` outbound | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` | `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (generic) | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` — `canonical_traversal` from `typical_traversals["type_subject"]` |
| HV2 (= SCHEMA-V2 UC3) | Method-level subject, asks `HTTP_CALLS` outbound (post-flip) | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — `canonical_traversal` from `typical_traversals["member_subject"]` (DECLARES_CLIENT → HTTP_CALLS chain) |
| HV3 | Method-level subject, asks `ASYNC_CALLS` outbound (post-flip) | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['ASYNC_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — `member_subject` traversal (DECLARES_PRODUCER → ASYNC_CALLS) |
| HV4 (= SCHEMA-V2 UC15) | Producer subject, correct direction, empty graph | `Producer{}` | `neighbors([producer_id], 'out', ['ASYNC_CALLS'])` returning `[]` | n/a | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` only (no structural row; HV8-style) |
| HV5 (= SCHEMA-V2 UC17) | Producer subject, asks `ASYNC_CALLS` inbound | `Producer{}` | `neighbors([producer_id], 'in', ['ASYNC_CALLS'])` | n/a | `TPL_NEIGHBORS_WRONG_DIRECTION` — Producer matches `src`, not `dst`; row 2 |
| HV6 | Client subject, asks `HTTP_CALLS` inbound | `Client{}` | `neighbors([client_id], 'in', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_DIRECTION` — Client matches `src`, not `dst`; row 2 |
| HV7 | Route subject, asks `HTTP_CALLS` outbound | `Route{}` | `neighbors([route_id], 'out', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_DIRECTION` — Route matches `dst`, not `src`; row 2 |
| HV8 | Symbol method, asks `EXPOSES` outbound — not a controller | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['EXPOSES'])` returning `[]` | generic | **No structural hint** — row 3 does not apply (method-level subject); graph state |
| HV9 | Symbol method, asks `DECLARES_CLIENT` outbound — no client declared | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['DECLARES_CLIENT'])` returning `[]` | generic | **No structural hint** — structurally valid query |
| HV10 (= SCHEMA-V2 UC22) | Class-level subject, asks `HTTP_CALLS` outbound (post-flip) | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — class label `Symbol` matches neither `Client` nor `Route`; `alien_subject` / default traversal |
| HV11 | Method subject, asks `OVERRIDES` outbound, nothing to override | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['OVERRIDES'])` returning `[]` | generic | **No structural hint** |
| HV12 | Annotation symbol, asks `EXTENDS` outbound | `Symbol{symbol_kind=annotation}` | `neighbors([ann_id], 'out', ['EXTENDS'])` returning `[]` | generic | **No structural hint** when `member_only=False` and annotation is a valid `Symbol` endpoint; otherwise row 1 if PR-A excludes annotations from `EXTENDS.src` |
| HV13 | Client subject, asks `HTTP_CALLS` outbound — resolver found no route | `Client{}` | `neighbors([client_id], 'out', ['HTTP_CALLS'])` returning `[]` | generic | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` only |
| HV14 | Producer subject, asks `ASYNC_CALLS` outbound — unresolved broker | `Producer{}` | `neighbors([producer_id], 'out', ['ASYNC_CALLS'])` returning `[]` | n/a | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` only |
| HV15 | Method subject, asks both `HTTP_CALLS` and `DECLARES_CLIENT` outbound | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['HTTP_CALLS', 'DECLARES_CLIENT'])` | one generic hint | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` for `HTTP_CALLS` only; no hint for `DECLARES_CLIENT` (HV9) |
| HV16 | Caller-side subject, `HTTP_CALLS` returns edges with fuzzy strategy (post-flip) | `Client{}` | non-empty `neighbors([client_id], 'out', ['HTTP_CALLS'])` | v2 fuzzy hint | v2 fuzzy hint; `neighbors_empty_hints` not called |
| HV17 | Class-level subject, asks `EXPOSES` outbound | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['EXPOSES'])` | generic | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` — row 3 |
| HV18 | Route subject, asks `DECLARES` outbound | `Route{}` | `neighbors([route_id], 'out', ['DECLARES'])` returning `[]` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — Route matches neither Symbol endpoint; row 1 |
| HV19 | CI: `EDGE_SCHEMA` coverage | n/a | n/a | n/a | For **each** edge `e` in `EDGE_SCHEMA`, ∃ a `(subject_node_label, direction)` pair such that `neighbors_empty_hints` would emit **at least one** of rows 1–3 or row 4 for a synthetic empty result. Does **not** require every empty query to hint. |
| HV20 | Future edge added | varies | varies | hand-edit `EDGE_SCHEMA` | Hints follow schema automatically; HV19 fails CI until traversals + `member_only` are populated |

### Awkward cases surfaced

- **HV12** consumes whatever PR-A locks for `EXTENDS.src` (annotation eligibility).
- **HV15** per-edge fan-out is bounded by the 5-hint cap and brownfield dedupe.
- **HV13/HV14** are why row 4 exists: resolver-sourced edges with no emitted row have no `attrs.strategy` to drive the v2 fuzzy hint.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Per-edge bespoke prose | Road signs only; `MCP_HINTS_FIELD_DESCRIPTION` holds contract prose |
| Multi-hop planning beyond `typical_traversals` | One canonical traversal per role key; agent composes further |
| Graph-state reasoning in structural templates | Principle 7; brownfield row 4 is the sole empty-result exception |
| Dot-key edge labels in recommendations | v2 invariant; post-filter in PR-D tests |
| Hint caching | Pure function of payload + `EDGE_SCHEMA` |
| Edge-schema hints on `find` / `resolve` / `describe` | Those tools already have hint families |
| Localization | English only |

## §6 — Migration plan — 1 PR (= SCHEMA-V2 PR-D)

**Propose gates** (aligned with SCHEMA-V2 Decision 30):

- **Draft PR** (#154): must be merged to `master` before SCHEMA-V2 **PR-A** starts implementation.
- **Locked**: this propose's `Status` must be `locked` before SCHEMA-V2 **PR-D** merges.

**PR-D gates**: merges only after PR-A, PR-B, and PR-C are in `master` (post-flip `EDGE_SCHEMA`).

### PR-D — kind/direction-aware empty-result hints

**Title**: `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`

**Purpose**:

- Delete `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.
- Add the four templates in §3.1.
- Add `neighbors_empty_hints(...)` and `typical_traversal_for(...)` helper in `mcp_hints.py`.
- Extend `neighbors_v2` hint payload per §3.6; wire empty branch in `generate_hints`.
- Add `EdgeSpec.member_only` to `EDGE_SCHEMA` if PR-A did not.
- Dot-key edge-label post-filter + tests.

**Test summary** (`tests/test_mcp_hints.py`): HV1–HV19 by name (`test_hints_hv{N}_...`); explicit no-hint cases HV8, HV9, HV11, HV12 (when `member_only=False`); HV15 multi-edge; HV16 v2 fuzzy regression on non-empty; HV19 schema coverage; dot-in-edge-types invariant on rendered hints.

## §7 — Decisions taken (no longer open)

1. **`TPL_NEIGHBORS_EMPTY_KIND_CHECK` is deleted**, not extended.
2. **Four templates**: alien kind, wrong direction, type-level requery, brownfield-resolver absence.
3. **Structural evaluation order per edge**: alien kind → wrong direction → type-level (first match wins).
4. **Row 4 (brownfield) dedupes across requested edges** and may co-fire with a structural row.
5. **All new empty-neighbour templates use `PRIORITY_META=1`**, matching the v1 empty template they replace.
6. **`member_only` semantics** per §3.4; default `False`; never `True` on `Client`/`Producer` endpoint edges.
7. **Traversals come only from `typical_traversals`**; type-level uses `"type_subject"` key.
8. **`typical_traversals` is a role-keyed map** finalized in SCHEMA-V2 PR-A (§3.5).
9. **Brownfield membership is read via `EdgeSpec.brownfield_resolver_sourced`**, not by re-walking `BROWNFIELD_RESOLVER_STRATEGY_SET` in the empty path.
10. **`member_only=True` only on Symbol–Symbol (or Symbol–Route) edges** listed in §3.4; unit test in PR-A or PR-D.
11. **5-hint cap unchanged**; `finalize_hint_list` reused.
12. **Implementation is SCHEMA-V2 PR-D**; this propose is a separate doc PR.
13. **`mcp_hints.py` imports `EDGE_SCHEMA` from `java_ontology`** — no copy.
14. **No back-compat alias** for `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.
15. **Propose gate = merged to `master` before PR-A; `Status: locked` before PR-D** — matches SCHEMA-V2 Decision 30 (draft PR on GitHub is the vehicle; the file must be on `master`).

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Missing traversal for some `(edge, role)` | PR-A populates `typical_traversals`; HV19 CI |
| False-positive structural hints on valid empties | HV8, HV9, HV11; three-step order |
| Dot-key labels in hint text | Post-filter + test |
| `member_only` ambiguity on `EXTENDS` | Default `False`; HV12 |
| PR-D before post-flip schema | PR-D gated on PR-C |
| Brownfield vs fuzzy duplication | Principle 4; HV4/HV16 |
| Silent breakage on new edges | HV19 |

## Appendix A — Traceability

**Review-1 (2026-05-16)** — aligned with SCHEMA-V2 Decision 30 and current `mcp_hints.py` / `neighbors_v2`:

| Change | Why |
|---|---|
| Three-step structural order (alien → wrong direction → type-level) | HV5/HV6 were unreachable under old "check requested endpoint only" rule |
| `PRIORITY_META`, not `PRIORITY_LEAF_FOLLOWUP` | Matches shipped v1/v2 empty-neighbors priority |
| `TYPE_LEVEL_REQUERY` uses `{canonical_traversal}` only | Principle 1 — no `DECLARES` literal in `mcp_hints.py` |
| `member_only` scoped to Symbol method-level edges only | Removed contradiction with post-flip HTTP/ASYNC Client/Producer endpoints |
| §3.6 hint payload + multi-id rule | `neighbors_v2` does not pass subject/direction today |
| Propose gate: merged before PR-A, locked before PR-D | Was stricter than SCHEMA-V2 Decision 30; TL;DR "draft PR" clarified |
| HV16 uses `Client` + post-flip `HTTP_CALLS` | Method `Symbol` cannot have non-empty `ASYNC_CALLS` post-flip |
| Dropped `direction='any'` / HV18 replaced | API is `in` \| `out` only |
| HV19 clarified as ∃ coverage per edge, not ∀ empty queries | Compatible with HV8/HV9/HV11 |
| Principle 8 for brownfield empty hints | Explicit exception to structural-only framing |
| Removed duplicate template appendix | Templates live in §3.1 only |

**Cross-propose references**:

- `propose/completed/SCHEMA-V2-PROPOSE.md` §3.4, §3.11, Decision 28–30, PR-D §6 (locked via #151).
- `propose/completed/HINTS-V2-PROPOSE.md` — fuzzy hint, dot-key invariant (unchanged).
- `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` — v1 catalogue except `TPL_NEIGHBORS_EMPTY_KIND_CHECK` deleted.
