#!/usr/bin/env python3
"""Semantic search over ChromaDB collections built by ``java_index_flow_chroma.py``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import chromadb
import numpy as np
from sentence_transformers import SentenceTransformer

from chunk_heuristics import analyze_chunk, looks_like_code_identifier
from index_common import SBERT_MODEL

TABLES: dict[str, str] = {
    "java": "javacodeindex_java_code",
    "sql": "sqlschemaindex_sql_schema",
    "yaml": "yamlconfigindex_yaml_config",
}

_IMPORT_DISTANCE_PENALTY = 0.08


def _client_type_raw() -> str:
    return os.environ.get("CHROMA_MCP_CLIENT", "persistent").strip().lower()


def _persistent_path() -> str:
    return os.path.expandvars(
        os.path.expanduser(os.environ.get("CHROMA_MCP_PATH", "./chromadb_data"))
    )


def connect_chroma_client() -> chromadb.ClientAPI:
    ct = _client_type_raw()
    if ct in ("http", "https"):
        host = os.environ.get("CHROMA_MCP_HOST", "localhost")
        port = int(os.environ.get("CHROMA_MCP_PORT", "8000"))
        ssl = os.environ.get("CHROMA_MCP_SSL", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        return chromadb.HttpClient(
            host=host,
            port=port,
            ssl=ssl,
            tenant=os.environ.get("CHROMA_MCP_TENANT", "default_tenant"),
            database=os.environ.get("CHROMA_MCP_DATABASE", "default_database"),
        )
    if ct == "cloud":
        key = os.environ.get("CHROMA_MCP_API_KEY", "").strip()
        if not key:
            raise ValueError("CHROMA_MCP_CLIENT=cloud requires CHROMA_MCP_API_KEY")
        return chromadb.CloudClient(
            api_key=key,
            tenant=os.environ.get("CHROMA_MCP_TENANT", "default_tenant"),
            database=os.environ.get("CHROMA_MCP_DATABASE", "default_database"),
        )
    return chromadb.PersistentClient(
        path=_persistent_path(),
        tenant=os.environ.get("CHROMA_MCP_TENANT", "default_tenant"),
        database=os.environ.get("CHROMA_MCP_DATABASE", "default_database"),
    )


def connection_summary() -> str:
    ct = _client_type_raw()
    if ct in ("http", "https"):
        host = os.environ.get("CHROMA_MCP_HOST", "localhost")
        port = os.environ.get("CHROMA_MCP_PORT", "8000")
        tls = " (TLS)" if os.environ.get("CHROMA_MCP_SSL", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ) else ""
        return f"http client {host}:{port}{tls}"
    if ct == "cloud":
        tenant = os.environ.get("CHROMA_MCP_TENANT", "default_tenant")
        database = os.environ.get("CHROMA_MCP_DATABASE", "default_database")
        return f"cloud tenant={tenant!r} database={database!r}"
    return f"persistent path={_persistent_path()}"


def _collection_for(kind: str) -> str:
    env_map = {
        "java": "CHROMA_MCP_COLLECTION_JAVA",
        "sql": "CHROMA_MCP_COLLECTION_SQL",
        "yaml": "CHROMA_MCP_COLLECTION_YAML",
    }
    key = env_map.get(kind)
    if key:
        override = os.environ.get(key, "").strip()
        if override:
            return override
    return TABLES[kind]


def list_resolved_collections() -> dict[str, str]:
    return {k: _collection_for(k) for k in TABLES}


def coerce_position_field(val: object) -> dict[str, object]:
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


def _meta_str(meta: dict[str, Any] | None, key: str, default: str = "") -> str:
    if not meta or key not in meta:
        return default
    v = meta[key]
    if v is None:
        return default
    return str(v)


def _parse_row_from_chroma(
    *,
    doc_id: str,
    document: str | None,
    metadata: dict[str, Any] | None,
    distance: float | None,
    kind: str,
    hybrid: bool,
) -> dict[str, Any]:
    meta = metadata or {}
    filename = _meta_str(meta, "filename")
    if not filename and "\x1e" in doc_id:
        filename = doc_id.split("\x1e", 1)[0]
    if not filename:
        filename = doc_id
    lang = _meta_str(meta, "language", "")
    start = coerce_position_field(meta.get("start"))
    end = coerce_position_field(meta.get("end"))
    text = document if document is not None else _meta_str(meta, "text", "")
    row: dict[str, Any] = {
        "filename": filename,
        "text": text,
        "start": start,
        "end": end,
        "language": lang or None,
        "_kind": kind,
        "_hybrid": hybrid,
    }
    if distance is not None:
        row["_distance"] = float(distance)
    return row


def _vector_sort_key(r: dict) -> float:
    d = float(r["_distance"])
    if r.get("_hints", {}).get("import_heavy"):
        d += _IMPORT_DISTANCE_PENALTY
    return d


def cosine_distance_to_score(distance: float) -> float:
    """Chroma cosine distance → higher-is-better score (same convention as pgvector MCP)."""
    return 1.0 - float(distance)


def _query_vector(model: SentenceTransformer, text: str) -> np.ndarray:
    v = model.encode(
        text,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(v, dtype=np.float32)


def _search_one_collection(
    collection,
    *,
    query_vec: np.ndarray,
    fetch_n: int,
    path_substring: str | None,
    kind: str,
    hybrid: bool,
    fts_text: str | None,
) -> list[dict[str, Any]]:
    qvec = query_vec.reshape(1, -1).tolist()
    kwargs: dict[str, Any] = {
        "query_embeddings": qvec,
        "n_results": max(1, min(fetch_n, 500)),
        "include": ["documents", "metadatas", "distances"],
    }
    doc_filter = (fts_text or "").strip()
    if hybrid and doc_filter:
        kwargs["where_document"] = {"$contains": doc_filter}

    raw = collection.query(**kwargs)
    ids_batch = raw.get("ids") or []
    docs_batch = raw.get("documents") or []
    meta_batch = raw.get("metadatas") or []
    dist_batch = raw.get("distances") or []
    if not ids_batch:
        return []
    ids = ids_batch[0]
    docs = docs_batch[0] if docs_batch else []
    metas = meta_batch[0] if meta_batch else []
    dists = dist_batch[0] if dist_batch else []

    rows: list[dict[str, Any]] = []
    for i, doc_id in enumerate(ids):
        doc = docs[i] if i < len(docs) else None
        meta = metas[i] if i < len(metas) else None
        dist = dists[i] if i < len(dists) else None
        rows.append(
            _parse_row_from_chroma(
                doc_id=str(doc_id),
                document=doc,
                metadata=meta,
                distance=float(dist) if dist is not None else None,
                kind=kind,
                hybrid=hybrid,
            )
        )

    if path_substring:
        sub = path_substring.lower()
        rows = [r for r in rows if sub in str(r.get("filename", "")).lower()]

    return rows


def run_search(
    query: str,
    *,
    client: chromadb.ClientAPI | None,
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

    if client is None:
        client = connect_chroma_client()

    if model is None:
        model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
    query_vec = _query_vector(model, query)
    fts_for_hybrid = (effective_fts or "").strip() if effective_hybrid else ""

    need = max(limit + offset, 1)
    fetch_mult = 5 if path_substring else 2
    if effective_hybrid and fts_for_hybrid:
        fetch_mult = max(fetch_mult, 4)

    if len(table_keys) == 1:
        key = table_keys[0]
        cname = _collection_for(key)
        try:
            coll = client.get_collection(cname)
        except Exception as e:
            raise RuntimeError(f"Chroma collection {cname!r}: {e}") from e

        rows = _search_one_collection(
            coll,
            query_vec=query_vec,
            fetch_n=need * fetch_mult,
            path_substring=path_substring,
            kind=key,
            hybrid=bool(effective_hybrid and fts_for_hybrid),
            fts_text=fts_for_hybrid if fts_for_hybrid else None,
        )
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
        rows.sort(key=_vector_sort_key)
        return rows[offset : offset + limit]

    merged: list[dict[str, Any]] = []
    per_coll = max(need * 3, need) * fetch_mult
    for key in table_keys:
        cname = _collection_for(key)
        try:
            coll = client.get_collection(cname)
        except Exception as e:
            raise RuntimeError(f"Chroma collection {cname!r}: {e}") from e
        merged.extend(
            _search_one_collection(
                coll,
                query_vec=query_vec,
                fetch_n=per_coll,
                path_substring=path_substring,
                kind=key,
                hybrid=False,
                fts_text=None,
            )
        )
    for r in merged:
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
    merged.sort(key=_vector_sort_key)
    return merged[offset : offset + limit]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vector search in ChromaDB code index.",
    )
    parser.add_argument("query", help="Natural-language search query")
    parser.add_argument(
        "--table",
        choices=["java", "sql", "yaml", "all"],
        default="java",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--path-contains", metavar="SUBSTR", default=None)
    parser.add_argument("--model", default=SBERT_MODEL)
    parser.add_argument("--device", default=None)
    parser.add_argument("--text-width", type=int, default=320)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--fts-text", metavar="TEXT", default=None)
    parser.add_argument("--auto-hybrid", action="store_true")
    args = parser.parse_args()

    keys = list(TABLES) if args.table == "all" else [args.table]
    if args.hybrid and args.table == "all":
        print("Error: --hybrid needs a single --table.", file=sys.stderr)
        sys.exit(2)
    if args.auto_hybrid and args.table == "all":
        print("Error: --auto-hybrid needs a single --table.", file=sys.stderr)
        sys.exit(2)

    try:
        client = connect_chroma_client()
        results = run_search(
            args.query,
            client=client,
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
        rank_s = f"cosine distance={float(row['_distance']):.4f}"
        hints = row.get("_hints") or {}
        hint_s = ""
        if hints.get("primary_type_hint"):
            hint_s += f" | type:{hints['primary_type_hint']}"
        if hints.get("import_heavy"):
            hint_s += " | mostly-imports"
        hy = " | hybrid-doc-filter" if row.get("_hybrid") else ""
        print(f"--- {i}. [{kind}] {rank_s}{hy} | {fn}{line_hint} | lang={lang}{hint_s}")
        print(preview)
        print()


if __name__ == "__main__":
    main()
