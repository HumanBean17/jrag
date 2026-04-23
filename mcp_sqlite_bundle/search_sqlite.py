#!/usr/bin/env python3
"""Semantic search over SQLite + sqlite-vec tables built by CocoIndex (java_index_flow_sqlite)."""

from __future__ import annotations

import sqlite3_ext_shim  # noqa: F401

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec
from sentence_transformers import SentenceTransformer

from chunk_heuristics import analyze_chunk, looks_like_code_identifier
from index_common import SBERT_MODEL, coerce_json_dict

TABLES: dict[str, str] = {
    "java": "javacodeindex_java_code",
    "sql": "sqlschemaindex_sql_schema",
    "yaml": "yamlconfigindex_yaml_config",
}

VECTOR_COLUMN = "embedding"
_IMPORT_DISTANCE_PENALTY = 0.08

_connection_cache: str | None = None
_connection: sqlite3.Connection | None = None


def l2_distance_to_score(distance: float) -> float:
    """Map L2 distance to a similarity score for unit-normalized embeddings."""
    return 1.0 - distance * distance / 2.0


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    if not hasattr(conn, "enable_load_extension"):
        msg = (
            "sqlite3.Connection has no enable_load_extension — your Python was built "
            "without SQLite extension loading. Use a standard python.org build or a "
            "Python linked against a loadable libsqlite3."
        )
        raise RuntimeError(msg)
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _get_connection(db_path: str) -> sqlite3.Connection:
    global _connection_cache, _connection
    p = str(Path(db_path).expanduser().resolve())
    if not Path(p).is_file():
        raise FileNotFoundError(f"SQLite code index not found: {p}")
    if _connection is not None and _connection_cache == p:
        return _connection
    if _connection is not None:
        _connection.close()
    conn = sqlite3.connect(p, check_same_thread=False)
    _load_vec_extension(conn)
    conn.row_factory = sqlite3.Row
    _connection = conn
    _connection_cache = p
    return conn


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
    return f'"filename" LIKE \'%{pat}%\' ESCAPE \'\\\\\''


def _apply_chunk_hints(rows: list[dict[str, Any]]) -> None:
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


def _vector_sort_key(r: dict[str, Any]) -> float:
    d = float(r["_distance"])
    if r.get("_hints", {}).get("import_heavy"):
        d += _IMPORT_DISTANCE_PENALTY
    return d


def _query_vector(model: SentenceTransformer, text: str) -> np.ndarray:
    v = model.encode(
        text,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(v, dtype=np.float32)


def _search_one_table(
    conn: sqlite3.Connection,
    physical_table: str,
    *,
    kind: str,
    query_vec: np.ndarray,
    limit: int,
    path_predicate: str | None,
) -> list[dict[str, Any]]:
    has_lang = kind == "java"
    if has_lang:
        base_cols = '"id", "filename", "text", "start", "end", "range_start", "range_end", "language"'
    else:
        base_cols = '"id", "filename", "text", "start", "end", "range_start", "range_end"'

    q_blob = sqlite_vec.serialize_float32(query_vec.astype(np.float32, copy=False).tolist())
    wh = f'vec_distance_l2("{VECTOR_COLUMN}", ?) AS _distance'
    where_sql = "WHERE 1=1"
    if path_predicate:
        where_sql += f" AND ({path_predicate})"
    order_sql = "ORDER BY _distance ASC"
    limit_sql = f"LIMIT {int(limit)}"

    sql = (
        f"SELECT {base_cols}, {wh} "
        f'FROM "{physical_table}" {where_sql} {order_sql} {limit_sql}'
    )
    cur = conn.execute(sql, (q_blob,))
    rows: list[dict[str, Any]] = []
    for row in cur.fetchall():
        d = {k: row[k] for k in row.keys()}
        d.pop("_distance", None)
        dist = float(row["_distance"])
        d["_kind"] = kind
        d["_hybrid"] = False
        d["_distance"] = dist
        d["start"] = coerce_json_dict(d.get("start"))
        d["end"] = coerce_json_dict(d.get("end"))
        if not has_lang:
            d["language"] = None
        rows.append(d)
    return rows


def run_search(
    query: str,
    *,
    db_path: str,
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
) -> list[dict[str, Any]]:
    if hybrid or fts_text is not None or auto_hybrid:
        raise ValueError(
            "SQLite search supports vector search only. "
            "Do not set hybrid, fts_text, or auto_hybrid."
        )
    if limit + offset < 1:
        return []

    path_predicate = _build_path_predicate(path_substring) if path_substring else None
    if model is None:
        model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
    query_vec = _query_vector(model, query)
    conn = _get_connection(db_path)
    need = max(limit + offset, 1)

    if len(table_keys) == 1:
        key = table_keys[0]
        rows = _search_one_table(
            conn,
            TABLES[key],
            kind=key,
            query_vec=query_vec,
            limit=need,
            path_predicate=path_predicate,
        )
        _apply_chunk_hints(rows)
        rows.sort(key=_vector_sort_key)
        return rows[offset : offset + limit]

    merged: list[dict[str, Any]] = []
    per_table = max(need * 3, need)
    for key in table_keys:
        merged.extend(
            _search_one_table(
                conn,
                TABLES[key],
                kind=key,
                query_vec=query_vec,
                limit=per_table,
                path_predicate=path_predicate,
            )
        )
    _apply_chunk_hints(merged)
    merged.sort(key=_vector_sort_key)
    return merged[offset : offset + limit]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vector search in SQLite code index (sqlite-vec).",
    )
    parser.add_argument("query", help="Natural-language search query")
    parser.add_argument(
        "--table",
        choices=["java", "sql", "yaml", "all"],
        default="java",
    )
    parser.add_argument("--limit", type=int, default=10)
    raw_db = os.environ.get("SQLITE_CODE_INDEX_DB")
    db_default = (
        str(Path(raw_db).expanduser())
        if raw_db and str(raw_db).strip()
        else str((Path.cwd() / "java_code_index.sqlite").resolve())
    )
    parser.add_argument(
        "--db",
        default=db_default,
        help="Path to SQLite DB (or SQLITE_CODE_INDEX_DB; default: ./java_code_index.sqlite in cwd).",
    )
    parser.add_argument("--path-contains", metavar="SUBSTR", default=None)
    parser.add_argument("--model", default=SBERT_MODEL)
    parser.add_argument("--device", default=None)
    parser.add_argument("--text-width", type=int, default=320)
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        print(f"Error: SQLite database missing: {db_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    keys = list(TABLES) if args.table == "all" else [args.table]

    try:
        results = run_search(
            args.query,
            db_path=str(db_path.resolve()),
            table_keys=keys,
            limit=args.limit,
            path_substring=args.path_contains,
            model_name=args.model,
            device=args.device,
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
            el = end["line"] if isinstance(end, dict) and "line" in end else start["line"]
            line_hint = f" L{start['line']}-{el}"
        text = (row.get("text") or "").replace("\n", " ")
        preview = text if len(text) <= w else text[: w - 3] + "..."
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
