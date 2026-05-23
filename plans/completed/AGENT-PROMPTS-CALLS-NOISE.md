# Agent task prompts — CALLS-NOISE-AND-RESOLUTION (PR-1 → PR-3)

Status: **completed** (reference template). Plan:
[`plans/completed/PLAN-CALLS-NOISE.md`](./PLAN-CALLS-NOISE.md). Propose:
[`propose/completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md`](../../propose/completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md)
(tracks [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)).

One prompt per code PR. **Landing order:** PR-1 → PR-2 → PR-3. Do not start the next PR until the previous is merged to `master`.

**Fixture anchors (pinned — do not invent types):** see plan § Fixture anchors. High-fanout bank method:
`com.bank.chat.engine.processors.ClientMessageProcessor#process(ProcessingContext,InternalEvent)`.
Supertype dedup + `overload_ambiguous` tests use `tests/fixtures/call_graph_smoke/` (PR-1 adds `SupertypeDedupPatterns.java`).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- Nothing reachable from MCP tool handlers may write to **stdout**.
- Ontology bump **14 → 15 in PR-1 only**; PR-2/PR-3 stay at 15.
- If ambiguous versus the plan/propose, stop and ask — do not expand scope.
- Do not `git push` unless the user explicitly asked.
- No drive-by lint fixes outside deliverables.

---

## PR-1 — Schema: `callee_declaring_role` + supertype dedup + counters

**Branch:** `feat/calls-noise-schema` off `master`.
**Base:** `master` (propose merged; plan + this prompts file on `master`).
**Plan section:** `plans/PLAN-CALLS-NOISE.md` § PR-1.
**PR title:** `feat(schema): add callee_declaring_role to CALLS; supertype-walk dedup; unresolved counters`

**Attach (`@-files`):**

- `@plans/PLAN-CALLS-NOISE.md` (PR-1 only)
- `@propose/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md` (§3.3.1 supertype dedup pseudocode, §6 PR-1)
- `@build_ast_graph.py`
- `@java_ontology.py`
- `@ast_java.py`
- `@README.md` (Re-index section)
- `@docs/AGENT-GUIDE.md`
- `@tests/fixtures/call_graph_smoke/` (add `SupertypeDedupPatterns.java`)
- `@tests/test_call_graph_smoke_roundtrip.py`
- `@tests/test_schema_consistency.py`
- `@tests/conftest.py` (call_graph_smoke fixture if needed)

**Prompt:**

````
You are implementing PR-1 from `plans/PLAN-CALLS-NOISE.md`.

Read the **PR-1** section and plan § Fixture anchors before coding. Plan wins over this prompt; propose §3.3.1 is the dedup algorithm source of truth.

## Scope

1. Bump `ONTOLOGY_VERSION` **14 → 15** (`ast_java.py` / graph meta).
2. Add `callee_declaring_role STRING` to `CALLS` DDL; populate on every `_emit_call_edge` path (default `OTHER`).
3. Implement `collapse_supertype_duplicates` per propose §3.3.1 — run **only** before the `overload_ambiguous` emit loop; **never** collapse `overload_ambiguous`.
4. Add `GraphMeta` columns `pass3_unresolved_phantom_receiver`, `pass3_unresolved_chained` (count today's phantom-receiver / chained-receiver `CALLS` rows).
5. Register `callee_declaring_role` on `EDGE_SCHEMA['CALLS'].attrs` in `java_ontology.py`.
6. Update README + AGENT-GUIDE: ontology 15 re-index; new column; **row-count delta** from supertype dedup (not only additive column).
7. Add `tests/fixtures/call_graph_smoke/.../SupertypeDedupPatterns.java` (minimal interface + concrete same-site stub).
8. Add the **exact** tests named in the plan PR-1 table (verbatim function names).

## Out of scope (do NOT touch)

- `UnresolvedCallSite`, `UNRESOLVED_AT`, or moving phantom/chained rows out of `CALLS` (PR-3).
- `EdgeFilter`, `edge_filter`, `neighbors_v2` filter changes (PR-2).
- `include_unresolved`, `dedup_calls`, `row_kind` (PR-3).
- Deleting or clearing `tables.phantoms` wholesale (known-external still uses it until PR-3 narrows usage).
- `mcp_v2.py`, `mcp_hints.py`, `server.py`, `kuzu_queries.py` (unless a version gate string must bump — prefer not).
- `java-codebase-rag unresolved-calls` CLI (PR-3).
- HINTS template / `FUZZY_STRATEGY_SET` changes (PR-3 checklist).
- Fictional fixture types (`OrderService`, `MyRepository`) — use plan anchors only.

If you need any of the above, **stop and ask**.

## Deliverables

1. Ontology 15; `CALLS.callee_declaring_role` in DDL + emission + `EDGE_SCHEMA`.
2. Supertype-walk dedup helper wired in `pass3_calls` per §3.3.1.
3. `GraphMeta` counters populated on build.
4. `SupertypeDedupPatterns` stub + all six PR-1 named tests passing.
5. README/AGENT-GUIDE re-index callout includes cardinality change from dedup.

## Tests to run (iteration loop)

Run only these during local iteration; CI `test` on PR + `master` is the merge gate (full `pytest tests` when code changes).

- `tests/test_call_graph_smoke_roundtrip.py` — supertype dedup + `overload_ambiguous` scenarios on `call_graph_smoke`.
- `tests/test_schema_consistency.py` — `EDGE_SCHEMA` / DDL parity for new `CALLS` attr.
- `tests/test_ast_graph_build.py` — graph build / meta counters if PR-1 tests live here.

## Tests

Run:

```bash
.venv/bin/ruff check build_ast_graph.py java_ontology.py ast_java.py tests/
.venv/bin/python -m pytest tests/test_call_graph_smoke_roundtrip.py tests/test_schema_consistency.py tests/test_ast_graph_build.py -v -k "callee_declaring or supertype_dedup or overload_ambiguous or graph_meta_unresolved or calls_edge_has"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: all PR-1 named tests pass; only documented skips (e.g. heavy env gates elsewhere). No new failures in full suite.

## Sentinel checks (`git diff master..HEAD`)

```bash
git diff master..HEAD -- build_ast_graph.py | rg "UnresolvedCallSite|UNRESOLVED_AT" && exit 1 || true
git diff master..HEAD -- mcp_v2.py | rg "class EdgeFilter|edge_filter:" && exit 1 || true
git diff master..HEAD -- build_ast_graph.py | rg "del tables\.phantoms|phantoms\.clear\(\)" && exit 1 || true
```

## Manual evidence

```bash
rm -rf /tmp/calls-pr1 && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system --kuzu-path /tmp/calls-pr1 --verbose
.venv/bin/java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/calls-pr1
```

Confirm ontology 15 and new `GraphMeta` counters in meta output.

## Definition of Done

- [ ] All six PR-1 test names from the plan exist and pass.
- [ ] Sentinels return zero matches.
- [ ] README re-index callout mentions row-count delta from supertype dedup.
- [ ] PR title: `feat(schema): add callee_declaring_role to CALLS; supertype-walk dedup; unresolved counters`
- [ ] Branch: `feat/calls-noise-schema`
````

---

## PR-2 — MCP: `EdgeFilter` + ORDER BY + pushdown

**Branch:** `feat/calls-noise-edge-filter` off `master` **after PR-1 merged**.
**Base:** `master` at merge commit of PR-1.
**Plan section:** `plans/PLAN-CALLS-NOISE.md` § PR-2.
**PR title:** `feat(mcp): EdgeFilter on neighbors_v2`

**Attach (`@-files`):**

- `@plans/PLAN-CALLS-NOISE.md` (PR-2 only)
- `@propose/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md` (§3.4.1 ORDER BY + pushdown, §3.4.2 exclude_external, Decisions 35–38)
- `@mcp_v2.py`
- `@kuzu_queries.py`
- `@server.py` (tool descriptions / `_INSTRUCTIONS` only if needed)
- `@java_ontology.py` (`EDGE_SCHEMA['CALLS']`)
- `@docs/AGENT-GUIDE.md`
- `@README.md` (only if neighbors docs need `edge_filter`)
- `@tests/test_mcp_v2.py`
- `@tests/test_mcp_hints.py` (Decisions 20, 30 hints only)

**Prompt:**

````
You are implementing PR-2 from `plans/PLAN-CALLS-NOISE.md`.

Read **PR-2** and propose §3.4.1–§3.4.2 before coding. Plan wins over this prompt.

## Scope

1. Add `EdgeFilter` Pydantic model (`extra='forbid'`) in `mcp_v2.py`.
2. Wire `neighbors_v2(..., edge_filter=...)` with **fail-loud** validation when `edge_types` has >1 type or a filter field is not on every requested edge schema.
3. Dedicated CALLS Cypher path (or extended flat path) with:
   - `WHERE` pushdown for `min_confidence`, strategies, `callee_declaring_role` lists
   - `ORDER BY e.call_site_line, e.call_site_byte`
   - Edge predicates applied **before** `offset`/`limit` (no pre-filter cap that drops filtered rows)
4. Update `docs/AGENT-GUIDE.md`: `exclude_external` is **not** on `neighbors`; `NodeFilter.role` vs `EdgeFilter.callee_declaring_role` trap; **`exclude_callee_declaring_roles: ['OTHER']` drops known-external rows**; `/mini-map` cross-link for accessor noise.
5. Update `MCP_HINTS_FIELD_DESCRIPTION` for `edge_filter`.
6. HINTS: `OTHER`-fallback + `NodeFilter.role` collision hints (Decisions 20, 30).
7. Perf test `test_neighbors_calls_perf_empty_filter_client_message_processor` on pinned `ClientMessageProcessor#process`; **skip unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`**.
8. Add all **exact** PR-2 test names from the plan table.

**Defer to PR-3:** `java-codebase-rag unresolved-calls` CLI — **no empty stub** in PR-2.

## Out of scope (do NOT touch)

- `UnresolvedCallSite`, `UNRESOLVED_AT`, pass3 phantom/chained row removal (PR-3).
- `include_unresolved`, `dedup_calls`, `row_kind` on neighbors (PR-3).
- `ONTOLOGY_VERSION` bump (stays 15).
- `build_ast_graph.py` pass3 emission changes (except if a version comment is unavoidable — prefer zero).
- HINTS §3.9.1 checklist H1–H8 / `FUZZY_STRATEGY_SET` phantom removal (PR-3).
- `exclude_external` on `neighbors_v2`.
- `EdgeFilter` on `find_callers` / `find_callees`.
- CLI subcommands for unresolved calls (PR-3).

If you need any of the above, **stop and ask**.

## Deliverables

1. `EdgeFilter` + fail-loud single-edge-type validator (mirror `NodeFilter` applicability pattern).
2. CALLS neighbors return source order; filters pushed into Cypher before slice.
3. AGENT-GUIDE + hints field description updated per Decision 38 and HV37.
4. All eight PR-2 named tests; perf test heavy-gated.
5. PR description records supersession of MCP-V2 "no per-edge filter on neighbors" (Decision 16).

## Tests to run (iteration loop)

Run only these during local iteration; CI `test` is the merge gate (full pytest for code changes).

- `tests/test_mcp_v2.py` — `EdgeFilter`, ORDER BY, pushdown-before-limit, fail-loud, strategy xor.
- `tests/test_mcp_hints.py` — OTHER-fallback and NodeFilter.role collision hints only.

## Tests

Run:

```bash
.venv/bin/ruff check mcp_v2.py kuzu_queries.py server.py tests/test_mcp_v2.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_v2.py -v -k "ordered_by_call_site or edge_filter"
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "role_collision or other_fallback or neighbors_calls"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: all PR-2 named tests pass; perf test skips unless `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

## Sentinel checks (`git diff master..HEAD`)

```bash
git diff master..HEAD | rg "UnresolvedCallSite|UNRESOLVED_AT" && exit 1 || true
git diff master..HEAD -- build_ast_graph.py | rg "strategy=.phantom.|strategy=.chained_receiver." | rg "^\-.*_emit_call_edge" && exit 1 || true
git diff master..HEAD -- mcp_v2.py | rg "include_unresolved|dedup_calls" && exit 1 || true
git diff master..HEAD -- mcp_v2.py kuzu_queries.py | rg "ORDER BY.*call_site_line" || { echo "missing ORDER BY call_site_line"; exit 1; }
git diff master..HEAD -- docs/AGENT-GUIDE.md README.md | rg "neighbors.*exclude_external|exclude_external.*neighbors" && exit 1 || true
```

(`LIMIT.*call_site` grep is **advisory** only — named pushdown tests are authoritative.)

## Manual evidence

```bash
.venv/bin/python -m pytest tests/test_mcp_v2.py -k "ordered_by_call_site or edge_filter" -v
```

## Definition of Done

- [ ] All eight PR-2 test names from the plan pass (perf skips without heavy env).
- [ ] Sentinels pass; ORDER BY sentinel matches.
- [ ] No `unresolved-calls` CLI stub shipped.
- [ ] PR title: `feat(mcp): EdgeFilter on neighbors_v2`
- [ ] Branch: `feat/calls-noise-edge-filter`
````

---

## PR-3 — Breaking: `UnresolvedCallSite` + hints + interleave

**Branch:** `feat/calls-noise-unresolved-facet` off `master` **after PR-2 merged**.
**Base:** `master` at merge commit of PR-2.
**Plan section:** `plans/PLAN-CALLS-NOISE.md` § PR-3.
**PR title:** `feat(schema, mcp, hints): phantom-receiver/chained sites move to UnresolvedCallSite; include_unresolved; CALLS dedup`

**Attach (`@-files`):**

- `@plans/PLAN-CALLS-NOISE.md` (PR-3 only)
- `@propose/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md` (§3.5, §3.9.1 H1–H8, §6 PR-3)
- `@build_ast_graph.py`
- `@mcp_v2.py`
- `@mcp_hints.py`
- `@java_ontology.py`
- `@kuzu_queries.py`
- `@server.py`
- `@README.md`
- `@docs/AGENT-GUIDE.md`
- `@docs/JAVA-CODEBASE-RAG-CLI.md` (if CLI section exists)
- `@tests/test_ast_graph_build.py`
- `@tests/test_mcp_v2.py`
- `@tests/test_mcp_hints.py`
- `@tests/test_kuzu_queries.py` (find_callers paths)

**Prompt:**

````
You are implementing PR-3 from `plans/PLAN-CALLS-NOISE.md`.

Read **PR-3** and propose §3.9.1 (HINTS checklist H1–H8) before coding. This is the **only breaking PR**. Plan wins over this prompt.

## Scope

1. Add `UnresolvedCallSite` node table + `UNRESOLVED_AT` relationship.
2. Change `pass3_calls` (`build_ast_graph.py` ~1192–1211): emit UCS + `UNRESOLVED_AT` for chained-receiver and phantom-unresolved-receiver sites — **not** phantom-dst `CALLS`. **Preserve** known-external branch ~1257–1271 as `CALLS`.
3. Restrict `_phantom_method_id` / `tables.phantoms` to known-external emissions only.
4. `neighbors_v2`: `include_unresolved`, `dedup_calls`, `row_kind` on edge rows.
   - Interleave: global `(call_site_line, call_site_byte)`; at equal `(line, byte)`, `resolved` before `unresolved_call_site`.
   - `dedup_calls=True`: one row per `(src_id, dst_id)`; canonical = min `(line, byte)`; `call_site_lines` sorted ascending.
   - `include_unresolved=True` **⊥** `edge_filter` — fail-loud `ValueError`.
5. `describe` method rollup: `unresolved_call_sites` capped at 5.
6. Wire **`java-codebase-rag unresolved-calls list|stats`** to real data (first CLI landing).
7. Complete §3.9.1 H1–H8: templates, `FUZZY_STRATEGY_SET`, rewrite/remove phantom CALLS hint tests.
8. README breaking change + re-index note (ontology stays **15**).
9. Add all **exact** PR-3 test names from the plan table.

## Out of scope (do NOT touch)

- Second ontology bump (stay at 15).
- `exclude_external` on `neighbors_v2`.
- `EdgeFilter` on `find_callers`.
- Moving known-receiver-external rows out of `CALLS`.
- Erasing `overload_ambiguous` via dedup.
- Multi-hop `neighbors_v2`.
- Porting `/mini-map` heuristics into the indexer.

If you need any of the above, **stop and ask**.

## Deliverables

1. Zero `CALLS` rows with `strategy in ('phantom','chained_receiver')` for receiver-failure cases; UCS facet populated.
2. Known-external `CALLS` preserved (HV37).
3. `include_unresolved` interleave + `dedup_calls` per plan ordering rules.
4. HINTS checklist H1–H8 complete; `test_hints_neighbors_fuzzy_strategy_calls_phantom_emits` removed or rewritten.
5. CLI + describe surfaces working on fresh bank index.
6. All PR-3 named tests passing.

## Tests to run (iteration loop)

Run only these during local iteration; CI `test` is the merge gate (full pytest for code changes).

- `tests/test_ast_graph_build.py` — pass3 UCS emission, no phantom/chained CALLS rows.
- `tests/test_call_graph_smoke_roundtrip.py` — resolver regressions on smoke fixture.
- `tests/test_mcp_v2.py` — interleave, dedup, mutex, find_callers row set.
- `tests/test_mcp_hints.py` — high-fanout, has-unresolved, fuzzy-set updates (H1–H8).
- `tests/test_kuzu_queries.py` — find_callers no phantom/chained strategy if tests live here.

## Tests

Run:

```bash
.venv/bin/ruff check build_ast_graph.py mcp_v2.py mcp_hints.py kuzu_queries.py server.py tests/
.venv/bin/python -m pytest tests/test_mcp_v2.py tests/test_mcp_hints.py tests/test_ast_graph_build.py tests/test_call_graph_smoke_roundtrip.py -v \
  -k "phantom or chained or unresolved or callee_declaring or dedup_calls or include_unresolved"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: all PR-3 named tests pass; global invariant — zero receiver-failure `phantom`/`chained_receiver` on `CALLS` after fresh bank build.

## Sentinel checks (`git diff master..HEAD`)

```bash
git diff master..HEAD -- build_ast_graph.py | rg "strategy=.chained_receiver.|strategy=.phantom." | rg "^\+.*_emit_call_edge" && exit 1 || true
git diff master..HEAD -- build_ast_graph.py | rg "^\-.*tables\.phantoms" && exit 1 || true
git diff master..HEAD -- docs/ README.md AGENTS.md | rg "CALLS.*strategy.*phantom|strategy in \(.*phantom.*chained" && exit 1 || true
.venv/bin/python -m pytest tests/test_mcp_hints.py -k "phantom" -v --tb=no -q
```

Post-build invariant (after manual graph build to `/tmp/calls-pr3`):

```bash
.venv/bin/python -c "
import kuzu
db = kuzu.Database('/tmp/calls-pr3')
conn = kuzu.Connection(db)
r = conn.execute(\"MATCH ()-[c:CALLS]->() WHERE c.strategy IN ['phantom','chained_receiver'] RETURN count(*)\")
n = r.get_next()[0]
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

## Definition of Done

- [ ] All PR-3 named tests pass; H4 hint test removed or rewritten.
- [ ] Sentinels pass; post-build CALLS phantom/chained count is 0.
- [ ] README documents breaking change; propose moved to `propose/completed/` in same PR or follow-up chore PR per team convention.
- [ ] PR title: `feat(schema, mcp, hints): phantom-receiver/chained sites move to UnresolvedCallSite; include_unresolved; CALLS dedup`
- [ ] Branch: `feat/calls-noise-unresolved-facet`
````

---

## After all PRs land

- [x] Plan + prompts archived under `plans/completed/`.
- [x] Propose archived under `propose/completed/`.
- [ ] Close [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177) if still open.
