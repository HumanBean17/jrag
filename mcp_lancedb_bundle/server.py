#!/usr/bin/env python3
"""LanceDB code-search MCP (stdio). Self-contained bundle — copy this folder anywhere.

Run:
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  LANCEDB_URI=/abs/path/to/lancedb_data .venv/bin/python server.py

Claude Code (project): see README.md and mcp.json.example (includes Java AST graph / Kuzu).
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

from index_common import SBERT_MODEL
from search_lancedb import TABLES, l2_distance_to_score, run_search

try:
    from java_ast_graph.graph_retriever import (
        collect_graph_seeds,
        expand_interface_consumers,
        expand_neighbors_bidirectional,
        find_types_by_name_substring,
        find_types_in_file_by_rel_path,
        get_readonly_graph,
        guess_identifier_seeds,
        guess_substring_seeds_from_query,
        list_implementors,
        list_injectors_of,
        read_file_snippet,
    )
    from java_ast_graph.hybrid_rrf import fuse_vector_and_graph
    from java_ast_graph.kuzu_io import default_db_path as _kuzu_default_path
except ImportError:
    get_readonly_graph = None  # type: ignore[misc, assignment]
    collect_graph_seeds = None  # type: ignore[misc, assignment]
    expand_interface_consumers = None  # type: ignore[misc, assignment]
    expand_neighbors_bidirectional = None  # type: ignore[misc, assignment]
    find_types_by_name_substring = None  # type: ignore[misc, assignment]
    find_types_in_file_by_rel_path = None  # type: ignore[misc, assignment]
    guess_identifier_seeds = None  # type: ignore[misc, assignment]
    guess_substring_seeds_from_query = None  # type: ignore[misc, assignment]
    list_implementors = None  # type: ignore[misc, assignment]
    list_injectors_of = None  # type: ignore[misc, assignment]
    read_file_snippet = None  # type: ignore[misc, assignment]
    fuse_vector_and_graph = None  # type: ignore[misc, assignment]
    _kuzu_default_path = None  # type: ignore[misc, assignment]

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"

_INSTRUCTIONS = (
    "Semantic search over a LanceDB code index (Java, Flyway SQL, YAML configs). "
    "Use for meaning-based discovery—not exact keywords. Prefer limit 5; use offset to page. "
    "In codebase_search, hybrid=true means vector + full-text (FTS) RRF on one table—not Kuzu graph. "
    "It needs an inverted FTS index on the text column (created at query time when possible). "
    "Check hybrid_attempted vs hybrid_used; fallback messages are prefixed HYBRID_FALLBACK:. "
    "If FTS/hybrid is unavailable, results are vector-only. "
    "hybrid=true cannot be used with table=all. "
    "AST graph (Kuzu Cypher, not Neo4j: use named rel types, not type(r)): "
    "T_EXTENDS, T_IMPLEMENTS, T_INJECTS, F_DECLARED_IN, T_IN_PACKAGE, M_DECLARED; "
    "example: MATCH (a:Type)-[:T_EXTENDS]->(b:Type) RETURN a.fqn, b.fqn LIMIT 20. "
    "Type FQNs follow the indexed repository—do not assume a specific subpackage. "
    "Tools: graph_implementors, graph_injectors, graph_expand_from_type_seed, graph_match; "
    "codebase_vector_graph runs DKB query-time: vector top-k, seeds from query + chunk text, "
    "bidirectional graph expansion, optional interface–consumer pass (implementors + injectors), "
    "then RRF with vector chunks—no FTS; use codebase_search hybrid for vector+FTS only. "
    "Same normalized file path is merged. "
    "Build graph: python -m java_ast_graph.build (KUZU_DB_PATH, GRAPH_SOURCE_ROOTS). "
    "refresh_code_index requires LANCEDB_MCP_ALLOW_REFRESH=1; optional GRAPH_BUILD_ON_REFRESH=1 "
    "runs the graph build after CocoIndex."
)


class CodeChunkHit(BaseModel):
    file_path: str = Field(description="Project-relative file path")
    language: str = Field(description="Detected or inferred language")
    content: str = Field(description="Chunk text")
    start_line: int = Field(description="Start line (1-based); 0 if unknown")
    end_line: int = Field(description="End line (1-based); 0 if unknown")
    start_byte: int = Field(
        default=0,
        description="Chunk start byte offset in the source file (UTF-8)",
    )
    end_byte: int = Field(
        default=0,
        description="Chunk end byte offset in the source file (exclusive)",
    )
    score: float = Field(
        description=(
            "Relevance: vector mode uses L2-on-unit-vector similarity; "
            "hybrid mode uses LanceDB RRF. Higher is better."
        ),
    )
    chunk_kind: str = Field(description="java | sql | yaml")
    primary_type_hint: str | None = Field(
        default=None,
        description="Heuristic Java type name if detected in chunk.",
    )
    import_heavy: bool = Field(
        default=False,
        description="Heuristic: mostly import lines (downranked).",
    )


class CodeSearchOutput(BaseModel):
    success: bool
    results: list[CodeChunkHit] = Field(default_factory=list)
    message: str | None = None
    hybrid_attempted: bool = Field(
        default=False,
        description="True when this request used vector+FTS (hybrid or auto_hybrid) on a single table.",
    )
    hybrid_used: bool = Field(
        default=False,
        description="True when results are vector+FTS RRF; false if vector-only (e.g. missing FTS index).",
    )


class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    refresh_enabled: bool
    cocoindex_target: str
    tables: dict[str, str]
    kuzu_db_path: str | None = None
    kuzu_db_exists: bool = False
    graph_build_on_refresh: bool = False


class RefreshIndexOutput(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None
    graph_exit_code: int | None = None
    graph_stdout: str = ""
    graph_stderr: str = ""


class GraphNamesOutput(BaseModel):
    success: bool
    names: list[str] = Field(default_factory=list)
    message: str | None = None


class GraphRowsOutput(BaseModel):
    success: bool
    rows: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None


class GraphMatchOutput(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    message: str | None = None


class HybridRagOutput(BaseModel):
    success: bool
    items: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None


_st_lock = threading.Lock()
_st_model: SentenceTransformer | None = None


def _get_sentence_transformer(model_name: str, device: str | None) -> SentenceTransformer:
    global _st_model
    with _st_lock:
        if _st_model is None:
            _st_model = SentenceTransformer(
                model_name,
                device=device,
                trust_remote_code=True,
            )
        return _st_model


def _resolve_lancedb_uri() -> str:
    raw = os.environ.get("LANCEDB_URI", "./lancedb_data")
    p = Path(raw)
    if p.exists() and not raw.startswith(("s3://", "gs://", "az://")):
        return str(p.resolve())
    return raw


def _project_root() -> Path:
    env = os.environ.get("LANCEDB_MCP_PROJECT_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent


def _refresh_allowed() -> bool:
    return os.environ.get("LANCEDB_MCP_ALLOW_REFRESH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _kuzu_path_str() -> str | None:
    if _kuzu_default_path is None:
        return None
    return str(_kuzu_default_path())


def _graph_conn():
    if get_readonly_graph is None:
        return None
    return get_readonly_graph()


def _rows_to_hits(rows: list[dict[str, Any]]) -> list[CodeChunkHit]:
    hits: list[CodeChunkHit] = []
    for r in rows:
        start = r.get("start") or {}
        end = r.get("end") or {}
        sl = int(start["line"]) if isinstance(start, dict) and "line" in start else 0
        el = (
            int(end["line"])
            if isinstance(end, dict) and "line" in end
            else sl
        )
        sb = int(start["byte_offset"]) if isinstance(start, dict) and "byte_offset" in start else 0
        eb = int(end["byte_offset"]) if isinstance(end, dict) and "byte_offset" in end else 0
        kind = str(r.get("_kind", "java"))
        lang = r.get("language")
        if not lang:
            lang = "sql" if kind == "sql" else "yaml" if kind == "yaml" else "text"
        if r.get("_hybrid"):
            score = float(r.get("_score", 0.0))
        else:
            score = l2_distance_to_score(float(r["_distance"]))
        hints = r.get("_hints") or {}
        hits.append(
            CodeChunkHit(
                file_path=str(r["filename"]),
                language=str(lang),
                content=str(r.get("text") or ""),
                start_line=sl,
                end_line=el,
                start_byte=sb,
                end_byte=eb,
                score=score,
                chunk_kind=kind,
                primary_type_hint=hints.get("primary_type_hint"),
                import_heavy=bool(hints.get("import_heavy")),
            )
        )
    return hits


def create_mcp_server() -> FastMCP:
    mcp = FastMCP("lancedb-code-search", instructions=_INSTRUCTIONS)

    @mcp.tool(
        name="codebase_search",
        description=(
            "Vector or vector+FTS search: hybrid=true / auto_hybrid combine embeddings with full-text "
            "on one table (RRF)—not the Kuzu graph. Single table only for hybrid. "
            "Needs an inverted FTS index on the text column; on failure, vector-only results and a message. "
            "For vector + structural graph (Kuzu) + RRF, use codebase_vector_graph."
        ),
    )
    async def codebase_search(
        query: str = Field(description="Search query"),
        table: str = Field(default="java", description="java | sql | yaml | all"),
        limit: int = Field(default=5, ge=1, le=50),
        offset: int = Field(default=0, ge=0, le=500),
        path_contains: str | None = Field(default=None),
        hybrid: bool = Field(
            default=False,
            description="If true, run vector+full-text (FTS) RRF on one table (not Kuzu/AST graph).",
        ),
        fts_text: str | None = Field(default=None),
        auto_hybrid: bool = Field(
            default=False,
            description="Auto-enable vector+FTS hybrid when the table has FTS; not graph RAG.",
        ),
    ) -> CodeSearchOutput:
        if table not in ("java", "sql", "yaml", "all"):
            return CodeSearchOutput(
                success=False,
                message=f"Invalid table={table!r}; use java, sql, yaml, or all.",
            )
        if hybrid and table == "all":
            return CodeSearchOutput(
                success=False,
                message="hybrid=true requires a single table, not all.",
            )
        if auto_hybrid and table == "all":
            return CodeSearchOutput(
                success=False,
                message="auto_hybrid=true requires a single table, not all.",
            )

        uri = _resolve_lancedb_uri()
        is_remote = uri.startswith(("s3://", "gs://", "az://"))
        if not is_remote and not Path(uri).exists():
            return CodeSearchOutput(
                success=False,
                message=(
                    f"LanceDB path does not exist: {uri}. "
                    "Set LANCEDB_URI to your lancedb_data directory."
                ),
            )

        model_name = os.environ.get("SBERT_MODEL", SBERT_MODEL)
        device = os.environ.get("SBERT_DEVICE") or None
        keys = list(TABLES) if table == "all" else [table]

        def _run() -> tuple[
            list[dict[str, Any]],
            str | None,
            dict[str, bool] | None,
        ]:
            model = _get_sentence_transformer(model_name, device)
            return run_search(
                query,
                uri=uri,
                table_keys=keys,
                limit=limit,
                offset=offset,
                path_substring=path_contains,
                model_name=model_name,
                device=device,
                model=model,
                hybrid=hybrid,
                fts_text=fts_text,
                auto_hybrid=auto_hybrid,
            )

        try:
            rows, fts_notice, hmeta = await asyncio.to_thread(_run)
        except Exception as e:
            return CodeSearchOutput(success=False, message=f"Search failed: {e!s}")

        h_att = hmeta.get("hybrid_attempted", False) if hmeta else False
        h_used = hmeta.get("hybrid_used", False) if hmeta else False
        msg: str | None = fts_notice
        if (
            msg
            and h_att
            and not h_used
        ):
            msg = f"HYBRID_FALLBACK: {msg}"
        return CodeSearchOutput(
            success=True,
            results=_rows_to_hits(rows),
            message=msg,
            hybrid_attempted=h_att,
            hybrid_used=h_used,
        )

    @mcp.tool(name="list_code_index_tables")
    async def list_code_index_tables() -> IndexInfoOutput:
        kp = _kuzu_path_str()
        ex = bool(kp and Path(kp).exists())
        return IndexInfoOutput(
            lancedb_uri=_resolve_lancedb_uri(),
            embedding_model=os.environ.get("SBERT_MODEL", SBERT_MODEL),
            project_root=str(_project_root()),
            refresh_enabled=_refresh_allowed(),
            cocoindex_target=_COCOINDEX_TARGET,
            tables=dict(TABLES),
            kuzu_db_path=kp,
            kuzu_db_exists=ex,
            graph_build_on_refresh=_env_truthy("GRAPH_BUILD_ON_REFRESH"),
        )

    @mcp.tool(name="refresh_code_index")
    async def refresh_code_index(
        confirm: bool = Field(
            default=False,
            description="Must be true to run cocoindex (slow).",
        ),
    ) -> RefreshIndexOutput:
        if not _refresh_allowed():
            return RefreshIndexOutput(
                success=False,
                message="Set LANCEDB_MCP_ALLOW_REFRESH=1 to enable.",
            )
        if not confirm:
            return RefreshIndexOutput(
                success=False,
                message="Pass confirm=true to run indexing.",
            )

        root = _project_root()
        cocoindex_bin = Path(sys.executable).resolve().parent / "cocoindex"
        if not cocoindex_bin.is_file():
            return RefreshIndexOutput(
                success=False,
                message=f"cocoindex not found next to Python: {cocoindex_bin}",
            )
        flow_path = root / "java_index_flow_lancedb.py"
        if not flow_path.is_file():
            return RefreshIndexOutput(
                success=False,
                message=f"java_index_flow_lancedb.py not under LANCEDB_MCP_PROJECT_ROOT: {root}",
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                str(cocoindex_bin),
                "update",
                _COCOINDEX_TARGET,
                "--full-reprocess",
                "-f",
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out_b, err_b = await proc.communicate()
        except Exception as e:
            return RefreshIndexOutput(success=False, message=f"spawn failed: {e!s}")

        out = out_b.decode(errors="replace")
        err = err_b.decode(errors="replace")
        ok = proc.returncode == 0
        g_out = ""
        g_err = ""
        g_code: int | None = None
        if ok and _env_truthy("GRAPH_BUILD_ON_REFRESH"):
            try:
                gproc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "java_ast_graph.build",
                    "--quiet",
                    cwd=str(root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                )
                go_b, ge_b = await gproc.communicate()
                g_out = go_b.decode(errors="replace")
                g_err = ge_b.decode(errors="replace")
                g_code = gproc.returncode
            except Exception as ge:
                g_err = str(ge)
                g_code = -1
        return RefreshIndexOutput(
            success=ok,
            exit_code=proc.returncode,
            stdout=out[-8000:] if len(out) > 8000 else out,
            stderr=err[-8000:] if len(err) > 8000 else err,
            message=None if ok else f"exit {proc.returncode}",
            graph_exit_code=g_code,
            graph_stdout=g_out[-4000:] if len(g_out) > 4000 else g_out,
            graph_stderr=g_err[-4000:] if len(g_err) > 4000 else g_err,
        )

    def _rel_from_file_key(file_key: str) -> str:
        return file_key.split("::", 1)[-1] if "::" in file_key else file_key

    @mcp.tool(
        name="graph_implementors",
        description='List types that T_IMPLEMENTS a given interface FQN (Java AST graph / Kuzu).',
    )
    async def graph_implementors(
        interface_fqn: str = Field(description="Fully qualified interface name"),
        limit: int = Field(default=50, ge=1, le=500),
    ) -> GraphNamesOutput:
        gc = _graph_conn()
        if not gc or list_implementors is None:
            return GraphNamesOutput(
                success=False,
                message="Kuzu graph unavailable (build with: python -m java_ast_graph.build).",
            )
        conn, _p = gc
        try:
            names = await asyncio.to_thread(list_implementors, conn, interface_fqn, limit=limit)
        finally:
            conn.close()
        return GraphNamesOutput(success=True, names=names)

    @mcp.tool(
        name="graph_injectors",
        description="List types with a T_INJECTS edge into the given type (upstream injectors).",
    )
    async def graph_injectors(
        type_fqn: str = Field(description="Fully qualified type name"),
        limit: int = Field(default=50, ge=1, le=500),
    ) -> GraphNamesOutput:
        gc = _graph_conn()
        if not gc or list_injectors_of is None:
            return GraphNamesOutput(
                success=False,
                message="Kuzu graph unavailable (build with: python -m java_ast_graph.build).",
            )
        conn, _p = gc
        try:
            names = await asyncio.to_thread(list_injectors_of, conn, type_fqn, limit=limit)
        finally:
            conn.close()
        return GraphNamesOutput(success=True, names=names)

    @mcp.tool(
        name="graph_expand_from_type_seed",
        description=(
            "Manual graph expansion from a type name substring (no vector step). For full DKB "
            "retrieval (vector + chunk entity seeds + interface expansion + RRF), use "
            "codebase_vector_graph."
        ),
    )
    async def graph_expand_from_type_seed(
        type_name_seed: str = Field(
            description="Substring of FQN or simple name to look up (e.g. PaymentService)",
        ),
        depth: int = Field(default=1, ge=1, le=4),
        limit: int = Field(default=80, ge=1, le=300),
    ) -> GraphRowsOutput:
        gc = _graph_conn()
        if (
            not gc
            or find_types_by_name_substring is None
            or expand_neighbors_bidirectional is None
        ):
            return GraphRowsOutput(
                success=False,
                message="Kuzu graph unavailable (build with: python -m java_ast_graph.build).",
            )
        conn, _p = gc
        try:
            hits = await asyncio.to_thread(
                find_types_by_name_substring, conn, type_name_seed, limit=12
            )
            seeds = [h.fqn for h in hits]
            if not seeds:
                return GraphRowsOutput(success=True, rows=[], message="No matching types.")
            rows = await asyncio.to_thread(
                expand_neighbors_bidirectional,
                conn,
                seeds,
                depth=depth,
                limit=limit,
            )
        finally:
            conn.close()
        return GraphRowsOutput(success=True, rows=rows)

    @mcp.tool(
        name="graph_match",
        description=(
            "Read-only Cypher on the Kuzu graph (must start with MATCH). "
            "Kuzu is not Neo4j: avoid type(r); name relationships as "
            "[:T_EXTENDS], [:T_IMPLEMENTS], [:T_INJECTS] (or [:F_DECLARED_IN], "
            "[:M_DECLARED], [:T_IN_PACKAGE] to files/methods/packages). "
        ),
    )
    async def graph_match(
        cypher: str = Field(
            description=(
                "Read query. Example: MATCH (a:Type)-[:T_EXTENDS]->(b:Type) "
                "RETURN a.fqn, b.fqn LIMIT 20"
            ),
        ),
    ) -> GraphMatchOutput:
        q = cypher.strip()
        low = q.lower()
        if not low.startswith("match"):
            return GraphMatchOutput(
                success=False,
                message="Only queries starting with MATCH are allowed.",
            )
        for bad in (" delete", " detach", " drop", " create ", " merge ", " set "):
            if bad in low:
                return GraphMatchOutput(
                    success=False,
                    message=f"Disallowed token in query: {bad.strip()!r}",
                )
        gc = _graph_conn()
        if not gc:
            return GraphMatchOutput(
                success=False,
                message="Kuzu graph unavailable.",
            )
        conn, _p = gc
        try:
            res = await asyncio.to_thread(conn.execute, q)
            cols = res.get_column_names()
            data = res.get_all()
        except Exception as e:
            return GraphMatchOutput(success=False, message=str(e))
        finally:
            conn.close()
        return GraphMatchOutput(success=True, columns=list(cols), rows=data)

    @mcp.tool(
        name="codebase_vector_graph",
        description=(
            "Vector + Kuzu structural graph (DKB query-time, no FTS): (1) vector top-k, "
            "(2) graph seeds from query + optional chunk text, (3) bidirectional hop expansion, "
            "(4) interface–consumer add pass (implementors + injectors), (5) RRF with vector rows. "
            "Different from codebase_search with hybrid=true (that is vector+FTS). "
            "`limit` = vector hit count. Type FQNs follow the indexed project. "
            "Rows with the same normalized file path are merged. "
            "Reduce `graph_limit` / `snippet_max_bytes` / `max_vector_text_chars` if responses are too large."
        ),
    )
    async def codebase_vector_graph(
        query: str = Field(
            description="Natural language query; V0 also uses identifier-like tokens in chunk text.",
        ),
        table: str = Field(default="java", description="java | sql | yaml (single table)"),
        limit: int = Field(
            default=5,
            ge=1,
            le=30,
            description="Vector search top-k (LanceDB).",
        ),
        vector_limit: int | None = Field(
            default=None,
            description="If set, overrides the vector top-k; otherwise max(limit, 8).",
        ),
        graph_depth: int = Field(
            default=2,
            ge=1,
            le=3,
            description="Bidirectional graph hops (1–2 typical; 3 for deep traces).",
        ),
        graph_limit: int = Field(
            default=28,
            ge=1,
            le=150,
            description="Max graph context rows; split between structure + interface pass.",
        ),
        max_vector_text_chars: int = Field(
            default=2000,
            ge=200,
            le=20000,
            description="Max characters per vector chunk text included in the merged output.",
        ),
        snippet_max_bytes: int = Field(
            default=2000,
            ge=500,
            le=20000,
            description="Max bytes read per file for graph (structural) context rows.",
        ),
        include_chunk_seeds: bool = Field(
            default=True,
            description="If true, extract entity seeds from top-k chunk bodies (DKB step 2).",
        ),
        interface_expansion: bool = Field(
            default=True,
            description="If true, for interface types add T_IMPLEMENTS and T_INJECTS neighbors (step 5).",
        ),
    ) -> HybridRagOutput:
        if (
            fuse_vector_and_graph is None
            or guess_identifier_seeds is None
            or guess_substring_seeds_from_query is None
            or find_types_in_file_by_rel_path is None
            or collect_graph_seeds is None
        ):
            return HybridRagOutput(
                success=False,
                message="java_ast_graph hybrid module not available.",
            )
        if table not in ("java", "sql", "yaml"):
            return HybridRagOutput(success=False, message="table must be java, sql, or yaml.")
        uri = _resolve_lancedb_uri()
        is_remote = uri.startswith(("s3://", "gs://", "az://"))
        if not is_remote and not Path(uri).exists():
            return HybridRagOutput(success=False, message=f"LanceDB path missing: {uri}")
        root = _project_root()
        model_name = os.environ.get("SBERT_MODEL", SBERT_MODEL)
        device = os.environ.get("SBERT_DEVICE") or None
        vk = int(vector_limit) if vector_limit is not None else max(int(limit), 8)

        def _vec() -> list[dict[str, Any]]:
            model = _get_sentence_transformer(model_name, device)
            rows, _fts, _ = run_search(
                query,
                uri=uri,
                table_keys=[table],
                limit=vk,
                path_substring=None,
                model_name=model_name,
                device=device,
                model=model,
                hybrid=False,
                fts_text=None,
                auto_hybrid=False,
            )
            return rows

        try:
            vector_rows = await asyncio.to_thread(_vec)
        except Exception as e:
            return HybridRagOutput(success=False, message=f"vector search failed: {e!s}")

        v_cap = int(max_vector_text_chars)
        snip_cap = int(snippet_max_bytes)
        v_dicts: list[dict[str, Any]] = []
        for r in vector_rows:
            v_dicts.append(
                {
                    "filename": str(r.get("filename", "")),
                    "text": str(r.get("text", ""))[:v_cap],
                    "_kind": r.get("_kind", table),
                }
            )

        def _rows_from_expanded(
            expanded: list[dict[str, object]],
        ) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            seen_fqn: set[str] = set()
            for ex in expanded:
                fqn = ex.get("fqn")
                if fqn in (None, ""):
                    continue
                fqn_s = str(fqn)
                if fqn_s in seen_fqn:
                    continue
                seen_fqn.add(fqn_s)
                file_key = str(ex.get("file_key", ""))
                rel = _rel_from_file_key(file_key)
                if read_file_snippet is not None:
                    body = read_file_snippet(root, rel, max_bytes=snip_cap)
                else:
                    body = ""
                rows.append(
                    {
                        "fqn": ex.get("fqn"),
                        "context_id": f"g:{ex.get('fqn')}",
                        "text": body,
                        "file_key": file_key,
                        "filename": rel,
                        "edge": ex.get("edge"),
                    }
                )
            return rows

        graph_chunk_rows: list[dict[str, Any]] = []
        gc = _graph_conn()
        if gc and collect_graph_seeds and expand_neighbors_bidirectional:
            conn, _p = gc
            try:
                typed = collect_graph_seeds(
                    query,
                    vector_rows,
                    conn,
                    include_chunk_seeds=include_chunk_seeds,
                )
                expanded: list[dict[str, object]] = []
                budget = int(graph_limit)
                if typed:
                    struct_cap = (
                        budget
                        if not interface_expansion
                        else max(1, (budget * 2) // 3)
                    )
                    expanded = expand_neighbors_bidirectional(
                        conn, typed, depth=graph_depth, limit=struct_cap
                    )
                extra_iface: list[dict[str, object]] = []
                if interface_expansion and expand_interface_consumers is not None and typed:
                    iface_budget = max(0, budget - len(expanded))
                    candidates = list(
                        dict.fromkeys(
                            typed + [str(x.get("fqn", "")) for x in expanded if x.get("fqn")]
                        )
                    )
                    if iface_budget and candidates:
                        extra_iface = expand_interface_consumers(
                            conn, candidates, limit=iface_budget
                        )
                graph_chunk_rows = _rows_from_expanded(expanded + extra_iface)
            finally:
                conn.close()

        merged = fuse_vector_and_graph(v_dicts, graph_chunk_rows, k=60)
        return HybridRagOutput(success=True, items=merged)

    return mcp


def main() -> None:
    asyncio.run(create_mcp_server().run_stdio_async())


if __name__ == "__main__":
    main()
