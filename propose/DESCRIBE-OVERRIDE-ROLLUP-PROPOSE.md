# DESCRIBE-OVERRIDE-ROLLUP — Surface override-axis affordances on method `describe` so the dispatch chasm is visible

**Status**: draft
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12

## TL;DR

- **The call**: when `describe` is called on a **method Symbol** declared on an interface or abstract class (or, symmetrically, on a concrete method that overrides one), `edge_summary` adds synthetic rollup keys exposing the override relationship and any brownfield signal hidden behind it.
- **The keys** (method side, symmetric to PR-89's class side):
  - on an abstract/interface method: `OVERRIDDEN_BY (via signature)`, `DECLARES_CLIENT (via overrides)`, `EXPOSES (via overrides)`
  - on a concrete override: `OVERRIDES (via signature)`
- **Why**: today, an agent walking `Foo.process` → `CALLS` → `AssignClient.openChat` (the interface method) sees an `edge_summary` with `CALLS` and `DECLARES` only. The concrete `LocalAssignClient.openChat` that carries `@CodebaseClient` is reachable only through a 5-hop walk with a name-and-arity join in the middle. No agent will guess it; the chasm is invisible.
- **Scope**: surgical. Read-path Cypher; **no schema change**, no new edge types persisted, no indexing pass. Walks `IMPLEMENTS`/`EXTENDS` (class-level, already there) + same-signature filter on declared members.
- **Symmetric to PR-89**: PR-89 fixed the **containment axis** (class loses signal that lives on its members). This propose fixes the **dispatch axis** (interface method loses signal that lives on its concrete overrides). Same shape: synthetic rollup keys in `edge_summary`, parens-suffix naming, omitted when zero.
- **Migration**: 1 PR. ~60 LoC behind `_edge_summary_for_node` + helper, ~5 new tests.
- **Risk**: dispatch resolution is heuristic (Java has no compile-time guarantee that `LocalAssignClient.openChat(...)` is the override — it's a name + arity + supertype-chain match). The propose makes this explicit in the key suffix (`(via signature)`) and confidence semantics.

## 1. Frame: what is this thing, really?

> **`describe` on an interface or abstract method must tell the agent that the *interesting* signal lives one dispatch-hop away. Otherwise the agent halts at a node whose `edge_summary` is empty by design, even though the answer to their question is one indirection below.**

The graph faithfully records what Java records: `@CodebaseClient` is on the override, not on the declaration. Dispatch is runtime; the graph can't promise it. But the agent's question — "is this method involved in cross-service communication?" — depends on the answer being one indirection below. Today the agent has no breadcrumb for that indirection.

The override rollup re-presents the same edge data from the method's dispatch vantage point, without modifying the underlying graph. The graph is unchanged; the agent's view of the graph is more useful.

This frame **rules out**:

- Adding a persisted `OVERRIDES` / `OVERRIDDEN_BY` edge table to the schema
- Adding precomputed scalar fields on method Symbol rows
- Rewriting `CALLS` to also point at the override(s) (would falsify call-site resolution)
- Modifying the underlying graph schema in any way

## 2. Design principles

1. **Synthetic rollup, not schema change.** The new `edge_summary` keys are computed at `describe`-time from existing edges (`IMPLEMENTS`/`EXTENDS` class-level + member signature match). Reindexing produces no new rows, columns, or tables.
2. **Method Symbols only.** Rollup applies when the described node is a method Symbol (`kind ∈ {method, constructor}` — constructors are included for completeness but in practice produce zero overrides). Type Symbols, Routes, and Clients get their existing behaviour (PR-89 for types) unchanged.
3. **Two vantage points, named explicitly.**
   - `OVERRIDDEN_BY (via signature)` on the **declaration side** (interface/abstract method): count of concrete methods that override this one.
   - `OVERRIDES (via signature)` on the **implementation side** (concrete override): count of declarations this method overrides (typically 1 — single inheritance; >1 only when implementing same-signature methods from multiple interfaces).
4. **Naming makes the synthesis obvious.** Parens-suffix `(via overrides)` or `(via signature)` flags rollup. Distinct from PR-89's `(via members)` suffix so an agent reading both knows which axis is being walked.
5. **Direction stays meaningful.** Override rollup keys carry `{in, out}` shape:
   - `OVERRIDDEN_BY (via signature)`: `{in: 0, out: N}` (declaration → impls; conceptually "out" because we walk away from the declaration toward concrete overrides).
   - `OVERRIDES (via signature)`: `{in: 0, out: M}` (impl → declarations).
   - `DECLARES_CLIENT (via overrides)` / `EXPOSES (via overrides)`: `{in: 0, out: N}` — count of brownfield edges on the overrides.
6. **Signature-match, not name-only.** Two methods are "same-signature" iff: simple name matches, arity matches, parameter type FQNs match in order. Generic erasure: type parameters use the erasure (`T` → `Object`). This matches the resolution Java actually uses for dispatch.
7. **Single hop only.** One level of override: declaration ↔ direct concrete overrides. Transitive override chains (interface A extends interface B; class C implements A) are walked separately — describe `A.m` to see overrides of A; describe `B.m` to see overrides of B. The agent does the recursion explicitly, the rollup doesn't.
8. **Don't double-count with PR-89's class-level rollup.** A concrete override class's `DECLARES_CLIENT (via members)` already counts its own `@CodebaseClient` methods. The override-axis rollup on the interface method counts the **same** edges from a different vantage point — this is expected, not a bug. Each rollup answers a different question; both being non-zero is the correct picture.

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
  "DECLARES": {"in": 1, "out": 0},
  "CALLS":    {"in": 1, "out": 0},
  "OVERRIDDEN_BY (via signature)":   {"in": 0, "out": 1},
  "DECLARES_CLIENT (via overrides)": {"in": 0, "out": 1},
  "EXPOSES (via overrides)":         {"in": 0, "out": 0}
}
```

`EXPOSES (via overrides)` is omitted (count 0); the schema-omission rule from `edge_counts_for` applies. With multiple impls (e.g. `LocalAssignClient`, `RemoteAssignClient`, neither tagged), the override-axis rollup still shows `OVERRIDDEN_BY (via signature): out: 2` and both brownfield keys omitted.

### 3.2 `edge_summary` shape on a concrete override

For `LocalAssignClient.openChat` (concrete, `@CodebaseClient` on it):

```json
{
  "DECLARES":        {"in": 1, "out": 0},
  "DECLARES_CLIENT": {"in": 0, "out": 1},
  "OVERRIDES (via signature)": {"in": 0, "out": 1}
}
```

The `OVERRIDES (via signature)` key tells the agent: "this method has a declaration upstream — describe it if you want to find sibling implementations." Symmetric to the interface side.

### 3.3 Cypher sketch (illustrative — exact form belongs in a plan)

Two queries on the declaration side (described method = `m`):

```cypher
// Walk: described method's declaring type -> classes that implement/extend it
//       -> for each such class, methods with same (name, arity, param-type-fqns)
MATCH (m:Symbol {id: $id})<-[:DECLARES]-(t:Symbol)
MATCH (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t)
MATCH (impl)-[:DECLARES]->(mover:Symbol)
WHERE mover.name = m.name
  AND mover.param_count = m.param_count
  AND mover.param_type_fqns = m.param_type_fqns
RETURN collect(DISTINCT mover.id) AS impl_method_ids
```

Then, for the brownfield rollups, count `DECLARES_CLIENT` / `EXPOSES` outgoing edges from the collected impl method ids. Three counts total per describe of an abstract method: `OVERRIDDEN_BY`, `DECLARES_CLIENT (via overrides)`, `EXPOSES (via overrides)`.

Symmetric query on the implementation side: walk `(m)<-[:DECLARES]-(impl)-[:IMPLEMENTS|EXTENDS]->(decl)-[:DECLARES]->(decl_m:Symbol)` with same-signature filter; count distinct `decl_m`.

### 3.4 How the agent uses it

In `docs/AGENT-GUIDE.md` under `describe`:

> Override-axis rollup keys may appear in `edge_summary` for method Symbols:
>
> - `OVERRIDDEN_BY (via signature)` — appears on interface / abstract method declarations. Walk: `neighbors(ids=<method_id>, direction="in", edge_types=["DECLARES"])` → for the declaring type, `neighbors(ids=<type_id>, direction="in", edge_types=["IMPLEMENTS","EXTENDS"])` → for each impl class, `neighbors(ids=<impl_class_id>, direction="out", edge_types=["DECLARES"])` and filter on matching `(name, param_count, param_type_fqns)`.
> - `DECLARES_CLIENT (via overrides)` / `EXPOSES (via overrides)` — same walk, then on each impl method id query `DECLARES_CLIENT` / `EXPOSES`.
> - `OVERRIDES (via signature)` — appears on concrete override methods. Symmetric walk upward through the declaring class's supertypes.
>
> These keys are **synthetic** (computed at describe-time via signature match, not stored as graph edges). Do not pass them to `neighbors(edge_types=...)`. Use them as a hop affordance only.

### 3.5 What does NOT change

- `_edge_summary_for_node` for non-method Symbols is byte-identical to today and to PR-89.
- The Kuzu schema is unchanged. No new tables, no new columns, no new fields on `Symbol`.
- `CALLS` resolution is **unchanged**. `Foo.process` still `CALLS` `AssignClient.openChat`, not the impl. The override rollup is purely additive presentation on the interface method's describe output.
- `neighbors` accepts the same edge types as before. Synthetic keys cannot be passed in `edge_types=`.

## 4. Use-case re-walk

16 cases covering interface/abstract/sealed/generic/diamond scenarios.

| # | Scenario | Describe target | OVERRIDDEN_BY rollup | DECLARES_CLIENT (via overrides) | EXPOSES (via overrides) | OVERRIDES rollup | Notes |
|---|---|---|---|---|---|---|---|
| UC1 | The originating case: `AssignClient.openChat` interface method, one impl `LocalAssignClient.openChat` carries `@CodebaseClient` | interface method | `out: 1` | `out: 1` | (omitted) | n/a | the bug-fix case from the user's question |
| UC2 | Same as UC1, agent now describes `LocalAssignClient.openChat` | concrete override | n/a | n/a | n/a | `out: 1` | symmetric — pointer back to the declaration |
| UC3 | Interface method with no implementations indexed in the codebase | interface method | (omitted) | (omitted) | (omitted) | n/a | rollup correctly silent |
| UC4 | Interface method with 4 impls; none carry brownfield annotations | interface method | `out: 4` | (omitted) | (omitted) | n/a | dispatch affordance still emitted even when brownfield is silent |
| UC5 | Abstract class method, 2 subclass overrides, 1 carries `@PostMapping` | abstract method | `out: 2` | (omitted) | `out: 1` | n/a | route-side symmetry |
| UC6 | Concrete method that does NOT override anything (defined directly on a class, no supertype declares it) | concrete method | n/a | n/a | n/a | (omitted) | `OVERRIDES` rollup silent — no upstream declaration |
| UC7 | Concrete method implementing **two** interface methods with same signature (diamond) | concrete method | n/a | n/a | n/a | `out: 2` | counted once per upstream declaration |
| UC8 | Static method on an interface (Java 8+) | interface method (static) | (omitted) | (omitted) | (omitted) | n/a | static methods aren't dispatched; rollup silent. Plan-level: filter by `is_static` if available |
| UC9 | Default method on an interface, overridden by 1 impl | interface method (default) | `out: 1` | varies | varies | n/a | default methods are dispatched; treated like abstract ones for rollup |
| UC10 | Generic method `<T> List<T> findAll()` on interface, impl is `List<String> findAll()` | interface method | `out: 1` | varies | varies | n/a | erasure: generic `T` → `Object`; signature match on erasure. Plan-level: confirm AST surfaces erased types |
| UC11 | Constructor (described directly) | constructor | n/a (no rollup) | n/a | n/a | n/a | constructors aren't overridden; rollup helpers return empty dict |
| UC12 | Method on a sealed interface with 3 permitted impls, all annotated | interface method | `out: 3` | `out: 3` | (omitted) | n/a | sealed doesn't change anything; rollup just counts |
| UC13 | Interface method overridden indirectly: `interface A { void m(); } interface B extends A {} class C implements B { void m() {...} }` | method on A | `out: 0` (single hop) | (omitted) | (omitted) | n/a | **one hop only**: C implements B, not A. Agent describes B.m next if interested |
| UC14 | Same chain, described on B.m | method on B (inherited declaration; only present if B redeclares) | varies | varies | varies | n/a | only meaningful if B redeclares the method; otherwise B.m doesn't exist as a Symbol |
| UC15 | Method name overload: `process(String)` on interface, impl has both `process(String)` and `process(int)` | interface method | `out: 1` | varies | varies | n/a | arity + param-type-fqn filter excludes `process(int)` |
| UC16 | Pathological: interface method with 50 impls, all in different packages | interface method | `out: 50` | varies | varies | n/a | rollup is one Cypher with cardinality bounded by IMPLEMENTS/EXTENDS in-degree of the declaring type; acceptable |

**Gaps found in walk**:

- UC7 (diamond) confirmed: a concrete override of two same-signature interface declarations gets `OVERRIDES (via signature): out: 2`. No de-dup; both are real upstream declarations.
- UC8 (static interface methods) needs `is_static` info on the method Symbol to filter; if unavailable, the rollup will incorrectly show overrides for static methods. **Decision**: if `is_static` is present, filter; else rely on the fact that statics rarely have same-signature overrides in practice and document the limitation in the plan.
- UC10 (generics): signature-match must use erased types. Confirms the propose's reliance on `param_type_fqns` being erased (consistent with how `_lookup_method_candidates` walks supertypes today).
- UC13 (transitive chain) explicitly bounded to one hop. The agent recurses by describing the next-level type.

## 5. What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add a persisted `OVERRIDES` / `OVERRIDDEN_BY` edge table | Couples indexing to a presentation concern. Same reasoning as PR-89: a synthetic Cypher rollup is cheap enough, and dispatch resolution is heuristic — committing it to an edge would lie. |
| Rewrite `CALLS` to also point at concrete overrides | Falsifies call-site resolution. The interface method is what the source actually refers to. |
| Add scalar fields like `override_count` to Symbol rows | Couples indexing to read-time. Every future rollup would need its own column. |
| Recurse override chains (transitive impls) | Multi-hop fanout, harder to reason about. The agent walks step-by-step. UC13 demonstrates the explicit chain. |
| Suppress rollup when `CALLS` already reaches the impl directly (e.g. static dispatch through `LocalAssignClient.openChat` typed field) | Different call-site, different question. If the agent describes the interface method, they get the override rollup regardless of any specific call site. |
| Roll up `HTTP_CALLS (via overrides)` / `ASYNC_CALLS (via overrides)` | Symmetric to PR-89's deferral. Same reasoning: a separate propose if surfaced. |
| Compute "best" override (most-derived, most-annotated) | YAGNI. The agent gets a count + the walk pattern; ranking is application logic. |
| Filter rollup counts by `confidence` | `edge_summary` is unfiltered counts everywhere. Stay consistent. |
| Surface the rollup on Field / Class / Route / Client nodes | Out of axis. Only methods are dispatched. |
| Use a `OVERRIDES` rel name without `(via signature)` suffix | Would falsely promise an edge that doesn't exist. The suffix is load-bearing. |
| Cross-package signature-match when no `IMPLEMENTS`/`EXTENDS` chain exists | Would invent dispatch relationships. The walk is constrained by actual class-hierarchy edges. |
| Surface a "this method is itself only a declaration" flag separately | The rollup output already conveys it: `OVERRIDDEN_BY (via signature)` present iff declaration; `OVERRIDES (via signature)` present iff override. |

## 6. Migration plan — 1 PR

### PR-DESCRIBE-OVERRIDE-1: synthetic override-axis rollup in `describe.edge_summary`

- **Purpose**: extend `_edge_summary_for_node` to add up to four override-axis synthetic keys when the described node is a method Symbol.
- **Implementation surface**:
  - Add helper `KuzuGraph.override_axis_rollup_for(method_id)` returning `dict[str, dict[str, int]]` with up to four keys.
  - Internally:
    1. Resolve the method's declaring type, name, param_count, param_type_fqns.
    2. Run the dispatch-down walk (declarations → impls) and dispatch-up walk (override → declarations).
    3. Count `DECLARES_CLIENT` / `EXPOSES` outgoing edges on the collected override method ids.
    4. Emit non-zero keys only.
  - In `mcp_v2._edge_summary_for_node` (or `describe_v2`), check `kind == "symbol"` AND `data.kind in {"method", "constructor"}`. If so, call the rollup helper and merge non-zero results.
  - Update `DescribeOutput` documentation / pydantic field-level description to mention method-side rollup keys.
- **Test summary**: 5 new tests in `tests/test_mcp_v2.py`:
  - `test_describe_interface_method_with_annotated_impl_emits_rollup` — UC1 fixture.
  - `test_describe_concrete_override_emits_overrides_rollup` — UC2 fixture.
  - `test_describe_method_no_overrides_silent` — UC6 fixture (no upstream, no impls).
  - `test_describe_abstract_method_with_route_override_emits_exposes` — UC5 fixture (route-side).
  - `test_describe_interface_method_diamond_override_counts_once_per_upstream` — UC7 fixture.
- **Doc updates**: one paragraph in `docs/AGENT-GUIDE.md` under `describe` (text in §3.4 above).

## 7. Decisions taken (no longer open)

1. **Fix shape**: synthetic rollup in `edge_summary`, computed at `describe`-time. **No schema change**, no `OVERRIDES` edge table, no precomputed fields.
2. **Trigger**: only when described node is a method Symbol (`data.kind in {method, constructor}`).
3. **Axes covered**: dispatch-down (declaration → impls) AND dispatch-up (impl → declarations) — symmetric.
4. **Key names**:
   - `OVERRIDDEN_BY (via signature)` (declaration side)
   - `OVERRIDES (via signature)` (implementation side)
   - `DECLARES_CLIENT (via overrides)` and `EXPOSES (via overrides)` (brownfield rollup on declaration side)
5. **Suffix convention**: `(via signature)` for dispatch-axis keys; `(via overrides)` for brownfield rollups walked through overrides. Distinct from PR-89's `(via members)`.
6. **Direction shape**: `{in: 0, out: N}` for every override-axis rollup key (members aren't pointed at by these synthetic relationships in any meaningful "in" direction).
7. **Omission rule**: when count is 0, the key is omitted entirely.
8. **Signature match**: simple name + arity + ordered erased param-type-fqns. Generic types use erasure (`T → Object`). Matches `_lookup_method_candidates`'s existing semantics.
9. **Depth**: exactly one `IMPLEMENTS`/`EXTENDS` hop on either side. Transitive override chains require explicit agent recursion (UC13).
10. **Diamond impls**: a concrete override counts once per **upstream declaration** (UC7). No de-dup of multi-interface implementation.
11. **Static interface methods**: filter via `is_static` flag if present on the AST; else accept that statics may produce false positives in pathological cases and document the limitation.
12. **Constructors**: included in trigger but rollup helper returns empty for them in practice (no upstream to override).
13. **Surface**: extends `edge_summary` only. No new MCP tool, no new flag on `describe`.
14. **Querying**: rollup keys are read-only. Passing them to `neighbors(edge_types=...)` returns the existing parser error.
15. **Interaction with PR-89's `(via members)`**: both rollups answer different questions about the same edge data. Both being non-zero on related nodes is expected, not double-counting.
16. **`HTTP_CALLS (via overrides)` / `ASYNC_CALLS (via overrides)`**: deferred. Separate propose if surfaced (symmetric to PR-89's deferral).
17. **Documentation**: AGENT-GUIDE gets one paragraph naming the synthetic keys and the explicit walk pattern.

## 8. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Signature-match diverges from what JVM dispatch actually does (varargs, generic erasure edge cases) | Mirror `_lookup_method_candidates`'s existing semantics (already battle-tested for `CALLS` resolution). Plan-level: factor the signature-match predicate into a shared helper. |
| Static interface methods produce spurious "overrides" | Filter by `is_static` if available. Limitation documented in AGENT-GUIDE if `is_static` flag isn't on Symbol today. |
| Performance regression on widely-implemented interfaces (e.g. `Runnable`, 100s of impls in a large codebase) | Walk is bounded by `IMPLEMENTS`/`EXTENDS` in-degree on the declaring type. Even at 100 impls, one Cypher with a same-signature filter. Plan-level: measure on `tests/bank-chat-system` and the user's brownfield enterprise fixture. |
| Agents treat synthetic keys as real edges and pass them to `neighbors` | Key naming (`(via signature)` / `(via overrides)` suffix) and AGENT-GUIDE paragraph spell out the synthesis. `neighbors` already rejects unknown edge types. |
| Interaction with PR-89's `(via members)` rollup confuses reviewers | The two rollups are presented on **different node kinds** (PR-89: type Symbol; this propose: method Symbol). They never appear on the same `edge_summary`. AGENT-GUIDE addresses both under their respective `describe` sections. |
| Generic erasure mismatch when AST stores parameterized types instead of erased FQNs | Plan-level: confirm `methods_by_type` rows expose erased `param_type_fqns`. If not, the plan adds an erasure helper before the rollup query. |
| Diamond overrides (UC7) produce surprisingly high `OVERRIDES` counts on common interfaces | Expected, documented in the key semantics. The agent's takeaway: "this method implements N interface contracts; describe each declaration to find the operator's intent." |

## Appendix A — Concrete artifact: `override_axis_rollup_for` skeleton

```python
# kuzu_queries.py — KuzuGraph method (plan-level: exact placement decided in PR)

_OVERRIDE_METHOD_KINDS = {"method", "constructor"}

def override_axis_rollup_for(self, method_id: str) -> dict[str, dict[str, int]]:
    """Synthetic rollup of override-axis dispatch affordances for a method Symbol.

    Emits up to four keys (any with count 0 are omitted):
      - OVERRIDDEN_BY (via signature)   : declaration-side, dispatch-down count
      - OVERRIDES (via signature)       : impl-side, dispatch-up count
      - DECLARES_CLIENT (via overrides) : brownfield rollup over impls
      - EXPOSES (via overrides)         : brownfield rollup over impls

    Both directions are computed because we don't a-priori know whether the
    described method is a declaration, an override, or both (default methods).
    """
    # Dispatch-down: find concrete methods on subclasses with same signature
    impl_ids = self._rows(
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(t:Symbol) "
        "MATCH (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t) "
        "MATCH (impl)-[:DECLARES]->(mover:Symbol) "
        "WHERE mover.name = m.name "
        "  AND mover.param_count = m.param_count "
        "  AND mover.param_type_fqns = m.param_type_fqns "
        "  AND mover.id <> m.id "
        "RETURN collect(DISTINCT mover.id) AS impl_method_ids",
        {"id": method_id},
    )
    impls: list[str] = (impl_ids[0].get("impl_method_ids") if impl_ids else []) or []

    # Dispatch-up: find declaration methods on supertypes with same signature
    decl_ids = self._rows(
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(impl:Symbol) "
        "MATCH (impl)-[:IMPLEMENTS|EXTENDS]->(parent:Symbol) "
        "MATCH (parent)-[:DECLARES]->(decl_m:Symbol) "
        "WHERE decl_m.name = m.name "
        "  AND decl_m.param_count = m.param_count "
        "  AND decl_m.param_type_fqns = m.param_type_fqns "
        "  AND decl_m.id <> m.id "
        "RETURN collect(DISTINCT decl_m.id) AS decl_method_ids",
        {"id": method_id},
    )
    decls: list[str] = (decl_ids[0].get("decl_method_ids") if decl_ids else []) or []

    out: dict[str, dict[str, int]] = {}
    if impls:
        out["OVERRIDDEN_BY (via signature)"] = {"in": 0, "out": len(impls)}
        # Brownfield rollup over impls
        n_clients = self._count_outgoing(impls, "DECLARES_CLIENT")
        n_routes = self._count_outgoing(impls, "EXPOSES")
        if n_clients > 0:
            out["DECLARES_CLIENT (via overrides)"] = {"in": 0, "out": n_clients}
        if n_routes > 0:
            out["EXPOSES (via overrides)"] = {"in": 0, "out": n_routes}
    if decls:
        out["OVERRIDES (via signature)"] = {"in": 0, "out": len(decls)}
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

First draft. No revisions yet.
