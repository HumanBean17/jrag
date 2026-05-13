# Plan: describe override-axis rollup (`edge_summary` virtual dispatch keys)

Status: **active (planning)**. This plan implements
[`propose/DESCRIBE-OVERRIDE-ROLLUP-PROPOSE.md`](../propose/DESCRIBE-OVERRIDE-ROLLUP-PROPOSE.md).

Depends on: **none** for graph or indexer work (read-path only). **Coordinate with landed PR-89:** [`plans/completed/PLAN-DESCRIBE-MEMBER-EDGE-ROLLUP.md`](completed/PLAN-DESCRIBE-MEMBER-EDGE-ROLLUP.md) — `_edge_summary_for_node` in `mcp_v2.py` already threads `kind` + `row` and merges type-side `member_edge_rollup_for`. This rollout adds a **disjoint** branch for **method** symbols only (not constructors — short-circuit). Type and method branches are mutually exclusive on `row["kind"]`.

## Goal

- When `describe` targets a **method** `Symbol` (`data.kind == "method"`), `edge_summary` may include up to four **additive** composed / virtual keys: `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`, `OVERRIDES` — semantics per the propose (dispatch-down / dispatch-up / brownfield projection counts).
- **Constructor** symbols: trigger set includes them in the propose, but the implementation **short-circuits** to `{}` (no Cypher) — constructors are not overridden in the Java sense (UC11).
- **Static** methods (`"static"` in `modifiers`): entire override rollup suppressed (UC8).
- **Omission rule:** omit each key when its relevant count is `0` (same convention as `edge_counts_for` / `member_edge_rollup_for`).
- **No** Kuzu schema change, **no** ontology bump, **no** re-index requirement, **no** new MCP tools or `describe` parameters.
- **Docs:** `docs/AGENT-GUIDE.md` — extend the `describe` section with one subsection on **override-axis** keys (walk recipes, `neighbors` rejection, edge-row counting note for `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES`).
- **Optional:** one README sentence near the `describe` row (operator visibility; same pattern as PR-89).

## Principles (do not relitigate in review)

- **Virtual rollup only:** keys are computed at `describe`-time from `IMPLEMENTS` / `EXTENDS` + `DECLARES` + `signature` equality; nothing is persisted.
- **Method Symbols only (practical):** type / route / client / field behaviour unchanged. **Constructors** hit the branch but return `{}` immediately in Python (zero extra queries).
- **Naming:** standalone `OVERRIDDEN_BY` / `OVERRIDES` name the virtual dispatch relation; composed keys `OVERRIDDEN_BY.<EdgeType-like>` use the same dot convention as PR-89’s `DECLARES.<projected>` — but the parent axis is **virtual**, not a stored `DECLARES` hop off the described node.
- **Directions:** every override-axis entry uses `{"in": 0, "out": N}`; omit when `N == 0`.
- **Signature match:** `mover.signature = m.signature` (and same for `decl_m`) — the Kuzu `Symbol.signature` column is the only method-identity field at graph level; aligns with name+arity semantics of `_lookup_method_candidates` (see propose appendix B).
- **Depth:** exactly one `IMPLEMENTS`/`EXTENDS` hop per direction (UC13 — no transitive closure in rollup).
- **Static filter:** suppress all override-axis keys when the described method lists `"static"` in `modifiers` (populate via AST today). Prefer **`NOT list_contains(m.modifiers, 'static')`** in Cypher if the query anchors on `m`, matching repo `STRING[]` style (`kuzu_queries.py` uses `list_contains` elsewhere); alternatively gate in `mcp_v2` before calling the helper when `"static" in (row.get("modifiers") or [])`.
- **No** `OVERRIDDEN_BY.HTTP_CALLS` / `OVERRIDDEN_BY.ASYNC_CALLS` in this rollout (deferred with PR-89’s `DECLARES.HTTP_CALLS` deferral).

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Read-path override rollup + docs + model field text | **none** | `kuzu_queries.py`, `mcp_v2.py`, `docs/AGENT-GUIDE.md`, optional `README.md`, `tests/test_mcp_v2_compose.py` (+ possible one-line assertion fix in existing describe test) | five new tests (exact names below) | PR-89 already on `master` |

Landing order: **PR-1 only**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Surface | Extend `edge_summary` dict only (`has_overrides` field — out of scope; propose decision #18 deferred). |
| Trigger | `record.kind == "symbol"` and `data["kind"] == "method"` for rollup work; `constructor` → no-op without queries. |
| Query placement | New `KuzuGraph.override_axis_rollup_for(self, method_id: str) -> dict[str, dict[str, int]]` in `kuzu_queries.py` (adjacent to `member_edge_rollup_for`). |
| `OVERRIDDEN_BY` count | `len` of **distinct** override method node ids from dispatch-down query (`collect(DISTINCT mover.id)` pattern). |
| Brownfield counts | **Edge rows** outgoing `DECLARES_CLIENT` / `EXPOSES` from collected override method ids (same “edge rows not distinct methods” note as PR-89). Implement via `UNWIND $ids AS mid MATCH (x:Symbol {id: mid})-[e:REL]->() RETURN count(e)` (or two queries) — prefer one helper used by both rels to avoid drift. |
| Merge point | `_edge_summary_for_node`: after `edge_counts_for`, `elif` **method** branch: `summary.update(graph.override_axis_rollup_for(node_id))`. **Do not** run type rollup and method rollup for the same node (disjoint `kind` sets). |
| `neighbors` | Keys remain invalid `EdgeType` literals; existing Pydantic adapter rejects unknown strings. |
| README / re-index | No “Re-index required” callout; optional README one-liner. |

---

# PR-1 — Override-axis synthetic keys for method Symbols

## File-by-file changes

### 1. `kuzu_queries.py`

- Add `KuzuGraph.override_axis_rollup_for(self, method_id: str) -> dict[str, dict[str, int]]`:
  - **Dispatch-down:** Cypher as in propose §3.3 / appendix A: `(m)<-[:DECLARES]-(t)`, `(impl)-[:IMPLEMENTS|EXTENDS]->(t)`, `(impl)-[:DECLARES]->(mover)` with `mover.signature = m.signature`, `mover.id <> m.id`, and **not** static on **m** (the described declaration / default method).
  - Return `collect(DISTINCT mover.id)` (or equivalent) into Python; if non-empty, set `OVERRIDDEN_BY` to `{"in": 0, "out": len(impl_ids)}` (use **distinct id count**, matching propose appendix which uses `len(impls)` after distinct collect).
  - **Brownfield:** from that id list, count all outgoing `DECLARES_CLIENT` and `EXPOSES` edges (separate aggregates); emit `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES` only when counts > 0.
  - **Dispatch-up:** symmetric walk `(m)<-[:DECLARES]-(impl)`, `(impl)-[:IMPLEMENTS|EXTENDS]->(parent)`, `(parent)-[:DECLARES]->(decl_m)` with `decl_m.signature = m.signature`, `decl_m.id <> m.id`. Count **distinct** `decl_m.id` for `OVERRIDES` (UC7 allows `out: 2` — no dedup across declarations).
  - **Static on described method:** if using Cypher-only gating, anchor `WHERE NOT list_contains(COALESCE(m.modifiers, []), 'static')` (verify `COALESCE` / empty-list behaviour against Kuzu version used in CI) for both directions; or skip helper entirely from `mcp_v2` when modifiers contain `static`.
- **Dispatch-up for static m:** UC8 requires rollup silent; ensure the dispatch-up branch does not emit `OVERRIDES` for static interface methods when that would contradict the table — gating the **whole** helper on static `m` matches propose UC8 “all omitted”.

### 2. `mcp_v2.py`

- Add `_METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP = frozenset({"method"})` (constructors handled by absence from this set, or explicit `if sym_kind == "constructor": pass`).
- In `_edge_summary_for_node`, after the existing type rollup `if`:
  - `elif kind == "symbol" and str(row.get("kind") or "") in _METHOD_SYMBOL_KINDS_FOR_OVERRIDE_ROLLUP:` → `summary.update(graph.override_axis_rollup_for(node_id))`.
- Extend **`NodeRecord.edge_summary`** `Field(description=...)` to mention override-axis keys (`OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`, `OVERRIDES`) for **method** symbols, still stressing that dotted / virtual keys are not `EdgeType` literals for `neighbors`.

### 3. `docs/AGENT-GUIDE.md`

- Under `#### describe`, after the existing **Composed `edge_summary` keys (type Symbols)** block, add **Override-axis keys (method Symbols)** using propose §3.4 text (walk pattern, Pydantic rejection, counting semantics for composed brownfield keys).

### 4. `README.md` (optional)

- One sentence: `describe`’s `edge_summary` may include override-axis virtual keys on method symbols; pointer to AGENT-GUIDE.

### 5. `tests/test_mcp_v2_compose.py`

Add **exactly** these five tests (propose §6):

1. `test_describe_interface_method_with_annotated_impl_emits_rollup`
2. `test_describe_concrete_override_emits_overrides_rollup`
3. `test_describe_method_no_overrides_silent`
4. `test_describe_abstract_method_with_route_override_emits_exposes`
5. `test_describe_interface_method_diamond_override_counts_once_per_upstream`

**Fixture strategy:**

- Prefer **oracle Cypher** on the session `kuzu_graph` (same style as existing `test_describe_class_with_brownfield_clients_emits_composed_key`) to locate `(interface_method)-…-(impl_method)` chains with `signature` match and optional `DECLARES_CLIENT` / `EXPOSES` on the impl side; assert `describe_v2`’s `edge_summary` matches oracle counts for `OVERRIDDEN_BY` and composed keys.
- If `tests/bank-chat-system` lacks a required shape (e.g. clean **diamond** UC7, or abstract + partial route override UC5), add a **minimal** extra fixture tree under `tests/fixtures/` (not special-cased in production code) and a session-local or builder-backed graph — do **not** weaken tests to vacuous passes (see `tests/README.md`).
- **UC10 / signature erasure:** during implementation, confirm `Symbol.signature` stores erased form consistent between interface and impl; if a fixture mismatch appears, fix fixture or document one-line limitation in AGENT-GUIDE — no schema change in this PR.

### 6. Regression guard: existing `test_describe_method_symbol_no_composed_keys`

- That test proves **type-rolloup** `DECLARES.*` keys stay off method nodes. After this PR, the same method may legitimately show `OVERRIDDEN_BY` / `OVERRIDES`.
- **Amend assertions** to only require absence of keys `DECLARES.DECLARES_CLIENT` and `DECLARES.EXPOSES` (unchanged intent). Do not require zero dot-keys globally.

## Tests for PR-1

1. `test_describe_interface_method_with_annotated_impl_emits_rollup`
2. `test_describe_concrete_override_emits_overrides_rollup`
3. `test_describe_method_no_overrides_silent`
4. `test_describe_abstract_method_with_route_override_emits_exposes`
5. `test_describe_interface_method_diamond_override_counts_once_per_upstream`

## Definition of done (PR-1)

- [ ] `override_axis_rollup_for` exists; returns only positive-count keys; static described methods yield `{}`.
- [ ] `describe_v2` merges rollup for eligible **method** symbols; constructors unchanged from pre-rollout **direct** edge counts (no override keys).
- [ ] `neighbors_v2(..., edge_types=["OVERRIDDEN_BY"])` still fails validation at the Pydantic boundary.
- [ ] Five tests above pass; existing type-rollup tests still pass; `test_describe_method_symbol_no_composed_keys` assertion scope updated as above.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] `.venv/bin/python -m pytest tests -v` green (no heavy gate).
- [ ] AGENT-GUIDE updated; README updated if the optional bullet is taken.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Implement `override_axis_rollup_for` (dispatch-down, dispatch-up, brownfield counts) | `kuzu_queries.py` | Manual `describe` on a known interface method from bank-chat matches oracle |
| 2 | Wire `elif` method branch in `_edge_summary_for_node`; extend `NodeRecord` description | `mcp_v2.py` | Interface + impl scenario shows expected keys |
| 3 | AGENT-GUIDE + optional README | docs | Text matches propose §3.4 |
| 4 | Five new tests + fix `test_describe_method_symbol_no_composed_keys` | `tests/test_mcp_v2_compose.py` | `pytest` green |

---

## Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | `test_describe_method_symbol_no_composed_keys` starts failing or mis-documents intent | Medium | Restrict assertions to `DECLARES.*` absence only (see above). |
| 2 | Kuzu `IN` vs `list_contains` for `STRING[]` modifiers | Low | Use `list_contains` consistently with `kuzu_queries.py` / `mcp_v2` filters. |
| 3 | `signature` mismatch generic erasure (UC10) | Medium | Verify on fixture; adjust test oracle or document — no silent wrong counts without investigation. |
| 4 | Wide interfaces (`Runnable`-like fanout) | Low | Accept one bounded query per describe; micro-check latency on bank-chat if concerned (propose §8). |
| 5 | Diamond / multi-interface semantics surprise operators | Low | AGENT-GUIDE documents `OVERRIDES` counts upstream declarations (UC7). |

## Out of scope

- Persisted `OVERRIDES` / `OVERRIDDEN_BY` relationship types in Kuzu.
- Changing `CALLS` targets or call-graph resolution.
- Scalar columns on `Symbol` rows for override counts.
- Transitive multi-hop override closure inside one `describe`.
- `OVERRIDDEN_BY.HTTP_CALLS` / `OVERRIDDEN_BY.ASYNC_CALLS`.
- Rollup on Field / Class / Route / Client nodes.
- `has_overrides` boolean (deferred with PR-89 member-predicate pattern).

## Whole-plan done definition

1. Propose §3 surface behaviour matches `describe_v2` output on covered scenarios (UC1, UC2, UC5–UC7, UC6 silent).
2. Documentation and Pydantic field descriptions tell agents not to pass virtual keys to `neighbors`.
3. All default `pytest tests` pass; propose moved to `propose/completed/` only **after** the PR merges (repo convention).

## Tracking

- `PR-1`: _pending_
