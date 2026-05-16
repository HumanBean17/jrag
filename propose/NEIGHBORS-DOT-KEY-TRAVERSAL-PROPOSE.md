# NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE

## Status

Proposal — not yet implemented.

Addresses [#162](https://github.com/HumanBean17/java-codebase-rag/issues/162).

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
| `attrs` | Terminal edge attributes (confidence, strategy) plus `via_id` — the intermediate method Symbol id |

`edge_type` echoes the dot-key rather than the bare terminal label to avoid
implying a direct single-hop edge that does not exist from the origin node.
`via_id` in `attrs` identifies the intermediate method, letting the agent
trace back to the declaring member without extra tool calls.

### Direction constraint

v1 supports **outbound only** (`direction="out"`). The `edge_summary`
dot-keys only carry `out` counts today, and the inbound reverse path
(e.g. "which class declared this client?") is already navigable via
`neighbors(client_id, 'in', ['DECLARES_CLIENT'])` → method, then
`neighbors(method_id, 'in', ['DECLARES'])` → class.

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
`count(e)`:

```cypher
MATCH (t:Symbol {id: $id})-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->(c:Client)
RETURN m.id AS via_id, label(e) AS edge_type,
       c.id AS other_id, e.confidence AS confidence, e.strategy AS strategy
```

Single query per origin per dot-key — no N+1 fan-out.

## Scope

- Extend `neighbors` to accept `DECLARES.DECLARES_CLIENT`,
  `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` as edge_types
- Add `ComposedEdgeType` to `mcp_v2.py`; update validation adapter
- Add 2-hop Cypher dispatch in the `neighbors` handler
- Populate `via_id` in `Edge.attrs` for composed results
- Update `edge_summary` description on `NodeRecord` to remove the
  "do not pass them to `neighbors(edge_types=…)`" prohibition for
  `DECLARES.*` keys (keep it for `OVERRIDDEN_BY.*` keys)
- Update `docs/EDGE-NAVIGATION.md` typical-traversals to mention
  the single-call dot-key alternative
- Update README tool table `neighbors` description

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
- **Regression**: existing `neighbors` calls with flat edge_types unchanged

## Open Questions ([TBD])

1. Should `edge_type` in the result echo the dot-key or the bare terminal
   label?
   — Recommended: **dot-key** (`DECLARES.DECLARES_CLIENT`). Avoids
   implying a direct edge from the origin that does not exist.

2. Should `via_id` be a top-level `Edge` field or live in `attrs`?
   — Recommended: **`attrs`**. Keeps the `Edge` model stable; `via_id`
   is only meaningful for composed traversals.

3. Should `origin_id` be the starting node (class) or the intermediate
   method?
   — Recommended: **starting node** (class). That is what the agent
   passed as input; `via_id` in attrs provides the intermediate link.

4. Should dot-keys work with `NodeFilter`?
   — Recommended: **yes**, filter applies to the terminal node (same
   semantics as flat edge_types). E.g. `filter={"microservice":"chat-core"}`
   with `DECLARES.DECLARES_CLIENT` filters the returned Clients.

## Out of scope

- `OVERRIDDEN_BY.*` dot-keys — these require describe-time signature
  matching computation, not a stored graph traversal. Deferred to
  follow-up: [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165).
- Adding capabilities or edge-presence fields to `NodeRef` (superseded
  by this approach)
- Changes to `SearchHit` model
- Inbound (`direction="in"`) dot-key traversals

## Sequencing / Follow-ups

Single implementation PR. Touches `mcp_v2.py` (model + handler),
`docs/EDGE-NAVIGATION.md`, `README.md`, and test files.

Follow-up issue for `OVERRIDDEN_BY.*` support tracked separately.
