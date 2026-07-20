<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# CALLS-NOISE-AND-RESOLUTION — clean the CALLS edge by removing one bucket and projecting the other

**Status**: landed (PR-3)
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-18
**Tracks**: [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)
**Plan**: [`plans/completed/PLAN-CALLS-NOISE.md`](../plans/completed/PLAN-CALLS-NOISE.md) (per-PR sentinels, tests, landing order)
**Prompts**: [`plans/completed/AGENT-PROMPTS-CALLS-NOISE.md`](../plans/completed/AGENT-PROMPTS-CALLS-NOISE.md) (PR-1 → PR-3 handoffs)

## TL;DR

- `CALLS` is a sequence (ordered by `call_site_line`, `call_site_byte`), not a set. Source-order traversal is the dominant agent use case and must be preserved.
- Two locked moves, no new edge types:
  1. **`CALLS` sheds only true receiver-failure rows** (`strategy='phantom'` and `strategy='chained_receiver'`). Those move to a caller-side facet (`UnresolvedCallSite` + `UNRESOLVED_AT`). **Known-receiver-external** rows (`resolved=False` with preserved receiver-tier `strategy`/`confidence`), `overload_ambiguous`, name-only-fb single-candidate, `implicit_super`, `constructor`, etc. **stay as `CALLS` rows**.
  2. **`callee_declaring_role` becomes a `CALLS` edge attribute.** `neighbors_v2` gains a single-edge-type, fail-loud `edge_filter` that projects the ordered stream by edge attributes without breaking ordering.
- Migration: 3 sequential code PRs — PR-2/PR-3 MCP knobs are additive until used; PR-1 supertype dedup changes row cardinality at duplicate sites; PR-3 breaks phantom/chained `CALLS` rows. Ontology bump 14 → 15 in PR-1. One re-index across the sequence.
- Two `neighbors_v2` consumption modes for CALLS: default = resolved + known-external clean stream (today's shape minus phantom/chained rows after PR-3); `include_unresolved=True` = interleaved transcript with `row_kind` discriminator. **`include_unresolved=True` is mutually exclusive with `edge_filter`** (fail-loud) — composing them was reverted in revision 3 because it would re-introduce unfiltered noise.
- `neighbors_v2` stays one-hop. Multi-hop is out of scope and would need its own propose (visited-set, cycles, fanout cap, hint behavior).
- Out: `min_confidence` and `exclude_strategies` as `neighbors_v2` parameters (subsumed by `edge_filter`); a `CALLS` semantic split into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE`; `callee_capability` / `callee_annotation` / `callee_microservice` filter axes; `EdgeFilter` on `find_callers`; an MCP surface for "find unresolved callers"; package-relativity filters; multi-hop `neighbors_v2`; per-edge dedup as a `neighbors_v2` knob (kept as a CALLS-specific response shape decision in PR-3).
- **MCP ordering contract (PR-2):** `neighbors_v2` today does not `ORDER BY` CALLS rows; PR-2 locks `ORDER BY e.call_site_line, e.call_site_byte` for every CALLS path (flat, `edge_filter`, and PR-3 `include_unresolved` interleave).
- **`exclude_external` is not added to `neighbors_v2`.** It remains on `find_callers` / `find_callees` only (Decision 38). JDK/library noise on default `neighbors(out, ['CALLS'])` is addressed via `edge_filter` (`min_confidence`, `exclude_strategies`) after PR-2, not FQN-prefix rules.
- **Accessor noise is only partly solved** by `callee_declaring_role`; entity getter/setter discrimination needs heuristics — see cross-link to [`propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) `/mini-map` (Decision 39).
- Non-obvious constraint: `pass3_calls` and `find_callers` currently rely on phantom Symbol rows existing as the `dst` endpoint of `strategy='phantom'`/`'chained_receiver'` CALLS rows. PR-3 changes that invariant atomically. Known-receiver-external phantom-FQN rows (line 1257-1271) are kept; only true receiver-failure phantoms are removed.

### Fixture anchors (pinned for tests and HV rows)

Do **not** use fictional types (`OrderService`, `MyRepository`) in committed test names or
perf scenarios. See [`plans/completed/PLAN-CALLS-NOISE.md`](../plans/completed/PLAN-CALLS-NOISE.md) § Fixture anchors.

| Anchor | FQN / path | Measured on fresh `bank-chat-system` (2026-05-19) |
| --- | --- | --- |
| High-fanout method | `com.bank.chat.engine.processors.ClientMessageProcessor#process(ProcessingContext,InternalEvent)` | **57** outbound `CALLS`; **5** `phantom` + **3** `chained_receiver` removed after PR-3 → **~49** default rows |
| Supertype dedup | `tests/fixtures/call_graph_smoke/` (`SupertypeDedupPatterns`, PR-1 adds) | Bank has **no** interface+concrete duplicate `save` sites |
| `overload_ambiguous` | `call_graph_smoke` `smoke.OverloadPatterns#sameArity` | Bank: **0** `overload_ambiguous` rows |

## §1 — Frame

> `CALLS` is the ordered transcript of one method's body. Its job is to be readable in source order; its noise is to mix true receiver-failure invocations with semantically-loaded ones.

`CALLS` has been carrying two unrelated burdens. **Burden one**: representing real invocations the agent might want to traverse in order — including known-external library calls (JDK/Spring/Lombok) where the receiver type is resolved but the callee method isn't indexed. These are not noise; they are honest "we know where this goes but the callee body isn't in scope" rows. **Burden two**: encoding "the resolver gave up on the receiver" via `strategy='phantom'` (unresolved receiver) and `strategy='chained_receiver'` rows that point at synthetic Symbol rows the agent has no traversal use for. Burden one is what `CALLS` is for; burden two is a graph-completeness convenience that became a per-query tax on every agent.

The frame rules out four things:

- **A `CALLS` semantic split** (`DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE`). It breaks the ordered-transcript property — agents reading a method body would have to 4-way fan-merge by `(line, byte)` across edge types.
- **A new `neighbors_v2` parameter per noise dimension** (`min_confidence`, `exclude_strategies`, `callee_role`, etc). Each one is a stop-gap. We add **one** general-purpose edge-attribute filter mechanism instead.
- **Reasoning about why a particular edge is noise at query time.** The graph builder already knows whether a call site is resolved and what role its target's declaring type has. We push that data into the graph at build time, not infer it at query time.
- **Discarding receiver-tier metadata for known-external calls.** `build_ast_graph.py:1257-1271` already preserves `confidence`, `strategy`, `arg_count`, and a deterministic phantom FQN for "receiver resolved, callee not indexed" cases. README §"Phantom nodes" documents this. Stripping that on the basis of `resolved=False` alone loses real signal. **`exclude_external` is not on `neighbors_v2`** — it stays on `find_callers` / `find_callees` (see §3.4.1). On `neighbors`, JDK noise is dropped via `edge_filter` after PR-2, not FQN-prefix rules.

## §2 — Design principles

1. **`CALLS` is a sequence of invocations whose receiver was resolved.** True receiver-failure rows (`strategy='phantom'`, `strategy='chained_receiver'`) are not edges; they are caller-side metadata. Receiver-resolved-but-callee-not-indexed rows stay in `CALLS` with `resolved=False`.
2. **Edge type splits only when sequential interleaving is never wanted.** `CALLS` fails this test; no semantic split.
3. **One general-purpose edge-attribute filter on `neighbors_v2`, not one knob per attribute.** `edge_filter: EdgeFilter` (typed model in §3.4); attributes added later inherit the surface for free.
4. **All filter values are populated at build time from declared graph state.** No query-time inference, no per-row recomputation in `neighbors_v2`.
5. **Default `neighbors([m], 'out', ['CALLS'])` returns the same shape and ordering as today, minus true-receiver-failure rows.** Known-external rows are kept. No new required arguments.
6. **`find_callers` / `find_route_callers` keep their current semantics.** They aggregate over the new ordered stream (no phantom-receiver rows); the `min_confidence` parameter that already exists on them is unchanged.
7. **`edge_filter` is single-edge-type-scoped and fail-loud on inapplicable attributes.** `edge_types=['CALLS','OVERRIDES']` + `edge_filter={callee_declaring_role:'SERVICE'}` raises a `ValueError` with a teaching message ("`callee_declaring_role` is not on `OVERRIDES`; split into two `neighbors_v2` calls or restrict `edge_types`"). This matches the existing `_nodefilter_inapplicable_fields` fail-loud pattern at `mcp_v2.py:191-206`.
8. **Decisions about call-site semantics live next to the data, in `pass3_calls`, not in `neighbors_v2`.** Edge attributes are computed at emission time.
9. **`neighbors_v2` CALLS results are source-ordered at the MCP layer.** Every CALLS query path uses `ORDER BY e.call_site_line, e.call_site_byte` before `offset`/`limit` (Decision 36). Empty-filter and `edge_filter` projections share the same ordering contract.

## §3 — The proposed surface

### §3.1 — `CALLS` DDL change

Current (`build_ast_graph.py:2317-2319`):

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN)
```

Proposed (PR-1 adds `callee_declaring_role`; PR-3 keeps `resolved` since known-external rows still use it):

```sql
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING)
```

Added in PR-1: `callee_declaring_role STRING` — the `role` of the `Symbol` row that is the parent (declaring type) of the callee. Pulled from `tables.symbols[parent_of(dst)].role`. Defaults to `OTHER` if the parent is missing or unroleable (matches existing `_node_row` default).

**Not removed** (revision 3 change): `resolved BOOLEAN` stays. Known-receiver-external rows continue to use `resolved=False` exactly as today. After PR-3, the only `resolved=False` rows are known-external (receiver resolved, callee not indexed) — true receiver-failure rows have moved to `UnresolvedCallSite`.

Removed semantically in PR-3 (no DDL change): rows emitted at `build_ast_graph.py:1192-1199` (`strategy='chained_receiver'`) and `build_ast_graph.py:1204-1211` (`strategy='phantom'`, unresolved receiver branch only). The known-external branch at `build_ast_graph.py:1257-1271` (receiver resolved, candidates empty, preserves receiver-tier `strategy`/`confidence`) is **not** touched.

### §3.2 — Caller-side `UnresolvedCallSite` facet

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
  arg_count INT64,
  callee_simple STRING,
  receiver_expr STRING,
  reason STRING,              -- 'phantom_unresolved_receiver' | 'chained_receiver'
  PRIMARY KEY(id)
)
CREATE REL TABLE UNRESOLVED_AT(FROM Symbol TO UnresolvedCallSite)
```

**Decision** (§7 Decision 3): **Option B**. Kuzu's JSON column ergonomics for the projected query patterns (`describe(method_id)` listing unresolved sites; debuggability CLI) are worse than a sibling row table; and a row table keeps the option open to add resolver-quality metrics later without re-encoding JSON.

`UnresolvedCallSite` is intentionally *not* a Symbol-kind node. It is its own node type. It does not participate in any other edge. **The `reason` enum has exactly two values** (revision 3): `phantom_unresolved_receiver` and `chained_receiver`. The previously-listed `name_only_zero_candidates` reason is gone — those rows are known-external CALLS in `pass3_calls`'s line 1257-1271 branch, which is preserved.

### §3.3 — `pass3_calls` emission changes

At `build_ast_graph.py:1188-1271`:

| `pass3_calls` branch | Today | Tomorrow (PR-1 + PR-3) |
|---|---|---|
| Chained-receiver (`build_ast_graph.py:1192-1199`) | Emit phantom Symbol + `CALLS(strategy='chained_receiver', resolved=False, confidence=0.0)` | PR-3: Emit `UnresolvedCallSite(reason='chained_receiver')` + `UNRESOLVED_AT(caller, ucs)` |
| Unresolved receiver (`build_ast_graph.py:1204-1211`) | Emit phantom Symbol + `CALLS(strategy='phantom', resolved=False, confidence=0.0)` | PR-3: Emit `UnresolvedCallSite(reason='phantom_unresolved_receiver')` + `UNRESOLVED_AT(caller, ucs)` |
| Known-receiver external (`build_ast_graph.py:1257-1271`) — receiver resolved, no candidates indexed | Emit `_phantom_method_id` Symbol (deterministic FQN) + `CALLS(strategy=<receiver-tier>, confidence=<receiver-tier>, resolved=False)` | **Unchanged.** Stays as a `CALLS` row with preserved receiver-tier metadata. PR-1 adds `callee_declaring_role` from the candidate parent (or `OTHER` if parent is missing). |
| `overload_ambiguous` (`build_ast_graph.py:1284-1289`) — name-only-fb, >1 candidate, `resolved=True` | Emit N `CALLS(strategy='overload_ambiguous', resolved=True)` rows, one per candidate | **Unchanged.** N rows preserved; `overload_ambiguous` is the resolver's own ambiguity signal and stays as N rows so `find_callers` / explain flows can see it. PR-1 adds `callee_declaring_role` to each row (from each candidate's own parent). |
| Single-candidate name-only-fb (`build_ast_graph.py:1251-1255`) — `resolved=True` | Emit `CALLS(strategy=<receiver-tier>, resolved=True)` | **Unchanged.** PR-1 adds `callee_declaring_role`. |
| Resolved + single concrete candidate found | Emit `CALLS(...)` | **PR-1 adds supertype-walk dedup before emit** (Decision 33, scope-narrowed in revision 3). When `_lookup_method_candidates` returns multiple candidates *because one is the declared concrete method on the receiver type and the others are inherited supertype declarations*, collapse to the concrete-class candidate. **`overload_ambiguous` is left alone** — N rows preserved. PR-1 also adds `callee_declaring_role`. |

PR-3 deletes only the *phantom-receiver* and *chained-receiver* code paths. `_phantom_method_id` and `tables.phantoms` are **not** fully deleted — the known-external branch at line 1257-1271 still uses `_phantom_method_id` to mint deterministic external-callee FQNs. PR-3 reduces both to "used only for known-external emissions" and adds a CI test asserting no `strategy='phantom'` or `strategy='chained_receiver'` rows survive.

Multi-candidate supertype-walk dedup evidence (interface + concrete pointing at the same source-line) is preserved as a build-time `logger.debug` line, not as graph state.

#### §3.3.1 — Supertype-walk dedup pseudocode (PR-1, Decision 33)

Runs **only** on the `len(candidates) > 1` branch **before** the `overload_ambiguous` emit loop (`build_ast_graph.py:1284-1289`). Never runs when `edge_strat == 'overload_ambiguous'` (name-only-fb with `len(candidates) > 1`).

```
function collapse_supertype_duplicates(candidates, recv_type_fqn):
    if len(candidates) <= 1:
        return candidates
    concrete_on_receiver = [
        c for c in candidates
        if c.parent_fqn == recv_type_fqn
        and c.decl.signature matches call signature
    ]
    if len(concrete_on_receiver) != 1:
        return candidates   # 0 or >1 concrete on receiver — do not collapse
    concrete = concrete_on_receiver[0]
    supertypes = [
        c for c in candidates
        if c != concrete
        and c.parent_fqn is a strict supertype of recv_type_fqn (extends walk)
        and c.decl.signature matches concrete.decl.signature
    ]
    if not supertypes:
        return candidates   # not an interface+inherited-impl duplicate pattern
    if any(c for c in candidates if c not in {concrete, *supertypes}):
        return candidates   # unrelated candidate at same site — do not collapse
    log.debug("pass3 supertype dedup %s -> %s", [c.node_id for c in candidates], concrete.node_id)
    return [concrete]
```

**Non-goals (must not collapse):**

- `overload_ambiguous` (name-only-fb, multiple same-level overloads).
- Default methods / bridge methods unless the signature match is exact on the same receiver type.
- Multiple concrete implementations on the same receiver type.
- Cross-microservice candidate sets (same-ms filter already ran).

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

Behavior (revision 3, fail-loud):

- `edge_filter` requires `edge_types` to be a single edge type whose schema declares every attribute referenced in the filter. `edge_types=['CALLS']` + `edge_filter={callee_declaring_role:'SERVICE'}` projects the ordered stream by attribute.
- `edge_types=['CALLS','OVERRIDES']` + `edge_filter={callee_declaring_role:'SERVICE'}` raises a fail-loud `ValueError` with a teaching message: `"callee_declaring_role is not on OVERRIDES; restrict edge_types to ['CALLS'] or split into two neighbors_v2 calls"`. Mirrors `_nodefilter_inapplicable_fields` (`mcp_v2.py:191-206`). Increments the `[filter-frame] fail-loud category=edge_filter` counter.
- `edge_filter` axes that touch attribute names whose semantics are CALLS-specific are documented as such and validated against `EDGE_SCHEMA` (CI test).

**Decision** (§7 Decision 8): `EdgeFilter` is a typed Pydantic model, not a free-form dict. The "no per-edge knob explosion" rule is enforced by keeping the model small and reviewing additions through a propose, not by making the surface dynamic.

A future "per-edge-type-keyed filter map" (e.g. `edge_filter={'CALLS': EdgeFilter(...), 'OVERRIDES': EdgeFilter(...)}`) is explicitly deferred to a separate propose; the single-edge-type fail-loud shape is the smaller commitment.

#### §3.4.1 — `ORDER BY` contract and `edge_filter` pushdown (PR-2, Decisions 36–37)

**Gap today:** `neighbors_v2` fetches CALLS without `ORDER BY` and slices after an in-memory list (`mcp_v2.py` ~1386–1459). The graph stores `(call_site_line, call_site_byte)` but MCP does not guarantee source-order delivery.

**Locked behavior (PR-2):**

1. **Ordering.** For `edge_types == ['CALLS']` (and PR-3 `include_unresolved` interleave), every path ends with `ORDER BY e.call_site_line, e.call_site_byte` (resolved CALLS) or the same tuple on the merged transcript. `offset`/`limit` apply **after** ordering.
2. **Predicate pushdown.** When `edge_types == ['CALLS']` and `edge_filter` is set, push these into the Cypher `WHERE` clause (parameterized):
   - `e.confidence >= $min_confidence`
   - `e.strategy IN $include_strategies` / `e.strategy NOT IN $exclude_strategies`
   - `e.callee_declaring_role = $role` / `IN $roles` / `NOT IN $exclude_roles`
3. **Filter placement.** `NodeFilter` and `edge_filter` both apply **before** `offset`/`limit`. Never add a SQL/Kuzu `LIMIT` on the raw hop that runs **before** edge predicates (preserves #177 fix: filtered rows must not be truncated by an unfiltered cap).
4. **`NodeFilter` on callee.** Terminal-node `NodeFilter` may still require a join/load of `b:Symbol`; `edge_filter` predicates stay on `e:CALLS`.
5. **Perf (Decision 31).** HV34 asserts empty-filter `neighbors([m],'out',['CALLS'])` on pinned
   `ClientMessageProcessor#process` (fixture anchors) is within 1.5× pre-PR-2 median on the same
   hardware — with `ORDER BY` included. Gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

**Test names (committed in plan, not here):** `test_neighbors_calls_ordered_by_call_site`, `test_neighbors_calls_edge_filter_pushdown_in_cypher`, `test_neighbors_calls_edge_filter_before_limit`, `test_neighbors_calls_perf_empty_filter_client_message_processor`.

#### §3.4.2 — `exclude_external` stance (PR-2 docs, Decision 38)

| Surface | `exclude_external` | JDK / library noise |
|---|---|---|
| `find_callers` / `find_callees` | **Yes** (default `True`) | FQN-prefix filter on caller/callee `Symbol.fqn` |
| `neighbors_v2` | **No** (not added) | `edge_filter={min_confidence: 0.5}` and/or `exclude_strategies: ['phantom', 'chained_receiver']` (pre-PR-3); after PR-3 phantom/chained strategies are gone from CALLS — use `exclude_callee_declaring_roles: ['OTHER']` heuristically or inspect `attrs.strategy` on known-external rows |
| `/mini-map` skill | N/A (client-side) | FQN-prefix heuristic in skill body until `edge_filter` is ubiquitous |

**Docs requirement (PR-2):** `docs/AGENT-GUIDE.md` must state explicitly that `exclude_external` is **not** a `neighbors` parameter. HV37 / Decision 34 must not imply FQN-prefix exclusion on `neighbors`.

### §3.5 — `describe(method_id)` extension AND in-line `neighbors_v2` interleaving

**Two surfaces, not one.** The sidecar describe rollup is the *recall* surface; the agent reading a method body needs the unresolved sites *in source order*, interleaved with resolved CALLS rows. Both surfaces matter.

**Surface A — `describe(method_id)` rollup.**

```
unresolved_call_sites: 2
  - line 47: chained_receiver  (clientBuilder.someConfig().build() — receiver expression)
  - line 89: phantom_unresolved_receiver  (callee_simple='log', receiver_expr='LOG')
```

Capped at 5 rows inline; full list available via the CLI in §3.6. Fits the existing rollup machinery (cf. `DECLARES.DECLARES_CLIENT` at `kuzu_queries.py:625`).

**Surface B — `neighbors_v2` opt-in interleaved view.**

`neighbors_v2` gains a per-call optional argument `include_unresolved: bool = False`. When `True` **and** `edge_types == ['CALLS']`, the response interleaves `UnresolvedCallSite` projections with resolved CALLS rows, ordered jointly by `(call_site_line, call_site_byte)`. Unresolved entries appear as a distinct row type with a discriminator field:

```python
class UnresolvedCallSiteRow(BaseModel):
    row_kind: Literal["unresolved_call_site"] = "unresolved_call_site"  # discriminator value
    # resolved CALLS rows use row_kind="resolved" via EdgeRowBase default
    caller_id: str
    call_site_line: int
    call_site_byte: int
    arg_count: int
    callee_simple: str
    receiver_expr: str
    reason: str  # 'phantom_unresolved_receiver' | 'chained_receiver'
```

**`row_kind` is a discriminator on every `neighbors_v2` edge row, not just CALLS.** All `EdgeRowBase` subclasses gain `row_kind: str = "resolved"`. CALLS resolved rows keep `"resolved"`; CALLS unresolved rows use `"unresolved_call_site"`. Other edge types (HANDLES, OVERRIDES, PUBLISHES_TO, etc.) emit `"resolved"` today; the field is reserved for future edge-type-specific resolution-failure splits without re-shaping the response.

**`include_unresolved=True` is mutually exclusive with `edge_filter` (revision 3 reversal).** Setting both raises a fail-loud `ValueError`: `"include_unresolved=True is incompatible with edge_filter; UnresolvedCallSite rows have no edge attributes to filter on"`. Rationale: `edge_filter={callee_declaring_role:'SERVICE'} + include_unresolved=True` would return SERVICE-resolved CALLS plus *all* unresolved sites (chained-receiver, phantom-receiver, anything) — not "service-like unresolved sites." That re-introduces unfiltered noise. A real "service-like unresolved" workflow would need an `unresolved_filter` axis backed by a concrete classifier (e.g. receiver type-name suffix matching), which has no use case behind it yet.

**Default `False`.** Agents who don't know about the option get today's CALLS stream. The HINTS-V4 high-fanout template (§3.10) and the new HINTS-V4 "unresolved-site presence" template (§3.10) nudge agents toward the right surface.

### §3.6 — CLI surface for graph quality

New CLI subcommand on the `java-codebase-rag` operator binary (revision 3 — was `pc unresolved-calls`; aligned with `docs/JAVA-CODEBASE-RAG-CLI.md`):

```
java-codebase-rag unresolved-calls list [--method-id <id>] [--reason <reason>] [--microservice <name>] [--limit 100]
java-codebase-rag unresolved-calls stats [--by reason|microservice|caller_role]
```

This is the debuggability surface for the bucket of data that left `CALLS` after PR-3 (chained-receiver + phantom-unresolved-receiver). Not exposed via MCP — agents don't traverse unresolved sites, but the graph builder's recall quality is a real concern that needs a CLI face.

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

When `dedup_calls=True`, identical `(src_id, dst_id)` pairs collapse to one row with `call_site_count=N` and `call_site_lines=[...]`. **Decision** (§7 Decision 11): default `False` to preserve back-compat in the `neighbors_v2` row shape for callers that already exist. Opt-in is the path of least surprise.

### §3.8 — `find_callers` / `find_route_callers` behavior

No signature change. Both continue to walk `CALLS` backward. After PR-3, they pick up the "no true receiver-failure rows" invariant for free. Known-external rows are still returned (they remain in `CALLS` with `resolved=False`); `overload_ambiguous` rows are still returned (N rows per ambiguous site). Cross-service skip behavior at `build_ast_graph.py:1218-1232` is unchanged.

**Decision** (§7 Decision 9): we do **not** add `callee_declaring_role` filtering to `find_callers`. That helper's intent is "who calls this method?" — the callee is the parameter, not a filter axis. If an agent wants "who calls this method *from a service*?" they project `find_callers` output by the caller's own `role` attribute via a follow-up `describe`. This stays out of the helper's signature.

### §3.9 — What does NOT change

- `EDGE_SCHEMA.CALLS.src` / `.dst` / `.typical_traversals` — still `Symbol → Symbol`; ordering convention documented.
- `HTTP_CALLS` / `ASYNC_CALLS` / any other edge — no `callee_declaring_role` added (their endpoint kinds already encode role). Principle 3 explicitly forbids the symmetry argument.
- HINTS-V3 templates and existing HINTS-V4 success-path templates (HTTP/async/DECLARES families) — **unchanged**. PR-3 adds two new CALLS templates (§3.10) only.
- `confidence` and `strategy` semantics for resolved-and-known-external rows — unchanged. The values stay the same; only the row set shrinks (no more `strategy='phantom'` for unresolved-receiver cases or `strategy='chained_receiver'`).
- The 5-hint output cap — unchanged.
- `neighbors_v2` is one-hop. Multi-hop is out of scope (Decision 21 deleted in revision 3).

#### §3.9.1 — HINTS / ontology PR-3 checklist (mandatory)

PR-3 must not claim "hints unchanged" without completing this list:

| # | Item | Owner |
|---|---|---|
| H1 | `java_ontology.py` `EDGE_SCHEMA['CALLS'].attrs` — add `callee_declaring_role` `EdgeAttr` (PR-1 registers; PR-3 snapshot test still green) | PR-1 + PR-3 CI |
| H2 | `FUZZY_STRATEGY_SET` — remove `phantom` and `chained_receiver` **or** document they apply only to non-CALLS edges | PR-3 |
| H3 | `TPL_NEIGHBORS_FUZZY_STRATEGY` — stop firing on CALLS phantom/chained strategies (rows removed); optional: still fire on known-external `resolved=False` when strategy ∈ remaining fuzzy set | PR-3 |
| H4 | Update / replace `test_hints_neighbors_fuzzy_strategy_calls_phantom_emits` and `test_hints_neighbors_multi_origin_fuzzy_emits_once` | PR-3 |
| H5 | Wire `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` + `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` (§3.10); suppress high-fanout when `edge_filter` provided | PR-3 |
| H6 | `generate_hints` payload for `neighbors` — pass `edge_filter_provided` + unresolved count when `include_unresolved=False` | PR-3 |
| H7 | `MCP_HINTS_FIELD_DESCRIPTION` — document `edge_filter`, `include_unresolved`, `dedup_calls`; note mutual exclusivity | PR-2 + PR-3 |
| H8 | High-fanout template text — mention `edge_filter` for JDK noise, **not** `exclude_external` on `neighbors` | PR-3 |

### §3.10 — Two new HINTS-V4-style templates (PR-3)

When `neighbors([method], 'out', ['CALLS'])` returns more than a threshold (locked at 10 in §7 Decision 12) and no `edge_filter` is provided:

```python
TPL_NEIGHBORS_CALLS_HIGH_FANOUT = (
    "{n} CALLS on this method; the noisy axes are callee_declaring_role "
    "and per-call-site multiplicity. Try edge_filter={{callee_declaring_role: 'SERVICE'}} "
    "for delegation hops, edge_filter={{exclude_callee_declaring_roles: ['ENTITY','DTO']}} "
    "to drop accessor noise, edge_filter={{min_confidence: 0.5}} to trim low-confidence rows "
    "(exclude_external is find_callers-only, not neighbors), or dedup_calls=True to collapse identical callees."
)
```

Priority `PRIORITY_LEAF_FOLLOWUP=2`. Fires only on the CALLS-on-method case; no equivalent for HTTP_CALLS, ASYNC_CALLS, etc. (their fan-outs are much smaller in practice).

A second template fires when the method has any `UNRESOLVED_AT` rows and `include_unresolved=False`:

```python
TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED = (
    "{n} CALLS shown; this method also has {k} unresolved call sites "
    "(see describe(method_id).unresolved_call_sites, or call neighbors with "
    "include_unresolved=True for a source-ordered interleaved view — note "
    "include_unresolved is mutually exclusive with edge_filter)."
)
```

Priority `PRIORITY_LEAF_FOLLOWUP=2`. Fires regardless of `n` (including the 100%-unresolved-receiver case where `n=0`). Suppressed when `include_unresolved=True`.

## §4 — Use-case re-walk

| # | Use case | Today (#177-symptom) | Tomorrow |
|---|---|---|---|
| HV1 | Agent asks "what does `ClientMessageProcessor#process` do?" — wants source-order transcript | `neighbors([m],'out',['CALLS'])` returns **57** rows on bank (dominant `import_map`; **5** `phantom`, **3** `chained_receiver`). Token window pressure. | After PR-3: **~49** rows (8 receiver-failure rows gone; known-external preserved). Same `(line, byte)` ordering. Hint fires (≥10 rows) recommending filter axes. |
| HV2 | Agent walks method body with `call_site_line` | Today's row shape; CALLS order not guaranteed in MCP | Identical row shape — no field removed. `call_site_count` defaults to 1. PR-2: rows returned in `(call_site_line, call_site_byte)` order (Decision 36). |
| HV3 | Agent asks "does `ClientMessageProcessor#process` invoke the repository?" | Manual filter on `neighbors` output by FQN substring | `neighbors([m],'out',['CALLS'], edge_filter={callee_declaring_role: 'REPOSITORY'})` returns the persistence hops in source order. |
| HV4 | Agent asks "where does `ClientMessageProcessor#process` delegate to other services?" | Manual filter | `edge_filter={callee_declaring_role: 'SERVICE'}` |
| HV5 | Agent asks "drop accessor noise" | Manual filter | `edge_filter={exclude_callee_declaring_roles: ['ENTITY','DTO']}` |
| HV6 | Agent asks "who calls `ClientMessageProcessor#process`?" via `find_callers` | Returns resolved + phantom-receiver + known-external rows; agent post-filters | Returns resolved + known-external rows only (phantom-receiver/chained gone after PR-3). Existing `exclude_external=True` on **`find_callers`** still drops library noise. No signature change. |
| HV7 | `trace_request_flow` step from a controller method | Currently walks `CALLS` with implicit phantom inclusion | Walks CALLS with no phantom-receiver/chained rows. Same downstream chain shape. No new fan-out. |
| HV8 | Graph-quality engineer asks "how many unresolved sites in `payment-service`?" | Greps `CALLS` for `strategy in ('phantom','chained_receiver')` — gone after PR-3 | `java-codebase-rag unresolved-calls stats --by microservice` |
| HV9 | Graph-quality engineer asks "show me unresolved sites in `ClientMessageProcessor#process`" | Currently visible as phantom-dst CALLS rows | `describe(method_id).unresolved_call_sites` (capped at 5) or `java-codebase-rag unresolved-calls list --method-id <id>` |
| HV10 | Agent asks `neighbors([class_id], 'out', ['CALLS'])` (wrong kind for CALLS post-flip rule) | HINTS-V3 already covers this case | Unchanged — HINTS-V3 covers `member_only=True` for CALLS. |
| HV11 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={min_confidence: 0.5}` | n/a | Filters by confidence. Most resolved rows have confidence ≥ 0.5 already; known-external rows have receiver-tier confidence preserved. |
| HV12 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={exclude_strategies: ['method_reference']}` | n/a | Drops `Foo::bar` style edges from the transcript. |
| HV13 | Agent calls `neighbors` with `edge_types=['CALLS','OVERRIDES'], edge_filter={callee_declaring_role: 'SERVICE'}` | n/a | **Fail-loud `ValueError`** (revision 3): `"callee_declaring_role is not on OVERRIDES; restrict edge_types to ['CALLS']"`. Matches existing `_nodefilter_inapplicable_fields` pattern. |
| HV14 | Agent calls `neighbors` with `edge_types=['CALLS'], edge_filter={include_strategies: ['exact'], exclude_strategies: ['fuzzy']}` | n/a | Pydantic-level validation error — `include_strategies` and `exclude_strategies` are mutually exclusive. |
| HV15 | Agent calls `neighbors([m], 'out', ['CALLS'], dedup_calls=True)` on a method that calls `repo.save` 3× | n/a | Returns one row with `call_site_count=3, call_site_lines=[47, 89, 102]`. |
| HV16 | Agent runs a HINTS-V4 success-path template after `neighbors([m],'out',['CALLS'])` returns ≥10 rows (e.g. `ClientMessageProcessor#process` after PR-3) | n/a | `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` fires; agent sees three concrete next-step suggestions. |
| HV17 | `find_callers("repositorySave", min_confidence=0.5)` | Today returns resolved + phantom-receiver (filtered by confidence) | After PR-3: returns resolved + known-external (filtered by confidence); no phantom-receiver. Behavior change is *less noise on receiver failures*, not *missing data*. |
| HV18 | A method has 100% of its calls fall in true-receiver-failure cases (all `this.x()` chains or fully phantom-receiver) | Currently shows 100% phantom CALLS rows | After PR-3: `neighbors` returns 0 rows. `describe` rolls up "N unresolved call sites" with a "see CLI for full list" footer. `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` fires. |
| HV19 | A CI test asserts no agent prompt grep'd for `strategy in ('phantom','chained_receiver')` on CALLS rows | n/a | After PR-3, this is structurally guaranteed (rows don't exist). |
| HV20 | `EDGE_SCHEMA` consistency CI test (HINTS-V3 HV19 equivalent) | Currently green | After PR-1: `CALLS.member_only=True` (already set in #157), `EdgeSpec` traversal hints unchanged, `callee_declaring_role` registered as a known filterable attribute on the edge. |

### Awkward cases

- **HV6/HV17** (`find_callers` row set): downstream consumers that currently rely on seeing phantom-receiver-dst rows in `find_callers` output will see fewer rows after PR-3. The replacement is `java-codebase-rag unresolved-calls list --callee-simple <name>` (CLI). Known-external rows remain, so the most common "who calls `save`?" workflow is unaffected.
- **HV18** (100% true-receiver-failure): some methods may genuinely have all their calls resolve to dynamic/reflective paths. The empty-CALLS case used to communicate "this method has invocations we couldn't resolve"; now it communicates "this method has no invocations we could resolve the receiver of." The describe rollup + the HINTS-V4 `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` hint are what bridge that gap. PR-3 must verify both fire correctly.
- **HV13** (fail-loud on mixed edge types): the alternative (silent no-op, original Principle 7) was the wrong call — it contradicted `mcp_v2.py`'s established fail-loud-on-inapplicable-fields contract. Fail-loud with a teaching message is consistent and discoverable.

### §4.5 — Pre-#177 use cases (regression-style)

These rows capture the workflows that *triggered* #177 — the things an agent was trying to do when the noise wall got in the way. They stress-test the design against the question "would this propose have prevented #177?", not just "can the design be used."

| # | Use case | Today (#177-trigger) | Tomorrow |
|---|---|---|---|
| HV21 | End-to-end "explain `ClientMessageProcessor#process`" | Agent calls `neighbors(out,[CALLS])` → **57** rows on bank → token-window pressure → agent picks random callees → explanation misses persistence/delegation | Agent calls `neighbors(out,[CALLS])` → **~49** rows in source order (8 receiver-failure gone, known-external kept), `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` fires, `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` suggests `edge_filter={callee_declaring_role:'SERVICE'}` → agent makes a 2nd filtered call → explanation covers delegation + persistence + unresolved warning. |
| HV22 | Two-pass exploration: skeleton then transcript | Agent gets one wall; reading order ambiguous | Pass 1: `edge_filter={callee_declaring_role:'SERVICE'}` → small skeleton. Pass 2: no filter → full transcript (~49 rows post-PR-3 on pinned method). Two calls, both cheap; same-key results are independent (no implicit cache invalidation). |
| HV23 | Partial-unresolution method (8 resolved + 5 unresolved interleaved) | Agent sees 13 CALLS rows in `(line,byte)` order with 5 phantom-dst rows mixed in | Agent calls `neighbors(out,[CALLS], include_unresolved=True)` → 13 rows in source order, 5 with `row_kind="unresolved_call_site"` and `reason`, 8 with `row_kind="resolved"`. Reading-order preserved; unresolved entries carry enough metadata (callee_simple, receiver_expr, arg_count) to be useful in the transcript. |
| HV24 | Cross-microservice CALLS surprise | Today: skipped sites are logged but not graph-visible; agent thinks the method has fewer downstream hops than it does | Same as today — cross-microservice sites are *not* emitted as `UnresolvedCallSite` rows (locked Decision 24; they're cross-service policy, not resolution failures). The existing log line is kept; `pass3_skipped_cross_service` counter unchanged. Agent uses `trace_request_flow` for cross-service intent. |
| HV25 | Re-index diff intelligibility | Pre/post-index `neighbors` row count differs, no signal to the user explaining why | PR-1 updates README + AGENT-GUIDE with the migration delta. `GraphMeta.calls_total` reflects the post-PR-3 count; the two new `pass3_unresolved_phantom_receiver` and `pass3_unresolved_chained` counters (Decision 23) appear in `describe(graph)` output, providing pre/post-comparable telemetry. |
| HV26 | Recall of unresolved callers ("who calls `save`, including unresolved sites?") | `find_callers("save")` returns resolved + phantom-receiver rows; the phantom-receiver rows expose the symbol_simple of the unresolved callee | `find_callers("save")` returns resolved + known-external. Recall path for unresolved: `java-codebase-rag unresolved-calls list --callee-simple save`. Locked Decision 26: no MCP surface for "find unresolved callers" — the workflow is debuggability, not agent traversal. |
| HV27 | Filter boundary: "calls to methods in the same package as the caller" | Manual post-filter on `neighbors` output | Out of scope for `EdgeFilter` (Decision 28). Use `NodeFilter(fqn_prefix=<caller_pkg>)` if the agent already knows the caller's package; otherwise two queries. The propose explicitly does **not** expose package-relativity in `EdgeFilter`. |
| HV28 | `find_callers` confidence-filter parity check | `find_callers("save", min_confidence=0.5)` works today | Continues to work. **`find_callers` is not migrated to accept `EdgeFilter`** (Decision 27); the existing discrete `min_confidence` parameter is kept. The asymmetry (`neighbors_v2` uses `EdgeFilter`; `find_callers` uses discrete) is documented in the PR-2 description. |
| HV29 | Telemetry: `GraphMeta` counters | Today: `clients_total`, `producers_total`, `declares_client_total`, `declares_producer_total`, etc. (post SCHEMA-V2) | PR-1 adds two discrete `INT64` counters: `pass3_unresolved_phantom_receiver`, `pass3_unresolved_chained` (one per `reason` value). Decision 23 — column fan-out, not a JSON-encoded map. |
| HV30 | Interface vs concrete callee declaring role | Today: `pass3_calls` uses `_lookup_method_candidates` which walks supertypes. The declaring-type role on the returned candidate could be the interface (often `OTHER`) or the concrete class (typed). | **Locked Decision 20**: `callee_declaring_role` is sourced from the candidate's `parent_id` Symbol's `role`. PR-2 validates column population on `bank-chat-system` annotated types; supertype-walk dedup evidence is on `call_graph_smoke` (HV36). |
| HV31 | Brownfield `@CodebaseRole` on declaring type | Today: brownfield role is layered onto the type's `role` via `resolve_role_and_capabilities` (`graph_enrich.py:672`) | `callee_declaring_role` picks it up transparently (it reads `parent.role`, which already reflects the brownfield layer). No additional code. Locked Decision 29 records this. |
| HV32 | `NodeFilter` + `EdgeFilter` composition | n/a | AND across both, ordering preserved. `NodeFilter(microservice='order-service')` + `EdgeFilter(callee_declaring_role='SERVICE')` returns CALLS edges from the queried method to methods whose declaring type role is `SERVICE` *and* whose owning microservice is `order-service`, in `(line, byte)` order. Locked Decision 22. |
| HV33 | The `NodeFilter.role` vs `EdgeFilter.callee_declaring_role` naming-collision trap | n/a | `NodeFilter.role` filters on the **neighbor node's own role**; for a method-kind Symbol that's almost always `OTHER`. `EdgeFilter.callee_declaring_role` filters on the **callee's declaring type's role**. Both names retained — renaming either is a worse trade. Documented as a callout in `docs/AGENT-GUIDE.md` + a HINTS-V4 hint if `NodeFilter(role=...)` is applied with `edge_types=['CALLS']` and the returned rows are dominantly method-kind symbols with `role='OTHER'` (locked Decision 30). |
| HV34 | Empty-filter performance | n/a | `neighbors([m],'out',['CALLS'])` with no `edge_filter` is the hot path. PR-2 includes a Kuzu predicate-pushdown sanity check: the `callee_declaring_role` column projection on resolved rows must not materially slow the empty-filter case. If profiling shows otherwise, PR-2 adds an index on `(src_id, callee_declaring_role)`. Decision 31: pinned `ClientMessageProcessor#process` empty-filter query within 1.5× pre-PR-2 median on same hardware; pytest id in plan; skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`. |
| HV35 | `callee_capability` filter request from a future reviewer | n/a | **Out of scope.** Locked Decision 32: `EdgeFilter` exposes only `callee_declaring_role`. `callee_capability` / `callee_annotation` / `callee_microservice` are out of scope. Re-opens via a new propose. |
| HV36 | Multi-candidate supertype-walk dedup (interface declaration + concrete receiver declaration, same signature) | Today: `pass3_calls` may emit two CALLS rows for the same `(call_site_line, call_site_byte)` with different `dst_id` (interface method id vs concrete method id) and different declaring-type roles. Agent reading source order sees the same line twice. | **Locked Decision 33** (revision-3 scope: supertype-walk only): when `_lookup_method_candidates` returns multiple candidates because one is the declared concrete method on the receiver type and others are inherited supertype declarations, `pass3_calls` collapses to the concrete-class candidate before emit. **`overload_ambiguous` is left alone** — N rows preserved. PR-1 tests on **`tests/fixtures/call_graph_smoke/`**: (a) new `SupertypeDedupPatterns` stub → one CALLS row per site with `callee_declaring_role='REPOSITORY'`; (b) `OverloadPatterns#sameArity` → N `overload_ambiguous` rows (bank has **zero** such rows — do not use bank alone). |
| HV37 | Known-receiver external call to JDK/Spring/Lombok (`LOG.info(...)`, `List.of(...)`) | Today: `CALLS(strategy=<receiver-tier>, resolved=False)` with deterministic phantom-FQN dst; preserved by `build_ast_graph.py:1257-1271` | **Unchanged in graph.** PR-3 does **not** move these out of `CALLS`. PR-1 adds `callee_declaring_role` (typically `OTHER`). On `neighbors`, drop via `edge_filter` (e.g. `min_confidence`, `exclude_callee_declaring_roles`) — **not** `exclude_external` (Decision 38). On `find_callers`, `exclude_external=True` unchanged. |
| HV38 | Agent expects source-order CALLS from `neighbors` | Rows may arrive in Kuzu insertion order | PR-2: `ORDER BY e.call_site_line, e.call_site_byte` on all CALLS paths before `offset`/`limit` (Decision 36). |

### Awkward cases (§4.5)

- **HV23** (interleaved view): the discriminator-field approach (`row_kind`) is verbose. The alternative (heterogeneous list without discriminator) is worse — agents would have to infer entry type from absence-of-fields, which is exactly the anti-pattern this whole propose pushes back on.
- **HV30** (interface vs. concrete): even with the supertype-walk dedup of HV36/Decision 33, the role projection still depends on `_lookup_method_candidates` returning the concrete impl when one exists. For edge cases where only the interface is indexed (e.g. `JdbcTemplate.query(String, RowMapper<T>, Object...)` with no `@Repository` on `JdbcTemplate`), `callee_declaring_role` will be `OTHER`. Agents will not see those repository hops under a `callee_declaring_role='REPOSITORY'` filter. Mitigation in Decision 20: PR-2 adds an `OTHER`-fallback hint when `callee_declaring_role='SERVICE'`/`'REPOSITORY'` returns 0 results but the unfiltered call has ≥5 results — suggesting the agent try `exclude_callee_declaring_roles=['ENTITY','DTO']` instead.
- **HV34** (perf): "named scenario" rather than "numeric threshold" is the right calibration because Kuzu performance varies by hardware. The test asserts the same empty-filter `ClientMessageProcessor#process` query on `bank-chat-system` is within 1.5× of its pre-PR-2 median latency on the same hardware; gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`. If the assertion fails, PR-2 fixes before merge.
- **HV36** (dedup scope): the dedup is intentionally narrow. `overload_ambiguous` rows are not deduped — they represent real resolver ambiguity, and erasing them with a winner-selection would hide ambiguity downstream consumers (find_callers, explain flows) may need to know about. The dedup applies only when one candidate is the receiver-type's own concrete declaration and the others are inherited supertype declarations of the same signature.
- **HV37** (known-external preservation): keeping known-external rows in `CALLS` with `resolved=False` means `resolved BOOLEAN` stays in the DDL. Agents that want a "purely resolved" stream can add `edge_filter={include_strategies: <resolved-set>}` once PR-2 lands. To exclude JDK/library noise on **`neighbors`**, use `edge_filter` (`min_confidence`, `exclude_strategies`, roles) — **not** `exclude_external` (Decision 38). On **`find_callers` / `find_callees`**, `exclude_external=True` is unchanged. **`exclude_callee_declaring_roles: ['OTHER']` also removes known-external rows** (they are typically `OTHER`); document in AGENT-GUIDE.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Semantic split of `CALLS` into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE` | Breaks the ordered-transcript property (Principle 2). Agents reading a method body would have to fan-merge across edge types. |
| `callee_declaring_role` on `HTTP_CALLS` / `ASYNC_CALLS` / other edges | Those edges encode role in their endpoint kind already (Client, Producer). Principle 3 forbids the symmetry. |
| `caller_role` filter on `find_callers` | Caller's role is queryable post-hoc via `describe`. Adding it to the helper expands surface for marginal gain (§3.8 Decision 9). |
| Per-call-site dedup as a default | Off by default; opt-in via `dedup_calls=True` to preserve back-compat with the agent's familiar row-per-call-site shape (§3.7 Decision 11). |
| Move `unresolved_call_sites` to MCP `neighbors` | Agents don't traverse them. Exposed via `describe` rollup (capped) and CLI (full). |
| Replace `confidence` / `strategy` with a `quality` enum | Out of scope. Existing values still apply; only the row set shrinks. |
| Add `framework`, `microservice`, or other node-derived attributes to CALLS rows | Already queryable via `NodeFilter`. Edge-attribute filters are for things the build process computes for the *edge*, not for things derivable from the endpoint nodes. |
| Cross-edge filter composition (e.g. "CALLS to a method whose caller is in microservice X") | Out of scope. Use two-step `neighbors` queries or a richer DSL in a future propose. |
| Re-emit phantom-receiver CALLS rows behind a feature flag | No active users / no soft migration. |
| Localized hint text | Out of scope — consistent with HINTS-V3/V4. |
| `callee_capability` / `callee_annotation` filter axes | Cardinality high, agent value unclear; locked Decision 32. Re-opens via a new propose. |
| `callee_microservice` filter axis on `EdgeFilter` | `NodeFilter(microservice=...)` already composes with `EdgeFilter` (HV32 / Decision 22); adding a duplicate axis on the edge would invite confusion about which side owns the filter. Locked out by Decision 32. |
| `EdgeFilter` on `find_callers` / `find_route_callers` | Discrete `min_confidence` parameter is kept; asymmetry with `neighbors_v2` is intentional (HV28 / Decision 27). |
| MCP surface for "find unresolved callers by callee_simple" | Debuggability path lives in `java-codebase-rag unresolved-calls list --callee-simple <name>`; not an agent traversal pattern (Decision 26). |
| Cross-microservice-skipped CALLS sites becoming `UnresolvedCallSite` rows | Cross-service policy, not a resolution failure; existing log line + counter unchanged (Decision 24). |
| Renaming `NodeFilter.role` or `EdgeFilter.callee_declaring_role` to disambiguate | Naming-collision trap mitigated by docs + HINTS-V4 hint (Decision 30); rename is a worse trade. |
| Package-relativity filters in `EdgeFilter` (e.g. same-package callees) | Out of scope; use `NodeFilter(fqn_prefix=...)` or two queries (HV27). |
| `unresolved_filter` axis on `reason` (e.g. "include only chained-receiver unresolved sites") | No use case behind it; the workflow for "show me everything" sets `include_unresolved=True` without a reason filter. If a real "service-like unresolved" workflow emerges, it needs a concrete classifier (receiver type-name matching) and its own propose. |
| Composing `include_unresolved=True` with `edge_filter` | Reverts revision 2's compose decision: would re-introduce unfiltered noise (all unresolved rows pass through filter that was meant to scope the resolved side). Mutually exclusive, fail-loud (Decision 25). |
| Multi-hop `neighbors_v2` (`depth>1`) | Out of scope. `neighbors_v2` is one-hop today (README:12, `mcp_v2.py:39`). Multi-hop is a separate design problem (visited-set, cycle handling, fanout cap, hint behavior at depth boundaries) and needs its own propose. |
| Erasing `overload_ambiguous` via dedup | Decision 33's scope is narrow — supertype-walk dedup only. `overload_ambiguous` rows are preserved; they are the resolver's own ambiguity signal. |
| Moving known-receiver-external rows (`build_ast_graph.py:1257-1271`) out of `CALLS` | Reviewer-flagged data-loss bug in revision 2. These rows carry preserved receiver-tier `strategy`/`confidence`/`arg_count` and a deterministic phantom FQN — real signal, not noise. README §"Phantom nodes" documents the existing contract. Decision 34 records this. |
| Silent-no-op `edge_filter` on edges that don't carry the attribute | Contradicts `mcp_v2.py:6,82-91,191-206` fail-loud-on-inapplicable-fields contract. Reverted in revision 3 — Principle 7 + Decision 10 now fail-loud (HV13). |
| `exclude_external` on `neighbors_v2` | Duplicates `find_callers` FQN-prefix logic on a different surface; use `edge_filter` on `neighbors` instead (Decision 38). Document asymmetry in AGENT-GUIDE. |
| Method-level accessor labels (`ACCESSOR` vs business logic on same `ENTITY` type) | `callee_declaring_role` alone cannot split getters from real entity methods. Use [`propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) `/mini-map` heuristics or a future propose (Decision 39). |
| Porting `/mini-map` classification into the indexer | Skill-side remedy is intentional; server-side role projection closes phantom/chained + stereotype buckets only. |

## §6 — Migration plan — 3 PRs

This propose locks before any code PR merges. The propose itself merges as a separate PR (no code) and then the three code PRs follow in order. **Revision 3 reorders PRs:** PR-2 is MCP-additive; PR-1 may reduce duplicate-site row counts after re-index; **PR-3 is the only breaking change** to default `CALLS` transcripts.

### PR-1 — Add `callee_declaring_role` + supertype-walk dedup + `GraphMeta` counters

**Title**: `feat(schema): add callee_declaring_role to CALLS; supertype-walk dedup; unresolved counters`

**Purpose**:
- Bump `ONTOLOGY_VERSION` to 15.
- Add `callee_declaring_role STRING` to `CALLS` DDL; populate at emission (PR-1 grounding: `build_ast_graph.py:1284-1289`, `:1240-1276`, `:1310-1325` for all `_emit_call_edge` call sites). Default `OTHER` if the parent is missing or unroleable.
- Add supertype-walk dedup in `pass3_calls`: collapse interface-declaration + same-receiver-type concrete-declaration candidates to the concrete candidate before emit. **Do not touch `overload_ambiguous`**.
- Add `pass3_unresolved_phantom_receiver INT64` and `pass3_unresolved_chained INT64` to `GraphMeta` (Decision 23). These count today's phantom-receiver and chained-receiver CALLS rows; PR-3 will populate them from `UnresolvedCallSite` instead, with the same semantic meaning.
- README + AGENT-GUIDE updated to document `callee_declaring_role`, supertype dedup, and **row-count delta at duplicate sites** (re-index changes cardinality, not only a new column).

**No PR-3-style row deletions, no schema removals.** MCP signatures unchanged. **Supertype dedup may reduce `CALLS` row count** where interface+concrete duplicates were emitted; existing readers see the new column and possibly fewer rows per method after re-index.

**Test summary**: named scenarios — supertype-walk dedup on `call_graph_smoke` `SupertypeDedupPatterns` (PR-1 adds stub); `overload_ambiguous` preserved on `call_graph_smoke` `OverloadPatterns#sameArity`; `callee_declaring_role` populated on `bank-chat-system` for known `@Repository`/`@Service` types; brownfield `@CodebaseRole` picked up transparently (HV31); `EDGE_SCHEMA` snapshot reflects the DDL change; `GraphMeta` counters appear in `describe(graph)`.

### PR-2 — `EdgeFilter` on `neighbors_v2` (CLI deferred to PR-3)

**Title**: `feat(mcp): EdgeFilter on neighbors_v2`

**Purpose**:
- Add `EdgeFilter` Pydantic model in `mcp_v2.py`.
- Wire it through `neighbors_v2` and the underlying Kuzu query (`kuzu_queries.py` neighbors path) with **Cypher predicate pushdown** (§3.4.1) and **`ORDER BY e.call_site_line, e.call_site_byte`** for CALLS (Decision 36–37).
- Add `min_confidence`, `exclude_strategies`, `include_strategies`, `callee_declaring_role`, `callee_declaring_roles`, `exclude_callee_declaring_roles` as fields.
- **Fail-loud single-edge-type validation** (Principle 7 + Decision 35): raise `ValueError` with teaching message when `edge_filter` references an attribute not on every edge type in `edge_types`. Mirrors `_nodefilter_inapplicable_fields` (`mcp_v2.py:191-206`). Increments the fail-loud counter.
- Pydantic-level validation: `include_strategies` xor `exclude_strategies`.
- **`java-codebase-rag unresolved-calls` CLI deferred to PR-3** — no empty stub in PR-2 (misleading before `UnresolvedCallSite` tables exist).
- Update `MCP_HINTS_FIELD_DESCRIPTION` and `EDGE_SCHEMA` snapshot to register `callee_declaring_role` as a known filterable attribute on CALLS.
- `docs/AGENT-GUIDE.md`: `exclude_external` is **not** on `neighbors` (Decision 38); document `NodeFilter.role` vs `EdgeFilter.callee_declaring_role` trap; **`exclude_callee_declaring_roles: ['OTHER']` drops known-external rows**; cross-link `/mini-map` for accessor noise.
- Add `OTHER`-fallback hint when role filter returns 0 but unfiltered ≥5 results (Decision 20).
- Add `NodeFilter(role=...)` vs `EdgeFilter.callee_declaring_role` collision hint (Decision 30): fires when `NodeFilter(role=...)` is applied to `neighbors([m],'out',['CALLS'])` and the returned rows are dominantly method-kind symbols with `role='OTHER'`.
- Add Kuzu predicate-pushdown perf named-scenario test on pinned `ClientMessageProcessor#process` (Decision 31); skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.
- Revisit the MCP-V2 "no per-edge filter" design rule in the PR description; record the supersession.

**Still no `CALLS` row deletions.** Existing readers see the same rows as today; PR-2 only adds a filter projection surface.

**Test summary**: named scenarios — filter projects ordered stream by role; mixed-edge-type filter raises fail-loud `ValueError` with teaching message (HV13); filter xor validation raises Pydantic error; `test_neighbors_calls_ordered_by_call_site` (HV38); `test_neighbors_calls_edge_filter_pushdown_in_cypher`; filter-before-limit invariant; perf-named-scenario (heavy-gated) passes.

### PR-3 — Move true receiver-failure rows out of CALLS + interleaved view + dedup hint

**Title**: `feat(schema, mcp, hints): phantom-receiver/chained sites move to UnresolvedCallSite; include_unresolved; CALLS dedup`

**Purpose**:
- Add `UnresolvedCallSite` node table + `UNRESOLVED_AT` edge table.
- Change `pass3_calls` at `build_ast_graph.py:1192-1199` and `:1204-1211` to emit `UnresolvedCallSite(reason='chained_receiver'|'phantom_unresolved_receiver')` + `UNRESOLVED_AT(caller, ucs)` instead of phantom Symbol + CALLS. **Do not touch the known-external branch at `:1257-1271`.**
- Restrict `_phantom_method_id` usage to known-external emissions only.
- Populate the `GraphMeta` counters added in PR-1 from `UnresolvedCallSite` rows.
- Extend `describe` for method-kind Symbol with the `unresolved_call_sites` rollup (capped at 5).
- Wire `java-codebase-rag unresolved-calls list/stats` CLI to real data.
- Add `include_unresolved: bool = False` to `neighbors_v2`. When `True` and `edge_types == ['CALLS']`, interleave `UnresolvedCallSite` rows (`row_kind="unresolved_call_site"`) with resolved CALLS rows (`row_kind="resolved"`) in global `(call_site_line, call_site_byte)` order; at equal `(line, byte)`, `row_kind='resolved'` before `row_kind='unresolved_call_site'`. **Mutually exclusive with `edge_filter`**: setting both raises a fail-loud `ValueError` (Decision 25).
- Add `row_kind` discriminator with default `"resolved"` to all `EdgeRowBase` subclasses (not CALLS-only). Snapshot test asserts the field is present on every edge-row Pydantic model.
- Add `dedup_calls: bool = False` to `neighbors_v2`. When `True`, collapse identical `(src_id, dst_id)`; canonical row uses minimum `(call_site_line, call_site_byte)`; `call_site_lines` sorted ascending.
- Extend `CallEdgeRow` with `call_site_count: int` and `call_site_lines: list[int] | None`.
- Add `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` template (§3.10) wired through the existing HINTS-V4 success-path generator.
- Add `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` template (§3.10) wired through the same generator. Suppressed when `include_unresolved=True`.
- Add HV19 invariant test: no `strategy='phantom'` or `strategy='chained_receiver'` rows remain in `CALLS`.
- Complete §3.9.1 HINTS / ontology checklist (H1–H8).

**This is the only breaking PR.** Documented in the PR description as a breaking change; sentinel grep checks in the cursor task prompt enumerate every reference.

**Test summary**: named scenarios — pass3 emits zero `CALLS` rows for chained-receiver / phantom-receiver sites; emits `UnresolvedCallSite` + `UNRESOLVED_AT` for each; known-external rows preserved (HV37 — `LOG.info` call site stays in CALLS with `resolved=False` and preserved receiver-tier strategy); `find_callers` returns zero phantom-receiver/chained-strategy rows on `bank-chat-system`; `describe` rollup on a method with 8 unresolved sites shows "8 unresolved call sites" with first 5 inline; `include_unresolved=True` + `edge_filter` raises fail-loud `ValueError`; dedup collapses identical `(src,dst)` pairs; default `dedup_calls=False` produces today's shape; high-fanout template fires above threshold; does not fire when `edge_filter` is provided.

## §7 — Decisions taken (no longer open)

1. **`CALLS` sheds only true receiver-failure rows** (`strategy='phantom'` for unresolved receivers, `strategy='chained_receiver'`). Known-external (`resolved=False` with preserved receiver-tier metadata), `overload_ambiguous`, name-only-fb single-candidate, `implicit_super`, `constructor`, and all other current strategies stay as `CALLS` rows.
2. **`resolved BOOLEAN` stays in `CALLS` DDL.** Known-external rows continue to use `resolved=False` exactly as today. Revision 3 reversal of the original "remove resolved" decision.
3. **`UnresolvedCallSite` is a sibling node table, not a JSON column on Symbol.** PR-3.
4. **`UnresolvedCallSite` is not a Symbol-kind node** and does not participate in any other edge. It is reachable only via `UNRESOLVED_AT` from its caller, via `describe`, and via the CLI.
5. **`callee_declaring_role` becomes a `CALLS` edge attribute,** populated at `pass3_calls` emission time from the callee parent's `role`. Default `OTHER` if parent is missing or unroleable. PR-1.
6. **No semantic split of `CALLS`** into `DELEGATES_TO` / `PERSISTS_VIA` / `ACCESSES_STATE` / `UNRESOLVED_CALL`. Ordered-transcript property is the dominant agent use case (Principle 2).
7. **No `callee_declaring_role` on `HTTP_CALLS` / `ASYNC_CALLS` or other edges.** Their endpoint kinds already encode role (Principle 3).
8. **`EdgeFilter` is a typed Pydantic model with `extra='forbid'`**, not a free-form dict. Field additions go through a propose.
9. **`find_callers` / `find_route_callers` do not gain `callee_declaring_role`** filtering (§3.8).
10. **`edge_filter` is single-edge-type-scoped and fail-loud on inapplicable attributes** (revision 3 reversal of the original "silent no-op" rule). Matches the existing `_nodefilter_inapplicable_fields` fail-loud contract at `mcp_v2.py:191-206`.
11. **`dedup_calls=False` by default** in `neighbors_v2`. Opt-in. Preserves the agent-familiar row-per-call-site shape.
12. **High-fanout HINTS-V4 template threshold = 10 CALLS rows.** Locked by §3.10.
13. **`unresolved_call_sites` is exposed via `describe` (capped at 5)** and via the `java-codebase-rag unresolved-calls` CLI, not via `neighbors`.
14. **No back-compat alias for the removed phantom-receiver/chained `CALLS` rows.** Per locked repo rule "Breaking changes allowed; no active users." Applies in PR-3 only.
15. **`include_strategies` and `exclude_strategies` are mutually exclusive** in `EdgeFilter`. Pydantic validator enforces.
16. **`MCP-API-V2-REDESIGN-PROPOSE.md`'s "no per-edge filter on `neighbors`" rule is superseded** by Principle 3 of this propose. Recorded in PR-2 description.
17. **`pass3_calls` cross-microservice skip behavior is unchanged.** Today's same-microservice candidate preference at `build_ast_graph.py:1218-1232` continues to apply.
18. **PR-1 is a single re-index moment.** ONTOLOGY_VERSION 14 → 15 in PR-1. PR-2 and PR-3 do not bump it again (they add tables/columns under the same ontology version since the new tables aren't queryable until they exist).
19. **Three sub-PRs, sequential, ordered for additive-then-breaking** (revision 3 reordering). PR-2 MCP-additive; PR-1 may change row cardinality via supertype dedup; PR-3 is the only breaking change to default `CALLS` shape.
20. **`callee_declaring_role` is sourced from the candidate's `parent_id` Symbol's `role`** — i.e. the role of the type that declares the resolved callee method, after `_lookup_method_candidates` has chosen between interface and concrete declarations. Today's preference order (same-microservice > resolved > supertype walk) is unchanged. When resolution lands on an interface with no stereotype, the value is `OTHER`. PR-2 adds an `OTHER`-fallback hint when a role filter returns 0 but the unfiltered call has ≥5 results.
21. **`neighbors_v2` stays one-hop.** Multi-hop is out of scope and would need its own propose. (Revision 3 deletion of the original "edge_filter applied at every hop in depth>1" decision; the depth>1 surface was invented and isn't in `neighbors_v2` today.)
22. **`NodeFilter` and `EdgeFilter` compose with AND;** `(call_site_line, call_site_byte)` ordering is preserved when both are applied.
23. **`GraphMeta` gains two discrete `INT64` counters — one per `reason` value:** `pass3_unresolved_phantom_receiver`, `pass3_unresolved_chained`. Column fan-out, not a JSON-encoded map. Populated structurally in PR-1 (counted from today's CALLS phantom/chained rows); populated from `UnresolvedCallSite` in PR-3 with the same semantic meaning.
24. **Cross-microservice-skipped CALLS sites are not `UnresolvedCallSite` rows.** Existing log line + `pass3_skipped_cross_service` counter are kept.
25. **`include_unresolved=True` is mutually exclusive with `edge_filter`** (revision 3 reversal of revision 2's compose decision). Setting both raises a fail-loud `ValueError`. Rationale: composing them would emit "SERVICE-resolved CALLS + *all* unresolved sites," which is not "service-like unresolved sites" — it re-introduces the unfiltered noise the rest of the propose works to remove. A real "service-like unresolved" workflow needs an `unresolved_filter` axis backed by a concrete classifier (receiver type-name matching) and its own propose.
26. **No MCP surface for "find unresolved callers by `callee_simple`."** The workflow is debuggability via `java-codebase-rag unresolved-calls list --callee-simple <name>`, not agent traversal.
27. **`find_callers` / `find_route_callers` are not migrated to accept `EdgeFilter`.** Their existing discrete `min_confidence` parameter is kept. The asymmetry with `neighbors_v2` is intentional and documented.
28. **Package-relativity / cross-edge composition filters are out of scope for `EdgeFilter`.** Use `NodeFilter(fqn_prefix=...)` or chained `neighbors` calls.
29. **Brownfield `@CodebaseRole`-derived role is picked up transparently** by `callee_declaring_role` because it reads `parent.role`, which already reflects the brownfield layer (`graph_enrich.py:672`). No additional code.
30. **`NodeFilter.role` and `EdgeFilter.callee_declaring_role` are intentionally not renamed.** The collision is documented in `docs/AGENT-GUIDE.md` and mitigated by a HINTS-V4 hint that fires when `NodeFilter(role=...)` is applied to `neighbors([m],'out',['CALLS'])` and the returned rows are dominantly method-kind symbols with `role='OTHER'`.
31. **PR-2 ships a Kuzu predicate-pushdown sanity test.** Named scenario: pinned `ClientMessageProcessor#process` empty-filter `neighbors([m],'out',['CALLS'])` on `bank-chat-system` within 1.5× pre-PR-2 median on same hardware; `test_neighbors_calls_perf_empty_filter_client_message_processor`; skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`. If the test fails at PR-2 review time, PR-2 adds an index on `(src_id, callee_declaring_role)` and re-validates.
32. **`EdgeFilter` exposes only `callee_declaring_role` from the role/capability/annotation/microservice quadruple.** Capability, annotation, and microservice filters are out of scope. Re-opens via a new propose if agent value emerges later. `callee_microservice` is specifically out — use `NodeFilter(microservice=...)` which already composes.
33. **Supertype-walk dedup only** (revision 3 scope-narrow): when `_lookup_method_candidates` returns multiple candidates because one is the declared concrete method on the receiver type and others are inherited supertype declarations of the same signature, `pass3_calls` collapses to the concrete-class candidate before emit. **`overload_ambiguous` rows are not touched.** Multi-candidate dedup evidence is preserved as a build-time debuggability log line, not as graph state. HV36 locks this in PR-1's `call_graph_smoke` tests (not bank alone).
34. **Known-receiver-external `CALLS` rows are preserved** (`build_ast_graph.py:1257-1271`). These rows carry `resolved=False` with preserved receiver-tier `strategy`/`confidence`/`arg_count` and a deterministic phantom FQN. They are not noise — they are honest "we know where this goes but the callee body isn't in scope" rows. README §"Phantom nodes" documents the existing contract. PR-3 does not move them. **`exclude_external` is not added to `neighbors_v2`** — JDK noise on `neighbors` uses `edge_filter`; `find_callers` keeps `exclude_external`.
35. **`edge_filter` validation is per-call, fail-loud on inapplicable attributes** (revision 3 — supersedes the original silent-no-op decision). The validator runs against `EDGE_SCHEMA` and raises `ValueError` with a teaching message when any attribute referenced in the filter is not present on every edge type in `edge_types`. Increments `[filter-frame] fail-loud category=edge_filter` counter.
36. **`neighbors_v2` CALLS paths use `ORDER BY e.call_site_line, e.call_site_byte` before `offset`/`limit`.** PR-2. Applies to empty-filter, `edge_filter`, and (PR-3) `include_unresolved` interleave.
37. **`edge_filter` predicates are pushed into Cypher `WHERE` for `edge_types=['CALLS']`**, then `NodeFilter` on terminal nodes, then slice. No pre-filter SQL `LIMIT`. PR-2.
38. **`exclude_external` is not added to `neighbors_v2`.** Document asymmetry with `find_callers` / `find_callees` in AGENT-GUIDE. PR-2.
39. **Accessor / getter noise is out of scope for the indexer** — partially addressed by `exclude_callee_declaring_roles`; full remedy is client-side via [`propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) `/mini-map` until a future method-level label propose.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Agents (or skills) currently grep CALLS rows for `strategy in ('phantom','chained_receiver')` to inspect graph quality | Documented in PR-3 description as a breaking change; `java-codebase-rag unresolved-calls list` is the replacement; HV8/HV9 named scenarios verify the new surface works. |
| Removing phantom-receiver/chained code paths in `pass3_calls` breaks downstream code I haven't audited | PR-3's diff is the audit. Sentinel grep checks in the cursor task prompt enumerate every reference. PR-1 and PR-2 ship first and are additive, so the audit happens with `EdgeFilter` already available for verification queries. |
| `callee_declaring_role` is misleading for callees whose parent type's role is `OTHER` | Default `OTHER` is honest. The `OTHER` bucket is itself an informative filter target ("exclude OTHER" is a reasonable agent move). |
| `EdgeFilter` Pydantic surface grows uncontrolled over time | Field additions require a propose. CI test asserts `EdgeFilter.model_fields` matches a snapshot. |
| High-fanout template fires too often | Threshold (10) is conservative; can be raised in a follow-up without re-index. Hint is advisory, not breaking. |
| `UnresolvedCallSite` table grows unboundedly on large codebases | Same cardinality as today's phantom-receiver + chained-receiver Symbol count. Net storage cost ≈ unchanged. |
| Describe rollup of `unresolved_call_sites` adds noise to the describe output | Capped at 5 inline; footer says "see `java-codebase-rag unresolved-calls list --method-id <id>` for the full list." Behavior matches existing DECLARES.* describe rollup pattern. |
| `dedup_calls=True` loses per-call-site line resolution for some downstream use case | Opt-in only; `call_site_lines` is populated when dedup is on, preserving the data. |
| `find_callers` callers that relied on phantom-receiver-dst rows being present silently break | Audited in PR-3; CI test asserts `find_callers` returns zero phantom-receiver/chained-strategy rows on `bank-chat-system`. Known-external rows still returned. |
| `java-codebase-rag unresolved-calls stats --by caller_role` requires joining `UnresolvedCallSite` → `UNRESOLVED_AT` → caller Symbol → declaring type | One extra hop; acceptable for a CLI debuggability surface. Not on a hot path. |
| Supertype-walk dedup misidentifies an `overload_ambiguous` case and erases a candidate | Dedup is scoped strictly to "one candidate is the receiver-type's own concrete declaration AND the others are inherited supertype declarations of the same signature" — does not fire on name-only-fb multi-candidate cases. PR-1 fixture test asserts both scenarios. |
| Known-external rows confuse agents reading `resolved=False` as "unresolved" | README §"Phantom nodes" already documents this; PR-3 description re-states. The `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` hint refers specifically to `UnresolvedCallSite` rows (chained + phantom-receiver), not known-external. |
| Agents assume `neighbors` returns CALLS in source order | PR-2 adds explicit `ORDER BY`; `test_neighbors_calls_ordered_by_call_site` on `bank-chat-system` (HV38). |
| `edge_filter` applied after `LIMIT` truncates signal | Pushdown + filter-before-slice locked in §3.4.1; sentinel grep in plan forbids early `LIMIT` on unfiltered CALLS hop. |
| HINTS still reference phantom CALLS strategies after PR-3 | §3.9.1 checklist (H2–H4); tests must be updated in PR-3, not left stale. |
| `/mini-map` and `edge_filter` overlap confuses product direction | Decision 39 + §5 cross-link: server closes phantom/chained + stereotype; skill closes accessor heuristics. |

## Appendix A — Concrete DDL diff

```sql
-- CALLS DDL (build_ast_graph.py:2317-2319)
-- Before:
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN);

-- After (PR-1 adds callee_declaring_role; `resolved` STAYS, revision 3):
CREATE REL TABLE CALLS(FROM Symbol TO Symbol,
  call_site_line INT64, call_site_byte INT64, arg_count INT64,
  confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN,
  callee_declaring_role STRING);

-- New tables (PR-3):
CREATE NODE TABLE UnresolvedCallSite(
  id STRING,
  caller_id STRING,
  call_site_line INT64,
  call_site_byte INT64,
  arg_count INT64,
  callee_simple STRING,
  receiver_expr STRING,
  reason STRING,           -- 'phantom_unresolved_receiver' | 'chained_receiver'
  PRIMARY KEY(id)
);

CREATE REL TABLE UNRESOLVED_AT(FROM Symbol TO UnresolvedCallSite);

-- GraphMeta counter columns (PR-1, populated structurally; PR-3 re-populates from UnresolvedCallSite):
ALTER NODE TABLE GraphMeta ADD COLUMN pass3_unresolved_phantom_receiver INT64;
ALTER NODE TABLE GraphMeta ADD COLUMN pass3_unresolved_chained INT64;
```

## Appendix B — Traceability

**Revision 5 (2026-05-19, PR [#179](https://github.com/HumanBean17/java-codebase-rag/pull/179))** — fixture anchors, plan alignment, Cursor prompts (docs only; no code):

- **Pinned bank method:** `ClientMessageProcessor#process` (57 outbound CALLS; ~49 after PR-3) replaces fictional `OrderService.process` in HV/perf rows.
- **Tests:** supertype dedup + `overload_ambiguous` on `call_graph_smoke` (PR-1 adds `SupertypeDedupPatterns`); bank for role-column population only.
- **HV37 footnote:** `exclude_external` scoped to `find_callers`/`find_callees`; `OTHER` role filter bluntness documented.
- **Test name:** `test_neighbors_calls_edge_filter_pushdown_in_cypher` unified; perf test renamed to `..._client_message_processor` (heavy-gated).
- **PR-2:** CLI deferred to PR-3; PR-1 README notes row-count delta from dedup.
- **PR-3:** interleave tie-break + `dedup_calls` canonical line locked in plan.
- **Cursor prompts:** [`plans/completed/AGENT-PROMPTS-CALLS-NOISE.md`](../plans/completed/AGENT-PROMPTS-CALLS-NOISE.md) added — merge gate for PR-1 **code** is satisfied once [#179](https://github.com/HumanBean17/java-codebase-rag/pull/179) lands on `master`.

**Revision 4 (2026-05-19, post-critical-review implementation contract)** — propose patches before code PRs:

- **Status → under review.** Plan file added: [`plans/completed/PLAN-CALLS-NOISE.md`](../plans/completed/PLAN-CALLS-NOISE.md).
- **ORDER BY contract (Decision 36, §3.4.1, HV38):** PR-2 locks `ORDER BY e.call_site_line, e.call_site_byte` on all CALLS `neighbors_v2` paths.
- **`edge_filter` pushdown (Decision 37, §3.4.1):** Cypher `WHERE` predicates before `offset`/`limit`; no pre-filter `LIMIT`.
- **`exclude_external` stance (Decision 38, §3.4.2):** not added to `neighbors_v2`; HV37 corrected; AGENT-GUIDE requirement in PR-2.
- **Supertype dedup pseudocode (§3.3.1):** implementable algorithm for Decision 33.
- **HINTS / ontology PR-3 checklist (§3.9.1):** replaces "hints unchanged" bullet; H1–H8 mandatory in PR-3.
- **Mini-map cross-link (Decision 39, §5):** accessor noise partially out of scope; coordinates with `AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`.
- **Counts:** 39 decisions; 38 use cases (HV1–HV38).

**Revision 3 (2026-05-19, post-PR-#178-review)** — major restructure after reviewer flagged six blockers:

- **Known-external CALLS rows preserved.** Reviewer pointed out `build_ast_graph.py:1257-1271` (receiver resolved, callee not indexed — JDK/Spring/Lombok) preserves `confidence`/`strategy`/`arg_count`/deterministic phantom FQN with `resolved=False`. README §"Phantom nodes" documents this. The previous "resolved-only CALLS" framing stripped this signal. New Decision 34 records that PR-3 only moves `strategy='phantom'` (unresolved receiver) and `strategy='chained_receiver'` out. `resolved BOOLEAN` stays in DDL (Decision 2 reversed). HV37 added.
- **Supertype-walk dedup scope narrowed.** Reviewer flagged that blanket `(src,line,byte)` dedup erases `overload_ambiguous` — the resolver's own ambiguity signal. Decision 33 rewritten to fire only on the interface-vs-concrete-supertype case; `overload_ambiguous` rows are preserved as N. HV36 rewritten.
- **`edge_filter` semantics now fail-loud single-edge-type.** Reviewer flagged the silent-no-op rule contradicted `mcp_v2.py:6,82-91,191-206` fail-loud-on-inapplicable-fields contract. Principle 7 rewritten; Decision 10 reversed; Decision 35 added for the validator. HV13 rewritten from "silent no-op" to "fail-loud ValueError with teaching message."
- **`include_unresolved=True` mutually exclusive with `edge_filter`.** Reviewer pointed out that compose-mode re-introduces unfiltered noise (filter applies to resolved side only; *all* unresolved rows pass through regardless). Decision 25 reverted from revision 2's compose to revision 1's mutual exclusivity, now fail-loud. HV38 deleted.
- **Multi-hop `neighbors_v2(depth>1)` removed.** Reviewer pointed out `neighbors_v2` is one-hop today (README:12, `mcp_v2.py:39`) and the depth>1 surface was invented. Decision 21 rewritten to lock one-hop; HV28 (the depth=2 row) deleted; out-of-scope row added.
- **CLI naming fixed.** `pc unresolved-calls` → `java-codebase-rag unresolved-calls` everywhere (matches `docs/JAVA-CODEBASE-RAG-CLI.md`).
- **PR ordering reworked per reviewer's safer sequence.** PR-1 adds `callee_declaring_role` + supertype-walk dedup + GraphMeta counters (all additive). PR-2 adds `EdgeFilter` + CLI stub (still additive). PR-3 moves true-receiver-failure rows out (only breaking PR).
- **Decision count adjusted.** Removed depth>1 decision (was 21). Added Decisions 34 (known-external preservation), 35 (fail-loud validator). HV count: HV1–HV20 + HV21–HV37 = 37 rows.

**Revision 2 (2026-05-18, post-author-grill round 2)** — amendments after second self-grill:

- Removed: HV28 (`cursor-pr-review` use case) and Decision 24 (`cursor-pr-review` as downstream consumer). Rationale: `.cursor` skills in this repo are dev-time tooling, not runtime agent workflows on the indexed codebase.
- Decision 23 reshaped from JSON-map to discrete `INT64` counters.
- Decision 25 (was 26) reversed to compose (subsequently re-reverted in revision 3).
- Decision 30 (was 31) refined: collision-hint trigger condition changed to "dominantly OTHER rows."
- Decision 31 (was 32) refined: perf scenario named explicitly.
- Decision 33 added: multi-candidate dedup (subsequently scope-narrowed in revision 3).
- HV38 added (subsequently deleted in revision 3).
- `row_kind` reshaped to global default.
- §5 added: `callee_microservice` and `unresolved_filter` out-of-scope rows.

**Revision 1 (2026-05-18, post-author-grill round 1)** — additions only, no decisions removed:

- Added §4.5 (HV21–HV37): regression-style use cases covering the workflows that triggered #177.
- Added Surface B (`include_unresolved`) to §3.5: in-line interleaved transcript view with `row_kind` discriminator.
- Added `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` template to §3.10.
- Added §5 rows for capability/annotation, `EdgeFilter` on `find_callers`, unresolved-callers MCP, cross-microservice skip, naming-collision rename, package-relativity.
- Added Decisions 20–33 to §7.
- Updated TL;DR and PR-2/PR-3 deliverables to match.

**What stayed unchanged from the original draft**:
- Decisions 1, 3, 4, 6, 7, 8, 9, 11–19 unchanged through all three revisions (with renumbering; Decision 2 reversed in revision 3).
- Principles 1–6 and 8 unchanged through all three revisions (Principle 7 reversed in revision 3).
- HV1–HV12, HV14–HV16, HV19–HV27 unchanged in substance (renumbered/rephrased where references changed).

**Cross-propose references**:
- Supersedes `propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`'s "no per-edge filter on `neighbors`" rule (Decision 16).
- Builds on `propose/completed/SCHEMA-V2-PROPOSE.md` §3.4 (`EDGE_SCHEMA`) — extends with `callee_declaring_role` registration.
- Builds on `propose/completed/HINTS-V3-PROPOSE.md` (kind/direction templates) — existing templates unchanged; PR-3 adds §3.10 templates per §3.9.1.
- Builds on `propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md` — high-fanout and unresolved-presence templates plug into the existing success-path generator.
- Complements [`propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/completed/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) `/mini-map` for accessor/getter noise (Decision 39) — server-side `edge_filter` does not replace skill heuristics.
- Resolves [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177).
