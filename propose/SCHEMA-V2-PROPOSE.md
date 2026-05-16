# SCHEMA-V2 — edges connect the nodes the edge is about (HTTP_CALLS, ASYNC_CALLS, Producer node, canonical Edge Navigation Schema)

**Status**: draft
**Author**: Dmitriy Teriaev + Computer
**Date**: 2026-05-16

## TL;DR

- **Principle**: edges in a navigation graph connect the nodes whose data the edge is about. Bypassing a half-modeled node is the bug shape that bit the May 16 cross-service trace.
- Apply that principle uniformly: `HTTP_CALLS` moves from `Symbol → Route` to `Client → Route`; `ASYNC_CALLS` moves from `Symbol → Route(kafka_topic)` to `Producer → Route(kafka_topic)`. A new `Producer` node mirrors `Client` (the `@CodebaseProducer` annotation already exists; the node didn't).
- Both flips are single-edge replacements. No dual-edge transition. No active users per repo rules → no migration alias.
- New `DECLARES_PRODUCER(Symbol → Producer)` parallels `DECLARES_CLIENT`. Symmetric naming, symmetric semantics.
- Introduce `EDGE_SCHEMA: dict[str, EdgeSpec]` in `java_ontology.py` covering all 11 edges (10 existing + `DECLARES_PRODUCER`). Same ontology home pattern as `FUZZY_STRATEGY_SET` (Decision §7.19 of hints-v2).
- Generate `docs/EDGE-NAVIGATION.md` from `EDGE_SCHEMA`. CI fails if hand-edited or if the kuzu DDL strings disagree with the schema. One state of truth, three consumers (DDL, hint engine, docs).
- Migration: 4 PRs. PR-A schema infra (no DDL flips yet). PR-B flips `HTTP_CALLS`. PR-C adds `Producer` node + `DECLARES_PRODUCER` + flips `ASYNC_CALLS`. PR-D wires hints v3 (kind/direction-aware empty-result hints, design lives in a separate `HINTS-V3-PROPOSE.md`).

## §1 — Frame: the principle, then the symptoms

The user-rag graph is consumed by agents and humans navigating call structure. A navigation graph passes or fails on one criterion: **if a reasonable consumer holds a node id and asks "what does this connect to?", do they get a meaningful answer, or an empty result that hides a schema accident?**

The current schema fails that test in two analogous places:

| | HTTP | Async |
|---|---|---|
| Caller-side annotation | `@CodebaseHttpClient` | `@CodebaseProducer` / `@CodebaseProducers` |
| Caller-side metadata exists? | Yes (lives on `Client` node) | Yes (lives on `AsyncProducerHint`, inlined on edge attrs) |
| Caller-side **node** exists? | Yes (`Client`) — but the call edge bypasses it | **No** (no `Producer` node at all) |
| Call edge | `HTTP_CALLS(Symbol → Route)` — bypasses `Client` | `ASYNC_CALLS(Symbol → Route)` — bypasses the missing producer |
| What an agent holding the caller-side id finds with `neighbors(id, 'out', [edge_type])` | structurally empty: edge doesn't leave the Client | structurally empty: there is no caller-side id to hold |

These are two stages of the same half-modeling bug. HTTP has the node and the edge bypasses it; async doesn't have the node at all. Both fail the navigation criterion. Both ship in v2.

The Edge Navigation Schema (`EDGE_SCHEMA` in `java_ontology.py`) makes the navigation criterion enforceable: every edge has a single canonical specification, every consumer (DDL, hint engine, docs) reads it, and a CI invariant catches drift. Future "should the edge tail be X or Y?" questions become a schema-PR conversation rather than a "why is `neighbors` empty?" debugging session.

## §2 — Design principles

1. **Edges connect the nodes whose data the edge is about.** HTTP_CALLS is about Client→Route (annotation declaration → endpoint declaration). ASYNC_CALLS is about Producer→Topic. Symbol is the *declarer*, not the *actor*; declarer-side edges go through `DECLARES_*`.
2. **One canonical schema definition.** `EDGE_SCHEMA` in `java_ontology.py` is the source of truth. DDL, hint engine, docs all read it. Hand-edited drift becomes a CI failure.
3. **Breaking changes are cheap, dual-edge transitions are expensive.** No active users means we replace, never co-exist.
4. **Hint engine is a consumer of the schema, not an author of facts.** v3 hints read `EDGE_SCHEMA` to emit kind/direction-aware empty-result hints. No hardcoded edge-shape literals in `mcp_hints.py`.
5. **Match fidelity preserves caller-side granularity.** A method with two clients (or two producers) pointing at two routes produces two edges, anchored at the two caller-side nodes, not collapsed at the Symbol.
6. **Symmetry between HTTP and async.** `Client` ↔ `Producer`. `DECLARES_CLIENT` ↔ `DECLARES_PRODUCER`. `HTTP_CALLS(Client→Route)` ↔ `ASYNC_CALLS(Producer→Route)`. Future readers learn the pattern once.
7. **Out-of-scope rigorously.** `NODE_SCHEMA`, AsyncConsumer node (there is no `@CodebaseConsumer` annotation), multi-target Clients/Producers — all deferred to follow-up issues. v2 ships exactly the symmetry above.
8. **Schema docs are generated, not written.** `docs/EDGE-NAVIGATION.md` is built by a generator. Editing it by hand is a CI failure.

## §3 — Proposed surface changes

### §3.1 — `EDGE_SCHEMA` in `java_ontology.py`

```python
from dataclasses import dataclass
from typing import Literal

NodeKind = Literal["Symbol", "Route", "Client", "Producer"]   # Producer is new in v2
Cardinality = Literal["many_to_many", "many_to_one", "one_to_many", "one_to_one"]

@dataclass(frozen=True)
class EdgeAttr:
    name: str
    kuzu_type: str   # "STRING" | "BOOLEAN" | "DOUBLE" | "INT64"
    purpose: str     # human-readable; rendered into the doc

@dataclass(frozen=True)
class EdgeSpec:
    name: str
    src: NodeKind
    dst: NodeKind
    cardinality: Cardinality
    brownfield_sourced: bool       # True if any row's `strategy` may be in FUZZY_STRATEGY_SET
    attrs: tuple[EdgeAttr, ...]
    purpose: str                   # one-sentence what-this-edge-means
    typical_traversals: tuple[str, ...]  # canonical neighbors() shapes (rendered into doc + consumed by hints)

EDGE_SCHEMA: dict[str, EdgeSpec] = {
    "EXTENDS":           EdgeSpec(...),  # Symbol → Symbol
    "IMPLEMENTS":        EdgeSpec(...),  # Symbol → Symbol
    "INJECTS":           EdgeSpec(...),  # Symbol → Symbol
    "DECLARES":          EdgeSpec(...),  # Symbol → Symbol  (type → member)
    "OVERRIDES":         EdgeSpec(...),  # Symbol → Symbol
    "CALLS":             EdgeSpec(...),  # Symbol → Symbol
    "EXPOSES":           EdgeSpec(...),  # Symbol → Route   (declaring method → endpoint)
    "DECLARES_CLIENT":   EdgeSpec(...),  # Symbol → Client
    "DECLARES_PRODUCER": EdgeSpec(...),  # Symbol → Producer   (NEW)
    "HTTP_CALLS":        EdgeSpec(...),  # Client → Route      (was Symbol → Route)
    "ASYNC_CALLS":       EdgeSpec(...),  # Producer → Route    (was Symbol → Route)
}
```

Full populated dict in Appendix A.

### §3.2 — New `Producer` node

Schema mirrors `Client`:

```sql
CREATE NODE TABLE Producer(
    id STRING, producer_kind STRING, target_topic STRING,
    topic STRING, broker STRING,
    member_fqn STRING, member_id STRING,
    microservice STRING, module STRING, filename STRING,
    start_line INT64, end_line INT64, resolved BOOLEAN, source_layer STRING,
    PRIMARY KEY(id))
```

Field choices follow `Client` 1:1 except domain-specific fields (`target_topic`, `topic`, `broker` instead of `target_service`, `path`, `method`). Same brownfield-layer hint (`source_layer`), same member back-reference (`member_fqn`, `member_id`).

### §3.3 — DDL changes in `build_ast_graph.py`

```python
# HTTP_CALLS — endpoints flip
_SCHEMA_HTTP_CALLS = (
    "CREATE REL TABLE HTTP_CALLS(FROM Client TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "method_call STRING, raw_uri STRING, match STRING)"
)

# ASYNC_CALLS — endpoints flip
_SCHEMA_ASYNC_CALLS = (
    "CREATE REL TABLE ASYNC_CALLS(FROM Producer TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "direction STRING, raw_topic STRING, match STRING)"
)

# DECLARES_PRODUCER — new
_SCHEMA_DECLARES_PRODUCER = (
    "CREATE REL TABLE DECLARES_PRODUCER(FROM Symbol TO Producer, "
    "confidence DOUBLE, strategy STRING)"
)
```

Each DDL string is asserted against `EDGE_SCHEMA[...]` in `tests/test_schema_consistency.py`. Edit DDL but forget the schema → CI failure; edit schema but forget DDL → CI failure.

### §3.4 — Pass-level wire-up

- **Pass4 (or wherever `Client` rows are built today)**: emit parallel `Producer` rows from `AsyncProducerHint` instances. Producer ids follow the same `p:<hash>` shape used today implicitly by async edges (we'll formalize the prefix in the plan).
- **Pass5/pass6**: `HttpCallRow` keys by `client_id` instead of `caller_symbol_id`. `AsyncCallRow` keys by `producer_id` instead of `caller_symbol_id`. Multiple clients (or producers) on the same method fan out at the caller-side node, not the Symbol.
- **`DECLARES_CLIENT` and `DECLARES_PRODUCER` edges** emit at the same point clients/producers are materialized, from the declaring method Symbol.

Plan-level details (exact field renames, fixture migration, edge-emit ordering) live in `plans/PLAN-SCHEMA-V2.md`, not here.

### §3.5 — `docs/EDGE-NAVIGATION.md` generator

A small script `scripts/generate_edge_navigation.py` reads `EDGE_SCHEMA` and produces `docs/EDGE-NAVIGATION.md`:

```markdown
# Edge Navigation Schema (generated from java_ontology.EDGE_SCHEMA — do not edit)

| Edge | From | To | Cardinality | Brownfield-sourced | Purpose |
|---|---|---|---|---|---|
| EXTENDS           | Symbol   | Symbol | many_to_one  | no  | class/interface extends |
| IMPLEMENTS        | Symbol   | Symbol | many_to_many | no  | … |
| DECLARES_CLIENT   | Symbol   | Client | one_to_many  | yes | … |
| DECLARES_PRODUCER | Symbol   | Producer | one_to_many | yes | … |
| HTTP_CALLS        | Client   | Route  | many_to_many | yes | resolved HTTP call from a declared client to a target route |
| ASYNC_CALLS       | Producer | Route  | many_to_many | yes | resolved async call from a declared producer to a topic |
| ...

## HTTP_CALLS
**Endpoints**: `Client → Route`
**Attributes**: `confidence: DOUBLE`, `strategy: STRING`, `method_call: STRING`, `raw_uri: STRING`, `match: STRING`
**Purpose**: …
**Typical traversals**:
- `neighbors([client_id], 'out', ['HTTP_CALLS'])` — target route(s)
- `neighbors([route_id],  'in',  ['HTTP_CALLS'])` — client callers; combine with `DECLARES_CLIENT` inbound for the declaring method
```

CI rule: `python scripts/generate_edge_navigation.py --check` returns nonzero if the committed doc doesn't match what the generator would produce.

### §3.6 — Hints v3 wire-up (preview, design lives in a separate propose)

`mcp_hints.py` consumes `EDGE_SCHEMA` to emit kind- and direction-aware empty-result hints for `neighbors`:

- Subject kind doesn't match either endpoint of any requested edge type → hint names the canonical traversal: *"`HTTP_CALLS` connects `Client → Route`; this is a `symbol`. Try `neighbors(['<id>'], 'out', ['DECLARES_CLIENT'])` to find clients declared by this method."*
- Subject kind matches but direction is wrong → hint says so.
- Subject is a Symbol with `symbol_kind in {class, interface, enum, record, annotation}` and the requested edge lives on methods → hint points at `DECLARES`-then-re-query.

Full design in `propose/HINTS-V3-PROPOSE.md` (separate). PR-D in §6 ships the implementation once both proposes land.

## §4 — Use-case re-walk

### HTTP cases

| # | Use case | Pre-v2 result | Post-v2 result |
|---|---|---|---|
| UC1 | Agent holds Client id `c:…`, asks `neighbors([c:…], 'out', ['HTTP_CALLS'])` | `[]` (impossible by schema) | one or more Route rows |
| UC2 | Agent holds class Symbol id, asks `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` | `[]` (edge lives on methods) | `[]` + v3 hint pointing at the `DECLARES`-then-re-query path |
| UC3 | Agent holds method Symbol id, asks `neighbors([method_id], 'out', ['HTTP_CALLS'])` | one or more Route rows | `[]` + v3 hint pointing at `DECLARES_CLIENT` then HTTP_CALLS via the Client |
| UC4 | Agent holds Route id, asks `neighbors([route_id], 'in', ['HTTP_CALLS'])` | Symbol caller(s) | Client caller(s); follow `DECLARES_CLIENT` inbound from each Client for the declaring method |
| UC5 | Method has two `@CodebaseHttpClient` annotations pointing at different routes | two HTTP_CALLS edges from the same Symbol; ambiguous attribution | one HTTP_CALLS edge from each Client; clean attribution |
| UC6 | Pass6 finds no matching route for a Client | no edge emitted from Symbol — Client is orphan | no edge emitted from Client — Client visibly "declared but unresolved" via empty out-edge |
| UC7 | Pass6 finds multiple matching routes (ambiguous) | multiple HTTP_CALLS edges from Symbol, each `match="ambiguous"` | multiple HTTP_CALLS edges from Client, each `match="ambiguous"` |
| UC8 | Cross-service trace: caller method → its client → target route → declaring method on other service | `(method)-HTTP_CALLS->(route)-EXPOSES<-(method)` 3-hop | `(method)-DECLARES_CLIENT->(client)-HTTP_CALLS->(route)-EXPOSES<-(method)` 4-hop |
| UC9 | `pr_analysis` query "find all routes called by this PR's changed methods" | `MATCH (s:Symbol {…})-[:HTTP_CALLS]->(r:Route)` direct | `MATCH (s:Symbol {…})-[:DECLARES_CLIENT]->(c:Client)-[:HTTP_CALLS]->(r:Route)` two-hop |
| UC10 | `describe(c:…)` `edge_summary` | `DECLARES_CLIENT in:1 out:0` — Client always orphan-on-out | `DECLARES_CLIENT in:1 out:0`, `HTTP_CALLS in:0 out:N` — Client fully described |
| UC11 | Agent navigates interface→implementing class→method→HTTP call site | works | works (HTTP_CALLS leaves Client, reached via DECLARES_CLIENT) |

### Async cases

| # | Use case | Pre-v2 result | Post-v2 result |
|---|---|---|---|
| UC12 | Agent reads a method annotated `@CodebaseProducer` and wants to know what topic it produces to | no producer-side node id to query; must read raw annotation attrs or follow `ASYNC_CALLS` from the method | `neighbors([method_id], 'out', ['DECLARES_PRODUCER'])` → Producer id; then `neighbors([producer_id], 'out', ['ASYNC_CALLS'])` → Topic |
| UC13 | Agent holds a Topic Route id, asks "who produces to me?" → `neighbors([topic_id], 'in', ['ASYNC_CALLS'])` | Symbol producer(s) | Producer caller(s); follow `DECLARES_PRODUCER` inbound for the declaring method |
| UC14 | Method has two `@CodebaseProducer` annotations producing to different topics | two ASYNC_CALLS edges from the same Symbol; ambiguous attribution | one ASYNC_CALLS edge from each Producer; clean attribution |
| UC15 | Pass6 cannot resolve a Producer to a topic (broker unknown, topic regex mismatch) | no ASYNC_CALLS edge emitted from Symbol — producer hint dies on the edge that never existed | Producer node exists with empty out-edge — visibly "declared but unresolved" |
| UC16 | Cross-service async trace: producer method → its producer → topic → consumer method | `(method)-ASYNC_CALLS->(topic)<-EXPOSES-(consumer_method)` 3-hop | `(method)-DECLARES_PRODUCER->(producer)-ASYNC_CALLS->(topic)<-EXPOSES-(consumer_method)` 4-hop |
| UC17 | Agent asks `neighbors([producer_id], 'in', ['ASYNC_CALLS'])` | n/a (no producer node existed) | `[]` + v3 hint "ASYNC_CALLS arrives at Route; for callers of this Producer, use `DECLARES_PRODUCER` inbound" |
| UC18 | A method has both `@CodebaseHttpClient` and `@CodebaseProducer` (synchronous side-effect + async event) | two edges from the same Symbol mixed at one node | one HTTP_CALLS via Client, one ASYNC_CALLS via Producer — channels cleanly separated |

### Schema-infrastructure cases

| # | Use case | Pre-v2 result | Post-v2 result |
|---|---|---|---|
| UC19 | `EDGE_SCHEMA` reader asks "what edges target Route?" | not directly answerable from one place | `[e for e in EDGE_SCHEMA.values() if e.dst == "Route"]` → `{EXPOSES, HTTP_CALLS, ASYNC_CALLS}` |
| UC20 | A new edge `INHERITS_FROM_FRAMEWORK` is added; contributor forgets to update docs | docs go stale silently | CI fails: generator output differs from committed doc |
| UC21 | Contributor edits DDL `HTTP_CALLS(FROM Symbol TO Route)` reverting the v2 flip | builds and ships | CI fails: parsed DDL disagrees with `EDGE_SCHEMA["HTTP_CALLS"].src` |
| UC22 | Hints v3 needs the canonical traversal for an empty result on `neighbors([class_id], 'out', ['HTTP_CALLS'])` | hardcoded edge-shape literal in `mcp_hints.py` | reads `EDGE_SCHEMA["HTTP_CALLS"]` → subject Symbol is not `Client`; emits "wrong subject kind" hint with canonical traversal from `typical_traversals` |
| UC23 | Build-time schema change: someone adds attribute `tracing_propagated: BOOLEAN` to HTTP_CALLS | DDL and doc drift independently | adds `EdgeAttr` to `EDGE_SCHEMA["HTTP_CALLS"].attrs`, DDL regenerated/checked, doc regenerated, hints adopt automatically |

### Awkward cases surfaced

- **UC8 and UC16** are now 4-hop traversals instead of 3 for cross-service trace assembly. The extra hop is meaningful (it names the Client / Producer) but agents will run one extra `neighbors` call per cross-service hop. Mitigation: `member_fqn`/`member_id` cached on Client/Producer `data` already supports client-side join; we may add a composite-traversal hint in v3.
- **UC9** is now two-hop in Cypher. PR-B's description enumerates every site.
- **UC18** is the cleanest argument for the symmetric design — if HTTP and async didn't share the same `Symbol → DECLARES_X → CallerNode → Edge → Route` pattern, a method using both would have asymmetric navigation.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add a `Consumer` node mirroring `Producer` | The callee side is already symmetric for HTTP and async: `@GetMapping` and `@KafkaListener` both annotate methods, and the listener method is the Route's `method_fqn` reachable via `EXPOSES(Symbol → Route)` (see `ast_java.py:2169`). The `Producer` node exists because `kafkaTemplate.send(...)` is a call *expression* inside an arbitrary method body — it has no first-class identity to carry topic/target metadata. Listener methods do, so no extra node is needed. The `http_consumer` route kind in `build_ast_graph.py` is pass6 internal Feign caller synthesis, a different concept. See Decision 21. |
| Soft-migration alias `(s:Symbol)-[:HTTP_CALLS_LEGACY]->(r:Route)` | No active users (per repo rules). |
| Add a `Member`/`Method` node distinct from `Symbol` | Out of scope. `Symbol.symbol_kind` already discriminates; v3 hints exploit this. |
| Bake `EDGE_SCHEMA` into kuzu DDL via codegen | Step too far for v2; manual DDL with CI check is enough. Codegen later if churn justifies. |
| Extend to `NODE_SCHEMA` | Useful, separate propose. Edges are the immediate pain. |
| Multi-target Clients / Producers (one node representing multiple endpoints/topics) | Out of scope; current model is 1 caller-side node = 1 declared call site. |
| Convenience composite-edge views (`SYMBOL_HTTP_CALLS` materialized) | Out of scope. Two-hop Cypher is fine. Reconsider in a follow-up if pain shows. |

## §6 — Migration plan — 4 PRs

### PR-A — `EDGE_SCHEMA` + doc generator + CI invariants (no DDL flips)

**Title**: `feat(schema): add EDGE_SCHEMA to java_ontology and generate docs/EDGE-NAVIGATION.md`

**Purpose**: Introduce `EdgeSpec`/`EdgeAttr` dataclasses and the populated `EDGE_SCHEMA` dict reflecting the **current** schema (HTTP_CALLS still Symbol→Route, ASYNC_CALLS still Symbol→Route, no Producer node). Add `scripts/generate_edge_navigation.py` and check it in CI. Add `tests/test_schema_consistency.py` asserting the DDL strings in `build_ast_graph.py` agree with `EDGE_SCHEMA`. This PR ships schema-as-source-of-truth without flipping anything.

**Test summary**: named scenarios in `tests/test_schema_consistency.py` covering DDL↔ontology round-trip for all 10 current edges; named scenarios in `tests/test_edge_navigation_doc.py` covering generator output stability and `--check` mode.

### PR-B — flip `HTTP_CALLS` endpoints

**Title**: `feat(schema): HTTP_CALLS originates from Client, not Symbol`

**Purpose**: Update `EDGE_SCHEMA["HTTP_CALLS"]` to `Client → Route`; update DDL; update pass6 to emit edges from Client ids; rewrite all callers in `kuzu_queries.py`, `pr_analysis.py`, `mcp_v2.py`, `server.py`; update HTTP-flavored tests in `test_call_edges_e2e.py`, `test_brownfield_clients.py`, `test_pr_analysis.py`, `test_mcp_v2.py`, `test_mcp_v2_compose.py`.

**Test summary**: round-trip scenarios in `test_call_edges_e2e.py` covering UC1, UC5–UC8; `test_pr_analysis.py` covers UC9; `test_brownfield_clients.py` covers `target_service`-empty path-only matching.

### PR-C — `Producer` node + `DECLARES_PRODUCER` + flip `ASYNC_CALLS`

**Title**: `feat(schema): introduce Producer node and route ASYNC_CALLS through it`

**Purpose**: Add `Producer` node table; add `DECLARES_PRODUCER(Symbol → Producer)` edge; update `EDGE_SCHEMA` (`DECLARES_PRODUCER` new, `ASYNC_CALLS` flips to `Producer → Route`); materialize Producer rows in the pass that builds Client rows today; rewrite async paths in pass5/pass6 and all callers; update tests in `test_call_edges_e2e.py` and any async-specific tests.

**Test summary**: round-trip scenarios in `test_call_edges_e2e.py` covering UC12–UC18; producer-side parallels of the HTTP brownfield resolver tests; UC18 (mixed HTTP + async on the same method).

### PR-D — hints v3 (kind/direction-aware empty-result hints)

**Title**: `feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA`

**Purpose**: Replace `TPL_NEIGHBORS_EMPTY_KIND_CHECK` (generic) with a family of templates driven by `EDGE_SCHEMA`. Detailed surface in `propose/HINTS-V3-PROPOSE.md` (separate propose); this PR lands after PR-A is merged so the hint engine can consume `EDGE_SCHEMA`.

**Test summary**: named scenarios in `tests/test_mcp_hints.py` covering UC2, UC3, UC10, UC15, UC17, UC22; v2-regression scenario asserting fuzzy-strategy hint still fires.

## §7 — Decisions taken (no longer open)

1. **HTTP_CALLS endpoints are `Client → Route`.** Single edge, single shape.
2. **ASYNC_CALLS endpoints are `Producer → Route`.** Symmetric with HTTP.
3. **`Producer` node is added in v2** with field shape mirroring `Client`.
4. **`DECLARES_PRODUCER(Symbol → Producer)`** parallels `DECLARES_CLIENT(Symbol → Client)`. Symmetric naming, symmetric semantics.
5. **No dual-edge transition.** Symbol→Route HTTP_CALLS and Symbol→Route ASYNC_CALLS are removed in their respective PRs; no deprecation aliases.
6. **`EDGE_SCHEMA: dict[str, EdgeSpec]` lives in `java_ontology.py`.** Single canonical home, consistent with §7.19 of hints-v2.
7. **`docs/EDGE-NAVIGATION.md` is generated, not hand-written.** Edit-by-hand is a CI failure.
8. **DDL strings are asserted against `EDGE_SCHEMA`.** Mismatch is a CI failure.
9. **`EdgeSpec.brownfield_sourced: bool`.** True iff any row may carry a `strategy` in `FUZZY_STRATEGY_SET` (Decision §7.19 of hints-v2). Drives v3 hint logic.
10. **Cardinality is informational, not a kuzu constraint.** kuzu doesn't enforce cardinality; the field documents intent and may inform future invariants.
11. **`typical_traversals` are rendered into both doc and hint engine.** Source of truth for "what's the right way to traverse this edge."
12. **`@CodebaseConsumer` is out of scope.** No such annotation exists today; if one is added, it's a separate propose.
13. **Multi-client / multi-producer methods fan out at the caller-side node.** No Symbol-level collapsing.
14. **Pass keys `HttpCallRow` by `client_id` and `AsyncCallRow` by `producer_id`.** Plan-level details in `plans/PLAN-SCHEMA-V2.md`.
15. **Caller queries become two-hop for both HTTP and async.** `MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[:HTTP_CALLS]->(r:Route)` and `MATCH (s:Symbol)-[:DECLARES_PRODUCER]->(p:Producer)-[:ASYNC_CALLS]->(r:Route)`. No convenience view in v2.
16. **`describe(c:…)` and `describe(p:…)` `edge_summary` now show non-zero out-edges.** No `describe` code change; data just becomes accurate.
17. **Hints v3 (PR-D) is gated on PR-A landing.** If PR-A reverts, PR-D reverts.
18. **Test scope discipline.** Schema consistency tests in `tests/test_schema_consistency.py`. Doc generator tests in `tests/test_edge_navigation_doc.py`. Edge-flip tests stay in the existing call-edge test files.
19. **`EDGE_SCHEMA` is locked at 11 entries in v2.** Adding/removing entries is a propose-level decision, not a PR.
20. **PR ordering: A → B → C → D.** B and C are independent in principle (different edges) but C builds on A's schema infrastructure; D consumes both. Sequential reduces review surface.
21. **No `Consumer` node — the callee-side asymmetry is real and deliberate.** `Producer` exists because `kafkaTemplate.send(...)` is a call expression inside an arbitrary method body, with no first-class identity to hang topic/target metadata on. The callee side has no equivalent problem: `@KafkaListener` annotates a method, and that method already serves as the Route's `method_fqn` with `EXPOSES(Symbol → Route)` — exactly mirroring how `@GetMapping` methods expose `http_endpoint` Routes. Splitting the listener into `Consumer` + Symbol would duplicate identity without unlocking any navigation primitive. The cross-service trace shapes in UC8 and UC16 already terminate cleanly at `EXPOSES <- (consumer_method)` for both HTTP and async.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Cross-service trace assembly is one hop longer everywhere (HTTP and async) | UC8 and UC16 walk the new shape; if this becomes a frequent agent struggle, a composite-traversal hint or materialized view ships in a separate propose. |
| Cypher rewrite misses a call site | PR-B / PR-C reviewed against `grep -rn "HTTP_CALLS\|ASYNC_CALLS" --include="*.py"` output; every match enumerated in the PR description. |
| `EDGE_SCHEMA`↔DDL invariant brittle (false-positive CI failures during legitimate edits) | The invariant compares parsed `(src, dst)` only; attribute changes don't trip it. Downgrade to warning in a follow-up if noise-to-signal is bad. |
| Doc generator output churns on cosmetic changes (key ordering) | Generator emits keys in `EDGE_SCHEMA` declaration order, not dict-iteration order; tests pin output. |
| Producer node design diverges from Client because we copy-pasted before the second annotation type surfaced real differences | Producer fields are reviewed in PR-C against actual `AsyncProducerHint` data, not assumed-symmetric. Open follow-up if a real-world async case needs a field Client doesn't have. |
| `@CodebaseConsumer` lands later and the symmetry argument forces a third node | Cheap to add a `Consumer` node + `DECLARES_CONSUMER` edge alongside; the EDGE_SCHEMA infrastructure absorbs the change with no migration. The current asymmetry is deliberate (Decision 21), not an oversight. |
| `target_service` empty path-only matching produces too many false-positive cross-service edges under the new shape | Same matching logic as today (`build_ast_graph.py:1812–1875`); risk profile unchanged. Client-anchored edge surfaces ambiguity more visibly, which is an improvement. |

## Appendix A — `EDGE_SCHEMA` populated form (sketch)

```python
EDGE_SCHEMA = {
    "EXTENDS": EdgeSpec(
        name="EXTENDS",
        src="Symbol", dst="Symbol",
        cardinality="many_to_one",
        brownfield_sourced=False,
        attrs=(
            EdgeAttr("dst_name", "STRING", "raw supertype name as written in source"),
            EdgeAttr("dst_fqn",  "STRING", "best-effort resolved FQN of the supertype"),
            EdgeAttr("resolved", "BOOLEAN", "True iff dst_fqn was resolved to an in-graph Symbol"),
        ),
        purpose="class/interface direct supertype relation",
        typical_traversals=(
            "neighbors([symbol_id], 'out', ['EXTENDS'])  # supertype",
            "neighbors([symbol_id], 'in',  ['EXTENDS'])  # direct subtypes",
        ),
    ),
    "IMPLEMENTS":        EdgeSpec(..., src="Symbol",   dst="Symbol",   cardinality="many_to_many", brownfield_sourced=False, ...),
    "INJECTS":           EdgeSpec(..., src="Symbol",   dst="Symbol",   cardinality="many_to_many", brownfield_sourced=False, ...),
    "DECLARES":          EdgeSpec(..., src="Symbol",   dst="Symbol",   cardinality="one_to_many",  brownfield_sourced=False, ...),
    "OVERRIDES":         EdgeSpec(..., src="Symbol",   dst="Symbol",   cardinality="many_to_one",  brownfield_sourced=False, ...),
    "CALLS":             EdgeSpec(..., src="Symbol",   dst="Symbol",   cardinality="many_to_many", brownfield_sourced=True,  ...),
    "EXPOSES":           EdgeSpec(..., src="Symbol",   dst="Route",    cardinality="one_to_one",   brownfield_sourced=True,  ...),
    "DECLARES_CLIENT":   EdgeSpec(..., src="Symbol",   dst="Client",   cardinality="one_to_many",  brownfield_sourced=True,  ...),
    "DECLARES_PRODUCER": EdgeSpec(..., src="Symbol",   dst="Producer", cardinality="one_to_many",  brownfield_sourced=True,  ...),
    "HTTP_CALLS":  EdgeSpec(
        name="HTTP_CALLS",
        src="Client", dst="Route",   # <-- v2 change
        cardinality="many_to_many",
        brownfield_sourced=True,
        attrs=(
            EdgeAttr("confidence",  "DOUBLE", "pass6 match confidence in [0.0, 1.0]"),
            EdgeAttr("strategy",    "STRING", "match strategy literal (FUZZY_STRATEGY_SET or primary)"),
            EdgeAttr("method_call", "STRING", "HTTP method of the call site"),
            EdgeAttr("raw_uri",     "STRING", "uninterpolated URI template from the annotation"),
            EdgeAttr("match",       "STRING", "exact|ambiguous|phantom — pass6 outcome literal"),
        ),
        purpose="resolved HTTP call from a declared client to a target route",
        typical_traversals=(
            "neighbors([client_id], 'out', ['HTTP_CALLS'])  # target route(s)",
            "neighbors([route_id],  'in',  ['HTTP_CALLS'])  # client callers; combine with DECLARES_CLIENT inbound for the declaring method",
        ),
    ),
    "ASYNC_CALLS": EdgeSpec(
        name="ASYNC_CALLS",
        src="Producer", dst="Route",  # <-- v2 change
        cardinality="many_to_many",
        brownfield_sourced=True,
        attrs=(
            EdgeAttr("confidence", "DOUBLE", "pass6 match confidence in [0.0, 1.0]"),
            EdgeAttr("strategy",   "STRING", "match strategy literal"),
            EdgeAttr("direction",  "STRING", "produce|consume — async edge direction literal"),
            EdgeAttr("raw_topic",  "STRING", "uninterpolated topic template from the annotation"),
            EdgeAttr("match",      "STRING", "exact|ambiguous|phantom — pass6 outcome literal"),
        ),
        purpose="resolved async call from a declared producer to a topic route",
        typical_traversals=(
            "neighbors([producer_id], 'out', ['ASYNC_CALLS'])  # target topic(s)",
            "neighbors([route_id],    'in',  ['ASYNC_CALLS'])  # producer callers; combine with DECLARES_PRODUCER inbound for the declaring method",
        ),
    ),
}
```

Full population (with every attribute) lives in `java_ontology.py` after PR-A.

## Appendix B — What changed (traceability)

- **First-draft framing (HTTP-only)**: SCHEMA-V2 originally scoped to flipping HTTP_CALLS only; async-side was deferred to a follow-up issue on the grounds that "no AsyncClient analog node exists."
- **Revision (reviewer pushback)**: the reviewer correctly noted `@CodebaseProducer` already exists in the annotation set (`ast_java.py:180`) and produces metadata that is currently lost on the edge — the same half-modeling bug as HTTP, one stage earlier. Scoping out async would have shipped a state-of-truth document that institutionalized the asymmetry it was meant to prevent.
- **What changed**: §1 reframed around the principle (edges connect the nodes the edge is about), with HTTP and async as two symptoms of one bug. Added `Producer` node, `DECLARES_PRODUCER` edge, ASYNC_CALLS flip. UCs expanded from 17 → 23. Decisions from 16 → 20. PRs from 3 → 4. `EDGE_SCHEMA` entries from 10 → 11.
- **Second-round grilling (Consumer-node question)**: reviewer asked whether the caller-side asymmetry (`Client`/`Producer` distinct, callee-side both `Symbol`) was itself a bug. Investigation confirmed it is not: `@KafkaListener` methods are already the Route's `method_fqn` and connect via `EXPOSES`, exactly mirroring `@GetMapping` methods. The `Producer` node exists only because `kafkaTemplate.send(...)` is a call expression with no first-class method identity — listener methods don't have that problem. Decision 21 locks this asymmetry as deliberate; §5 out-of-scope row was rewritten from "no annotation yet" to "no navigation gap to fill."
- **What stayed**: `EDGE_SCHEMA` home in `java_ontology.py`, generated doc with CI enforcement, DDL↔ontology invariant, no soft-migration aliases, hints v3 as a separate propose gated on PR-A. PR count, UC count, edge count unchanged after the second-round grilling.
