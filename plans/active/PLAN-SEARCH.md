# Plan: SEARCH — redesign `jrag search` (trust, dedup, hybrid, graph-native)

Status: **active (planning)**. Intended home when approved: `plans/active/PLAN-SEARCH.md`
(+ companion `plans/active/AGENT-PROMPTS-SEARCH.md`, modeled on
`plans/completed/AGENT-PROMPTS-INIT-INCREMENT-PERF.md`). This file is the plan-mode
draft; copy it to the repo path on approval.

> **Grounded against current source (2026-07-06) by direct read of
> `search_lancedb.py`, `mcp_v2.py`, `jrag.py`, `jrag_render.py`, `jrag_envelope.py`,
> `ast_java.py`, `lance_optimize.py`, `pipeline.py`, `java_index_flow_lancedb.py`,
> plus manual testing against `tests/bank-chat-system/.java-codebase-rag/`.**
> Every edit site below is cited `file:line`.

Depends on: nothing external. Phase 0 PRs are mostly independent (see landing order);
Phases 1–4 build on Phase 0's honest-score foundation.

## Why (context)

`jrag search` was the tool's first feature. The semantic core is strong (a
"where are messages published to kafka" query correctly surfaces the three Kafka
publisher classes as top hits), and one shared engine (`mcp_v2.search_v2`,
`mcp_v2.py:800`) backs both the agent CLI and MCP — good architecture. But a review
(found bugs + 3 parallel idea-generation passes) surfaced a cluster of problems that
make search **look broken even when it's working**, plus two real crash/correctness
bugs. The user approved a **full redesign**; this plan ships the foundation (Phase 0)
as reviewable PRs and roadmaps the rest. Locked user decisions:

1. **Scope: full redesign** (all 5 phases).
2. **Dedup: one row per symbol/type by default**, `--chunks` opts back to chunk-level.
3. **Unified 0–1 score** across vector + hybrid (normalize hybrid RRF to match vector),
   with the raw rank signal preserved in `--explain`.
4. **FTS index built at index time** for yaml/sql (and java) — fix at the source.

## Goal (Phase 0 — what ships)

- A **unified, rank-monotonic 0–1 score** so the rendered `score=` is honest (monotonic
  with rank) and `--min-score` means the same thing in vector and hybrid modes.
- An **`--explain` breakdown** (dist / role / symbol / import / rrf) reviving the
  currently-dead `explain_score_components` helper.
- **Per-symbol dedup by default** (`primary_type_fqn`), `--chunks` to opt back.
- **`--hybrid` that works on every table** (FTS built at index time) and degrades
  gracefully instead of throwing a raw Lance error on old indexes.
- **Zero-result guidance** ("matches exist under role X") and a `--limit 0` fix.
- **`REPOSITORY` role for JPA interfaces** (`extends JpaRepository`) via supertype
  inference (the role enum already exists everywhere; only inference is missing).

Explicitly **not** reimplemented in Phase 0: ranking fundamentals, the embedding
model, graph expansion engine, the envelope/error framework.

## Principles (do not relitigate in review)

- **One engine.** All changes land in the shared `search_v2` / `run_search` path;
  CLI (`jrag.py`) and MCP (`server.py`) stay thin and symmetric. No CLI-only behavior.
- **Don't perturb ranking while fixing scores.** Sort keys read `_distance` /
  `_score` / `_rrf_score` internally (verified); we rescale the **displayed** score at
  its origin / in a post-sort pass, so ranking is unchanged unless we say otherwise.
- **Respect the anti-overfitting rule** (`tests/README.md`, `test_lancedb_e2e.py:10–13`):
  e2e tests assert shape, never exact scores/ranking/snippets. Score-sensitive
  assertions go in unit tests with controlled/mock hits.
- **Backend already has unexposed features** (`graph_expand`, `auto_hybrid`,
  `context_neighbors` in `run_search` at `search_lancedb.py:809/816/817`). Phases 1–2
  are mostly "thread these through CLI/MCP," not new engines.

## Architecture (where the code lives)

Shared engine (both CLI + MCP):
- `search_lancedb.py` — `run_search` (`:796`), `_search_one_table` (`:466`),
  scoring: `_vector_sort_key` (`:338`), `_hybrid_sort_key` (`:350`),
  `l2_distance_to_score` (`:401`), `_apply_symbol_bonus` (`:325`), `_role_weight`
  (`:310`), `_graph_expand_merge` (`:678`), `_rrf_merge` (`:761`), FTS
  `ensure_text_fts_index` (`:426`). Score origins: vector `:545`, hybrid `:518`,
  graph-RRF `:787/:790`.
- `mcp_v2.py` — `search_v2` (`:800`), `_row_to_search_hit` (`:571`), `SearchHit`
  (`:443`), `SearchOutput` (`:492`), `NodeFilter` (`:171`).

CLI surface: `java_codebase_rag/jrag.py` — search subparser (`:1037–1091`),
`_cmd_search` (`:4018–4167`), score floor (`:4115–4119`), dedup insertion window
(`:4127→4146`), `--limit 0` bug site (`:4045`/`:4146`), stderr shim (`:4170`).

MCP surface: `server.py` — `search` tool registration (`:496`), `async def search`
(`:510–545`).

Render: `java_codebase_rag/jrag_render.py` — `render` (`:639`), `_render_listing`
(`:223`), inline-extras `_NORMAL_INLINE_EXTRAS` (`:66`), `_BRIEF_INLINE_EXTRAS`
(`:81`), raw score print (`:269/277`); `java_codebase_rag/jrag_envelope.py` — node
key allowlists `_BRIEF_NODE_KEYS` (`:755`), `_NORMAL_NODE_KEYS` (`:780`),
`mark_truncated` (`:345`).

Indexing: `java_codebase_rag/lance_optimize.py` — `optimize_lance_tables` (`:97`),
post-optimize safe window after `await table.optimize()` (`:173`),
`LANCE_TABLE_NAMES` (`:35`); `pipeline.py` — `run_cocoindex_update` (`:124`) →
`_maybe_run_serialized_optimize` (`:151/156`); `java_index_flow_lancedb.py` — chunk
schemas (`:228/253/265`), all tables have `text` + `embedding` columns.

Ontology: `ast_java.py` — `ROLE_ANNOTATIONS` (`:89`, already has `Repository→REPOSITORY`),
`infer_role_for_type` (`:2741–2777`, **no supertype scan**), capability supertype
scan to mirror (`:2811–2814`), `ONTOLOGY_VERSION` (`:87`). `java_ontology.py:22`
`VALID_ROLES`, `mcp_v2.py:75` `Role`, `search_lancedb.py:188` `_ROLE_SCORE_WEIGHTS`
— all already include `REPOSITORY`.

## PR breakdown — overview

| PR | Scope | Ontology bump | Key files | Independent of |
|----|-------|---------------|-----------|----------------|
| PR-SEARCH-1a | Unified honest + normalized 0–1 score | no | search_lancedb.py, mcp_v2.py, jrag_render.py | — (foundation) |
| PR-SEARCH-1b | `--explain` score breakdown | no | mcp_v2.py, jrag.py, jrag_render.py, jrag_envelope.py | 1a |
| PR-SEARCH-2 | Per-symbol dedup default + `--chunks` | no | search_lancedb.py, mcp_v2.py, jrag.py, server.py | 1a (soft) |
| PR-SEARCH-3 | FTS at index time + graceful hybrid fallback | no | lance_optimize.py, search_lancedb.py, mcp_v2.py | 1a (soft, shares :518) |
| PR-SEARCH-4 | Zero-result guidance + `--limit 0` fix + housekeeping | no | jrag.py, mcp_v2.py, server.py, jrag_render.py | — |
| PR-SEARCH-5 | REPOSITORY role for JPA interfaces | **yes** | ast_java.py | — |

**Landing order:** 1a → (1b, 2, 4 in parallel) → 3 → 5. 1a is the foundation (score
honesty); 1b depends on 1a's component-plumbing; 2/4 are independent; 3 lightly
overlaps 1a at `search_lancedb.py:518` (coordinate, land after 1a); 5 is isolated but
bumps ontology (land alone, signals reprocess).

## Resolved design decisions

| Topic | Decision |
|-------|----------|
| Score scale | Displayed score = rank-monotonic 0–1 in **both** modes. Vector: `l2_distance_to_score(adjusted_distance)`; Hybrid/graph-RRF: theoretical-max normalization (preserve "weak query" signal, unlike min-max). Raw rank kept in `--explain`. |
| Index type for FTS | Keep **Tantivy** (`create_fts_index`) for parity with the lazy path (`search_lancedb.py:434`); do **not** mix native `INVERTED` on some tables — mixing errors. |
| Dedup location | Inside `search_v2`/`run_search` (not CLI-only) with over-fetch, so CLI + MCP both dedup. Java table only (sql/yaml have no `primary_type_fqn` → pass through). |
| Dedup default | ON by default; `--chunks` opts back to today's chunk-level output. Documented breaking change. |
| `--fuzzy` | **Keep** (retiring it breaks `test_search_fuzzy_rejected...` and gains little); leave the in-handler reject. Out of scope to remove. |
| REPOSITORY detection | Supertype-name scan in `infer_role_for_type`; bump `ONTOLOGY_VERSION` so users get a clean "reprocess" signal and the Ladybug stale-graph check trips. |

---

# PR-SEARCH-1a — Unified honest + normalized 0–1 score

**Goal:** make the rendered `score=` monotonic with rank and on a common 0–1 scale
across vector and hybrid modes, so `--min-score` is consistent and trustworthy.

**Key facts (verified):**
- Sort uses **adjusted** distance (`_vector_sort_key` `search_lancedb.py:338–347`:
  `distance + import_penalty − role_weight − symbol_bonus`); displayed score is
  `l2_distance_to_score(raw distance)` set at `:545` — decoupled, hence non-monotonic.
- Sort keys read `_distance`/`_score`/`_rrf_score` internally; rescaling the displayed
  score at origin / post-sort **does not change ranking**.
- All four `_score_components` sub-keys are populated by the time the sort runs:
  `distance` (`:341`), `import_penalty` (`:344`), `role_weight` (`:321`),
  `symbol_bonus` (`:335`).
- Hybrid `_score` origin: `:518` (LanceDB `_relevance_score`, RRF, ≈0.016 — ~60×
  smaller than vector). Graph-RRF origin: `:787/:790`.

## File-by-file changes

### 1. `search_lancedb.py` (modified)
- **Vector honest score:** after the sort, recompute the displayed score from the
  adjusted distance. Insert a post-sort pass immediately after `:891` (single-table
  vector) and `:933` (multi-table): for each row set
  `r["_score"] = l2_distance_to_score(comps["distance"] + comps.get("import_penalty",0)
  − comps.get("role_weight",0) − comps.get("symbol_bonus",0))`, clamped to `[0, 1.0]`.
  (Single-table hybrid path `:889` keeps its own normalization below.)
- **Hybrid normalization:** at the origin `:518` (or a post-sort pass after `:889`),
  normalize the LanceDB RRF `_score` to 0–1 by theoretical max
  (`max = n_lists / (k + 1)`, `k=60`; for plain hybrid `n_lists=2`). Keep the raw
  rank in `_score_components["rrf_raw"]` for `--explain`.
- **Graph-RRF normalization:** after `_rrf_merge` sort (`:792`), normalize
  `_rrf_score` by its theoretical max (`Σ weight·1/(k+rank+1)`) before return.
- Add a small `_normalize_hybrid_score(...)` helper; keep `l2_distance_to_score` as-is.

### 2. `mcp_v2.py` (modified)
- `_row_to_search_hit` (`:571–594`): no score-math change needed (it already prefers
  `_rrf_score`/`_score`), but verify `:572` picks up the now-normalized values.

### 3. `jrag_render.py` (modified)
- Round the rendered score to 3 decimals at `:269`/`:277` (`f"{key}={round(v,3)}"`)
  — currently raw float; normalization changes precision/length.

## Tests for PR-SEARCH-1a
`tests/test_search_lancedb.py` (add):
1. `test_vector_displayed_score_is_rank_monotonic` — feed controlled rows with
   differing bonuses, assert `_score` is non-increasing down the sorted list.
2. `test_hybrid_score_normalized_to_unit_range` — assert hybrid `_score` ∈ [0,1] and
   top hit ≥ 0.5 under theoretical-max.

`tests/test_jrag_orientation.py` (update):
3. `test_search_min_score_drops_negative_noise` (`:361`) — re-baseline for the unified
   scale (vector scores are now clamped ≥ 0; negative-noise semantics change). Keep the
   intent (floor drops weak hits), adjust the controlled hit values.

`tests/test_jrag_render.py` (update):
4. `test_search_text_normal_shows_score_not_snippet` (`:534`) — allow rounded score.

## Definition of done (PR-SEARCH-1a)
- [ ] Vector displayed score is non-increasing with rank on `tests/bank-chat-system`
      (manual: `jrag search "kafka publishers"` scores descend).
- [ ] Hybrid scores land in [0,1], comparable to vector.
- [ ] `--min-score 0.3` drops the same proportion of hits in vector and hybrid.
- [ ] All listed tests pass; e2e shape-tests unchanged.
- [ ] PR title: `feat(search): unified rank-monotonic 0–1 score (PR-SEARCH-1a)`.

## Implementation steps
| # | Step | File(s) | Done when |
|---|------|---------|-----------|
| 1 | Vector post-sort honest-score pass | search_lancedb.py:891,933 | monotonic-score unit test green |
| 2 | Hybrid + graph-RRF normalization | search_lancedb.py:518,889,792 | hybrid-range unit test green |
| 3 | Render rounding | jrag_render.py:269,277 | render test green |
| 4 | Re-baseline min_score test | test_jrag_orientation.py:361 | suite green |

---

# PR-SEARCH-1b — `--explain` score breakdown

**Goal:** surface the already-computed `_score_components` so agents/users can see
*why* a hit ranked (dist / role / symbol / import / rrf).

**Key facts (verified):**
- `explain_score_components` (`search_lancedb.py:362–398`) is **dead code** — zero
  callers (grep-confirmed). Free to repurpose.
- `_score_components` is currently **dropped** at `_row_to_search_hit`
  (`mcp_v2.py:571–594`, never copied onto `SearchHit`).

## File-by-file changes

### 1. `mcp_v2.py` (modified)
- Add field `score_components: dict | None = None` to `SearchHit` (`:443–453`).
- In `_row_to_search_hit` (`:583–594`), copy `row.get("_score_components")` onto the hit.

### 2. `java_codebase_rag/jrag.py` (modified)
- Add `--explain` flag on the search subparser (`:1051–1090`).
- In `_cmd_search`, when `args.explain`, populate `d["explain"]` =
  `explain_score_components(hit.score_components, role=..., hybrid=..., graph_expanded=...)`
  on each node dict (`:4120–4127`). Import the revived helper from `search_lancedb`.

### 3. `java_codebase_rag/jrag_render.py` + `jrag_envelope.py` (modified)
- Add `"explain"` to `_NORMAL_INLINE_EXTRAS` (`jrag_render.py:66`) and to
  `_NORMAL_NODE_KEYS` (`jrag_envelope.py:780`) so it survives projection and renders
  inline. (Brief tier: optional.)

### 4. `server.py` (modified)
- Add `explain: bool = False` param to the MCP `search` tool (`:510–533`), thread into
  `asyncio.to_thread(mcp_v2.search_v2, ...)` (`:535–545`) and `search_v2` signature.

## Tests for PR-SEARCH-1b
`tests/test_mcp_v2.py`: `SearchHit` carries `score_components`; `search` tool accepts
`explain`. `tests/test_jrag_orientation.py`: `--explain` passthrough (copy
`test_search_hybrid_calls_hybrid_path` `:242`). `tests/test_jrag_render.py`:
`explain=` token appears at normal detail.

## Definition of done (PR-SEARCH-1b)
- [ ] `jrag search "audit" --explain` shows a `explain=dist=0.42 role:+0.05 symbol:+0.03`
      style token per hit (text + JSON).
- [ ] MCP `search` accepts `explain` and returns `score_components` per hit.
- [ ] PR title: `feat(search): --explain score breakdown (PR-SEARCH-1b)`.

---

# PR-SEARCH-2 — Per-symbol dedup by default + `--chunks`

**Goal:** collapse multiple chunks of the same `primary_type_fqn` to one row (best
chunk wins), so a single type can't flood the page. `--chunks` restores chunk-level.

**Key facts (verified):**
- No per-symbol/per-file dedup exists on the search path. Only chunk-level dedup
  inside `_rrf_merge` (`search_lancedb.py:782`, keyed `(filename, range_start, range_end)`).
- Symbol identity = `primary_type_fqn` (Lance column, `JAVA_ENRICHED_COLUMNS:33`),
  surfaces as `SearchHit.fqn` (`mcp_v2.py:586`). Use `fqn`, not the finer `symbol_id`.
- Multi-table merge already over-fetches (`per_table = max(need*3, need)` `:911`) —
  same pattern to reuse for dedup.

## File-by-file changes

### 1. `search_lancedb.py` (modified)
- Add `dedup_by_fqn: bool = False` param to `run_search` (`:796`). When set: scale the
  fetch `need` (e.g., `need_dedup = need * 4`) so enough unique FQNs survive, then
  after the sort collapse by `primary_type_fqn` (first-seen-wins; rows are sorted),
  annotate the survivor with `chunks = N` (count collapsed), then window. Java table
  only (sql/yaml lack the column → no-op).

### 2. `mcp_v2.py` (modified)
- Add `dedup: bool = True` to `search_v2` (`:800`); pass `dedup_by_fqn` to `run_search`.
- Add `chunks: int | None` to `SearchHit` (`:443`) for the collapse count.

### 3. `java_codebase_rag/jrag.py` (modified)
- Add `--chunks` flag (store_true) on the subparser (`:1051–1090`). In `_cmd_search`,
  pass `dedup = not args.chunks` to `search_v2` (`:4092–4101`).

### 4. `server.py` (modified)
- Add `chunks: bool = False` to the MCP `search` tool (`:510`); thread to `search_v2`
  as `dedup = not chunks`.

### 5. Docs (modified) — breaking-change callout
- `docs/AGENT-GUIDE.md`, `docs/JAVA-CODEBASE-RAG-CLI.md`: document dedup default +
  `--chunks`.

## Tests for PR-SEARCH-2
`tests/test_search_lancedb.py`: `test_run_search_dedup_by_fqn_collapses_chunks`
(mock rows with repeated `primary_type_fqn`, assert one survivor + `chunks=N`).
`tests/test_jrag_orientation.py`: mock `search_v2` returning 2 same-FQN hits, assert
1 node by default; `--chunks` → 2 nodes. `tests/test_mcp_v2.py`: `dedup` param shape
+ that `chunks=False` disables it.

## Definition of done (PR-SEARCH-2)
- [ ] `jrag search "kafka"` no longer shows `ChatKafkaConfiguration` 3× (one row,
      `chunks=3`).
- [ ] `--chunks` restores today's chunk-level output.
- [ ] MCP `search` dedups by default; `chunks=true` opts out.
- [ ] Pagination still correct (over-fetch keeps pages full).
- [ ] PR title: `feat(search)!: per-symbol dedup by default, --chunks opt-out (PR-SEARCH-2)`.

## Implementation steps
| # | Step | File(s) | Done when |
|---|------|---------|-----------|
| 1 | `run_search` over-fetch + fqn-collapse | search_lancedb.py:796,855,905 | dedup unit test green |
| 2 | `SearchHit.chunks` + `search_v2(dedup=)` | mcp_v2.py:443,800 | mcp test green |
| 3 | `--chunks` CLI flag | jrag.py:1051,4092 | CLI test green |
| 4 | MCP `chunks` param + docs | server.py:510, docs | parity test green |

---

# PR-SEARCH-3 — FTS at index time + graceful hybrid fallback

**Goal:** `--hybrid` works deterministically on every table (no first-query race, no
yaml/sql crash); old indexes degrade gracefully.

**Key facts (verified):**
- FTS is **lazy only**: `ensure_text_fts_index` (`search_lancedb.py:426`) calls
  Tantivy `create_fts_index("text")` (`:434`), invoked only in the hybrid branch
  (`:492–493`). The yaml-hybrid crash ("Cannot perform full text search unless an
  INVERTED index…") comes from this lazy path racing/failing.
- Clean index-time insertion point: `optimize_lance_tables`
  (`java_codebase_rag/lance_optimize.py:97`), after `await table.optimize()` (`:173`),
  the documented writer-free window (`pipeline.py:143–152`). Iterate
  `LANCE_TABLE_NAMES` (`:35–39`).
- All three tables have a `text` column (`java_index_flow_lancedb.py:228/253/265`).

## File-by-file changes

### 1. `java_codebase_rag/lance_optimize.py` (modified)
- After the successful `await table.optimize()` (`:173`) and before `results[name]="ok"`
  (`:186`), call `await table.create_fts_index("text", replace=True)` per table
  (Tantivy — parity with the lazy path). try/except the "already exists" case (idiom
  at `search_lancedb.py:435–443`). Runs for every `init`/`reprocess`/`increment`
  (all flow through `_maybe_run_serialized_optimize`, `pipeline.py:151`).

### 2. `search_lancedb.py` (modified)
- Keep `ensure_text_fts_index` (`:426`) as a harmless fallback (no-op if index exists).
- **Graceful fallback:** in `_search_one_table`'s hybrid branch (`:492`) / `search_v2`,
  catch the specific "Cannot perform full text search" / missing-FTS error and
  re-run vector-only with an `advisories[]` note ("FTS index missing on <table>;
  fell back to vector-only; reindex to enable hybrid"). No raw Lance error to the user.

### 3. `mcp_v2.py` (modified)
- Surface the fallback advisory through `SearchOutput.advisories` (`:492`).

## Tests for PR-SEARCH-3
`tests/test_search_lancedb_capability.py`: extend the tmp-Lance-table pattern for
yaml/sql rows + hybrid. `tests/test_lancedb_e2e.py` (heavy-gated): add a
hybrid-on-yaml e2e asserting shape (not scores). New unit test: missing-FTS →
graceful vector-only + advisory (mock the FTS error).

## Definition of done (PR-SEARCH-3)
- [ ] After a fresh `init`, `jrag search "server port" --table yaml --hybrid` returns
      results (no Lance error).
- [ ] An old index without FTS yields a clean advisory + vector-only results, exit ok.
- [ ] `optimize_lance_tables` creates FTS for java/sql/yaml.
- [ ] PR title: `feat(index): build FTS index at index time + graceful hybrid fallback (PR-SEARCH-3)`.

> **User action required:** existing indexes need one `reprocess` to get FTS on
> yaml/sql. Call out in the PR description and `docs/CONFIGURATION.md`.

---

# PR-SEARCH-4 — Zero-result guidance + `--limit 0` fix + housekeeping

**Goal:** stop the silent-zero footgun; fix `--limit 0`; tidy error paths and stderr.

**Key facts (verified):**
- `--limit 0` → `truncated:true` with no nodes: `mark_truncated(rows, 0)` returns
  `([], True)` (`jrag_envelope.py:353`). Fix in `_cmd_search` (a test,
  `test_jrag_envelope.py:426–430`, pins the **helper's** current behavior — so do not
  touch `mark_truncated`).
- Role-filtered silent zero: `search "audit" --role SERVICE` → 0 because audit code
  lives in `COMPONENT`/`OTHER`. Filter works (verified) — there's just no hint.
- MCP `table="all"+hybrid`: CLI rejects cleanly, but `search_v2` → `run_search` raises
  raw `ValueError` (`search_lancedb.py:836–839`) → `success=false` with an ugly message.
- Lance Rust FTS deprecation warnings leak past `_suppress_runtime_stderr_noise`
  (`jrag.py:4170`).

## File-by-file changes

### 1. `java_codebase_rag/jrag.py` (modified)
- **`--limit 0`:** short-circuit in `_cmd_search` around `:4045`/`:4146` — when
  `limit==0`, return `Envelope(status="ok", nodes={}, truncated=False)`.
- **Zero-result guidance:** when `search_v2` returns 0 hits AND a role/service/module
  filter was applied, run one cheap unfiltered probe (small limit) to find which
  `role` values contain matches; emit a `warnings[]` line:
  `"0 results with --role SERVICE; N matches exist under COMPONENT/OTHER — try --role COMPONENT"`.
  Cap the probe cost (limit 10). Gate behind "filter present AND empty."

### 2. `mcp_v2.py` + `server.py` (modified)
- Clean the `table="all"+hybrid` error: validate in `search_v2` (before `run_search`)
  and return `SearchOutput(success=False, message="hybrid requires a single table;
  use java/sql/yaml, not all")` instead of letting `ValueError` propagate.

### 3. `jrag.py` `_suppress_runtime_stderr_noise` (`:4170`) (modified)
- Also filter the Lance `Deprecation warning … did not include _score/_distance` lines.

## Tests for PR-SEARCH-4
`tests/test_jrag_orientation.py`: `--limit 0` → `truncated:false`, empty; zero-result
guidance emits the role hint (mock `search_v2`: filtered → empty, unfiltered → hits
with role COMPONENT). `tests/test_mcp_v2.py`: `table="all"+hybrid` → clean
`success=false` message. `tests/test_jrag_envelope.py:426–430` stays green (untouched).

## Definition of done (PR-SEARCH-4)
- [ ] `jrag search "x" --limit 0` → `{"status":"ok","truncated":false}`, no nodes.
- [ ] `jrag search "audit" --role SERVICE` → `0 search` + a warning naming COMPONENT.
- [ ] MCP `table="all"+hybrid` → clean error message.
- [ ] No Lance deprecation warnings on stderr during hybrid search.
- [ ] PR title: `fix(search): zero-result guidance, --limit 0, clean all+hybrid error (PR-SEARCH-4)`.

---

# PR-SEARCH-5 — REPOSITORY role for JPA interfaces

**Goal:** JPA repository interfaces (`extends JpaRepository` etc.) resolve to
`REPOSITORY` instead of `OTHER`, enabling `--role REPOSITORY` / `find --role REPOSITORY`.

**Key facts (verified):**
- `REPOSITORY` already exists in `ROLE_ANNOTATIONS` (`ast_java.py:95`), `VALID_ROLES`
  (`java_ontology.py:22`), `mcp_v2.Role` (`:75`), `_ROLE_SCORE_WEIGHTS`
  (`search_lancedb.py:188`). **No enum changes needed.**
- Gap: `infer_role_for_type` (`ast_java.py:2741–2777`) has **no supertype scan** —
  classes/interfaces with no role annotation fall through to OTHER. Mirror the
  capability supertype scan at `:2811–2814`.
- cocoindex content-memoizes chunks → role-inference changes don't auto-re-tag;
  bump `ONTOLOGY_VERSION` (`:87`) to signal reprocess + trip the Ladybug stale check
  (`ladybug_queries.py:378`).

## File-by-file changes

### 1. `ast_java.py` (modified)
- In `infer_role_for_type`, just before `return "OTHER"` (`:2777`): scan
  `(*type_decl.extends, *type_decl.implements)` simple names against
  `{JpaRepository, CrudRepository, PagingAndSortingRepository,
  ReactiveCrudRepository, ListCrudRepository}` → return `"REPOSITORY"`. Pattern after
  the capability scan at `:2811–2814`.
- Bump `ONTOLOGY_VERSION` (`:87`) (e.g. 17 → 18).

## Tests for PR-SEARCH-5
`tests/` AST role-inference test: a type extending `JpaRepository` → `REPOSITORY`;
a plain `@Repository`-annotated type still → `REPOSITORY`; a non-repo interface stays
its inferred role. (No e2e needed; this is AST-level.)

## Definition of done (PR-SEARCH-5)
- [ ] `infer_role_for_type` returns `REPOSITORY` for `extends JpaRepository`.
- [ ] `ONTOLOGY_VERSION` bumped.
- [ ] PR title: `feat(ontology): REPOSITORY role for JPA interfaces (PR-SEARCH-5)`.

> **User action required:** `reprocess` to re-tag existing chunks.

---

# Phase 1–4 roadmap (skeletons — expand per phase before implementation)

**Phase 1 — Relevance.** (a) Identifier auto-hybrid: backend `auto_hybrid` already
exists (`search_lancedb.py:809/825–833`, uses `looks_like_code_identifier`) — expose
+ make default for identifier-shaped queries (`jrag.py` subparser, `search_v2`,
`server.py`). Fixes the weak `search "TransferService"` case. (b) Code-aware
tokenization: extend identifier splitting for `@Annotation`, generics, `UPPER_CASE`
to feed `_apply_symbol_bonus` (`:325`). (c) Opt-in cross-encoder re-rank (`--rerank`).
(d) Multi-query fan-out via `_rrf_merge` (`:761`).

**Phase 2 — Graph-native (differentiator).** (a) `--graph-expand`: backend
`graph_expand`/`expand_depth` already exist (`search_lancedb.py:816`,
`_graph_expand_merge` `:678`) — expose via CLI/MCP. (b) Search→traverse one-shot
(pipe top hit into `callers`/`callees`). (c) `--like <chunk_id>` search-by-example
(reuse a hit's embedding as the query vector).

**Phase 3 — Ergonomics.** (a) Inline operators parsed into `NodeFilter`
(`role:service path:kafka service:chat-assign`). (b) `--summary` (result distribution
by role/service). (c) `--format compact`. (d) Drill-down hints beyond the top 2
(`_inspect_hints_for_rows` `jrag.py:1832`). (e) Optional streaming for large pages.

**Phase 4 — Performance.** (a) Persistent embedding daemon (kills per-invocation
SBERT load; MCP already caches via `_get_sentence_transformer`). (b) Optional
code-tuned embedder.

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | Dedup shrinks pages / breaks +1-fetch truncation | High | Over-fetch in `run_search` (`need*4`); dedup before window; re-derive truncation sentinel post-dedup. Unit-test offset+dedup. |
| 2 | Score normalization shifts `--min-score` meaning | Med | Land 1a first; re-baseline `test_search_min_score…` (`:361`); document the new 0–1 scale in `docs/`. |
| 3 | 1a and 3 both touch `search_lancedb.py:518` | Med | Land 1a before 3; 3 adds graceful fallback around the same site. |
| 4 | Honest-score clamp hides genuine >1 bonus signal | Low | Clamp at 1.0 but keep raw components in `--explain`. |
| 5 | FTS-at-index-time lengthens `init` | Low | FTS build is fast on these table sizes; measure in e2e. |
| 6 | REPOSITORY inference false positives (a type extending a non-JPA `Repository`-named iface) | Low | Match the known Spring Data simple-name set only; users can override via `@CodebaseRole`/`role_overrides`. |

# Out of scope (Phase 0)
- Removing `--fuzzy` (keep; low value, breaks a test).
- New embedding model / cross-encoder (Phase 1/4).
- Graph expansion exposure (Phase 2).
- Streaming, inline operators, summary (Phase 3).

# Whole-plan (Phase 0) done definition
1. Scores are honest (rank-monotonic) and on a unified 0–1 scale; `--explain` shows why.
2. Default output is one row per symbol; `--chunks` restores chunk-level.
3. `--hybrid` works on all tables after reindex; old indexes degrade gracefully.
4. `--limit 0` is clean; role-filtered empties give guidance; `all+hybrid` errors cleanly.
5. JPA interfaces resolve to `REPOSITORY`.
6. Full suite green; e2e shape-tests untouched per the anti-overfitting rule.

# Verification (end-to-end, on `tests/bank-chat-system`)
```bash
# rebuild index (FTS at index time + REPOSITORY re-tag)
.venv/bin/java-codebase-rag init --source-root tests/bank-chat-system   # or reprocess
cd tests/bank-chat-system

# honest + normalized scores (should descend; hybrid comparable to vector)
../../.venv/bin/jrag search "where are messages published to kafka"
../../.venv/bin/jrag search "server port" --table yaml --hybrid
../../.venv/bin/jrag search "TransferService"            # identifier still weak until Phase 1
../../.venv/bin/jrag search "audit" --explain            # shows dist/role/symbol/rrf

# dedup (ChatKafkaConfiguration once, chunks=N) + opt-out
../../.venv/bin/jrag search "kafka configuration"
../../.venv/bin/jrag search "kafka configuration" --chunks

# guidance + limit-0 + clean error
../../.venv/bin/jrag search "audit" --role SERVICE       # 0 + hint naming COMPONENT
../../.venv/bin/jrag search "x" --limit 0                 # truncated:false
../../.venv/bin/jrag search "kafka" --table all --hybrid  # clean error

# REPOSITORY role
../../.venv/bin/jrag find --role REPOSITORY               # now returns repo interfaces
```
Test suite (per AGENTS.md: erase stale manual indexes first):
```bash
rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}
.venv/bin/pip install -e .
.venv/bin/pytest tests/test_search_lancedb.py tests/test_search_lancedb_capability.py \
  tests/test_mcp_v2.py tests/test_jrag_orientation.py tests/test_jrag_render.py \
  tests/test_jrag_envelope.py
# full suite once at the end (slow)
.venv/bin/pytest
```

# Tracking
- PR-SEARCH-1a: _pending_ (foundation)
- PR-SEARCH-1b: _pending_ (blocked by 1a)
- PR-SEARCH-2: _pending_
- PR-SEARCH-3: _pending_ (land after 1a)
- PR-SEARCH-4: _pending_
- PR-SEARCH-5: _pending_ (ontology bump; land alone)

# Notes
- On approval, copy this file to `plans/active/PLAN-SEARCH.md` and generate the
  companion `plans/active/AGENT-PROMPTS-SEARCH.md` (one fenced prompt per PR, modeled
  on `plans/completed/AGENT-PROMPTS-INIT-INCREMENT-PERF.md`; include `@-files`,
  sentinel greps, and pytest invocations per PR).
- PR-SEARCH-2 and PR-SEARCH-3 require user-facing `reprocess`; call out in PR bodies
  and `docs/CONFIGURATION.md` / `docs/JAVA-CODEBASE-RAG-CLI.md`.
