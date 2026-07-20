<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# DESCRIBE-OVERRIDE-ROLLUP — Surface override-axis affordances on method `describe` so the dispatch chasm is visible

**Status**: completed (landed in PR #110)
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12

## TL;DR

- **The call**: when `describe` is called on a **method Symbol** declared on an interface or abstract class (or, symmetrically, on a concrete method that overrides one), `edge_summary` adds composed rollup keys exposing the override relationship and any brownfield signal hidden behind it.
- **The keys** (method side, symmetric to PR-89's class side):
  - on an abstract/interface method: `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`
  - on a concrete override: `OVERRIDES`
- **Naming**: dot notation `<virtual_parent_relation>.<projected_relation>` makes the composition explicit, consistent with PR-89's `DECLARES.DECLARES_CLIENT` convention. `OVERRIDDEN_BY.DECLARES_CLIENT` reads as *"the `DECLARES_CLIENT` projection reached via this method's concrete overrides."* Neither `OVERRIDDEN_BY` nor `OVERRIDES` is a valid `EdgeType` literal, so these keys cannot be passed to `neighbors(edge_types=…)` — Pydantic rejects them.
- **Why**: today, an agent walking `Foo.process` → `CALLS` → `AssignClient.openChat` (the interface method) sees an `edge_summary` with `CALLS` and `DECLARES` only. The concrete `LocalAssignClient.openChat` that carries `@CodebaseClient` is reachable only through a 5-hop walk with a name-and-arity join in the middle. No agent will guess it; the chasm is invisible.
- **Scope**: surgical. Read-path Cypher; **no schema change**, no new edge types persisted, no indexing pass. Walks `IMPLEMENTS`/`EXTENDS` (class-level, already there) + same-signature filter on declared members.
- **Symmetric to PR-89**: PR-89 fixed the **containment axis** (class loses signal that lives on its members). This propose fixes the **dispatch axis** (interface method loses signal that lives on its concrete overrides). Same shape: composed rollup keys in `edge_summary`, dot-notation naming, omitted when zero.
- **Migration**: 1 PR. ~60 LoC behind `_edge_summary_for_node` + helper, ~5 new tests.
- **Risk**: dispatch resolution is heuristic (Java has no compile-time guarantee that `LocalAssignClient.openChat(...)` is the override — it's a name + arity + supertype-chain match). The heuristic uses `signature` string matching on Symbol nodes, mirroring `_lookup_method_candidates`'s name+arity semantics.

## 1. Frame: what is this thing, really?

> **`describe` is the agent's "what is this node?" primary call. Its `edge_summary` must answer questions at the *scope the agent asked about*. When the agent calls `describe` on an interface method, the answer should summarise cross-service participation at declaration-grain, even when the underlying truth is recorded at override-grain.**

The brownfield contract attaches `@CodebaseClient` (and Spring route annotations, etc.) **to concrete override methods by deliberate design** — each override represents one specific outbound call or one exposed route, with its own target, path, and `client_kind` / `framework`. The granularity is meaningful: the graph correctly records `DECLARES_CLIENT: Symbol → Client` and `EXPOSES: Symbol → Route` at the override-method level. That granularity is right for traversal.

It is **wrong for declaration-grain affordance**. An agent calling `describe` on an interface method is asking a coarser-grained question than the operator answered at the override level: *"does this declaration have concrete overrides that participate in cross-service communication, and is it worth walking to find out where?"* Today's `edge_summary` on the interface method answers structural questions (`DECLARES`, `CALLS`) but is silent on the brownfield projections that hang off concrete overrides one dispatch-hop away. The agent has to know to walk through a 5-hop chain it has no breadcrumb for.

The rollup is a **scope translation**, not a recovery of "hidden declaration-level intent." The override-method-grained truth stays where it is; we add a declaration-grained summary indexed by the interface method, computed at read-time. The graph is unchanged; the agent's view of the graph gains an affordance at the scope it asked about.

This frame **rules out**:

- Adding a persisted `OVERRIDES` / `OVERRIDDEN_BY` edge table to the schema
- Adding precomputed scalar fields on method Symbol rows
- Rewriting `CALLS` to also point at the override(s) (would falsify call-site resolution)
- Modifying the underlying graph schema in any way

## 2. Design principles

1. **Composed rollup, not schema change.** The new `edge_summary` keys are computed at `describe`-time from existing edges (`IMPLEMENTS`/`EXTENDS` class-level + member signature match). Reindexing produces no new rows, columns, or tables.
2. **Method Symbols only.** Rollup applies when the described node is a method Symbol (`kind ∈ {method, constructor}` — constructors are included for completeness but in practice produce zero overrides). Type Symbols, Routes, and Clients get their existing behaviour (PR-89 for types) unchanged.
3. **Two vantage points, named explicitly.**
   - `OVERRIDDEN_BY` on the **declaration side** (interface/abstract method): count of concrete methods that override this one.
   - `OVERRIDES` on the **implementation side** (concrete override): count of declarations this method overrides (typically 1 — single inheritance; >1 only when implementing same-signature methods from multiple interfaces).
4. **Naming names the composition.** Standalone keys (`OVERRIDDEN_BY`, `OVERRIDES`) name the virtual dispatch relation. Composed keys take the form `<virtual_parent>.<projected_relation>` (e.g. `OVERRIDDEN_BY.DECLARES_CLIENT`): the virtual parent is the dispatch-axis walk, the projected relation is the edge counted at the second hop. This is consistent with PR-89's `DECLARES.<projected>` convention. Neither `OVERRIDDEN_BY` nor `OVERRIDES` is a valid `EdgeType` literal, so these keys raise a Pydantic `ValidationError` if passed to `neighbors(edge_types=…)` — composition is read-only by construction, not just by convention.
5. **Direction stays meaningful.** Override rollup keys carry `{in, out}` shape:
   - `OVERRIDDEN_BY`: `{in: 0, out: N}` (declaration → impls; conceptually "out" because we walk away from the declaration toward concrete overrides).
   - `OVERRIDES`: `{in: 0, out: M}` (impl → declarations).
   - `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES`: `{in: 0, out: N}` — count of brownfield edges on the overrides.
6. **Signature-match uses the `signature` column.** The Kuzu `Symbol` table stores a `signature STRING` column (format `"name(T1,T2)"`). Two methods are "same-signature" when their `signature` strings match. This mirrors `_lookup_method_candidates`'s name+arity semantics — the existing function matches on `name == callee_simple` and `len(parameters) == arg_count`, which is exactly what `signature` equality provides (same name, same parameter count, same simple type names). No `param_count` or `param_type_fqns` columns exist on Symbol; the `signature` column is the single source of truth for method identity at the Kuzu level.
7. **Single hop only.** One level of override: declaration ↔ direct concrete overrides. Transitive override chains (interface A extends interface B; class C implements A) are walked separately — describe `A.m` to see overrides of A; describe `B.m` to see overrides of B. The agent does the recursion explicitly, the rollup doesn't.
8. **Don't double-count with PR-89's class-level rollup.** A concrete override class's `DECLARES.DECLARES_CLIENT` already counts its own `@CodebaseClient` methods. The override-axis rollup on the interface method counts the **same** edges from a different vantage point — this is expected, not a bug. Each rollup answers a different question; both being non-zero is the correct picture.

## 3. The proposed surface

### 3.1 `edge_summary` shape on an interface method

Today, for `AssignClient.openChat`:

```json
{
  "DECLARES": {"in": 1, "out": 0},
  "CALLS":    {"in": 1, "out": 0}
}
```

After the change, when one impl carries `@CodebaseClient`:

```json
{
  "DECLARES":                      {"in": 1, "out": 0},
  "CALLS":                         {"in": 1, "out": 0},
  "OVERRIDDEN_BY":                 {"in": 0, "out": 1},
  "OVERRIDDEN_BY.DECLARES_CLIENT": {"in": 0, "out": 1}
}
```

`OVERRIDDEN_BY.EXPOSES` is omitted here (count 0). Each composed key is emitted only when its count is positive (same convention `edge_counts_for` uses today: zero-row keys aren't emitted). With multiple impls (e.g. `LocalAssignClient`, `RemoteAssignClient`, neither tagged), the override-axis rollup still shows `OVERRIDDEN_BY: out: 2` and both brownfield composition keys omitted.

### 3.2 `edge_summary` shape on a concrete override

For `LocalAssignClient.openChat` (concrete, `@CodebaseClient` on it):

```json
{
  "DECLARES":        {"in": 1, "out": 0},
  "DECLARES_CLIENT": {"in": 0, "out": 1},
  "OVERRIDES":       {"in": 0, "out": 1}
}
```

The `OVERRIDES` key tells the agent: "this method has a declaration upstream — describe it if you want to find sibling implementations." Symmetric to the interface side.

### 3.3 Cypher sketch (illustrative — exact form belongs in a plan)

Two queries on the declaration side (described method = `m`):

```cypher
// Walk: described method's declaring type -> classes that implement/extend it
//       -> for each such class, methods with same signature string
MATCH (m:Symbol {id: $id})<-[:DECLARES]-(t:Symbol)
MATCH (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t)
MATCH (impl)-[:DECLARES]->(mover:Symbol)
WHERE mover.signature = m.signature
  AND mover.id <> m.id
RETURN collect(DISTINCT mover.id) AS impl_method_ids
```

The `signature` column stores `"name(T1,T2)"` — string equality provides the same name+arity+simple-type-name semantics that `_lookup_method_candidates` uses internally (`name == callee_simple` and `len(parameters) == arg_count`).

Then, for the brownfield rollups, count `DECLARES_CLIENT` / `EXPOSES` outgoing edges from the collected impl method ids. Three counts total per describe of an abstract method: `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`.

Symmetric query on the implementation side: walk `(m)<-[:DECLARES]-(impl)-[:IMPLEMENTS|EXTENDS]->(parent)-[:DECLARES]->(decl_m:Symbol)` with `decl_m.signature = m.signature`; count distinct `decl_m`.

### 3.4 How the agent uses it

In `docs/AGENT-GUIDE.md` under `describe`:

> **Override-axis composed keys.** For method Symbols, `edge_summary` may include keys naming the dispatch-axis virtual relations:
>
> - `OVERRIDDEN_BY` — appears on interface / abstract method declarations. Means: N concrete methods with matching `signature` exist on implementing/extending classes. Walk: `neighbors(ids=<method_id>, direction="in", edge_types=["DECLARES"])` → for the declaring type, `neighbors(ids=<type_id>, direction="in", edge_types=["IMPLEMENTS","EXTENDS"])` → for each impl class, `neighbors(ids=<impl_class_id>, direction="out", edge_types=["DECLARES"])` and filter on matching `signature`.
> - `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES` — same walk, then on each impl method id query `DECLARES_CLIENT` / `EXPOSES`.
> - `OVERRIDES` — appears on concrete override methods. Symmetric walk upward through the declaring class's supertypes.
>
> These keys are **composed** (computed at describe-time via signature match, not stored as graph edges). Do not pass them to `neighbors(edge_types=…)` (the call fails with a Pydantic `ValidationError`). Use them as a hop affordance only.
>
> Note on counting semantics: `OVERRIDDEN_BY.DECLARES_CLIENT` counts **edge rows on overrides**, not distinct override methods. One override method that declares multiple `Client` rows contributes its full edge count. The "does this interface method have any client-declaring overrides?" predicate is answered by `count > 0`; the count itself is an affordance for how rich the downstream walk will be.

### 3.5 What does NOT change

- `_edge_summary_for_node` for non-method Symbols is byte-identical to today and to PR-89.
- The Kuzu schema is unchanged. No new tables, no new columns, no new fields on `Symbol`.
- `CALLS` resolution is **unchanged**. `Foo.process` still `CALLS` `AssignClient.openChat`, not the impl. The override rollup is purely additive presentation on the interface method's describe output.
- `neighbors` accepts the same `EdgeType` literals as before. Composed keys cannot be passed in `edge_types=` — Pydantic rejects them before any Cypher executes.

## 4. Use-case re-walk

16 cases covering interface/abstract/sealed/generic/diamond scenarios.

| # | Scenario | Describe target | `OVERRIDDEN_BY` | `OVERRIDDEN_BY.DECLARES_CLIENT` | `OVERRIDDEN_BY.EXPOSES` | `OVERRIDES` | Notes |
|---|---|---|---|---|---|---|---|
| UC1 | The originating case: `AssignClient.openChat` interface method, one impl `LocalAssignClient.openChat` carries `@CodebaseClient` | interface method | `out: 1` | `out: 1` | (omitted) | n/a | the bug-fix case from the user's question |
| UC2 | Same as UC1, agent now describes `LocalAssignClient.openChat` | concrete override | n/a | n/a | n/a | `out: 1` | symmetric — pointer back to the declaration |
| UC3 | Interface method with no implementations indexed in the codebase | interface method | (omitted) | (omitted) | (omitted) | n/a | rollup correctly silent |
| UC4 | Interface method with 4 impls; none carry brownfield annotations | interface method | `out: 4` | (omitted) | (omitted) | n/a | dispatch affordance still emitted even when brownfield is silent |
| UC5 | Abstract class method, 2 subclass overrides, 1 carries `@PostMapping` | abstract method | `out: 2` | (omitted) | `out: 1` | n/a | route-side symmetry |
| UC6 | Concrete method that does NOT override anything (defined directly on a class, no supertype declares it) | concrete method | n/a | n/a | n/a | (omitted) | `OVERRIDES` silent — no upstream declaration |
| UC7 | Concrete method implementing **two** interface methods with same signature (diamond) | concrete method | n/a | n/a | n/a | `out: 2` | counted once per upstream declaration |
| UC8 | Static method on an interface (Java 8+) | interface method (static) | (omitted) | (omitted) | (omitted) | n/a | static methods aren't dispatched; rollup silent. Plan-level: filter by `"static" IN modifiers` |
| UC9 | Default method on an interface, overridden by 1 impl | interface method (default) | `out: 1` | varies | varies | n/a | default methods are dispatched; treated like abstract ones for rollup |
| UC10 | Generic method `<T> List<T> findAll()` on interface, impl is `List<String> findAll()` | interface method | `out: 1` | varies | varies | n/a | signature match uses `signature` column. Plan-level: confirm AST stores erased signature |
| UC11 | Constructor (described directly) | constructor | n/a (no rollup) | n/a | n/a | n/a | constructors aren't overridden; rollup helpers return empty dict |
| UC12 | Method on a sealed interface with 3 permitted impls, all annotated | interface method | `out: 3` | `out: 3` | (omitted) | n/a | sealed doesn't change anything; rollup just counts |
| UC13 | Interface method overridden indirectly: `interface A { void m(); } interface B extends A {} class C implements B { void m() {...} }` | method on A | `out: 0` (single hop) | (omitted) | (omitted) | n/a | **one hop only**: C implements B, not A. Agent describes B.m next if interested |
| UC14 | Same chain, described on B.m | method on B (inherited declaration; only present if B redeclares) | varies | varies | varies | n/a | only meaningful if B redeclares the method; otherwise B.m doesn't exist as a Symbol |
| UC15 | Method name overload: `process(String)` on interface, impl has both `process(String)` and `process(int)` | interface method | `out: 1` | varies | varies | n/a | `signature` equality excludes `process(int)` — `"process(String)"` ≠ `"process(int)"` |
| UC16 | Pathological: interface method with 50 impls, all in different packages | interface method | `out: 50` | varies | varies | n/a | rollup is one Cypher with cardinality bounded by IMPLEMENTS/EXTENDS in-degree of the declaring type; acceptable |

**Gaps found in walk**:

- UC7 (diamond) confirmed: a concrete override of two same-signature interface declarations gets `OVERRIDES: out: 2`. No de-dup; both are real upstream declarations.
- UC8 (static interface methods) needs static-method filtering. The `modifiers STRING[]` column on Symbol stores `"static"` when applicable — the Cypher filter is `NOT "static" IN m.modifiers`. If the modifier isn't reliably populated, the rollup will incorrectly show overrides for static methods in pathological cases. **Decision**: filter by `"static" IN modifiers`; document the limitation if modifier population proves inconsistent.
- UC10 (generics): signature-match uses the `signature` column. Plan-level: confirm `signature` stores erased types (e.g. `findAll()` not `findAll<T>()`).
- UC13 (transitive chain) explicitly bounded to one hop. The agent recurses by describing the next-level type.

## 5. What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add a persisted `OVERRIDES` / `OVERRIDDEN_BY` edge table | Couples indexing to a presentation concern. Same reasoning as PR-89: a synthetic Cypher rollup is cheap enough, and dispatch resolution is heuristic — committing it to an edge would lie. |
| Rewrite `CALLS` to also point at concrete overrides | Falsifies call-site resolution. The interface method is what the source actually refers to. |
| Add scalar fields like `override_count` to Symbol rows | Couples indexing to read-time. Every future rollup would need its own column. |
| Recurse override chains (transitive impls) | Multi-hop fanout, harder to reason about. The agent walks step-by-step. UC13 demonstrates the explicit chain. |
| Suppress rollup when `CALLS` already reaches the impl directly (e.g. static dispatch through `LocalAssignClient.openChat` typed field) | Different call-site, different question. If the agent describes the interface method, they get the override rollup regardless of any specific call site. |
| Roll up `OVERRIDDEN_BY.HTTP_CALLS` / `OVERRIDDEN_BY.ASYNC_CALLS` | Symmetric to PR-89's deferral. Same reasoning: a separate propose if surfaced. |
| Compute "best" override (most-derived, most-annotated) | YAGNI. The agent gets a count + the walk pattern; ranking is application logic. |
| Filter rollup counts by `confidence` | `edge_summary` is unfiltered counts everywhere. Stay consistent. |
| Surface the rollup on Field / Class / Route / Client nodes | Out of axis. Only methods are dispatched. |
| Cross-package signature-match when no `IMPLEMENTS`/`EXTENDS` chain exists | Would invent dispatch relationships. The walk is constrained by actual class-hierarchy edges. |
| Surface a "this method is itself only a declaration" flag separately | The rollup output already conveys it: `OVERRIDDEN_BY` present iff declaration; `OVERRIDES` present iff override. |
| Add an override-predicate field (`has_overrides: true`) | Same reasoning as PR-89's decision #16 (member-predicate deferral): `count > 0` on the composed key already answers the predicate. Revisit if agent boilerplate accumulates. |

## 6. Migration plan — 1 PR

### PR-DESCRIBE-OVERRIDE-1: synthetic override-axis rollup in `describe.edge_summary`

- **Purpose**: extend `_edge_summary_for_node` to add up to four override-axis synthetic keys when the described node is a method Symbol.
- **Implementation surface**:
  - Add helper `KuzuGraph.override_axis_rollup_for(method_id)` returning `dict[str, dict[str, int]]` with up to four keys.
  - Internally:
    1. Resolve the method's declaring type and `signature`.
    2. Run the dispatch-down walk (declarations → impls) and dispatch-up walk (override → declarations) using `signature` equality.
    3. Count `DECLARES_CLIENT` / `EXPOSES` outgoing edges on the collected override method ids.
    4. Emit non-zero keys only.
  - In `mcp_v2._edge_summary_for_node` (or `describe_v2`), check `kind == "symbol"` AND `data.kind == "method"`, then merge non-zero rollup results (constructors: no helper call — see [`plans/completed/PLAN-DESCRIBE-OVERRIDE-ROLLUP.md`](../../plans/completed/PLAN-DESCRIBE-OVERRIDE-ROLLUP.md) §PR-1).
  - Update `DescribeOutput` documentation / pydantic field-level description to mention method-side rollup keys.
- **Test summary**: 5 new tests in `tests/test_mcp_v2_compose.py` (same module as PR-89 describe `edge_summary` tests; avoids splitting describe rollups across files):
  - `test_describe_interface_method_with_annotated_impl_emits_rollup` — UC1 fixture.
  - `test_describe_concrete_override_emits_overrides_rollup` — UC2 fixture.
  - `test_describe_method_no_overrides_silent` — UC6 fixture (no upstream, no impls).
  - `test_describe_abstract_method_with_route_override_emits_exposes` — UC5 fixture (route-side).
  - `test_describe_interface_method_diamond_override_counts_once_per_upstream` — UC7 fixture.
- **Doc updates**: one paragraph in `docs/AGENT-GUIDE.md` under `describe` (text in §3.4 above).

## 7. Decisions taken (no longer open)

1. **Fix shape**: composed rollup in `edge_summary`, computed at `describe`-time. **No schema change**, no `OVERRIDES` edge table, no precomputed fields.
2. **Trigger**: only when described node is a method Symbol (`data.kind in {method, constructor}`).
3. **Axes covered**: dispatch-down (declaration → impls) AND dispatch-up (impl → declarations) — symmetric.
4. **Key names**:
   - `OVERRIDDEN_BY` (declaration side — count of concrete overrides)
   - `OVERRIDES` (implementation side — count of upstream declarations)
   - `OVERRIDDEN_BY.DECLARES_CLIENT` and `OVERRIDDEN_BY.EXPOSES` (brownfield composition on declaration side)
5. **Naming convention**: consistent with PR-89's dot notation. Standalone keys (`OVERRIDDEN_BY`, `OVERRIDES`) name the virtual dispatch relation. Composed keys use `<virtual_parent>.<projected_relation>` (e.g. `OVERRIDDEN_BY.DECLARES_CLIENT`). The `DECLARES.<projected>` family (PR-89) composes through a **stored** parent edge; the `OVERRIDDEN_BY.<projected>` family composes through a **virtual** parent relation (computed from class hierarchy + signature match). Both share the dot convention; the distinction is documented.
6. **Direction shape**: `{in: 0, out: N}` for every override-axis rollup key.
7. **Omission rule**: when count is 0, the key is omitted entirely.
8. **Signature match**: uses the `signature STRING` column on Symbol (format `"name(T1,T2)"`). Two methods match when their `signature` strings are equal. This mirrors `_lookup_method_candidates`'s name+arity semantics — the existing function matches on `name == callee_simple` and `len(parameters) == arg_count`, which is exactly what `signature` equality provides. No `param_count` or `param_type_fqns` columns exist on Symbol; the `signature` column is the single source of truth.
9. **Depth**: exactly one `IMPLEMENTS`/`EXTENDS` hop on either side. Transitive override chains require explicit agent recursion (UC13).
10. **Diamond impls**: a concrete override counts once per **upstream declaration** (UC7). No de-dup of multi-interface implementation.
11. **Static interface methods**: filter via `"static" IN m.modifiers` in the Cypher query. The `modifiers STRING[]` column on Symbol stores `"static"` when applicable. If modifier population is inconsistent, statics may produce false positives in pathological cases — documented as a plan-level risk.
12. **Constructors**: included in trigger but rollup helper returns empty for them in practice (no upstream to override).
13. **Surface**: extends `edge_summary` only. No new MCP tool, no new flag on `describe`.
14. **Querying**: composed keys are read-only by construction. Neither `OVERRIDDEN_BY` nor `OVERRIDES` is a valid `EdgeType` literal (`mcp_v2.py:18-28`), so passing them to `neighbors(edge_types=…)` raises a Pydantic `ValidationError` before any Cypher executes — same invariant as PR-89's dot keys.
15. **Interaction with PR-89's `DECLARES.<projected>`**: both rollups answer different questions about the same edge data from different vantage points (containment vs dispatch). Both being non-zero on related nodes is expected, not double-counting. The two families are emitted on **different node kinds** (PR-89: type Symbol; this propose: method Symbol) — they never appear on the same `edge_summary`.
16. **`OVERRIDDEN_BY.HTTP_CALLS` / `OVERRIDDEN_BY.ASYNC_CALLS`**: deferred. Separate propose if surfaced (symmetric to PR-89's deferral of `DECLARES.HTTP_CALLS` / `DECLARES.ASYNC_CALLS`).
17. **Documentation**: AGENT-GUIDE gets one paragraph naming the composed keys and the explicit walk pattern.
18. **Override-predicate alternative deferred (not rejected).** `count > 0` on the composed key answers the predicate today. If agent code accumulates repeated `rollup.get(…, {}).get('out', 0) > 0` boilerplate, a `has_overrides` field becomes a clean follow-up. Same reasoning as PR-89's decision #16 (member-predicate deferral).

## 8. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| `signature` column equality is coarser or finer than JVM dispatch (varargs produce different `signature` strings; generic erasure edge cases) | `signature` equality mirrors `_lookup_method_candidates`'s name+arity semantics (already battle-tested for `CALLS` resolution). Plan-level: factor the signature-match predicate into a shared helper; audit `signature` column format for erasure consistency. |
| Static interface methods produce spurious "overrides" | Filter by `"static" IN m.modifiers` in the Cypher query. `modifiers STRING[]` on Symbol is populated from the AST. Plan-level: verify static-modifier population on interface methods in `tests/bank-chat-system`. |
| Performance regression on widely-implemented interfaces (e.g. `Runnable`, 100s of impls in a large codebase) | Walk is bounded by `IMPLEMENTS`/`EXTENDS` in-degree on the declaring type. Even at 100 impls, one Cypher with a same-signature filter. Plan-level: measure on `tests/bank-chat-system` and the user's brownfield enterprise fixture. |
| Agents treat composed keys as real edges and pass them to `neighbors` | Neither `OVERRIDDEN_BY` nor `OVERRIDES` is a valid `EdgeType` literal — Pydantic rejects them with `ValidationError` before any Cypher executes. Same invariant as PR-89's dot keys. AGENT-GUIDE paragraph documents this. |
| Interaction with PR-89's `DECLARES.<projected>` rollup confuses reviewers | The two families are emitted on **different node kinds** (PR-89: type Symbol; this propose: method Symbol). They never appear on the same `edge_summary`. AGENT-GUIDE addresses both under their respective `describe` sections. |
| Generic erasure mismatch when AST stores parameterized types in `signature` instead of erased names | Plan-level: confirm `signature` column stores erased types. If not, the plan adds an erasure normalisation step before the rollup query. |
| Diamond overrides (UC7) produce surprisingly high `OVERRIDES` counts on common interfaces | Expected, documented in the key semantics. The agent's takeaway: "this method implements N interface contracts; describe each declaration to find the operator's intent." |
| Operators read `OVERRIDDEN_BY.DECLARES_CLIENT: {out: 5}` as "5 override methods are clients" when it means "5 `Client` edge rows across all overrides" | AGENT-GUIDE §3.4 note on counting semantics calls this out: composed counts measure edge rows, not distinct methods. Same divergence as PR-89's edge-count vs member-count note. |

## Appendix A — Concrete artifact: `override_axis_rollup_for` skeleton

```python
# kuzu_queries.py — KuzuGraph method (plan-level: exact placement decided in PR)

_OVERRIDE_METHOD_KINDS = {"method", "constructor"}

def override_axis_rollup_for(self, method_id: str) -> dict[str, dict[str, int]]:
    """Composed rollup of override-axis dispatch affordances for a method Symbol.

    Emits up to four keys (any with count 0 are omitted):
      - OVERRIDDEN_BY                 : declaration-side, dispatch-down count
      - OVERRIDES                     : impl-side, dispatch-up count
      - OVERRIDDEN_BY.DECLARES_CLIENT : brownfield composition over impls
      - OVERRIDDEN_BY.EXPOSES         : brownfield composition over impls

    Both directions are computed because we don't a-priori know whether the
    described method is a declaration, an override, or both (default methods).

    Signature match uses the ``signature`` column (format "name(T1,T2)") —
    string equality provides name+arity+simple-type-name semantics matching
    ``_lookup_method_candidates``.
    """
    # Dispatch-down: find concrete methods on subclasses with same signature
    impl_ids = self._rows(
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(t:Symbol) "
        "MATCH (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t) "
        "MATCH (impl)-[:DECLARES]->(mover:Symbol) "
        "WHERE mover.signature = m.signature "
        "  AND mover.id <> m.id "
        "  AND NOT 'static' IN m.modifiers "
        "RETURN collect(DISTINCT mover.id) AS impl_method_ids",
        {"id": method_id},
    )
    impls: list[str] = (impl_ids[0].get("impl_method_ids") if impl_ids else []) or []

    # Dispatch-up: find declaration methods on supertypes with same signature
    decl_ids = self._rows(
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(impl:Symbol) "
        "MATCH (impl)-[:IMPLEMENTS|EXTENDS]->(parent:Symbol) "
        "MATCH (parent)-[:DECLARES]->(decl_m:Symbol) "
        "WHERE decl_m.signature = m.signature "
        "  AND decl_m.id <> m.id "
        "RETURN collect(DISTINCT decl_m.id) AS decl_method_ids",
        {"id": method_id},
    )
    decls: list[str] = (decl_ids[0].get("decl_method_ids") if decl_ids else []) or []

    out: dict[str, dict[str, int]] = {}
    if impls:
        out["OVERRIDDEN_BY"] = {"in": 0, "out": len(impls)}
        n_clients = self._count_outgoing(impls, "DECLARES_CLIENT")
        n_routes = self._count_outgoing(impls, "EXPOSES")
        if n_clients > 0:
            out["OVERRIDDEN_BY.DECLARES_CLIENT"] = {"in": 0, "out": n_clients}
        if n_routes > 0:
            out["OVERRIDDEN_BY.EXPOSES"] = {"in": 0, "out": n_routes}
    if decls:
        out["OVERRIDES"] = {"in": 0, "out": len(decls)}
    return out
```

```python
# mcp_v2.py — change in _edge_summary_for_node

def _edge_summary_for_node_with_override_rollup(
    graph: KuzuGraph, node_id: str, kind: str, row: dict[str, Any]
) -> dict[str, dict[str, int]]:
    summary = graph.edge_counts_for(node_id)
    sym_kind = str(row.get("kind") or "")
    if kind == "symbol" and sym_kind in _OVERRIDE_METHOD_KINDS:
        summary.update(graph.override_axis_rollup_for(node_id))
    elif kind == "symbol" and sym_kind in _ROLLUP_TYPE_KINDS:  # PR-89
        summary.update(graph.member_edge_rollup_for(node_id))
    return summary
```

## Appendix B — What changed (traceability)

### v1 → v2: six defects from code-grounded review aligned with PR-89 v2.1

A review pass — reading v1 against the actual code in `build_ast_graph.py`, `ast_java.py`, and `mcp_v2.py`, and applying the same pressure-test methodology that drove PR-89 through v2/v2.1 — surfaced six defects. All fixed in v2.

**Defect 1 (critical): Phantom Kuzu columns.** v1's Cypher sketches queried `m.param_count` and `m.param_type_fqns` — neither column exists on the Symbol table (`build_ast_graph.py:2034-2043`). The schema stores `signature STRING` (format `"name(T1,T2)"`) as the single source of truth for method identity at the Kuzu level. v2 rewrites all Cypher to use `mover.signature = m.signature` / `decl_m.signature = m.signature`. This also fixes Appendix A's code skeleton.

**Defect 2: `_lookup_method_candidates` match precision overclaimed.** v1 principle #6 and decision #8 claimed "simple name + arity + ordered erased param-type-fqns" matching, citing `_lookup_method_candidates` as precedent. The actual function (`build_ast_graph.py:776-835`) matches on name + arity only (`m.decl.name == callee_simple` and `len(m.decl.parameters) == arg_count`). There is no param-type-FQN comparison. v2 corrects the claim: `signature` equality provides name+arity+simple-type-name semantics, which is consistent with (and slightly more precise than) the existing function.

**Defect 3: Naming convention stale.** v1 used parens-suffix naming (`(via signature)`, `(via overrides)`) matching PR-89's v1 convention, which PR-89 v2 replaced with dot notation (`DECLARES.DECLARES_CLIENT`). v2 aligns: standalone keys `OVERRIDDEN_BY` / `OVERRIDES` for the virtual dispatch relation; composed keys `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES` for brownfield projections. Consistent with PR-89's `<parent>.<projected>` convention, with the distinction that the parent relation is virtual (computed) rather than stored.

**Defect 4: §1 frame overstated Java's role.** v1 said "the graph correctly records what Java records: `@CodebaseClient` is on the override, not on the declaration. Dispatch is runtime; the graph can't promise it." This mirrors the same overstatement PR-89 v2.1 fixed. `@CodebaseClient` on the override is by deliberate operator design — each concrete method represents one specific outbound call. v2 reframes as **scope translation**: the override-method-grain truth is correct; the agent's interface-method-grain question deserves an interface-method-grain answer.

**Defect 5: `is_static` flag phantom.** v1 decision #11 referenced an `is_static` flag on Symbol nodes. No such column exists. The `modifiers STRING[]` column stores `"static"` when applicable. v2 corrects: filter by `"static" IN m.modifiers` in the Cypher query.

**Defect 6: §3.1 example contradicted omission rule.** v1's JSON example showed `"EXPOSES (via overrides)": {"in": 0, "out": 0}` while the text said it should be omitted when zero. v2 removes the zero-count line from the example, consistent with principle #7.

### What stayed unchanged from v1

- The architectural call: composed 2-hop rollup, no schema change, no new edge types, no precomputed columns.
- The trigger set (method Symbols only; `method | constructor`).
- The depth (one `IMPLEMENTS`/`EXTENDS` hop, no recursion).
- All 16 use cases (column headers renamed; UC8/UC10/UC15 notes updated for `signature`/`modifiers` grounding).
- Test count and shape (5 new tests, same scenarios).
- Decisions #1, #2, #3, #6 (renumbered), #7, #9, #10, #12, #13 unchanged in substance.

### What changed and why (summary)

| Change | v1 | v2 | Why |
|---|---|---|---|
| Key syntax | `(via signature)` / `(via overrides)` | dot notation: `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, etc. | Align with PR-89 v2's convention |
| Cypher match | `param_count` + `param_type_fqns` | `signature` string equality | Phantom columns; `signature` is the actual schema |
| `_lookup_method_candidates` claim | name + arity + param_type_fqns | name + arity (via `signature`) | Function does name+arity only |
| §1 frame | "Java forces this" | Scope translation | Same fix as PR-89 v2.1 defect 1 |
| Static-method filter | `is_static` flag | `"static" IN modifiers` | No `is_static` column on Symbol |
| §3.1 example | Zero-count key shown | Omitted | Match omission rule |
| Decision count | 17 | 18 (added #18: override-predicate deferral) | Symmetric with PR-89 #16 |
| Risk count | 7 | 8 (added operator-misreading row) | Symmetric with PR-89 |
| §5 NOT-do rows | 11 | 12 (added override-predicate row) | Symmetric with PR-89 |
| Status | `draft` | `under review (v2)` | — |
