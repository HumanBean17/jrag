#!/usr/bin/env python3
"""LanceDB code-search MCP (stdio)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

import mcp_trace
import mcp_v2
from index_common import SBERT_MODEL
from java_codebase_rag.cli_progress import (
    accumulate_and_relay_subprocess_streams,
    emit_vectors_finish,
    emit_vectors_start,
)
from java_codebase_rag.config import emit_legacy_env_hints_if_present, resolved_sbert_model_for_process_env, resolve_operator_config
from kuzu_queries import KuzuGraph, resolve_kuzu_path
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from search_lancedb import TABLES

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"
_INSTRUCTIONS = (
    "Java codebase graph navigator (LanceDB + Kuzu). "
    "Tools: search (NL/code locate), find (structured NodeFilter), describe (one node + edge_summary: stored edge-label counts and optional composed keys for type Symbols and override-axis virtual keys for method Symbols), "
    "neighbors (one hop; you MUST pass direction in|out AND edge_types list — no defaults), "
    "trace (multi-hop BFS with server-side pruning; direction + edge_types required; use for path/impact/cross-service questions where neighbors loops exceed 2 hops), "
    "resolve (identifier-shaped lookup for symbol/route/client/producer — three statuses one|many|none). "
    "NodeFilter `filter` is a JSON object (preferred); a JSON-encoded string is also accepted as a fallback. "
    "Unknown filter keys and populated fields not applicable to the effective node kind fail with success=false and message. "
    "Edge labels: EXTENDS, IMPLEMENTS, INJECTS, OVERRIDES, DECLARES, DECLARES_CLIENT, DECLARES_PRODUCER, CALLS, EXPOSES, HTTP_CALLS, ASYNC_CALLS; "
    "type Symbols may also use composed neighbors edge_types DECLARES.DECLARES_CLIENT, DECLARES.DECLARES_PRODUCER, DECLARES.EXPOSES (out only). "
    "Reprocess/init, meta, tables, diagnose-ignore, analyze-pr: use java-codebase-rag CLI — not MCP."
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
    """Structured result for ``run_refresh_pipeline`` / CLI ``reprocess`` JSON.

    ``phases_run`` records which phase subprocesses actually started; the CLI maps
    failures to exit **2** when it is empty (setup / nothing spawned) and exit **1**
    when it is non-empty (build failure). Callers constructing this model manually
    must set ``phases_run`` accordingly — omitting it leaves the default ``[]``,
    which the CLI treats like a preflight failure.
    """

    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None
    graph_exit_code: int | None = None
    graph_stdout: str = ""
    graph_stderr: str = ""
    phases_run: list[Literal["vectors", "graph"]] = Field(default_factory=list)


class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    cocoindex_target: str
    tables: dict[str, str]
    graph: GraphMetaOutput


def _resolve_lancedb_uri() -> str:
    raw = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if not raw:
        raw = str((Path.cwd() / ".java-codebase-rag").resolve())
    p = Path(raw).expanduser()
    if not str(raw).startswith(("s3://", "gs://", "az://")):
        try:
            return str(p.resolve())
        except OSError:
            return str(p)
    return raw


def _project_root() -> Path:
    env = os.environ.get("JAVA_CODEBASE_RAG_SOURCE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _cocoindex_subprocess_env(project_root: Path) -> dict[str, str]:
    sub_env = os.environ.copy()
    sub_env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(project_root)
    idx = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx:
        sub_env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(Path(idx).expanduser().resolve())
    return sub_env


def _graph_enabled() -> bool:
    return KuzuGraph.exists()


def _graph_meta_output() -> GraphMetaOutput:
    if not KuzuGraph.exists():
        return GraphMetaOutput(
            success=True,
            enabled=False,
            db_path=resolve_kuzu_path(),
            message="Kuzu graph not present; run java-codebase-rag reprocess or build_ast_graph.py",
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
        embedding_model=resolved_sbert_model_for_process_env(SBERT_MODEL),
        project_root=str(_project_root()),
        cocoindex_target=_COCOINDEX_TARGET,
        tables=dict(TABLES),
        graph=_graph_meta_output(),
    )


async def run_refresh_pipeline(*, quiet: bool = False, verbose: bool = True) -> RefreshIndexOutput:
    root = _project_root()
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        return RefreshIndexOutput(
            success=False,
            message=f"cocoindex not found next to Python: {cocoindex_bin}",
            phases_run=[],
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
                phases_run=[],
            )
    proc: asyncio.subprocess.Process | None = None
    out_b, err_b = b"", b""
    if quiet:
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
            return RefreshIndexOutput(
                success=False,
                message=f"spawn failed: {exc!s}",
                phases_run=[],
            )
    else:
        emit_vectors_start()
        t0 = time.perf_counter()
        code_c = -1
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
            out_b, err_b = await accumulate_and_relay_subprocess_streams(proc, relay=True, verbose=verbose)
            code_c = proc.returncode if proc.returncode is not None else -1
        except Exception as exc:
            return RefreshIndexOutput(
                success=False,
                message=f"spawn failed: {exc!s}",
                phases_run=[],
            )
        finally:
            emit_vectors_finish(elapsed_s=time.perf_counter() - t0, exit_code=code_c)
    assert proc is not None
    out = out_b.decode(errors="replace")
    err = err_b.decode(errors="replace")
    ok = proc.returncode == 0
    phases_run: list[Literal["vectors", "graph"]] = ["vectors"]
    graph_code: int | None = None
    graph_out = ""
    graph_err = ""
    if ok:
        if not quiet:
            print(file=sys.stderr, flush=True)
        builder = Path(__file__).resolve().parent / "build_ast_graph.py"
        if builder.is_file():
            try:
                graph_args = [
                    sys.executable,
                    str(builder),
                    "--source-root",
                    str(root),
                    "--kuzu-path",
                    resolve_kuzu_path(),
                ]
                if not quiet:
                    graph_args.append("--verbose")
                gproc = await asyncio.create_subprocess_exec(
                    *graph_args,
                    cwd=str(root),
                    env=_cocoindex_subprocess_env(root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                phases_run = ["vectors", "graph"]
                if quiet:
                    gout_b, gerr_b = await gproc.communicate()
                else:
                    gout_b, gerr_b = await accumulate_and_relay_subprocess_streams(gproc, relay=True, verbose=verbose)
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
        phases_run=phases_run,
    )


def create_mcp_server() -> FastMCP:
    mcp = FastMCP("java-codebase-rag", instructions=_INSTRUCTIONS)

    @mcp.tool(
        name="search",
        description=(
            "Ranked chunk retrieval: `query` is opaque text (natural language or code fragments); "
            "results are score-ranked, not boolean-matched. Optional `filter` uses the same NodeFilter "
            "schema as `find` but only **symbol-applicable** fields apply (strict frame). Wildcards "
            "(`*`, `?`) in prefix fields are rejected—use ranked `query` text instead. There is **no** "
            "structured DSL inside `query`; structured predicates belong in `find`. "
            "For identifier-shaped lookups (FQN, id prefix, route/client identifiers, …), use `resolve` first; "
            "use `search` for natural-language or ranked fuzzy discovery. "
            "Successful responses echo `limit`/`offset` and may include `hints_structured` (tool call suggestions with `reason` field) and `advisories` (pure informational text)."
        ),
    )
    async def search(
        query: str = Field(description="Search query"),
        table: Literal["java", "sql", "yaml", "all"] = Field(
            default="java",
            description="Which content table to search. 'all' fuses java/sql/yaml results.",
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
        filter: dict[str, Any] | str | None = Field(
            default=None,
            description=(
                "Optional NodeFilter post-filter on symbol-oriented hit rows. Unknown keys or populated fields not "
                "applicable to symbols return success=false. Prefer a JSON object; a JSON-encoded string is accepted."
            ),
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

    @mcp.tool(
        name="find",
        description=(
            "Exact structured listing for one node kind. Per-kind applicable fields: **symbol** — "
            "microservice, module, role, exclude_roles, annotation, capability, fqn_prefix, symbol_kind, symbol_kinds; "
            "**route** — microservice, module, http_method, path_prefix, framework; **client** — microservice, module, "
            "source_layer, client_kind, target_service, target_path_prefix, http_method; **producer** — microservice, "
            "module, source_layer, producer_kind, topic_prefix. "
            "Wildcards in prefix fields are rejected. An empty filter (`{}`) or `filter=None` means no predicate (all nodes of "
            "that kind; use pagination). Unknown keys or inapplicable populated fields return success=false. "
            "Successful responses echo `limit`/`offset` and may include `hints_structured` (tool call suggestions with `reason` field) and `advisories` (pure informational text)."
        ),
    )
    async def find(
        kind: Literal["symbol", "route", "client", "producer"] = Field(
            description=(
                "Which graph table to search. 'symbol' = declarations, "
                "'route' = endpoints, 'client' = outbound HTTP clients, "
                "'producer' = outbound async producers."
            )
        ),
        filter: dict[str, Any] | str = Field(
            ...,
            description=(
                "Required NodeFilter dict (extra keys forbidden). Fields must be applicable to `kind`. "
                "Prefer a JSON object; a JSON-encoded string is accepted."
            ),
        ),
        limit: int = Field(default=25, ge=1, le=500, description="Max nodes to return"),
        offset: int = Field(default=0, ge=0, le=499, description="Skip this many nodes (pagination)"),
    ) -> mcp_v2.FindOutput:
        return await asyncio.to_thread(mcp_v2.find_v2, kind, filter, limit, offset, None)

    @mcp.tool(
        name="describe",
        description=(
            "Full node record plus `edge_summary` (in/out counts per stored edge label, plus optional describe-time keys). Type Symbols may add "
            "composed keys DECLARES.DECLARES_CLIENT, DECLARES.DECLARES_PRODUCER, and DECLARES.EXPOSES (navigable on type Symbols via neighbors, out only); "
            "method Symbols may add override-axis virtual keys (OVERRIDDEN_BY, OVERRIDDEN_BY.DECLARES_CLIENT, OVERRIDDEN_BY.DECLARES_PRODUCER, "
            "OVERRIDDEN_BY.EXPOSES, plus an `OVERRIDES` map entry that merges stored `[:OVERRIDES]` counts with the dispatch-up rollup per direction). "
            "Override-axis virtual keys are navigable via neighbors on non-static method Symbol origins "
            "(out only; composed keys include via_id in attrs). The stored `OVERRIDES` relationship "
            "is also a normal edge label (e.g. direction in from declaration toward overriders). "
            "Pass `id` for any kind, or exact `fqn` for Symbol lookup (`id` wins when both are set). "
            "`describe(fqn=…)` keeps the first graph row when multiple symbols share that FQN; when an FQN may collide, "
            "prefer `resolve(identifier=…, hint_kind='symbol')` first, then `describe(id=…)` on the chosen node. "
            "Successful responses may include `hints_structured` (tool call suggestions with `reason` field) and `advisories` (pure informational text)."
        ),
    )
    async def describe(
        id: str | None = Field(
            default=None,
            description=(
                "Graph node id: sym:, route:, client:, or producer: prefix "
                '(e.g. sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest); '
                "producer: p:a1b2c3d4e5f67890 — the stored id from the graph, not a human-readable "
                "pipe key). For producers by topic, prefer resolve(identifier=<topic>, hint_kind='producer'). "
                "When set, takes precedence over fqn."
            ),
        ),
        fqn: str | None = Field(
            default=None,
            description="Exact FQN for Symbol lookup (alternative to id; Symbol kind only)",
        ),
    ) -> mcp_v2.DescribeOutput:
        return await asyncio.to_thread(mcp_v2.describe_v2, id, fqn, None)

    @mcp.tool(
        name="neighbors",
        description=(
            "Graph walk: **direction** (`in` | `out`) and non-empty **edge_types** are required (stored labels for one hop; "
            "type Symbol origins may also pass composed DECLARES.DECLARES_CLIENT, DECLARES.DECLARES_PRODUCER, or DECLARES.EXPOSES "
            "for 2-hop member rollups; method Symbol origins may pass OVERRIDDEN_BY, OVERRIDDEN_BY.DECLARES_CLIENT, "
            "OVERRIDDEN_BY.DECLARES_PRODUCER, OVERRIDDEN_BY.EXPOSES for override-axis rollups — out only, via_id in "
            "attrs on composed keys). "
            "Optional `filter` applies to each neighbor endpoint row; populated fields must be applicable to that "
            "neighbor's kind—mixed-kind result sets fail on the first inapplicable neighbor (strict frame). "
            "Optional `edge_filter` requires edge_types=['CALLS'] only (no composed dot-keys or extra stored "
            "labels); projects the ordered CALLS stream by edge attributes (min_confidence, strategies, "
            "callee_declaring_role). Wildcards in prefix fields are rejected. Unknown filter keys return success=false. "
            "Successful responses echo `requested_edge_types` and may include `hints_structured` (tool call suggestions with `reason` field) and `advisories` (pure informational text). "
            "Each edge's `attrs.strategy` indicates resolution quality (brownfield/fallback vs primary paths)."
        ),
    )
    async def neighbors(
        ids: str | list[str] = Field(
            description="Origin symbol/route/client/producer id, or list for batch",
        ),
        direction: Literal["in", "out"] = Field(
            description="Required. 'in' = predecessors (callers), 'out' = successors (callees). No default.",
        ),
        edge_types: list[mcp_v2.NeighborEdgeType] = Field(
            description=(
                "Required non-empty list of stored edge labels (e.g. CALLS, EXPOSES, HTTP_CALLS, OVERRIDES) "
                "and/or composed DECLARES.DECLARES_* (type Symbol origin, out only) or OVERRIDDEN_BY* "
                "(non-static method Symbol origin, out only)"
            ),
        ),
        limit: int = Field(
            default=25,
            ge=1,
            le=500,
            description=(
                "Max edges after concatenating all origins (ids order; offset/limit on merged list)"
            ),
        ),
        offset: int = Field(
            default=0,
            ge=0,
            le=1000,
            description="Skip this many edges after merge (pagination)",
        ),
        filter: dict[str, Any] | str | None = Field(
            default=None,
            description=(
                "Optional NodeFilter on the neighbor node. Same applicability rules as `find` for that node's kind. "
                "Prefer a JSON object; a JSON-encoded string is accepted."
            ),
        ),
        edge_filter: dict[str, Any] | str | None = Field(
            default=None,
            description=(
                "Optional EdgeFilter on CALLS edge attributes (edge_types=['CALLS'] only). Use "
                "callee_declaring_role for callee stereotype projection — not NodeFilter.role on method neighbors. "
                "Mutually exclusive with include_unresolved. Prefer a JSON object; a JSON-encoded string is accepted."
            ),
        ),
        include_unresolved: bool = Field(
            default=False,
            description=(
                "When true with edge_types=['CALLS'] and direction='out', interleave UnresolvedCallSite "
                "rows (row_kind='unresolved_call_site') with resolved CALLS in source order. "
                "Mutually exclusive with edge_filter."
            ),
        ),
        dedup_calls: bool = Field(
            default=False,
            description=(
                "When true with edge_types=['CALLS'], collapse identical (origin, callee) CALLS to one row "
                "with call_site_count and call_site_lines; unresolved sites are not deduped."
            ),
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
            edge_filter,
            include_unresolved,
            dedup_calls,
            None,
        )

    @mcp.tool(
        name="resolve",
        description=(
            "Identifier-shaped node lookup (FQN, sym:/route:/client:/producer: id, HTTP method+path, "
            "route path template, client target_service, target+path pair, or producer topic). Returns "
            "status=one (single node), many (≥2 ranked candidates with reason), or none "
            "(no match — fall back to search(query=...) for natural language or fuzzy text). "
            "Optional hint_kind narrows to symbol, route, client, or producer. "
            "Successful responses may include hints_structured (tool call suggestions with `reason` field) and advisories (pure informational text) — same contract as other v2 tools. "
            "Malformed empty/whitespace identifier returns success=false. "
            "Examples: resolve('com.foo.Bar', hint_kind='symbol'); "
            "resolve('GET /api/v1/customers', hint_kind='route'); "
            "resolve('the client that handles assignments') → none (use search instead)."
        ),
    )
    async def resolve(
        identifier: str = Field(
            description=(
                "Identifier-shaped node lookup (FQN, id prefix, route path, client target, producer topic, …)"
            ),
        ),
        hint_kind: Literal["symbol", "route", "client", "producer"] | None = Field(
            default=None,
            description="Optional kind constraint. Omit to search symbol, route, client, and producer.",
        ),
    ) -> mcp_v2.ResolveOutput:
        return await asyncio.to_thread(mcp_v2.resolve_v2, identifier, hint_kind, None)

    @mcp.tool(
        name="trace",
        description=(
            "Multi-hop BFS traversal with server-side pruning. Returns pruned path structure in a single call. "
            "Use `trace` instead of multiple `neighbors` calls when: (a) the question implies a path or chain "
            "(e.g. 'trace from controller to database', 'what happens when POST /api/orders is called'), "
            "(b) you need impact analysis ('who depends on X'), (c) you need to cross service boundaries "
            "(HTTP_CALLS / ASYNC_CALLS), or (d) a `neighbors` loop has exceeded 2 hops without converging. "
            "For simple one-hop adjacency ('what does M call?', 'who calls M?'), use `neighbors` instead — "
            "it returns full unfiltered results. After `trace` returns a pruned result, use `neighbors` on "
            "specific nodes to drill into edges that `trace` collapsed or pruned. "
            "`direction` and `edge_types` are required. Stored edge labels only — no composed dot-keys. "
            "`prune_roles` is a soft gate: edges to pruned-role nodes are recorded but BFS stops traversing "
            "through them (agent sees the connection but traversal focuses on higher-signal paths). "
            "`fan_out_cap` limits per-node edge expansion; scaffolding edges (DECLARES_CLIENT, DECLARES_PRODUCER) "
            "are exempt. `collapse_trivial` merges wrapper chains (A→B→C where B is trivial). "
            "Result: `nodes` dict (id → NodeRef), `edges` list with BFS metadata (hop, parent_edge_id, "
            "collapsed, cross_service_boundary), ranked `paths` (root-to-leaf), and `stats` with pruning counts. "
            "Cross-service boundary: BFS records the cross-service edge and includes the downstream Route/Producer "
            "in `nodes` but stops the frontier — the agent decides whether to continue."
        ),
    )
    async def trace(
        ids: str | list[str] = Field(
            description="Seed node IDs (single string or list). Differs from neighbors (single ID) — trace supports multi-seed for impact analysis.",
        ),
        direction: Literal["in", "out"] = Field(
            description="Traversal direction: in (callers/dependents) or out (callees/dependencies). Required — no default.",
        ),
        edge_types: list[str] = Field(
            description="Edge types to traverse (stored labels only: CALLS, IMPLEMENTS, OVERRIDES, EXPOSES, HTTP_CALLS, ASYNC_CALLS, etc.). Required non-empty. No composed dot-keys.",
        ),
        max_depth: int = Field(default=3, description="Max BFS hops (1-5, default 3)"),
        max_paths: int = Field(default=20, description="Max root-to-leaf paths to return"),
        max_nodes_discovered: int = Field(
            default=500, description="Node discovery budget before pruning (100-2000)",
        ),
        filter: dict[str, Any] | str | None = Field(
            default=None,
            description="NodeFilter as JSON object or string. Hard gate — nodes failing filter are excluded entirely.",
        ),
        edge_filter: dict[str, Any] | str | None = Field(
            default=None,
            description="EdgeFilter for CALLS edges (min_confidence, strategies, etc.). Same contract as neighbors edge_filter.",
        ),
        prune_roles: list[str] | None = Field(
            default=None,
            description="Roles to prune (edges recorded, frontier stops through these roles). Soft gate — differs from NodeFilter exclude_roles (hard gate).",
        ),
        fan_out_cap: int | None = Field(
            default=5, description="Per-node edge cap (scaffolding edges exempt). Set to 0 to disable.",
        ),
        collapse_trivial: bool = Field(
            default=True, description="Collapse wrapper chains (A→B→C where B is trivial intermediate)",
        ),
        include_unresolved: bool = Field(
            default=False,
            description="Include UnresolvedCallSite edges (CALLS out only)",
        ),
    ) -> mcp_trace.TraceOutput:
        return await asyncio.to_thread(
            mcp_trace.trace_v2,
            ids,
            direction,
            edge_types,
            max_depth,
            max_paths,
            max_nodes_discovered,
            filter,
            edge_filter,
            prune_roles,
            fan_out_cap if fan_out_cap is not None else 5,
            collapse_trivial,
            include_unresolved,
            None,
        )

    return mcp


def main() -> None:
    emit_legacy_env_hints_if_present()

    # Load YAML config and apply embedding settings to environment
    # This ensures SBERT_MODEL and SBERT_DEVICE from .java-codebase-rag.yml are available
    # before any tool handler runs (same behavior as CLI path)
    cfg = resolve_operator_config(source_root=_project_root())
    cfg.apply_to_os_environ()
    mcp_v2.set_hints_enabled(cfg.hints_enabled)

    asyncio.run(create_mcp_server().run_stdio_async())


if __name__ == "__main__":
    main()
