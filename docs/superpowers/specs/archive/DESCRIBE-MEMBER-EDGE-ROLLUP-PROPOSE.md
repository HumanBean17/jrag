> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# DESCRIBE-MEMBER-EDGE-ROLLUP — Surface method-level `DECLARES_CLIENT` / `EXPOSES` in the class's `edge_summary`

**Status**: **completed** — landed as PR-1 (read-path rollup; see [`plans/completed/PLAN-DESCRIBE-MEMBER-EDGE-ROLLUP.md`](../../plans/completed/PLAN-DESCRIBE-MEMBER-EDGE-ROLLUP.md)).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12

## TL;DR

- **The call**: when `describe` is called on a **type Symbol** (`class`, `interface`, `enum`, `record`, `annotation`), `edge_summary` adds two composed rollup keys aggregating the type's members' outgoing brownfield/route edges: `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES`.
- **Naming**: dot notation `<parent_relation>.<projected_relation>` makes the composition explicit. `DECLARES.DECLARES_CLIENT` reads as *"the `DECLARES_CLIENT` projection reached via the type's `DECLARES` children"*. The dot is not a valid `EdgeType` literal, so these keys cannot be passed to `neighbors(edge_types=…)` — Pydantic rejects them.
- **Why**: today, a class annotated `@CodebaseRole(CLIENT)` + `@CodebaseCapability(HTTP_CLIENT)` whose 3 methods carry `@CodebaseClient` exposes **zero** signal of cross-service communication through `describe`. The brownfield `Client` nodes hang off the methods; the class's `edge_summary` only shows `DECLARES`, `EXTENDS`, etc. The agent has to know to do 2–3 extra hops it has no breadcrumb for.
- **Scope**: surgical. Two composed keys, computed at `describe`-time via a 2-hop Cypher query. **No schema change.** No new edge types, no precomputed scalar fields on type rows.
- **Symmetric**: covers both client side (`DECLARES.DECLARES_CLIENT`) and route side (`DECLARES.EXPOSES`) — the same architectural pattern (method-attached edges invisible from the class) bites both.
- **Migration**: 1 PR. ~30 LoC behind `_edge_summary_for_node`, ~4 new tests.
- **Risk**: minimal. Additive composed keys; pure read-path; old consumers see new keys but every existing key keeps its meaning.

## 1. Frame: what is this thing, really?

> **`describe` is the agent's "what is this node?" primary call. Its `edge_summary` must answer questions at the *scope the agent asked about*. When the agent calls `describe` on a class, the answer should summarise cross-service participation at class-grain, even when the underlying truth is recorded at method-grain.**

The brownfield contract attaches `@CodebaseClient` / `@CodebaseRoute` (and Spring's `@PostMapping`, `@FeignClient`-method, etc.) **to methods by deliberate design** — each method represents one outbound call or one exposed route, with its own target, path, and `client_kind` / `framework`. The granularity is meaningful: the graph correctly records `DECLARES_CLIENT: Symbol → Client` and `EXPOSES: Symbol → Route` at the method level. That granularity is right for traversal.

It is **wrong for class-grain affordance**. An agent calling `describe` on a class is asking a coarser-grained question than the operator answered at the method level: *"does this class participate in any cross-service communication, and is it worth walking to find out where?"* Today's `edge_summary` answers structural questions about the class (`DECLARES`, `EXTENDS`, `IMPLEMENTS`, `INJECTS`) but is silent on the brownfield projections that hang off its methods. The agent has to know to walk through one hop it has no breadcrumb for.

The rollup is a **scope translation**, not a recovery of "hidden class-level intent." The method-grained truth stays where it is; we add a class-grained summary indexed by the class, computed at read-time. The graph is unchanged; the agent's view of the graph gains an affordance at the scope it asked about.

This frame **rules out**:
- adding new edge types (no `DECLARES_CLIENT_AT_CLASS_LEVEL`)
- adding precomputed scalar fields on type Symbol rows (no `member_client_count`)
- modifying the underlying graph schema in any way

## 2. Design principles

1. **Composed rollup, not schema change.** The new `edge_summary` keys are computed at `describe`-time from existing edges. Reindexing produces no new rows, columns, or tables.
2. **Type Symbols only.** Rollup applies when the described node is a type Symbol (`kind ∈ {class, interface, enum, record, annotation}`). Method Symbols, Route nodes, and Client nodes get today's behaviour unchanged.
3. **Naming names the composition.** Composed keys take the form `<parent_relation>.<projected_relation>` (e.g. `DECLARES.DECLARES_CLIENT`). The parent relation is the one-hop traversal off the described type; the projected relation is the edge counted at the second hop. The dot is **not** a valid `EdgeType` literal, so these keys raise a Pydantic `ValidationError` if passed to `neighbors(edge_types=…)` — composition is read-only by construction, not just by convention.
4. **Direction stays meaningful.** Composed keys carry the same `{in, out}` shape as real edges. For class-attached composition, `out` counts edges from members of the type to their targets; `in` is always 0 (members are not pointed at by `DECLARES_CLIENT` / `EXPOSES`).
5. **Don't double-count.** A composed key never re-counts edges already attributed to the type. Class-level `DECLARES_CLIENT` (which doesn't exist today; the table is `FROM Symbol TO Client` where Symbol is a member) cannot inflate the rollup — the 2-hop Cypher counts only `(class)-[DECLARES]->(member)-[DECLARES_CLIENT]->(client)` chains.
6. **Cheap query, bounded fanout.** A type Symbol's `DECLARES` out-degree is bounded by its declared-member count (typically < 50). Two 1-hop queries (one per composed key) per describe call. No global aggregation, no scan over `Symbol`.
7. **No new MCP surface.** No new tool, no new flag on `describe`. The information appears in the existing field, in the existing call.
8. **Document, don't infer.** AGENT-GUIDE adds one paragraph under `describe` semantics naming the composed keys, the dot convention, and the explicit 2-hop walk pattern.

## 3. The proposed surface

### 3.1 `edge_summary` shape

Today's shape:

```json
{
  "DECLARES":   {"in": 0, "out": 4},
  "EXTENDS":    {"in": 0, "out": 4},
  "IMPLEMENTS": {"in": 0, "out": 1},
  "INJECTS":    {"in": 1, "out": 0}
}
```

After the change, for a class whose 3 of 4 methods carry `@CodebaseClient`:

```json
{
  "DECLARES":                  {"in": 0, "out": 4},
  "EXTENDS":                   {"in": 0, "out": 4},
  "IMPLEMENTS":                {"in": 0, "out": 1},
  "INJECTS":                   {"in": 1, "out": 0},
  "DECLARES.DECLARES_CLIENT":  {"in": 0, "out": 3}
}
```

`DECLARES.EXPOSES` is **omitted** here (count 0). Each composed key is emitted only when its count is positive (same convention `edge_counts_for` uses today: zero-row keys aren't emitted). A controller class with 5 routes and no clients sees only `DECLARES.EXPOSES`; a generic POJO sees neither.

The dot syntax encodes the composition path: `<parent>.<projected>`. Future composed keys follow the same shape (e.g. `DECLARES.HTTP_CALLS` if the deferred case in decision #13 is ever surfaced).

### 3.2 Cypher (illustrative — exact form belongs in a plan)

Two queries, run only when the described node is a type Symbol:

```cypher
// DECLARES.DECLARES_CLIENT
MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->()
RETURN count(e) AS n

// DECLARES.EXPOSES
MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->()
RETURN count(e) AS n
```

Direction is fixed (out) because both edge tables are `Symbol → Client` / `Symbol → Route` respectively. No `in` counterpart query is needed; `in` is always 0 for these composed keys.

### 3.3 How the agent uses it

In `docs/AGENT-GUIDE.md` under `describe`:

> **Composed `edge_summary` keys.** For type Symbols, `edge_summary` may include keys with dot notation: `<parent_relation>.<projected_relation>`. Two are emitted today:
>
> - `DECLARES.DECLARES_CLIENT` — the type's methods declare brownfield HTTP clients (count is the number of `Client` rows reached through `DECLARES → DECLARES_CLIENT`). To enumerate them: `neighbors(ids=<class_id>, direction="out", edge_types=["DECLARES"])` → for each method id, `neighbors(ids=<method_id>, direction="out", edge_types=["DECLARES_CLIENT"])`.
> - `DECLARES.EXPOSES` — the type's methods expose routes. Same walk shape with `EXPOSES`.
>
> Composed keys are **read-only**: they cannot be passed to `neighbors(edge_types=…)` (the dot is not a valid `EdgeType` literal — the call fails with a Pydantic `ValidationError`). Use them as a hop affordance only.
>
> Note on counting semantics: composed counts measure **edge rows**, not distinct member methods. One method that declares multiple `Client` rows (e.g. a `rest_template` method with several call sites) contributes its full edge count to `DECLARES.DECLARES_CLIENT`. The "does this class have any clients?" predicate is answered by `count > 0`; the count itself is an affordance for how rich the downstream walk will be.

### 3.4 What does NOT change

- `_edge_summary_for_node` for non-type Symbols (methods, constructors, routes, clients) is byte-identical to today.
- The Kuzu schema is unchanged. No new tables, no new columns.
- `neighbors` accepts the same `EdgeType` literals as before. Passing `"DECLARES.DECLARES_CLIENT"` returns a Pydantic `ValidationError` (today's behaviour for any unknown edge type, applied here by construction).
- JSON shape of `DescribeOutput` is unchanged. `edge_summary` is already `dict[str, dict[str, int]]`; the new keys are just additional dict keys.

## 4. Use-case re-walk

15 cases covering the rollup happy path, edge cases, and symmetry.

| # | Scenario | Type Symbol kind | `DECLARES.DECLARES_CLIENT` | `DECLARES.EXPOSES` | Notes |
|---|---|---|---|---|---|
| UC1 | Brownfield HTTP client class, 4 methods, 3 carry `@CodebaseClient` | class | `out: 3` | (omitted) | the originating case from the user's question |
| UC2 | Spring `@RestController` with 5 handler methods | class | (omitted) | `out: 5` | symmetric to UC1 |
| UC3 | POJO with no annotations | class | (omitted) | (omitted) | no composed keys emitted |
| UC4 | `@Service` calling other classes, no HTTP / route annotations on its methods | class | (omitted) | (omitted) | composed keys correctly silent |
| UC5 | Mixed: class with 2 `@CodebaseClient` methods AND 1 `@PostMapping` | class | `out: 2` | `out: 1` | both keys appear |
| UC6 | `@CodebaseClient` on a method later resolved as a Feign exposer — same method appears in both `EXPOSES` and `DECLARES_CLIENT` (per PR-85's locked Feign duplication policy) | class | `out: 1` | `out: 1` | both edges exist; both counted; **no de-dup attempted** |
| UC7 | `@FeignClient` interface with 3 method declarations | interface | `out: 3` | (omitted) | feign client interface: members declare clients; no routes |
| UC8 | Generic `@interface` declaration (no usages) | annotation | (omitted) | (omitted) | declared kind is `annotation`; composed keys silent |
| UC9 | `enum` with methods, none HTTP-related | enum | (omitted) | (omitted) | type kind in rollup-eligible set but no edges |
| UC10 | Method Symbol passed to describe (not a type) | method | n/a | n/a | composed keys skipped entirely; today's behaviour |
| UC11 | Route node passed to describe | n/a (Route) | n/a | n/a | composed keys skipped (not a type Symbol) |
| UC12 | Client node passed to describe | n/a (Client) | n/a | n/a | composed keys skipped (not a type Symbol) |
| UC13 | Nested type: outer class contains an inner class with `@CodebaseClient` methods | class | `out: 0` for outer; `out: N` for inner if `describe` is called on the inner type | (omitted/varies) | **Nested types are not `DECLARES` children of their outer type.** Outer types are registered via recursive `_register_type` (build_ast_graph.py:350-354) without appending to `tables.members`; only methods/constructors land in `members` and produce `DECLARES` edges (build_ast_graph.py:2332-2335). The composition naturally excludes nested types — not by depth choice but by graph structure. The agent reaches inner-type clients by calling `describe` on the inner type directly. |
| UC14 | Type with 50 methods, 0 brownfield annotations | class | (omitted) | (omitted) | query returns 0; composed keys silent; cost is one cheap Cypher with `DECLARES` walk |
| UC15 | Type with 500 declared members (pathological) | class | varies | varies | bounded by member count; still one query each; acceptable |

**Gaps surfaced by walk**:

- **UC6** — Feign double-edge case: both composed keys count it. Matches PR-85's locked Feign duplication policy. No suppression.
- **UC13** — *structural* exclusion of nested types: composition walks `DECLARES`, which by implementation only ever lands on methods/constructors. Nested types are registered separately and are never `DECLARES` targets. The result (outer class shows `out: 0` for inner's clients) is the same as a "one hop deep" rule would produce, but the cause is graph structure, not a depth choice. Decision #7 reflects this.
- **Edge-count vs method-count divergence (carried from v2)**: a single method declaring multiple `Client` rows (e.g. a `rest_template` method with several call sites) contributes its full edge count. `DECLARES.DECLARES_CLIENT: {out: 5}` on a class with 3 client-methods is a coherent answer when those 3 methods declare 5 `Client` rows between them. AGENT-GUIDE paragraph (§3.3) calls this out so agents read the count correctly.

## 5. What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add precomputed scalar fields on type Symbol rows (`member_client_count`, `member_route_count`) | Couples indexing to a read-time concern. The 2-hop Cypher is cheap enough. Every future rollup would need its own column. |
| Add a new edge type `DECLARES_CLIENT_AT_CLASS_LEVEL` | Pollutes the graph schema with synthetic edges. Breaks principle 1. |
| Roll up `CALLS` from methods to their declaring class | Different problem (intra-service call graph aggregation). Separate propose if needed. |
| Roll up `HTTP_CALLS` / `ASYNC_CALLS` (the cross-service edges themselves) to the class | These already attach to **methods**, same as `DECLARES_CLIENT`. The user's question was specifically about the case where `HTTP_CALLS` hasn't been derived yet (only the `Client` exists). If we want `DECLARES.HTTP_CALLS` later, the dot convention makes it a clean follow-up; not folded in here to keep scope tight. |
| Recurse rollups through nested type declarations (`DECLARES.DECLARES.…`) | Moot — nested types are not `DECLARES` children of their outer type (see UC13). Even a recursive variant would not reach inner types via `DECLARES`. A different walk (over the lexical parent chain) would be required, which is out of scope. |
| Filter composed counts by edge `confidence` or `strategy` | `edge_summary` today is unfiltered counts. Stay consistent. |
| Make composed keys queryable via `neighbors(edge_types=...)` | They're composed, not stored. Pydantic `EdgeType` literal rejects them by construction. |
| Use a new field `member_edge_rollups` separate from `edge_summary` | Two places to look. One field, distinguished by dot notation. |
| Restructure to a member-role field (e.g. `member_role_summary` with role labels per member) | **Rejected.** Methods have no role enum in this codebase (`ROLE_ANNOTATIONS` in `ast_java.py` defines roles on **types only** — `CONTROLLER`, `SERVICE`, `CLIENT`, etc.). A method "is a client" only in the sense that it has an outgoing `DECLARES_CLIENT` edge. See decision #15. |
| Add a separate `member_predicates` field (`has_client_methods: bool`, `has_route_methods: bool`) alongside composed counts | **Deferred, not rejected.** The `count > 0` test on a composed key already serves as the predicate today. If agent code accumulates repeated `rollup.get('DECLARES.DECLARES_CLIENT', {}).get('out', 0) > 0` boilerplate, a `member_predicates` field becomes a clean follow-up — it would coexist with composed counts (counts for walk affordance, predicates for branching). See decision #16. |
| Emit composed keys when count == 0 | `edge_summary` already omits zero entries today. Stay consistent. |
| Roll up for interface Symbols that are extended by other classes | Different question (subclass aggregation). Out of scope. |
| Add a CLI verb to list type Symbols with the highest composed counts | YAGNI. `find` already supports filtering. |

## 6. Migration plan — 1 PR

### PR-DESCRIBE-ROLLUP-1: composed rollup keys in `describe.edge_summary`

- **Purpose**: extend `_edge_summary_for_node` (or `KuzuGraph.edge_counts_for`) to add the two composed keys when the described node is a type Symbol.
- **Implementation surface**:
  - Add a new helper (e.g. `KuzuGraph.member_edge_rollup_for(type_id)`) that runs the two 2-hop Cypher queries and returns a `dict[str, dict[str, int]]` slice with keys `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES`.
  - In `mcp_v2._edge_summary_for_node` (or `describe_v2`), check `kind == "symbol"` AND `data["kind"] in {"class", "interface", "enum", "record", "annotation"}`. If so, call the rollup helper and merge non-zero results into the dict before returning.
  - Update `DescribeOutput` documentation / pydantic field-level description to mention composed keys and the dot convention.
- **Test summary**: 4 new tests in `tests/test_mcp_v2.py` (or wherever describe is tested):
  - `test_describe_class_with_brownfield_clients_emits_composed_key` — class + 3 `@CodebaseClient` methods; key `DECLARES.DECLARES_CLIENT` has `out: 3`.
  - `test_describe_controller_class_emits_composed_exposes` — class with 5 `@PostMapping` methods; key `DECLARES.EXPOSES` has `out: 5`.
  - `test_describe_method_symbol_no_composed_keys` — method-kind Symbol; composed keys absent.
  - `test_describe_pojo_no_composed_keys` — class with no brownfield annotations; composed keys absent.
- **Doc updates**: one paragraph in `docs/AGENT-GUIDE.md` under `describe` (text in §3.3 above).

## 7. Decisions taken (no longer open)

1. **Fix shape**: composed rollup in `edge_summary`, computed at `describe`-time. **No schema change**, no new edge types, no precomputed scalar fields.
2. **Scope**: clients AND routes together — symmetric composition for both `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES`.
3. **Trigger**: only when described node is a type Symbol with `data.kind ∈ {class, interface, enum, record, annotation}`.
4. **Key naming**: dot notation `<parent_relation>.<projected_relation>` — `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES`. **Scope of the convention**: this dot syntax covers the `DECLARES.<method_attached_edge>` family — compositions whose first hop lands on a method/constructor Symbol (today: `DECLARES_CLIENT`, `EXPOSES`; deferred: `HTTP_CALLS`, `ASYNC_CALLS`, all `Symbol → Route|Client` where the source Symbol is a member). It does **not** cover compositions whose first hop lands on a non-member Symbol — e.g. `EXTENDS.CALLS`, `IMPLEMENTS.EXPOSES` — because the second hop is then ambiguous (it would need an implicit third `DECLARES` hop on the intermediate type, which the syntax does not encode). If such compositions ever become useful, they require a separate naming decision, not a free reuse of this convention. Supersedes v1's `(via members)` suffix — see Appendix B.
5. **Direction shape**: `{in: 0, out: N}` — same shape as real edges; `in` is always 0 (members are not pointed at by `DECLARES_CLIENT` / `EXPOSES`). If `out == 0`, the whole composed key is omitted (see decision 6).
6. **Omission rule**: when a composed count is 0, the key is omitted entirely (consistent with `edge_counts_for`'s today's behaviour).
7. **Depth**: exactly one `DECLARES` hop. Nested types are *structurally* excluded — they are not `DECLARES` children of their outer type (only methods/constructors are; see UC13 and `build_ast_graph.py:2332-2335`). The composition naturally stops at one hop; no recursive depth-cutoff logic is needed because there is nothing further to recurse into via `DECLARES`.
8. **De-dup**: none. UC6's Feign double-edge case (both `EXPOSES` and `DECLARES_CLIENT` on same method) counts both. Matches PR-85 brownfield-exclusivity.
9. **Confidence/strategy filtering**: none. `edge_summary` is unfiltered counts; composition matches.
10. **Surface**: extends `edge_summary` only. No new MCP tool, no new flag on `describe`.
11. **Querying**: composed keys are **read-only by construction**. The dot is not a valid `EdgeType` literal — Pydantic `_NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(…)` rejects them before any Cypher executes. Not a convention; a type-system invariant.
12. **Documentation**: AGENT-GUIDE gets one paragraph naming the composed keys, the dot convention, the explicit 2-hop walk pattern, and the edge-count vs method-count semantic note.
13. **`DECLARES.HTTP_CALLS` / `DECLARES.ASYNC_CALLS` composed keys**: deferred. Separate propose if surfaced. The dot convention is already future-proof for these.
14. **Backwards compatibility**: every existing `edge_summary` key keeps its meaning; new keys are additive; old JSON consumers that whitelist keys ignore them silently.
15. **Member-role alternative surface**: rejected. Methods have no role enum in this codebase — roles live on types only (`CONTROLLER`, `SERVICE`, `CLIENT`, etc. via `ROLE_ANNOTATIONS` in `ast_java.py`). A `member_role_summary` would have nothing well-defined to put in it. See Appendix B (v2 reasoning, refined in v2.1).
16. **Member-predicate field (`has_client_methods: bool`, `has_route_methods: bool`)**: deferred, not rejected. The `count > 0` test on a composed key already serves as the predicate, and adding a separate field would unlock decision #14's backwards-compat invariant for marginal gain. If agent code accumulates repeated `rollup.get(…, {}).get('out', 0) > 0` boilerplate across enough call sites that it becomes a documented pain point, a `member_predicates` field is a clean follow-up: it would coexist with composed counts (counts for walk affordance, predicates for branching). Revisit when there is evidence of need, not before. See Appendix B.

## 8. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Agents treat composed keys as real edges and pass them to `neighbors` | Pydantic `EdgeType` literal rejects `DECLARES.DECLARES_CLIENT` by construction (decision #11). Failure surfaces at validation time, not silently. AGENT-GUIDE paragraph (§3.3) names the dot convention. |
| Performance regression on large classes (many declared members) | Bounded by `DECLARES` out-degree, typically < 50. Even for a pathological 500-member class the Cypher is two cheap 2-hop joins. Plan-level: micro-benchmark with `tests/bank-chat-system` describe-class fixtures. |
| JSON consumers that whitelist known `edge_summary` keys silently drop the composed keys | Acceptable. Consumers can opt in to the new keys by name. Old consumers see today's keys unchanged. |
| Operators reading the JSON misread `DECLARES.DECLARES_CLIENT` as a typo or a different edge type | The dot syntax visually signals composition, not equality with `DECLARES_CLIENT`. AGENT-GUIDE paragraph names the convention explicitly. |
| Operators read `DECLARES.DECLARES_CLIENT: {out: 5}` as "this class has 5 client methods" when it actually means "5 `Client` rows reached through this class's `DECLARES` children" | AGENT-GUIDE paragraph (§3.3) calls out the edge-count vs method-count semantics explicitly. The "does this class have clients?" predicate is answered by `count > 0`; the count is an affordance for walk richness, not a method-count. |
| Feign double-edge case (UC6) inflates the apparent "size" of brownfield client surface | This is the **correct** count given PR-85's locked Feign duplication policy. If anyone disputes UC6, the propose can be reopened, but the composition matches the underlying graph. |
| The 2-hop walk diverges from `neighbors`-driven exploration if a new edge filter is added later | The rollup helper lives in the same module as `edge_counts_for`. Any future filter change to one should pull through the other. Plan-level note. |
| `DECLARES` is schematically `Symbol → Symbol`; the rollup's correctness depends on the *implementation* invariant that `DECLARES` only ever lands on methods/constructors (`build_ast_graph.py:2332-2335`). If a future change ever emits `DECLARES` edges to other Symbol kinds (e.g. nested types, fields-as-Symbol, package-as-Symbol), `DECLARES.DECLARES_CLIENT` would silently start counting through unintended intermediates. | Plan-level: add an assertion or a test that exercises a class with nested types + fields to confirm `DECLARES` out-degree equals method/constructor count. Re-audit this propose if the `DECLARES` emission rule changes. |

## Appendix A — Concrete artifact: `member_edge_rollup_for` skeleton

```python
# kuzu_queries.py — KuzuGraph method (plan-level: exact placement decided in PR)

_ROLLUP_TYPE_KINDS = {"class", "interface", "enum", "record", "annotation"}

def member_edge_rollup_for(self, type_id: str) -> dict[str, dict[str, int]]:
    """Composed rollup of member-attached brownfield edges, indexed by the type.

    Returns at most two keys: `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES`.
    Both are out-direction only (the underlying edges are Symbol-out -> Client/Route).
    Returns an empty dict when both counts are zero.
    """
    rows = self._rows(
        "MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->() "
        "RETURN count(e) AS n",
        {"id": type_id},
    )
    n_clients = int(rows[0].get("n") or 0) if rows else 0

    rows = self._rows(
        "MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->() "
        "RETURN count(e) AS n",
        {"id": type_id},
    )
    n_routes = int(rows[0].get("n") or 0) if rows else 0

    out: dict[str, dict[str, int]] = {}
    if n_clients > 0:
        out["DECLARES.DECLARES_CLIENT"] = {"in": 0, "out": n_clients}
    if n_routes > 0:
        out["DECLARES.EXPOSES"] = {"in": 0, "out": n_routes}
    return out
```

```python
# mcp_v2.py — change in _edge_summary_for_node or describe_v2

def _edge_summary_for_node_with_rollup(
    graph: KuzuGraph, node_id: str, kind: str, row: dict[str, Any]
) -> dict[str, dict[str, int]]:
    summary = graph.edge_counts_for(node_id)
    if kind == "symbol" and str(row.get("kind") or "") in _ROLLUP_TYPE_KINDS:
        summary.update(graph.member_edge_rollup_for(node_id))
    return summary
```

## Appendix B — What changed (traceability)

### What stayed unchanged from v1

- The frame (§1) — `describe` as the agent's primary affordance for "should I walk this?"
- The architectural call: synthetic 2-hop rollup, no schema change, no new edge types, no precomputed columns.
- The trigger set (type Symbols only; `class | interface | enum | record | annotation`).
- The depth (one `DECLARES` hop, no recursion).
- The de-dup policy on UC6 (Feign double-edge counts both).
- The omission rule (count == 0 → key not emitted).
- All 15 use cases (only the column header names changed).
- Test count and shape (4 new tests, same scenarios).
- Decision #1, #2, #3, #5, #6, #7, #8, #9, #10, #14 unchanged in substance.

### What changed and why

- **Key syntax: `<EDGE> (via members)` → `<parent>.<projected>`** (e.g. `DECLARES_CLIENT (via members)` → `DECLARES.DECLARES_CLIENT`). Self-review surfaced that `(via members)` reads ambiguously: `DECLARES` is *itself* a "via members" edge in spirit (it goes class → method), so the suffix doesn't unambiguously flag synthesis vs traversal shape. Dot notation directly exposes the composition: `<parent_relation>.<projected_relation>`. The parent relation appears first, the projected relation second — matching how the 2-hop Cypher is structured (`-[:DECLARES]->(m)-[e:X]->()`).

- **Decision #11 strengthened from "convention" to "type-system invariant"**. The dot is not a valid `EdgeType` literal in `mcp_v2.EdgeType`, so `_NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(["DECLARES.DECLARES_CLIENT"])` raises a Pydantic `ValidationError` before any Cypher executes. v1 framed this as "agents shouldn't pass composed keys to neighbors"; v2 makes it impossible. Validated against `mcp_v2.py:17-31`.

- **New decision #15 — member-role / member-predicate alternative rejected**. v2 self-review considered restructuring the surface to a `member_role_summary` or `member_predicates` field instead of composed edge keys. Rejected on two grounds:
  - **No method-level role enum exists.** Roles (`CONTROLLER`, `SERVICE`, `CLIENT`, …) live on types, not on methods (per `ast_java.py:ROLE_ANNOTATIONS`). A method "is a client" only in the sense that it has an outgoing `DECLARES_CLIENT` edge.
  - **The predicate "does this class have clients?" is already answered by `count > 0`** on the composed key. A separate predicate field would duplicate information without adding affordance.

- **New principle #3 wording**: previously "Naming makes the synthesis obvious"; now "Naming names the composition." The old wording promised something the suffix didn't deliver; the new wording matches what the dot syntax actually does.

- **AGENT-GUIDE paragraph (§3.3) extended with edge-count vs method-count note**. Self-review surfaced that `DECLARES.DECLARES_CLIENT: {out: 5}` on a class with 3 client-methods is a coherent (and correct) reading when those 3 methods declare 5 `Client` rows between them. v1 did not flag this; v2 calls it out so agents and operators read the count correctly.

- **New §8 risk row**: "Operators read the count as method-count." Mitigated by the §3.3 AGENT-GUIDE note above.

- **§5 NOT-do row added**: "Restructure to a member-role / member-predicate field." Documents the v2 self-review rejection so reviewers don't relitigate.

- **Decision count: 14 → 15** (added #15). Principle count unchanged (8). Risk count: 5 → 6 (added the operator-misreading row). UC count unchanged (15).

- **Status**: `draft` → `under review (v2)`.

### v2 → v2.1: pressure-test revealed three defects

A second self-review pass on v2 — reading the doc against the actual code in `ast_java.py` and `build_ast_graph.py` rather than against my own reasoning — surfaced three real defects. v2.1 fixes them surgically without re-architecting.

**Defect 1: §1 frame overstated Java's role.** v2 said *"methods are an implementation detail of how Java forces them to express it."* This is false. `@CodebaseClient` lands on methods by **deliberate operator design** — each method represents one outbound call with its own target / path / client_kind. The granularity is meaningful, not a Java-syntax workaround. v2.1 reframes the rollup as **scope translation** (class-grain affordance for a method-grain truth), not recovery of class-level intent that Java hid.

**Defect 2: UC13's reasoning was wrong.** v2 said nested types are *"walked when the agent describes them directly"* because the composition is *"one hop deep by design."* This implies nested types are `DECLARES` children that we choose not to recurse into. They are not. `tables.members` is populated only with methods/constructors (`build_ast_graph.py:341-348`); nested types go through recursive `_register_type` (`build_ast_graph.py:350-354`) without entering `members`. So nested types are **never** `DECLARES` children. v2.1 rewrites UC13 to make the structural reason explicit, and reworded decision #7 accordingly — there is no "recursion choice" to make because the graph already excludes nested types from `DECLARES`.

**Defect 3: Decision #4's generalisation claim was too broad.** v2 said the dot convention *"generalises to future composed keys."* Tested against hypothetical `EXTENDS.CALLS` / `IMPLEMENTS.EXPOSES`: those don't fit the convention because the first hop lands on a non-member Symbol, leaving the second hop ambiguous (it would need an implicit third `DECLARES` hop on the intermediate type). v2.1 narrows the claim to the `DECLARES.<method_attached_edge>` family explicitly, and notes that compositions whose first hop lands on a non-member Symbol require a separate naming decision.

**Refinement: member-predicate alternative split out and deferred (not rejected).** v2 decision #15 bundled member-role and member-predicate alternatives into one rejection. v2.1 splits them:
- Member-**role** (decision #15): genuinely rejected. No method-level role enum exists.
- Member-**predicate** (decision #16, new): deferred, not rejected. `count > 0` covers the predicate today; if agent boilerplate accumulates, a `member_predicates` field would coexist with composed counts as a clean follow-up.

The §5 NOT-do row was likewise split into two rows reflecting the role-vs-predicate distinction.

**Defensive risk row added.** §8 gains a row noting that `DECLARES`'s schema is `Symbol → Symbol` (permissive) while the implementation always emits class → method/constructor (narrow). If that emission rule ever changes, the rollup's count semantics shift silently. Plan-level mitigation: an assertion or test confirming `DECLARES` out-degree equals method/constructor count.

**Counts after v2.1**:
- Principles §2: **8** (unchanged — wording in #3 already updated in v2)
- Use cases §4: **15** (unchanged — UC13 wording rewritten, count unchanged)
- Decisions §7: **16** (was 15; split member-role/predicate into #15 + #16)
- Risks §8: **7** (was 6; added the `DECLARES` schema-permissiveness row)
- §5 NOT-do rows: split member-role/predicate into 2 rows
- Status: `under review (v2)` → `under review (v2.1)`
