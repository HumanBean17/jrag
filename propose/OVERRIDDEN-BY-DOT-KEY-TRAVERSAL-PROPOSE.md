# OVERRIDDEN-BY-DOT-KEY-TRAVERSAL-PROPOSE

## Status

**Draft** — in-flight under `plan/overridden-by-dot-key-traversal`.

Addresses [#165](https://github.com/HumanBean17/java-codebase-rag/issues/165).

Follow-up from [#162](https://github.com/HumanBean17/java-codebase-rag/issues/162) and
[`NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE`](./completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md)
(landed PR [#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)).

## Decision reversal (narrow)

[`NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE`](./completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md) deliberately left
`OVERRIDDEN_BY.*` describe-only because the dispatch hop is not a single stored
2-hop `DECLARES` pattern. That remains true for the **first** hop, but the graph
now materializes `[:OVERRIDES]` between method Symbols (ontology 13+), and
builder tests prove stored `neighbors(..., ['OVERRIDES'])` matches
`override_axis_rollup_for` dispatch-down ids. The composed terminal hops
(`DECLARES_CLIENT`, `DECLARES_PRODUCER`, `EXPOSES`) are stored and identical to
the member-axis pattern.

**Scope of this reversal:** only the `OVERRIDDEN_BY.*` family (4 virtual keys).
`DECLARES.*` stays as landed. Stored flat `OVERRIDES` remains a valid one-hop
`EdgeType` — dot-keys are optional shortcuts, not a replacement.

Supersedes (on landing):

- `docs/AGENT-GUIDE.md` — "OVERRIDDEN_BY* describe-only / not valid edge_types"
- `server.py` / README / `mcp_v2.py` `NodeRecord.edge_summary` text that rejects
  `OVERRIDDEN_BY*` in `neighbors`
- `mcp_hints.py` two-hop templates
  `TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS` (and producer/route siblings)
- `test_neighbors_still_rejects_overridden_by` → accept `OVERRIDDEN_BY.*`

Does **not** supersede:

- `OVERRIDES` as a stored one-hop edge (still valid alone)
- Describe-time rollup in `override_axis_rollup_for` (counts stay the source of
  truth for `edge_summary`; traversal must match)

## Problem statement

On an interface or abstract **method** Symbol, `describe` already surfaces:

```json
{
  "OVERRIDDEN_BY": {"in": 0, "out": 2},
  "OVERRIDDEN_BY.DECLARES_CLIENT": {"in": 0, "out": 3}
}
```

Agents that pass those keys to `neighbors` get a Pydantic `ValidationError`
before Cypher runs. Hints prescribe a **two-call** workaround:

```
neighbors(['{id}'],'in',['OVERRIDES'])
then neighbors(overrider_ids,'out',['DECLARES_CLIENT'])
```

That recreates the discoverability dead-end [#162] fixed for type-level
`DECLARES.*`: `edge_summary` advertises a composed path the tool rejects.

**Principle (same as #162):** what you see in `edge_summary` for override-axis
keys is what you can request in `neighbors`.

## Proposed solution

Extend `neighbors` to accept four composed override-axis dot-keys. Execute a
single Cypher query per origin per key (no N+1 fan-out). Use **stored**
`[:OVERRIDES]` for the dispatch hop so traversal stays aligned with
`override_axis_rollup_for` without duplicating `IMPLEMENTS|EXTENDS` +
`signature` Cypher in the read path.

### Supported dot-keys (v1)

| Dot-key | Graph path (stored + virtual) | `other` kind |
|---|---|---|
| `OVERRIDDEN_BY` | `(decl:Symbol)<-[:OVERRIDES]-(mover:Symbol)` | `symbol` (method) |
| `OVERRIDDEN_BY.DECLARES_CLIENT` | above + `(mover)-[:DECLARES_CLIENT]->(Client)` | `client` |
| `OVERRIDDEN_BY.DECLARES_PRODUCER` | above + `(mover)-[:DECLARES_PRODUCER]->(Producer)` | `producer` |
| `OVERRIDDEN_BY.EXPOSES` | above + `(mover)-[:EXPOSES]->(Route)` | `route` |

**Agent workflow (2 tool calls after search):**

```
describe(interface_method_id)   → OVERRIDDEN_BY.DECLARES_CLIENT: {out: 3}
neighbors(interface_method_id, 'out', ['OVERRIDDEN_BY.DECLARES_CLIENT'])
  → Client NodeRefs with via_id = concrete overrider method
```

Down from 3+ tool calls (describe + OVERRIDES + per-overrider DECLARES_CLIENT).

### Edge result shape

Same contract as `DECLARES.*` ([NEIGHBORS-DOT-KEY](./completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md) § Edge result shape):

| Field | Value |
|---|---|
| `origin_id` | Declared method Symbol id the agent passed |
| `edge_type` | The dot-key (e.g. `OVERRIDDEN_BY.DECLARES_CLIENT`), not bare `DECLARES_CLIENT` |
| `direction` | `out` (override-axis convention; see [Direction](#direction-constraint)) |
| `other` | Terminal `NodeRef` (method for base key; Client / Producer / Route for composed) |
| `attrs` | Full flat-hop attr projection on terminal edge `e`, plus `via_id` for composed keys |

**`via_id` semantics:** the **overrider** method Symbol id (`mover` in Cypher) —
the concrete implementation that owns the terminal edge. Omitted for base
`OVERRIDDEN_BY` (single hop; `other` is already the overrider method).

### Origin kind constraint

Override-axis dot-keys require a **non-static method** Symbol origin
(`kind = 'method'`, not `constructor`, no `static` modifier). Matches
`override_axis_rollup_for` gate in `kuzu_queries.py`.

- Type Symbol + `OVERRIDDEN_BY.*` → `success=False`:
  `"Composed edge types (OVERRIDDEN_BY.DECLARES_CLIENT) require a method Symbol origin"`
- Static method / constructor → same error (or silent empty if rollup already `{}`;
  prefer explicit error for consistency with `DECLARES.*` type-origin rejection)
- Route / Client / Producer origins → existing kind resolution failures

`DECLARES.*` and `OVERRIDDEN_BY.*` may appear in one `edge_types` list; handler
partitions by prefix / registry and applies the correct origin gate per family.

### Direction constraint

v1 supports **`direction="out"` only** for override-axis dot-keys. Rollup keys
only expose `out` counts today. Inbound dispatch ("which declaration does this
override implement?") remains `neighbors(..., ['OVERRIDES'], direction='out')`
from the concrete method, or `OVERRIDDEN_BY` is not the right key.

`direction="in"` with an override dot-key → `success=False` with a clear message
(mirror `DECLARES.*` inbound rejection).

### ComposedEdgeType validation

Extend `ComposedEdgeType` in `mcp_v2.py`:

```python
ComposedEdgeType = Literal[
    "DECLARES.DECLARES_CLIENT",
    "DECLARES.DECLARES_PRODUCER",
    "DECLARES.EXPOSES",
    "OVERRIDDEN_BY",
    "OVERRIDDEN_BY.DECLARES_CLIENT",
    "OVERRIDDEN_BY.DECLARES_PRODUCER",
    "OVERRIDDEN_BY.EXPOSES",
]
```

`_NEIGHBOR_EDGE_TYPES_ADAPTER` unchanged shape: `list[EdgeType | ComposedEdgeType]`.

### Cypher implementation

Add `KuzuGraph.override_axis_traversal_for(method_id, composed_key)` in
`kuzu_queries.py`, parallel to `member_edge_traversal_for`:

**Base `OVERRIDDEN_BY`:**

```cypher
MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol)
RETURN mover.id AS other_id
```

**Composed** (untyped `[e]` + `label(e) = $rel`, same binder pattern as
`member_edge_traversal_for`):

```cypher
MATCH (decl:Symbol {id: $id})<-[:OVERRIDES]-(mover:Symbol)-[e]->(term)
WHERE label(e) = $rel
RETURN mover.id AS via_id, label(e) AS stored_edge_type,
       term.id AS other_id,
       e.confidence AS confidence, e.strategy AS strategy,
       ...  /* same column set as member_edge_traversal_for / flat neighbors_v2 */
```

Registry in `kuzu_queries.py` (addresses [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) partially):

```python
_OVERRIDE_AXIS_COMPOSED_REL_MAP: tuple[tuple[str, str | None], ...] = (
    ("OVERRIDDEN_BY", None),  # base: no terminal rel
    ("OVERRIDDEN_BY.DECLARES_CLIENT", "DECLARES_CLIENT"),
    ("OVERRIDDEN_BY.DECLARES_PRODUCER", "DECLARES_PRODUCER"),
    ("OVERRIDDEN_BY.EXPOSES", "EXPOSES"),
)
```

`mcp_v2.py` imports composed key allowlists from `kuzu_queries` (or a tiny shared
`composed_edge_keys.py`) so `ComposedEdgeType` and traversal maps cannot drift.

**Equivalence guard:** keep (or add) tests that
`override_axis_traversal_for` row counts match `override_axis_rollup_for` out
counts for the same `method_id` on `tests/fixtures/override_axis_rollup_smoke/`
and bank-chat `requestAssignment` scenarios. Do **not** re-embed the
`IMPLEMENTS|EXTENDS` + `signature` walk in the traversal path unless a fixture
proves `[:OVERRIDES]` gaps.

### Counting semantics alignment

For the same origin and no `limit`/`offset`/`filter`:

| Key | `len(neighbors results)` must equal |
|---|---|
| `OVERRIDDEN_BY` | `edge_summary["OVERRIDDEN_BY"]["out"]` (one row per incoming `OVERRIDES` edge / distinct overrider) |
| `OVERRIDDEN_BY.DECLARES_*` | `edge_summary["OVERRIDDEN_BY.DECLARES_*"]["out"]` (sum of terminal edge rows across overriders, not distinct terminals) |

Duplicate `other.id` with different `via_id` is expected when two overriders
point at the same `Client` node.

Pagination applies to the combined flat + composed result list (same as #162).

## Scope

- `kuzu_queries.py` — `override_axis_traversal_for`, composed-key registry export
- `mcp_v2.py` — widen `ComposedEdgeType`; `neighbors_v2` branch (method origin,
  `out` only); base-key `Edge` without `via_id`
- `mcp_hints.py` — single-call templates for `OVERRIDDEN_BY.*`; success-path
  hints when dot-key neighbors return rows
- `server.py` — `describe` / `neighbors` tool `description=` strings
- `docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, README tool table
- `java_ontology.py` — optional `EDGE_SCHEMA` / `type_subject` strings for
  override-axis typical traversals (if those tables mention describe-only)
- Tests — see [Tests / validation](#tests--validation)

## Schema / ontology / re-index impact

- **Ontology bump:** not required (read-path only)
- **Re-index required:** no (uses existing `OVERRIDES` and terminal edges)
- **Config / tool surface:** four new valid `neighbors` `edge_types` values;
  composed results include `via_id` in `attrs` (except base `OVERRIDDEN_BY`)

## Tests / validation

- `test_neighbors_overridden_by_dot_key_returns_overriders` — base key returns
  method `NodeRef`s; ids match `neighbors(in, OVERRIDES)` on same origin
- `test_neighbors_overridden_by_dot_key_declares_client` — terminal Clients +
  `via_id`; count matches `describe` rollup
- `test_neighbors_overridden_by_dot_key_declares_producer` — smoke fixture
  `AbstractProducerApi.publish`
- `test_neighbors_overridden_by_dot_key_exposes` — smoke fixture `AbstractApi.handle`
- `test_neighbors_overridden_by_dot_key_count_matches_edge_summary` — unfiltered
  len vs `edge_summary` for bank-chat interface method + smoke fixture
- `test_neighbors_overridden_by_dot_key_type_origin_rejected` — class id + dot-key
- `test_neighbors_overridden_by_dot_key_static_method_rejected` — if applicable
- `test_neighbors_overridden_by_dot_key_inbound_rejected` — `direction="in"`
- `test_neighbors_accepts_overridden_by_dot_keys` — replaces
  `test_neighbors_still_rejects_overridden_by`
- Hint regression — `test_hints_*` prescribe dot-key recipe, not two-hop OVERRIDES
  chain (grep `then neighbors(overrider_ids`)

Manual:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_v2_compose.py tests/test_mcp_hints.py -v
```

## Decisions (locked)

1. **`edge_type` echoes the dot-key**, not the stored terminal label (same as #162).

2. **`via_id` in `attrs` only** for composed `OVERRIDDEN_BY.*` keys; base
   `OVERRIDDEN_BY` has no `via_id`.

3. **Traversal uses stored `[:OVERRIDES]`** for the dispatch hop; rollup Cypher
   is not duplicated in the hot path.

4. **`direction="out"`** on the virtual override axis even though `OVERRIDES`
   edges are stored `(overrider)-[:OVERRIDES]->(decl)` — matches `edge_summary`
   and agent mental model ("walk from declaration toward implementations").

5. **`NodeFilter` applies to terminal nodes** (Client / Producer / Route). For
   base `OVERRIDDEN_BY`, filter applies to overrider method `NodeRef`s.

6. **Flat `OVERRIDES` stays** in `EdgeType`; agents may still use one-hop
   `neighbors(..., ['OVERRIDES'])` when they only need overrider methods.

## Limitations

- Does not add per-method edge-presence on `NodeRef`s returned by
  `neighbors(..., ['DECLARES'])` ([#167](https://github.com/HumanBean17/java-codebase-rag/issues/167)).
- Inbound override-axis dot-keys (`direction="in"`) — out of scope; use stored
  `OVERRIDES` with appropriate direction from the concrete method.
- Does not add composed keys for override-axis `HTTP_CALLS` / `ASYNC_CALLS`
  (three-hop; same rationale as omitting `DECLARES.HTTP_CALLS` in SCHEMA-V2).

## Out of scope

- Changes to `override_axis_rollup_for` counting rules
- Builder / `pass*` changes to `OVERRIDES` materialization
- `search` / `find` result shape changes
- Full [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) sweep
  beyond sharing the override-axis composed map with `mcp_v2` (DECLARES map
  consolidation can land in the same PR or immediately after)

## Sequencing

Single implementation PR after propose approval:

1. `kuzu_queries.py` — traversal + registry
2. `mcp_v2.py` — types + handler
3. Tests (`test_mcp_v2_compose.py`, hints)
4. Docs + `server.py` + hints templates

Move this file to `propose/completed/` when the PR merges.

Optional follow-up: `plans/PLAN-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md` +
`plans/CURSOR-PROMPTS-OVERRIDDEN-BY-DOT-KEY-TRAVERSAL.md` if splitting review
steps is useful (not required for a single PR).
