# Hybrid BM25 + Vector Ranking via RRF (fork B, issue #431)

**Status:** Active — design approved 2026-07-12.
**Tracks:** [issue #431](https://github.com/HumanBean17/java-codebase-rag/issues/431).
**Depends on:** Fork A (PR #432, merged) — `Symbol.search_text` column + `sym_fts` LadybugDB FTS index at ontology v19. **Satisfied.**

## Summary

Promote LadybugDB Okapi BM25 from a fallback-only signal (macOS-Intel lexical path) to a first-class, always-on **third input** to the vector/hybrid read path's RRF (Reciprocal Rank Fusion). Exact-identifier lexical matches then anchor dense rankings on the primary path.

```
final_rank = RRF([vector_hits, graph_expand_hits, bm25_hits], k=tuned)
```

## Background & current state

- The vector path (`search/search_lancedb.py`) fuses **2 lists** — `vector_rows` + `graph_rows` — in `_graph_expand_merge` via `_rrf_merge(k=60)`.
- `_rrf_merge` is already N-list generic; the only "2-ness" lives in (a) the caller and (b) the `_HYBRID_SCORE_MAX` constant in `search/search_scoring.py` (`2.0/61.0` term).
- Fork A shipped `Symbol.search_text` + `sym_fts` (ontology v19) and made BM25 the primary path in `search/search_lexical.py` via `_try_fts_candidates(g, query, filter, path_contains)`, returning `{rows, bm25_scores}`. The hand-rolled token-overlap scan (PR #403) survives only as the lexical fallback.
- `absence_diagnosis` is orthogonal (vocabulary q-gram + difflib did-you-mean); it does not consume BM25 today.
- No eval harness exists in-repo.

## Goals

- Add BM25 as an always-on third RRF list on the vector path.
- Make ranking config (`{list-set, k}`) runtime-injectable.
- Build a recall/precision @k + MRR eval harness over `~/jrag-bench/shopizer`.
- Tune RRF `k` for the 3-list fusion on that eval; ship the winner.
- Surface a `bm25` score component on the vector path under `explain=True`.

## Non-goals

- No change to `SearchHit` / `SearchOutput` / `NodeFilter` schemas (`score_components` is an open dict).
- No change to `absence_diagnosis` (deferred — see Follow-ups).
- No change to the `sym_fts` index or `search_text` column (fork A).
- No change to the LanceDB-internal `.text(fts_text)` single-table hybrid mode.
- No physical unification of the lexical-only and vector code paths (deferred).
- No `--no-vectors` distribution (deferred).
- No new operator-facing CLI flag for rank config (the injection is internal).

## Architecture & data flow

**Current:**
```
search_v2 → search_lancedb.run_search
  → vector query (LanceDB)       → vector_rows
  → graph-expand (LadybugGraph)  → graph_rows
  → _rrf_merge([vector_rows, graph_rows], k=60)
```

**Proposed:**
```
search_v2 → search_lancedb.run_search
  → vector query (LanceDB)              → vector_rows
  → graph-expand (LadybugGraph)         → graph_rows
  → BM25 FTS query (LadybugGraph sym_fts) → bm25_rows   [always-on]
  → _rrf_merge([vector_rows, graph_rows, bm25_rows], k=tuned)
```

- The BM25 fetch reuses `search_lexical._try_fts_candidates` — the same code the lexical backend uses. **No FTS-query, tokenizer, or filter-translation logic is duplicated.** `SYMBOL_FTS_INDEX` (search_scoring.py) remains the single source of truth for the index name.
- **Symbol→chunk resolution:** BM25 candidates are `Symbol` nodes; `_rrf_merge` dedups on `(filename, range_start, range_end)`. BM25 Symbols resolve to chunk(s) via the same expansion `graph_rows` already use. Orphaned Symbols (no chunk) are dropped. A Symbol mapping to multiple chunks expands to all (BM25 is a per-Signal, per-Symbol rank position).
- **FTS unavailable / empty query / over-restrictive filter:** the BM25 list is empty; 3-list `_rrf_merge` with one empty list degrades cleanly to current 2-list behavior. **No exception, no advisory.** This covers the airgapped case silently.
- **macOS-Intel lexical-only path:** behavior unchanged in this PR. Conceptually "hybrid with an empty vector list," but not physically unified (deferred).

## Components

| File | Change |
|---|---|
| `search/search_lancedb.py` | `_graph_expand_merge` (or a new sibling helper) calls `search_lexical._try_fts_candidates` → `bm25_rows`; resolves Symbol→chunk; passes `[vector_rows, graph_rows, bm25_rows]` to `_rrf_merge`. Reads `{list-set, k}` from an injected rank config (default = 3-list, tuned-k). Populates `row["_score_components"]["bm25"]`. |
| `search/search_scoring.py` | `_HYBRID_SCORE_MAX` RRF term derived from list count (`num_lists/(k+1)`) instead of hard-coded `2.0/61.0`. `explain_score_components` gains a `bm25=` token in the hybrid branch. |
| `search/search_lexical.py` | No behavioral change. (Possibly expose `_try_fts_candidates` for reuse; minimal.) |
| `eval/` (new package) | New top-level package + CLI runner. Indexes shopizer into a temp dir; builds Tier-A ground truth; runs 2-list baseline, 3-list candidate, and k-sweep; writes Markdown + JSON results. |

**Score components (vector path, `explain=True`):** adds `bm25` (raw Okapi BM25 score; `0` if the hit did not come from the BM25 list), alongside existing `rrf_raw`, `hybrid_rrf`, `role_weight`, `symbol_bonus`, `import_penalty`.

**Rank-config contract (internal):** `_graph_expand_merge` accepts a config selecting list-set ∈ `{{vector,graph}, {vector,graph,bm25}}` and integer `k`. Production default = `{vector,graph,bm25}` at the tuned k. The eval and tests inject alternatives.

## Eval harness

- **Location:** new `eval/` package (not under `tests/`); invoked via a runner entry point. Not a CI pass/fail gate.
- **Corpus:** `~/jrag-bench/shopizer`, indexed into a temp dir (fresh, never committed), mirroring `tests/conftest.py` hygiene.
- **Ground truth — Tier A (auto-generated, ships with harness):** for each indexed `Symbol`, queries derived from name/FQN tokens (e.g. `"DistributionChunkService"`, `"distribution chunk service"`), `relevant = {that symbol}`. Deterministic, free, no manual authoring. Tests the identifier regime BM25 should dominate.
- **Ground truth — Tier B (user-authored, optional):** `~/jrag-bench/shopizer/ground_truth.{json,yaml}` of natural-language intent queries → relevant symbols. Loaded if present; reported separately.
- **Metrics:** Recall@k (k ∈ {1,5,10,20}); Precision@k; **MRR (primary)**; recall@10 as no-regression guardrail; recall@1 as tiebreak. Binary relevance (graded/NDCG deferred).
- **Comparisons (single pass, one index):** 2-list @ k=60 (baseline); 3-list @ k ∈ {30,60,90,120}. Latency (p50 ms) measured per config.
- **Output:** Markdown table + raw JSON to `~/jrag-bench/shopizer/results/<timestamp>/`.
- **Win criterion:** ship the 3-list config at the k maximizing **MRR** with no regression on recall@10 vs baseline. Negative results are reported honestly, not hidden; if no k beats baseline, ship anyway (lexical-first-class is the goal) and open a follow-up.

## Testing

- **Unit (CI):** `_rrf_merge` over 3/N lists (dedup, `num_lists/(k+1)` normalization, empty-list degradation); `_HYBRID_SCORE_MAX` derivation for list counts 1/2/3; `bm25` component population + `explain=True` gating; `explain_score_components` `bm25=` token; filter-respect (BM25 Symbol outside `NodeFilter` dropped); rank-config injection honoring injected `{list-set, k}`.
- **Integration (CI):** no-regression on existing `tests/search/` fixtures; FTS-unavailable on vector path → silent 2-list degradation (monkeypatch FTS load, assert no exception/advisory).
- **Eval-harness (CI, lightweight):** Tier-A generator deterministic on a fixture; recall@k & MRR math unit-tested against hand-computed cases; full-harness smoke run on a tiny fixture asserts *output produced* (not specific ranking numbers).
- **Not CI-gated:** the actual shopizer recall/MRR numbers (research artifacts, non-hermetic).
- **Baseline:** develop against the search subset; full suite once at end.

## Error handling & latency

- FTS unavailable / degenerate query / over-restrictive filter → empty BM25 list → silent degradation to 2-list. No new exceptions, exit codes, or advisories.
- `QUERY_FTS_INDEX` is a DB-side indexed query returning `top:=200` rows, comparable to the existing graph-expand hop. The eval measures the per-search latency delta (2-list vs 3-list); gating/caching deferred pending that measurement.

## Deferred / follow-ups (GitHub issues to be opened)

1. BM25-fed did-you-mean in `absence_diagnosis` (with its own MRR eval).
2. Physically unify the lexical-only and vector read paths ("hybrid with empty vector list").
3. `--no-vectors` distribution mode.
4. Airgapped/bundled FTS install path (`INSTALL FTS` fetches from `extension.ladybugdb.com` on first use).
5. NDCG@k with graded relevance.
6. Latency gating (query-shape) or BM25-result caching, if the eval shows material cost.

## References

- `search/search_lancedb.py:_graph_expand_merge` (RRF caller), `:_rrf_merge` (N-list core).
- `search/search_scoring.py:_HYBRID_SCORE_MAX`, `:SYMBOL_FTS_INDEX`, `:explain_score_components`, `:build_fts_query`.
- `search/search_lexical.py:_try_fts_candidates`, `:_ensure_fts_loaded`, `:_CANDIDATE_LIMIT_CAP`.
- `graph/build_ast_graph.py:_compute_symbol_search_text`, `:_ensure_symbol_fts_index`.
- `ast/ast_java.py:ONTOLOGY_VERSION` (=19).
- `mcp/mcp_v2.py:SearchHit`, `:SearchOutput`, `:NodeFilter`, `:_row_to_search_hit`.
- `absence/absence_diagnosis.py:diagnose`.
