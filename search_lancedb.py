#!/usr/bin/env python3
"""Semantic search over LanceDB tables built by CocoIndex (java_index_flow_lancedb)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import lancedb
import numpy as np
from sentence_transformers import SentenceTransformer

from chunk_heuristics import analyze_chunk, looks_like_code_identifier
from index_common import SBERT_MODEL
from java_codebase_rag.config import maybe_expand_embedding_model_path, resolved_sbert_model_for_process_env

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


_IMPORT_DISTANCE_PENALTY = 0.08
_IMPORT_HYBRID_SCORE_FACTOR = 0.88

# Bonus for chunks whose declared symbols (method / field names) share tokens with
# the query. Behavioural queries like "what happens when a client message arrives"
# should float chunks containing `processClientMessage` above ones that only
# enqueue; this is a cheap, query-dependent signal computed at rank time.
_SYMBOL_MATCH_BONUS_PER_HIT = 0.03
_SYMBOL_MATCH_BONUS_CAP = 0.06

# Action verbs that typically mark behavioural entry points in this codebase.
# A chunk whose symbols begin with one of these verbs earns a small flat bump
# — again only for java chunks and only when role-filtering is off.
_ACTION_VERB_PREFIXES: tuple[str, ...] = (
    "process", "handle", "on", "pick", "select", "assign",
    "notify", "dispatch", "publish", "consume", "route",
    "trigger", "enqueue", "distribute", "update", "create",
    "apply", "resolve", "reassign", "close", "open",
)
_ACTION_VERB_BONUS = 0.02

# Type-name overlap bonus. The class name is a much stronger discovery signal
# than any individual method, because class naming in this codebase encodes
# the domain concept (`DistributionChunkService`, `OperatorSessionService`,
# `JoinOperatorController`). So we reward overlap between query tokens and the
# simple name of `primary_type_fqn` more heavily than per-method overlap, and
# we stack it on top of the existing `_symbol_bonus`.
_TYPE_MATCH_BONUS_PER_HIT = 0.05
_TYPE_MATCH_BONUS_CAP = 0.10

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as", "or",
    "and", "but", "if", "then", "else", "when", "what", "how", "why", "does",
    "do", "did", "has", "have", "had", "this", "that", "these", "those", "it",
    "its", "new", "no", "not", "will", "would", "should", "can", "could",
    "may", "might", "happens", "happen", "happened", "get", "gets", "got",
})

# Role-aware reweighting for Java chunks. Positive values favour actionable
# behavioural code (entrypoints, orchestrators, integrations) over configuration,
# schema, and persistence stubs for "what happens when..."-style queries.
# Applied to the similarity score (higher = better); distance-based sort subtracts
# the weight. Skipped when caller filters explicitly by role.
_ROLE_SCORE_WEIGHTS: dict[str, float] = {
    "CONTROLLER": 0.10,
    "SERVICE": 0.08,
    "CLIENT": 0.06,
    "COMPONENT": 0.03,
    "REPOSITORY": 0.02,
    "MAPPER": 0.00,
    "OTHER": 0.00,
    "ENTITY": -0.06,
    "CONFIG": -0.10,
    # DTOs are passive data carriers; they almost never answer "how/what
    # happens" queries. Penalty is slightly stronger than ENTITY so a DTO
    # with a great embedding match still loses to a mediocre SERVICE hit.
    "DTO": -0.08,
}

# Theoretical maximum for hybrid composite score (used for display normalization).
# Hybrid sort metric: raw_rrf * (import_factor if import_heavy else 1)
#                   + role_weight + symbol_bonus
# where raw_rrf ≤ 2/(k+1) for 2-list RRF, role_weight ≤ max(_ROLE_SCORE_WEIGHTS),
# and symbol_bonus ≤ _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS.
# The import factor is ≤ 1, so we use the raw max (2/61).
_HYBRID_SCORE_MAX = (2.0 / 61.0) + max(_ROLE_SCORE_WEIGHTS.values()) + _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS


def _query_tokens(query: str) -> set[str]:
    """Lowercased alpha-only tokens from the query, minus stopwords, len >= 3.

    Used to score symbol-name overlap; we keep it simple and locale-free.
    """
    out: set[str] = set()
    cur: list[str] = []

    def _flush() -> None:
        if cur:
            tok = "".join(cur).lower()
            cur.clear()
            if len(tok) >= 3 and tok not in _STOPWORDS:
                out.add(tok)

    for c in query:
        if c.isalpha():
            cur.append(c)
        else:
            _flush()
    _flush()
    return out


def _split_identifier(name: str) -> list[str]:
    """camelCase / snake_case -> lowercase token list."""
    parts: list[str] = []
    cur: list[str] = []
    for c in name:
        if c == "_":
            if cur:
                parts.append("".join(cur).lower())
                cur = []
        elif c.isupper() and cur:
            parts.append("".join(cur).lower())
            cur = [c]
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).lower())
    return [p for p in parts if p]


def _symbol_bonus(r: dict, query_toks: set[str]) -> float:
    """Symbol-name overlap + action-verb bump for java chunks.

    Caps at `_SYMBOL_MATCH_BONUS_CAP + _ACTION_VERB_BONUS` to avoid runaway
    ranks on chunks declaring many symbols.
    """
    if str(r.get("_kind", "")) != "java":
        return 0.0
    raw = r.get("symbols") or []
    if isinstance(raw, str):
        # Legacy JSON-encoded list column; parse defensively.
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else []
        except Exception:
            raw = []
    symbols = [str(s) for s in raw if s]

    overlap_hits = 0
    has_action = False
    for s in symbols:
        bare = s.split("(", 1)[0].strip()
        if not bare:
            continue
        toks = _split_identifier(bare)
        if toks:
            if toks[0] in _ACTION_VERB_PREFIXES:
                has_action = True
            if query_toks & set(toks):
                overlap_hits += 1

    bonus = min(overlap_hits * _SYMBOL_MATCH_BONUS_PER_HIT, _SYMBOL_MATCH_BONUS_CAP)
    if has_action:
        bonus += _ACTION_VERB_BONUS

    # Type-name overlap: strongest single lexical signal for "which class is
    # the answer?" queries. Uses the simple name of primary_type_fqn.
    fqn = str(r.get("primary_type_fqn") or "")
    if fqn:
        simple = fqn.rsplit(".", 1)[-1]
        type_toks = set(_split_identifier(simple))
        type_hits = len(query_toks & type_toks)
        if type_hits:
            bonus += min(type_hits * _TYPE_MATCH_BONUS_PER_HIT, _TYPE_MATCH_BONUS_CAP)
    return bonus


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


def _role_weight(r: dict) -> float:
    """Effective role weight for a row, captured into `_score_components.role_weight`."""
    comps = r.setdefault("_score_components", {})
    cached = comps.get("role_weight")
    if cached is not None:
        return float(cached)
    if r.get("_skip_role_weight") or str(r.get("_kind", "")) != "java":
        comps["role_weight"] = 0.0
        return 0.0
    role = (r.get("role") or "").upper()
    w = _ROLE_SCORE_WEIGHTS.get(role, 0.0)
    comps["role_weight"] = w
    return w


def _apply_symbol_bonus(rows: list[dict], query_toks: set[str]) -> None:
    """Pre-compute symbol-match bonus into `_score_components.symbol_bonus`."""
    if not query_toks:
        return
    for r in rows:
        if r.get("_skip_role_weight"):
            # When the caller locked role, respect their intent everywhere.
            continue
        b = _symbol_bonus(r, query_toks)
        if b:
            r.setdefault("_score_components", {})["symbol_bonus"] = b


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
        comps["import_penalty"] = _IMPORT_HYBRID_SCORE_FACTOR
    s += _role_weight(r)
    s += float(comps.get("symbol_bonus", 0.0))
    return -s


def explain_score_components(
    comps: dict[str, float] | None,
    *,
    role: str | None = None,
    hybrid: bool = False,
    graph_expanded: bool = False,
) -> str:
    """Compact human-readable 'why' string for a ranked hit.

    Joins the interesting components of `_score_components` in a stable order
    so agents can reason about rankings without chasing raw floats. Returns
    "" if there's nothing worth mentioning.
    """
    if not comps:
        comps = {}
    parts: list[str] = []
    if hybrid:
        # Prefer rrf_raw (added by PR-SEARCH-1a) for explanation
        rrf = comps.get("rrf_raw") or comps.get("hybrid_rrf")
        if rrf is not None:
            parts.append(f"rrf={float(rrf):.3f}")
    else:
        d = comps.get("distance")
        if d is not None:
            parts.append(f"dist={float(d):.2f}")
    rw = comps.get("role_weight")
    if rw:
        label = f"role:{role}" if role else "role"
        parts.append(f"{label}:{float(rw):+.02f}")
    sb = comps.get("symbol_bonus")
    if sb:
        parts.append(f"symbol:{float(sb):+.02f}")
    ip = comps.get("import_penalty")
    if ip:
        parts.append(f"import_penalty:{float(ip):+.02f}")
    if graph_expanded:
        parts.append("graph")
    return " ".join(parts)


def l2_distance_to_score(distance: float) -> float:
    """Map L2 distance to a similarity score for unit-normalized embeddings."""
    return 1.0 - distance * distance / 2.0


def _effective_distance(comps: dict[str, float]) -> float:
    """Compute the adjusted distance used for sorting.

    Matches _vector_sort_key logic: distance + import_penalty - role_weight - symbol_bonus.
    """
    d = comps.get("distance", 0.0)
    d += comps.get("import_penalty", 0.0)
    d -= comps.get("role_weight", 0.0)
    d -= comps.get("symbol_bonus", 0.0)
    return d


def _clamp01(x: float) -> float:
    """Clamp a value to the [0.0, 1.0] range."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _hybrid_post_sort_normalization(rows: list[dict]) -> None:
    """Set honest displayed scores for hybrid search after sorting.

    Reconstructs the composite score (raw_rrf * import_factor + role_weight + symbol_bonus)
    and normalizes by _HYBRID_SCORE_MAX to ensure rank-monotonicity.

    Mutates rows in-place, replacing _score with the normalized value.
    """
    for r in rows:
        comps = r.setdefault("_score_components", {})
        raw = comps.get("hybrid_rrf", 0.0)
        comps["rrf_raw"] = raw  # preserve raw RRF for --explain
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
            r["_score"] = l2_distance_to_score(float(d))
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


def _graph_expand_merge(
    vector_rows: list[dict],
    *,
    query_vec: np.ndarray,
    db: object,
    uri: str,
    limit: int,
    extra_predicates: list[str],
    expand_depth: int,
    ladybug_path: str | None,
) -> list[dict]:
    """Expand vector top-k through the LadybugDB graph and fuse (RRF) with the original list."""
    # Lazy import so the module works without ladybug installed when graph_expand=False.
    try:
        from ladybug_queries import LadybugGraph
    except Exception:
        return vector_rows

    if not LadybugGraph.exists(ladybug_path):
        return vector_rows

    seed_fqns = sorted({r.get("primary_type_fqn") for r in vector_rows if r.get("primary_type_fqn")})
    if not seed_fqns:
        return vector_rows

    try:
        graph = LadybugGraph.get(ladybug_path)
        structural = graph.expand_fqns(seed_fqns, depth=expand_depth)
        method_pairs = graph.expand_methods(
            seed_fqns, depth=expand_depth, exclude_external=True,
        )
        expand_weight_by_fqn: dict[str, float] = {}
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
        return vector_rows

    novel = [fqn for fqn in neighbor_fqns if fqn and fqn not in set(seed_fqns)]
    if not novel:
        return vector_rows

    extra = list(extra_predicates)
    extra.extend(_build_extra_predicates(
        columns=_table_columns(uri, TABLES["java"], db),
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
        return vector_rows
    _apply_chunk_hints(graph_rows)
    graph_rows.sort(key=_vector_sort_key)
    for r in graph_rows:
        r["_graph_expanded"] = True
        r["_graph_expand_weight"] = expand_weight_by_fqn.get(
            r.get("primary_type_fqn"), 1.0,
        )
    fused = _rrf_merge(
        [vector_rows, graph_rows],
        row_weight_for_list_index=[
            None,
            lambda row: float(row.get("_graph_expand_weight", 1.0)),
        ],
    )
    return fused


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
    need = max(limit + offset, 1)

    extra_java = _build_extra_predicates(
        columns=_table_columns(uri, TABLES["java"], db),
        role=role, module=module, microservice=microservice,
        package_prefix=package_prefix, fqn_in=None,
        role_in=role_in, exclude_roles=exclude_roles,
        capability=capability, capability_in=capability_in,
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
            # Vector: set honest displayed score from adjusted distance, clamped to [0,1]
            for r in rows:
                comps = r.setdefault("_score_components", {})
                effective_dist = _effective_distance(comps)
                r["_score"] = _clamp01(l2_distance_to_score(effective_dist))

        if graph_expand and key == "java" and expand_depth > 0:
            rows = _graph_expand_merge(
                rows,
                query_vec=query_vec,
                db=db,
                uri=uri,
                limit=need,
                extra_predicates=extra_java,
                expand_depth=expand_depth,
                ladybug_path=ladybug_path,
            )

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
    if skip_role_weight:
        for r in merged:
            r["_skip_role_weight"] = True
    _apply_symbol_bonus(merged, query_toks)
    merged.sort(key=_vector_sort_key)
    # Vector: set honest displayed score from adjusted distance, clamped to [0,1]
    for r in merged:
        comps = r.setdefault("_score_components", {})
        effective_dist = _effective_distance(comps)
        r["_score"] = _clamp01(l2_distance_to_score(effective_dist))
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
