# HINTS-V2 — extend hints to `resolve` and to edge-attribute-driven `neighbors` signals

**Status**: approved (plan: [`plans/PLAN-HINTS-V2.md`](../plans/PLAN-HINTS-V2.md); move to `propose/completed/` when PR-A and PR-B land)
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-16

## TL;DR

- Extend the `hints` contract — landed in #144 for `search` / `find` / `describe` / `neighbors` — to cover two gaps a real-world agent trace surfaced.
- Add `hints: list[str]` to `ResolveOutput`. The v1 surface scoped `resolve` out because the tool didn't exist yet; it ships now (#137) and `status: none` / `status: many` are high-value road-sign moments currently delivered only in prose `message`.
- Add edge-attribute-driven hints to `neighbors`. v1's only neighbors rule is the empty-result `0 results — check if the requested edge_types apply to this kind`; the trace showed a `DECLARES_CLIENT` call returning 3 **clients** with `strategy: layer_c_source` (a brownfield fallback) and zero hints. v1 deliberately did not commit to edge-attribute or per-row signals — v2 introduces them for the first time.
- v2 hints are **strategy-categorical**, not confidence-thresholded — strategy is a categorical edge attribute with a known taxonomy, no threshold to tune.
- Migration: 2 PRs. PR-A adds `hints` field + v1-shape rules to `resolve`. PR-B adds `neighbors` fuzzy-edge hints (one new template) plus extends `mcp_hints.py` rule set. The "documentation-grade, not programmatic-dispatch" rule (v1 §7.15) stays binding; no per-result hint expansion on `neighbors`.

## §1 — Frame: hints describe edge-shape and missing-result drama, not "what to do next"

v1 locked: **hints are documentation-grade road signs about what the output contains and what an agent might overlook.** The frame rules out two things v2 must also avoid:

- **Per-result follow-up hints** ("describe each of these 3 routes"). That bloats the cap and overlaps with `describe`'s own hints.
- **Threshold-calibrated quality scores on edges**. `confidence` is a float and tuning a threshold is calibration debt. Strategy is categorical and stable.

v2 extends the surface (a new tool, `resolve`) and adds one more edge-attribute-driven trigger (strategy ∈ fuzzy set on any edge in `neighbors` results). Both fit inside the v1 frame.

## §2 — Design principles

1. **Add rules; do not change shape**. `hints: list[str]` already exists on four outputs. v2 adds the same field to `ResolveOutput` and adds entries to `mcp_hints.py`'s catalog — no contract change for existing tools.
2. **Strategy over confidence**. v2 fuzzy-edge signal is the strategy enum, not a confidence threshold. Strategy is categorical and aligned with the brownfield layer pipeline.
3. **Cap discipline unchanged**. ≤ 5 hints per output, dedupe by rendered string, same priority ordering as v1 §7.12.
4. **No per-result hints on `neighbors`**. v1's "documentation-grade, not programmatic-dispatch" principle stays binding. `neighbors` does not emit one hint per row.
5. **`resolve` hints fire on missing or ambiguous landings**. The high-value `resolve` moments are `status: none` (no match) and `status: many` (ambiguity). `status: one` emits nothing — the agent has its answer.
6. **Hints stay pure**. Hint generation remains a pure function of its payload — echoed output fields plus the same kind of request-context plumbing `find_v2` already uses for `kind` / `filter`. v2 reads `output.results[].attrs.strategy` for `neighbors` and `output.status` / `output.resolved_identifier` / `output.candidates` (plus plumbed `hint_kind` / seeds) for `resolve`; no new graph reads, no LLM calls.
7. **Triggers spell out the enum**. The fuzzy-strategy enum lives in `java_ontology.py` (Decision §7.19); resolve-template literals live in `mcp_hints.py`. "fallback" is never used as a generic hand-wavy term.
8. **Additive for clients**. Clients ignoring `hints` see no behavior change. ResolveOutput gains a field; existing fields keep their semantics.

## §3 — Proposed surface changes

### §3.1 `ResolveOutput` gains `hints` and `resolved_identifier`

```python
class ResolveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: ResolveStatus              # "one" | "many" | "none"
    node: NodeRef | None = None
    candidates: list[ResolveCandidate] = Field(default_factory=list)
    message: str | None = None
    resolved_identifier: str | None = None  # echo of post-validation identifier; None on success=False
    hints: list[str] = Field(default_factory=list, description=MCP_HINTS_FIELD_DESCRIPTION)
```

Two new fields: `hints` (the road-sign field) and `resolved_identifier` (the post-validation, trimmed identifier echoed from the request — set on every `success=True` response, `None` on validation-failure / `success=False`). `extra="forbid"` is preserved.

`resolved_identifier` mirrors v1 §7.18's discipline: pagination hints read `output.limit` on `FindOutput`, not call kwargs. v2 resolve hints read `output.resolved_identifier`, not call kwargs. `generate_hints` remains a pure function of its payload (mirroring `find_v2`'s hybrid: most fields echo from the output object; `hint_kind` and the optional seeds in §3.1.2 are request-context plumbed into the payload by the call site, the same way `find_v2` plumbs `kind` and `filter`).

### §3.1.1 Hint payload plumbing contract (resolve)

`generate_hints("resolve", payload)` reads four values from the payload dict — a hybrid of output-field echoes and request-context plumbing, mirroring how `find_v2` already plumbs `kind` and `filter`:

- `payload["status"]` — echoed from `ResolveOutput.status` (`"one" | "many" | "none"`).
- `payload["resolved_identifier"]` — echoed from `ResolveOutput.resolved_identifier`.
- `payload["candidates"]` — echoed from `ResolveOutput.candidates`.
- `payload["hint_kind"]` — request-context plumbed in by the call site (not on `ResolveOutput`); allowed values: `None | "symbol" | "route" | "client"`. The optional route/client seeds in §3.1.2 are plumbed the same way.

The `resolve_v2` call site is required to populate the payload with these fields before calling `generate_hints`. If `resolved_identifier` is missing or empty on a `status: none` response, `generate_hints` suppresses the hint (Decision §7.14). `hint_kind` may legitimately be `None` (default branch is symbol-shape, Decision 3).

### §3.1.2 Optional route/client identifier seeds

For route/client `status: none`, the rendered hint includes a concrete filter fragment derived from the identifier (Decision §7.17). The call site computes these seeds with the same parsers `resolve_v2` already uses (`_resolve_parse_route_method_path`, `_resolve_parse_microservice_route`) and plumbs them into the payload:

- `payload["path_prefix_seed"]` — for route hints: a path prefix extracted from the identifier, or `None` if no parse matched.
- `payload["target_service_seed"]` — for client hints: a service token extracted from the identifier, or `None` if no parse matched.

If the relevant seed is `None`, the route/client hint is suppressed (no placeholder ellipsis ever renders). Parser logic stays in `mcp_v2.py`; `generate_hints` consumes the seed string verbatim.

### §3.2 New `mcp_hints.py` entries (catalog excerpts)

**Resolve rules**:

| Rule | Trigger | Template |
|---|---|---|
| `resolve_none_try_search` | `status == "none"` and `hint_kind in {None, "symbol"}` and `resolved_identifier` is non-empty and contains no wildcards (`*`/`?`) | `no match — try search(query='{identifier}') for ranked fuzzy lookup` |
| `resolve_none_try_find_route` | `status == "none"` and `hint_kind == "route"` and `path_prefix_seed` is non-empty | `no match — try find(kind='route', filter={{path_prefix: '{seed}'}})` |
| `resolve_none_try_find_client` | `status == "none"` and `hint_kind == "client"` and `target_service_seed` is non-empty | `no match — try find(kind='client', filter={{target_service: '{seed}'}})` |
| `resolve_many_tighten` | `status == "many"` and `len(candidates) > 1` | `{n} candidates — tighten identifier or pick a candidate by id` |

All templates render to ≤ 120 chars **after substitution** per v1 §7.6. The search template embeds `resolved_identifier` verbatim; if substitution exceeds 120 chars, the hint is dropped at the cap check (Decision §7.18). No truncation, no ellipsis — agents need verbatim identifiers to compose the next call.

**Neighbors fuzzy-edge rule**:

| Rule | Trigger | Template |
|---|---|---|
| `neighbors_fuzzy_strategy_present` | Any edge in `results` has `attrs.strategy ∈ FUZZY_STRATEGY_SET` | `some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row` |

`FUZZY_STRATEGY_SET` is a closed taxonomy and **lives in `java_ontology.py`**, not `mcp_hints.py` (Decision §7.19). `mcp_hints.py` imports it. The set's contents (locked):

```python
# in java_ontology.py
FUZZY_STRATEGY_SET = frozenset({
    # brownfield route/client layers (sourced from build_ast_graph.py _ROUTE_LAYER_RANK):
    "layer_c_source",     # extracted from source text — heuristic, lowest brownfield rank
    "layer_b_fqn",        # FQN-pattern heuristic — guesses route shape from naming, no annotation evidence
    # CALLS edge resolution strategies (sourced from build_ast_graph.py CALLS-builder):
    "phantom",            # synthetic edge to unresolved receiver (confidence=0.0)
    "chained_receiver",   # receiver chain not resolved (confidence=0.0)
    "overload_ambiguous", # multiple overloads matched (confidence varies)
    "implicit_super",     # walked up the type hierarchy implicitly
})
```

**`layer_b_ann` vs `layer_b_fqn`** (Decision §7.20): both are brownfield-pipeline rank-1 and rank-4 in `_ROUTE_LAYER_RANK`. `_ann` keys on **explicit annotation code** present in the source — it has evidence. `_fqn` keys on **naming patterns** — it has no evidence beyond an identifier shape. `_fqn` is the lowest-rank brownfield fallback; `_ann` is reliable.

Strategies **not** in the set (treated as reliable primary paths): `layer_a_meta`, `layer_b_ann`, `annotation`, `codebase_route`, `codebase_client`, `annotated_explicit`, builtin/exact-match strategies. Drift between the brownfield pipeline and this set is prevented by the CI classification invariant (issue #147).

### §3.3 Priority placement

Two new priorities slot into the v1 §7.12 ordering. The full ordering becomes:

| Tier | Class | Examples |
|---|---|---|
| 4 (highest) | `DECLARES.*` type rollups | v1 type-method clients-via-members |
| 3 | `OVERRIDDEN_BY.*` rollups | v1 method overriders |
| 2 | Leaf follow-ups | v1 method/route/client leaves |
| 1 (lowest) | Meta-hints | v1 page-full, search-weak, neighbors-empty, **v2 fuzzy-strategy, v2 resolve-rules** |

v2 hints are meta-tier — they're advisory commentary on the result, not navigational follow-ups. They lose first when the cap binds.

## §4 — Use-case re-walk

Cases tagged **v2** are the new behavior; others verify v1 still works and v2 doesn't regress it.

| # | Use case | Status | Calls / hint observed |
|---|---|---|---|
| UC1 | Agent calls `resolve('com.foo.Bar#baz')`, gets `status: one` | v1 unchanged | `hints: []` (status: one emits nothing) |
| UC2 | Agent calls `resolve('com.foo.Bar#nonExistent')`, gets `status: none` | **v2 new** | `hints: ["no match — try search(query='com.foo.Bar#nonExistent') for ranked fuzzy lookup"]` |
| UC2b | Long FQN like the trace identifier (~70 chars); rendered hint exceeds 120 chars | **v2 new** | `hints: []` (drop-on-overflow, Decision §7.18; prose `message` still informs the agent) |
| UC2c | Identifier contains `*` wildcard | **v2 new** | `hints: []` (wildcard suppressed; agent should use `search` directly, Decision §7.21) |
| UC3 | Agent calls `resolve('POST /v1/operator/session/update', hint_kind='route')`, no match; parser extracts `/v1/operator/session/update` as path_prefix_seed | **v2 new** | `hints: ["no match — try find(kind='route', filter={path_prefix: '/v1/operator/session/update'})"]` |
| UC3b | Same as UC3 but parser fails to extract a path_prefix_seed | **v2 new** | `hints: []` (no concrete filter seed → no hint, Decision §7.17) |
| UC4 | Agent calls `resolve('smartcare-assign-chat', hint_kind='client')`, no match; parser extracts `smartcare-assign-chat` as target_service_seed | **v2 new** | `hints: ["no match — try find(kind='client', filter={target_service: 'smartcare-assign-chat'})"]` |
| UC4b | Agent calls `resolve('foo', hint_kind='client')`, no match; parser yields no service seed | **v2 new** | `hints: []` (no seed) |
| UC5 | Agent calls `resolve('open')` (short name), gets `status: many` with 7 candidates | **v2 new** | `hints: ["7 candidates — tighten identifier or pick a candidate by id"]` |
| UC6 | Agent calls `neighbors(method_id, 'out', ['DECLARES_CLIENT'])`, gets 3 **clients** all with `strategy: layer_c_source` (the trace case) | **v2 new** | `hints: ["some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"]` |
| UC7 | Agent calls `neighbors(method_id, 'out', ['DECLARES_CLIENT'])`, gets 3 **clients** all with `strategy: annotation` | v1 unchanged | `hints: []` (no fuzzy strategy present) |
| UC8 | Agent calls `neighbors(method_id, 'out', ['CALLS'])`, results include `phantom`/`chained_receiver` on at least one edge | **v2 new** | `hints: ["some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"]` (fires once by construction) |
| UC9 | Agent calls `neighbors(class_id, 'out', ['DECLARES'])`, gets 4 children, no strategy attrs on DECLARES edges | v1 unchanged | `hints: []` (DECLARES doesn't carry fuzzy strategies) |
| UC10 | Agent calls `neighbors([id1, id2], 'out', ['CALLS'])`, results include phantom edges from id1 only | **v2 new** | `hints: ["some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"]` (fires once, attr present on any edge) |
| UC11 | `neighbors` returns empty result | v1 unchanged | `hints: ["0 results — check if the requested edge_types apply to this kind"]` |
| UC12 | `describe(type_id)` with `DECLARES.DECLARES_CLIENT > 0` | v1 unchanged | type-clients-via-members hint fires |
| UC13 | `find(kind='symbol', filter={fqn_prefix:'com.x.Y'})` empty | v1 unchanged | resolve-fallback hint fires |
| UC14 | `find` page full | v1 unchanged | page-full hint fires |
| UC15 | `search` page-full + tight score band | v1 unchanged | search-weak hint fires |
| UC16 | `resolve` returns `status: many` with 2 candidates, both exact_fqn (different services) | **v2 new** | `hints: ["2 candidates — tighten identifier or pick a candidate by id"]` |
| UC16b | `resolve` returns `status: many` truncated at `_RESOLVE_CANDIDATE_CAP = 10` (real underlying count higher) | **v2 new** | `hints: ["10 candidates — tighten identifier or pick a candidate by id"]` (no "+" suffix; ambiguity acknowledged, see Risk §8) |
| UC16c | `resolve` returns `status: none` but `resolved_identifier` is missing from hint payload (plumbing bug) | **v2 new** | `hints: []` (suppressed by Decision §7.14; tests catch this) |
| UC17 | Agent observes a `neighbors` result with all `strategy: layer_a_meta` and `confidence: 1.0` — the well-resolved happy path | v1 unchanged | `hints: []` (no fuzzy strategy) |

### Awkward cases surfaced

- **UC8 + UC10 collapse**: when a multi-id `neighbors` call mixes fuzzy and non-fuzzy edges, the single fuzzy-strategy hint fires once for the whole output. This is correct (cap discipline, dedupe-by-rendered-string), but the agent loses per-row information. **Mitigation**: the hint's rendered string explicitly says "check attrs.strategy on each row" — pointing the agent to the per-edge `attrs` it already has in the same payload. v2 stays documentation-grade.
- **UC2 vs UC3 vs UC4 disambiguation**: when `hint_kind` is `None` and the identifier could be symbol-or-route-or-client, we default to symbol-shape. A future v3 could try harder; v2 prefers a single clear hint over fan-out.
- **UC16 with 10+ candidates**: `_RESOLVE_CANDIDATE_CAP = 10` already truncates `resolve` output. The hint says "10 candidates"; the agent already knows from context that "10" might mean "10 or more." Acceptable as-is.

No missing primitives surfaced. Surface lock candidate.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Per-result follow-up hints on `neighbors` | v1 §7.15 frame: documentation-grade, not programmatic-dispatch. Would spam the cap. |
| Confidence-threshold templates | Categorical strategy is enough and avoids calibration debt. `confidence` stays in `attrs` for any downstream tool that wants it. |
| Distinguishing `phantom` from `layer_c_source` in the rendered string | One unified "fuzzy/brownfield" hint stays at meta-tier and stays terse. The agent reads `attrs.strategy` to disambiguate. |
| Hints inside `ResolveCandidate` rows | Hints are output-level only (cap discipline depends on it). The per-candidate `reason` field already discriminates. |
| Hints for `status: one` resolve | Nothing to road-sign; the agent has its single answer. |
| Search-quality hints beyond v1's structural rule | Trace did not surface a `search` gap. Defer. |
| Hints for the legacy V1 surface | V1 MCP is retired (#142 / per master). N/A. |
| Programmatic-dispatch protocol on hints | Hints remain plain strings. v3 is the place to discuss any structured form, if a real trace demands it. |
| Truncated-vs-exact disambiguation in `resolve many` hint | Would require a `truncated: bool` on `ResolveOutput`. Out of scope for a hints propose; file a separate propose if a real trace shows agents getting misled by the ambiguity. |
| Hints for `status: none` arising from validation rejection | That path is already documented in prose `message`; agents that ignore validation messages won't be helped by an extra hint string. |

## §6 — Migration plan — 2 PRs

### PR-A — `resolve` hints

**Title**: `feat(hints): add hints field and rules to ResolveOutput`

**Purpose**: Add `hints: list[str]` to `ResolveOutput`; extend `mcp_hints.py` to handle `output_kind == "resolve"` with the four rules in §3.2; wire the call site in `mcp_v2.py`'s `resolve_v2` to populate `hints` on success-true outputs.

**Test summary**: named scenarios in `tests/test_mcp_hints.py` covering every resolve UC row in §4 (UC1, UC2, UC2b, UC2c, UC3, UC3b, UC4, UC4b, UC5, UC16, UC16b, UC16c). Plus a round-trip test: `resolve_v2` end-to-end on a fixture that returns each of the three `status` values, asserting the hints field is populated correctly and that seed-suppression / wildcard-suppression / 120-char-overflow paths produce `hints: []`.

### PR-B — `neighbors` fuzzy-edge hint

**Title**: `feat(hints): emit fuzzy-strategy hint when neighbors results carry brownfield/fallback edges`

**Purpose**: Extend `mcp_hints.py`'s `output_kind == "neighbors"` branch to inspect `results[].attrs.strategy` against `FUZZY_STRATEGY_SET`; add the single new template.

**Test summary**: named scenarios in `tests/test_mcp_hints.py` covering UC6–UC10 and UC17. Plus a round-trip test that builds a small graph with one `layer_c_source` client and one `annotation` client on `DECLARES_CLIENT` edges, calls `neighbors`, and asserts the hint fires only for the fuzzy case.

## §7 — Decisions taken (no longer open)

1. **`hints` field added to `ResolveOutput` with the same semantics as v1's four outputs.** Default `default_factory=list`. Description constant reused (`MCP_HINTS_FIELD_DESCRIPTION`).
2. **Resolve hints fire only on `status: none` and `status: many`.** `status: one` emits nothing.
3. **Resolve `none` hint family branches on `hint_kind`** (`symbol` / `route` / `client`) with one template per kind. Default branch (symbol) when `hint_kind` is `None`.
4. **Resolve `many` hint is a single template with candidate-count interpolation, where `{n} = len(candidates)`.** No per-candidate breakdown. When the candidate list was truncated at `_RESOLVE_CANDIDATE_CAP = 10`, the hint says "10 candidates" — v2 does not distinguish "exactly 10" from "truncated at 10" (see Risk §8 and §5 carve-out).
5. **Neighbors fuzzy-edge rule emits exactly one hint per output by construction.** The wire-up appends a single `TPL_NEIGHBORS_FUZZY_STRATEGY` entry when `_any_fuzzy_strategy(results)` is true, regardless of how many fuzzy edges are present. Dedupe-by-rendered-string in `finalize_hint_list` is belt-and-suspenders, not the primary mechanism.
6. **`FUZZY_STRATEGY_SET` is locked as**: `{"layer_c_source", "layer_b_fqn", "phantom", "chained_receiver", "overload_ambiguous", "implicit_super"}`. Strategies outside this set are treated as primary/reliable for hint purposes. A CI-enforced classification invariant (issue #147) prevents drift by failing builds when a new `resolution_strategy=` literal appears in the brownfield pipeline and is not classified in the ontology (Decision §7.19 places the set in `java_ontology.py`).
7. **Strategy is the v2 fuzzy-edge signal; confidence is not used in v2.** A future v3 may add a confidence-band hint if real traces demand it.
8. **No per-result hints on `neighbors`.** v1 §7.15 frame stays binding; the cap and dedupe rules already enforce this.
9. **All v2 hints live at the meta priority tier.** They lose first when the cap binds. v1's `DECLARES.*` and `OVERRIDDEN_BY.*` rollup hints continue to win.
10. **Hint generation stays pure.** `generate_hints` reads only the payload dict (echoed output fields plus request-context plumbing per §3.1.1 / §3.1.2); no graph access, no LLM calls. Same hybrid as `find_v2`.
11. **`extra="forbid"` is preserved on `ResolveOutput`.** Adding `hints` does not relax the model config.
12. **Breaking-change posture is "additive for ignorant clients."** Clients that already ignore `hints` on the other four outputs ignore it on `resolve` too. No deprecation aliases needed (no active users; per repo rules).
13. **Catalog templates are verbatim strings in `mcp_hints.py`.** Same convention as v1.
14. **Resolve hint payload plumbing is required at the call site.** `resolve_v2` must populate `status`, `resolved_identifier`, `hint_kind`, `candidates`, and the optional `path_prefix_seed` / `target_service_seed` in the hint payload. If `resolved_identifier` is missing or empty on a `status: none` response, `generate_hints` suppresses the hint rather than rendering a degraded template (e.g. `try search(query='')`). `hint_kind` is allowed to be `None` (defaults to symbol branch per Decision 3). The `many` hint does not depend on identifier/hint_kind. Tests assert plumbing is present.
15. **`status: none` from validation rejection is out of scope.** `_resolve_validate_identifier` rejects empty/whitespace identifiers before any lookup; that path has its own `message` and gets `hints: []`. v2 hints fire only after validation succeeds (the "well-formed but no match" case).
16. **Candidate-cap truncation is not surfaced in the hint.** When `len(candidates) == _RESOLVE_CANDIDATE_CAP` the hint says "10 candidates" — same wording as "exactly 10." Adding a `truncated: bool` to `ResolveOutput` is a separate propose; v2's two new `ResolveOutput` fields (`hints`, `resolved_identifier`) do not address truncation.
17. **Route/client `none` hints render concrete filter fragments, not ellipsis placeholders.** v1 §7.6 demands road-sign discipline: hints must be one concrete call-shape with no prose, ellipsis, or placeholders. v2 derives `path_prefix_seed` / `target_service_seed` from the identifier via the same parsers `resolve_v2` already uses, and the hint embeds the seed verbatim. If parsing yields no seed, the hint is suppressed.
18. **Templates obey v1 §7.6's 120-char rule on the rendered string.** Drop-on-overflow: if substitution produces a string longer than 120 chars, the hint is not emitted. No truncation of identifier or seed — the agent needs verbatim values. UC2b documents this for the search template; tests enforce.
19. **`FUZZY_STRATEGY_SET` lives in `java_ontology.py`.** Closed vocabularies belong with the ontology, not in tool-surface modules. `mcp_hints.py` imports the set. The CI classification invariant (#147) scans the brownfield pipeline against the ontology, not against `mcp_hints.py`.
20. **`layer_b_ann` is primary, `layer_b_fqn` is fuzzy** (despite both being "layer B"). `_ann` keys on explicit annotation code with evidence; `_fqn` keys on FQN-pattern guesses with no annotation evidence. The brownfield pipeline ranks `_fqn` as the lowest-rank fallback (`_ROUTE_LAYER_RANK[layer_b_fqn] = 4`).
21. **Wildcard identifiers (`*` / `?`) suppress the resolve hint.** `resolve_v2` does not currently reject wildcards in identifiers (unlike filter prefix fields). A wildcard identifier should redirect to `search` via existing prose `message`, not produce a `search(query='*')` hint. `generate_hints` detects wildcards in `resolved_identifier` and suppresses (UC2c).
22. **Resolve `none` hint does not duplicate `message` verbatim.** The prose `message` is generic ("use search(query=...) for ranked fuzzy lookup"); v2 hints embed the actual `resolved_identifier` so the rendered string differs. This is not a verbatim duplication risk and the two channels are intentionally redundant in spirit (Risk §8).

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Fuzzy-strategy hint fires too often in brownfield-heavy repos and trains the agent to ignore it | Meta-priority tier — drops first under cap pressure. Rendered string is terse. Real-world rebalancing target deferred to v3 if user-rag traces show training-to-ignore. |
| Resolve "none" hint duplicates the prose `message` (both say "try search") | Hint embeds the concrete `resolved_identifier` (`search(query='foo.bar.Baz#qux')`); `message` is generic English for log readers. Different rendered output for identical intent — agents that read structured hints win; humans reading logs read `message`. Decision §7.22 makes this an intentional dual channel. |
| Cap of 5 binds and we lose the fuzzy-strategy hint | By design — when 5 rollup-tier hints are competing, those are more navigationally useful. Meta-tier rightly loses. |
| Agent treats `hints[0]` as a deterministic next-call API and breaks when templates change | v1 §7.15 lock remains binding. Tests assert hint *presence* and *substring*, not exact whole-string equality, so template wording can evolve. |

## Appendix A — `mcp_hints.py` additions (verbatim)

```python
# --- v2: resolve templates ---

TPL_RESOLVE_NONE_TRY_SEARCH = (
    "no match — try search(query='{identifier}') for ranked fuzzy lookup"
)
TPL_RESOLVE_NONE_TRY_FIND_ROUTE = (
    "no match — try find(kind='route', filter={{path_prefix: '{seed}'}})"
)
TPL_RESOLVE_NONE_TRY_FIND_CLIENT = (
    "no match — try find(kind='client', filter={{target_service: '{seed}'}})"
)
TPL_RESOLVE_MANY_TIGHTEN = (
    "{n} candidates — tighten identifier or pick a candidate by id"
)

_RESOLVE_HINT_MAX_CHARS = 120  # v1 §7.6
_RESOLVE_WILDCARDS = ("*", "?")

# --- v2: neighbors fuzzy-strategy template ---

TPL_NEIGHBORS_FUZZY_STRATEGY = (
    "some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"
)

# FUZZY_STRATEGY_SET lives in java_ontology.py (Decision §7.19); imported here.
from .java_ontology import FUZZY_STRATEGY_SET  # noqa: E402
```

Wire-up in `generate_hints`:

```python
if output_kind == "resolve":
    status = str(payload.get("status") or "")
    if status == "one":
        return []
    if status == "many":
        n = len(payload.get("candidates") or [])
        if n > 1:
            pairs.append((PRIORITY_META, TPL_RESOLVE_MANY_TIGHTEN.format(n=n)))
        return finalize_hint_list(pairs)
    if status == "none":
        # All values echo from output fields / call-site plumbing — see §3.1.1.
        identifier = payload.get("resolved_identifier")
        hint_kind = payload.get("hint_kind")  # None | "symbol" | "route" | "client"
        # Decision §7.14: suppress on missing/empty identifier.
        if not isinstance(identifier, str) or not identifier.strip():
            return finalize_hint_list(pairs)
        # Decision §7.21: wildcards suppress.
        if any(w in identifier for w in _RESOLVE_WILDCARDS):
            return finalize_hint_list(pairs)
        rendered: str | None = None
        if hint_kind == "route":
            seed = payload.get("path_prefix_seed")
            if isinstance(seed, str) and seed.strip():
                rendered = TPL_RESOLVE_NONE_TRY_FIND_ROUTE.format(seed=seed)
        elif hint_kind == "client":
            seed = payload.get("target_service_seed")
            if isinstance(seed, str) and seed.strip():
                rendered = TPL_RESOLVE_NONE_TRY_FIND_CLIENT.format(seed=seed)
        else:
            rendered = TPL_RESOLVE_NONE_TRY_SEARCH.format(identifier=identifier)
        # Decision §7.18: drop-on-overflow (no truncation).
        if rendered is not None and len(rendered) <= _RESOLVE_HINT_MAX_CHARS:
            pairs.append((PRIORITY_META, rendered))
        return finalize_hint_list(pairs)
    return []

# extend the existing neighbors branch:
if output_kind == "neighbors":
    results = list(payload.get("results") or [])
    req_types = payload.get("requested_edge_types")
    if not isinstance(req_types, list):
        req_types = []
    n_types = len([x for x in req_types if str(x).strip()])
    if not results and n_types > 0:
        pairs.append((PRIORITY_META, TPL_NEIGHBORS_EMPTY_KIND_CHECK))
    else:
        # v2: fuzzy-strategy on any edge
        if _any_fuzzy_strategy(results):
            pairs.append((PRIORITY_META, TPL_NEIGHBORS_FUZZY_STRATEGY))
    return finalize_hint_list(pairs)


def _any_fuzzy_strategy(edges: list[dict[str, Any]]) -> bool:
    for e in edges:
        attrs = e.get("attrs") if isinstance(e.get("attrs"), dict) else {}
        s = attrs.get("strategy") if isinstance(attrs, dict) else None
        if isinstance(s, str) and s in FUZZY_STRATEGY_SET:
            return True
    return False
```

Plumbing note: the `resolve_v2` call site must populate the hint payload with `status`, `resolved_identifier`, `hint_kind`, `candidates`, and (when `hint_kind` is `"route"` / `"client"`) `path_prefix_seed` / `target_service_seed` — the same way `find_v2` populates `kind` and `filter`. See §3.1.1, §3.1.2, and Decisions §7.14 / §7.17 — missing plumbing or a missing seed suppresses the hint rather than rendering a degraded template.


