# CALLS-NOISE-AND-RESOLUTION — clean the CALLS edge by removing one bucket and projecting the other

**Status**: draft
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-18
**Tracks**: [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)

## TL;DR

- `CALLS` is a sequence (ordered by `call_site_line`, `call_site_byte`), not a set. Source-order traversal is the dominant agent use case and must be preserved.
- Two locked moves, no new edge types:
  1. **`CALLS` carries only resolved invocations.** Phantom and chained-receiver call sites move to a caller-side facet (`Symbol.unresolved_call_sites`). Phantom Symbol rows disappear from the graph.
  2. **`callee_declaring_role` becomes a `CALLS` edge attribute.** `neighbors_v2` gains a typed `edge_filter` mechanism that projects the ordered stream by attribute without breaking ordering.
- Migration: 3 sequential code PRs. Ontology bump 14 → 15 in PR-1; one re-index required across the sequence. No new MCP tool.
- Two `neighbors_v2` consumption modes for CALLS: default = resolved-only clean stream; `include_unresolved=True` = interleaved transcript with `row_kind` discriminator (mutually-exclusive with `edge_filter`).
- Out: `min_confidence` and `exclude_strategies` as `neighbors_v2` parameters (subsumed by the broader `edge_filter` mechanism); a `CALLS` semantic split into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE`; `callee_capability` / `callee_annotation` filter axes; `EdgeFilter` on `find_callers`; an MCP surface for "find unresolved callers"; package-relativity filters; per-edge dedup as a `neighbors_v2` knob (kept as a CALLS-specific response shape decision in PR-3).
- Non-obvious constraint: `pass3_calls` and `find_callers` currently rely on phantom Symbol rows existing as the `dst` endpoint of every CALLS row. PR-1 changes that invariant atomically.

## §1 — Frame

> `CALLS` is the ordered transcript of one method's body. Its job is to be readable in source order; its noise is to mix bytecode-level invocations with semantically-loaded ones.

`CALLS` has been carrying two unrelated burdens. **Burden one**: representing real invocations the agent might want to traverse in order. **Burden two**: encoding "the resolver gave up here," via phantom and chained-receiver edges that point at synthetic Symbol rows the agent has no traversal use for. The first is what `CALLS` is for; the second is a graph-completeness convenience that became a per-query tax on every agent.

The frame rules out three things:

- **A `CALLS` semantic split** (`DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE`). It breaks the ordered-transcript property — agents reading a method body would have to 4-way fan-merge by `(line, byte)` across edge types. Edge-type splits are valid only when the agent never wants to interleave edges of different types in one traversal (cf. `HTTP_CALLS` vs `ASYNC_CALLS` — they pass the test; intra-method invocations do not).
- **A new `neighbors_v2` parameter per noise dimension** (`min_confidence`, `exclude_strategies`, `callee_role`, etc). Each one is a stop-gap that calcifies the under-typing. We add **one** general-purpose edge-attribute filter mechanism instead.
- **Reasoning about why a particular edge is noise at query time.** The graph builder already knows whether a call site is resolved and what role its target's declaring type has. We push that data into the graph at build time, not infer it at query time.

## §2 — Design principles

1. **`CALLS` is a sequence of resolved invocations.** Resolution failures are not edges; they are caller-side metadata.
2. **Edge type splits only when sequential interleaving is never wanted.** `CALLS` fails this test; no semantic split.
3. **One general-purpose edge-attribute filter on `neighbors_v2`, not one knob per attribute.** `edge_filter: dict[str, Any]` (typed model in §3.4); attributes added later (e.g. `framework` on routes) inherit the surface for free.
4. **All filter values are populated at build time from declared graph state.** No query-time inference, no per-row recomputation in `neighbors_v2`.
5. **Default `neighbors([m], 'out', ['CALLS'])` returns the same shape and ordering as today, minus phantoms and minus chained-receiver sites.** No new required arguments.
6. **`find_callers` / `find_route_callers` keep their current semantics.** They aggregate over the new ordered stream (resolved only); the `min_confidence` parameter that already exists on them is unchanged. (No new parameter required to scope to a specific `callee_declaring_role` on these helpers — see §5.)
7. **Caller-side `unresolved_call_sites` is a debuggability + recall facet, not a traversal surface.** It is exposed via `describe(method_id)` and via a dedicated CLI subcommand, **not** via `neighbors`.
8. **Decisions about call-site semantics live next to the data, in `pass3_calls`, not in `neighbors_v2`.** Edge attributes are computed at emission time.

## §3 — The proposed surface

### §3.1 — `CALLS` DDL change

Current (`build_ast_graph.py:2317-2319`):

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN)
```

Proposed:

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING,
  callee_declaring_role STRING)
```

Removed: `resolved BOOLEAN` (now always TRUE for any CALLS row; if a row exists, it is resolved).

Added: `callee_declaring_role STRING` — the `role` of the `Symbol` row that is the parent (declaring type) of the callee. Pulled from `tables.symbols[parent_of(dst)].role`. Defaults to `OTHER` if the parent is missing or unroleable (matches existing `_node_row` default).

Removed semantically (no DDL change): phantom and chained-receiver rows. These were emitted by `pass3_calls` at `build_ast_graph.py:1192-1199` and `1204-1211`.

### §3.2 — Caller-side `unresolved_call_sites` facet

Two options for storage:

**Option A — JSON column on Symbol.**

```sql
ALTER NODE TABLE Symbol ADD COLUMN unresolved_call_sites STRING  -- JSON-encoded list[UnresolvedCallSite]
```

**Option B — sibling row table.**

```sql
CREATE NODE TABLE UnresolvedCallSite(
  id STRING,                  -- caller_id || ':' || call_site_line || ':' || call_site_byte
  caller_id STRING,
  call_site_line INT64,
  call_site_byte INT64,
  callee_simple STRING,
  receiver_expr STRING,
  reason STRING,              -- 'phantom_unresolved_receiver' | 'chained_receiver' | 'name_only_zero_candidates'
  PRIMARY KEY(id)
)
CREATE REL TABLE UNRESOLVED_AT(FROM Symbol TO UnresolvedCallSite)
```

**Decision** (§7 Decision 3): **Option B**. Kuzu's JSON column ergonomics for the projected query patterns (`describe(method_id)` listing unresolved sites; debuggability CLI) are worse than a sibling row table; and a row table keeps the option open to add resolver-quality metrics later without re-encoding JSON.

`UnresolvedCallSite` is intentionally *not* a Symbol-kind node. It is its own node type. It does not participate in any other edge.

### §3.3 — `pass3_calls` emission changes

At `build_ast_graph.py:1188-1212`:

| Today | Tomorrow |
|---|---|
| Chained-receiver → emit phantom Symbol + `CALLS(strategy='chained_receiver', resolved=False, confidence=0.0)` | Emit `UnresolvedCallSite(reason='chained_receiver')` + `UNRESOLVED_AT(caller, ucs)` |
| Unresolved receiver → emit phantom Symbol + `CALLS(strategy='phantom', resolved=False, confidence=0.0)` | Emit `UnresolvedCallSite(reason='phantom_unresolved_receiver')` + `UNRESOLVED_AT(caller, ucs)` |
| Resolved receiver, name-only-fb, zero candidates → currently emits a phantom too (verify in PR-1) | Emit `UnresolvedCallSite(reason='name_only_zero_candidates')` + `UNRESOLVED_AT(caller, ucs)` |
| Resolved + candidates found → emit `CALLS(...)` | Emit `CALLS(..., callee_declaring_role=<parent.role or 'OTHER'>)` |

`_phantom_method_id` and `tables.phantoms` are deleted in PR-1.

### §3.4 — `neighbors_v2` `edge_filter` surface

New optional argument on `neighbors_v2`:

```python
class EdgeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Generic confidence / strategy axes (applicable to any confidence-bearing edge)
    min_confidence: float | None = None
    exclude_strategies: list[str] | None = None
    include_strategies: list[str] | None = None  # exclusive with exclude_strategies

    # CALLS-specific axes
    callee_declaring_role: str | None = None              # exact match
    callee_declaring_roles: list[str] | None = None       # any-of
    exclude_callee_declaring_roles: list[str] | None = None
```

Behavior:

- A `neighbors_v2` call with `edge_types=['CALLS']` and `edge_filter={callee_declaring_role: 'SERVICE'}` returns the **same ordered stream** as the unfiltered call, with rows whose `callee_declaring_role != 'SERVICE'` projected out. Ordering by `(call_site_line, call_site_byte)` is unchanged.
- `edge_filter` is **per-call, not per-edge-type**. If multiple `edge_types` are requested, the filter applies to all of them; attributes that don't exist on a given edge type are silently ignored (e.g. `callee_declaring_role` on `OVERRIDES` is a no-op).
- All `edge_filter` axes that touch attribute names whose semantics are CALLS-specific are documented as such and validated against `EDGE_SCHEMA` (the validation is a CI test, not a runtime cost).

**Decision** (§7 Decision 8): `EdgeFilter` is a typed Pydantic model, not a free-form dict. The "no per-edge knob explosion" rule is enforced by keeping the model small and reviewing additions through a propose, not by making the surface dynamic.

### §3.5 — `describe(method_id)` extension AND in-line `neighbors_v2` interleaving

**Two surfaces, not one.** The sidecar describe rollup is the *recall* surface; the agent reading a method body needs the unresolved sites *in source order*, interleaved with resolved CALLS rows. Both surfaces matter.

**Surface A — `describe(method_id)` rollup.**

```
unresolved_call_sites: 3
  - line 47: chained_receiver  (clientBuilder.someConfig().build() — receiver expression)
  - line 89: phantom_unresolved_receiver  (callee_simple='log', receiver_expr='LOG')
  - line 102: name_only_zero_candidates  (callee_simple='process', receiver_expr='this')
```

Capped at 5 rows inline; full list available via the CLI in §3.6. Fits the existing rollup machinery (cf. `DECLARES.DECLARES_CLIENT` at `kuzu_queries.py:625`).

**Surface B — `neighbors_v2` opt-in interleaved view.**

`neighbors_v2` gains a per-call optional argument `include_unresolved: bool = False`. When `True` **and** `edge_types` includes `'CALLS'`, the response interleaves `UnresolvedCallSite` projections with resolved CALLS rows, ordered jointly by `(call_site_line, call_site_byte)`. Unresolved entries appear as a distinct row type with a discriminator field:

```python
class UnresolvedCallSiteRow(BaseModel):
    row_kind: Literal["unresolved_call_site"] = "unresolved_call_site"
    caller_id: str
    call_site_line: int
    call_site_byte: int
    callee_simple: str
    receiver_expr: str
    reason: str  # 'phantom_unresolved_receiver' | 'chained_receiver' | 'name_only_zero_candidates'
```

Resolved CALLS rows continue to use the existing `CallEdgeRow` shape with `row_kind="call_edge"` added (default; back-compat at the wire level). The discriminator lets the agent distinguish at parse time without inspecting absence-of-fields.

**`include_unresolved` ignores `edge_filter`.** If the agent filters resolved CALLS by `callee_declaring_role='SERVICE'`, the interleaved unresolved sites still appear — they have no `callee_declaring_role` (the resolver gave up before reaching a declaring type). Documented as locked Decision 26. The agent that wants a *clean filtered transcript* omits `include_unresolved`; the agent that wants a *complete annotated transcript* sets it and ignores `edge_filter`.

**Default `False`.** Agents who don't know about the option get today's resolved-only stream. The HINTS-V4 high-fanout template (§3.10) and a new HINTS-V4 "unresolved-site presence" template (§3.10) nudge agents toward the right surface.

### §3.6 — CLI surface for graph quality

New CLI subcommand `pc unresolved-calls`:

```
pc unresolved-calls list [--method-id <id>] [--reason <reason>] [--microservice <name>] [--limit 100]
pc unresolved-calls stats [--by reason|microservice|caller_role]
```

This is the debuggability surface for the bucket of data that left `CALLS`. Not exposed via MCP — agents don't traverse unresolved sites, but the graph builder's recall quality is a real concern that needs a CLI face.

### §3.7 — CALLS-edge response shape: dedup (PR-3)

A method that calls `userRepository.save(x)` three times currently emits three `CALLS` rows. Each is structurally identical except for `(call_site_line, call_site_byte)`. PR-3 adds a response-shape option on the `neighbors_v2` row for CALLS edges:

```python
class CallEdgeRow(EdgeRowBase):
    # … existing fields …
    call_site_count: int = 1                # 1 if not deduped; N if deduped
    call_site_lines: list[int] | None = None   # populated only if call_site_count > 1 and dedup is on
```

`neighbors_v2` gains a per-call optional argument:

```python
dedup_calls: bool = False  # default off — preserves current row-per-call-site shape
```

When `dedup_calls=True`, identical `(src_id, dst_id)` pairs collapse to one row with `call_site_count=N` and `call_site_lines=[...]`. **Decision** (§7 Decision 11): default `False` to preserve back-compat in the `neighbors_v2` row shape for callers that already exist (none external, but the AMA agent prompts mention `call_site_line` directly — see §4 HV2). Opt-in is the path of least surprise.

### §3.8 — `find_callers` / `find_route_callers` behavior

No signature change. Both continue to walk `CALLS` backward; both pick up the resolved-only invariant for free. Cross-service skip behavior at `build_ast_graph.py:1218-1232` is unchanged (it already operates only on resolved candidates).

**Decision** (§7 Decision 9): we do **not** add `callee_declaring_role` filtering to `find_callers`. That helper's intent is "who calls this method?" — the callee is the parameter, not a filter axis. If an agent wants "who calls this method *from a service*?" they project `find_callers` output by the caller's own `role` attribute via a follow-up `describe`. This stays out of the helper's signature.

### §3.9 — What does NOT change

- `EDGE_SCHEMA.CALLS.src` / `.dst` / `.typical_traversals` — still `Symbol → Symbol`; ordering convention documented.
- `HTTP_CALLS` / `ASYNC_CALLS` / any other edge — no `callee_declaring_role` added (their endpoint kinds already encode role). Principle 3 explicitly forbids the symmetry argument.
- HINTS-V3 / HINTS-V4 templates — unchanged. The dot-key support from #171 still works as-is. PR-2 may add a new HINTS-V4-style success-path template (see §3.10) but the existing templates are not edited.
- `confidence` and `strategy` semantics for resolved rows — unchanged. The values stay the same; only the set of rows shrinks (no more `strategy='phantom'` or `strategy='chained_receiver'` because those rows aren't emitted).
- `FUZZY_STRATEGY_SET` / `BROWNFIELD_RESOLVER_STRATEGY_SET` — unchanged.
- The 5-hint output cap — unchanged.

### §3.10 — One new HINTS-V4-style template (PR-3)

When `neighbors([method], 'out', ['CALLS'])` returns more than a threshold (locked at 10 in §7 Decision 12) and no `edge_filter` is provided:

```python
TPL_NEIGHBORS_CALLS_HIGH_FANOUT = (
    "{n} CALLS on this method; the noisy axes are callee_declaring_role "
    "and per-call-site multiplicity. Try edge_filter={{callee_declaring_role: 'SERVICE'}} "
    "for delegation hops, edge_filter={{exclude_callee_declaring_roles: ['ENTITY','DTO']}} "
    "to drop accessor noise, or dedup_calls=True to collapse identical callees."
)
```

Priority `PRIORITY_LEAF_FOLLOWUP=2`. Fires only on the CALLS-on-method case; no equivalent for HTTP_CALLS, ASYNC_CALLS, etc. (their fan-outs are much smaller in practice).

A second template fires when the method has any `UNRESOLVED_AT` rows and `include_unresolved=False`:

```python
TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED = (
    "{n} resolved CALLS shown; this method also has {k} unresolved call sites "
    "(see describe(method_id).unresolved_call_sites, or call neighbors with "
    "include_unresolved=True for a source-ordered interleaved view)."
)
```

Priority `PRIORITY_LEAF_FOLLOWUP=2`. Fires regardless of `n` (including the 100%-unresolved case where `n=0`). Suppressed when `include_unresolved=True`.

## §4 — Use-case re-walk

| # | Use case | Today (#177-symptom) | Tomorrow |
|---|---|---|---|
| HV1 | Agent asks "what does `OrderService.process` do?" — wants source-order transcript | `neighbors([m],'out',['CALLS'])` returns 47 rows: 18 entity-accessor noise, 14 phantom/chained, 4 delegation, 11 repository. Token window pressure. | `neighbors([m],'out',['CALLS'])` returns 33 rows (14 phantom/chained gone). Same `(line, byte)` ordering. Hint fires (33 > 10) recommending filter axes. |
| HV2 | Agent walks method body with `call_site_line` | Today's row shape | Identical row shape — no field removed. `call_site_count` defaults to 1. |
| HV3 | Agent asks "does `OrderService.process` invoke the repository?" | Manual filter on `neighbors` output by FQN substring | `neighbors([m],'out',['CALLS'], edge_filter={callee_declaring_role: 'REPOSITORY'})` returns the persistence hops in source order. |
| HV4 | Agent asks "where does `OrderService.process` delegate to other services?" | Manual filter | `edge_filter={callee_declaring_role: 'SERVICE'}` |
| HV5 | Agent asks "drop accessor noise" | Manual filter | `edge_filter={exclude_callee_declaring_roles: ['ENTITY','DTO']}` |
| HV6 | Agent asks "who calls `OrderService.process`?" via `find_callers` | Returns resolved + phantom rows; agent post-filters | Returns resolved rows only. No signature change. |
| HV7 | `trace_request_flow` step from a controller method | Currently walks `CALLS` with implicit phantom inclusion | Walks resolved-only CALLS. Same downstream chain shape. No new fan-out. |
| HV8 | Graph-quality engineer asks "how many unresolved sites in `payment-service`?" | Greps `CALLS` for `resolved=False` — gone after PR-1 | `pc unresolved-calls stats --by microservice` (CLI) |
| HV9 | Graph-quality engineer asks "show me unresolved sites in `OrderService.process`" | Currently visible as phantom-dst CALLS rows | `describe(method_id).unresolved_call_sites` (capped at 5) or `pc unresolved-calls list --method-id <id>` |
| HV10 | Agent asks `neighbors([class_id], 'out', ['CALLS'])` (wrong kind for CALLS post-flip rule) | HINTS-V3 already covers this case | Unchanged — HINTS-V3 covers `member_only=True` for CALLS. |
| HV11 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={min_confidence: 0.5}` | n/a | Filters by confidence. Note: most resolved rows have confidence ≥ 0.5 already; this is a graceful no-op for typical agent prompts. |
| HV12 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={exclude_strategies: ['method_reference']}` | n/a | Drops `Foo::bar` style edges from the transcript. |
| HV13 | Agent calls `neighbors` with `edge_types=['CALLS','OVERRIDES'], edge_filter={callee_declaring_role: 'SERVICE'}` | n/a | Filter applies to CALLS rows; ignored on OVERRIDES (attribute doesn't exist on the edge). No error. Documented as "filters are per-call, attributes silently no-op on edges that don't carry them." |
| HV14 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={include_strategies: ['exact'], exclude_strategies: ['fuzzy']}` | n/a | Pydantic-level validation error — `include_strategies` and `exclude_strategies` are mutually exclusive. |
| HV15 | Agent calls `neighbors([m], 'out', ['CALLS'], dedup_calls=True)` on a method that calls `repo.save` 3× | n/a | Returns one row with `call_site_count=3, call_site_lines=[47, 89, 102]`. |
| HV16 | Agent runs a HINTS-V4 success-path template after `neighbors([m],'out',['CALLS'])` returns 33 rows | n/a | `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` fires; agent sees three concrete next-step suggestions. |
| HV17 | `find_callers("repositorySave", min_confidence=0.5)` | Today returns resolved + phantom (filtered by confidence) | Returns resolved only. Behavior change is *less noise*, not *missing data*. |
| HV18 | A method has 100% of its calls unresolved (all `this.x()` chains or fully phantom) | Currently shows 100% phantom CALLS rows | `neighbors` returns 0 rows. `describe` rolls up "5+ unresolved call sites" with a "see CLI for full list" footer. HINTS-V4 success-path-empty hint fires. |
| HV19 | A CI test asserts no agent prompt grep'd for `resolved=False` or `strategy in ('phantom','chained_receiver')` on CALLS rows | n/a | After PR-1, this is structurally guaranteed (rows don't exist). |
| HV20 | `EDGE_SCHEMA` consistency CI test (HINTS-V3 HV19 equivalent) | Currently green | After PR-1: `CALLS.member_only=True` (already set in #157), `EdgeSpec` traversal hints unchanged, `callee_declaring_role` registered as a known filterable attribute on the edge. |

### Awkward cases

- **HV18** (100% unresolved): some methods may genuinely have all their calls resolve to dynamic/reflective paths. The empty-CALLS case used to communicate "this method has invocations we couldn't resolve"; now it communicates "this method has no resolved invocations." The describe rollup + the HINTS-V4 success-path-empty hint are what bridge that gap. PR-3 must verify both fire correctly.
- **HV13** (filter ignored on edges where attribute doesn't exist): documented as silent no-op, not error. Reviewer pushback expected; the alternative (raise on unknown attribute) is worse because it breaks "agents add filters speculatively and let them be no-ops on edges that don't carry the attribute."

### §4.5 — Pre-#177 use cases (regression-style)

These rows capture the workflows that *triggered* #177 — the things an agent was trying to do when the noise wall got in the way. They stress-test the design against the question "would this propose have prevented #177?", not just "can the design be used."

| # | Use case | Today (#177-trigger) | Tomorrow |
|---|---|---|---|
| HV21 | End-to-end "explain `OrderService.process`" | Agent calls `neighbors(out,[CALLS])` → 35 rows (18 accessor + 14 phantom/chained + 4 delegation + 11 repo) → token-window pressure → agent picks 5 random callees → explanation misses the persistence layer | Agent calls `neighbors(out,[CALLS])` → 21 resolved rows in source order, `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` fires ("+5 unresolved sites"), `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` suggests `edge_filter={callee_declaring_role:'SERVICE'}` for delegation skeleton → agent makes a 2nd call with the filter → explanation covers delegation + persistence + unresolved warning. |
| HV22 | Two-pass exploration: skeleton then transcript | Agent gets one wall; reading order ambiguous | Pass 1: `edge_filter={callee_declaring_role:'SERVICE'}` → 4 rows (the delegation skeleton). Pass 2: no filter → 21 rows (full transcript). Two calls, both cheap; same-key results are independent (no implicit cache invalidation). |
| HV23 | Partial-unresolution method (8 resolved + 5 unresolved interleaved) | Agent sees 13 CALLS rows in `(line,byte)` order with 5 phantom-dst rows mixed in | Agent calls `neighbors(out,[CALLS], include_unresolved=True)` → 13 rows in source order, 5 with `row_kind="unresolved_call_site"` and `reason`, 8 with `row_kind="call_edge"`. Reading-order preserved; unresolved entries carry enough metadata (callee_simple, receiver_expr) to be useful in the transcript. |
| HV24 | Cross-microservice CALLS surprise | Today: skipped sites are logged but not graph-visible; agent thinks the method has fewer downstream hops than it does | Same as today — cross-microservice sites are *not* emitted as `UnresolvedCallSite` rows (locked Decision 25; they're cross-service policy, not resolution failures). The existing log line is kept; `pass3_skipped_cross_service` counter unchanged. Agent uses `trace_request_flow` for cross-service intent. |
| HV25 | Re-index diff intelligibility | Pre/post-index `neighbors` row count differs, no signal to the user explaining why | PR-1 updates README + AGENT-GUIDE with the migration delta. `GraphMeta.calls_total` reflects resolved-only count post-PR-1; the new `pass3_unresolved_sites_total` + `pass3_unresolved_by_reason` counters (Decision 23) appear in `describe(graph)` output, providing pre/post-comparable telemetry. |
| HV26 | Recall of unresolved callers ("who calls `save`, including unresolved sites?") | `find_callers("save")` returns resolved + phantom rows; the phantom rows expose the symbol_simple of the unresolved callee | `find_callers("save")` returns resolved-only. Recall path for unresolved: `pc unresolved-calls list --callee-simple save` (CLI). Locked Decision 27: no MCP surface for "find unresolved callers" — the workflow is debuggability, not agent traversal. |
| HV27 | Filter boundary: "calls to methods in the same package as the caller" | Manual post-filter on `neighbors` output | Out of scope for `EdgeFilter` (Decision 29). Use `NodeFilter(fqn_prefix=<caller_pkg>)` if the agent already knows the caller's package; otherwise two queries. The propose explicitly does **not** expose package-relativity in `EdgeFilter`. |
| HV28 | `cursor-pr-review` validates a PR didn't *increase* unresolved-site count | Today: review reads phantom CALLS rows | After PR-1: review reads `UnresolvedCallSite` rows via the CLI; PR-1's PR description includes a note to the `cursor-pr-review` skill maintainer. Decision 24 records the consumer; the skill update itself is out of scope for this propose. |
| HV29 | `neighbors_v2` with `depth=2` and `edge_filter` | n/a | Filter applied **at every hop**, independently. `neighbors([m], 'out', ['CALLS'], depth=2, edge_filter={callee_declaring_role:'SERVICE'})` returns SERVICE callees of `m`, then SERVICE callees of those. (Locked Decision 21.) |
| HV30 | `find_callers` confidence-filter parity check | `find_callers("save", min_confidence=0.5)` works today | Continues to work. **`find_callers` is not migrated to accept `EdgeFilter`** (Decision 28); the existing discrete `min_confidence` parameter is kept. The asymmetry (`neighbors_v2` uses `EdgeFilter`; `find_callers` uses discrete) is documented in the PR-2 description. |
| HV31 | Telemetry: `GraphMeta` counters | Today: `clients_total`, `producers_total`, `declares_client_total`, `declares_producer_total`, etc. (post SCHEMA-V2) | PR-1 adds `pass3_unresolved_sites_total INT64` and `pass3_unresolved_by_reason STRING` (JSON map). Decision 23. |
| HV32 | Interface vs concrete callee declaring role | Today: `pass3_calls` uses `_lookup_method_candidates` which walks supertypes. The declaring-type role on the returned candidate could be the interface (often `OTHER`) or the concrete class (typed). | **Locked Decision 20**: `callee_declaring_role` is sourced from the candidate's `parent_id` Symbol's `role`. `_lookup_method_candidates` already prefers same-microservice and resolved candidates; we don't change that. When resolution lands on an interface declaration (no concrete impl indexed), `callee_declaring_role` reflects the interface's role — which is typically `OTHER` for `JpaRepository`-style interfaces (no stereotype on the interface itself). PR-2 ships a `bank-chat-system` fixture validation that `JpaRepository.save` and `MyRepository extends JpaRepository<...>` both yield `callee_declaring_role='REPOSITORY'` (via the concrete `MyRepository`'s `@Repository` stereotype). The CI test HV32 locks this. |
| HV33 | Brownfield `@CodebaseRole` on declaring type | Today: brownfield role is layered onto the type's `role` via `resolve_role_and_capabilities` (`graph_enrich.py:672`) | `callee_declaring_role` picks it up transparently (it reads `parent.role`, which already reflects the brownfield layer). No additional code. Locked Decision 30 records this. |
| HV34 | `NodeFilter` + `EdgeFilter` composition | n/a | AND across both, ordering preserved. `NodeFilter(microservice='order-service')` + `EdgeFilter(callee_declaring_role='SERVICE')` returns CALLS edges from the queried method to methods whose declaring type role is `SERVICE` *and* whose owning microservice is `order-service`, in `(line, byte)` order. Locked Decision 22. |
| HV35 | The `NodeFilter.role` vs `EdgeFilter.callee_declaring_role` naming-collision trap | n/a | `NodeFilter.role` filters on the **neighbor node's own role**; for a method-kind Symbol that's almost always `OTHER` (inheritance from `_node_row` default). `EdgeFilter.callee_declaring_role` filters on the **callee's declaring type's role**. Both names retained — renaming either is a worse trade. Documented as a callout in `docs/AGENT-GUIDE.md` + a HINTS-V4 hint if `NodeFilter(role=...)` is applied with `edge_types=['CALLS']` and returns zero (locked Decision 31). |
| HV36 | Empty-filter performance | n/a | `neighbors([m],'out',['CALLS'])` with no `edge_filter` is the hot path. PR-2 includes a Kuzu predicate-pushdown sanity check: the `callee_declaring_role` column projection on resolved rows must not materially slow the empty-filter case. If profiling shows otherwise, PR-2 adds an index on `(src_id, callee_declaring_role)`. Decision 32 makes this a CI perf invariant (named scenario, not a numeric threshold). |
| HV37 | `callee_capability` filter request from a future reviewer | n/a | **Out of scope.** Locked Decision 33: `EdgeFilter` exposes only `callee_declaring_role` from the role/capability/annotation triple; capability and annotation filters are *not* added because their cardinality is high and the agent value is unclear. Re-opens via a new propose. |

### Awkward cases (§4.5)

- **HV23** (interleaved view): the discriminator-field approach (`row_kind`) is verbose. The alternative (heterogeneous list without discriminator) is worse — agents would have to infer entry type from absence-of-fields, which is exactly the anti-pattern this whole propose pushes back on.
- **HV32** (interface vs. concrete): the validation depends on `_lookup_method_candidates` returning the concrete impl when one exists. If it returns the interface for some edge cases (e.g. `JdbcTemplate.query(String, RowMapper<T>, Object...)` where there's no `@Repository` on `JdbcTemplate`), `callee_declaring_role` will be `OTHER`. Agents will not see those repository hops under a `callee_declaring_role='REPOSITORY'` filter. Mitigation in Decision 20: PR-2 adds an `OTHER`-fallback hint when `callee_declaring_role='SERVICE'`/`'REPOSITORY'` returns 0 results but the unfiltered call has ≥5 results — suggesting the agent try `exclude_callee_declaring_roles=['ENTITY','DTO']` instead.
- **HV36** (perf): "named scenario" rather than "numeric threshold" is the right calibration because Kuzu performance varies by hardware. The test asserts the empty-filter case is within 1.5× of the today's median latency on the `bank-chat-system` fixture; if the assertion fails, PR-2 fixes before merge.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Semantic split of `CALLS` into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE` | Breaks the ordered-transcript property (Principle 2). Agents reading a method body would have to fan-merge across edge types. |
| `callee_declaring_role` on `HTTP_CALLS` / `ASYNC_CALLS` / other edges | Those edges encode role in their endpoint kind already (Client, Producer). Principle 3 forbids the symmetry. |
| `caller_role` filter on `find_callers` | Caller's role is queryable post-hoc via `describe`. Adding it to the helper expands surface for marginal gain (§3.8 Decision 9). |
| Per-call-site dedup as a default | Off by default; opt-in via `dedup_calls=True` to preserve back-compat with the agent's familiar row-per-call-site shape (§3.7 Decision 11). |
| Move `unresolved_call_sites` to MCP `neighbors` | Agents don't traverse them. Exposed via `describe` rollup (capped) and CLI (full). |
| Replace `confidence` / `strategy` with a `quality` enum | Out of scope. Existing values still apply; only the rows shrink. |
| Add `framework`, `microservice`, or other node-derived attributes to CALLS rows | Already queryable via `NodeFilter`. Edge-attribute filters are for things the build process computes for the *edge*, not for things derivable from the endpoint nodes. |
| Cross-edge filter composition (e.g. "CALLS to a method whose caller is in microservice X") | Out of scope. Use two-step `neighbors` queries or a richer DSL in a future propose. |
| Re-emit phantom CALLS rows behind a feature flag | No active users / no soft migration. |
| Localized hint text | Out of scope — consistent with HINTS-V3/V4. |
| `callee_capability` / `callee_annotation` filter axes | Cardinality high, agent value unclear; locked Decision 33. Re-opens via a new propose. |
| `EdgeFilter` on `find_callers` / `find_route_callers` | Discrete `min_confidence` parameter is kept; asymmetry with `neighbors_v2` is intentional (HV30 / Decision 28). |
| MCP surface for "find unresolved callers by callee_simple" | Debuggability path lives in `pc unresolved-calls list --callee-simple <name>`; not an agent traversal pattern (Decision 27). |
| Cross-microservice-skipped CALLS sites becoming `UnresolvedCallSite` rows | Cross-service policy, not a resolution failure; existing log line + counter unchanged (Decision 25). |
| Renaming `NodeFilter.role` or `EdgeFilter.callee_declaring_role` to disambiguate | Naming-collision trap mitigated by docs + HINTS-V4 hint (Decision 31); rename is a worse trade. |
| Package-relativity filters in `EdgeFilter` (e.g. same-package callees) | Out of scope; use `NodeFilter(fqn_prefix=...)` or two queries (HV27). |

## §6 — Migration plan — 3 PRs

This propose locks before any code PR merges. The propose itself merges as a separate PR (no code) and then the three code PRs follow in order.

### PR-1 — resolved-only CALLS (+ UnresolvedCallSite node + UNRESOLVED_AT edge)

**Title**: `feat(schema): CALLS carries only resolved invocations; unresolved sites move to UnresolvedCallSite`

**Purpose**:
- Bump `ONTOLOGY_VERSION` to 15.
- Add `UnresolvedCallSite` node table + `UNRESOLVED_AT` edge table.
- Change `pass3_calls` (`build_ast_graph.py:1188-1212`) to emit `UnresolvedCallSite` instead of phantom Symbol + `CALLS(strategy='phantom'|'chained_receiver')`.
- Delete `_phantom_method_id` and `tables.phantoms`.
- Add `callee_declaring_role` to `CALLS` DDL; populate at emission.
- Remove `resolved` from `CALLS` DDL.
- Extend `describe` for method-kind Symbol with the `unresolved_call_sites` rollup (capped at 5).
- README + AGENT-GUIDE "Re-index required" sections updated.

**Test summary**: named scenarios — pass3 emits zero CALLS rows for chained-receiver / unresolved-receiver / name-only-zero-candidates sites; emits `UnresolvedCallSite` + `UNRESOLVED_AT` for each; emits CALLS with `callee_declaring_role` populated for resolved sites against fixtures `bank-chat-system` and `parser-fixture`; `EDGE_SCHEMA` snapshot test reflects the DDL change; `find_callers` returns zero phantom-dst results on `bank-chat-system`; `describe` rollup on a method with 8 unresolved sites shows "8 unresolved call sites" with first 5 inline.

### PR-2 — `EdgeFilter` on `neighbors_v2` + CLI subcommand

**Title**: `feat(mcp): EdgeFilter on neighbors_v2; pc unresolved-calls CLI`

**Purpose**:
- Add `EdgeFilter` Pydantic model in `mcp_v2.py`.
- Wire it through `neighbors_v2` and the underlying Kuzu query (`kuzu_queries.py` neighbors path).
- Add `min_confidence`, `exclude_strategies`, `include_strategies`, `callee_declaring_role`, `callee_declaring_roles`, `exclude_callee_declaring_roles` as fields.
- Pydantic-level validation: `include_strategies` xor `exclude_strategies`.
- Add `pc unresolved-calls list` and `pc unresolved-calls stats` CLI subcommands (in `docs/JAVA-CODEBASE-RAG-CLI.md` + the CLI binary).
- Update `MCP_HINTS_FIELD_DESCRIPTION` and `EDGE_SCHEMA` snapshot to register `callee_declaring_role` as a known filterable attribute on CALLS.
- Add `include_unresolved: bool = False` to `neighbors_v2`. When `True` and `'CALLS'` in `edge_types`, interleave `UnresolvedCallSite` rows (`row_kind="unresolved_call_site"`) with resolved `CallEdgeRow` (`row_kind="call_edge"`) in `(call_site_line, call_site_byte)` order. `edge_filter` is ignored when `include_unresolved=True` (Decision 26).
- Add `OTHER`-fallback hint when role filter returns 0 but unfiltered ≥5 results (Decision 20).
- Add `NodeFilter(role=...)` vs `EdgeFilter.callee_declaring_role` collision hint (Decision 31).
- Add Kuzu predicate-pushdown perf named-scenario test on `bank-chat-system` (Decision 32).
- Revisit the MCP-V2 "no per-edge filter" design rule in the PR description; record the supersession (this propose plus the locked principle 3).

**Test summary**: named scenarios — filter projects ordered stream by role; filter ignored on non-CALLS edges (silent); filter xor validation raises Pydantic error; CLI `pc unresolved-calls list --method-id <id>` returns all unresolved sites for a method; `--by reason` stats aggregate correctly.

### PR-3 — CALLS-edge dedup + HINTS-V4 high-fanout template

**Title**: `feat(hints, neighbors): CALLS dedup option + high-fanout success-path hint`

**Purpose**:
- Add `dedup_calls: bool = False` to `neighbors_v2`.
- Extend `CallEdgeRow` with `call_site_count: int` and `call_site_lines: list[int] | None`.
- Add `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` template (§3.10) wired through the existing HINTS-V4 success-path generator.
- Add `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` template (§3.10) wired through the same generator. Suppressed when `include_unresolved=True`.
- Add HV19 invariant test.

**Test summary**: named scenarios — dedup collapses identical `(src,dst)` pairs; default `dedup_calls=False` produces today's shape; high-fanout template fires above threshold; does not fire when `edge_filter` is provided; threshold value (10) covered by named scenario.

## §7 — Decisions taken (no longer open)

1. **`CALLS` carries only resolved invocations.** Phantom + chained-receiver + name-only-zero-candidates sites are not edges; they are caller-side facets.
2. **`resolved BOOLEAN` is removed from `CALLS` DDL.** Existence implies resolution.
3. **`UnresolvedCallSite` is a sibling node table, not a JSON column on Symbol.** PR-1.
4. **`UnresolvedCallSite` is not a Symbol-kind node** and does not participate in any other edge. It is reachable only via `UNRESOLVED_AT` from its caller, via `describe`, and via the CLI.
5. **`callee_declaring_role` becomes a `CALLS` edge attribute,** populated at `pass3_calls` emission time from the callee parent's `role`. Default `OTHER` if parent is missing or unroleable.
6. **No semantic split of `CALLS`** into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE` / `UNRESOLVED_CALL`. Ordered-transcript property is the dominant agent use case (Principle 2).
7. **No `callee_declaring_role` on `HTTP_CALLS` / `ASYNC_CALLS` or other edges.** Their endpoint kinds already encode role (Principle 3).
8. **`EdgeFilter` is a typed Pydantic model with `extra='forbid'`**, not a free-form dict. Field additions go through a propose.
9. **`find_callers` / `find_route_callers` do not gain `callee_declaring_role`** filtering (§3.8).
10. **`edge_filter` is per-call, not per-edge-type.** Attributes silently no-op on edges that don't carry them (HV13).
11. **`dedup_calls=False` by default** in `neighbors_v2`. Opt-in. Preserves the agent-familiar row-per-call-site shape (HV2, HV15).
12. **High-fanout HINTS-V4 template threshold = 10 CALLS rows.** Locked by §3.10.
13. **`unresolved_call_sites` is exposed via `describe` (capped at 5)** and via the `pc unresolved-calls` CLI, not via `neighbors`.
14. **No back-compat alias for the removed `resolved=False` CALLS rows.** Per locked repo rule "Breaking changes allowed; no active users."
15. **`include_strategies` and `exclude_strategies` are mutually exclusive** in `EdgeFilter`. Pydantic validator enforces.
16. **`MCP-API-V2-REDESIGN-PROPOSE.md`'s "no per-edge filter on `neighbors`" rule is superseded** by Principle 3 of this propose. Recorded in PR-2 description.
17. **`pass3_calls` cross-microservice skip behavior is unchanged.** Today's same-microservice candidate preference at `build_ast_graph.py:1218-1232` continues to apply.
18. **PR-1 is a single re-index moment.** ONTOLOGY_VERSION 14 → 15. PR-2 and PR-3 do not bump it again.
19. **Three sub-PRs, sequential.** PR-1 → PR-2 → PR-3. No parallelization.
20. **`callee_declaring_role` is sourced from the candidate's `parent_id` Symbol's `role`** — i.e. the role of the type that declares the resolved callee method, after `_lookup_method_candidates` has chosen between interface and concrete declarations. Today's preference order (same-microservice > resolved > supertype walk) is unchanged. When resolution lands on an interface with no stereotype, the value is `OTHER`. PR-2 validates against `bank-chat-system` that the common `Spring @Repository` case yields `REPOSITORY`, not `OTHER`. PR-2 also adds an `OTHER`-fallback hint when a role filter returns 0 but the unfiltered call has ≥5 results.
21. **`edge_filter` is applied at every hop in `neighbors_v2(depth>1)`,** independently.
22. **`NodeFilter` and `EdgeFilter` compose with AND;** `(call_site_line, call_site_byte)` ordering is preserved when both are applied.
23. **`GraphMeta` gains `pass3_unresolved_sites_total INT64`** and **`pass3_unresolved_by_reason STRING`** (JSON-encoded `{reason: count}` map). Populated in PR-1.
24. **`cursor-pr-review` is acknowledged as a downstream consumer** of `UnresolvedCallSite`. The skill update is not part of this propose; PR-1's PR description records a note for the skill maintainer.
25. **Cross-microservice-skipped CALLS sites are not `UnresolvedCallSite` rows.** Existing log line + `pass3_skipped_cross_service` counter are kept.
26. **`include_unresolved=True` ignores `edge_filter`.** The interleaved view is "the complete annotated transcript"; the filtered view is "the clean projection." They are different surfaces for different agent intents.
27. **No MCP surface for "find unresolved callers by `callee_simple`."** The workflow is debuggability via `pc unresolved-calls list --callee-simple <name>`, not agent traversal.
28. **`find_callers` / `find_route_callers` are not migrated to accept `EdgeFilter`.** Their existing discrete `min_confidence` parameter is kept. The asymmetry with `neighbors_v2` is intentional and documented.
29. **Package-relativity / cross-edge composition filters are out of scope for `EdgeFilter`.** Use `NodeFilter(fqn_prefix=...)` or chained `neighbors` calls.
30. **Brownfield `@CodebaseRole`-derived role is picked up transparently** by `callee_declaring_role` because it reads `parent.role`, which already reflects the brownfield layer (`graph_enrich.py:672`). No additional code.
31. **`NodeFilter.role` and `EdgeFilter.callee_declaring_role` are intentionally not renamed.** The collision is documented in `docs/AGENT-GUIDE.md` and mitigated by a HINTS-V4 hint that fires when `NodeFilter(role=...)` is applied to `neighbors([m],'out',['CALLS'])` and returns zero rows (recommending the agent likely meant `EdgeFilter(callee_declaring_role=...)`).
32. **PR-2 ships a Kuzu predicate-pushdown sanity test.** Named scenario: empty-filter `neighbors([m],'out',['CALLS'])` on `bank-chat-system` is within 1.5× of today's median latency. If the test fails at PR-2 review time, PR-2 adds an index on `(src_id, callee_declaring_role)` and re-validates.
33. **`EdgeFilter` exposes only `callee_declaring_role` from the role/capability/annotation triple.** Capability and annotation filters are out of scope. Re-opens via a new propose if agent value emerges later.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Agents (or skills) currently grep CALLS rows for `resolved=False` to inspect graph quality | Documented in PR-1 description as a breaking change; `pc unresolved-calls list` is the replacement; HV8/HV9 named scenarios verify the new surface works. |
| `_phantom_method_id` / `tables.phantoms` removal breaks downstream code I haven't audited | PR-1's diff is the audit. Sentinel grep checks in the cursor task prompt enumerate every reference. |
| `callee_declaring_role` is misleading for callees whose parent type's role is `OTHER` | Default `OTHER` is honest. The `OTHER` bucket is itself an informative filter target ("exclude OTHER" is a reasonable agent move). |
| `EdgeFilter` Pydantic surface grows uncontrolled over time | Field additions require a propose. CI test asserts `EdgeFilter.model_fields` matches a snapshot. |
| High-fanout template fires too often | Threshold (10) is conservative; can be raised in a follow-up without re-index. Hint is advisory, not breaking. |
| `UnresolvedCallSite` table grows unboundedly on large codebases | Same cardinality as today's phantom Symbol count. Net storage cost ≈ unchanged. |
| Describe rollup of `unresolved_call_sites` adds noise to the describe output | Capped at 5 inline; footer says "see `pc unresolved-calls list --method-id <id>` for the full list." Behavior matches existing DECLARES.* describe rollup pattern. |
| `dedup_calls=True` loses per-call-site line resolution for some downstream use case | Opt-in only; `call_site_lines` is populated when dedup is on, preserving the data. |
| `find_callers` callers that relied on phantom-dst rows being present silently break | Audited in PR-1; CI test asserts `find_callers` returns zero unresolved-strategy rows on `bank-chat-system`. |
| `pc unresolved-calls stats --by caller_role` requires joining `UnresolvedCallSite` → `UNRESOLVED_AT` → caller Symbol → declaring type | One extra hop; acceptable for a CLI debuggability surface. Not on a hot path. |

## Appendix A — Concrete DDL diff

```sql
-- CALLS DDL (build_ast_graph.py:2317-2319)
-- Before:
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN);

-- After:
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING,
  callee_declaring_role STRING);

-- New tables:
CREATE NODE TABLE UnresolvedCallSite(
  id STRING,
  caller_id STRING,
  call_site_line INT64,
  call_site_byte INT64,
  callee_simple STRING,
  receiver_expr STRING,
  reason STRING,
  PRIMARY KEY(id)
);

CREATE REL TABLE UNRESOLVED_AT(FROM Symbol TO UnresolvedCallSite);
```

## Appendix B — Traceability

**Revision 1 (2026-05-18, post-author-grill)** — additions only, no decisions removed:

- Added §4.5 (HV21–HV37): regression-style use cases covering the workflows that triggered #177.
- Added Surface B (`include_unresolved`) to §3.5: in-line interleaved transcript view with `row_kind` discriminator.
- Added `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` template to §3.10.
- Added §5 rows for capability/annotation, `EdgeFilter` on `find_callers`, unresolved-callers MCP, cross-microservice skip, naming-collision rename, package-relativity.
- Added Decisions 20–33 to §7 covering: interface-vs-concrete resolution (20), depth>1 semantics (21), NodeFilter+EdgeFilter composition (22), GraphMeta telemetry (23), cursor-pr-review consumer (24), cross-microservice non-`UnresolvedCallSite` policy (25), `include_unresolved` vs `edge_filter` exclusivity (26), no-unresolved-callers MCP (27), `find_callers` discrete-parameter asymmetry (28), package-relativity scope (29), brownfield role transparency (30), NodeFilter/EdgeFilter naming collision (31), perf invariant (32), no capability/annotation filters (33).
- Updated TL;DR and PR-2/PR-3 deliverables to match.

**What stayed unchanged from the original draft**:
- Decisions 1–19 unchanged.
- Principles 1–8 unchanged.
- DDL diff (Appendix A) unchanged.
- 3-PR migration sequence unchanged.
- HV1–HV20 use-case rows unchanged.

**What changed and why** (decisions → surface):
- Surface B (`include_unresolved`) added because HV23 (partial-unresolution method) made it clear the sidecar describe rollup wasn't enough — agents reading a method body need unresolved sites *in source order*, not five lines below the transcript.
- HV32 (interface-vs-concrete) and Decision 20 added because the filter value's dependency on `_lookup_method_candidates` resolution was the biggest unaddressed gap from author-grill round 1. The `OTHER`-fallback hint mitigates the worst case.
- Decision 31 (no rename of `NodeFilter.role` vs `EdgeFilter.callee_declaring_role`) added because the collision is real and a rename costs more than a docs callout + hint.

**Cross-propose references**:
- Supersedes `propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`'s "no per-edge filter on `neighbors`" rule (Decision 16).
- Builds on `propose/completed/SCHEMA-V2-PROPOSE.md` §3.4 (`EDGE_SCHEMA`) — extends with `callee_declaring_role` registration.
- Builds on `propose/completed/HINTS-V3-PROPOSE.md` (kind/direction templates) — no template edited, one new template added in §3.10.
- Builds on `propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md` — high-fanout template plugs into the existing success-path generator.
- Resolves [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177).
