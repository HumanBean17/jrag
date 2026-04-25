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
from kuzu_queries import KuzuGraph, resolve_kuzu_path
from search_lancedb import (
    TABLES,
    explain_score_components,
    l2_distance_to_score,
    run_search,
)

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
    "Semantic search over a LanceDB code index (Java, Flyway SQL, YAML configs), plus a "
    "deterministic AST-derived graph (Kuzu) for structural queries: implementors, "
    "subclasses, injectors, impact analysis, and graph-expanded codebase_search. "
    "Use codebase_search for meaning-based discovery — prefer limit 5; use offset to page. "
    "Use find_implementors / find_subclasses / find_injectors / impact_analysis / neighbors / "
    "list_by_role / list_by_annotation for exact structural traversal. "
    "Use trace_flow for CONTROLLER -> SERVICE -> REPOSITORY/FEIGN end-to-end chains; "
    "its seeds are auto-filtered to entrypoint-like roles (CONTROLLER / COMPONENT / "
    "SERVICE / FEIGN_CLIENT), so it's the right tool for 'how / what happens when' queries. "
    "For behavioural queries prefer exclude_roles=['DTO','ENTITY','CONFIG','OTHER'] on "
    "codebase_search; for schema/domain questions pass role='DTO' or role='ENTITY' instead. "
    "Set auto_hybrid=true when the query contains identifiers / CamelCase / snake_case "
    "tokens (class names, method names) — it mixes vector + FTS via RRF. "
    "SCOPING (the *previous* `service` field is gone): pass `microservice` to filter to "
    "one deployable repo / top-level dir under project root (e.g. 'java-microservice-A'); "
    "pass `module` to filter to a single Maven/Gradle build module (innermost build-marker "
    "ancestor; equal to `microservice` for single-module projects). Both are AND-combined "
    "when set together. Use list_code_index_tables to discover what microservice and module "
    "names actually got inferred. "
    "Java hits include role / module / microservice / FQN + score_components + a compact "
    "`why` string explaining the rank (dist / role / symbol / import_penalty). Pass "
    "context_neighbors=1 to attach adjacent chunks via `context_before` / `context_after`. "
    "refresh_code_index runs cocoindex and then rebuilds the Kuzu graph; needs "
    "LANCEDB_MCP_ALLOW_REFRESH=1 and cocoindex beside the venv Python."
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
    package: str | None = Field(default=None, description="Java package (enriched)")
    module: str | None = Field(
        default=None,
        description=(
            "Inferred Maven/Gradle build module (innermost build-marker ancestor). "
            "Equals `microservice` for single-module projects."
        ),
    )
    microservice: str | None = Field(
        default=None,
        description=(
            "Inferred microservice (outermost build-marker ancestor under project_root, "
            "or top-level directory)."
        ),
    )
    primary_type_fqn: str | None = Field(default=None, description="Enclosing type FQN")
    primary_type_kind: str | None = Field(default=None, description="class | interface | enum | record | annotation")
    role: str | None = Field(default=None, description="CONTROLLER/SERVICE/REPOSITORY/... (enriched)")
    annotations_on_type: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list, description="Fields/methods/types declared in chunk")
    graph_expanded: bool = Field(default=False, description="True if row came via Kuzu graph expansion")
    score_components: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Ranking rationale: distance / hybrid_rrf / role_weight / import_penalty."
        ),
    )
    context_before: str = Field(
        default="",
        description="Neighboring chunk text preceding this one (set when context_neighbors > 0).",
    )
    context_after: str = Field(
        default="",
        description="Neighboring chunk text following this one (set when context_neighbors > 0).",
    )
    why: str = Field(
        default="",
        description=(
            "Compact human-readable rank rationale derived from score_components "
            "(e.g. 'dist=0.42 role:SERVICE:+0.08 symbol:+0.05'). Empty when there "
            "is nothing notable to explain."
        ),
    )


class SymbolDto(BaseModel):
    id: str
    kind: str
    name: str
    fqn: str
    package: str = ""
    module: str = ""
    microservice: str = ""
    filename: str = ""
    start_line: int = 0
    end_line: int = 0
    start_byte: int = 0
    end_byte: int = 0
    modifiers: list[str] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    role: str = ""
    signature: str = ""
    parent_id: str = ""
    resolved: bool = True


class SymbolListOutput(BaseModel):
    success: bool
    results: list[SymbolDto] = Field(default_factory=list)
    message: str | None = None


class InjectionEdgeDto(BaseModel):
    consumer: SymbolDto
    target: SymbolDto
    mechanism: str
    annotation: str
    field_or_param: str
    resolved: bool = True


class InjectorsOutput(BaseModel):
    success: bool
    results: list[InjectionEdgeDto] = Field(default_factory=list)
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
    message: str | None = None


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
    graph: GraphMetaOutput


class ViaEdgeDto(BaseModel):
    edge_type: str = Field(description="INJECTS | EXTENDS | IMPLEMENTS")
    from_fqn: str = Field(description="FQN of the parent-stage symbol that introduced this node")
    hop: int = Field(description="1 = direct neighbour of the previous-stage frontier")


class StageSymbolDto(BaseModel):
    symbol: SymbolDto
    via: list[ViaEdgeDto] = Field(
        default_factory=list,
        description=(
            "Edges from the previous stage that pulled this symbol in. "
            "Empty for stage 0 (seeds)."
        ),
    )


class FlowStageDto(BaseModel):
    stage_index: int
    stage_name: str
    symbols: list[StageSymbolDto] = Field(default_factory=list)


class TraceFlowOutput(BaseModel):
    success: bool
    stages: list[FlowStageDto] = Field(default_factory=list)
    seed_hits: list[CodeChunkHit] = Field(default_factory=list)
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
    )


def _symbol_to_dto(s) -> SymbolDto:
    return SymbolDto(
        id=s.id, kind=s.kind, name=s.name, fqn=s.fqn,
        package=s.package, module=s.module, microservice=s.microservice,
        filename=s.filename,
        start_line=s.start_line, end_line=s.end_line,
        start_byte=s.start_byte, end_byte=s.end_byte,
        modifiers=list(s.modifiers), annotations=list(s.annotations),
        role=s.role, signature=s.signature, parent_id=s.parent_id,
        resolved=bool(s.resolved),
    )


def _clean_str_list(val: Any) -> list[str]:
    """Coerce a column value into list[str].

    Native Arrow lists come back as Python lists (new indexes). Legacy rows
    written before the LanceType(pa.list_(pa.string())) schema fix arrive as
    JSON-encoded strings like '["Service","Component"]' — decode them so we
    never iterate a string character-by-character.
    """
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val]
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                import json as _json
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
        return [s]
    return [str(val)]


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
        if "_rrf_score" in r:
            score = float(r["_rrf_score"])
        elif r.get("_hybrid"):
            score = float(r.get("_score", 0.0))
        else:
            score = l2_distance_to_score(float(r.get("_distance", 1.0)))
        hints = r.get("_hints") or {}
        comps = {
            k: float(v) for k, v in (r.get("_score_components") or {}).items()
            if isinstance(v, (int, float))
        }
        why = explain_score_components(
            comps,
            role=r.get("role") or None,
            hybrid=bool(r.get("_hybrid") or "_rrf_score" in r),
            graph_expanded=bool(r.get("_graph_expanded", False)),
        )
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
                package=r.get("package") or None,
                module=r.get("module") or None,
                microservice=r.get("microservice") or None,
                primary_type_fqn=r.get("primary_type_fqn") or None,
                primary_type_kind=r.get("primary_type_kind") or None,
                role=r.get("role") or None,
                annotations_on_type=_clean_str_list(r.get("annotations_on_type")),
                symbols=_clean_str_list(r.get("symbols")),
                graph_expanded=bool(r.get("_graph_expanded", False)),
                score_components=comps,
                context_before=str(r.get("_context_before") or ""),
                context_after=str(r.get("_context_after") or ""),
                why=why,
            )
        )
    return hits


def create_mcp_server() -> FastMCP:
    mcp = FastMCP("lancedb-code-search", instructions=_INSTRUCTIONS)

    @mcp.tool(
        name="codebase_search",
        description=(
            "Vector / hybrid search over a LanceDB codebase index. "
            "Natural language or code snippet; optional hybrid for identifiers.\n"
            "Examples:\n"
            '  minimal:         {\"query\": \"how chat assigns on operator\"}\n'
            '  behavioural:     {\"query\": \"how chat assigns on operator\", '
            '\"exclude_roles\": [\"DTO\",\"ENTITY\",\"CONFIG\",\"OTHER\"]}\n'
            '  identifier-ish:  {\"query\": \"DistributionChunkService\", \"auto_hybrid\": true}\n'
            '  scoped:          {\"query\": \"...\", \"microservice\": \"chat-assign\", '
            '\"role\": \"SERVICE\", \"limit\": 10}\n'
            '  cross-module:    {\"query\": \"...\", \"microservice\": \"chat-core\", '
            '\"module\": \"chat-app\"}\n'
            "Limits: omit any optional field rather than passing null is fine; lists must "
            "be JSON arrays of strings; `role` is a single value (use `exclude_roles` for sets)."
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
        auto_hybrid: bool = Field(default=False),
        role: str | None = Field(
            default=None,
            description="Java only: CONTROLLER|SERVICE|REPOSITORY|COMPONENT|CONFIG|ENTITY|FEIGN_CLIENT|MAPPER|DTO",
        ),
        exclude_roles: list[str] | None = Field(
            default=None,
            description=(
                "Java only: drop chunks whose role is in this list. Useful "
                "for behavioural queries; try ['DTO','ENTITY','CONFIG','OTHER']."
            ),
        ),
        module: str | None = Field(
            default=None,
            description=(
                "Filter to a single Maven/Gradle build module (innermost "
                "build-marker ancestor)."
            ),
        ),
        microservice: str | None = Field(
            default=None,
            description=(
                "Filter to a single deployable microservice (outermost "
                "build-marker ancestor under project_root, or top-level "
                "directory). AND-combined with `module` when both are set."
            ),
        ),
        package_prefix: str | None = Field(
            default=None, description="Java only: filter to `package = prefix` or `package LIKE prefix.%`.",
        ),
        graph_expand: bool = Field(
            default=False,
            description="After vector top-k on `java`, BFS through the Kuzu graph and merge neighbor chunks (RRF).",
        ),
        expand_depth: int = Field(default=1, ge=1, le=3),
        context_neighbors: int = Field(
            default=0,
            ge=0, le=2,
            description=(
                "If > 0, attach that many adjacent Java chunks as "
                "context_before/context_after. 1 is a good default."
            ),
        ),
    ) -> CodeSearchOutput:
        if table not in ("java", "sql", "yaml", "all"):
            return CodeSearchOutput(
                success=False,
                message=(
                    f"Invalid `table` value {table!r}: must be one of "
                    "'java', 'sql', 'yaml', 'all' (string)."
                ),
            )
        if hybrid and table == "all":
            return CodeSearchOutput(
                success=False,
                message=(
                    "`hybrid=true` requires a single table; got table='all'. "
                    "Set table to 'java', 'sql', or 'yaml'."
                ),
            )
        if auto_hybrid and table == "all":
            return CodeSearchOutput(
                success=False,
                message=(
                    "`auto_hybrid=true` requires a single table; got table='all'. "
                    "Set table to 'java', 'sql', or 'yaml'."
                ),
            )
        # Note: type-level validation (string/list/int) is enforced by
        # FastMCP/Pydantic before this handler runs. We only catch
        # *value*-level problems here (bad enum values, incompatible flag
        # combinations) so the error text names the field and the fix.

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
                role=role,
                module=module,
                microservice=microservice,
                package_prefix=package_prefix,
                graph_expand=graph_expand and _graph_enabled(),
                expand_depth=expand_depth,
                context_neighbors=context_neighbors,
                exclude_roles=exclude_roles,
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
        name="find_implementors",
        description="Classes implementing a given interface (simple name or FQN).",
    )
    async def find_implementors(
        name: str = Field(description="Interface simple name or FQN"),
        module: str | None = Field(default=None, description="Maven/Gradle module name."),
        microservice: str | None = Field(default=None, description="Microservice name."),
        limit: int = Field(default=100, ge=1, le=500),
    ) -> SymbolListOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(
            graph.find_implementors, name,
            module=module, microservice=microservice, limit=limit,
        )
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="find_subclasses",
        description="Classes/interfaces extending a given class or interface.",
    )
    async def find_subclasses(
        name: str = Field(description="Class/interface simple name or FQN"),
        module: str | None = Field(default=None, description="Maven/Gradle module name."),
        microservice: str | None = Field(default=None, description="Microservice name."),
        limit: int = Field(default=100, ge=1, le=500),
    ) -> SymbolListOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(
            graph.find_subclasses, name,
            module=module, microservice=microservice, limit=limit,
        )
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="find_injectors",
        description="Classes that inject (field/ctor/setter/Lombok) a given type.",
    )
    async def find_injectors(
        name: str = Field(description="Injected type simple name or FQN"),
        module: str | None = Field(default=None, description="Maven/Gradle module name."),
        microservice: str | None = Field(default=None, description="Microservice name."),
        limit: int = Field(default=100, ge=1, le=500),
    ) -> InjectorsOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return InjectorsOutput(success=False, message=msg)
        edges = await asyncio.to_thread(
            graph.find_injectors, name,
            module=module, microservice=microservice, limit=limit,
        )
        results = [
            InjectionEdgeDto(
                consumer=_symbol_to_dto(e.src),
                target=_symbol_to_dto(e.dst),
                mechanism=e.mechanism, annotation=e.annotation,
                field_or_param=e.field_or_param, resolved=e.resolved,
            )
            for e in edges
        ]
        return InjectorsOutput(success=True, results=results)

    @mcp.tool(
        name="list_by_role",
        description="All graph symbols with a given role (CONTROLLER|SERVICE|REPOSITORY|...).",
    )
    async def list_by_role(
        role: str = Field(description="CONTROLLER|SERVICE|REPOSITORY|COMPONENT|CONFIG|ENTITY|FEIGN_CLIENT|MAPPER|OTHER"),
        module: str | None = Field(default=None, description="Maven/Gradle module name."),
        microservice: str | None = Field(default=None, description="Microservice name."),
        limit: int = Field(default=100, ge=1, le=500),
    ) -> SymbolListOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(
            graph.list_by_role, role,
            module=module, microservice=microservice, limit=limit,
        )
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="list_by_annotation",
        description="All graph symbols whose annotations list contains the given simple name.",
    )
    async def list_by_annotation(
        annotation: str = Field(description="Annotation simple name, e.g. 'Transactional'"),
        module: str | None = Field(default=None, description="Maven/Gradle module name."),
        microservice: str | None = Field(default=None, description="Microservice name."),
        limit: int = Field(default=100, ge=1, le=500),
    ) -> SymbolListOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(
            graph.list_by_annotation, annotation,
            module=module, microservice=microservice, limit=limit,
        )
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="graph_neighbors",
        description="Generic bidirectional neighbor expansion over EXTENDS|IMPLEMENTS|INJECTS.",
    )
    async def graph_neighbors(
        name: str = Field(description="Symbol simple name or FQN"),
        depth: int = Field(default=1, ge=1, le=3),
        edge_types: list[str] | None = Field(
            default=None, description="Subset of: EXTENDS, IMPLEMENTS, INJECTS",
        ),
        direction: str = Field(default="both", description="out | in | both"),
        limit: int = Field(default=200, ge=1, le=1000),
    ) -> SymbolListOutput:
        if direction not in ("out", "in", "both"):
            return SymbolListOutput(success=False, message="direction must be out|in|both")
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(
            graph.neighbors, name,
            depth=depth, edge_types=edge_types, direction=direction, limit=limit,
        )
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="impact_analysis",
        description="Reverse closure over INJECTS+IMPLEMENTS+EXTENDS (who breaks if this changes).",
    )
    async def impact_analysis(
        name: str = Field(description="Symbol simple name or FQN"),
        depth: int = Field(default=2, ge=1, le=4),
        limit: int = Field(default=300, ge=1, le=1000),
    ) -> SymbolListOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return SymbolListOutput(success=False, message=msg)
        rows = await asyncio.to_thread(graph.impact_analysis, name, depth=depth, limit=limit)
        return SymbolListOutput(success=True, results=[_symbol_to_dto(r) for r in rows])

    @mcp.tool(
        name="graph_meta",
        description="Kuzu graph metadata: counts, ontology version, build timestamp.",
    )
    async def graph_meta() -> GraphMetaOutput:
        return _graph_meta_output()

    @mcp.tool(
        name="trace_flow",
        description=(
            "End-to-end behavioural trace for a natural-language query. "
            "Picks seed entrypoints via vector search (restricted to CONTROLLER / "
            "COMPONENT / SERVICE / FEIGN_CLIENT roles, with a fallback pass when "
            "nothing matches), then walks the Kuzu graph in role-ordered stages "
            "(CONTROLLER/COMPONENT/SERVICE -> SERVICE/COMPONENT -> "
            "FEIGN_CLIENT/REPOSITORY/MAPPER) and returns the likely chain.\n"
            "Each stage symbol carries `via: [{edge_type, from_fqn, hop}]` so "
            "callers can see *why* it was pulled in (INJECTS / EXTENDS / "
            "IMPLEMENTS). Stage 0 is seeds and has `via=[]`.\n"
            "Examples:\n"
            '  minimal:  {\"query\": \"what happens on new client message\"}\n'
            '  scoped:   {\"query\": \"...\", \"microservice\": \"chat-assign\", '
            '\"seed_limit\": 5, \"stage_limit\": 8, \"depth\": 2}\n'
            "Limits: exactly 3 stages (entrypoints / services / integrations); "
            "`seed_limit` caps the vector-search seeds feeding stage 0; `stage_limit` "
            "caps per-stage size; `depth` is hops-per-stage, not total depth. This is "
            "not a full call graph — method-level CALLS / PUBLISHES edges are not yet "
            "indexed."
        ),
    )
    async def trace_flow(
        query: str = Field(description="Behavioural query, e.g. 'what happens on new client message'."),
        module: str | None = Field(
            default=None, description="Restrict the trace to a single Maven/Gradle module.",
        ),
        microservice: str | None = Field(
            default=None, description="Restrict the trace to a single microservice.",
        ),
        seed_limit: int = Field(default=5, ge=1, le=20),
        stage_limit: int = Field(default=8, ge=1, le=50),
        depth: int = Field(default=2, ge=1, le=3),
        exclude_roles: list[str] | None = Field(
            default=None,
            description=(
                "Additional roles to exclude when producing `seed_hits` "
                "(on top of the DTO/ENTITY/CONFIG baseline). The graph "
                "traversal stages are always role-ordered regardless."
            ),
        ),
    ) -> TraceFlowOutput:
        ok, graph, msg = _require_graph()
        if not ok or graph is None:
            return TraceFlowOutput(success=False, message=msg)

        uri = _resolve_lancedb_uri()
        is_remote = uri.startswith(("s3://", "gs://", "az://"))
        if not is_remote and not Path(uri).exists():
            return TraceFlowOutput(
                success=False,
                message=f"LanceDB path does not exist: {uri}.",
            )

        model_name = os.environ.get("SBERT_MODEL", SBERT_MODEL)
        device = os.environ.get("SBERT_DEVICE") or None

        # Baseline excludes passive/infra roles so DTOs/entities never
        # become stage-0 entrypoints for behavioural queries. Callers can
        # extend this via `exclude_roles`.
        baseline_excludes = {"DTO", "ENTITY", "CONFIG", "OTHER"}
        if exclude_roles:
            baseline_excludes.update(r.upper() for r in exclude_roles if r)

        entry_roles = ["CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"]

        def _seed(role_allowlist: list[str] | None) -> list[dict[str, Any]]:
            model = _get_sentence_transformer(model_name, device)
            return run_search(
                query,
                uri=uri,
                table_keys=["java"],
                limit=seed_limit,
                offset=0,
                path_substring=None,
                model_name=model_name,
                device=device,
                model=model,
                hybrid=False,
                fts_text=None,
                auto_hybrid=False,
                role=None,
                module=module,
                microservice=microservice,
                package_prefix=None,
                graph_expand=False,
                expand_depth=1,
                context_neighbors=0,
                role_in=role_allowlist,
                exclude_roles=None if role_allowlist else sorted(baseline_excludes),
            )

        # First pass: restrict seeds to entrypoint-like roles. If that
        # comes back empty (e.g. a codebase without @Controller), fall
        # back to the baseline-excluded search so we still surface
        # *something* rather than nothing.
        try:
            seed_rows = await asyncio.to_thread(_seed, entry_roles)
            if not seed_rows:
                seed_rows = await asyncio.to_thread(_seed, None)
        except Exception as e:
            return TraceFlowOutput(success=False, message=f"seed search failed: {e!s}")

        seeds = sorted({
            str(r.get("primary_type_fqn"))
            for r in seed_rows
            if r.get("primary_type_fqn")
        })
        if not seeds:
            return TraceFlowOutput(
                success=True,
                seed_hits=_rows_to_hits(seed_rows),
                message="No FQNs on seed hits; re-index to populate enrichment.",
            )

        try:
            stages_raw = await asyncio.to_thread(
                graph.trace_flow, seeds,
                module=module, microservice=microservice,
                depth=depth, stage_limit=stage_limit,
            )
        except Exception as e:
            return TraceFlowOutput(success=False, message=f"trace failed: {e!s}")

        stage_names = ("entrypoints", "services", "integrations")
        stages: list[FlowStageDto] = []
        for i, stage in enumerate(stages_raw):
            name = stage_names[i] if i < len(stage_names) else f"stage_{i}"
            entries: list[StageSymbolDto] = []
            for entry in stage:
                entries.append(StageSymbolDto(
                    symbol=_symbol_to_dto(entry.symbol),
                    via=[
                        ViaEdgeDto(edge_type=v.edge_type, from_fqn=v.from_fqn, hop=v.hop)
                        for v in entry.via
                    ],
                ))
            stages.append(FlowStageDto(
                stage_index=i, stage_name=name, symbols=entries,
            ))

        return TraceFlowOutput(
            success=True,
            stages=stages,
            seed_hits=_rows_to_hits(seed_rows),
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
