# HINTS-ROAD-SIGNS — machine-readable next-action signals on MCP V2 outputs

**Status**: under review
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-14 (revised 2026-05-15)

## TL;DR

- Add a `hints: list[str]` field to every MCP V2 output (`SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`).
- Each hint is a short, in-context, machine-readable string that points to the *next call* an agent likely wants — a road sign, not a tutorial.
- Hints are generated server-side from observable output state (e.g., presence of dot-keys in `edge_summary`, non-zero `OVERRIDDEN_BY`, kind of returned node), not by re-querying the graph.
- Strict hint discipline: ≤ 120 chars per hint, ≤ 5 hints per output, no prose explanation. Tone is "Exit 14: Balashikha →", not "to enumerate clients you might want to consider…".
- Migration: 2 PRs. PR-A materializes a stored `[:OVERRIDES]` relationship in the graph builder, extends the schema, adds `OVERRIDES` to `EdgeType`, wires `neighbors` to traverse it, and bumps `ontology_version` (prerequisite for override-axis hint emissions — today `OVERRIDES` is virtual Cypher computed in `override_axis_rollup_for`, not a stored rel). PR-B adds the `hints` field, the v1 template catalog, and echoes `limit`/`offset` on both `FindOutput` and `SearchOutput`. PR-B is additive on the agent-visible surface — existing consumers ignore the new fields.
- Out of scope: structured next-action records, pre-fetched walk results, agent-side hint dispatch logic. Those are future proposes if Shape 1 hints prove their worth.

## §1 — Frame: what is a hint, really?

> A hint is a road sign attached to a tool output: it tells the agent the next reachable call, not what that call means or why to take it.

The MCP V2 surface is a layered set of primitives (`search`, `find`, `describe`, `neighbors`) that the agent composes. Composition requires the agent to know, given an output, what the next reachable call is. Today that knowledge lives in three places: prose schema descriptions (which LLMs skip under context pressure), syntax conventions like dot-keys (which LLMs pattern-match unreliably), and external propose docs (which the agent has never read). Hints move that knowledge inline.

A hint is **not** documentation, not a tutorial, not a structured action plan. It's a *peer-level signal* — same priority as the result data — that says "here is a call you can make next." If a hint cannot be reduced to ≤ 120 chars and a single call-shape, it is the wrong primitive for the job; the right primitive is a schema description update, a new tool, or a propose-doc decision.

This frame rules things out:

- **Rules out** prose tutorials in the output (the schema description is already there for prose; don't duplicate).
- **Rules out** multi-step walk plans (Shape 2 territory — separate decision).
- **Rules out** speculative hints not grounded in observable output state ("you might also like to…" — no).
- **Rules out** hints that require the MCP to re-query the graph to compute (hints are cheap; if computing one needs a Cypher round-trip, it's not a hint).

## §2 — Design principles

1. **Road-sign discipline.** A hint is ≤ 120 chars, references one next call, and contains no prose justification.
2. **Grounded in observable state.** Every hint maps to a condition on the existing output fields. No speculation, no graph re-query.
3. **Peer to the data, not nested in it.** `hints` is a top-level list on the output, not a sub-field on each result row. Hints describe the *output as a whole*.
4. **Lossy by design.** Hints are advisory. An agent that ignores all hints must still be able to solve the task using the schema-described surface alone.
5. **Strict cap on hint count.** ≤ 5 per output, applied after dedupe-by-rendered-string (see §2.8). If more than 5 unique conditions match, drop the lower-priority ones. The cap forces curation.
6. **Stable phrasing.** Hint strings are templated (not LLM-generated), so phrasing does not drift between releases. Templates live in a single module.
7. **No alias magic.** A hint that recommends `neighbors(edge_types=["DECLARES"])` uses the exact `EdgeType` literal — never a dot-key, never a paraphrase.
8. **Coalesce by emission, not by trigger.** Distinct triggers that render to the same hint string (post-template-substitution) are deduplicated before the cap is applied. The cap counts *unique rendered strings*. Coalescing by trigger is forbidden — two semantically distinct signals that happen to share a rollup key remain two hints unless their rendered output is character-identical.
9. **Triggers are signals; emissions are calls.** A hint's *trigger* (what makes it fire) may reference any observable output state — dot-keys, rollup counts, score spreads, empty result lists, echoed pagination fields. A hint's *emission* (the string the agent reads) uses atomic `EdgeType` literals and concrete call shapes only. The two vocabularies never share — a dot-key in `edge_summary` triggers a hint that emits two `neighbors()` calls over atomic edge types.

## §3 — The proposed surface

### §3.1 — Field shape

Add `hints` to all four output models in `mcp_v2.py`. In addition, echo pagination state on both `FindOutput` and `SearchOutput` so hint generation stays a pure function of the response payload (see §7.18):

```python
class FindOutput(BaseModel):
    success: bool
    results: list[NodeRef] = Field(default_factory=list)
    message: str | None = None
    limit: int | None = Field(default=None, description="Echoed from the request — the page size the server applied. None on success=False.")
    offset: int | None = Field(default=None, description="Echoed from the request — the page offset the server applied. None on success=False.")
    hints: list[str] = Field(default_factory=list, description="...")

class SearchOutput(BaseModel):
    success: bool
    results: list[SearchHit] = Field(default_factory=list)
    message: str | None = None
    limit: int | None = Field(default=None, description="Echoed from the request. None on success=False.")
    offset: int | None = Field(default=None, description="Echoed from the request. None on success=False.")
    hints: list[str] = Field(default_factory=list, description="...")
```

Both `limit` and `offset` are `int | None` with default `None` — builders for error paths (`success=False`) and any code path that doesn't take pagination input can leave them unset. Hint triggers that read these fields treat `None` as “absent, do not fire”. `DescribeOutput` and `NeighborsOutput` get only the `hints` field, no pagination echo.

The `hints` field carries this schema description on every output:

```
"Road-sign hints pointing to likely next calls. Each hint is a short string "
"referencing one MCP V2 tool call. Hints are advisory and may be safely ignored. "
"Maximum 5 hints per output. Hints never recommend dot-key edge labels (composed "
"rollups) as neighbors() arguments."
```

The schema description is normative: agents that mechanically dispatch on hints must read it.

### §3.2 — Hint generation contract

Hints are generated in a single dedicated function, called by each tool's response-builder after the result data is computed. Pseudocode:

```python
def generate_hints(output_kind: str, payload: Any) -> list[str]:
    """Pure function. No graph access. Reads payload, emits hints."""
    hints: list[str] = []
    # ... condition-based emission ...
    return hints[:5]  # hard cap
```

The function is pure — no I/O, no graph queries, no LLM calls. Inputs are the already-computed output; outputs are short strings. Determinism is testable.

### §3.3 — Hint templates per output kind

The full catalog of hints emitted at v1. Each row is one template; multiple may fire per output.

| Output | Trigger condition | Hint template |
|---|---|---|
| `describe` (type Symbol) | `edge_summary["DECLARES.DECLARES_CLIENT"].out > 0` | `clients reachable via members: neighbors([{id}],'out',['DECLARES']) then neighbors(member_ids,'out',['DECLARES_CLIENT'])` |
| `describe` (type Symbol) | `edge_summary["DECLARES.EXPOSES"].out > 0` | `routes exposed via members: neighbors([{id}],'out',['DECLARES']) then neighbors(member_ids,'out',['EXPOSES'])` |
| `describe` (method Symbol) | `edge_summary["OVERRIDDEN_BY"].out > 0` | `overriders: neighbors([{id}],'in',['OVERRIDES'])` (requires PR-A; see §6) |
| `describe` (method Symbol) | `edge_summary["OVERRIDDEN_BY.DECLARES_CLIENT"].out > 0` | `clients in overriders: neighbors(['{id}'],'in',['OVERRIDES']) then neighbors(overrider_ids,'out',['DECLARES_CLIENT'])` (requires PR-A; see §6) |
| `describe` (method Symbol) | `edge_summary["DECLARES_CLIENT"].out > 0` | `outbound client: neighbors([{id}],'out',['DECLARES_CLIENT'])` |
| `describe` (method Symbol) | `edge_summary["EXPOSES"].out > 0` | `inbound route: neighbors([{id}],'out',['EXPOSES'])` |
| `describe` (method Symbol) | `edge_summary["CALLS"].out >= 10` | `many CALLS — consider filtering by target microservice` |
| `describe` (route node) | always | `declaring method: neighbors([{id}],'in',['EXPOSES'])` |
| `describe` (client node) | always | `declaring method: neighbors([{id}],'in',['DECLARES_CLIENT'])` |
| `find` | `len(results) == 0` and filter has an identifier-shaped value (e.g. `fqn_prefix`, `target_service`) | `no matches — try resolve(identifier, hint_kind='{kind}') for canonical lookup` |
| `find` | `len(results) >= limit` (page-full) | `result page full at {limit} — narrow filter or paginate` |
| `neighbors` | `len(results) == 0` and `len(edge_types) > 0` | `0 results — check if the requested edge_types apply to this kind` |
| `neighbors` | rows include `edge_type='DECLARES'` to method targets, **and** any of those methods has known `DECLARES_CLIENT` out in summary | (deferred — needs second-hop awareness; not in v1) |
| `search` | `len(results) == limit` **and** `(max_score - min_score) < 0.1 * max_score` (structural low-confidence signal — no absolute threshold). Requires `SearchOutput.limit` echo per §3.1 / §7.18. | `results look weak — narrow the query or try find(role=…)` |

This catalog is the v1 lock. Adding a new template requires a propose-doc amendment.

### §3.4 — Where hints do NOT appear

- **Per-row.** No `hint` field on individual `NodeRef`, `Edge`, or `SearchHit`. Hints are output-level only.
- **In error responses.** If `success=False`, the `message` field carries the error; `hints` stays empty. (An error message *may* be road-sign shaped if helpful, but that's `message`'s job, not `hints`'.)
- **In `data` payloads.** The free-form `data: dict` on `NodeRecord` is not a hint surface.

## §4 — Use-case re-walk

15 realistic cases (UC6 split into UC6a/UC6b to match the route-vs-client row split in §3.3). Each row records what the agent sees and what hints would emit.

| # | Use case | Output | Hints emitted |
|---|---|---|---|
| UC1 | Agent describes class `SmartCareAssignClientImpl` with rollup keys | describe | "clients reachable via members: …", "routes exposed via members: …" (if applicable) |
| UC2 | Agent describes a method whose `edge_summary` shows `OVERRIDDEN_BY.DECLARES_CLIENT.out > 0` | describe | "clients in overriders: neighbors(['{id}'],'in',['OVERRIDES']) then neighbors(overrider_ids,'out',['DECLARES_CLIENT'])" |
| UC3 | Agent describes method that declares a client | describe | "outbound client: neighbors([id],'out',['DECLARES_CLIENT'])" |
| UC4 | Agent describes method that exposes a route | describe | "inbound route: neighbors([id],'out',['EXPOSES'])" |
| UC5 | Agent describes method with 15 outbound CALLS | describe | "many CALLS — consider filtering by target microservice" |
| UC6a | Agent describes a route node | describe | "declaring method: neighbors([id],'in',['EXPOSES'])" |
| UC6b | Agent describes a client node | describe | "declaring method: neighbors([id],'in',['DECLARES_CLIENT'])" |
| UC7 | Agent does `find(kind=client, filter={fqn_prefix:...})` and gets empty (post-#117 fix, kind-applicable predicate) | find | "no matches — try resolve(identifier, hint_kind='client') for canonical lookup" |
| UC8 | Agent does `find` and hits the page-full limit | find | "result page full at {limit} — narrow filter or paginate" |
| UC9 | Agent does `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` and gets 0 (correctly, because DECLARES_CLIENT lives on methods) | neighbors | "0 results — check if the requested edge_types apply to this kind" |
| UC10 | Agent does `search`, gets a full page of hits all clustered within 10% of the top score (no dominant match) | search | "results look weak — narrow the query or try find(role=…)" |
| UC11 | Agent describes a leaf method (no rollups, no override axis) | describe | (no hints — clean output, agent has all it needs) |
| UC12 | Agent does `find` with successful match list | find | (no hints — page not full, results present) |
| UC13 | Agent does `neighbors` with results matching all requested edge_types | neighbors | (no hints — happy path) |
| UC14 | Agent describes a class with `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES` both non-zero, plus `OVERRIDDEN_BY` (it's an interface) | describe | up to 3 hints — clients via members, routes via members, overriders if applicable |

15 realistic cases (UC6a + UC6b counted separately). The §2.5 cap is exercised by a dedicated test scenario in §6 (a hand-crafted record with > 5 firing conditions), not by a hypothetical UC — the UC re-walk is the design-validation move, the cap is a guardrail with its own test.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Structured `next_actions` field with executable steps | Shape 2 in the design discussion. Needs evidence first that flat strings are insufficient. Promoting strings → structured later is one PR; un-shipping a half-used structured surface is several. |
| Pre-fetched walk results inside `describe` | Shape 3 in the discussion. Reopens PR #89 decision #7 (depth=1) and the "graph navigator, not path-walker" §1 frame. Out of scope. |
| LLM-generated hints | Phrasing drift, non-determinism, and a new LLM dependency in a stateless graph navigator. Templates only. |
| Per-row hints (`Edge.hint`, `NodeRef.hint`) | Per-row noise; hints describe the *output*, not each cell. If a per-row signal is needed, that's a separate field (e.g., `Edge.attrs["follow_up"]`), not part of this propose. |
| Hint dispatch helper tool / CLI | The agent reads hints in-context. No new tool needed. If agents start dispatching on hints programmatically, that's a future propose. |
| Additional cross-tool hint templates beyond the locked `find` empty → `resolve` row (§7.16) | The frame allows them, but no other v1 row crosses tool boundaries. New cross-tool templates need a propose-doc amendment per §7.11. |
| Hints in `success=False` responses | The `message` field already carries error info. Two channels for one signal invites drift. |
| Internationalization of hint strings | English-only at v1. The agent's prompt language is the right place to localize, not the tool surface. |
| Versioning of the hint catalog | Hints are advisory by §2.4. Agents must not break if a hint changes phrasing or disappears. No `hints_version` field. |
| Request-context plumbing for pagination hints | Once `FindOutput` and `SearchOutput` both echo `limit`/`offset` per §7.18, pagination hint generation stays pure-payload. Threading request kwargs into the hint generator is out of scope — the payload is the contract. |
| Pagination echo on `neighbors` | `neighbors` may grow pagination later; if so, it gets its own echo via its own propose. v1 echoes pagination on `find` and `search` (the latter required by the §7.19 structural search trigger). |
| `HTTP_CALLS` / `ASYNC_CALLS` / `IMPLEMENTS` / `EXTENDS` hint rows | Plausible v2 candidates per the second review pass. Not in v1; the v1 catalog already exercises the field shape and the dedupe/cap/kind-gate plumbing. Expansion can land additively in a future amendment per decision §7.11. |

## §6 — Migration plan — 2 PRs

**PR-A: Materialize `OVERRIDES` as a stored edge** *(prerequisite, three-part graph work)*

Today `OVERRIDES` is computed by virtual Cypher in `kuzu_queries.override_axis_rollup_for` — there is no stored `[:OVERRIDES]` relationship. PR-A is the full graph-side story:

- **Schema**: extend the Kuzu schema with an `OVERRIDES` relationship type between method Symbols, written in `build_ast_graph.py` (where the other `CREATE REL TABLE` statements live; `graph_enrich.py` is enrichment-only and does not create relationships).
- **Builder — unified directed-edge rule**: in `build_ast_graph.py` (the relationship-write pass), materialize one edge per override pair: **`(A)-[:OVERRIDES]->(B)` whenever method `A` on a subtype overrides method `B` on a supertype** (signature match). One rule, both halves of the virtual rollup:
  - When the rollup runs **down** (`m` is on supertype `t`, find implementing-type methods `mover` with matching signature) — the same rule yields `(mover)-[:OVERRIDES]->(m)`. `neighbors(m, 'in', ['OVERRIDES'])` then returns the `mover` id set.
  - When the rollup runs **up** (`m` is on subtype, find parent-type declared methods `decl_m` with matching signature) — the same rule yields `(m)-[:OVERRIDES]->(decl_m)`. `neighbors(m, 'out', ['OVERRIDES'])` then returns the `decl_m` id set.
  - Builder pseudo-code: walk every method `A`; for the type that declares `A`, walk transitive `IMPLEMENTS`/`EXTENDS` ancestors; for each ancestor type `T_parent`, for each method `B` declared by `T_parent` whose `signature` matches `A.signature` and `B.id != A.id`, write `(A)-[:OVERRIDES]->(B)`. One walk, one edge per pair, both rollup halves covered.
- **Query path**: extend `neighbors` to traverse `OVERRIDES` via stored edges (both directions — `'in'` reads overriders, `'out'` reads parent declarations). `OVERRIDDEN_BY` stays rollup-only (it's the count-view derived from incoming `OVERRIDES`).
- **Ontology**: add `"OVERRIDES"` to the `EdgeType` literal in `mcp_v2.py`. Bump `ontology_version`. Adjust the `edge_summary` description in `NodeRecord` to drop the “not valid for neighbors()” carve-out for `OVERRIDES` only (keep it for `OVERRIDDEN_BY` and dot-key rollups, which remain virtual).

Hint emissions in PR-B depend on PR-A landing first; without the stored edge plus `EdgeType` admission every override-axis emission row in §3.3 would be a strict-frame failure at call time.

- **Named test scenarios**:
  - **Equivalence — both halves**: for a sample of method ids, exercise both arms of `override_axis_rollup_for`:
    - (down) `neighbors(supertype_method_id, 'in', ['OVERRIDES'])` over the stored edge returns the same id set the rollup's down arm would (the `impl_ids` set — implementing-type methods).
    - (up) `neighbors(subtype_method_id, 'out', ['OVERRIDES'])` over the stored edge returns the same id set the rollup's up arm would (the `decl_ids` set — parent-type declaration methods).
    - The two sets must match in cardinality and content; if either arm comes up short, the materialization is missing pairs.
  - **Schema**: the new `OVERRIDES` relationship type is present in the built graph and survives a build/load round-trip.
  - **Ontology bump**: the new edge type round-trips through `ontology_version` (existing ontology-bump test fixtures extended).
  - **Validation**: the `EdgeType` literal-validation in `neighbors` accepts `"OVERRIDES"` and still rejects `"OVERRIDDEN_BY"` and dot-keys (those remain rollup-only).
  - **Symmetry**: building the same source twice produces the same `OVERRIDES` edge set (no nondeterministic builder ordering).

If the cost of materializing edges is high enough that we prefer a scoped `neighbors` special-case (computing `OVERRIDES` traversals via the same virtual Cypher the rollup uses, without storing edges), that is a different design and needs its own propose — it changes the strict-frame story (`neighbors` would dispatch on edge type to choose stored vs virtual). PR-A as scoped here stays on the stored-edge path.

**PR-B: Add `hints` field, pagination echo, and v1 template catalog**

- **Purpose**: Add `hints: list[str]` to all four output models; add `limit`/`offset` echoes on both `FindOutput` and `SearchOutput`; implement the hint-generation contract from §3.2 with the §3.3 catalog; wire it into the four tool response builders.
- **Named test scenarios** (the contract; total count is a side-effect of implementation):
  - Each row in §3.3 has a fixture-based scenario that fires its template and asserts the emitted string.
  - Cap scenario: a hand-crafted `describe` payload with > 5 firing conditions emits exactly 5 hints, dropped in reverse priority order per decision §7.12, after dedupe-by-rendered-string per §2.8.
  - Dedupe scenario: a payload where two distinct triggers render to character-identical hint strings emits one hint, not two; the dedupe runs before the cap.
  - Char-cap scenario: every template in the catalog, rendered with realistic placeholder values, satisfies `len(hint) <= 120`. Templates that cannot fit a realistic rendering are dropped from v1.
  - Kind-gate scenario: a method-Symbol `describe` payload synthesized with type-only rollup keys present (impossible but defensible state) emits no type-rollup hints — a regression bumper for the §3.3 kind separator.
  - Empty-hint scenarios: clean outputs (UC11 / UC12 / UC13) emit `hints == []`.
  - Error-path scenario: `success=False` outputs emit `hints == []` regardless of payload.
  - Pagination-echo scenario: both `FindOutput` and `SearchOutput` round-trip the request `limit` and `offset` verbatim; the page-full hint fires iff `len(results) >= limit`.
  - Pagination-echo error-path scenario: `success=False` responses leave `limit` / `offset` as `None`; no pagination-derived hint fires when either is `None`.
  - Structural low-confidence search scenario: a `search` response where all hit scores fall within 10% of the top hit and `len(results) == limit` emits the structural hint; a response with a clearly dominant top hit emits no hint; a response where `limit` is `None` emits no hint regardless of score spread.

PR-B is additive on the agent-visible surface (no removed fields, no changed call shapes). PR-A bumps the ontology and is the breaking-change part of the migration.

## §7 — Decisions taken (no longer open)

1. **Hint field name is `hints`** (not `next_actions`, `suggestions`, `road_signs`, `tips`). Plain, accurate, short.
2. **Type is `list[str]`**, not `list[dict]`. Strings now; structured records are a separate future propose.
3. **Hints are output-level**, not per-row. `hints` lives on `SearchOutput`/`FindOutput`/`DescribeOutput`/`NeighborsOutput`, never on `NodeRef`/`Edge`/`SearchHit`.
4. **Generation is pure, server-side, no graph access.** Hints are derived from already-computed output. No new Cypher round-trips.
5. **Hard cap of 5 hints per output**, applied after dedupe-by-rendered-string (§7.20). Drop in reverse priority (§7.12); coalesce only on character-identical emission strings (§2.8).
6. **Hard cap of 120 chars per hint, measured on the *rendered* string** (after `{id}` and other placeholders are substituted). Enforced by unit test that exercises each template with a realistic placeholder value, not just the template. If a row in §3.3 cannot fit a realistic rendering within 120 chars, that row is dropped from v1 — brevity wins over coverage.
7. **Templates are static, not LLM-generated.** Phrasing stability matters more than naturalness.
8. **Hints reference real `EdgeType` literals only.** Never dot-keys, never paraphrases. Aligned with PR #89 decision #11.
9. **No hints in error responses.** `message` carries error info; `hints` stays empty when `success=False`.
10. **Hints are advisory.** Agents that ignore all hints must still solve tasks. No tool path requires hint consumption.
11. **The §3.3 catalog is the v1 lock.** New templates require a propose-doc amendment.
12. **Priority order for the cap**: `DECLARES.*` rollups > `OVERRIDDEN_BY.*` rollups > leaf-edge follow-ups > meta-hints (page-full, low-confidence). When > 5 conditions match, drop in reverse priority. Priority applies *after* dedupe-by-rendered-string (§2.8 and §7.20).
13. **Empty `hints` list is a valid output state.** UC11 / UC12 / UC13 explicitly emit nothing.
14. **Per-tool template module location**: `mcp_hints.py` (new file). One module so the catalog is greppable in one place.
15. **Hints are documentation-grade, not programmatic-dispatch.** The agent reads `hints` as part of its prompt context. The surface does not commit to mechanical consumption in v1. If a future workflow needs typed, machine-walkable decompositions (e.g., for an orchestrator that constructs the next `neighbors()` call from a parsed structure), that's a separate propose for a typed surface — e.g., the `rollup_paths` shape sketched in issue #118.
16. **Cross-tool hints are allowed at v1, scoped to one row.** The `find` empty-result row points at `resolve(…)`. Other cross-tool templates need their own propose amendment per decision §7.11.
17. **`OVERRIDES` is materialized as a stored edge in PR-A before hint emissions land in PR-B.** The override-axis emission rows in §3.3 require `neighbors(..., ['OVERRIDES'])` to traverse a real relationship; today `OVERRIDES` is virtual Cypher in `kuzu_queries.override_axis_rollup_for`. PR-A's scope is therefore three-part: schema, builder (mirroring the rollup logic), and `neighbors` traversal path, plus the `EdgeType` literal admission and `ontology_version` bump. `OVERRIDDEN_BY` stays rollup-only (it remains the count-view derived from incoming `OVERRIDES`). If a future review concludes the materialization cost is too high, the alternative — a `neighbors` special-case dispatching to virtual Cypher for `OVERRIDES` — is a different design and gets its own propose; this propose commits to the stored-edge path.
18. **Pagination state is part of the response payload, not the call context.** Both `FindOutput` and `SearchOutput` echo `limit` and `offset` from the request. Hint generation remains a pure function of the output object — the page-full hint reads `output.limit` and `len(output.results)`, never the request kwargs; the structural low-confidence search hint (§7.19) reads `output.limit` for the same reason. Both fields are `int | None`; hint triggers treat `None` as absent (do not fire). Other tools that grow pagination follow the same pattern in their own proposes.
19. **Low-confidence search hint is structural, not threshold-on-score.** The v1 trigger is `len(results) == limit AND max_score - min_score < 0.1 * max_score` — calibration-free, observable from the payload alone, robust across RRF / hybrid / pure-cosine modes. `len(results) == limit` is defined as “the **returned** result list, after any post-processing filters, fills the requested page” — i.e., `search_v2` may apply filters that trim the result set; the trigger reads what the agent actually sees in `output.results`, not the pre-filter count. When the signal is uncertain v1 emits no hint rather than a miscalibrated one. If a future ranking change makes a sharper threshold meaningful, that's a separate amendment.
20. **Dedupe by rendered emission string is required and runs before the §7.12 cap.** Distinct triggers that render to character-identical hint strings collapse to one hint. The cap counts unique rendered strings. Coalescing by trigger is forbidden (§2.8) — semantically distinct triggers that render to different strings remain separate hints even if they recommend the same conceptual next step.
21. **`hints` field is additive, not breaking, despite the repo's breaking-changes-allowed policy.** Adding an optional Pydantic field that defaults to `[]` is behaviorally invisible to any caller that does not opt in. The policy override is reserved for actually-breaking changes (PR-A's ontology bump is one); calling PR-B additive is accurate and worth keeping in the doc.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Hints drift from real tool behavior (e.g., template says `neighbors(['DECLARES_CLIENT'])` but the parameter shape changes) | Hint strings are unit-tested against the actual tool schema. CI fails if a template references a tool name or arg that no longer exists. |
| Hints become a kitchen sink | Hard cap of 5 + §2.1 road-sign discipline + propose-amendment requirement for new templates. Three independent brakes. |
| LLM agents over-trust hints and stop reading `edge_summary` | §2.4 ("lossy by design") + advisory framing + visible-but-not-required in schema description. If agents do over-trust, hint phrasing can be tuned without changing surface. |
| Hint catalog becomes stale as the graph schema evolves | Templates that reference removed edge types fail the schema-match unit test (see risk 1). |
| Phrasing leaks into agent reasoning style ("I'll enumerate clients via members because…") | Templates are crisp and imperative, no causal phrasing. Avoid words like "because", "to see", "you might want to". |
| Conflict with future structured `next_actions` field (Shape 2) | Field name `hints` does not collide with `next_actions`. Hints can coexist or be deprecated; the choice is the next propose's job. |
| Hints in some outputs but not others creates inconsistency | All four outputs get the field at v1, even if some templates are empty. No partial rollout. |

## Appendix A — v1 hint template catalog (concrete artifact)

The §3.3 table *is* the appendix artifact — every row is a verbatim template string the implementer copies into `mcp_hints.py`. Reproduced here for traceability:

```
# DescribeOutput
DECLARES.DECLARES_CLIENT.out>0  →  "clients via members: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'out',['DECLARES_CLIENT'])"
DECLARES.EXPOSES.out>0          →  "routes via members: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'out',['EXPOSES'])"
OVERRIDDEN_BY.out>0              →  "overriders: neighbors(['{id}'],'in',['OVERRIDES'])"  # requires PR-A; rollup stores counts on .out per override_axis_rollup_for
OVERRIDDEN_BY.DECLARES_CLIENT.out>0  →  "clients in overriders: neighbors(['{id}'],'in',['OVERRIDES']) then neighbors(overrider_ids,'out',['DECLARES_CLIENT'])"  # requires PR-A
DECLARES_CLIENT.out>0 (method)  →  "outbound client: neighbors(['{id}'],'out',['DECLARES_CLIENT'])"
EXPOSES.out>0 (method)          →  "inbound route: neighbors(['{id}'],'out',['EXPOSES'])"
CALLS.out>=10 (method)          →  "many CALLS — consider filtering by target microservice"
kind == route, always           →  "declaring method: neighbors(['{id}'],'in',['EXPOSES'])"
kind == client, always          →  "declaring method: neighbors(['{id}'],'in',['DECLARES_CLIENT'])"

# FindOutput
results==[] and filter has identifier-shaped value  →  "no matches — try resolve(identifier, hint_kind='{kind}') for canonical lookup"
len(results) >= limit                               →  "result page full at {limit} — narrow filter or paginate"

# NeighborsOutput
results==[] and edge_types non-empty  →  "0 results — check if the requested edge_types apply to this kind"

# SearchOutput
len(results)==limit and (max_score - min_score) < 0.1*max_score  →  "results look weak — narrow the query or try find(role=…)"  # requires SearchOutput.limit echo, see §3.1
```

File placement (`mcp_hints.py`), function decomposition, integration points in `mcp_v2.py`, and test file names go in `plans/PLAN-HINTS.md` — not in this propose.

## Appendix B — What changed (traceability)

**What stayed unchanged from the first draft**

- §1 frame statement; §2 principles 1–8; §3.1 field shape; §3.2 generation contract; §5 "deliberately does NOT do" table; §8 risks table.
- Decisions §7.1–7 (`list[str]`, output-level, pure server-side, hard caps, static templates).
- Decisions §7.9–11 (no hints in error responses, advisory, v1 catalog locked).
- Decision §7.12 (priority order for the cap), §7.13 (empty list is valid).

**What changed after the 2026-05-15 re-grilling (post-#117 / post-`resolve`)**

1. §2.9 added — the *triggers vs emissions* principle. Implicit in §3.3, now stated.
2. §3.3 row 4 (`OVERRIDDEN_BY.DECLARES_CLIENT`) rewritten from a paraphrased "walk … then …" to a concrete two-call emission matching row 1's shape — contradiction with decision §7.8 fixed.
3. §3.3 `find`-empty row repointed from `search(…)` to `resolve(…, hint_kind=…)`. The pre-`resolve` fallback wording was removed from tool descriptions in PR-RESOLVE-2; letting it survive in the hint catalog re-introduced what we just removed.
4. UC7 updated to match the new `find`-empty hint.
5. UC15 ("hypothetical 8-rollup-signal cap test") dropped; the UC count is now 14. The cap is exercised by a dedicated test scenario in §6, not by a hypothetical UC.
6. §6 reformatted into named test scenarios (the contract; total count is a side-effect of implementation), aligned with the named-scenario discipline that landed in `cursor-task-prompt` and the propose-doc-author skill.
7. Old decision §7.14 (no hints for `find` when no filter was passed) dropped. In the post-#117 strict-frame world, `find()` without a filter is a contract error that fails loud — the carve-out was solving a problem the strict frame already solved.
8. New decision §7.15 added: hints are documentation-grade, not programmatic-dispatch. Locks the consumer model so future readers don't relitigate it.
9. New decision §7.16 added: cross-tool hints allowed at v1, scoped to the one row pointing at `resolve(…)`. Other cross-tool templates need their own amendment per §7.11.
10. Appendix A trimmed from a `mcp_hints.py` skeleton with function bodies to a verbatim template catalog. Function decomposition is plan-level work and belongs in `plans/PLAN-HINTS.md`.
11. Open-links section rewritten: #117 landed, `resolve` shipped, #118 is a partial overlap (documentation-grade only) not a resolution. Misleading "locking hints here mostly resolves #118" claim removed.

**What changed after the fourth review pass (2026-05-15, PR-A builder sharpening)**

25. **PR-A builder rewritten as a unified directed-edge rule**: `(A)-[:OVERRIDES]->(B)` whenever subtype-method `A` overrides supertype-method `B` (signature match). The prior wording (“`(mover)-[:OVERRIDES]->(m)` for `m` on `t`”) narrated only the down half of `override_axis_rollup_for`, leaving a reader at risk of missing the up half (concrete method → parent declared methods). The unified rule emits one edge per pair and covers both rollup arms; the §6 equivalence test now exercises both `'in'` and `'out'` traversals against `impl_ids` and `decl_ids` respectively.
26. **Builder module pinned to `build_ast_graph.py`**: this is where `CREATE REL TABLE` and the per-edge `CREATE` statements live. `graph_enrich.py` is enrichment-only and creates no relationships. The prior “`graph_enrich.py` (or wherever)” wording risked starting in the wrong module.
27. **§5 “Request-context plumbing” row updated to reference both `FindOutput` and `SearchOutput`** — stale after §7.18 expanded to cover both in the previous pass.
28. **§7.19 page-full definition clarified for `search`**: `len(results) == limit` is defined as “the returned result list, after any post-processing filters, fills the requested page.” `search_v2` may post-filter; the trigger reads `output.results`, not a pre-filter count. Removes implementer ambiguity on what “full page” means.

**What changed after the third review pass (2026-05-15, second-look corrections)**

19. **`OVERRIDDEN_BY` trigger direction**: rollup keys are stored as `{"in": 0, "out": n}` per `kuzu_queries.override_axis_rollup_for`. The two override-axis trigger rows now read `.out > 0`, not `.in > 0`. UC2 row updated to match. Appendix A annotated. Without this fix the override-axis hints would have been dead code.
20. **PR-A scope expanded to materialize the stored `[:OVERRIDES]` edge**: today `OVERRIDES` is virtual Cypher in the rollup; `neighbors(..., ['OVERRIDES'])` over a non-existent stored rel would return empty. PR-A is now three-part — schema, builder mirroring the rollup logic, and `neighbors` traversal — plus the `EdgeType` admission and ontology bump. §7.17 rewritten to reflect this. New equivalence + schema + symmetry test scenarios added in §6 PR-A.
21. **`SearchOutput.limit` / `SearchOutput.offset` echoes added**: §7.19 structural low-confidence trigger reads `len(results) == limit`, which can't be observed if `SearchOutput` doesn't echo `limit`. §3.1 now shows both `FindOutput` and `SearchOutput` shapes. §7.18 updated. §5 carve-out narrowed to `neighbors` only.
22. **`limit` / `offset` typed `int | None` for error-path simplicity**: builders for `success=False` paths leave them unset. Hint triggers treat `None` as absent (do not fire). New error-path pagination test scenario added in §6 PR-B.
23. **Route/client describe template split into two concrete rows**: the old single row used `'EXPOSES' or 'DECLARES_CLIENT'` as a human-readable placeholder which would not have rendered to a valid call shape. Now two separate rows, one per kind — keeps the substitution mechanical and consistent with §2.7 (no alias magic).
24. **§5 "no cross-tool hints" row reconciled with §7.16**: the row was contradicting the locked cross-tool `find` → `resolve` template. Narrowed to "no *additional* cross-tool templates beyond the locked row".

**What changed after the second review pass (2026-05-15, contract-gap pass)**

12. Migration reshaped from 1 PR to 2 PRs. PR-A promotes `OVERRIDES` to a first-class `EdgeType` (ontology bump); PR-B adds `hints` + pagination echo + template catalog. Without PR-A every override-axis emission in §3.3 would be invalid against the existing `EdgeType` literal. New decision §7.17 locks this.
13. `FindOutput.limit` and `FindOutput.offset` echoes added in §3.1 and the `find` page-full hint now reads from the response payload, not request kwargs. New decision §7.18 locks pagination as part of the output contract.
14. Search low-confidence trigger rewritten from `top score < 0.5` (uncalibrated absolute threshold) to a structural signal: `len(results) == limit AND max - min < 0.1 * max`. UC10, the §3.3 row, and the Appendix A row all updated. New decision §7.19 locks the structural approach.
15. §2.8 rewritten to require dedupe-by-rendered-string before the cap; the cap now counts unique rendered strings. UC table and §3.3 unchanged (no current row pair renders to the same string), but the contract is now explicit. New decision §7.20 locks this; §7.12 cross-references it.
16. Kind-gate test scenario added to §6 as a regression bumper for the type-vs-method separator in §3.3.
17. §5 "deliberately does NOT do" extended with three rows: request-context plumbing for pagination, pagination echo on tools other than `find`, and v2-candidate hint rows (`HTTP_CALLS`, `ASYNC_CALLS`, `IMPLEMENTS`, `EXTENDS`).
18. New decision §7.21 added: the `hints` field is additive on the agent surface even though the repo policy allows breaking changes. The compatibility note is kept and is accurate for the new field; the breaking-changes policy applies to PR-A's ontology bump.

## Open links

- Issue #117 — filter contract per kind. **Landed** (strict frame) in PRs #131 / #132 / #133. The §3.3 hint catalog honors the strict frame: kind-applicable predicates only, no smart-by-nature fallbacks in hint emissions.
- Issue #118 — rollup decomposition affordance. **Partial overlap, not resolved.** Strings in this propose cover the *documentation-grade* consumer model from #118 option B — an LLM reads the hint string and constructs the two `neighbors()` calls. The *mechanical/typed* consumer model (`rollup_paths` shape) is a separate decision per §7.15: if future workflows need a typed, machine-walkable surface, that's a follow-up propose, not this one.
- Issue #119 — Kuzu `label(e) IN $list` bug. **Resolved** independently; the `neighbors` template emissions in §3.3 are reliable.
- `propose/completed/RESOLVE-TOOL-PROPOSE.md` — `resolve` tool. **Shipped** in PRs #137 / #140 / #141. The `find`-empty hint row in §3.3 points at `resolve(…, hint_kind=…)` as the canonical identifier-resolution path, matching the description sweep PR-RESOLVE-2 landed.
- PR #89 propose — rollup naming. Decision #11 (rollup dot-keys are read-only) is honored by §2.9: dot-keys may *trigger* a hint but never appear in *emissions*.
