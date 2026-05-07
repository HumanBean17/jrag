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
import mcp_v2
import pr_analysis
from kuzu_queries import KuzuGraph, resolve_kuzu_path
from search_lancedb import TABLES

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"

_INSTRUCTIONS = (
    "Graph navigation MCP over LanceDB+Kuzu. "
    "Use search/find/describe/neighbors for navigation and structural traversal. "
    "Operational tools are graph_meta, analyze_pr, diagnose_ignore, "
    "list_code_index_tables, and refresh_code_index."
)

class PrAnalyzeOutput(BaseModel):
    success: bool
    message: str | None = None
    changed_symbols: list[dict[str, Any]] = Field(default_factory=list)
    blast_radius_total: int = 0
    blast_radius_by_symbol: dict[str, int] = Field(default_factory=dict)
    cross_service_callers: int = 0
    routes_touched: list[str] = Field(default_factory=list)
    risk_score: float = 0.0
    risk_band: str = ""
    notes: list[str] = Field(default_factory=list)

class DiagnoseIgnoreOutput(BaseModel):
    success: bool
    ignored: bool = False
    layer: str | None = None
    matching_pattern: str | None = None
    explanation: str = ""
    message: str | None = None

class GraphMetaOutput(BaseModel):
    success: bool
    enabled: bool
    db_path: str
    ontology_version: int = 0
    built_at: int = 0
    source_root: str = ""
    parse_errors: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    module_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Map of inferred-module -> type-symbol count. Empty-string key "
            "means the builder could not find a build-marker ancestor; a "
            "large count there indicates a `module=...` filter would miss "
            "those files."
        ),
    )
    microservice_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Map of inferred-microservice -> type-symbol count. Use this to "
            "discover the canonical microservice names a `microservice=...` "
            "filter expects."
        ),
    )
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

class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    refresh_enabled: bool
    cocoindex_target: str
    tables: dict[str, str]
    graph: GraphMetaOutput

class RefreshIndexOutput(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None
    graph_exit_code: int | None = None
    graph_stdout: str = ""
    graph_stderr: str = ""

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
    return Path.cwd().resolve()

def _cocoindex_subprocess_env(project_root: Path) -> dict[str, str]:
    """Environment for the cocoindex subprocess (bundle cwd, Java tree via env)."""
    sub_env = os.environ.copy()
    sub_env["LANCEDB_MCP_PROJECT_ROOT"] = str(project_root)
    return sub_env

def _refresh_allowed() -> bool:
    return os.environ.get("LANCEDB_MCP_ALLOW_REFRESH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

def _graph_enabled() -> bool:
    raw = os.environ.get("LANCEDB_MCP_GRAPH_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return KuzuGraph.exists()

def _graph_meta_output() -> GraphMetaOutput:
    if not KuzuGraph.exists():
        return GraphMetaOutput(
            success=True, enabled=False,
            db_path=resolve_kuzu_path(),
            message="Kuzu graph not present; run refresh_code_index or build_ast_graph.py",
        )
    try:
        graph = KuzuGraph.get()
        meta = graph.meta()
    except Exception as e:
        return GraphMetaOutput(
            success=False, enabled=_graph_enabled(),
            db_path=resolve_kuzu_path(),
            message=f"Kuzu open failed: {e}",
        )
    if "error" in meta:
        return GraphMetaOutput(
            success=False, enabled=_graph_enabled(),
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
    if not isinstance(rfw, dict):
        rfw = {}
    routes_by_framework = {str(k): int(v) for k, v in rfw.items()}
    rbl = meta.get("routes_by_layer") or {}
    if not isinstance(rbl, dict):
        rbl = {}
    routes_by_layer = {str(k): int(v) for k, v in rbl.items()}
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
        http_calls_match_breakdown={str(k): int(v) for k, v in (meta.get("http_calls_match_breakdown") or {}).items()},
        async_calls_match_breakdown={str(k): int(v) for k, v in (meta.get("async_calls_match_breakdown") or {}).items()},
        cross_service_calls_total=int(meta.get("cross_service_calls_total") or 0),
        cross_service_resolution=meta.get("cross_service_resolution"),
    )

def create_mcp_server() -> FastMCP:
    mcp = FastMCP("lancedb-code-search", instructions=_INSTRUCTIONS)

    @mcp.tool(
        name="list_code_index_tables",
        description=(
            "Return LanceDB table names/paths and graph metadata, including inferred "
            "module/microservice scope context for search filters."
        ),
    )
    async def list_code_index_tables() -> IndexInfoOutput:
        return IndexInfoOutput(
            lancedb_uri=_resolve_lancedb_uri(),
            embedding_model=os.environ.get("SBERT_MODEL", SBERT_MODEL),
            project_root=str(_project_root()),
            refresh_enabled=_refresh_allowed(),
            cocoindex_target=_COCOINDEX_TARGET,
            tables=dict(TABLES),
            graph=_graph_meta_output(),
        )

    # ---------- Graph tools (Kuzu AST) ----------

    def _require_graph() -> tuple[bool, KuzuGraph | None, str | None]:
        if not _graph_enabled() or not KuzuGraph.exists():
            return False, None, (
                "Graph is not available. Build it with refresh_code_index or "
                "`python build_ast_graph.py --source-root <repo>`."
            )
        try:
            return True, KuzuGraph.get(), None
        except Exception as e:
            return False, None, f"Kuzu open failed: {e}"

    @mcp.tool(
        name="analyze_pr",
        description=(
            "Map a unified diff to changed indexed symbols and estimate blast radius / risk. "
            "Pass full unified-diff text (e.g. `git diff` output). Returns JSON-serializable "
            "risk report: changed_symbols, blast_radius_total, cross_service_callers, "
            "routes_touched (Route ids via EXPOSES), risk_score ([0,1]), risk_band, notes. "
            "Binary hunks and file renames are skipped for symbol mapping and surfaced in notes."
        ),
    )
    async def analyze_pr(
        diff_unified: str = Field(description="Unified diff text (git-style)"),
    ) -> PrAnalyzeOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return PrAnalyzeOutput(success=False, message=msg)
        rep = await asyncio.to_thread(pr_analysis.analyze_pr_pipeline, graph, diff_unified)
        payload = pr_analysis.pr_report_to_dict(rep)
        return PrAnalyzeOutput(success=True, **payload)

    @mcp.tool(
        name="diagnose_ignore",
        description=(
            "Explain whether a project path is ignored for indexing / graph walks and which "
            "layer decided (builtin_default, project_root, nested, gitignore). "
            "Pass a path relative to LANCEDB_MCP_PROJECT_ROOT (or cwd) or an absolute path "
            "inside the project."
        ),
    )
    async def diagnose_ignore(
        path: str = Field(description="File or directory path to diagnose"),
    ) -> DiagnoseIgnoreOutput:
        from path_filtering import LayeredIgnore

        root = _project_root()
        raw = Path(path)
        try:
            if raw.is_absolute():
                abs_path = raw.resolve()
            else:
                abs_path = (root / raw).resolve()
        except OSError as exc:
            return DiagnoseIgnoreOutput(
                success=False,
                message=f"Invalid path: {exc}",
            )
        li = LayeredIgnore(root)
        d = li.diagnose_dict(abs_path)
        layer_v = d.get("layer")
        pat_v = d.get("matching_pattern")
        return DiagnoseIgnoreOutput(
            success=True,
            ignored=bool(d.get("ignored")),
            layer=layer_v if layer_v is None or isinstance(layer_v, str) else str(layer_v),
            matching_pattern=pat_v if pat_v is None or isinstance(pat_v, str) else str(pat_v),
            explanation=str(d.get("explanation") or ""),
        )

    @mcp.tool(
        name="graph_meta",
        description="Kuzu graph metadata: counts, ontology version, build timestamp.",
    )
    async def graph_meta() -> GraphMetaOutput:
        return _graph_meta_output()

    @mcp.tool(
        name="refresh_code_index",
        description=(
            "Rebuild LanceDB chunks via cocoindex and then rebuild the Kuzu graph "
            "(slow; requires LANCEDB_MCP_ALLOW_REFRESH=1 and confirm=true)."
        ),
    )
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
        # Keep the venv symlink path so we resolve the script in the venv bin directory.
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
        except Exception as e:
            return RefreshIndexOutput(success=False, message=f"spawn failed: {e!s}")

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
                    gproc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(builder),
                        "--source-root",
                        str(root),
                        "--verbose",
                        cwd=str(root),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    gout_b, gerr_b = await gproc.communicate()
                    graph_code = gproc.returncode
                    graph_out = gout_b.decode(errors="replace")
                    graph_err = gerr_b.decode(errors="replace")
                except Exception as e:
                    graph_code = -1
                    graph_err = f"graph builder spawn failed: {e}"
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

    @mcp.tool(name="search", description="locate nodes by NL/code text")
    async def search(
        query: str = Field(description="Search query"),
        table: str = Field(default="java", description="java | sql | yaml | all"),
        hybrid: bool = Field(default=False),
        limit: int = Field(default=5, ge=1, le=50),
        offset: int = Field(default=0, ge=0, le=500),
        path_contains: str | None = Field(default=None),
        filter: dict[str, Any] | None = Field(default=None),
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
        kind: str = Field(description="symbol | route | client"),
        filter: dict[str, Any] = Field(...),
        limit: int = Field(default=25, ge=1, le=500),
        offset: int = Field(default=0, ge=0, le=499),
    ) -> mcp_v2.FindOutput:
        return await asyncio.to_thread(mcp_v2.find_v2, kind, filter, limit, offset, None)

    @mcp.tool(name="describe", description="full record + edge counts for one node")
    async def describe(id: str = Field(description="symbol/route/client id")) -> mcp_v2.DescribeOutput:
        return await asyncio.to_thread(mcp_v2.describe_v2, id, None)

    @mcp.tool(
        name="neighbors",
        description="one-hop walk; REQUIRED direction + edge_types",
    )
    async def neighbors(
        ids: str | list[str] = Field(description="origin id or ids"),
        direction: str = Field(description="in | out"),
        edge_types: list[str] = Field(description="edge labels to traverse"),
        limit: int = Field(default=25, ge=1, le=500),
        offset: int = Field(default=0, ge=0, le=1000),
        filter: dict[str, Any] | None = Field(default=None),
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
