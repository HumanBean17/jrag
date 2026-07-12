#!/usr/bin/env python3
"""Semantic search over LanceDB tables built by CocoIndex (java_index_flow_lancedb)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import warnings
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import lancedb
import numpy as np
from sentence_transformers import SentenceTransformer

from java_codebase_rag.ast.chunk_heuristics import analyze_chunk, looks_like_code_identifier
from java_codebase_rag.search.index_common import SBERT_MODEL
from java_codebase_rag.search import search_lexical
from java_codebase_rag.config import maybe_expand_embedding_model_path, resolved_sbert_model_for_process_env

# Scoring & dedup primitives live in `search_scoring` (dependency-free — no
# lancedb/torch) so the lexical backend `search_lexical` can share them on
# graph-only (macOS Intel) installs where this module is unimportable. Re-exported
# here for backward compatibility (`from search_lancedb import _clamp01`, etc.).
from java_codebase_rag.search.search_scoring import (  # noqa: F401
    BASELINE_2LIST_CONFIG,
    DEFAULT_RANK_CONFIG,
    DEDUP_OVERFETCH,
    RankConfig,
    build_fts_query,
    _ACTION_VERB_BONUS,
    _ACTION_VERB_PREFIXES,
    _HYBRID_SCORE_MAX,
    _IMPORT_DISTANCE_PENALTY,
    _IMPORT_HYBRID_SCORE_FACTOR,
    _ROLE_SCORE_WEIGHTS,
    _STOPWORDS,
    _SYMBOL_MATCH_BONUS_CAP,
    _SYMBOL_MATCH_BONUS_PER_HIT,
    _TYPE_MATCH_BONUS_CAP,
    _TYPE_MATCH_BONUS_PER_HIT,
    _apply_symbol_bonus,
    _clamp01,
    _dedup_by_fqn,
    _effective_distance,
    _query_tokens,
    _role_weight,
    _split_identifier,
    _symbol_bonus,
    declaration_line_number,
    explain_score_components,
    l2_distance_to_score,
    vector_display_score,
)

TABLES: dict[str, str] = {
    "java": "javacodeindex_java_code",
    "sql": "sqlschemaindex_sql_schema",
    "yaml": "yamlconfigindex_yaml_config",
}

# Optional enrichment columns on the java chunk table (absent on older indexes).
JAVA_ENRICHED_COLUMNS: tuple[str, ...] = (
    "package",
    "module",
    "microservice",
    "primary_type_fqn",
    "primary_type_kind",
    "role",
    "annotations_on_type",
    "symbols",
    "symbol_id",
    "metadata",
    "ontology_version",
    "capabilities",
    "generated",
    "generated_by",
)

VECTOR_COLUMN = "embedding"
_FTS_READY: set[tuple[str, str]] = set()
_FTS_LOCK = threading.Lock()
_SCHEMA_CACHE: dict[tuple[str, str], set[str]] = {}
_SCHEMA_LOCK = threading.Lock()


def _table_columns(uri: str, lance_table_name: str, db_obj: object | None = None) -> set[str]:
    key = (uri, lance_table_name)
    with _SCHEMA_LOCK:
        cached = _SCHEMA_CACHE.get(key)
        if cached is not None:
            return cached
    db = db_obj if db_obj is not None else lancedb.connect(uri)
    tbl = db.open_table(lance_table_name)
    cols = {f.name for f in tbl.schema}
    with _SCHEMA_LOCK:
        _SCHEMA_CACHE[key] = cols
    return cols


def _escape_sql_str(s: str) -> str:
    return s.replace("'", "''")


def _build_extra_predicates(
    *,
    columns: set[str],
    role: str | None,
    module: str | None,
    microservice: str | None,
    package_prefix: str | None,
    fqn_in: list[str] | None,
    role_in: list[str] | None = None,
    exclude_roles: list[str] | None = None,
    capability: str | None = None,
    capability_in: list[str] | None = None,
    generated_only: bool = False,
    exclude_generated: bool = False,
) -> list[str]:
    preds: list[str] = []
    if role and "role" in columns:
        preds.append(f"role = '{_escape_sql_str(role)}'")

    # When both role_in and capability_in are set, combine as OR so that
    # capability-only entrypoints (e.g. role=OTHER with MESSAGE_LISTENER)
    # are not silently excluded by the role filter.
    role_pred: str | None = None
    if role_in and "role" in columns:
        vals = ", ".join(f"'{_escape_sql_str(v)}'" for v in role_in)
        role_pred = f"role IN ({vals})"

    cap_in_pred: str | None = None
    if capability_in and "capabilities" in columns:
        # array_has is the preferred form in LanceDB >= 0.10 (verified against 0.30.2).
        parts = [
            f"array_has(capabilities, '{_escape_sql_str(c)}')"
            for c in capability_in
        ]
        cap_in_pred = "(" + " OR ".join(parts) + ")"

    if role_pred and cap_in_pred:
        preds.append(f"({role_pred} OR {cap_in_pred})")
    elif role_pred:
        preds.append(role_pred)
    elif cap_in_pred:
        preds.append(cap_in_pred)

    if exclude_roles and "role" in columns:
        vals = ", ".join(f"'{_escape_sql_str(v)}'" for v in exclude_roles)
        preds.append(f"(role IS NULL OR role NOT IN ({vals}))")
    if generated_only and "generated" in columns:
        preds.append("generated = true")
    if exclude_generated and "generated" in columns:
        preds.append("(generated IS NULL OR generated = false)")
    if module and "module" in columns:
        preds.append(f"module = '{_escape_sql_str(module)}'")
    if microservice and "microservice" in columns:
        preds.append(f"microservice = '{_escape_sql_str(microservice)}'")
    if package_prefix and "package" in columns:
        esc = _escape_sql_str(package_prefix)
        preds.append(f"(package = '{esc}' OR package LIKE '{esc}.%')")
    if fqn_in and "primary_type_fqn" in columns:
        # LanceDB/Arrow SQL supports IN; quote each.
        vals = ", ".join(f"'{_escape_sql_str(v)}'" for v in fqn_in)
        preds.append(f"primary_type_fqn IN ({vals})")
    if capability and "capabilities" in columns:
        preds.append(f"array_has(capabilities, '{_escape_sql_str(capability)}')")
    return preds


def coerce_position_field(val: object) -> dict[str, object]:
    """LanceDB may return struct columns as JSON strings; normalize to a dict."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _apply_chunk_hints(rows: list[dict]) -> None:
    for r in rows:
        lang = r.get("language") or ""
        kind = str(r.get("_kind", ""))
        if kind == "sql" and not lang:
            lang = "sql"
        if kind == "yaml" and not lang:
            lang = "yaml"
        h = analyze_chunk(r.get("text"), language=str(lang), kind=kind)
        r["_hints"] = {
            "primary_type_hint": h.primary_type_hint,
            "import_heavy": h.import_heavy,
        }


def _vector_sort_key(r: dict) -> float:
    d = float(r["_distance"])
    comps = r.setdefault("_score_components", {})
    comps["distance"] = d
    if r.get("_hints", {}).get("import_heavy"):
        d += _IMPORT_DISTANCE_PENALTY
        comps["import_penalty"] = _IMPORT_DISTANCE_PENALTY
    d -= _role_weight(r)
    d -= float(comps.get("symbol_bonus", 0.0))
    return d


def _hybrid_sort_key(r: dict) -> float:
    s = float(r.get("_score", 0.0))
    comps = r.setdefault("_score_components", {})
    comps["hybrid_rrf"] = s
    if r.get("_hints", {}).get("import_heavy"):
        s *= _IMPORT_HYBRID_SCORE_FACTOR
        comps["import_penalty"] = 1.0 - _IMPORT_HYBRID_SCORE_FACTOR
    s += _role_weight(r)
    s += float(comps.get("symbol_bonus", 0.0))
    return -s


def _hybrid_post_sort_normalization(rows: list[dict]) -> None:
    """Set honest displayed scores for hybrid search after sorting.

    Reconstructs the composite score (raw_rrf * import_factor + role_weight + symbol_bonus)
    and normalizes by _HYBRID_SCORE_MAX to ensure rank-monotonicity.

    Mutates rows in-place, replacing _score with the normalized value.
    """
    for r in rows:
        comps = r.setdefault("_score_components", {})
        raw = comps.get("hybrid_rrf", 0.0)
        comps["rrf_raw"] = raw  # preserve raw RRF for --explain. NOTE: when graph_expand + hybrid combine (Phase 2), _rrf_merge below overwrites this with graph-RRF, so --explain would show graph-RRF not hybrid-RRF.
        s = raw
        if r.get("_hints", {}).get("import_heavy"):
            s *= _IMPORT_HYBRID_SCORE_FACTOR
        s += comps.get("role_weight", 0.0) + comps.get("symbol_bonus", 0.0)
        r["_score"] = _clamp01(s / _HYBRID_SCORE_MAX)


def _escape_like_fragment(s: str) -> str:
    return s.replace("'", "''")


def _escape_sql_like_pattern(s: str) -> str:
    out: list[str] = []
    for c in s:
        if c in ("\\", "%", "_"):
            out.append("\\" + c)
        else:
            out.append(c)
    return "".join(out)


def _build_path_predicate(path_substring: str) -> str:
    pat = _escape_sql_like_pattern(path_substring)
    pat = _escape_like_fragment(pat)
    return f"filename LIKE '%{pat}%' ESCAPE '\\'"


def ensure_text_fts_index(uri: str, lance_table_name: str) -> None:
    key = (uri, lance_table_name)
    with _FTS_LOCK:
        if key in _FTS_READY:
            return
        db = lancedb.connect(uri)
        tbl = db.open_table(lance_table_name)
        try:
            tbl.create_fts_index("text", replace=False)
        except Exception as e:
            low = str(e).lower()
            if any(
                w in low
                for w in ("exist", "duplicate", "already", "same name")
            ):
                pass
            else:
                raise
        _FTS_READY.add(key)


def _query_vector(model: SentenceTransformer, text: str) -> np.ndarray:
    v = model.encode(
        text,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(v, dtype=np.float32)


def _combine_predicates(parts: list[str | None]) -> str | None:
    clean = [p for p in parts if p]
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    return " AND ".join(f"({p})" for p in clean)


# LanceDB (0.30.x) emits two Rust `tracing` WARN lines per hybrid query to stderr
# — "specified output columns but did not include `_score`/`_distance` ... Call
# `disable_scoring_autoprojection`". They are noise on the agent's stderr, not
# Python warnings (so `warnings.filterwarnings` can't catch them), and the fluent
# query builder exposes no `disable_scoring_autoprojection()` (the lower-level
# `to_lance().scanner(...)` path needs `pylance`, which isn't installed on the
# PEP 508 graph-only profile). We match them by stable substring so anything that
# is a REAL error still reaches stderr.
_LANCE_AUTOPROJ_MARKERS: tuple[str, ...] = (
    "disable_scoring_autoprojection",
    "did not include `_distance`",
    "did not include `_score`",
)

# The fd-2 redirect below mutates the PROCESS-GLOBAL fd 2 (and
# ``warnings.catch_warnings`` mutates global warning state). The MCP server
# dispatches every tool call through ``asyncio.to_thread`` on a thread pool
# (server.py), so two concurrent hybrid/auto-hybrid searches would race on the
# dup2 bookkeeping — corrupting the saved fd and crashing the whole server with
# ``Bad file descriptor``. Serialize the redirect so only one thread mutates fd
# 2 / warning state at a time. Concurrent hybrid queries therefore serialize
# their ``to_list()`` (correctness over throughput); a Rust-tracing-level
# suppression would remove the fd hijack entirely (follow-up).
_LANCE_WARN_REDIRECT_LOCK = threading.Lock()


def _is_autoproj_noise(line: str) -> bool:
    """True for a LanceDB autoprojection-deprecation line (to drop).

    Preserves genuine errors/tracebacks even if they happen to reference the API
    name — only the bare deprecation log lines (no Error/Traceback/Exception) are
    treated as noise.
    """
    if not any(marker in line for marker in _LANCE_AUTOPROJ_MARKERS):
        return False
    return not any(seg in line for seg in ("Traceback", "Error:", "error:", "Exception"))


@contextmanager
def _silence_lance_autoproj_warnings():
    """Swallow LanceDB's `_score`/`_distance` autoprojection deprecation warnings.

    Redirects fd 2 to a temp buffer for the duration of the wrapped call, drops
    only the autoprojection deprecation lines, and re-emits everything else to
    the real stderr so genuine errors stay visible. No-op if the caller opted
    back in via ``JAVA_CODEBASE_RAG_KEEP_LANCE_WARNINGS`` (debugging).

    Thread-safety: the redirect is serialized under ``_LANCE_WARN_REDIRECT_LOCK``
    because it mutates process-global fd 2 and warning state — the MCP server
    runs tool calls concurrently on a thread pool.
    """
    if os.environ.get("JAVA_CODEBASE_RAG_KEEP_LANCE_WARNINGS"):
        yield
        return
    # Also catch the (unlikely) Python-warning form defensively.
    with _LANCE_WARN_REDIRECT_LOCK, warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*(disable_scoring_autoprojection|did not include `(_distance|_score)`).*",
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as captured:
            saved = os.dup(2)
            try:
                os.dup2(captured.fileno(), 2)
                yield
            finally:
                # Restore fd 2 FIRST so the re-emit below reaches real stderr.
                os.dup2(saved, 2)
                os.close(saved)
            captured.seek(0)
            kept = "".join(line for line in captured if not _is_autoproj_noise(line))
        if kept:
            sys.stderr.write(kept)
            sys.stderr.flush()


def _simple_type_name(fqn: str | None) -> str | None:
    """``com.foo.Bar`` -> ``Bar``; None/empty -> None."""
    if not fqn:
        return None
    return str(fqn).rsplit(".", 1)[-1] or None


def _refine_java_start_lines(rows: list[dict]) -> None:
    """Point each java row's ``start.line`` at the type declaration, not the chunk anchor.

    LanceDB chunks are anchored at the chunk's first source line — for a
    file-spanning chunk that's the package/import line (``start.line`` = 1)
    while the ``class``/``interface`` declaration sits several lines down. The
    chunk anchor is a poor display line for a symbol hit (renders as
    ``File.java:1``); derive the real declaration line from the chunk text
    (pinned to the primary type) so a hit shows ``File.java:<decl>`` instead
    (F8). Method-only chunks whose range doesn't include a type declaration
    keep their chunk anchor unchanged.
    """
    for r in rows:
        if str(r.get("_kind", "")) != "java":
            continue
        start = r.get("start")
        if not isinstance(start, dict):
            continue
        anchor = start.get("line")
        if anchor is None:
            continue
        hints = r.get("_hints") or {}
        type_name = hints.get("primary_type_hint") or _simple_type_name(r.get("primary_type_fqn"))
        decl = declaration_line_number(r.get("text"), int(anchor), type_name)
        if decl is not None:
            start["line"] = decl


def _search_one_table(
    table_name: str,
    *,
    uri: str,
    db: object,
    query_vec: np.ndarray,
    limit: int,
    path_predicate: str | None,
    kind: str,
    hybrid: bool,
    fts_text: str | None,
    extra_predicates: list[str] | None = None,
) -> list[dict]:
    tbl = db.open_table(table_name)
    has_lang = kind == "java"
    table_cols = _table_columns(uri, table_name, db)
    enriched_cols = table_cols if has_lang else set()
    # `range_start` / `range_end` are needed downstream by `_attach_neighbor_context`
    # to locate the chunk inside its file; select them whenever the schema has them.
    base_cols = ["filename", "text", "start", "end"]
    for col in ("range_start", "range_end"):
        if col in table_cols:
            base_cols.append(col)
    java_extra = [c for c in JAVA_ENRICHED_COLUMNS if c in enriched_cols] if has_lang else []
    combined_pred = _combine_predicates([path_predicate, *(extra_predicates or [])])

    if hybrid:
        ensure_text_fts_index(uri, table_name)
        text_for_fts = fts_text if fts_text is not None else ""
        columns = (
            [*base_cols, "language", *java_extra]
            if has_lang
            else [*base_cols]
        )
        q = (
            tbl.search(
                query_type="hybrid",
                vector_column_name=VECTOR_COLUMN,
            )
            .vector(query_vec)
            .text(text_for_fts)
            .select(columns)
            .limit(limit)
        )
        if combined_pred:
            q = q.where(combined_pred, prefilter=True)
        # Hybrid selects explicit output columns without `_score`/`_distance`, so
        # LanceDB (0.30.x) emits two Rust autoprojection deprecation WARNs to
        # stderr per query. Silence just those lines; real errors still surface.
        with _silence_lance_autoproj_warnings():
            rows = q.to_list()
        for r in rows:
            r["_kind"] = kind
            rs = r.pop("_relevance_score", None)
            r["_hybrid"] = True
            if rs is not None:
                r["_score"] = float(rs)
            r["start"] = coerce_position_field(r.get("start"))
            r["end"] = coerce_position_field(r.get("end"))
        return rows

    columns = (
        [*base_cols, "language", *java_extra, "_distance"]
        if has_lang
        else [*base_cols, "_distance"]
    )
    q = tbl.search(query_vec, vector_column_name=VECTOR_COLUMN).select(
        columns
    ).limit(limit)
    if combined_pred:
        q = q.where(combined_pred, prefilter=True)
    rows = q.to_list()
    for r in rows:
        r["_kind"] = kind
        r["_hybrid"] = False
        # Populate `_score` from `_distance` so the SearchHit.score reflects
        # relevance. The hybrid branch sets `_score` from `_relevance_score`
        # above; without this, non-hybrid (default) search left `_score` unset
        # and mcp_v2._row_to_search_hit fell back to 0.0 for EVERY hit —
        # ranking still worked (the sort key uses `_distance` directly) but the
        # exposed score was always 0.0, making results look unranked.
        d = r.get("_distance")
        if d is not None:
            # Use the same non-clamping map as the display sites so graph-expand
            # rows (which run_search does NOT overwrite) never carry the old
            # 1-d²/2 value that collapses to 0 past √2. (The main single/multi
            # paths overwrite this with the bonus-adjusted effective distance.)
            r["_score"] = vector_display_score(float(d))
        r["start"] = coerce_position_field(r.get("start"))
        r["end"] = coerce_position_field(r.get("end"))
    return rows


def _debug_ctx(msg: str) -> None:
    """Emit context-expansion diagnostics when JAVA_CODEBASE_RAG_DEBUG_CONTEXT is set.

    Writes to stderr so it doesn't pollute MCP stdout. Cheap no-op otherwise.
    """
    if os.environ.get("JAVA_CODEBASE_RAG_DEBUG_CONTEXT"):
        print(f"[context_neighbors] {msg}", file=sys.stderr)


def _attach_neighbor_context(
    rows: list[dict], *, db: object, neighbors: int, uri: str | None = None,
) -> None:
    """Populate `_context_before` / `_context_after` with adjacent Java chunk text.

    Strategy (in order):
    1. Schema-aware scan of the java table, selecting only columns that exist
       (`filename` + `text` always; `range_start`/`range_end` when present).
    2. Sort the per-file bucket by `range_start` if available; otherwise keep
       the table's natural order (good enough because chunks are produced in
       file order by CocoIndex).
    3. Locate each row's index via (a) range tuple match, (b) exact text match
       as fallback. Missing both -> log and skip.
    4. Any exception is logged (behind env flag) and the field stays empty; we
       never break search because of context expansion.
    """
    if neighbors <= 0:
        return
    java_rows = [r for r in rows if str(r.get("_kind", "")) == "java"]
    if not java_rows:
        _debug_ctx("no java rows in window; nothing to expand")
        return
    filenames = {str(r.get("filename", "")) for r in java_rows if r.get("filename")}
    if not filenames:
        _debug_ctx("java rows had no filename field; skipping")
        return

    java_table = TABLES["java"]
    try:
        tbl = db.open_table(java_table)
    except Exception as exc:
        _debug_ctx(f"open_table({java_table}) failed: {exc!r}")
        return

    # Discover which positional columns the index actually carries. Older
    # indexes may predate `range_start`/`range_end`; newer ones always have
    # them. Asking for a missing column makes the whole scan fail.
    try:
        schema_cols = _table_columns(uri, java_table, db) if uri else {f.name for f in tbl.schema}
    except Exception as exc:
        _debug_ctx(f"schema lookup failed: {exc!r}")
        schema_cols = set()

    has_range = {"range_start", "range_end"}.issubset(schema_cols)
    scan_cols = ["filename", "text"]
    if has_range:
        scan_cols.extend(("range_start", "range_end"))

    try:
        in_list = ", ".join(f"'{_escape_sql_str(f)}'" for f in filenames)
        scanner = tbl.to_lance().scanner(
            filter=f"filename IN ({in_list})",
            columns=scan_cols,
        )
        all_chunks = scanner.to_table().to_pylist()
    except Exception as exc:
        _debug_ctx(f"bucket scan failed (cols={scan_cols}): {exc!r}")
        return

    if not all_chunks:
        _debug_ctx(f"bucket scan returned 0 chunks for {len(filenames)} filenames")
        return

    by_file: dict[str, list[dict]] = {}
    for ch in all_chunks:
        by_file.setdefault(str(ch.get("filename", "")), []).append(ch)
    if has_range:
        for lst in by_file.values():
            lst.sort(
                key=lambda c: (int(c.get("range_start") or 0), int(c.get("range_end") or 0))
            )

    attached = 0
    for r in java_rows:
        fn = str(r.get("filename", ""))
        bucket = by_file.get(fn, [])
        if not bucket:
            _debug_ctx(f"no bucket for filename={fn!r}")
            continue

        idx: int | None = None
        if has_range:
            start = int(r.get("range_start") or 0)
            end = int(r.get("range_end") or 0)
            if start or end:
                idx = next(
                    (
                        i for i, c in enumerate(bucket)
                        if int(c.get("range_start") or -1) == start
                        and int(c.get("range_end") or -1) == end
                    ),
                    None,
                )

        if idx is None:
            r_text = str(r.get("text") or "")
            if r_text:
                idx = next(
                    (i for i, c in enumerate(bucket) if str(c.get("text") or "") == r_text),
                    None,
                )

        if idx is None:
            _debug_ctx(
                f"could not locate chunk in bucket (file={fn!r}, "
                f"has_range={has_range}, bucket_size={len(bucket)})"
            )
            continue

        before_parts = [str(c.get("text") or "") for c in bucket[max(0, idx - neighbors):idx]]
        after_parts = [str(c.get("text") or "") for c in bucket[idx + 1 : idx + 1 + neighbors]]
        r["_context_before"] = "\n".join(before_parts)
        r["_context_after"] = "\n".join(after_parts)
        attached += 1

    _debug_ctx(f"attached context to {attached}/{len(java_rows)} java rows")


def _bm25_candidate_rows(
    *,
    g: object,
    query: str,
    uri: str,
    db: object,
    extra_predicates: list[str],
    columns: set[str],
    limit: int = 100,
) -> list[dict]:
    """Fetch BM25-ranked Symbol candidates from the FTS index and resolve them to
    chunk rows in BM25 rank order. Returns ``[]`` on any failure (silent degradation).

    Pipeline:
      1. ``search_lexical.fetch_fts_candidates(g, query)`` → BM25-ranked Symbols +
         a ``{symbol_node_id: bm25_score}`` map. ``None`` / empty → return ``[]``.
      2. Map each Symbol fqn to its enclosing TYPE fqn (``primary_type_fqn`` has no
         ``#``; a member ``Type#method`` maps to ``Type``). Dedupe by type fqn,
         keeping the MAX BM25 score among same-type symbols.
      3. Order type fqns by BM25 desc (fqn asc tiebreak — deterministic).
      4. Fetch chunk rows from LanceDB with a FILTER-ONLY query (no vector ranking,
         so BM25 order is preserved). Predicates = caller's ``extra_predicates`` +
         the ``primary_type_fqn IN (...)`` built from the ordered types — preserving
         filter parity with the vector path.
      5. Group fetched chunks by ``primary_type_fqn``; emit in BM25 rank order,
         each chunk carrying ``_score_components["bm25"]``.
      6. Apply ``_apply_chunk_hints`` + ``_refine_java_start_lines`` for consistency
         with graph_rows handling.

    Any exception (FTS or LanceDB) → ``_debug_ctx`` log + return ``[]`` (silent
    degradation; the vector path is unaffected).
    """
    # 1. BM25 candidate fetch via the FTS index.
    # Pre-split the query with the same tokenizer the ``sym_fts`` index uses
    # (``search_text`` stores ``_split_identifier`` tokens). LadybugDB FTS's own
    # tokenizer does NOT split camelCase, so a raw ``DistributionChunkService``
    # would match nothing — ``build_fts_query`` mirrors what the lexical backend
    # does at search_lexical.py (run_lexical_search), keeping index/query token
    # spaces aligned. An empty split (degenerate / stopword-only query) → no FTS
    # candidates → degrade silently to the vector path.
    fts_query = build_fts_query(query)
    if not fts_query or not fts_query.strip():
        return []
    try:
        fts = search_lexical.fetch_fts_candidates(g, fts_query, filter=None, path_contains=None)
    except Exception as exc:  # noqa: BLE001 — silent degradation
        _debug_ctx(f"bm25 FTS fetch raised: {exc!r}")
        return []
    if not fts or not fts.get("rows"):
        return []
    sym_rows = fts["rows"]
    scores = fts.get("scores") or {}

    # 2. Map symbol fqns → enclosing type fqns; keep MAX bm25 per type.
    type_fqn_to_bm25: dict[str, float] = {}
    for r in sym_rows:
        fqn = r.get("fqn")
        if not fqn:
            continue
        type_fqn = search_lexical.enclosing_type_fqn(str(fqn))
        if not type_fqn:
            continue
        score = float(scores.get(r.get("id"), 0.0))
        prev = type_fqn_to_bm25.get(type_fqn)
        if prev is None or score > prev:
            type_fqn_to_bm25[type_fqn] = score
    if not type_fqn_to_bm25:
        return []

    # 3. Deterministic ordering: BM25 desc, fqn asc.
    ordered_types = sorted(
        type_fqn_to_bm25.keys(),
        key=lambda f: (-type_fqn_to_bm25[f], f),
    )

    # 4. Filter-only chunk fetch (NO vector ranking → BM25 order preserved). The
    # ``primary_type_fqn IN (...)`` predicate must be buildable; if the index is so
    # old that the column is absent, we can't restrict the fetch and degrade to [].
    if "primary_type_fqn" not in columns:
        _debug_ctx("bm25 fetch skipped: primary_type_fqn column absent from schema")
        return []
    preds = list(extra_predicates) + _build_extra_predicates(
        columns=columns,
        role=None, module=None, microservice=None,
        package_prefix=None, fqn_in=ordered_types,
    )
    combined_pred = _combine_predicates(preds)
    base_cols = ["filename", "text", "start", "end"]
    for col in ("range_start", "range_end"):
        if col in columns:
            base_cols.append(col)
    java_extra = [c for c in JAVA_ENRICHED_COLUMNS if c in columns]
    select_cols = [*base_cols, "language", *java_extra]

    try:
        tbl = db.open_table(TABLES["java"])
        # LanceDB 0.34 filter-only path: search() with no vector arg issues a
        # non-vector scan; .where/.select/.limit/.to_list returns rows in table
        # order without re-ranking by similarity. (tbl.query() is NOT available in
        # 0.34; to_lance().scanner() requires pylance, which isn't installed on the
        # PEP 508 graph-only profile — search() with no vector is the supported API.)
        q = tbl.search().select(select_cols).limit(
            max(limit, len(ordered_types) * 4)
        )
        if combined_pred:
            q = q.where(combined_pred, prefilter=True)
        with _silence_lance_autoproj_warnings():
            fetched = q.to_list()
    except Exception as exc:  # noqa: BLE001 — silent degradation
        _debug_ctx(f"bm25 chunk fetch failed: {exc!r}")
        return []

    # 5. Group by primary_type_fqn; emit in BM25 rank order.
    by_type: dict[str, list[dict]] = {}
    for ch in fetched:
        tf = ch.get("primary_type_fqn")
        if tf is None:
            continue
        by_type.setdefault(str(tf), []).append(ch)

    out: list[dict] = []
    for type_fqn in ordered_types:
        chunks = by_type.get(type_fqn)
        if not chunks:
            continue  # filtered out by extra_predicates / absent from index
        bm25_val = round(float(type_fqn_to_bm25[type_fqn]), 4)
        for ch in chunks:
            ch["_kind"] = "java"
            ch["_hybrid"] = False
            ch.setdefault("_score_components", {})["bm25"] = bm25_val
            ch["start"] = coerce_position_field(ch.get("start"))
            ch["end"] = coerce_position_field(ch.get("end"))
            out.append(ch)

    # 6. Consistency with graph_rows handling.
    _apply_chunk_hints(out)
    _refine_java_start_lines(out)
    return out


def _graph_expand_merge(
    vector_rows: list[dict],
    *,
    query: str,
    query_vec: np.ndarray,
    db: object,
    uri: str,
    limit: int,
    extra_predicates: list[str],
    expand_depth: int,
    ladybug_path: str | None,
    rank_config: RankConfig = DEFAULT_RANK_CONFIG,
) -> list[dict]:
    """Expand vector top-k through the graph and/or fuse BM25, then RRF-merge.

    Which lists contribute is controlled by ``rank_config.lists``:
      - ``"vector"``  — always present (the backbone; validated by RankConfig).
      - ``"graph"``   — graph expand + fetch (skipped entirely when absent).
      - ``"bm25"``    — LadybugDB FTS candidate fetch fused as a third list.

    Silent degradation: any failure in the graph or BM25 path drops just that list;
    the vector list is never lost. Returns ``vector_rows`` unchanged when no
    auxiliary list yields rows.
    """
    want_graph = "graph" in rank_config.lists
    want_bm25 = "bm25" in rank_config.lists
    if not want_graph and not want_bm25:
        return vector_rows

    # Lazy import so the module works without ladybug installed when graph_expand=False.
    try:
        from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    except Exception:
        return vector_rows

    if not LadybugGraph.exists(ladybug_path):
        return vector_rows

    java_cols = _table_columns(uri, TABLES["java"], db)

    # --- graph list ---
    graph_rows: list[dict] = []
    expand_weight_by_fqn: dict[str, float] = {}
    if want_graph:
        seed_fqns = sorted({r.get("primary_type_fqn") for r in vector_rows if r.get("primary_type_fqn")})
        neighbor_fqns: list[str] = []
        if seed_fqns:
            try:
                graph_obj = LadybugGraph.get(ladybug_path)
                structural = graph_obj.expand_fqns(seed_fqns, depth=expand_depth)
                method_pairs = graph_obj.expand_methods(
                    seed_fqns, depth=expand_depth, exclude_external=True,
                )
                for f in structural:
                    if f:
                        expand_weight_by_fqn[f] = max(expand_weight_by_fqn.get(f, 0.0), 1.0)
                for f, conf in method_pairs:
                    if f:
                        expand_weight_by_fqn[f] = max(expand_weight_by_fqn.get(f, 0.0), conf)
                neighbor_fqns = list(dict.fromkeys(
                    list(structural) + [f for f, _ in method_pairs],
                ))
            except Exception:
                neighbor_fqns = []

            novel = [fqn for fqn in neighbor_fqns if fqn and fqn not in set(seed_fqns)]
            if novel:
                extra = list(extra_predicates)
                extra.extend(_build_extra_predicates(
                    columns=java_cols,
                    role=None, module=None, microservice=None,
                    package_prefix=None, fqn_in=novel,
                ))
                try:
                    graph_rows = _search_one_table(
                        TABLES["java"],
                        uri=uri, db=db, query_vec=query_vec,
                        limit=max(limit, 20),
                        path_predicate=None, kind="java",
                        hybrid=False, fts_text=None,
                        extra_predicates=extra,
                    )
                except Exception:
                    graph_rows = []
                _apply_chunk_hints(graph_rows)
                _refine_java_start_lines(graph_rows)
                graph_rows.sort(key=_vector_sort_key)
                for r in graph_rows:
                    r["_graph_expanded"] = True
                    r["_graph_expand_weight"] = expand_weight_by_fqn.get(
                        r.get("primary_type_fqn"), 1.0,
                    )

    # --- bm25 list ---
    bm25_rows: list[dict] = []
    if want_bm25:
        try:
            graph_obj = LadybugGraph.get(ladybug_path)
        except Exception:
            graph_obj = None
        if graph_obj is not None:
            bm25_rows = _bm25_candidate_rows(
                g=graph_obj,
                query=query,
                uri=uri,
                db=db,
                extra_predicates=extra_predicates,
                columns=java_cols,
                limit=limit,
            )

    # --- RRF fusion (only lists that yielded rows beyond vector) ---
    lists: list[list[dict]] = [vector_rows]
    row_weights: list[Callable[[dict], float] | None] = [None]
    if want_graph and graph_rows:
        lists.append(graph_rows)
        row_weights.append(lambda row: float(row.get("_graph_expand_weight", 1.0)))
    if want_bm25 and bm25_rows:
        lists.append(bm25_rows)
        row_weights.append(None)

    if len(lists) == 1:
        return vector_rows

    return _rrf_merge(
        lists,
        k=rank_config.rrf_k,
        row_weight_for_list_index=row_weights,
    )


def _rrf_merge(
    lists: list[list[dict]],
    *,
    k: int = 60,
    row_weight_for_list_index: list[Callable[[dict], float] | None] | None = None,
) -> list[dict]:
    """Reciprocal-rank-fuse several ranked lists of chunk rows.

    Rows are deduplicated by (filename, range_start, range_end). The merged
    rows get a `_rrf_score` field so callers can inspect or re-sort.

    When ``row_weight_for_list_index`` is set, its length must match ``lists``;
    a non-None entry is a callable ``row -> weight`` multiplied into that list's
    rank contribution (``None`` means weight ``1.0`` for every row).
    """
    pool: dict[tuple, dict] = {}
    for li, ranked in enumerate(lists):
        wfn: Callable[[dict], float] | None = None
        if row_weight_for_list_index is not None and li < len(row_weight_for_list_index):
            wfn = row_weight_for_list_index[li]
        for rank, row in enumerate(ranked):
            key = (row.get("filename"), row.get("range_start"), row.get("range_end"))
            existing = pool.get(key)
            weight = 1.0 if wfn is None else float(wfn(row))
            contribution = weight * (1.0 / (k + rank + 1))
            if existing is None:
                row["_rrf_score"] = contribution
                pool[key] = row
            else:
                existing["_rrf_score"] = float(existing.get("_rrf_score", 0.0)) + contribution
    merged = list(pool.values())
    merged.sort(key=lambda r: -float(r.get("_rrf_score", 0.0)))
    # Normalize displayed _rrf_score to [0,1] by theoretical max
    # RRF max = Σ weight·1/(k+rank+1); theoretical max when all rows are rank 0
    # with weight 1.0 = num_lists / (k + 1)
    num_lists = len(lists)
    max_rrf = num_lists / (k + 1)
    for r in merged:
        raw_score = float(r.get("_rrf_score", 0.0))
        comps = r.setdefault("_score_components", {})
        comps["rrf_raw"] = raw_score
        r["_rrf_score"] = _clamp01(raw_score / max_rrf)
    return merged


def run_search(
    query: str,
    *,
    uri: str,
    table_keys: list[str],
    limit: int,
    path_substring: str | None,
    model_name: str,
    device: str | None,
    offset: int = 0,
    model: SentenceTransformer | None = None,
    hybrid: bool = False,
    fts_text: str | None = None,
    auto_hybrid: bool = False,
    role: str | None = None,
    module: str | None = None,
    microservice: str | None = None,
    package_prefix: str | None = None,
    graph_expand: bool = False,
    expand_depth: int = 1,
    ladybug_path: str | None = None,
    context_neighbors: int = 0,
    role_in: list[str] | None = None,
    exclude_roles: list[str] | None = None,
    capability: str | None = None,
    capability_in: list[str] | None = None,
    generated_only: bool = False,
    exclude_generated: bool = False,
    dedup_by_fqn: bool = False,
    rank_config: RankConfig = DEFAULT_RANK_CONFIG,
) -> list[dict]:
    effective_hybrid = hybrid
    effective_fts = fts_text
    if (
        auto_hybrid
        and not hybrid
        and len(table_keys) == 1
        and looks_like_code_identifier(query)
    ):
        effective_hybrid = True
        if effective_fts is None:
            effective_fts = query.strip()

    if effective_hybrid and len(table_keys) != 1:
        raise ValueError(
            "hybrid search requires exactly one table; "
            "use table java, sql, or yaml (not all)."
        )

    path_predicate = (
        _build_path_predicate(path_substring) if path_substring else None
    )

    if model is None:
        model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
    query_vec = _query_vector(model, query)
    fts_for_hybrid = effective_fts if effective_fts is not None else query

    db = lancedb.connect(uri)
    if dedup_by_fqn:
        # Over-fetch to absorb per-FQN chunk multiplicity: fetch 4x so that
        # after collapsing, the page stays full and the +1 truncation sentinel survives.
        # The 4× factor assumes typical per-FQN chunk multiplicity; a single type with
        # many high-ranking chunks (e.g. generated/God classes) could starve the page or
        # make the +1 truncation sentinel unreliable; Phase 1 may revisit adaptive over-fetch (plan risk #1).
        need = max((limit + offset) * DEDUP_OVERFETCH, limit + offset + 1)
    else:
        # Non-dedup path: exact fetch as before
        need = max(limit + offset, 1)

    extra_java = _build_extra_predicates(
        columns=_table_columns(uri, TABLES["java"], db),
        role=role, module=module, microservice=microservice,
        package_prefix=package_prefix, fqn_in=None,
        role_in=role_in, exclude_roles=exclude_roles,
        capability=capability, capability_in=capability_in,
        generated_only=generated_only, exclude_generated=exclude_generated,
    ) if "java" in table_keys else []

    skip_role_weight = bool(role or role_in or exclude_roles)
    query_toks = _query_tokens(query)

    if len(table_keys) == 1:
        key = table_keys[0]
        preds = extra_java if key == "java" else []
        rows = _search_one_table(
            TABLES[key],
            uri=uri,
            db=db,
            query_vec=query_vec,
            limit=need,
            path_predicate=path_predicate,
            kind=key,
            hybrid=effective_hybrid,
            fts_text=fts_for_hybrid,
            extra_predicates=preds,
        )
        _apply_chunk_hints(rows)
        # Anchor each java row's start.line on the type declaration instead of
        # the chunk's first source line (often the package/import line = 1).
        _refine_java_start_lines(rows)
        if skip_role_weight:
            for r in rows:
                r["_skip_role_weight"] = True
        _apply_symbol_bonus(rows, query_toks)
        if effective_hybrid:
            rows.sort(key=_hybrid_sort_key)
            # Hybrid: set honest displayed score from composite sort metric, clamped to [0,1]
            _hybrid_post_sort_normalization(rows)
        else:
            rows.sort(key=_vector_sort_key)
            # Vector: displayed score from the effective (bonus-adjusted) distance,
            # normalized over the unit-embedding range so a correctly-ranked top
            # hit never collapses to 0.000 (the cosine map 1 - d²/2 clamps to 0
            # past √2; weak-but-best matches commonly sit at d ≈ 1.5).
            for r in rows:
                comps = r.setdefault("_score_components", {})
                effective_dist = _effective_distance(comps)
                r["_score"] = vector_display_score(effective_dist)

        if graph_expand and key == "java" and expand_depth > 0:
            rows = _graph_expand_merge(
                rows,
                query=query,
                query_vec=query_vec,
                db=db,
                uri=uri,
                limit=need,
                extra_predicates=extra_java,
                expand_depth=expand_depth,
                ladybug_path=ladybug_path,
                rank_config=rank_config,
            )

        # Dedup by primary_type_fqn after all sorting/merging, before windowing
        rows = _dedup_by_fqn(rows, dedup_by_fqn=dedup_by_fqn)

        window = rows[offset : offset + limit]
        if context_neighbors > 0 and key == "java":
            _attach_neighbor_context(window, db=db, neighbors=context_neighbors, uri=uri)
        return window

    merged: list[dict] = []
    per_table = max(need * 3, need)
    for key in table_keys:
        preds = extra_java if key == "java" else []
        merged.extend(
            _search_one_table(
                TABLES[key],
                uri=uri,
                db=db,
                query_vec=query_vec,
                limit=per_table,
                path_predicate=path_predicate,
                kind=key,
                hybrid=False,
                fts_text=None,
                extra_predicates=preds,
            )
        )
    _apply_chunk_hints(merged)
    _refine_java_start_lines(merged)
    if skip_role_weight:
        for r in merged:
            r["_skip_role_weight"] = True
    _apply_symbol_bonus(merged, query_toks)
    merged.sort(key=_vector_sort_key)
    # Vector: displayed score from the effective (bonus-adjusted) distance.
    for r in merged:
        comps = r.setdefault("_score_components", {})
        effective_dist = _effective_distance(comps)
        r["_score"] = vector_display_score(effective_dist)

    # Dedup by primary_type_fqn after all sorting/merging, before windowing
    merged = _dedup_by_fqn(merged, dedup_by_fqn=dedup_by_fqn)

    window = merged[offset : offset + limit]
    if context_neighbors > 0:
        _attach_neighbor_context(window, db=db, neighbors=context_neighbors, uri=uri)
    return window


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vector search in LanceDB index.",
    )
    parser.add_argument("query", help="Natural-language search query")
    parser.add_argument(
        "--table",
        choices=["java", "sql", "yaml", "all"],
        default="java",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--lancedb-uri",
        default=os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "")
        or str((Path.cwd() / ".java-codebase-rag").resolve()),
    )
    parser.add_argument("--path-contains", metavar="SUBSTR", default=None)
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "sentence-transformers hub id or local model directory "
            f"(default: SBERT_MODEL env or {SBERT_MODEL!r})"
        ),
    )
    parser.add_argument("--device", default=None)
    parser.add_argument("--text-width", type=int, default=320)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--fts-text", metavar="TEXT", default=None)
    parser.add_argument("--auto-hybrid", action="store_true")
    parser.add_argument("--role", default=None)
    parser.add_argument("--exclude-generated", action="store_true",
                        help="Exclude generated sources from results.")
    parser.add_argument("--generated-only", action="store_true",
                        help="Return only generated sources in results.")
    parser.add_argument("--module", default=None,
                        help="Filter to a single Maven/Gradle module name.")
    parser.add_argument("--microservice", default=None,
                        help="Filter to a single deployable microservice (top-level dir under project root).")
    parser.add_argument("--package-prefix", default=None)
    parser.add_argument("--graph-expand", action="store_true")
    parser.add_argument("--expand-depth", type=int, default=1)
    parser.add_argument("--ladybug-path", default=None)
    parser.add_argument(
        "--context-neighbors", type=int, default=0,
        help="Attach N adjacent chunks per hit as surrounding context (Java only).",
    )
    args = parser.parse_args()

    uri_path = Path(args.lancedb_uri)
    if not uri_path.exists():
        print(f"Error: LanceDB path missing: {uri_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    keys = list(TABLES) if args.table == "all" else [args.table]
    if args.hybrid and args.table == "all":
        print("Error: --hybrid needs a single --table.", file=sys.stderr)
        sys.exit(2)
    if args.auto_hybrid and args.table == "all":
        print("Error: --auto-hybrid needs a single --table.", file=sys.stderr)
        sys.exit(2)

    raw_model = args.model
    if raw_model is None or not str(raw_model).strip():
        model_name = resolved_sbert_model_for_process_env(SBERT_MODEL)
    else:
        model_name = maybe_expand_embedding_model_path(str(raw_model).strip())

    try:
        results = run_search(
            args.query,
            uri=str(uri_path),
            table_keys=keys,
            limit=args.limit,
            path_substring=args.path_contains,
            model_name=model_name,
            device=args.device,
            hybrid=args.hybrid,
            fts_text=args.fts_text,
            auto_hybrid=args.auto_hybrid,
            role=args.role,
            module=args.module,
            microservice=args.microservice,
            package_prefix=args.package_prefix,
            graph_expand=args.graph_expand,
            expand_depth=args.expand_depth,
            ladybug_path=args.ladybug_path,
            context_neighbors=args.context_neighbors,
            exclude_generated=args.exclude_generated,
            generated_only=args.generated_only,
        )
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No results.")
        return

    w = args.text_width
    for i, row in enumerate(results, start=1):
        kind = row["_kind"]
        fn = row["filename"]
        lang = row.get("language", "—")
        start = row.get("start") or {}
        end = row.get("end") or {}
        line_hint = ""
        if isinstance(start, dict) and "line" in start:
            el = (
                end["line"]
                if isinstance(end, dict) and "line" in end
                else start["line"]
            )
            line_hint = f" L{start['line']}-{el}"
        text = (row.get("text") or "").replace("\n", " ")
        preview = text if len(text) <= w else text[: w - 3] + "..."
        if row.get("_hybrid"):
            rank_s = f"hybrid RRF={float(row.get('_score', 0.0)):.4f}"
        else:
            rank_s = f"L2 distance={float(row['_distance']):.4f}"
        hints = row.get("_hints") or {}
        hint_s = ""
        if hints.get("primary_type_hint"):
            hint_s += f" | type:{hints['primary_type_hint']}"
        if hints.get("import_heavy"):
            hint_s += " | mostly-imports"
        role = row.get("role") or ""
        if role:
            hint_s += f" | role:{role}"
        ms = row.get("microservice") or ""
        if ms:
            hint_s += f" | microservice:{ms}"
        mod = row.get("module") or ""
        if mod and mod != ms:
            hint_s += f" | module:{mod}"
        gen = row.get("generated")
        gen_by = row.get("generated_by") or ""
        if gen:
            hint_s += f" | generated:{gen_by}" if gen_by else " | generated"
        comps = row.get("_score_components") or {}
        rw = comps.get("role_weight")
        if rw:
            hint_s += f" | role_weight:{rw:+.2f}"
        sb = comps.get("symbol_bonus")
        if sb:
            hint_s += f" | symbol_bonus:{sb:+.2f}"
        if row.get("_graph_expanded"):
            hint_s += " | graph"
        print(f"--- {i}. [{kind}] {rank_s} | {fn}{line_hint} | lang={lang}{hint_s}")
        print(preview)
        print()


if __name__ == "__main__":
    main()
