# Plan: CALLS-NOISE-AND-RESOLUTION

Status: **completed** — PR-1 → PR-3 landed on `master` (ontology 15).
Source propose:
[`propose/completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md`](../../propose/completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md)
(tracks [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)).

Depends on: **SCHEMA-V2 landed** (`EDGE_SCHEMA`, MCP v2 tools). Complements
[`propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md)
`/mini-map` for accessor noise (Decision 39) — not a blocker.

**Cursor prompts:** [`plans/completed/CURSOR-PROMPTS-CALLS-NOISE.md`](./CURSOR-PROMPTS-CALLS-NOISE.md)
(per-PR handoffs; reference template).

## Fixture anchors (pinned — do not use fictional types)

| Anchor | Where | Notes |
| --- | --- | --- |
| High-fanout bank method | `com.bank.chat.engine.processors.ClientMessageProcessor#process(ProcessingContext,InternalEvent)` | **57** outbound `CALLS` on fresh `bank-chat-system` index; **5** `phantom` + **3** `chained_receiver` → **~49** default rows after PR-3. HV1/HV21/HV34/perf tests use this FQN. |
| Supertype-walk dedup | `tests/fixtures/call_graph_smoke/` | PR-1 adds minimal `SupertypeDedupPatterns` stub (interface + concrete same-site). **Not** `bank-chat-system` — bank has no interface+concrete duplicate `save` sites today. |
| `overload_ambiguous` | `tests/fixtures/call_graph_smoke/` `smoke.OverloadPatterns#sameArity` | Bank graph has **zero** `overload_ambiguous` rows; extend or mirror `test_overload_sameArity_emits_two_overload_ambiguous_edges`. |
| `callee_declaring_role` on annotated types | `bank-chat-system` | e.g. `@Repository` / `@Service` declaring types — column population only; not dedup/overload scenarios. |

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
- **PR-2 and PR-3 MCP shapes are additive** for readers who ignore new knobs; **PR-3**
  breaks phantom/chained `CALLS` rows. **PR-1 supertype dedup changes row cardinality**
  at duplicate sites (re-index), not MCP signatures.
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
6. README + AGENT-GUIDE: new column + dedup behavior; re-index callout (**include row-count
   delta** from supertype dedup, not only the new column).
7. PR-1 adds `tests/fixtures/call_graph_smoke/.../SupertypeDedupPatterns.java` (minimal
   interface+concrete same-site stub per fixture anchors above).

## Tests (exact names)

| Test | Asserts |
| --- | --- |
| `test_pass3_supertype_dedup_jpa_repository_save_one_row` | `call_graph_smoke` `SupertypeDedupPatterns` → one CALLS row per site, `callee_declaring_role='REPOSITORY'` |
| `test_pass3_overload_ambiguous_still_n_rows` | `call_graph_smoke` `OverloadPatterns#sameArity` → N rows, `strategy='overload_ambiguous'` |
| `test_pass3_callee_declaring_role_bank_annotated_types` | `bank-chat-system` — `@Repository` / `@Service` callees get expected roles |
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
4. `docs/AGENT-GUIDE.md`: `exclude_external` not on `neighbors`; role-filter trap;
   **`exclude_callee_declaring_roles: ['OTHER']` also drops known-external rows** (HV37);
   `/mini-map` cross-link.
5. `MCP_HINTS_FIELD_DESCRIPTION` updated for `edge_filter`.
6. **`java-codebase-rag unresolved-calls` CLI deferred to PR-3** (no empty stub in PR-2).
7. HINTS: `OTHER`-fallback + `NodeFilter.role` collision hints (Decisions 20, 30).
8. Perf (heavy-gated): `test_neighbors_calls_perf_empty_filter_client_message_processor`
   — `ClientMessageProcessor#process` empty-filter query within 1.5× pre-PR-2 median;
   skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

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
| `test_neighbors_calls_perf_empty_filter_client_message_processor` | Decision 31 — skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1` |

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

# Advisory only — named tests are the real pushdown gate (LIMIT patterns vary)
git diff master..HEAD -- mcp_v2.py kuzu_queries.py | rg "LIMIT.*call_site" || true
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
   - **Interleave order:** global `(call_site_line, call_site_byte)`; at equal `(line, byte)`,
     `row_kind='resolved'` before `row_kind='unresolved_call_site'`.
   - **`dedup_calls=True`:** one row per `(src_id, dst_id)`; canonical site =
     minimum `(call_site_line, call_site_byte)`; `call_site_lines` sorted ascending.
5. `describe` unresolved rollup (cap 5); **`java-codebase-rag unresolved-calls` CLI** (first landing).
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

- [x] Propose merged; moved to `propose/completed/` when PR-3 lands.
- [x] All three PRs merged to `master` in order.
- [x] `CURSOR-PROMPTS-CALLS-NOISE.md` archived under `plans/completed/` with final sentinels.
- [x] README ontology 15 + re-index callout accurate.
- [ ] `/mini-map` skill doc mentions `edge_filter` when PR-2 ships (optional doc-only follow-up on agent-skills branch).
