> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: describe member edge rollup (`edge_summary` composed keys)

Status: **completed** (PR-1 landed). Source propose:
[`propose/completed/DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md`](../../propose/completed/DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md).

Depends on: **none** for graph or indexer work (read-path only).

**Related:** Companion describe rollup for method symbols (override-axis `edge_summary` keys) lives in [`propose/completed/DESCRIBE-OVERRIDE-ROLLUP-PROPOSE.md`](../../propose/completed/DESCRIBE-OVERRIDE-ROLLUP-PROPOSE.md) and [`plans/completed/PLAN-DESCRIBE-OVERRIDE-ROLLUP.md`](PLAN-DESCRIBE-OVERRIDE-ROLLUP.md) (landed in PR #110).

## Goal

- When `describe` targets a **type** `Symbol` (`kind` ∈ `class`, `interface`, `enum`, `record`, `annotation`), `edge_summary` includes up to two **additive** composed keys — `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES` — summarising **edge rows** reachable by `(type)-[:DECLARES]->(member:Symbol)-[:DECLARES_CLIENT|:EXPOSES]->(…)`.
- **Omission rule:** omit each composed key when its `out` count is `0` (same convention as `KuzuGraph.edge_counts_for` today).
- **No** Kuzu schema change, **no** ontology bump, **no** re-index requirement, **no** new MCP tools or `describe` parameters.
- **Docs:** `docs/AGENT-GUIDE.md` gains one paragraph under `describe` (composed keys, dot convention, 2-hop walk, edge-row vs method-count semantics, read-only w.r.t. `neighbors`).
- **Tests:** four new named tests (see below), green on default `pytest tests` (session `kuzu_graph` / bank-chat fixture).

## Principles (do not relitigate in review)

- **Composed rollup only:** keys are computed at `describe`-time; the graph stays unchanged.
- **Type Symbols only:** methods, constructors, routes, clients, package/file symbols, etc. — **no** `DECLARES.*` member rollup on those nodes (unchanged apart from normal `edge_counts_for`). A **constructor** `Symbol` described directly still gets only direct one-hop counts (including any `DECLARES_CLIENT` / `EXPOSES` on that constructor itself); it does not receive the **type** rollup, which is keyed off `data["kind"]` in the declaration-type set.
- **Naming:** `<parent_relation>.<projected_relation>`; dots are intentional — these keys are **not** `EdgeType` literals and must not be accepted by `neighbors(edge_types=…)`.
- **Directions:** composed entries use `{"in": 0, "out": N}`; omit when `N == 0`.
- **Counting:** counts are **edge rows** (not distinct methods); no de-duplication across `DECLARES_CLIENT` vs `EXPOSES` (Feign double-edge policy unchanged).
- **Depth:** exactly one `DECLARES` hop; nested types are excluded **structurally** (they are not `DECLARES` children of outer types in the current builder).
- **No** `DECLARES.HTTP_CALLS` / `DECLARES.ASYNC_CALLS` in this rollout (deferred by propose).

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Read-path rollup + docs + models | **none** | `kuzu_queries.py`, `mcp_v2.py`, `docs/AGENT-GUIDE.md`, optional one-line `README.md` MCP/describe note (lockstep with AGENT-GUIDE maintenance rule), `tests/test_mcp_v2.py` **or** `tests/test_mcp_v2_compose.py` | four new `describe` tests | — |

Landing order: **PR-1 only**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Surface | Extend `edge_summary` dict only (`member_predicates` / separate field — out of scope). |
| Trigger | `record.kind == "symbol"` and `data["kind"]` in `class`, `interface`, `enum`, `record`, `annotation`. |
| Query placement | New `KuzuGraph.member_edge_rollup_for(type_id: str) -> dict[str, dict[str, int]]` in `kuzu_queries.py` (next to `edge_counts_for`). |
| Merge point | After `edge_counts_for(id)`, merge rollup dict (non-empty keys only). |
| `neighbors` | Composed keys remain invalid `EdgeType`; Pydantic validation on `edge_types` rejects them (existing `TypeAdapter` + `Literal` set). |
| README / re-index | No "Re-index required" callout; optional README one-liner for operator visibility (see PR section). |

---

# PR-1 — Composed `edge_summary` keys for type Symbols

## File-by-file changes

### 1. `kuzu_queries.py`

- Add module-level `_ROLLUP_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})` (or keep set local to the method; avoid duplicating string sets across modules unnecessarily — if exported for tests, document).
- Add `KuzuGraph.member_edge_rollup_for(self, type_id: str) -> dict[str, dict[str, int]]`:
  - Run two Cypher queries (separate `MATCH`/`RETURN count(e)`), as in the propose appendix: `(t:Symbol {id})-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->()` and the same with `EXPOSES`.
  - Coerce each query to a **single** non-negative `int` (primary path: one aggregate row → `rows[0]`). If `_rows` ever returns multiple rows, **sum** `n` defensively so semantics stay “total edge rows,” not “first shard.”
  - Build at most two keys with `{"in": 0, "out": n}`; return `{}` when both counts are zero.
  - Reuse existing `_rows` / parameter style consistent with `edge_counts_for`.

### 2. `mcp_v2.py`

- Change `_edge_summary_for_node` to accept the described node context, e.g. `_edge_summary_for_node(graph, node_id, *, kind: str, row: dict[str, Any]) -> dict[str, dict[str, int]]`:
  - Base: `summary = graph.edge_counts_for(node_id)`.
  - If `kind == "symbol"` and `str(row.get("kind") or "")` is in the type-kind set, `summary.update(graph.member_edge_rollup_for(node_id))` (only adds positive-count keys).
- Update `describe_v2` to pass `kind` and `row` into `_edge_summary_for_node`.
- Before merge, run `rg _edge_summary_for_node` (or repo search): today only `describe_v2` calls it in production code; update any **tests or helpers** that call the helper directly after the signature change.
- Extend **`NodeRecord.edge_summary`** field description (`Field(description=...)`) to mention optional composed keys and that keys with a `.` are summaries, not traversable `EdgeType` labels.
- Optionally add a one-line comment near `EdgeType` / `_NEIGHBOR_EDGE_TYPES_ADAPTER` that composed `edge_summary` keys are intentionally excluded from `EdgeType` (documentation for implementers; keep stdout-clean).

### 3. `docs/AGENT-GUIDE.md`

- Under the `#### describe` section (after purpose line): add the paragraph from propose §3.3 (composed keys list, 2-hop enumeration recipe, Pydantic rejection for `neighbors`, edge-count vs method-count note).

### 4. `README.md` (optional but recommended)

- One sentence near the `describe` tool row: `edge_summary` may include composed dot-keys for type symbols (pointer to AGENT-GUIDE for semantics). Keeps operator-facing doc aligned with AGENT-GUIDE maintenance note.

### 5. Tests (`tests/test_mcp_v2_compose.py` **preferred** — already hosts `describe` `edge_summary` tests; alternatively `tests/test_mcp_v2.py` if you prefer all `describe_v2` tests in one file)

Implement **exactly** these four test names (from propose §6):

1. `test_describe_class_with_brownfield_clients_emits_composed_key`
2. `test_describe_controller_class_emits_composed_exposes`
3. `test_describe_method_symbol_no_composed_keys`
4. `test_describe_pojo_no_composed_keys`

**Fixture strategy (execution detail):**

- Prefer **oracle queries** on the session `kuzu_graph` (bank-chat) so tests stay invariant-based, not hard-coded to a specific FQN unless already stable in this repo:
  - **(1)** Pick `t.id` for a type `Symbol` with `MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[:DECLARES_CLIENT]->(:Client) WHERE t.kind IN $kinds` and `count(e) >= 1` (count in same shape as rollup). Assert `describe_v2(t.id)["edge_summary"]["DECLARES.DECLARES_CLIENT"]["out"]` equals that oracle count.
  - **(2)** Pick a **class**-grain controller type (e.g. `t.role = 'CONTROLLER'` and `t.kind = 'class'`) with at least one `DECLARES → EXPOSES` chain; assert composed `DECLARES.EXPOSES` `out` matches oracle `count(e)` for the 2-hop pattern. If multiple controllers qualify, still compare to oracle — do not assert an absolute integer like `5` unless the corpus guarantees it.
  - **(3)** `describe_v2` on a **method** symbol id (reuse a helper pattern from existing tests, e.g. `_controller_method_with_calls` or `_method_with_incoming_calls`); assert `"DECLARES.DECLARES_CLIENT"` and `"DECLARES.EXPOSES"` are **absent** from `edge_summary`.
  - **(4)** Pick a type `Symbol` with `DECLARES` out-edges to members but **zero** 2-hop `DECLARES_CLIENT` / `EXPOSES` through those members (oracle returns 0); assert composed keys absent.

If the session graph ever lacks a row for scenario (1) or (4), **do not** relax assertions into vacuous passes — instead add a minimal deterministic subtree or a function-scoped small build (follow patterns in `tests/_builders.py` / existing graph tests) as a follow-up; the implementer should verify locally first.

**Optional guard (propose §8 last risk):** assert or document that for a known controller class in bank-chat, `DECLARES` out-degree from the type matches the number of method/constructor members you enumerate via `neighbors` / Cypher — catches future builder changes that widen `DECLARES` targets.

## Definition of done (PR-1)

- [x] `member_edge_rollup_for` exists and returns only positive-count composed keys.
- [x] `describe_v2` merges rollup for eligible type symbols only.
- [x] `neighbors_v2(..., edge_types=["DECLARES.DECLARES_CLIENT"])` still fails validation (same class of error as today for invalid literals).
- [x] Four tests above pass, e.g.  
  `.venv/bin/python -m pytest tests/test_mcp_v2_compose.py::test_describe_class_with_brownfield_clients_emits_composed_key tests/test_mcp_v2_compose.py::test_describe_controller_class_emits_composed_exposes tests/test_mcp_v2_compose.py::test_describe_method_symbol_no_composed_keys tests/test_mcp_v2_compose.py::test_describe_pojo_no_composed_keys -v`  
  (adjust module path if tests land in `test_mcp_v2.py`), or run full `.venv/bin/python -m pytest tests -v`.
- [x] `.venv/bin/ruff check .` clean.
- [x] AGENT-GUIDE updated; README updated if the optional bullet is taken.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `member_edge_rollup_for` + Cypher | `kuzu_queries.py` | Unit-level: returns `{}` for id with no chains; correct counts on manual `kuzu` query for one type id |
| 2 | Thread `kind` + `row` into `_edge_summary_for_node`; merge rollup | `mcp_v2.py` | `describe_v2` on type shows keys when oracle > 0 |
| 3 | Field descriptions | `mcp_v2.py` | Pydantic schema / IDE docs mention composed keys |
| 4 | Agent + README docs | `docs/AGENT-GUIDE.md`, `README.md`? | Paragraph matches propose semantics |
| 5 | Four tests | `tests/test_mcp_v2_compose.py` (or `test_mcp_v2.py`) | `pytest` green |

---

## Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Agents pass composed keys to `neighbors` | Low | `EdgeType` literal + `TypeAdapter`; AGENT-GUIDE documents failure mode. |
| 2 | Operators confuse edge count with method count | Medium | AGENT-GUIDE paragraph (propose §3.3); `NodeRecord` field description. |
| 3 | Large types (many members) | Low | Two bounded 2-hop counts per `describe`; propose accepts cost. |
| 4 | Future `DECLARES` emission widens to non-method symbols | Medium | Optional test / comment linking to builder invariant (`build_ast_graph.py` member registration); re-open propose if builder changes. |
| 5 | Session fixture lacks DECLARES_CLIENT coverage | Medium | Oracle-driven tests; fall back to scoped mini-build only if needed. |
| 6 | Churn with override rollup PR | Medium | One `_edge_summary_for_node` signature and one merge strategy (see **Coordinate with** above); avoid landing two incompatible refactors to the same helper. |

## Out of scope

- New graph tables, columns, edge types, or `ontology_version` bump.
- `DECLARES.HTTP_CALLS`, `DECLARES.ASYNC_CALLS`, `CALLS` rollups, subclass aggregation, nested-type lexical rollups.
- `member_predicates` / `member_role_summary` fields.
- Filtering by `confidence` / `strategy` on composed counts.
- Making composed keys valid for `neighbors(edge_types=…)`.
- `plans/AGENT-PROMPTS-*` companion file (single small PR — add only if a human asks for Cursor handoff prompts).

## Whole-plan done definition

1. **Definition of done (PR-1)** — satisfied (implementation landed).
2. Propose archived at [`propose/completed/DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md`](../../propose/completed/DESCRIBE-MEMBER-EDGE-ROLLUP-PROPOSE.md).

## Tracking

- `PR-1`: **done** (code + docs + tests landed; propose in `propose/completed/`)
