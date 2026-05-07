#!/usr/bin/env python3
"""LanceDB code-search MCP (stdio)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Literal

import mcp_v2
from index_common import SBERT_MODEL
from kuzu_queries import KuzuGraph, resolve_kuzu_path
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from search_lancedb import TABLES

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"
_INSTRUCTIONS = (
    "Java codebase graph navigator (LanceDB + Kuzu). "
    "Tools: search (NL/code locate), find (structured NodeFilter), describe (one node + edge counts), "
    "neighbors (one hop; you MUST pass direction in|out AND edge_types list — no defaults). "
    "Edge labels: EXTENDS, IMPLEMENTS, INJECTS, DECLARES, DECLARES_CLIENT, CALLS, EXPOSES, HTTP_CALLS, ASYNC_CALLS. "
    "Rebuild, meta, tables, diagnose-ignore, analyze-pr: use user-rag CLI — not MCP."
)


class GraphMetaOutput(BaseModel):
    success: bool
    enabled: bool
    db_path: str
    ontology_version: int = 0
    built_at: int = 0
    source_root: str = ""
    parse_errors: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    module_counts: dict[str, int] = Field(default_factory=dict)
    microservice_counts: dict[str, int] = Field(default_factory=dict)
    routes_total: int = 0
    exposes_total: int = 0
    routes_by_framework: dict[str, int] = Field(default_factory=dict)
    routes_resolved_pct: float = 0.0
    routes_from_brownfield_pct: float = 0.0
    routes_by_layer: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    http_calls_match_breakdown: dict[str, int] = Field(default_factory=dict)
    async_calls_match_breakdown: dict[str, int] = Field(default_factory=dict)
    cross_service_calls_total: int = 0
    cross_service_resolution: str | None = None
    message: str | None = None


class RefreshIndexOutput(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None
    graph_exit_code: int | None = None
    graph_stdout: str = ""
    graph_stderr: str = ""


class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    refresh_enabled: bool
    cocoindex_target: str
    tables: dict[str, str]
    graph: GraphMetaOutput


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
    return Path.cwd().resolve()


def _cocoindex_subprocess_env(project_root: Path) -> dict[str, str]:
    sub_env = os.environ.copy()
    sub_env["LANCEDB_MCP_PROJECT_ROOT"] = str(project_root)
    return sub_env


def _graph_enabled() -> bool:
    raw = os.environ.get("LANCEDB_MCP_GRAPH_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return KuzuGraph.exists()


def _refresh_allowed() -> bool:
    return os.environ.get("LANCEDB_MCP_ALLOW_REFRESH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _graph_meta_output() -> GraphMetaOutput:
    if not KuzuGraph.exists():
        return GraphMetaOutput(
            success=True,
            enabled=False,
            db_path=resolve_kuzu_path(),
            message="Kuzu graph not present; run user-rag refresh or build_ast_graph.py",
        )
    try:
        graph = KuzuGraph.get()
        meta = graph.meta()
    except Exception as e:
        return GraphMetaOutput(
            success=False,
            enabled=_graph_enabled(),
            db_path=resolve_kuzu_path(),
            message=f"Kuzu open failed: {e}",
        )
    if "error" in meta:
        return GraphMetaOutput(
            success=False,
            enabled=_graph_enabled(),
            db_path=meta.get("db_path", resolve_kuzu_path()),
            message=str(meta["error"]),
        )
    try:
        mod_counts = graph.module_counts()
    except Exception:
        mod_counts = {}
    try:
        ms_counts = graph.microservice_counts()
    except Exception:
        ms_counts = {}
    rfw = meta.get("routes_by_framework") or {}
    routes_by_framework = {str(k): int(v) for k, v in rfw.items()} if isinstance(rfw, dict) else {}
    rbl = meta.get("routes_by_layer") or {}
    routes_by_layer = {str(k): int(v) for k, v in rbl.items()} if isinstance(rbl, dict) else {}
    return GraphMetaOutput(
        success=True,
        enabled=_graph_enabled(),
        db_path=meta.get("db_path", resolve_kuzu_path()),
        ontology_version=int(meta.get("ontology_version") or 0),
        built_at=int(meta.get("built_at") or 0),
        source_root=str(meta.get("source_root") or ""),
        parse_errors=int(meta.get("parse_errors") or 0),
        counts={k: int(v) for k, v in (meta.get("counts") or {}).items()},
        module_counts=mod_counts,
        microservice_counts=ms_counts,
        routes_total=int(meta.get("routes_total") or 0),
        exposes_total=int(meta.get("exposes_total") or 0),
        routes_by_framework=routes_by_framework,
        routes_resolved_pct=float(meta.get("routes_resolved_pct") or 0.0),
        routes_from_brownfield_pct=float(meta.get("routes_from_brownfield_pct") or 0.0),
        routes_by_layer=routes_by_layer,
        edge_counts={str(k): int(v) for k, v in (meta.get("edge_counts") or {}).items()},
        http_calls_match_breakdown={
            str(k): int(v) for k, v in (meta.get("http_calls_match_breakdown") or {}).items()
        },
        async_calls_match_breakdown={
            str(k): int(v) for k, v in (meta.get("async_calls_match_breakdown") or {}).items()
        },
        cross_service_calls_total=int(meta.get("cross_service_calls_total") or 0),
        cross_service_resolution=meta.get("cross_service_resolution"),
    )


def list_code_index_tables_payload() -> IndexInfoOutput:
    return IndexInfoOutput(
        lancedb_uri=_resolve_lancedb_uri(),
        embedding_model=os.environ.get("SBERT_MODEL", SBERT_MODEL),
        project_root=str(_project_root()),
        refresh_enabled=_refresh_allowed(),
        cocoindex_target=_COCOINDEX_TARGET,
        tables=dict(TABLES),
        graph=_graph_meta_output(),
    )


async def run_refresh_pipeline(*, quiet: bool = False) -> RefreshIndexOutput:
    if not _refresh_allowed():
        return RefreshIndexOutput(
            success=False,
            message="Refresh disabled: set LANCEDB_MCP_ALLOW_REFRESH=1 (or true/yes), then run user-rag refresh.",
            exit_code=None,
        )
    root = _project_root()
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        return RefreshIndexOutput(
            success=False,
            message=f"cocoindex not found next to Python: {cocoindex_bin}",
        )
    flow_path = root / "java_index_flow_lancedb.py"
    bundle_dir = Path(__file__).resolve().parent
    if not flow_path.is_file():
        fallback = bundle_dir / "java_index_flow_lancedb.py"
        if fallback.is_file():
            flow_path = fallback
        else:
            return RefreshIndexOutput(
                success=False,
                message=f"java_index_flow_lancedb.py not found under {root} nor {bundle_dir}",
            )
    try:
        proc = await asyncio.create_subprocess_exec(
            str(cocoindex_bin),
            "update",
            _COCOINDEX_TARGET,
            "--full-reprocess",
            "-f",
            cwd=str(flow_path.parent),
            env=_cocoindex_subprocess_env(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await proc.communicate()
    except Exception as exc:
        return RefreshIndexOutput(success=False, message=f"spawn failed: {exc!s}")
    out = out_b.decode(errors="replace")
    err = err_b.decode(errors="replace")
    ok = proc.returncode == 0
    graph_code: int | None = None
    graph_out = ""
    graph_err = ""
    if ok:
        builder = Path(__file__).resolve().parent / "build_ast_graph.py"
        if builder.is_file():
            try:
                graph_args = [sys.executable, str(builder), "--source-root", str(root)]
                if not quiet:
                    graph_args.append("--verbose")
                gproc = await asyncio.create_subprocess_exec(
                    *graph_args,
                    cwd=str(root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                gout_b, gerr_b = await gproc.communicate()
                graph_code = gproc.returncode
                graph_out = gout_b.decode(errors="replace")
                graph_err = gerr_b.decode(errors="replace")
            except Exception as exc:
                graph_code = -1
                graph_err = f"graph builder spawn failed: {exc}"
    message: str | None = None
    if not ok:
        message = f"cocoindex exit {proc.returncode}"
    elif graph_code is not None and graph_code != 0:
        message = f"graph builder exit {graph_code}"
    return RefreshIndexOutput(
        success=ok and (graph_code is None or graph_code == 0),
        exit_code=proc.returncode,
        stdout=out[-8000:] if len(out) > 8000 else out,
        stderr=err[-8000:] if len(err) > 8000 else err,
        message=message,
        graph_exit_code=graph_code,
        graph_stdout=graph_out[-4000:] if len(graph_out) > 4000 else graph_out,
        graph_stderr=graph_err[-4000:] if len(graph_err) > 4000 else graph_err,
    )


def create_mcp_server() -> FastMCP:
    mcp = FastMCP("lancedb-code-search", instructions=_INSTRUCTIONS)

    @mcp.tool(name="search", description="locate nodes by NL/code text")
    async def search(
        query: str = Field(description="Search query"),
        table: Literal["java", "sql", "yaml", "all"] = Field(
            default="java",
            description="java | sql | yaml | all",
        ),
        hybrid: bool = Field(
            default=False,
            description="If true, fuse FTS + vector (single-table java/sql/yaml only)",
        ),
        limit: int = Field(default=5, ge=1, le=50, description="Max hits to return"),
        offset: int = Field(default=0, ge=0, le=500, description="Skip this many hits (pagination)"),
        path_contains: str | None = Field(
            default=None,
            description="Substring match on file path (pre-filter from index)",
        ),
        filter: dict[str, Any] | None = Field(
            default=None,
            description="Optional NodeFilter (symbol-oriented keys) applied to each hit after search",
        ),
    ) -> mcp_v2.SearchOutput:
        return await asyncio.to_thread(
            mcp_v2.search_v2,
            query,
            table,
            hybrid,
            limit,
            offset,
            path_contains,
            filter,
            None,
        )

    @mcp.tool(name="find", description="locate nodes by structured filter")
    async def find(
        kind: Literal["symbol", "route", "client"] = Field(description="symbol | route | client"),
        filter: dict[str, Any] = Field(
            ...,
            description="Required NodeFilter object (shared schema; irrelevant keys ignored per kind)",
        ),
        limit: int = Field(default=25, ge=1, le=500, description="Max nodes to return"),
        offset: int = Field(default=0, ge=0, le=499, description="Skip this many nodes (pagination)"),
    ) -> mcp_v2.FindOutput:
        return await asyncio.to_thread(mcp_v2.find_v2, kind, filter, limit, offset, None)

    @mcp.tool(name="describe", description="full record + edge counts for one node")
    async def describe(
        id: str = Field(
            description=(
                "Graph node id: sym:, route:, or client: prefix "
                '(e.g. sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest))'
            ),
        ),
    ) -> mcp_v2.DescribeOutput:
        return await asyncio.to_thread(mcp_v2.describe_v2, id, None)

    @mcp.tool(name="neighbors", description="one-hop walk; REQUIRED direction + edge_types")
    async def neighbors(
        ids: str | list[str] = Field(description="Origin symbol/route/client id, or list for batch"),
        direction: Literal["in", "out"] = Field(
            description="Required: in (predecessors) or out (successors); no default",
        ),
        edge_types: list[str] = Field(
            description="Required non-empty list of edge labels (e.g. CALLS, EXPOSES, HTTP_CALLS)",
        ),
        limit: int = Field(
            default=25,
            ge=1,
            le=500,
            description="Max edges after merge (batch expands all origins first)",
        ),
        offset: int = Field(
            default=0,
            ge=0,
            le=1000,
            description="Skip this many edges after merge (pagination)",
        ),
        filter: dict[str, Any] | None = Field(
            default=None,
            description="Optional NodeFilter applied to the other endpoint of each edge",
        ),
    ) -> mcp_v2.NeighborsOutput:
        return await asyncio.to_thread(
            mcp_v2.neighbors_v2,
            ids,
            direction,
            edge_types,
            limit,
            offset,
            filter,
            None,
        )

    return mcp


def main() -> None:
    asyncio.run(create_mcp_server().run_stdio_async())


if __name__ == "__main__":
    main()
