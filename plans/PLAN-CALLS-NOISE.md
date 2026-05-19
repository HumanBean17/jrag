# Plan: CALLS-NOISE-AND-RESOLUTION

Status: **active** (propose under review). Source propose:
[`propose/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md`](../propose/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md)
(tracks [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177), propose PR [#178](https://github.com/HumanBean17/java-codebase-rag/pull/178)).

Depends on: **SCHEMA-V2 landed** (`EDGE_SCHEMA`, MCP v2 tools). Complements
[`propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md)
`/mini-map` for accessor noise (Decision 39) — not a blocker.

Per-PR Cursor prompts: add `plans/CURSOR-PROMPTS-CALLS-NOISE.md` when PR-1 starts
(structural template: any file in `plans/completed/CURSOR-PROMPTS-*.md`).

## Goal

- Remove **true receiver-failure** CALLS rows (`strategy='phantom'` unresolved receiver,
  `strategy='chained_receiver'`) from the default agent transcript; store them as
  `UnresolvedCallSite` + `UNRESOLVED_AT` (PR-3).
- Add **`callee_declaring_role`** on `CALLS` edges and **`edge_filter`** on `neighbors_v2`
  (PR-1 + PR-2) so agents can project the ordered stream by stereotype without splitting
  edge types.
- Lock **source-order delivery** at MCP: `ORDER BY e.call_site_line, e.call_site_byte`
  (PR-2).
- **Preserve** known-receiver-external rows (`build_ast_graph.py:1257-1271`) and
  `overload_ambiguous` multi-row emissions.

## Principles (do not relitigate in review)

- **`CALLS` stays one edge type** — ordered transcript; no `DELEGATES_TO` / `PERSISTS_VIA` split.
- **PR-1 and PR-2 are strictly additive** for existing readers; **PR-3 only** breaks
  phantom/chained CALLS rows.
- **`edge_filter` is single-edge-type, fail-loud** — matches `NodeFilter` applicability pattern.
- **`include_unresolved=True` ⊥ `edge_filter`** — fail-loud mutual exclusivity.
- **`exclude_external` stays on `find_callers` / `find_callees` only** — not on `neighbors`.
- **Supertype dedup only** — never collapse `overload_ambiguous` (§3.3.1 pseudocode).
- **One re-index at ontology 15** in PR-1; PR-2/PR-3 do not bump `ONTOLOGY_VERSION` again.

## PR breakdown — overview

| PR | Scope | Ontology | Breaking | Primary areas |
| --- | --- | --- | --- | --- |
| PR-1 | `callee_declaring_role`, supertype dedup, `GraphMeta` counters, `EDGE_SCHEMA`, README | **14 → 15** | No | `build_ast_graph.py`, `java_ontology.py`, tests |
| PR-2 | `EdgeFilter`, `ORDER BY`, Cypher pushdown, hints field docs, AGENT-GUIDE | 15 | No | `mcp_v2.py`, `kuzu_queries.py`, `server.py`, docs, tests |
| PR-3 | `UnresolvedCallSite`, PR-3 pass3 branch, `include_unresolved`, hints, CLI wire-up | 15 | **Yes** | `build_ast_graph.py`, `mcp_v2.py`, `mcp_hints.py`, `kuzu_queries.py`, CLI, tests |

**Landing order:** PR-1 → PR-2 → PR-3 (sequential; no parallel code PRs).

---

# PR-1 — Schema: `callee_declaring_role` + supertype dedup + counters

## Deliverables

1. `ONTOLOGY_VERSION` 14 → 15 (`ast_java.py` / graph meta).
2. `CALLS` DDL + `callee_declaring_role STRING`; populate in all `_emit_call_edge` paths.
3. `collapse_supertype_duplicates` per propose §3.3.1 — **before** `overload_ambiguous` loop only.
4. `GraphMeta`: `pass3_unresolved_phantom_receiver`, `pass3_unresolved_chained` (count today's phantom/chained CALLS).
5. `java_ontology.py` `EDGE_SCHEMA['CALLS'].attrs` += `callee_declaring_role`.
6. README + AGENT-GUIDE: new column + dedup behavior; re-index callout.

## Tests (exact names)

| Test | Asserts |
| --- | --- |
| `test_pass3_supertype_dedup_jpa_repository_save_one_row` | `MyRepository extends JpaRepository` → one CALLS row, `callee_declaring_role='REPOSITORY'` |
| `test_pass3_overload_ambiguous_still_n_rows` | Name-only-fb overloads → N rows, `strategy='overload_ambiguous'` |
| `test_calls_edge_has_callee_declaring_role_column` | DDL / meta introspection |
| `test_graph_meta_unresolved_counters_present` | Counters in `describe(graph)` / meta output |
| `test_edge_schema_calls_registers_callee_declaring_role` | Snapshot / attrs list |

## Sentinel checks (`git diff master..HEAD` — zero matches outside PR-1 scope)

Run from repo root after PR-1 commits:

```bash
# PR-1 must NOT delete phantom/chained CALLS emission yet
git diff master..HEAD -- build_ast_graph.py | rg "UnresolvedCallSite|UNRESOLVED_AT" && exit 1 || true

# PR-1 must NOT add neighbors EdgeFilter yet
git diff master..HEAD -- mcp_v2.py | rg "class EdgeFilter|edge_filter:" && exit 1 || true

# PR-1 must NOT remove tables.phantoms wholesale
git diff master..HEAD -- build_ast_graph.py | rg "del tables\.phantoms|phantoms\.clear\(\)" && exit 1 || true
```

**Allowed in PR-1:** `callee_declaring_role`, supertype dedup helper, `GraphMeta` columns, ontology 15, `EDGE_SCHEMA` attr.

## Manual evidence

```bash
rm -rf /tmp/calls-pr1 && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system --kuzu-path /tmp/calls-pr1 --verbose
.venv/bin/java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/calls-pr1
```

---

# PR-2 — MCP: `EdgeFilter` + ORDER BY + pushdown

## Deliverables

1. `EdgeFilter` Pydantic model (`extra='forbid'`) in `mcp_v2.py`.
2. `neighbors_v2(..., edge_filter=...)` — fail-loud when `edge_types` has >1 type or attribute not on schema.
3. Dedicated CALLS Cypher path (or extended flat path) with:
   - `WHERE` pushdown for `min_confidence`, strategies, `callee_declaring_role`
   - `ORDER BY e.call_site_line, e.call_site_byte`
   - Filter **before** `offset`/`limit` slice
4. `docs/AGENT-GUIDE.md`: `exclude_external` not on `neighbors`; role-filter trap; `/mini-map` cross-link.
5. `MCP_HINTS_FIELD_DESCRIPTION` updated for `edge_filter`.
6. Optional: `java-codebase-rag unresolved-calls` CLI stub (empty until PR-3) — or defer CLI to PR-3.
7. HINTS: `OTHER`-fallback + `NodeFilter.role` collision hints (Decisions 20, 30).
8. Perf: `test_neighbors_calls_perf_empty_filter_order_service` (1.5× median, same hardware).

## Tests (exact names)

| Test | Asserts |
| --- | --- |
| `test_neighbors_calls_ordered_by_call_site` | HV38 — line/byte monotonic |
| `test_neighbors_calls_edge_filter_callee_declaring_role` | HV3 — SERVICE projection |
| `test_neighbors_calls_edge_filter_pushdown_in_cypher` | `callee_declaring_role` or `confidence` in query `WHERE` |
| `test_neighbors_calls_edge_filter_before_limit` | High fan-out method: filtered count < unfiltered cap |
| `test_neighbors_calls_edge_filter_mixed_types_fail_loud` | HV13 |
| `test_neighbors_calls_edge_filter_strategy_xor` | HV14 |
| `test_neighbors_calls_nodefilter_role_collision_hint` | Decision 30 |
| `test_neighbors_calls_perf_empty_filter_order_service` | Decision 31 (optional heavy; document skip) |

## Sentinel checks (`git diff master..HEAD`)

```bash
# PR-2 must NOT emit UnresolvedCallSite yet
git diff master..HEAD | rg "UnresolvedCallSite|UNRESOLVED_AT" && exit 1 || true

# PR-2 must NOT remove phantom/chained CALLS in pass3
git diff master..HEAD -- build_ast_graph.py | rg "strategy=.phantom.|strategy=.chained_receiver." \
  | rg "^\-.*_emit_call_edge" && exit 1 || true

# PR-2 must NOT add include_unresolved / dedup_calls yet (PR-3)
git diff master..HEAD -- mcp_v2.py | rg "include_unresolved|dedup_calls" && exit 1 || true

# PR-2 must add ORDER BY for CALLS (positive sentinel — expect match)
git diff master..HEAD -- mcp_v2.py kuzu_queries.py | rg "ORDER BY.*call_site_line" || \
  { echo "missing ORDER BY call_site_line"; exit 1; }

# Forbid early LIMIT on unfiltered CALLS hop (before WHERE edge predicates)
git diff master..HEAD -- mcp_v2.py kuzu_queries.py | rg "LIMIT.*call_site" && exit 1 || true
```

**Docs sentinel — zero stale `exclude_external` on neighbors claims:**

```bash
git diff master..HEAD -- docs/AGENT-GUIDE.md README.md | \
  rg "neighbors.*exclude_external|exclude_external.*neighbors" && exit 1 || true
```

## Manual evidence

```bash
.venv/bin/python -m pytest tests/test_mcp_v2.py -k "ordered_by_call_site or edge_filter" -v
```

---

# PR-3 — Breaking: `UnresolvedCallSite` + hints + interleave

## Deliverables

1. `UnresolvedCallSite` node + `UNRESOLVED_AT` rel tables.
2. `pass3_calls`: lines 1192–1211 → UCS; **preserve** 1257–1271 known-external CALLS.
3. `_phantom_method_id` / `tables.phantoms` restricted to known-external only.
4. `include_unresolved`, `dedup_calls`, `row_kind` on edge rows.
5. `describe` unresolved rollup (cap 5); CLI wired to real data.
6. HINTS §3.9.1 checklist H1–H8 (templates, fuzzy set, tests).
7. README breaking change + re-index note (ontology stays 15).

## Tests (exact names)

| Test | Asserts |
| --- | --- |
| `test_pass3_no_phantom_chained_calls_rows` | HV19 — zero CALLS with those strategies |
| `test_pass3_unresolved_call_site_emitted` | UCS + UNRESOLVED_AT for chained/phantom receiver |
| `test_pass3_known_external_calls_preserved` | HV37 — JDK call stays CALLS `resolved=False` |
| `test_find_callers_no_phantom_chained_strategy` | HV6/HV17 |
| `test_neighbors_include_unresolved_interleaved_order` | HV23 |
| `test_neighbors_include_unresolved_edge_filter_mutex` | Decision 25 |
| `test_neighbors_dedup_calls_collapses_identical_dst` | HV15 |
| `test_hints_neighbors_calls_high_fanout` | HV16 |
| `test_hints_neighbors_calls_has_unresolved` | HV18 |
| `test_hints_neighbors_fuzzy_strategy_calls_phantom_emits` | **Removed or rewritten** (H4) |

## Sentinel checks (`git diff master..HEAD`)

```bash
# After PR-3: no CALLS rows with receiver-failure strategies (positive — test asserts)
# Code must not re-introduce phantom-dst CALLS for chained/phantom receiver:
git diff master..HEAD -- build_ast_graph.py | rg "strategy=.chained_receiver.|strategy=.phantom." \
  | rg "^\+.*_emit_call_edge" && exit 1 || true

# tables.phantoms must not be deleted entirely (known-external still uses it)
git diff master..HEAD -- build_ast_graph.py | rg "^\-.*tables\.phantoms" && exit 1 || true

# Agents/skills must not grep CALLS for phantom strategies in docs we touch
git diff master..HEAD -- docs/ README.md AGENTS.md | \
  rg "CALLS.*strategy.*phantom|strategy in \(.*phantom.*chained" && exit 1 || true

# HINTS: phantom CALLS row tests must be updated, not left failing
.venv/bin/python -m pytest tests/test_mcp_hints.py -k "phantom" -v --tb=no -q
```

**Global post-PR-3 invariant (CI / local on fresh index):**

```bash
.venv/bin/python -c "
import kuzu
db = kuzu.Database('/tmp/calls-pr3/code_graph.kuzu')  # after fixture build
conn = kuzu.Connection(db)
n = conn.execute(\"MATCH ()-[c:CALLS]->() WHERE c.strategy IN ['phantom','chained_receiver'] RETURN count(c)\").get_as_df().iloc[0,0]
assert n == 0, n
"
```

## Manual evidence

```bash
rm -rf /tmp/calls-pr3 && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system --kuzu-path /tmp/calls-pr3 --verbose
.venv/bin/java-codebase-rag unresolved-calls stats --source-root tests/bank-chat-system --index-dir /tmp/calls-pr3
.venv/bin/python -m pytest tests/test_mcp_v2.py tests/test_mcp_hints.py tests/test_ast_graph_build.py -v \
  -k "phantom or chained or unresolved or callee_declaring"
```

---

## Definition of done (whole effort)

- [ ] Propose merged; moved to `propose/completed/` when PR-3 lands.
- [ ] All three PRs merged to `master` in order.
- [ ] `CURSOR-PROMPTS-CALLS-NOISE.md` archived under `plans/completed/` with final sentinels.
- [ ] README ontology 15 + re-index callout accurate.
- [ ] `/mini-map` skill doc mentions `edge_filter` when PR-2 ships (optional doc-only follow-up on agent-skills branch).
