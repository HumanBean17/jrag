#!/usr/bin/env python3
"""Semantic search over Postgres + pgvector tables built by ``java_index_flow_postgres.py``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

import numpy as np
from pgvector.psycopg import register_vector
from psycopg import sql
import psycopg
from sentence_transformers import SentenceTransformer

from chunk_heuristics import analyze_chunk, looks_like_code_identifier
from index_common import SBERT_MODEL

TABLES: dict[str, str] = {
    "java": "javacodeindex_java_code",
    "sql": "sqlschemaindex_sql_schema",
    "yaml": "yamlconfigindex_yaml_config",
}

_RRF_K = 60

_IMPORT_DISTANCE_PENALTY = 0.08
_IMPORT_HYBRID_SCORE_FACTOR = 0.88


def _schema() -> str:
    return os.environ.get("PGVECTOR_MCP_SCHEMA", "public").strip() or "public"


def resolve_database_url() -> str:
    for key in (
        "PGVECTOR_MCP_DATABASE_URL",
        "DATABASE_URL",
        "COCOINDEX_DATABASE_URL",
    ):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return ""


def database_url_preview(dsn: str) -> str:
    """Host, port, and database name only (no user/password)."""
    if not dsn.strip():
        return ""
    try:
        u = urlparse(dsn)
        host = u.hostname or ""
        port = f":{u.port}" if u.port else ""
        db = (u.path or "/").lstrip("/").split("?")[0]
        return f"postgresql://{host}{port}/{db}"
    except Exception:
        return "(unparsed-dsn)"


def _table_for(kind: str) -> str:
    env_map = {
        "java": "PGVECTOR_MCP_TABLE_JAVA",
        "sql": "PGVECTOR_MCP_TABLE_SQL",
        "yaml": "PGVECTOR_MCP_TABLE_YAML",
    }
    env_key = env_map.get(kind)
    if env_key:
        override = os.environ.get(env_key, "").strip()
        if override:
            return override
    return TABLES[kind]


def list_resolved_tables() -> dict[str, str]:
    """Logical key → physical table name (after env overrides)."""
    return {k: _table_for(k) for k in TABLES}


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


def cosine_distance_to_score(distance: float) -> float:
    """Cosine distance from pgvector ``<=>``; map to a higher-is-better score (see pgvector docs)."""
    return 1.0 - float(distance)


def _row_identity(filename: str, location: Any) -> str:
    if isinstance(location, (dict, list)):
        loc_s = json.dumps(location, sort_keys=True)
    else:
        loc_s = str(location)
    return f"{filename}\0{loc_s}"


def coerce_position_field(val: object) -> dict[str, object]:
    """Postgres jsonb may decode to dict; Lance sometimes used JSON strings."""
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


def _query_vector(model: SentenceTransformer, text: str) -> np.ndarray:
    v = model.encode(
        text,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(v, dtype=np.float32)


def _build_path_filter(kind: str, path_substring: str | None) -> tuple[sql.SQL | None, list[Any]]:
    if not path_substring:
        return None, []
    pat = path_substring.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_expr = sql.SQL("filename LIKE {} ESCAPE {}").format(
        sql.Literal(f"%{pat}%"),
        sql.Literal("\\"),
    )
    return like_expr, []


def _qualified(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _search_vector(
    conn: psycopg.Connection,
    *,
    schema: str,
    table: str,
    kind: str,
    query_vec: np.ndarray,
    limit: int,
    offset: int,
    path_substring: str | None,
) -> list[dict[str, Any]]:
    qt = _qualified(schema, table)
    where_extra, _ = _build_path_filter(kind, path_substring)
    has_lang = kind == "java"

    parts: list[Any] = [
        sql.SQL('SELECT filename, location, text, "start", "end"'),
    ]
    if has_lang:
        parts.append(sql.SQL(", language"))
    parts.extend(
        [
            sql.SQL(", (embedding <=> %s::vector) AS _distance FROM "),
            qt,
            sql.SQL(" AS t"),
        ]
    )
    stmt = sql.SQL(" ").join(parts)
    if where_extra is not None:
        stmt = sql.SQL(" ").join([stmt, sql.SQL("WHERE "), where_extra])
    stmt = sql.SQL(" ").join(
        [stmt, sql.SQL("ORDER BY embedding <=> %s::vector LIMIT %s OFFSET %s")]
    )

    params: list[Any] = [query_vec, query_vec, limit, offset]
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        names = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for tup in rows:
        r = dict(zip(names, tup))
        r["_kind"] = kind
        r["_hybrid"] = False
        r["start"] = coerce_position_field(r.get("start"))
        r["end"] = coerce_position_field(r.get("end"))
        if "location" in r and isinstance(r["location"], (dict, list)):
            pass
        elif isinstance(r.get("location"), str):
            try:
                r["location"] = json.loads(r["location"])
            except json.JSONDecodeError:
                r["location"] = {}
        out.append(r)
    return out


def _search_fts(
    conn: psycopg.Connection,
    *,
    schema: str,
    table: str,
    kind: str,
    fts_query: str,
    limit: int,
    path_substring: str | None,
) -> list[dict[str, Any]]:
    qt = _qualified(schema, table)
    where_extra, _ = _build_path_filter(kind, path_substring)
    has_lang = kind == "java"

    base_sel = sql.SQL(
        'SELECT filename, location, text, "start", "end"'
    )
    if has_lang:
        base_sel = sql.SQL(" ").join([base_sel, sql.SQL(", language")])
    base_sel = sql.SQL(" ").join(
        [
            base_sel,
            sql.SQL(
                ", ts_rank_cd(to_tsvector('english', text), "
                "plainto_tsquery('english', %s)) AS _fts_rank FROM "
            ),
            qt,
            sql.SQL(
                " AS t WHERE to_tsvector('english', text) @@ "
                "plainto_tsquery('english', %s)"
            ),
        ]
    )
    if where_extra is not None:
        base_sel = sql.SQL(" ").join([base_sel, sql.SQL("AND "), where_extra])
    stmt = sql.SQL(" ").join(
        [
            base_sel,
            sql.SQL("ORDER BY _fts_rank DESC NULLS LAST LIMIT %s"),
        ]
    )

    params: list[Any] = [fts_query, fts_query, limit]
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        names = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for tup in rows:
        r = dict(zip(names, tup))
        r["_kind"] = kind
        r["start"] = coerce_position_field(r.get("start"))
        r["end"] = coerce_position_field(r.get("end"))
        if isinstance(r.get("location"), str):
            try:
                r["location"] = json.loads(r["location"])
            except json.JSONDecodeError:
                r["location"] = {}
        out.append(r)
    return out


def _rrf_merge(
    vec_rows: list[dict[str, Any]],
    fts_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    vec_ranks: dict[str, int] = {}
    for i, r in enumerate(vec_rows):
        key = _row_identity(str(r["filename"]), r.get("location"))
        vec_ranks[key] = i + 1

    fts_ranks: dict[str, int] = {}
    for i, r in enumerate(fts_rows):
        key = _row_identity(str(r["filename"]), r.get("location"))
        fts_ranks[key] = i + 1

    all_keys = list(dict.fromkeys([*vec_ranks.keys(), *fts_ranks.keys()]))
    by_key: dict[str, dict[str, Any]] = {}
    for r in vec_rows:
        k = _row_identity(str(r["filename"]), r.get("location"))
        by_key[k] = dict(r)
    for r in fts_rows:
        k = _row_identity(str(r["filename"]), r.get("location"))
        if k not in by_key:
            by_key[k] = dict(r)

    scored: list[tuple[float, dict[str, Any]]] = []
    for k in all_keys:
        s = 0.0
        if k in vec_ranks:
            s += 1.0 / (_RRF_K + vec_ranks[k])
        if k in fts_ranks:
            s += 1.0 / (_RRF_K + fts_ranks[k])
        row = by_key.get(k)
        if row is not None:
            row["_score"] = s
            row["_hybrid"] = True
            row["_distance"] = float(row.get("_distance", 1.0))
            scored.append((s, row))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]


def _search_one_table(
    *,
    conn: psycopg.Connection,
    schema: str,
    kind: str,
    query_vec: np.ndarray,
    out_limit: int,
    out_offset: int,
    path_substring: str | None,
    hybrid: bool,
    fts_text: str | None,
) -> list[dict[str, Any]]:
    table = _table_for(kind)
    if hybrid:
        need = max(out_limit + out_offset, 1) * 3
        fts_q = fts_text if fts_text is not None else ""
        vec_rows = _search_vector(
            conn,
            schema=schema,
            table=table,
            kind=kind,
            query_vec=query_vec,
            limit=need,
            offset=0,
            path_substring=path_substring,
        )
        fts_rows = _search_fts(
            conn,
            schema=schema,
            table=table,
            kind=kind,
            fts_query=fts_q,
            limit=need,
            path_substring=path_substring,
        )

        key_to_vec = {
            _row_identity(str(r["filename"]), r.get("location")): r for r in vec_rows
        }
        if not fts_rows:
            merged = vec_rows
            merged.sort(key=_vector_sort_key)
            return merged[out_offset : out_offset + out_limit]
        merged = _rrf_merge(vec_rows, fts_rows)
        for r in merged:
            vid = _row_identity(str(r["filename"]), r.get("location"))
            vr = key_to_vec.get(vid)
            if vr is not None:
                r["_distance"] = vr.get("_distance", 0.0)
            else:
                r.setdefault("_distance", 1.0)
        merged.sort(key=_hybrid_sort_key)
        return merged[out_offset : out_offset + out_limit]

    return _search_vector(
        conn,
        schema=schema,
        table=table,
        kind=kind,
        query_vec=query_vec,
        limit=out_limit,
        offset=out_offset,
        path_substring=path_substring,
    )


def run_search(
    query: str,
    *,
    dsn: str,
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
    schema: str | None = None,
) -> list[dict[str, Any]]:
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

    if model is None:
        model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
    query_vec = _query_vector(model, query)
    fts_for_hybrid = effective_fts if effective_fts is not None else query

    schema_eff = (schema.strip() if schema else _schema()) or "public"
    need = max(limit + offset, 1)

    with psycopg.connect(dsn) as conn:
        register_vector(conn)

        if len(table_keys) == 1:
            key = table_keys[0]
            rows = _search_one_table(
                conn=conn,
                schema=schema_eff,
                kind=key,
                query_vec=query_vec,
                out_limit=limit,
                out_offset=offset,
                path_substring=path_substring,
                hybrid=effective_hybrid,
                fts_text=fts_for_hybrid,
            )
            _apply_chunk_hints(rows)
            if effective_hybrid:
                rows.sort(key=_hybrid_sort_key)
            else:
                rows.sort(key=_vector_sort_key)
            return rows

        merged: list[dict[str, Any]] = []
        per_table = max(need * 3, need)
        for key in table_keys:
            merged.extend(
                _search_one_table(
                    conn=conn,
                    schema=schema_eff,
                    kind=key,
                    query_vec=query_vec,
                    out_limit=per_table,
                    out_offset=0,
                    path_substring=path_substring,
                    hybrid=False,
                    fts_text=None,
                )
            )
        _apply_chunk_hints(merged)
        merged.sort(key=_vector_sort_key)
        return merged[offset : offset + limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Vector search in Postgres (pgvector).")
    parser.add_argument("query", help="Natural-language search query")
    parser.add_argument(
        "--table",
        choices=["java", "sql", "yaml", "all"],
        default="java",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--schema",
        default=None,
        help="Postgres schema (default: PGVECTOR_MCP_SCHEMA or public)",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN (default: PGVECTOR_MCP_DATABASE_URL / DATABASE_URL / COCOINDEX_DATABASE_URL)",
    )
    parser.add_argument("--path-contains", metavar="SUBSTR", default=None)
    parser.add_argument("--model", default=SBERT_MODEL)
    parser.add_argument("--device", default=None)
    parser.add_argument("--text-width", type=int, default=320)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--fts-text", metavar="TEXT", default=None)
    parser.add_argument("--auto-hybrid", action="store_true")
    args = parser.parse_args()

    if args.dsn:
        dsn = args.dsn
    else:
        try:
            dsn = resolve_database_url()
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            print("Or pass --dsn.", file=sys.stderr)
            sys.exit(2)

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
            dsn=dsn,
            table_keys=keys,
            limit=args.limit,
            path_substring=args.path_contains,
            model_name=args.model,
            device=args.device,
            offset=args.offset,
            hybrid=args.hybrid,
            fts_text=args.fts_text,
            auto_hybrid=args.auto_hybrid,
            schema=args.schema,
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
        lang = row.get("language") or "—"
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
            rank_s = f"cosine distance={float(row['_distance']):.4f}"
        hints = row.get("_hints") or {}
        hint_s = ""
        if hints.get("primary_type_hint"):
            hint_s += f" | type:{hints['primary_type_hint']}"
        if hints.get("import_heavy"):
            hint_s += " | mostly-imports"
        print(
            f"--- {i}. [{kind}] {rank_s} | {fn}{line_hint} | lang={lang}{hint_s}"
        )
        print(preview)
        print()


if __name__ == "__main__":
    main()
