#!/usr/bin/env python3
"""Semantic search over LanceDB tables built by CocoIndex (java_index_flow_lancedb)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

import lancedb
import numpy as np
from sentence_transformers import SentenceTransformer

from chunk_heuristics import analyze_chunk, looks_like_code_identifier
from index_common import SBERT_MODEL

TABLES: dict[str, str] = {
    "java": "javacodeindex_java_code",
    "sql": "sqlschemaindex_sql_schema",
    "yaml": "yamlconfigindex_yaml_config",
}

VECTOR_COLUMN = "embedding"
_FTS_READY: set[tuple[str, str]] = set()
_FTS_LOCK = threading.Lock()


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
    if r.get("_hints", {}).get("import_heavy"):
        d += _IMPORT_DISTANCE_PENALTY
    return d


def _hybrid_sort_key(r: dict) -> float:
    s = float(r.get("_score", 0.0))
    if r.get("_hints", {}).get("import_heavy"):
        s *= _IMPORT_HYBRID_SCORE_FACTOR
    return -s


def l2_distance_to_score(distance: float) -> float:
    """Map L2 distance to a similarity score for unit-normalized embeddings."""
    return 1.0 - distance * distance / 2.0


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
) -> list[dict]:
    tbl = db.open_table(table_name)
    has_lang = kind == "java"
    base_cols = ["filename", "text", "start", "end"]
    if hybrid:
        ensure_text_fts_index(uri, table_name)
        text_for_fts = fts_text if fts_text is not None else ""
        columns = (
            [*base_cols, "language"]
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
        if path_predicate:
            q = q.where(path_predicate, prefilter=True)
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
        [*base_cols, "language", "_distance"]
        if has_lang
        else [*base_cols, "_distance"]
    )
    q = tbl.search(query_vec, vector_column_name=VECTOR_COLUMN).select(
        columns
    ).limit(limit)
    if path_predicate:
        q = q.where(path_predicate, prefilter=True)
    rows = q.to_list()
    for r in rows:
        r["_kind"] = kind
        r["_hybrid"] = False
        r["start"] = coerce_position_field(r.get("start"))
        r["end"] = coerce_position_field(r.get("end"))
    return rows


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

    if len(table_keys) == 1:
        key = table_keys[0]
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
        )
        _apply_chunk_hints(rows)
        if effective_hybrid:
            rows.sort(key=_hybrid_sort_key)
        else:
            rows.sort(key=_vector_sort_key)
        return rows[offset : offset + limit]

    merged: list[dict] = []
    per_table = max(need * 3, need)
    for key in table_keys:
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
            )
        )
    _apply_chunk_hints(merged)
    merged.sort(key=_vector_sort_key)
    return merged[offset : offset + limit]


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
        default=os.environ.get("LANCEDB_URI", "./lancedb_data"),
    )
    parser.add_argument("--path-contains", metavar="SUBSTR", default=None)
    parser.add_argument("--model", default=SBERT_MODEL)
    parser.add_argument("--device", default=None)
    parser.add_argument("--text-width", type=int, default=320)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--fts-text", metavar="TEXT", default=None)
    parser.add_argument("--auto-hybrid", action="store_true")
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

    try:
        results = run_search(
            args.query,
            uri=str(uri_path),
            table_keys=keys,
            limit=args.limit,
            path_substring=args.path_contains,
            model_name=args.model,
            device=args.device,
            hybrid=args.hybrid,
            fts_text=args.fts_text,
            auto_hybrid=args.auto_hybrid,
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
        print(f"--- {i}. [{kind}] {rank_s} | {fn}{line_hint} | lang={lang}{hint_s}")
        print(preview)
        print()


if __name__ == "__main__":
    main()
