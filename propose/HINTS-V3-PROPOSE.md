# HINTS-V3 — kind- and direction-aware empty-result hints driven by EDGE_SCHEMA

**Status**: draft
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-16

## TL;DR

- Replace the single generic empty-neighbors template `TPL_NEIGHBORS_EMPTY_KIND_CHECK = "0 results — check if the requested edge_types apply to this kind"` with a small family of kind- and direction-aware templates driven by `EDGE_SCHEMA` (introduced in `propose/SCHEMA-V2-PROPOSE.md` §3.4).
- Each template fires by inspecting the subject node kind, the requested `direction`, and the requested `edge_types` against `EDGE_SCHEMA[edge].src` / `.dst` / `.typical_traversals` — no hardcoded edge-shape literals in `mcp_hints.py`.
- New emit-side input: hints v3 also reads `BROWNFIELD_RESOLVER_STRATEGY_SET` (added in SCHEMA-V2 PR-A) to fire a distinct *"absence may mean unresolved, not absent"* hint that complements (does not replace) the v2 `TPL_NEIGHBORS_FUZZY_STRATEGY` hint.
- Migration: 1 PR (= SCHEMA-V2 PR-D), gated on this propose locking and on SCHEMA-V2 PR-A through PR-C all merging first. Re-index is already required by SCHEMA-V2 (`ONTOLOGY_VERSION` 13 → 14); HINTS-V3 does not bump it again.
- Goes away: `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (deleted). Stays: every existing v1/v2 template (DESCRIBE rollups, FIND, RESOLVE, fuzzy-strategy hint).
- Non-obvious constraint: hints v3 must never recommend a dot-key edge label as a `neighbors()` argument (carry-over from v2 propose §7.x). All template recommendations are checked against the canonical edge list.

## §1 — Frame

> Hints v3 is a thin translator from `EDGE_SCHEMA` to natural-language nudges. It owns no edge knowledge of its own.

The v1 empty-neighbors template is a placeholder: it tells the agent "your kind might be wrong" but doesn't tell it which kind, which direction, or what to call instead. SCHEMA-V2 makes that information mechanically derivable — `EDGE_SCHEMA[e].src`, `.dst`, `.typical_traversals` answer "what kinds attach to this edge?" and "what's the canonical traversal from a wrong-kind subject?". HINTS-V3 is what consumes that data at empty-result time.

The frame rules out three temptations:

- Hand-written per-edge templates ("for HTTP_CALLS, suggest DECLARES_CLIENT") — that's the bug v1 has. Knowledge lives in `EDGE_SCHEMA`, hints render it.
- Reasoning about why the result is empty (graph state, indexing, ranking) — out of scope. Hints v3 only handles structurally-impossible queries (wrong kind, wrong direction, wrong edge for kind) and brownfield-resolver absence.
- Cross-edge composition planning (multi-hop suggestions beyond the single canonical traversal stored in `EDGE_SCHEMA`) — out of scope. One canonical traversal per (subject_kind, edge, direction) tuple, sourced from `typical_traversals`.

## §2 — Design principles

1. **`EDGE_SCHEMA` is the only source of edge-shape knowledge.** No edge name, src/dst kind, or traversal string appears as a literal in `mcp_hints.py` outside of test fixtures.
2. **One template per dimension of mismatch.** Subject-kind mismatch, direction mismatch, type-vs-method-level mismatch, and brownfield-resolver absence are four distinct templates, not one polymorphic one.
3. **Hints v3 never recommends a dot-key edge label.** Generator output is filtered against the canonical edge list before emission.
4. **Hints v3 reads `BROWNFIELD_RESOLVER_STRATEGY_SET` and `FUZZY_STRATEGY_SET` independently.** Membership in the fuzzy set fires the v2 fuzzy hint (unchanged); membership in the broader resolver set fires the new "may be unresolved" hint. The two can both fire when the edge is fuzzy-resolved by the brownfield resolver — they are not mutually exclusive.
5. **Templates carry the canonical traversal verbatim from `EDGE_SCHEMA[e].typical_traversals`.** No re-rendering or string editing of the traversal inside `mcp_hints.py`.
6. **Empty-result hints are advisory, capped at 5 total per output (v1 invariant), and priority-ordered.** Kind/direction hints sit at `PRIORITY_LEAF_FOLLOWUP=2` like the v1 template they replace.
7. **A subject node kind that already matches the edge schema does not get a kind-mismatch hint, even on empty result.** Hints v3 only fires when there is a *structural* reason to expect the query to fail — never on "the graph happens to have no rows."

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
    "Try: neighbors(['{id}'],'out',['DECLARES']) then "
    "neighbors(member_ids,'{direction}',['{edge}'])"
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
    subject_record: dict[str, Any],         # the node looked up by id
    requested_edge_types: list[str],
    requested_direction: Literal["in", "out", "any"],
) -> list[tuple[int, str]]:
    """Emit at most one structural mismatch hint per requested edge, plus a
    brownfield-resolver hint if any requested edge is brownfield-resolved.
    Returns scored hints; caller merges with other sources and applies finalize_hint_list.
    """
```

The function:

1. Reads `EDGE_SCHEMA[edge]` for each requested edge type.
2. Compares `subject_record`'s node label + `data.kind` against `EdgeSpec.src` (for `direction="out"`) or `.dst` (for `direction="in"`); `direction="any"` checks both.
3. Picks the first applicable mismatch template per edge (subject-kind > direction > type-level), substitutes from the spec, and assigns priority `PRIORITY_LEAF_FOLLOWUP=2`.
4. If any requested edge has `EdgeSpec.brownfield_resolver_sourced=True`, emits `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` once (deduped if multiple such edges) at priority `PRIORITY_LEAF_FOLLOWUP=2`.

The function does **not** emit the v2 fuzzy-strategy hint — that path stays in the existing non-empty-result hint generator (since `FUZZY_STRATEGY_SET` membership is per-row, not per-edge-schema).

### §3.3 — Hint emission rules summary

| Trigger | Template | Priority |
|---|---|---|
| Subject's node kind is not in `EdgeSpec.src` (for `direction="out"`) or `.dst` (for `direction="in"`) | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` | 2 |
| Subject's node kind matches the opposite endpoint of the requested direction | `TPL_NEIGHBORS_WRONG_DIRECTION` | 2 |
| Subject is a Symbol with `symbol_kind ∈ _TYPE_SYMBOL_KINDS` and the requested edge lives on methods (i.e., `src` or `dst` is `Symbol` but `EdgeSpec.member_only=True` — see §3.4) | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` | 2 |
| Any requested edge has `EdgeSpec.brownfield_resolver_sourced=True` | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` | 2 |

At most one of the first three fires per edge (in the order shown). The brownfield hint can co-fire with any of them.

### §3.4 — Required `EdgeSpec` field additions (preview)

For hints v3 to do its job without literal edge-shape knowledge, `EdgeSpec` (defined in SCHEMA-V2 §3.4 / Appendix A) must carry one bit not strictly required by SCHEMA-V2 alone:

- `member_only: bool` — True iff the edge attaches only to `Symbol` rows whose `symbol_kind ∈ {method, constructor}` (e.g., `DECLARES_CLIENT`, `DECLARES_PRODUCER`, `EXPOSES`, `HTTP_CALLS` post-flip via Client which is declared by methods, `ASYNC_CALLS` post-flip via Producer).

`member_only` is a hint-engine-only field. The DDL-consistency CI check does not assert it (kuzu has no notion). It is set in `EDGE_SCHEMA` and read by `neighbors_empty_hints`. **This is the only `EdgeSpec` field added solely for hints v3.**

If SCHEMA-V2's `EdgeSpec` does not yet include `member_only`, this propose's PR-D adds it.

### §3.5 — What does NOT change

- `TPL_DESCRIBE_*` family (v1, type/method/route/client describe rollups): unchanged.
- `TPL_FIND_*`, `TPL_RESOLVE_*`, `TPL_SEARCH_WEAK`: unchanged.
- `TPL_NEIGHBORS_FUZZY_STRATEGY` (v2 fuzzy hint): unchanged.
- Priority constants, `finalize_hint_list`, the `MCP_HINTS_FIELD_DESCRIPTION` schema description: unchanged.
- The 5-hint output cap: unchanged.
- No new MCP tool, no new tool argument. `neighbors` already accepts the inputs hints v3 needs.

## §4 — Use-case re-walk

Each row references the SCHEMA-V2 use-case re-walk (§4 of that propose) where applicable. New rows are HV-prefixed.

| # | Use case | Subject | Request | Pre-v3 hint | Post-v3 hint |
|---|---|---|---|---|---|
| HV1 (= SCHEMA-V2 UC2) | Class-level subject, asks `DECLARES_CLIENT` outbound | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` | `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (generic) | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` — `neighbors(['{id}'],'out',['DECLARES'])` then `neighbors(member_ids,'out',['DECLARES_CLIENT'])` |
| HV2 (= SCHEMA-V2 UC3) | Method-level subject, asks `HTTP_CALLS` outbound (post-flip) | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['HTTP_CALLS'])` | `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (generic) | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — `'HTTP_CALLS' connects Client → Route; this is a Symbol. Try: neighbors(['{id}'],'out',['DECLARES_CLIENT']) then neighbors(client_ids,'out',['HTTP_CALLS'])` |
| HV3 | Method-level subject, asks `ASYNC_CALLS` outbound (post-flip) | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['ASYNC_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` pointing at `DECLARES_PRODUCER` then `ASYNC_CALLS` |
| HV4 (= SCHEMA-V2 UC15) | Producer subject, no resolved topic, asks for downstream | `Producer{}` | `neighbors([producer_id], 'out', ['ASYNC_CALLS'])` returning `[]` | n/a (Producer didn't exist) | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` — "ASYNC_CALLS edges are brownfield-resolver-emitted; absence may mean unresolved" |
| HV5 (= SCHEMA-V2 UC17) | Producer subject, asks `ASYNC_CALLS` inbound | `Producer{}` | `neighbors([producer_id], 'in', ['ASYNC_CALLS'])` | n/a | `TPL_NEIGHBORS_WRONG_DIRECTION` — `'ASYNC_CALLS' is Producer → Route; you requested direction='in'. Try direction='out'.` |
| HV6 | Client subject, asks `HTTP_CALLS` inbound | `Client{}` | `neighbors([client_id], 'in', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_DIRECTION` — same shape as HV5 |
| HV7 | Route subject, asks `HTTP_CALLS` outbound | `Route{}` | `neighbors([route_id], 'out', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_DIRECTION` |
| HV8 | Symbol method, asks `EXPOSES` outbound — empty because not a controller method | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['EXPOSES'])` returning `[]` | generic | **No hint** — subject kind matches `EXPOSES.src=Symbol`, direction is correct, edge is method-level and subject is a method. The empty result is *graph state*, not structural. (Principle 7.) |
| HV9 | Symbol method, asks `DECLARES_CLIENT` outbound — empty because the method has no `@CodebaseHttpClient` | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['DECLARES_CLIENT'])` returning `[]` | generic | **No hint** — kind+direction+member-level all correct. Graph state, not structural. |
| HV10 (= SCHEMA-V2 UC22) | Class-level subject, asks `HTTP_CALLS` outbound (post-flip) | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['HTTP_CALLS'])` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — `'HTTP_CALLS' connects Client → Route; this is a Symbol(class).` Plus, because the subject is a *type*-kind Symbol, the canonical traversal from `EDGE_SCHEMA["HTTP_CALLS"].typical_traversals[type_subject]` is substituted. |
| HV11 | Method subject, asks `OVERRIDES` outbound, no superclass method to override | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['OVERRIDES'])` returning `[]` | generic | **No hint** — structural query is fine. |
| HV12 | Annotation symbol, asks `EXTENDS` outbound | `Symbol{symbol_kind=annotation}` | `neighbors([ann_id], 'out', ['EXTENDS'])` returning `[]` | generic | If `EXTENDS.src ∋ Symbol` but `member_only=False` and kind is fine: no hint. If schema records that annotations cannot extend, `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` fires. (Depends on `EXTENDS` `EdgeSpec` finalization; documented in SCHEMA-V2 PR-A.) |
| HV13 | Client subject, asks `HTTP_CALLS` outbound — empty because the Client's `target_path` did not match any route (brownfield resolver failed) | `Client{}` | `neighbors([client_id], 'out', ['HTTP_CALLS'])` returning `[]` | generic | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED`. Subject kind + direction + member-level are all correct, but `EdgeSpec.brownfield_resolver_sourced=True`. |
| HV14 | Producer subject, asks `ASYNC_CALLS` outbound — empty because the Producer's broker is unknown | `Producer{}` | `neighbors([producer_id], 'out', ['ASYNC_CALLS'])` returning `[]` | n/a | `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` |
| HV15 | Method subject, asks both `HTTP_CALLS` and `DECLARES_CLIENT` outbound | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['HTTP_CALLS', 'DECLARES_CLIENT'])` returning HTTP `[]`, DECLARES_CLIENT `[]` | one generic hint | One `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` hint for `HTTP_CALLS`; **no hint** for `DECLARES_CLIENT` (HV9 logic). Two requested edges, one structural mismatch, one structurally-fine empty. |
| HV16 | Method subject, asks `ASYNC_CALLS` outbound; mixed brownfield-strategy edges actually present (non-empty result) | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'out', ['ASYNC_CALLS'])` returning N edges | v2 fuzzy hint fires if any edge has fuzzy strategy | Same as v2 — `neighbors_empty_hints` is not invoked on non-empty results. v2 fuzzy hint logic unchanged. |
| HV17 | Class-level subject, asks `EXPOSES` outbound | `Symbol{symbol_kind=class}` | `neighbors([class_id], 'out', ['EXPOSES'])` | generic | `TPL_NEIGHBORS_TYPE_LEVEL_REQUERY` — class is wrong member level for `EXPOSES`, suggest `DECLARES` then re-query. |
| HV18 | Mixed-direction `direction='any'` request on a non-fitting kind | `Symbol{symbol_kind=method}` | `neighbors([method_id], 'any', ['HTTP_CALLS'])` returning `[]` | generic | `TPL_NEIGHBORS_WRONG_SUBJECT_KIND` — neither `HTTP_CALLS.src` nor `.dst` matches `Symbol`; the canonical traversal from `typical_traversals` is suggested. |
| HV19 | `EDGE_SCHEMA` reader cross-check from a CI test | n/a | n/a | n/a | Test asserts every edge in `EDGE_SCHEMA` is reachable via at least one of the four templates above; no edge falls through to "no applicable hint generator". |
| HV20 | Future edge added (e.g. `INHERITS_FROM_FRAMEWORK`) | varies | varies | requires hand-edit | Adds entry to `EDGE_SCHEMA`; hints v3 picks it up automatically with no code change. |

### Awkward cases surfaced

- **HV12** depends on the final shape of `EdgeSpec` for `EXTENDS` — whether annotations are excluded from `src`. The propose doesn't lock that; SCHEMA-V2 PR-A is the right place. HINTS-V3 just consumes whatever PR-A produces.
- **HV15** demonstrates that the per-edge fan-out is not a problem in practice because the 5-hint cap and the dedup pass collapse redundant brownfield-resolver hints.
- **HV13/HV14** are the case the v2 fuzzy hint doesn't cover: the brownfield resolver tried, found nothing, emitted no edge — there is no `attrs.strategy` to inspect because there is no edge. The dedicated v3 brownfield template handles this asymmetry.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Generate per-edge bespoke prose (e.g. "Did you mean DECLARES_CLIENT? Here's why HTTP works this way…") | Out of scope. Hints are road signs, not tutorials. `MCP_HINTS_FIELD_DESCRIPTION` describes the contract. |
| Cross-edge composition planner (multi-hop suggestions beyond the canonical traversal in `typical_traversals`) | Out of scope. One canonical traversal per (edge, subject-kind, direction) lives in `EDGE_SCHEMA`. Composition belongs to the agent. |
| Reason about graph state (ranking, indexing, freshness) at empty-result time | Out of scope. Hints v3 only handles structurally-impossible queries and brownfield-resolver absence. |
| Add new templates that recommend dot-key edge labels (e.g. `DECLARES.DECLARES_CLIENT`) as `neighbors()` arguments | Forbidden by v2 invariant (`MCP_HINTS_FIELD_DESCRIPTION` last sentence). Generator output is checked. |
| Cache hint outputs across queries | No state. Hints are pure functions of subject + request + `EDGE_SCHEMA`. |
| Add a hint emission for `find` / `resolve` / `describe` empty results that's edge-schema-aware | Out of scope. Those tools already have dedicated hint families (`TPL_FIND_EMPTY_RESOLVE`, `TPL_RESOLVE_NONE_*`, `TPL_DESCRIBE_*`). |
| Localized hint text | Out of scope. English only, consistent with the rest of `mcp_hints.py`. |

## §6 — Migration plan — 1 PR (= SCHEMA-V2 PR-D)

**Merge gate**: this propose must be **locked** before SCHEMA-V2 PR-A merges (Decision 30 in SCHEMA-V2-PROPOSE.md). The implementation PR (PR-D) must merge **after** SCHEMA-V2 PR-A, PR-B, and PR-C are all in master, because the templates substitute `src_kind`/`dst_kind` from the post-flip `EDGE_SCHEMA`.

### PR-D — kind/direction-aware empty-result hints

**Title**: `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`

**Purpose**:

- Delete `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.
- Add the four templates listed in §3.1.
- Add `EdgeSpec.member_only: bool` to `EDGE_SCHEMA` entries (or fold into PR-A — see §7 Decision 6).
- Add `neighbors_empty_hints(...)` generator in `mcp_hints.py`.
- Wire it into the `neighbors` empty-result branch in `mcp_v2.py`.
- Add a dot-key edge label filter (assertion or post-filter) to enforce the v2 invariant.

**Test summary**: named scenarios in `tests/test_mcp_hints.py` covering HV1, HV2, HV3, HV4, HV5, HV6, HV7, HV8 (no-hint case), HV10, HV13, HV15, HV17, HV18, HV19; v2-regression scenarios asserting `TPL_NEIGHBORS_FUZZY_STRATEGY` still fires on non-empty fuzzy-resolved results; `MCP_HINTS_FIELD_DESCRIPTION` invariant test that no emitted hint string contains a dot in an edge-label position.

## §7 — Decisions taken (no longer open)

1. **`TPL_NEIGHBORS_EMPTY_KIND_CHECK` is deleted.** It is replaced by the family in §3.1, not extended.
2. **The four mismatch dimensions are subject-kind, direction, type-vs-method-level, and brownfield-resolver absence.** One template each.
3. **At most one of the first three templates fires per requested edge.** Order: subject-kind > direction > type-level.
4. **`TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` can co-fire with any of the first three, but is deduped across all requested edges.**
5. **Priority of all four new templates is `PRIORITY_LEAF_FOLLOWUP=2`** — same as the v1 template they replace. No new priority constant.
6. **`EdgeSpec.member_only: bool` is added to `EDGE_SCHEMA`.** Strictly hint-engine-only field. CI DDL-consistency check ignores it. It is preferable to land this field in SCHEMA-V2 PR-A (alongside other `EdgeSpec` fields) rather than in PR-D, but PR-D will add it if PR-A does not.
7. **Canonical traversal strings come verbatim from `EDGE_SCHEMA[e].typical_traversals`.** Hints v3 does not synthesize traversal strings.
8. **`typical_traversals` may be keyed by subject role** (e.g. `type_subject`, `member_subject`) where the canonical traversal differs by subject kind. Exact key set is finalized by SCHEMA-V2 PR-A.
9. **Hints v3 reads `BROWNFIELD_RESOLVER_STRATEGY_SET` via `EdgeSpec.brownfield_resolver_sourced`,** not by enumerating the set itself. The set is only used inside the per-row `_any_fuzzy_strategy`-style helper for the v2 fuzzy hint.
10. **No `member_only=True` edge between two non-Symbol nodes is permitted in `EDGE_SCHEMA`.** This invariant is asserted by a unit test in PR-A or PR-D.
11. **The 5-hint output cap (v1 invariant) is unchanged.** `finalize_hint_list` is reused.
12. **HINTS-V3 ships as SCHEMA-V2 PR-D, not as a separate code PR sequence.** The propose lives in its own PR but the implementation is one of the four schema-v2 code PRs.
13. **`mcp_hints.py` imports `EDGE_SCHEMA` from `java_ontology`.** No copy. No selective re-export.
14. **No back-compat alias for `TPL_NEIGHBORS_EMPTY_KIND_CHECK`.** Per locked repo rule "Breaking changes allowed; no active users."

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| `EDGE_SCHEMA` doesn't carry enough information for a useful canonical traversal on every (edge, subject-kind) pair | `typical_traversals` is keyed by subject role. Test HV19 asserts every edge in `EDGE_SCHEMA` produces at least one applicable template + traversal for at least one realistic subject-kind. |
| A template fires spuriously on a structurally-correct empty (HV8/HV9/HV11 false positives) | Principle 7 + named scenarios HV8, HV9, HV11 in PR-D test suite explicitly cover this. The mismatch templates require *structural* impossibility — kind, direction, or member-level wrong — never just "empty rows". |
| Dot-key edge labels leak into a hint recommendation despite the v2 invariant | Post-filter on rendered hints checks for `\.` in any single-quoted edge_types list and raises in tests. |
| `EdgeSpec.member_only` becomes ambiguous for edges like `EXTENDS` that can connect any Symbol kind | `member_only=False` is the default. We only set `True` on edges whose `src` and `dst` are unambiguously method-level (`DECLARES_CLIENT`, `DECLARES_PRODUCER`, `EXPOSES`, `OVERRIDES`, `CALLS`). |
| Hints v3 lands before SCHEMA-V2 PR-A/B/C, references nonexistent `Client`/`Producer` shapes | Gating: PR-D merge-blocks behind PR-C (declared in this propose §6 and in SCHEMA-V2 §6). |
| `TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED` duplicates `TPL_NEIGHBORS_FUZZY_STRATEGY` on edges that are both brownfield-resolved and fuzzy-strategy | They cover different axes: one fires on empty results from a brownfield-resolved edge; the other on non-empty results that contain fuzzy-strategy rows. Empty + non-empty are exclusive branches of `neighbors` post-processing; both hints cannot fire on the same call. Test HV4 + HV16 cover the two branches. |
| Future edge additions break HINTS-V3 silently | Test HV19 (every edge in `EDGE_SCHEMA` has at least one template path) is a CI invariant. Adding an edge to `EDGE_SCHEMA` without a covering template causes the test to fail. |

## Appendix A — Concrete template strings (Python)

```python
# In mcp_hints.py, replacing the section currently containing TPL_NEIGHBORS_EMPTY_KIND_CHECK.

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
    "Try: neighbors(['{id}'],'out',['DECLARES']) then "
    "neighbors(member_ids,'{direction}',['{edge}'])"
)

TPL_NEIGHBORS_BROWNFIELD_RESOLVED_MAYBE_UNRESOLVED = (
    "edges on '{edge}' are emitted by the brownfield resolver — "
    "absence here may mean unresolved (no matching annotation/target), "
    "not absent from the codebase"
)
```

## Appendix B — Traceability

This is a first-draft propose; no revisions yet. If a reviewer changes the design, this section will list **what stayed unchanged** and **what changed and why**.

**Cross-propose references**:
- Consumes `EDGE_SCHEMA` from `propose/SCHEMA-V2-PROPOSE.md` §3.4 (locked).
- Consumes `BROWNFIELD_RESOLVER_STRATEGY_SET` from `propose/SCHEMA-V2-PROPOSE.md` §3.11 / Decision 28 (locked).
- Implements `propose/SCHEMA-V2-PROPOSE.md` §3.12 (preview) and PR-D §6 (gating).
- Builds on `propose/completed/HINTS-V2-PROPOSE.md` §7.x (5-hint cap, dot-key edge-label invariant, fuzzy-strategy hint) — unchanged.
- Builds on `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` Appendix A (v1 catalogue) — unchanged except `TPL_NEIGHBORS_EMPTY_KIND_CHECK` is deleted.
