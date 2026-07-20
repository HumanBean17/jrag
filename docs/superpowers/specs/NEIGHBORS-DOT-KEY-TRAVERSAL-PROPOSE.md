<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE

## Status

**Landed** — PR [#171](https://github.com/HumanBean17/java-codebase-rag/pull/171) (PR-1).

Addresses [#162](https://github.com/HumanBean17/java-codebase-rag/issues/162)
(partially — see [Limitations](#limitations)).

## Decision reversal

This proposal **deliberately reverses decision #11** from
[`DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE`](./DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md)
(PR-89), which made composed dot-keys read-only by construction —
Pydantic `EdgeType` rejected them at the type-system level, and
`AGENT-GUIDE.md` documented them as hop affordances only.

**Why reverse now:** In practice, the "read-only" invariant created a
discoverability dead-end. `edge_summary` shows the agent that a 2-hop
path exists, then tells it the only way to traverse it is to manually
decompose the path into individual hops — the exact multi-call workflow
the rollup was meant to shortcut. The surface was not obvious enough
for agents (which tend to take dot-keys literally and try to pass them)
or for humans reading the guide. Making `DECLARES.*` dot-keys navigable
closes the loop that the rollup opened.

**Scope of reversal:** only the `DECLARES.*` family (3 stored 2-hop
paths). `OVERRIDDEN_BY.*` keys remain describe-only — they require
signature-matching computation, not stored edge traversal, and reversing
their read-only status is a separate design question
([#165](https://github.com/HumanBean17/java-codebase-rag/issues/165)).

Supersedes:
- Decision #11 in `DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md`
- `AGENT-GUIDE.md` guidance that composed dot-keys are "not valid
  `EdgeType` literals"
- `test_neighbors_rejects_overridden_by_and_dot_keys` (must be split:
  accept `DECLARES.*`, still reject `OVERRIDDEN_BY.*`)
- `mcp_hints.py` templates `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` /
  `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` (prescribe the old multi-hop
  recipe; must be updated to the single-call dot-key alternative)

## Problem Statement

When `neighbors` or `find` returns a batch of method `NodeRef`s, the agent
has no signal about which methods declare clients, producers, or expose
routes. The `NodeRef` schema carries only structural identity fields — no
edge-presence indicators.

Consider a typical workflow:

1. `search("payment processing")` → class FQN
2. `neighbors([class_id], 'out', ['DECLARES'])` → 15 method `NodeRef`s
3. Agent must decide which methods are interesting for cross-service tracing

At step 3 the agent must either call `describe` on all 15 methods (15 tool
calls), guess by name (unreliable), or hope it already described the class
and noticed `edge_summary` dot-keys — which it may not have done if it
arrived at the class via `find` or `neighbors` from another node.

The `describe` tool already surfaces the answer: `edge_summary` includes
composed dot-keys like `DECLARES.DECLARES_CLIENT: {"out": 2}` for type
Symbols. But `neighbors` rejects those dot-keys today:

> *"do not pass them to `neighbors(edge_types=…)`"*

This creates a discoverability dead-end: `edge_summary` shows a 2-hop path
exists, but the agent cannot act on it without decomposing the path into
individual hops manually.

## Proposed Solution

Make `neighbors` accept the `DECLARES.*` composed dot-key family as valid
`edge_types` values. When a dot-key is requested, `neighbors` executes a
single 2-hop Cypher query and returns the terminal edges/nodes directly.

**Principle: what you see in `edge_summary` is what you can request in
`neighbors`.**

### Supported dot-keys (v1)

| Dot-key | Graph path | Terminal node |
|---|---|---|
| `DECLARES.DECLARES_CLIENT` | `Symbol -[:DECLARES]-> Symbol -[:DECLARES_CLIENT]-> Client` | `Client` |
| `DECLARES.DECLARES_PRODUCER` | `Symbol -[:DECLARES]-> Symbol -[:DECLARES_PRODUCER]-> Producer` | `Producer` |
| `DECLARES.EXPOSES` | `Symbol -[:DECLARES]-> Symbol -[:EXPOSES]-> Route` | `Route` |

### Agent workflow with dot-keys (3 tool calls)

```
search("payment processing")  → class FQN
describe(class_id)             → edge_summary includes DECLARES.DECLARES_CLIENT: {out: 2}
neighbors(class_id, 'out', ['DECLARES.DECLARES_CLIENT'])  → 2 Client NodeRefs directly
```

Down from 17+ tool calls to 3.

### Edge result shape

Each returned `Edge` for a dot-key traversal:

| Field | Value |
|---|---|
| `origin_id` | The starting node passed by the agent (the class) |
| `edge_type` | The dot-key: `DECLARES.DECLARES_CLIENT` |
| `direction` | `out` |
| `other` | Terminal node `NodeRef` (Client / Producer / Route) |
| `attrs` | Terminal edge attributes (confidence, strategy, plus all other attrs that flat `neighbors` projects — see [Cypher attrs](#cypher-implementation)) plus `via_id` — the intermediate method Symbol id |

`edge_type` echoes the dot-key rather than the bare terminal label to avoid
implying a direct single-hop edge that does not exist from the origin node.
`via_id` in `attrs` identifies the intermediate method, letting the agent
trace back to the declaring member without extra tool calls.

### Origin kind constraint

v1 dot-keys require the origin to be a **type** Symbol
(`kind ∈ {class, interface, enum, record, annotation}`). This matches the
scope of `member_edge_rollup_for`, which only computes dot-key counts for
type Symbols.

If a method, route, client, or producer id is passed with a dot-key
`edge_type`, `neighbors` returns `success=False` with a validation error:
`"Composed edge types (DECLARES.DECLARES_CLIENT) require a type Symbol origin"`.

### Direction constraint

v1 supports **outbound only** (`direction="out"`). The `edge_summary`
dot-keys only carry `out` counts today, and the inbound reverse path
(e.g. "which class declared this client?") is already navigable via
`neighbors(client_id, 'in', ['DECLARES_CLIENT'])` → method, then
`neighbors(method_id, 'in', ['DECLARES'])` → class.

`direction="in"` with a dot-key returns `success=False` with a clear error.

### EdgeType validation

The current `EdgeType` Literal covers stored graph labels. Dot-keys are
composed navigation paths, not graph labels.

Add a `ComposedEdgeType` Literal for the three dot-keys and accept
`EdgeType | ComposedEdgeType` in the `neighbors` edge_types parameter.
`_NEIGHBOR_EDGE_TYPES_ADAPTER` validation is updated accordingly. Flat and
composed types may be mixed in one call (they resolve independently).

### Cypher implementation

The 2-hop queries mirror what `member_edge_rollup_for` already executes in
`kuzu_queries.py`, but `RETURN` the target node columns instead of
`count(e)`.

The projected columns on the terminal edge `e` must match the same attr set
that flat `neighbors_v2` projects: `confidence`, `strategy`, `match`,
`mechanism`, `annotation`, `field_or_param`, `source`, `call_site_line`,
`call_site_byte`, `arg_count`, `resolved`. Most will be `NULL` for a given
edge type (e.g. `DECLARES_CLIENT` only carries `confidence` and `strategy`),
but projecting the full set keeps the `attrs` contract uniform.

```cypher
MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->(c:Client)
RETURN m.id AS via_id, label(e) AS edge_type,
       c.id AS other_id, e.confidence AS confidence, e.strategy AS strategy,
       e.match AS match, e.mechanism AS mechanism, e.annotation AS annotation,
       e.field_or_param AS field_or_param, e.source AS source,
       e.call_site_line AS call_site_line, e.call_site_byte AS call_site_byte,
       e.arg_count AS arg_count, e.resolved AS resolved
```

Single query per origin per dot-key — no N+1 fan-out.

### Counting semantics alignment

Unfiltered `len(neighbors(..., ['DECLARES.DECLARES_CLIENT']))` must equal
`edge_summary["DECLARES.DECLARES_CLIENT"]["out"]` for the same origin
(assuming no `limit`/`offset` truncation). Both count **edge rows**, not
distinct methods — one method with multiple `Client` rows contributes its
full edge count.

Duplicate `other.id` values with different `via_id` values are expected
(rare but possible if two methods declare clients pointing to the same
`Client` node).

`limit`/`offset` apply to the combined result set, so an empty page with
non-zero `edge_summary` count is possible.

## Scope

- Extend `neighbors` to accept `DECLARES.DECLARES_CLIENT`,
  `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` as edge_types
- Add `ComposedEdgeType` to `mcp_v2.py`; update validation adapter
- Add 2-hop Cypher dispatch in the `neighbors` handler
- Add origin kind validation (type Symbol required for dot-keys)
- Populate `via_id` in `Edge.attrs` for composed results
- Update `edge_summary` description on `NodeRecord` to remove the
  "do not pass them to `neighbors(edge_types=…)`" prohibition for
  `DECLARES.*` keys (keep it for `OVERRIDDEN_BY.*` keys)
- Update `docs/EDGE-NAVIGATION.md` typical-traversals to mention
  the single-call dot-key alternative
- Update `docs/AGENT-GUIDE.md` — rewrite the "composed keys are read-only"
  paragraphs; `DECLARES.*` keys become navigable, `OVERRIDDEN_BY.*` stays
  describe-only
- Update `mcp_hints.py` — `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` and
  `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` must prescribe the dot-key
  single-call recipe instead of the old multi-hop walk
- Update `server.py` — `describe` and `neighbors` tool `description=`
  strings (MCP contract surface)
- Update README tool table `neighbors` description
- Split `test_neighbors_rejects_overridden_by_and_dot_keys`: accept
  `DECLARES.*`, still reject `OVERRIDDEN_BY.*`

## Schema / Ontology / Re-index impact

- Ontology bump: **not required** (no graph schema or enrichment change)
- Re-index required: **no** (reads existing graph data via new query paths)
- Config/tool surface changes: `neighbors` accepts 3 new `edge_types`
  values; `Edge` results for those types include `via_id` in `attrs`

## Tests / Validation

- **Unit**: `test_neighbors_declares_dot_key_client` — from a type Symbol
  with known `DECLARES.DECLARES_CLIENT` count, verify correct terminal
  Client nodes returned with `via_id` attrs
- **Unit**: `test_neighbors_declares_dot_key_producer` — same for
  `DECLARES.DECLARES_PRODUCER`
- **Unit**: `test_neighbors_declares_dot_key_exposes` — same for
  `DECLARES.EXPOSES`
- **Unit**: `test_neighbors_dot_key_mixed_with_flat` — mixed
  `["DECLARES", "DECLARES.DECLARES_CLIENT"]` call returns both member
  Symbols and terminal Clients
- **Unit**: `test_neighbors_dot_key_inbound_rejected` — `direction="in"`
  with a dot-key returns a clear error message
- **Unit**: `test_neighbors_dot_key_method_origin_rejected` — method id
  with a dot-key returns a validation error
- **Unit**: `test_neighbors_dot_key_count_matches_edge_summary` — verify
  unfiltered result count equals `edge_summary` out count for same origin
- **Unit**: `test_neighbors_still_rejects_overridden_by` — `OVERRIDDEN_BY`
  and `OVERRIDDEN_BY.*` dot-keys remain rejected
- **Hint**: verify updated hint templates prescribe dot-key recipe
- **Regression**: existing `neighbors` calls with flat edge_types unchanged

## Decisions (locked)

1. `edge_type` in the result echoes the **dot-key**
   (`DECLARES.DECLARES_CLIENT`), not the bare terminal label. Avoids
   implying a direct edge from the origin that does not exist.

2. `via_id` lives in **`attrs`**, not as a top-level `Edge` field. Keeps
   the `Edge` model stable; `via_id` is only meaningful for composed
   traversals.

3. `origin_id` is the **starting node** (class) — what the agent passed
   as input. `via_id` in attrs provides the intermediate link.

4. `NodeFilter` **applies to the terminal node** (same semantics as flat
   edge_types). E.g. `filter={"microservice":"chat-core"}` with
   `DECLARES.DECLARES_CLIENT` filters the returned Clients.

## Limitations

This proposal solves class-level bulk enumeration: from a type Symbol,
one `neighbors` call retrieves all terminal Client/Producer/Route nodes,
with `via_id` linking back to the declaring method.

It does **not** add per-method edge-presence signals on method `NodeRef`s
returned by `neighbors(..., ['DECLARES'])` or `find(kind="symbol")`. An
agent that receives a list of 15 method `NodeRef`s still cannot tell which
ones are interesting without either (a) calling `describe` on the parent
class first, or (b) describing individual methods.

If method-list filtering (without a prior `describe`) proves to be a pain
point, a future proposal could add lightweight signals (e.g. `capabilities`)
to `NodeRef` for symbol-kind nodes. Tracked as follow-up:
[#167](https://github.com/HumanBean17/java-codebase-rag/issues/167).

## Out of scope

- `OVERRIDDEN_BY.*` dot-keys — these require describe-time signature
  matching computation, not a stored graph traversal. Deferred to
  follow-up: [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165).
- Per-method `NodeRef` signals (capabilities / edge-presence fields) —
  separate concern, tracked as follow-up (see [Limitations](#limitations))
- Changes to `SearchHit` model
- Inbound (`direction="in"`) dot-key traversals

## Sequencing / Follow-ups

Single implementation PR. Touches `mcp_v2.py` (model + handler),
`mcp_hints.py` (hint templates), `server.py` (tool descriptions),
`docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, `README.md`, and
test files.

Follow-up issues:
- [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) —
  single source of truth for composed dot-keys (`ComposedEdgeType` drift)
- [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165) —
  `OVERRIDDEN_BY.*` dot-key support
- [#167](https://github.com/HumanBean17/java-codebase-rag/issues/167) —
  Per-method `NodeRef` edge-presence signals (if needed)
