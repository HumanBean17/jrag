# Hybrid BM25 + Vector RRF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote LadybugDB Okapi BM25 to a first-class, always-on third RRF list on the vector/hybrid search read path, and build a recall/precision@k + MRR eval harness to measure and tune it.

**Architecture:** The vector path (`search_lancedb._graph_expand_merge`) currently fuses 2 lists (vector + graph-expand) via the N-list-generic `_rrf_merge`. We add a third list — BM25 candidates from the existing `search_lexical._try_fts_candidates` helper (fork A, merged) — resolved to chunk rows in BM25 rank order, filter the result through the same LanceDB predicates as the vector path, and fuse with RRF. A new dep-free `RankConfig` makes `{lists, rrf_k}` runtime-injectable so the eval can A-B 2-list vs 3-list and sweep `k`. The `score_components["bm25"]` entry surfaces under `explain=True`.

**Tech Stack:** Python 3.11 (`.venv/bin/python`, editable install), LadybugDB 0.17.1 FTS (Okapi BM25) via the `sym_fts` index, LanceDB 0.34, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/active/2026-07-12-hybrid-bm25-rrf-design.md` (commit `8368e71`). **Issue:** #431. **Deferred follow-ups:** #434–#439.

## Global Constraints

- Use `.venv/bin/python` and `.venv/bin/pip` only — never system `python`/`pip`. Editable install only; if pytest complains about a stale install, run `.venv/bin/pip install -e ".[dev]"`.
- `search/search_scoring.py` MUST NOT import lancedb / torch / sentence_transformers / cocoindex — it is imported on graph-only (macOS Intel) installs where those are absent. `RankConfig` and any new metric helpers that the eval imports without the vector stack MUST live there (or another dep-free module).
- `SYMBOL_FTS_INDEX = "sym_fts"` (`search_scoring.py:20`) is the single source of truth for the FTS index name — never inline the string.
- Erase stale manual indexes before running tests: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`. Tests build their own index in a temp dir; never commit one under `tests/`.
- Develop against the search subset (`-k "lexical or search or hybrid or rrf"`); run the full suite once at the end.
- `SearchHit` / `SearchOutput` / `NodeFilter` schemas (`mcp/mcp_v2.py`) are UNCHANGED — `score_components` is an open dict.
- FTS unavailable must degrade SILENTLY to 2-list ranking on the vector path: no exception, no advisory, no exit-code change.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `src/java_codebase_rag/search/search_scoring.py` | Dep-free scoring/dedup primitives. Add `RankConfig` dataclass; derive `_HYBRID_SCORE_MAX` RRF term from list count; add `bm25=` token to `explain_score_components`. | Modify |
| `src/java_codebase_rag/search/search_lancedb.py` | Vector/hybrid read path. Add `_bm25_candidate_rows` helper; thread `rank_config` through `run_search` → `_graph_expand_merge`; pass BM25 list to `_rrf_merge`. | Modify |
| `src/java_codebase_rag/search/search_lexical.py` | Lexical backend. Possibly widen visibility of `_try_fts_candidates` for cross-module reuse. | Modify (minimal) |
| `src/java_codebase_rag/eval/__init__.py` | Eval package marker. | Create |
| `src/java_codebase_rag/eval/metrics.py` | Dep-free IR metrics: recall@k, precision@k, MRR. | Create |
| `src/java_codebase_rag/eval/ground_truth.py` | Tier-A auto ground-truth generator (Symbol→query). Tier-B YAML/JSON loader. | Create |
| `src/java_codebase_rag/eval/runner.py` | Build shopizer index, run 2-list baseline + 3-list k-sweep via injected `RankConfig`, write Markdown + JSON results. | Create |
| `tests/search/test_search_scoring.py` | Unit tests for `RankConfig`, `_HYBRID_SCORE_MAX` derivation, `bm25=` token. | Modify |
| `tests/search/test_search_lancedb.py` | Unit + integration tests for `_bm25_candidate_rows`, rank-config injection, FTS-unavailable degradation. | Modify |
| `tests/eval/test_metrics.py`, `tests/eval/test_ground_truth.py`, `tests/eval/test_runner.py` | Unit tests for eval harness machinery (metric math, generator determinism, runner smoke). | Create |

---

## Task 1: Derive `_HYBRID_SCORE_MAX` from RRF list count

**Files:**
- Modify: `src/java_codebase_rag/search/search_scoring.py:87-93`
- Test: `tests/search/test_search_scoring.py`

**Interfaces:**
- Produces: a new module-level function `_rrf_max(num_lists: int, k: int = 60) -> float` returning `num_lists / (k + 1)`. `_HYBRID_SCORE_MAX` is redefined to use `_rrf_max(2)` (preserving today's exact `2.0/61.0` value) PLUS the unchanged bonus terms (`max(_ROLE_SCORE_WEIGHTS.values()) + _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS`). The numeric value of `_HYBRID_SCORE_MAX` MUST stay byte-identical to today (it still describes the shipped 2-list hybrid display path until Task 4 flips production to 3-list).
- Consumes: nothing new.

- [ ] **Step 1: Write failing tests**

In `tests/search/test_search_scoring.py` add:

(a) `test_rrf_max_formula`: assert `_rrf_max(2, 60)` equals `2.0 / 61.0` (within 1e-12); `_rrf_max(3, 60)` equals `3.0 / 61.0`; `_rrf_max(3, 30)` equals `3.0 / 31.0`.

(b) `test_hybrid_score_max_unchanged`: assert the module attribute `_HYBRID_SCORE_MAX` equals the literal `(2.0/61.0) + max(_ROLE_SCORE_WEIGHTS.values()) + _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS` computed in the test from the same imported constants — i.e. the refactor introduces no numeric drift.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/search/test_search_scoring.py::test_rrf_max_formula -v` (and the second test).
Expected: FAIL — `_rrf_max` not defined (ImportError/AttributeError).

- [ ] **Step 3: Implement**

Add `_rrf_max(num_lists, k=60)` returning `num_lists / (k + 1)`. Redefine `_HYBRID_SCORE_MAX` to call `_rrf_max(2)` for the RRF term; leave the bonus terms and the surrounding comment unchanged except to note the RRF term is now derived. Do not change any other constant.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/search/test_search_scoring.py -v`
Expected: PASS (both new tests + all existing scoring tests).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/search/search_scoring.py tests/search/test_search_scoring.py`
Run: `git commit -m "refactor(scoring): derive _HYBRID_SCORE_MAX RRF term from list count"`

---

## Task 2: Add `RankConfig` and thread it through the read path

**Files:**
- Modify: `src/java_codebase_rag/search/search_scoring.py` (add `RankConfig`)
- Modify: `src/java_codebase_rag/search/search_lancedb.py:635-646` (`_graph_expand_merge` signature), `:887-897` (`run_search` call site), and `run_search` signature (line ~764)
- Test: `tests/search/test_search_scoring.py`, `tests/search/test_search_lancedb.py`

**Interfaces:**
- Produces (in `search_scoring.py`, dep-free):
  - `@dataclass(frozen=True) class RankConfig:` with fields:
    - `lists: frozenset[str]` — subset of `{"vector", "graph", "bm25"}`. Must contain `"vector"`. Validation in `__post_init__`: raise `ValueError` if `"vector"` not in `lists`, or if any element is outside the allowed set, or if `lists` is empty.
    - `rrf_k: int = 60` — must be `>= 1` (else `ValueError`).
  - `DEFAULT_RANK_CONFIG = RankConfig(lists=frozenset({"vector", "graph", "bm25"}), rrf_k=60)` — production default. (3-list is the shipped behavior after Task 4; until Task 4 lands the BM25 list, the `"bm25"` element is honored but yields an empty list, so behavior is effectively 2-list. This is intentional — Task 4 only adds the non-empty BM25 list.)
  - `BASELINE_2LIST_CONFIG = RankConfig(lists=frozenset({"vector", "graph"}), rrf_k=60)` — eval convenience.
- Produces (in `search_lancedb.py`):
  - `_graph_expand_merge` gains keyword-only param `rank_config: RankConfig = DEFAULT_RANK_CONFIG`. It passes `k=rank_config.rrf_k` into `_rrf_merge`. It fuses exactly the lists named in `rank_config.lists` (vector always present; graph present unless omitted; bm25 handled in Task 4, empty until then).
  - `run_search` gains keyword-only param `rank_config: RankConfig = DEFAULT_RANK_CONFIG`, forwarded to `_graph_expand_merge`.
- Consumes: Task 1's `_rrf_max`.

- [ ] **Step 1: Write failing tests**

(a) In `test_search_scoring.py`:
- `test_rank_config_defaults`: `DEFAULT_RANK_CONFIG.lists == frozenset({"vector","graph","bm25"})`, `.rrf_k == 60`.
- `test_rank_config_validation`: constructing `RankConfig(lists=frozenset({"graph"}))` raises `ValueError` (no vector); `RankConfig(lists=frozenset({"vector","nope"}))` raises `ValueError` (unknown list); `RankConfig(lists=frozenset({"vector"}), rrf_k=0)` raises `ValueError`.
- `test_rank_config_frozen`: mutating `DEFAULT_RANK_CONFIG.rrf_k = 5` raises `FrozenInstanceError`.

(b) In `test_search_lancedb.py`:
- `test_graph_expand_merge_honors_injected_k` (monkeypatch-based): construct a tiny scenario where `_graph_expand_merge` is called with `rank_config=RankConfig(lists=frozenset({"vector","graph"}), rrf_k=30)`; assert the fused rows' `_score_components["rrf_raw"]` normalization reflects `k=30` (i.e. max = `2/31`), proving `k` is injected. Use the existing fixture/monkeypatch pattern already present in this test file for `_graph_expand_merge`; if none exists, build the minimal stub `LadybugGraph` + `_search_one_table` doubles the file already uses elsewhere.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/search/test_search_scoring.py::test_rank_config_defaults tests/search/test_search_lancedb.py::test_graph_expand_merge_honors_injected_k -v`
Expected: FAIL — `RankConfig` not defined / `rank_config` param not accepted.

- [ ] **Step 3: Implement**

Add the `RankConfig` dataclass + the two module constants in `search_scoring.py` (with `from dataclasses import dataclass`). In `search_lancedb.py`: add the `rank_config` keyword-only param to `_graph_expand_merge` and `run_search`; pass `k=rank_config.rrf_k` to `_rrf_merge` in `_graph_expand_merge`; forward the param at the call site (line ~888). Do NOT yet add the BM25 list (Task 4) — this task only plumbs injection and keeps behavior identical to today because the default omits no behavior the current code has (graph still fuses; bm25 not yet fetched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/search/ -k "scoring or lancedb or hybrid" -v`
Expected: PASS — including the new tests, with zero regressions (behavior unchanged).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/search/search_scoring.py src/java_codebase_rag/search/search_lancedb.py tests/search/test_search_scoring.py tests/search/test_search_lancedb.py`
Run: `git commit -m "feat(search): injectable RankConfig for RRF list-set and k"`

---

## Task 3: Add `bm25=` token to `explain_score_components`

**Files:**
- Modify: `src/java_codebase_rag/search/search_scoring.py:342-393`
- Test: `tests/search/test_search_scoring.py`

**Interfaces:**
- Produces: `explain_score_components` emits a `bm25=<value>` token when called with `hybrid=True` and the component dict contains a truthy `"bm25"` key. Format: `bm25={float(value):.3f}`. It is appended after the existing `rrf=` token (within the `elif hybrid:` branch) and before the shared role/symbol/import tokens. When `"bm25"` is absent or zero, no token is emitted (consistent with how `symbol_bonus`/`role_weight` are conditionally shown).
- Consumes: nothing new.

- [ ] **Step 1: Write failing tests**

In `test_search_scoring.py`:
- `test_explain_bm25_token_present`: `explain_score_components({"rrf_raw": 0.03, "bm25": 12.5}, hybrid=True)` returns a string containing `"rrf=0.030"` AND `"bm25=12.500"` (order: rrf before bm25).
- `test_explain_bm25_token_absent_when_zero_or_missing`: `explain_score_components({"rrf_raw": 0.03}, hybrid=True)` does NOT contain `"bm25="`; same for `{"rrf_raw": 0.03, "bm25": 0.0}`.
- `test_explain_bm25_only_in_hybrid`: `explain_score_components({"bm25": 12.5}, lexical=True)` must NOT emit a `bm25=` token (the lexical branch keeps its existing `relevance=`/`name=` tokens — `bm25=` is hybrid-only).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/search/test_search_scoring.py::test_explain_bm25_token_present -v`
Expected: FAIL — no `bm25=` token produced.

- [ ] **Step 3: Implement**

In the `elif hybrid:` branch of `explain_score_components`, after appending the `rrf=` token, read `comps.get("bm25")` and, when truthy, append `f"bm25={float(bm25):.3f}"`. No other branch changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/search/test_search_scoring.py -v`
Expected: PASS (new + existing explain tests).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/search/search_scoring.py tests/search/test_search_scoring.py`
Run: `git commit -m "feat(scoring): surface bm25= token in hybrid explain output"`

---

## Task 4: BM25 candidate fetch + wire as third RRF list

**Files:**
- Modify: `src/java_codebase_rag/search/search_lancedb.py` (new `_bm25_candidate_rows` near `_graph_expand_merge` line 635; edits inside `_graph_expand_merge` at 709-716; default config already 3-list from Task 2)
- Modify: `src/java_codebase_rag/search/search_lexical.py:210` (widen visibility of `_try_fts_candidates` if needed for cross-module import — e.g. expose a thin public alias, do not change behavior)
- Test: `tests/search/test_search_lancedb.py`

**Interfaces:**

- Produces (in `search_lancedb.py`):
  - `def _bm25_candidate_rows(*, g: "LadybugGraph", query: str, uri: str, db: object, extra_predicates: list[str], columns: list[str]) -> list[dict]:`
    - Behavior:
      1. Call `search_lexical._try_fts_candidates(g, query, filter=None, path_contains=None)`. If it returns `None` (FTS extension/index unavailable) OR its `"rows"` is empty, return `[]`.
      2. From the result: `"scores"` is `{symbol_node_id: bm25_float}`, `"rows"` is a list of Symbol dicts each carrying at least `id` and a fully-qualified name field (the Symbol FQN — use the same key the lexical backend exposes for the FQN; confirm against `_SYMBOL_RETURN` in `search_lexical.py`). Build `ordered_fqns`: the distinct FQNs of the returned symbols, sorted by their BM25 score descending (ties broken by FQN ascending for determinism). Build `fqn_to_bm25: dict[str,float]` mapping each FQN to its max BM25 score among its symbols.
      3. Fetch chunk rows from LanceDB for exactly those FQNs, filter-respecting: build `preds = list(extra_predicates) + _build_extra_predicates(columns=columns, fqn_in=ordered_fqns)` and query the `java` table WITHOUT a vector ranking (filter-only `.where()` query through the same table-access pattern `_search_one_table` uses to open the table — but omitting the vector search so the rows are not re-ranked by similarity). Return columns must include at least `filename`, `range_start`, `range_end`, `primary_type_fqn`, plus whatever `_apply_chunk_hints` / `_refine_java_start_lines` need (mirror the column set `_search_one_table` returns for `java`).
      4. Group fetched chunks by `primary_type_fqn`. Emit chunk rows ordered by the BM25 rank of their owning FQN: iterate `ordered_fqns`; for each FQN that has chunks, emit its chunks in their natural table order. Each emitted chunk row is the same dict shape as a `vector_row`/`graph_row` and additionally has `_score_components["bm25"] = round(float(fqn_to_bm25[fqn]), 4)`. Chunks whose FQN was filtered out by `extra_predicates` are never fetched, so filter parity with the vector path is automatic.
      5. Return the ordered chunk-row list (may be empty).
    - Error handling: any exception from the FTS call or the LanceDB fetch is caught and the function returns `[]` (silent degradation — matches the spec's "FTS failure is a silent no-op on the vector path"). Log via `_debug_ctx` (the existing helper in this module) at debug level.

  - `_graph_expand_merge` change: when `"bm25"` is in `rank_config.lists`, after computing `graph_rows`, call `_bm25_candidate_rows(g=g, query=query, uri=uri, db=db, extra_predicates=extra_predicates, columns=_table_columns(uri, TABLES["java"], db))`. Then, when building the lists for `_rrf_merge`, pass `[vector_rows, graph_rows, bm25_rows]` with `k=rank_config.rrf_k`. The `query` string must be threaded into `_graph_expand_merge` as a new keyword-only param (it is not currently a param — add `query: str` to the signature and forward it from `run_search`, which already has `query`). When `"bm25"` is NOT in `rank_config.lists`, omit the BM25 list entirely (2-list behavior, used by the eval baseline).
  - Note on `graph_rows` presence: if `"graph"` is in `rank_config.lists` the graph list is fetched as today; if omitted, only vector + (optionally) bm25 are fused. The `vector` list is always present (validated by `RankConfig`).

- Consumes: Task 2's `RankConfig` / `DEFAULT_RANK_CONFIG`; `search_lexical._try_fts_candidates`; `search_scoring.SYMBOL_FTS_INDEX`; `_build_extra_predicates`, `_table_columns`, `_debug_ctx`, `TABLES`.

- [ ] **Step 1: Write failing tests**

In `test_search_lancedb.py` (monkeypatch `LadybugGraph`, `_search_one_table`/table access, and `search_lexical._try_fts_candidates` — use the doubling patterns already in this file):

(a) `test_bm25_candidate_rows_orders_by_bm25_score`: stub `_try_fts_candidates` to return symbols for FQNs `["B", "A", "C"]` with BM25 scores `{B: 30, A: 20, C: 10}`; stub the LanceDB chunk fetch to return one chunk per FQN. Assert the returned list is ordered `B, A, C` (BM25 desc) and each row's `_score_components["bm25"]` equals the owning FQN's score (B→30.0, A→20.0, C→10.0).

(b) `test_bm25_candidate_rows_fts_unavailable_returns_empty`: stub `_try_fts_candidates` to return `None`; assert `_bm25_candidate_rows(...)` returns `[]` and no LanceDB fetch is attempted (assert the table-access double is never called).

(c) `test_bm25_candidate_rows_respects_filter`: stub `_try_fts_candidates` to return FQNs `["A","B"]`; pass `extra_predicates=["primary_type_fqn <> 'B'"]` (or the project's equivalent SQL form) and assert only FQN `A`'s chunks are emitted (the predicate is applied at the chunk fetch, so B is filtered).

(d) `test_bm25_candidate_rows_multiple_chunks_per_symbol_preserve_order`: stub `_try_fts_candidates` returning FQN `A` (score 20) and FQN `B` (score 10); stub the chunk fetch to return 2 chunks for A and 1 for B. Assert output order is `[A_chunk1, A_chunk2, B_chunk1]` and all three carry the correct `bm25` component.

(e) `test_graph_expand_merge_includes_bm25_list`: with `rank_config=DEFAULT_RANK_CONFIG` (3-list), monkeypatch `_bm25_candidate_rows` to return a known non-empty chunk list, and assert the fused result contains rows whose `_score_components` includes contributions from the BM25 list (a row appearing ONLY in the BM25 list is present in the fused output and has `_score_components["bm25"]` set).

(f) `test_graph_expand_merge_omits_bm25_when_excluded`: with `rank_config=BASELINE_2LIST_CONFIG`, assert `_bm25_candidate_rows` is never called and the fused result matches today's 2-list behavior.

(g) Integration: `test_run_search_bm25_degrades_silently_when_fts_missing` — run `run_search` against a fixture index whose LadybugDB graph has NO `sym_fts` index (or monkeypatch `_ensure_fts_loaded` → False); assert it returns results with no exception, no row has a `bm25` component, and ranking matches the pre-change 2-list vector path (compare against a baseline snapshot or a `BASELINE_2LIST_CONFIG` run — they must be equal).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/search/test_search_lancedb.py -k "bm25_candidate or graph_expand_merge or run_search_bm25" -v`
Expected: FAIL — `_bm25_candidate_rows` undefined; `query` param not threaded.

- [ ] **Step 3: Implement**

Add `_bm25_candidate_rows` per the Produces contract above. Thread `query` into `_graph_expand_merge` and forward from `run_search`. Modify `_graph_expand_merge` to conditionally fetch + fuse the BM25 list based on `rank_config.lists`. If `_try_fts_candidates` is not importable from `search_lexical` due to its leading underscore, add a thin non-underscore alias in `search_lexical.py` (e.g. `fetch_fts_candidates = _try_fts_candidates`) without changing `_try_fts_candidates` behavior, and import the alias. Apply `_apply_chunk_hints` and `_refine_java_start_lines` to BM25 rows before returning (consistency with graph_rows handling at lines 701-702).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/search/ -k "lexical or search or hybrid or rrf or bm25" -v`
Expected: PASS — all new tests green, no regressions in the existing search subset (104+ passed baseline from worktree setup).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/search/search_lancedb.py src/java_codebase_rag/search/search_lexical.py tests/search/test_search_lancedb.py`
Run: `git commit -m "feat(search): fuse LadybugDB BM25 as third RRF list on vector path"`

---

## Task 5: Eval metrics module (recall@k, precision@k, MRR)

**Files:**
- Create: `src/java_codebase_rag/eval/__init__.py`, `src/java_codebase_rag/eval/metrics.py`
- Test: `tests/eval/__init__.py`, `tests/eval/test_metrics.py`

**Interfaces:**
- Produces (`eval/metrics.py`, dep-free, pure functions):
  - `def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:` — fraction of `relevant` appearing in `retrieved[:k]`. Returns `0.0` if `relevant` is empty. Value in `[0.0, 1.0]`.
  - `def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:` — `|retrieved[:k] ∩ relevant| / k`. Returns `0.0` if `k == 0`. Value in `[0.0, 1.0]`.
  - `def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:` — `1.0 / rank` of the first `retrieved` item that is in `relevant` (1-indexed); `0.0` if none.
  - `def mean(values: list[float]) -> float:` — arithmetic mean, `0.0` for empty.
  - `def aggregate(per_query: list[dict]) -> dict[str, float]:` — given a list of per-query dicts each containing keys `recall@1, recall@5, recall@10, recall@20, precision@5, mrr`, return the mean of each across all queries, keyed identically (e.g. `{"recall@10": 0.42, "mrr": 0.55, ...}`).
- Consumes: nothing.

- [ ] **Step 1: Write failing tests**

In `tests/eval/test_metrics.py`, hand-computed cases:
- `test_recall_at_k`: `retrieved=["a","b","c"], relevant={"b","d"}, k=3` → `0.5` (b found, d not); `k=1` → `0.0`; `relevant=set()` → `0.0`; `k=10` (longer than retrieved) → `0.5`.
- `test_precision_at_k`: `retrieved=["a","b","c"], relevant={"b"}, k=2` → `0.5`; `k=3` → `0.333...`; `k=0` → `0.0`.
- `test_reciprocal_rank`: `retrieved=["a","b","c"], relevant={"b"}` → `0.5`; `relevant={"z"}` → `0.0`; first-position match `relevant={"a"}` → `1.0`.
- `test_mean`: `[1.0, 0.0, 0.5]` → `0.5`; `[]` → `0.0`.
- `test_aggregate`: two per-query dicts `{"recall@10":1.0,"mrr":1.0, ...}` and `{"recall@10":0.0,"mrr":0.0, ...}` aggregate to `recall@10==0.5, mrr==0.5`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/eval/test_metrics.py -v`
Expected: FAIL — module/import not found.

- [ ] **Step 3: Implement**

Create `eval/__init__.py` (empty) and `eval/metrics.py` with the five functions per contract. Pure stdlib only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/eval/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/eval/__init__.py src/java_codebase_rag/eval/metrics.py tests/eval/__init__.py tests/eval/test_metrics.py`
Run: `git commit -m "feat(eval): IR metrics — recall@k, precision@k, reciprocal rank, aggregate"`

---

## Task 6: Eval ground-truth — Tier-A generator + Tier-B loader

**Files:**
- Create: `src/java_codebase_rag/eval/ground_truth.py`
- Test: `tests/eval/test_ground_truth.py`

**Interfaces:**
- Produces (`eval/ground_truth.py`):
  - `@dataclass(frozen=True) class LabeledQuery:` fields `query: str`, `relevant: frozenset[str]` (set of relevant Symbol FQNs), `tier: str` ("A" or "B").
  - `def build_tier_a(symbols: Iterable[SymbolLike]) -> list[LabeledQuery]:` — for each symbol produce queries from its simple name via `search_scoring._split_identifier`: one camelCase-joined identifier form and one space-joined lowercase token form (e.g. for `DistributionChunkService` produce `"DistributionChunkService"` and `"distribution chunk service"`). `relevant` = `frozenset({symbol.fqn})`. Skip symbols whose simple name splits to fewer than 2 tokens or is shorter than 3 chars (noise). `tier="A"`. Deterministic: output sorted by `(query, fqn)`.
  - `def load_tier_b(path: str | Path) -> list[LabeledQuery]:` — parse a YAML (`.yaml`/`.yml`) or JSON (`.json`) file whose schema is a list of `{query: str, relevant: [str, ...]}` objects; return `LabeledQuery(query=..., relevant=frozenset(...), tier="B")`. Raise `FileNotFoundError` if missing (the runner treats absence as "Tier-B disabled", so the runner checks existence before calling). `SymbolLike` is a small structural type (duck-typed) exposing `.fqn: str` and `.name: str` (the simple name).
- Consumes: `search_scoring._split_identifier` (dep-free). YAML via `yaml.safe_load` if PyYAML is already a dependency (check `pyproject.toml`); if not, support JSON only and document that Tier-B YAML requires the existing YAML dep or add it.

- [ ] **Step 1: Write failing tests**

In `tests/eval/test_ground_truth.py`:
- `test_build_tier_a_deterministic`: feed a fixed list of `SymbolLike` (use simple objects/namedtuples with `.fqn`/`.name`) including `name="DistributionChunkService"`; assert the output contains `LabeledQuery("DistributionChunkService", frozenset({fqn}), "A")` and `LabeledQuery("distribution chunk service", frozenset({fqn}), "A")`; assert running twice yields identical lists; assert output is sorted by `(query, fqn)`.
- `test_build_tier_a_skips_noise`: a symbol with `name="A"` (1 char) and one with `name="Do"` (single token after split) produce no queries.
- `test_load_tier_b_yaml` (if YAML available) / `test_load_tier_b_json`: write a temp file with two entries; assert parsed into two `LabeledQuery` with `tier="B"` and correct `relevant` frozensets. Assert `load_tier_b` on a non-existent path raises `FileNotFoundError`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/eval/test_ground_truth.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `eval/ground_truth.py` per contract. Reuse `search_scoring._split_identifier` for query derivation so tokenization parity with the FTS index holds (the index is built from the same splitter). Check `pyproject.toml` for PyYAML before using `yaml`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/eval/test_ground_truth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/eval/ground_truth.py tests/eval/test_ground_truth.py`
Run: `git commit -m "feat(eval): Tier-A auto ground-truth generator + Tier-B loader"`

---

## Task 7: Eval runner — index shopizer, sweep configs, emit results

**Files:**
- Create: `src/java_codebase_rag/eval/runner.py`
- Test: `tests/eval/test_runner.py`

**Interfaces:**
- Produces (`eval/runner.py`):
  - `@dataclass(frozen=True) class EvalConfig:` fields: `corpus_dir: str` (default `~/jrag-bench/shopizer`), `index_dir: str` (temp dir; created by the runner), `results_dir: str` (default `~/jrag-bench/shopizer/results`), `tier_b_path: str | None = None`, `ks: tuple[int, ...] = (30, 60, 90, 120)`, `top_k_metrics: tuple[int, ...] = (1, 5, 10, 20)`, `model_name: str = <repo default SentenceTransformer name>` (read from the same default `run_search` consumers use — confirm against `search_lancedb.run_search`'s `model_name` default).
  - `def run_eval(cfg: EvalConfig) -> EvalReport:` orchestration:
    1. Build a fresh shopizer index into `cfg.index_dir` using the project's existing index entry point (the `jrag`/`java-codebase-rag` CLI index command — invoke programmatically via the function the CLI wraps, not a subprocess, so errors surface). Locate the indexer function by searching the `cli`/pipeline module for the index entry the console script calls.
    2. Open the index for query: obtain the LanceDB `uri`/`db` handles and the LadybugDB graph path the same way `run_search`'s callers do (mirror the MCP `search_v2` wiring in `mcp/mcp_v2.py`). Load a `SentenceTransformer` model once (reuse `search_lancedb`'s model-loading helper).
    3. Build ground truth: enumerate `Symbol` nodes from the graph (mirror `search_lexical`'s symbol enumeration), `build_tier_a(...)`; if `cfg.tier_b_path` exists, `load_tier_b(...)` and concatenate.
    4. For each config to evaluate — `BASELINE_2LIST_CONFIG` (k=60) and `DEFAULT_RANK_CONFIG`-shape at each `k` in `cfg.ks` (i.e. `RankConfig(lists=frozenset({"vector","graph","bm25"}), rrf_k=k)`) — for each `LabeledQuery`: call `search_lancedb.run_search(query=..., uri=uri, table_keys=["java"], limit=max(cfg.top_k_metrics), path_substring=None, model_name=cfg.model_name, model=<loaded>, rank_config=<this config>)`, measure wall-clock per query, map returned hits' `primary_type_fqn` (or `fqn`) to retrieved FQN list, compute per-query recall@k/precision@k/mrr, aggregate.
    5. Return an `EvalReport` dataclass with per-config aggregated metrics + per-config p50 latency in ms.
    6. Persist: write `results/<ISO-timestamp>/report.md` (a Markdown table: rows = configs, columns = recall@1, recall@5, recall@10, recall@20, precision@5, mrr, p50_latency_ms) and `report.json` (raw `EvalReport` as JSON).
- Consumes: Tasks 2/4 (`RankConfig`, `BASELINE_2LIST_CONFIG`, `DEFAULT_RANK_CONFIG`, `run_search` with `rank_config`), Task 5 metrics, Task 6 ground truth, the existing indexer + index-opening + model-loading helpers.

- [ ] **Step 1: Write failing tests**

In `tests/eval/test_runner.py` (do NOT depend on the real shopizer corpus — use a tiny in-repo Java fixture corpus the test suite already indexes, or the smallest existing fixture):
- `test_eval_report_shape`: run `run_eval` against a tiny fixture corpus (point `corpus_dir` at a small fixture under `tests/`, a fresh temp `index_dir`); assert the returned `EvalReport` has one entry per config evaluated (`1 + len(ks)` entries), each entry contains all metric keys, and the latency field is a non-negative float.
- `test_eval_report_persists_files`: after `run_eval`, assert `<results_dir>/report.md` and `<results_dir>/report.json` exist and the Markdown contains a header row with all metric column names and one data row per config.
- `test_eval_tier_b_optional`: with `tier_b_path=None`, the run completes using only Tier-A ground truth (no exception). With `tier_b_path` pointing to a temp file with one entry, the report still produces all configs (Tier-B queries are included but the test only asserts the run completes and shape is correct — not specific numbers).
- A smoke marker test `test_runner_smoke_exists` is NOT needed if the above cover it; numbers are research outputs, never asserted.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/eval/test_runner.py -v`
Expected: FAIL — `runner` module not found.

- [ ] **Step 3: Implement**

Create `eval/runner.py` per contract. Reuse `run_search` directly (not the MCP layer) so `rank_config` is injectable. Keep the indexer/model/index-open wiring thin by delegating to existing helpers; if a helper doesn't exist for programmatic index-open, mirror the minimal calls `mcp_v2.search_v2` makes. Guard the whole run so a missing corpus raises a clear `FileNotFoundError` with the path (operators pointed at `~/jrag-bench/shopizer`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/eval/ -v`
Expected: PASS. (These tests build a real small index and may take longer than unit tests — that's acceptable; they are not part of the fast search subset.)

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/eval/runner.py tests/eval/test_runner.py`
Run: `git commit -m "feat(eval): shopizer recall/precision@k + MRR runner with k-sweep"`

---

## Task 8: Run eval on shopizer, tune k, set default, docs, full suite

**Files:**
- Modify: `src/java_codebase_rag/search/search_scoring.py` (`DEFAULT_RANK_CONFIG.rrf_k` → eval winner)
- Modify: `docs/ARCHITECTURE.md` (HOW: vector read path now 3-list; rank-config injection point)
- Modify: `docs/DESIGN.md` (WHAT/WHY: BM25 promoted to first-class; reference the eval)
- Possibly `docs/CONFIGURATION.md` (only if any operator-visible surface changed — none should; if not, skip)
- Test: full suite

**Interfaces:** none new.

- [ ] **Step 1: Run the eval on the real corpus**

Precondition: `~/jrag-bench/shopizer` exists with the shopizer source. If absent, STOP and report — the operator must clone shopizer there before this task can complete.
Run: `.venv/bin/python -m java_codebase_rag.eval.runner` (or the chosen invocation). Inspect `~/jrag-bench/shopizer/results/<timestamp>/report.md`.

- [ ] **Step 2: Pick the winning k**

Per the spec win criterion: choose the `k` (among 30/60/90/120) maximizing **MRR** with **no regression on recall@10** vs the 2-list baseline; recall@1 is the tiebreak. If no 3-list config beats baseline on MRR, keep `DEFAULT_RANK_CONFIG` at 3-list @ k=60 anyway (lexical-first-class is the goal) and record the result honestly in the docs + the PR description. Open a follow-up issue if negative.

- [ ] **Step 3: Set the default k**

Update `DEFAULT_RANK_CONFIG` in `search_scoring.py` so `rrf_k` equals the winning k (if unchanged from 60, note that explicitly). Commit.

- [ ] **Step 4: Update docs**

Update `docs/ARCHITECTURE.md` read-path description to the 3-list RRF and mention `RankConfig` as the injection point. Update `docs/DESIGN.md` to reflect BM25 as first-class on the primary path, referencing the eval result (winning k + headline metric). Do NOT change operator docs unless an operator-facing surface changed (none did).

- [ ] **Step 5: Run the full suite once**

Erase stale indexes: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (0 failures). Report any failures before proceeding.

- [ ] **Step 6: Commit**

Run: `git add src/java_codebase_rag/search/search_scoring.py docs/ARCHITECTURE.md docs/DESIGN.md`
Run: `git commit -m "feat(search): promote BM25 to first-class hybrid RRF list (issue #431)"`

---

## Self-Review Notes (resolved during authoring)

- **Code leakage:** Steps describe behavior, data shapes, signatures, and exact expected test results — no method bodies or test code.
- **Self-containment:** Every task carries full Consumes/Produces contracts; Tasks 5–7 do not require reading the spec to implement.
- **Spec coverage:** 3-list fusion (T2/T4), `_HYBRID_SCORE_MAX` derivation (T1), `bm25=` explain (T3), filter-respect + FTS-unavailable degradation (T4g), rank-config injection (T2), eval harness with Tier-A/Tier-B + recall/precision/MRR + k-sweep + latency + win criterion (T5–T8) — all spec sections mapped.
- **Type consistency:** `RankConfig.lists`/`rrf_k`, `DEFAULT_RANK_CONFIG`, `BASELINE_2LIST_CONFIG`, `_bm25_candidate_rows`, `EvalConfig`, `EvalReport`, `LabeledQuery` — names used consistently across tasks.
- **Deferred (issues #434–#439):** absence unification, did-you-mean, no-vectors, airgapped FTS, NDCG, latency gating — all explicitly out of scope and filed.
