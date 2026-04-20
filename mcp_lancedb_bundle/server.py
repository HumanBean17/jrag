#!/usr/bin/env python3
"""LanceDB code-search MCP (stdio). Self-contained bundle — copy this folder anywhere.

Run:
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  LANCEDB_URI=/abs/path/to/lancedb_data .venv/bin/python server.py

Claude Code (project): see README.md and mcp.json.example
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

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"

_INSTRUCTIONS = (
    "Semantic search over a LanceDB code index (Java, Flyway SQL, YAML configs). "
    "Use for meaning-based discovery—not exact keywords. Prefer limit 5; use offset to page. "
    "Optional hybrid mode (single table only) mixes vector + full-text (RRF). "
    "refresh_code_index requires LANCEDB_MCP_ALLOW_REFRESH=1 and cocoindex beside the venv Python."
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


class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    refresh_enabled: bool
    cocoindex_target: str
    tables: dict[str, str]


class RefreshIndexOutput(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
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
            "Vector / hybrid search over a LanceDB codebase index. "
            "Natural language or code snippet; optional hybrid for identifiers."
        ),
    )
    async def codebase_search(
        query: str = Field(description="Search query"),
        table: str = Field(default="java", description="java | sql | yaml | all"),
        limit: int = Field(default=5, ge=1, le=50),
        offset: int = Field(default=0, ge=0, le=500),
        path_contains: str | None = Field(default=None),
        hybrid: bool = Field(default=False),
        fts_text: str | None = Field(default=None),
        auto_hybrid: bool = Field(default=False),
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

        def _run() -> list[dict[str, Any]]:
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
            rows = await asyncio.to_thread(_run)
        except Exception as e:
            return CodeSearchOutput(success=False, message=f"Search failed: {e!s}")

        return CodeSearchOutput(success=True, results=_rows_to_hits(rows))

    @mcp.tool(name="list_code_index_tables")
    async def list_code_index_tables() -> IndexInfoOutput:
        return IndexInfoOutput(
            lancedb_uri=_resolve_lancedb_uri(),
            embedding_model=os.environ.get("SBERT_MODEL", SBERT_MODEL),
            project_root=str(_project_root()),
            refresh_enabled=_refresh_allowed(),
            cocoindex_target=_COCOINDEX_TARGET,
            tables=dict(TABLES),
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
        return RefreshIndexOutput(
            success=ok,
            exit_code=proc.returncode,
            stdout=out[-8000:] if len(out) > 8000 else out,
            stderr=err[-8000:] if len(err) > 8000 else err,
            message=None if ok else f"exit {proc.returncode}",
        )

    return mcp


def main() -> None:
    asyncio.run(create_mcp_server().run_stdio_async())


if __name__ == "__main__":
    main()
