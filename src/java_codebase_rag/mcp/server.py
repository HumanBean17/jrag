#!/usr/bin/env python3
"""LanceDB code-search MCP (stdio)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Literal

from java_codebase_rag.mcp import mcp_v2
from java_codebase_rag.analysis import resolve_service
from java_codebase_rag.search.index_common import SBERT_MODEL
from java_codebase_rag.cli_progress import (
    accumulate_and_relay_subprocess_streams,
)
from java_codebase_rag.pipeline import VECTORS_SKIPPED_GRAPH_ONLY, vector_stack_installed
from java_codebase_rag.progress import ProgressEvent
from java_codebase_rag._fdlimit import raise_fd_limit
from java_codebase_rag.config import (
    cocoindex_subprocess_env_defaults,
    discover_project_root,
    emit_legacy_env_hints_if_present,
    resolved_sbert_model_for_process_env,
    resolve_operator_config,
)
from java_codebase_rag.graph.ladybug_queries import LadybugGraph, resolve_ladybug_path
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
# NOTE: search_lancedb.TABLES is imported lazily in list_code_index_tables_payload() — it
# pulls lancedb/torch and is unavailable on graph-only installs (macOS Intel).

_COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"

# Package-internal locations of the cocoindex flow and the graph builder, both
# executed by file path (see java_codebase_rag.pipeline). Derived from this
# file's location so they resolve under editable and wheel installs alike.
_PKG_DIR = Path(__file__).resolve().parent.parent
_FLOW_FILE = _PKG_DIR / "index" / "java_index_flow_lancedb.py"
_BUILDER_FILE = _PKG_DIR / "graph" / "build_ast_graph.py"
_INSTRUCTIONS = (
    "Java codebase graph navigator over an indexed Java codebase. "
    "Tools: search (NL/code locate), find (structured NodeFilter), describe (one node + edge_summary: stored edge-label counts and optional composed keys for type Symbols and override-axis virtual keys for method Symbols), "
    "neighbors (one hop; you MUST pass direction in|out AND edge_types list — no defaults), "
    "resolve (identifier-shaped lookup for symbol/route/client/producer — three statuses: one | many | none). "
    "Unknown filter keys and populated fields not applicable to the effective node kind fail with success=false and message. "
    "Successful responses from any tool may include `hints_structured` (tool call suggestions with a `reason` field) and `advisories` (pure informational text) when hints are enabled. "
    "Edge labels: EXTENDS, IMPLEMENTS, INJECTS, OVERRIDES, DECLARES, DECLARES_CLIENT, DECLARES_PRODUCER, CALLS, EXPOSES, HTTP_CALLS, ASYNC_CALLS; "
    "type Symbols may also use composed neighbors edge_types DECLARES.DECLARES_CLIENT, DECLARES.DECLARES_PRODUCER, DECLARES.EXPOSES (out only, type Symbol origin). "
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
    optimize_error: str | None = None


class IndexInfoOutput(BaseModel):
    lancedb_uri: str
    embedding_model: str
    project_root: str
    cocoindex_target: str
    tables: dict[str, str]
    graph: GraphMetaOutput


# Module-level scope manager, initialized in main()
_scope_manager: ScopeManager | None = None


class ScopeManager:
    """Manages automatic microservice scope detection and injection."""

    def __init__(self, source_root: Path):
        self.source_root = source_root
        self.default_scope: str | None = self._detect_scope()
        self._log_detection()

    def _detect_scope(self) -> str | None:
        from java_codebase_rag.graph.graph_enrich import detect_microservice_from_path

        candidate = detect_microservice_from_path(Path.cwd(), self.source_root)
        if candidate is None:
            return None
        # Only auto-scope to a microservice that actually has indexed code.
        # detect_microservice_from_path can mislabel a non-microservice
        # top-level child of source_root — most importantly the config/context
        # directory the MCP server is launched from (no build marker, no
        # source) — via its "first path segment under root" fallback. Scoping
        # every query to such a name yields zero matches, so all tools return
        # empty. A real microservice the operator is working in is, by
        # definition, present in the index, so validating against the indexed
        # set cannot suppress a legitimate scope. When the index is unreadable
        # (empty known set) we keep the detected candidate rather than silently
        # disabling auto-scope on a transient graph error.
        known = self._indexed_microservices()
        if known and candidate not in known:
            return None
        return candidate

    def _indexed_microservices(self) -> set[str]:
        """Microservice names that have indexed type symbols.

        Graph-only source of truth: the graph is always built alongside Lance,
        and a Lance-only index (no graph) is not a supported state. Any failure
        (graph missing, open error, empty index) returns an empty set, which
        ``_detect_scope`` treats as "cannot validate — keep detection".
        """
        try:
            if not LadybugGraph.exists():
                return set()
            # LadybugGraph.get() opens the DB and runs meta(); it can raise
            # (e.g. RuntimeError on ontology-version mismatch). Caught here ->
            # empty set -> _detect_scope keeps the detected scope.
            counts = LadybugGraph.get().microservice_counts()
            return {name for name in counts if name}
        except Exception:
            return set()

    def _log_detection(self) -> None:
        if self.default_scope:
            print(f"[scope] Detected microservice: {self.default_scope}", file=sys.stderr)
            print(f"[scope] Queries scoped to {self.default_scope}", file=sys.stderr)
        else:
            print("[scope] No microservice detected (at project root)", file=sys.stderr)
            print("[scope] Queries will span all microservices", file=sys.stderr)

    def apply_auto_scope(self, node_filter: mcp_v2.NodeFilter | None) -> mcp_v2.NodeFilter | None:
        """Apply auto-detected scope to filter if no explicit microservice is set."""
        if self.default_scope is None:
            return node_filter
        if node_filter is None:
            return mcp_v2.NodeFilter(microservice=self.default_scope)
        if node_filter.microservice is None:
            return node_filter.model_copy(update={"microservice": self.default_scope})
        return node_filter


def _resolve_lancedb_uri() -> str:
    raw = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if not raw:
        raw = str((_project_root() / ".java-codebase-rag").resolve())
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
    discovered = discover_project_root(Path.cwd())
    return discovered if discovered is not None else Path.cwd().resolve()


def _source_root_for_operator_config() -> Path | None:
    """``source_root`` arg to hand ``resolve_operator_config`` from the MCP server.

    Returns ``JAVA_CODEBASE_RAG_SOURCE_ROOT`` when set (an explicit operator
    override that wins and suppresses the YAML ``source_root`` field, exactly
    like CLI ``--source-root``), otherwise ``None`` — so
    ``resolve_operator_config`` runs its OWN walk-up discovery and HONORS the
    YAML ``source_root`` field, matching the CLI (``init`` / ``increment`` /
    ``reprocess``) path.

    Do NOT pass ``_project_root()`` (the walk-up-discovered dir) here: a
    non-``None`` value routes into the "explicit source root" branch that
    skips the YAML ``source_root`` field, which made the MCP server and the
    CLI resolve different ``source_root`` / ``index_dir`` from the same config
    file (the init-vs-MCP index_dir divergence). ``_project_root()`` is kept
    only for the ``_resolve_lancedb_uri()`` fallback below.
    """
    env = os.environ.get("JAVA_CODEBASE_RAG_SOURCE_ROOT", "").strip()
    return Path(env).expanduser().resolve() if env else None


def _cocoindex_subprocess_env(project_root: Path) -> dict[str, str]:
    sub_env = os.environ.copy()
    sub_env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(project_root)
    idx = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx:
        sub_env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(Path(idx).expanduser().resolve())
    # Cap CocoIndex concurrency to avoid EMFILE ("too many open files") under
    # default OS fd limits. See: https://github.com/HumanBean17/java-codebase-rag/issues/306
    for _k, _v in cocoindex_subprocess_env_defaults().items():
        sub_env.setdefault(_k, _v)
    return sub_env


def _graph_enabled() -> bool:
    return LadybugGraph.exists()


def _graph_meta_output() -> GraphMetaOutput:
    if not LadybugGraph.exists():
        return GraphMetaOutput(
            success=True,
            enabled=False,
            db_path=resolve_ladybug_path(),
            message="Ladybug graph not present; run java-codebase-rag reprocess or build_ast_graph.py",
        )
    try:
        graph = LadybugGraph.get()
        meta = graph.meta()
    except Exception as e:
        return GraphMetaOutput(
            success=False,
            enabled=_graph_enabled(),
            db_path=resolve_ladybug_path(),
            message=f"Ladybug open failed: {e}",
        )
    if "error" in meta:
        return GraphMetaOutput(
            success=False,
            enabled=_graph_enabled(),
            db_path=meta.get("db_path", resolve_ladybug_path()),
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
        db_path=meta.get("db_path", resolve_ladybug_path()),
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
    try:
        from java_codebase_rag.search.search_lancedb import TABLES

        tables = dict(TABLES)
    except ImportError:
        # Graph-only install (no lancedb): no Lance vector tables exist.
        tables = {}
    return IndexInfoOutput(
        lancedb_uri=_resolve_lancedb_uri(),
        embedding_model=resolved_sbert_model_for_process_env(SBERT_MODEL),
        project_root=str(_project_root()),
        cocoindex_target=_COCOINDEX_TARGET,
        tables=tables,
        graph=_graph_meta_output(),
    )


async def _run_graph_phase(
    root: Path,
    *,
    quiet: bool,
    verbose: bool,
    on_progress: object | None,
    on_progress_console: object | None,
) -> tuple[int | None, str, str, bool]:
    """Run ``build_ast_graph.py`` and return ``(code, stdout, stderr, started)``.

    Shared by the vectors→graph refresh path and the graph-only path (macOS Intel,
    where the vector stack is gated off). ``started`` is True only when the graph
    subprocess was actually created, so callers set ``phases_run`` accurately — the
    CLI maps an empty ``phases_run`` to a preflight exit code 2 (nothing spawned).
    A missing builder or a spawn failure returns ``started=False`` with the graph
    code carrying the reason (``None`` for missing builder, ``-1`` for spawn error).
    """
    builder = _BUILDER_FILE
    if not builder.is_file():
        return None, "", "", False
    try:
        graph_args = [
            sys.executable,
            str(builder),
            "--source-root",
            str(root),
            "--ladybug-path",
            resolve_ladybug_path(),
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
        if quiet:
            gout_b, gerr_b = await gproc.communicate()
        else:
            gout_b, gerr_b = await accumulate_and_relay_subprocess_streams(
                gproc, relay=True, verbose=verbose,
                on_progress=on_progress, on_progress_console=on_progress_console,
            )
        return (
            gproc.returncode,
            gout_b.decode(errors="replace"),
            gerr_b.decode(errors="replace"),
            True,
        )
    except Exception as exc:
        return -1, "", f"graph builder spawn failed: {exc}", False


async def run_refresh_pipeline(
    *,
    quiet: bool = False,
    verbose: bool = True,
    on_progress=None,
    on_progress_console: object | None = None,
) -> RefreshIndexOutput:
    root = _project_root()
    if not vector_stack_installed():
        # Graph-only install (macOS Intel): the vector stack (cocoindex/lancedb/
        # sentence-transformers) is gated off by PEP 508 markers and uninstallable,
        # so the cocoindex binary is absent. Skip the vectors phase and build the
        # graph only — mirroring init/increment, which treat cocoindex-absent as a
        # skip, not a failure (the graph layer is the supported surface there). No
        # vectors progress event is emitted, so the renderer's vectors task stays
        # invisible (its "never spawned" invariant) instead of hanging at running.
        print(VECTORS_SKIPPED_GRAPH_ONLY, file=sys.stderr, flush=True)
        if not quiet:
            print(file=sys.stderr, flush=True)
        graph_code, graph_out, graph_err, started = await _run_graph_phase(
            root, quiet=quiet, verbose=verbose,
            on_progress=on_progress, on_progress_console=on_progress_console,
        )
        ok = graph_code == 0
        if not ok:
            message = (
                f"graph builder exit {graph_code}"
                if graph_code is not None
                else (graph_err.strip() or "graph builder unavailable")
            )
        else:
            message = "reprocess completed (graph-only; vectors skipped — vector stack not installed)"
        return RefreshIndexOutput(
            success=ok,
            exit_code=None,
            stdout="",
            stderr="",
            message=message,
            graph_exit_code=graph_code,
            graph_stdout=graph_out[-4000:] if len(graph_out) > 4000 else graph_out,
            graph_stderr=graph_err[-4000:] if len(graph_err) > 4000 else graph_err,
            phases_run=["graph"] if started else [],
            optimize_error=None,
        )
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        # 127 pre-spawn: emit a terminal failed vectors event so the renderer's
        # task doesn't hang at running (matches the sync pipeline path).
        if on_progress is not None:
            on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
        return RefreshIndexOutput(
            success=False,
            message=f"cocoindex not found next to Python: {cocoindex_bin}",
            phases_run=[],
        )
    flow_path = _FLOW_FILE
    if not flow_path.is_file():
        if on_progress is not None:
            on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
        return RefreshIndexOutput(
            success=False,
            message=f"java_index_flow_lancedb.py not found at {flow_path}",
            phases_run=[],
        )
    proc: asyncio.subprocess.Process | None = None
    out_b, err_b = b"", b""
    # DROP the Lance target tables so the update takes the fast INSERT path
    # instead of cocoindex's in-place bulk-update, which emits ~one deletion-
    # vector + version commit PER matched row — O(rows) of tiny file IO that
    # hangs for many minutes on large repos. Drop+recreate is identical output
    # for a full rebuild (the very thing --full-reprocess means). Same fix on
    # the sync path: pipeline.run_cocoindex_update. Drop failure is non-fatal:
    # the update falls back to the slow in-place path.
    try:
        drop_proc = await asyncio.create_subprocess_exec(
            str(cocoindex_bin),
            "drop",
            _COCOINDEX_TARGET,
            "-f",
            cwd=str(flow_path.parent),
            env=_cocoindex_subprocess_env(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await drop_proc.communicate()
    except Exception as exc:
        print(
            f"java-codebase-rag: drop-before-reprocess failed ({exc!s}); "
            "falling back to in-place update",
            file=sys.stderr,
        )
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
            if on_progress is not None:
                on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
            return RefreshIndexOutput(
                success=False,
                message=f"spawn failed: {exc!s}",
                phases_run=[],
            )
    else:
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
            # The vectors task is fed by the child's per-file ticks + the
            # approximate total line, parsed by the ProgressRelay inside the
            # async drain and routed to on_progress.
            out_b, err_b = await accumulate_and_relay_subprocess_streams(
                proc, relay=True, verbose=verbose,
                on_progress=on_progress, on_progress_console=on_progress_console,
            )
            code_c = proc.returncode if proc.returncode is not None else -1
        except Exception as exc:
            if on_progress is not None:
                on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status="failed", elapsed_s=None))
            return RefreshIndexOutput(
                success=False,
                message=f"spawn failed: {exc!s}",
                phases_run=[],
            )
        finally:
            # The parent emits the terminal vectors event (the flow can't — no
            # "all files done" hook). Drives clamp-on-completion + phase
            # transition to Optimize.
            if on_progress is not None:
                elapsed = time.perf_counter() - t0
                status = "done" if code_c == 0 else "failed"
                on_progress(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=None, status=status, elapsed_s=elapsed))
    assert proc is not None
    out = out_b.decode(errors="replace")
    err = err_b.decode(errors="replace")
    ok = proc.returncode == 0
    phases_run: list[Literal["vectors", "graph"]] = ["vectors"]
    graph_code: int | None = None
    graph_out = ""
    graph_err = ""
    optimize_error: str | None = None
    if ok:
        if not quiet:
            print(file=sys.stderr, flush=True)
        # Serialized post-flow Lance optimize: the flow disabled its background
        # optimize, so with cocoindex returned exit 0 there are no concurrent
        # writers — this is the safe window to compact. An optimize failure is
        # surfaced via optimize_error / stderr and must NOT flip the success of
        # a vectors phase that succeeded; the index is still searchable.
        try:
            from java_codebase_rag.lance_optimize import optimize_lance_tables

            idx_raw = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
            if idx_raw and not idx_raw.startswith(("s3://", "gs://", "az://")):
                idx_dir = Path(idx_raw).expanduser().resolve()
            elif idx_raw:
                idx_dir = Path(idx_raw)
            else:
                idx_dir = (root / ".java-codebase-rag").resolve()
            await optimize_lance_tables(idx_dir, quiet=quiet, on_progress=on_progress)
        except Exception as exc:
            optimize_error = f"lance optimize failed: {exc}"
            print(f"java-codebase-rag: {optimize_error}", file=sys.stderr)
        graph_code, graph_out, graph_err, graph_started = await _run_graph_phase(
            root, quiet=quiet, verbose=verbose,
            on_progress=on_progress, on_progress_console=on_progress_console,
        )
        if graph_started:
            phases_run = ["vectors", "graph"]
    message: str | None = None
    if not ok:
        message = f"cocoindex exit {proc.returncode}"
    elif graph_code is not None and graph_code != 0:
        message = f"graph builder exit {graph_code}"
    # Surface a post-flow optimize failure in the message too (success is not
    # flipped — the vectors phase succeeded and the index is still usable).
    if optimize_error is not None:
        message = optimize_error if message is None else f"{message}; {optimize_error}"
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
        optimize_error=optimize_error,
    )


def create_mcp_server() -> FastMCP:
    mcp = FastMCP("java-codebase-rag", instructions=_INSTRUCTIONS)

    @mcp.tool(
        name="search",
        description=(
            "Ranked chunk retrieval over content tables (java/sql/yaml); `query` is opaque text (natural language or code "
            "fragments) and results are score-ranked, not boolean-matched. For graph-structured listing "
            "(symbols/routes/clients/producers) use `find`, not `search`. Optional `filter` uses the same NodeFilter "
            "schema as `find` but only **symbol-applicable** fields apply — others return success=false. Substring "
            "fields match literally (no `*`/`?` metacharacters)—use ranked `query` text for fuzzy discovery. There is **no** "
            "structured DSL inside `query`; structured predicates belong in `find`. "
            "For identifier-shaped lookups (FQN, id, route/client identifiers, …), use `resolve` first; "
            "use `search` for natural-language or ranked fuzzy discovery. "
            "Set `explain=true` to include score breakdown per hit. "
            "Successful responses echo `limit`/`offset`."
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
            description="If true, fuse FTS + vector. Requires a single table (java/sql/yaml); hybrid with table='all' returns success=false.",
        ),
        limit: int = Field(default=5, ge=1, le=50, description="Max hits to return"),
        offset: int = Field(default=0, ge=0, le=500, description="Skip this many hits (pagination)"),
        path_contains: str | None = Field(
            default=None,
            description="Substring match on file path (pre-filter from index)",
        ),
        filter: mcp_v2.NodeFilter | None = Field(
            default=None,
            description=(
                "Optional NodeFilter post-filter on symbol-oriented hit rows. An empty object or omitted means no "
                "predicate. Unknown keys or populated fields not applicable to symbols return success=false."
            ),
        ),
        explain: bool = Field(
            default=False,
            description="If true, include score_components in each SearchHit (breakdown of distance/rrf, role, symbol, import_penalty).",
        ),
        chunks: bool = Field(
            default=False,
            description="If true, show every chunk (default collapses to one row per symbol/type).",
        ),
    ) -> mcp_v2.SearchOutput:
        scoped_filter = _scope_manager.apply_auto_scope(filter) if _scope_manager else filter
        return await asyncio.to_thread(
            mcp_v2.search_v2,
            query,
            table,
            hybrid,
            limit,
            offset,
            path_contains,
            scoped_filter,
            explain,
            None,
            not chunks,  # dedup=True by default; chunks=True opts out
        )

    @mcp.tool(
        name="find",
        description=(
            "Exact structured listing for one node kind. Per-kind applicable fields: **symbol** — "
            "microservice, module, role, exclude_roles, annotation, capability, fqn_contains, symbol_kind, symbol_kinds; "
            "**route** — microservice, module, http_method, path_contains, framework; **client** — microservice, module, "
            "source_layer, client_kind, target_service, target_path_contains, http_method; **producer** — microservice, "
            "module, source_layer, producer_kind, topic_contains. "
            "`role` is singular and `exclude_roles` plural; `capability` is a functional tag assigned during indexing. "
            "`fqn_contains` is a substring predicate — for exact FQN or id lookup use `resolve`/`describe`. "
            "Substring fields match literally (Cypher `CONTAINS`); no wildcard metacharacters. An empty filter (`{}`) or `filter=None` means no predicate (all nodes of "
            "that kind; use pagination). Unknown keys or inapplicable populated fields return success=false. "
            "Successful responses echo `limit`/`offset`."
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
        filter: mcp_v2.NodeFilter = Field(
            ...,
            description=(
                "Required NodeFilter object (extra keys forbidden). Fields must be applicable to `kind`."
            ),
        ),
        limit: int = Field(default=25, ge=1, le=500, description="Max nodes to return"),
        offset: int = Field(default=0, ge=0, le=499, description="Skip this many nodes (pagination)"),
    ) -> mcp_v2.FindOutput:
        scoped_filter = _scope_manager.apply_auto_scope(filter) if _scope_manager else filter
        return await asyncio.to_thread(mcp_v2.find_v2, kind, scoped_filter, limit, offset, None)

    @mcp.tool(
        name="describe",
        description=(
            "Full node record plus `edge_summary` (in/out counts per stored edge label). For type Symbols, `edge_summary` "
            "also exposes composed keys (DECLARES.DECLARES_CLIENT, DECLARES.DECLARES_PRODUCER, DECLARES.EXPOSES); for "
            "non-static method Symbols it adds override-axis virtual keys (OVERRIDDEN_BY and its composed forms, plus an "
            "`OVERRIDES` map merging stored `[:OVERRIDES]` counts with the dispatch-up rollup). These composed/override keys "
            "are out-only and navigable via `neighbors`; the stored `OVERRIDES` is also a normal edge label (in toward declaration). "
            "Pass `id` for any kind, or exact `fqn` for Symbol lookup (`id` wins when both are set). "
            "`describe(fqn=…)` keeps the first graph row when multiple symbols share that FQN; when an FQN may collide, "
            "prefer `resolve(identifier=…, hint_kind='symbol')` first, then `describe(id=…)` on the chosen node."
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
            "Graph walk: **direction** (`in` | `out`) and non-empty **edge_types** are required (one hop over stored edge "
            "labels; type/method Symbol origins may also pass composed or override-axis keys — see `edge_types`). From a "
            "type Symbol, `direction='out'` with EXPOSES yields route nodes and HTTP_CALLS/ASYNC_CALLS yield client/producer "
            "nodes; `direction='in'` reverses each relationship. "
            "`direction` and `edge_types` have no defaults; an empty `edge_types` fails. The CALLS-only features — "
            "`edge_filter`, `include_unresolved`, `dedup_calls` — each require `edge_types=['CALLS']`; `edge_filter` and "
            "`include_unresolved` are mutually exclusive. Violating a precondition (wrong CALLS context, composed/override "
            "keys on an ineligible origin or with `direction='in'`, unknown filter keys) returns "
            "success=false with a message; `dedup_calls` with other edge_types is a silent no-op. "
            "Optional `filter` applies to each neighbor endpoint row; populated fields must be applicable to that "
            "neighbor's kind—mixed-kind result sets fail on the first inapplicable neighbor (per-neighbor strict frame). "
            "Each edge's `attrs.strategy` indicates resolution quality (brownfield/fallback vs primary paths). "
            "Successful responses echo `requested_edge_types`."
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
        filter: mcp_v2.NodeFilter | None = Field(
            default=None,
            description=(
                "Optional NodeFilter on the neighbor node. An empty object or omitted means no predicate. "
                "Same applicability rules as `find` for that node's kind."
            ),
        ),
        edge_filter: mcp_v2.EdgeFilter | None = Field(
            default=None,
            description=(
                "Optional EdgeFilter on CALLS edge attributes (edge_types=['CALLS'] only). Use "
                "callee_declaring_role for callee stereotype projection — not NodeFilter.role on method neighbors. "
                "Mutually exclusive with include_unresolved."
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
        scoped_filter = _scope_manager.apply_auto_scope(filter) if _scope_manager else filter
        return await asyncio.to_thread(
            mcp_v2.neighbors_v2,
            ids,
            direction,
            edge_types,
            limit,
            offset,
            scoped_filter,
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
            "Malformed empty/whitespace identifier returns success=false. "
            "Examples: resolve('com.foo.Bar', hint_kind='symbol'); "
            "resolve('GET /api/v1/customers', hint_kind='route'); "
            "resolve('PaymentClient', hint_kind='client'); "
            "resolve('order.created', hint_kind='producer'); "
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

    return mcp


def main() -> None:
    raise_fd_limit()
    emit_legacy_env_hints_if_present()

    # Load YAML config and apply embedding settings to environment
    # This ensures SBERT_MODEL and SBERT_DEVICE from .java-codebase-rag.yml are available
    # before any tool handler runs (same behavior as CLI path)
    cfg = resolve_operator_config(source_root=_source_root_for_operator_config())
    cfg.apply_to_os_environ()
    mcp_v2.set_hints_enabled(cfg.hints_enabled)
    mcp_v2.set_absence_config(cfg)
    resolve_service.set_absence_config(cfg)

    # Initialize scope manager for automatic microservice detection
    global _scope_manager
    _scope_manager = ScopeManager(cfg.source_root)

    asyncio.run(create_mcp_server().run_stdio_async())


if __name__ == "__main__":
    main()
