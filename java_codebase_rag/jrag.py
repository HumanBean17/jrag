"""jrag - agent-facing CLI (PR-JRAG-1a foundation).

Compose-and-render layer over the existing backend (``resolve_v2``,
``LadybugGraph``, ``mcp_v2`` handlers, ``run_search``). v1 loads the index
in-process per call (no daemon); reuses the operator's index directory and
config resolver (``resolve_operator_config`` + ``apply_to_os_environ``).

PR-JRAG-1a ships only the foundation: ``build_parser`` (with ``--offset``
intentionally NOT global - registered only on find/search in PR-1b/PR-4),
``_resolve_cfg`` (operator config reuse), ``_load_graph`` (actionable error
envelopes), ``main`` (``raise_fd_limit`` first; stdout envelope + stderr
traceback on error), and the ``status`` command. Later PRs add subcommands and
fill the ``agent_next_actions`` hook.

Lazy-import invariant: ``build_parser()`` imports NO backend modules - so
``jrag --help`` stays fast and free of torch/sentence_transformers/mcp_v2.
Backend imports (``resolve_service``, ``ladybug_queries``,
``resolve_operator_config``, ``jrag_envelope`` helpers) live inside command
handlers. Sentinel:
    python -c "import java_codebase_rag.jrag as j; j.build_parser()"
loads no torch / sentence_transformers / mcp_v2.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from java_codebase_rag._fdlimit import raise_fd_limit

__all__ = ["build_parser", "main", "_console_script_main"]


class _IndexNotFound(RuntimeError):
    """Raised when no LadybugDB graph exists at the resolved path."""


class _IndexStale(RuntimeError):
    """Raised when the on-disk graph's ontology is older than required."""


# Generous limit for the topics --consumer-in / listeners --topic-prefix
# compose fetches (these resolve cross-topic edges and should not silently
# truncate the listener/consumer set under typical fixture sizes).
_CONSUMER_FETCH_LIMIT = 200


def _load_graph_or_error(args: argparse.Namespace):
    """Resolve config + load graph; on missing/stale index, print an error
    envelope and return ``(cfg, graph_or_None, rc)``.

    Shared by every listing command so the cfg/load/error frame is not
    hand-copied. ``rc`` is 2 on error (envelope already printed), 0 on success.
    """
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    cfg = _resolve_cfg(args)
    try:
        graph = _load_graph(cfg)
    except (_IndexNotFound, _IndexStale) as exc:
        env = Envelope(status="error", message=str(exc))
        print(render(env, fmt=args.format))
        return cfg, None, 2
    return cfg, graph, 0


def _clamped_limit(args: argparse.Namespace) -> int:
    """Return the limit clamped so ``limit+1 <= 500`` (backend clamp)."""
    raw_limit = args.limit if args.limit is not None else 20
    return min(raw_limit, 499)


def _render_listing(rows, *, limit: int, args: argparse.Namespace, noun: str) -> int:
    """Apply +1-fetch truncation, build the envelope, render as a listing.

    Shared by the listing commands whose backend returns a flat row list
    (routes / clients / producers). ``rows`` must already be the limit+1
    fetch. Renders as the default shape (no ``shape=``).
    """
    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook, to_envelope_rows
    from java_codebase_rag.jrag_render import render

    node_list = to_envelope_rows(rows) if rows and not isinstance(rows[0], dict) else list(rows)
    display_nodes_list, truncated = mark_truncated(node_list, limit)
    display_nodes = {node["id"]: node for node in display_nodes_list}

    env = Envelope(status="ok", nodes=display_nodes, truncated=truncated)
    next_actions_hook(env)
    print(render(env, fmt=args.format, noun=noun))
    return 0


def _symbol_hit_to_dict(hit) -> dict:
    """Convert a ``SymbolHit`` (dataclass) to the envelope node dict shape."""
    return {
        "id": hit.id,
        "kind": "symbol",
        "fqn": hit.fqn,
        "name": hit.name,
        "symbol_kind": hit.kind,
        "microservice": hit.microservice,
        "module": hit.module,
        "role": hit.role,
    }


def build_parser() -> argparse.ArgumentParser:
    """Argparse builder. Imports no backend modules.

    ``--offset`` is intentionally NOT a global flag (PR-JRAG-1a contract): it
    is added only to ``find`` / ``search`` subparsers in PR-JRAG-1b / PR-JRAG-4
    (those commands route through ``find_v2`` / ``search_v2`` which take an
    ``offset``). In 1a, no subparser has ``--offset``.
    """
    description = (
        "jrag - agent-facing CLI for graph-native code intelligence.\n\n"
        "Every <query> command resolves the identifier (resolve_v2) as the first\n"
        "step and maps one/many/none onto a single envelope. Default output is\n"
        "compact text; `--format json` emits the envelope verbatim.\n\n"
        "Status command (PR-JRAG-1a):\n"
        "  status            Print index freshness, ontology version, and counts.\n"
        "\n"
        "Run `jrag <command> --help` for command-specific options."
    )
    parser = argparse.ArgumentParser(
        prog="jrag",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        exit_on_error=False,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Common flags applied per command via parents=[common]. NOT global so
    # commands can override defaults (e.g. fan-out commands use limit=10).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--service", type=str, default=None, help="Filter by microservice.")
    common.add_argument("--module", type=str, default=None, help="Filter by module.")
    common.add_argument(
        "--limit", type=int, default=20, help="Cap on results (default 20; 10 for fan-out)."
    )
    common.add_argument(
        "--index-dir",
        type=str,
        default=None,
        dest="index_dir",
        help="Index directory override (default: discovered from cwd).",
    )
    common.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    common.add_argument(
        "--brief", action="store_true", help="Compact output (fewer fields per node)."
    )
    common.add_argument(
        "--fields",
        type=str,
        default=None,
        help="Comma-separated field allowlist for node projections.",
    )
    common.add_argument(
        "--count", action="store_true", help="Return only the count (no node rows)."
    )
    common.add_argument(
        "--exists", action="store_true", help="Return only an exists boolean (exit 0/2)."
    )

    status = subparsers.add_parser(
        "status",
        help="Print index freshness, ontology version, and counts.",
        parents=[common],
        description=(
            "Index health and freshness. Reports ontology version, source root, "
            "built_at, parse_errors, edge counts, and the counts dictionary from "
            "GraphMeta. Exits 2 with an actionable envelope if the index is "
            "missing or stale."
        ),
    )
    status.set_defaults(handler=_cmd_status)

    # find subparser (PR-JRAG-1b)
    find = subparsers.add_parser(
        "find",
        help="Find nodes by query or filter.",
        parents=[common],
        description=(
            "Find nodes by query or filter. Two modes:\n"
            "  Query mode (positional <query>): search by exact name/FQN (symbols only).\n"
            "  Filter mode (no positional): apply structured filters (NodeFilter flags).\n"
            "Kind inference: domain flags (--http-method, --client-kind, --producer-kind) imply\n"
            "route/client/producer when --kind is omitted. Contradiction emits an error envelope.\n"
            "Query mode + non-symbol kind (explicit or inferred) errors: name/FQN lookup only\n"
            "searches symbols; drop the positional <query> and use filter mode for routes/clients/producers."
        ),
    )
    find.add_argument("query", nargs="?", default=None, help="Search query (name/FQN). Omit for filter mode.")
    find.add_argument(
        "--kind",
        choices=("symbol", "route", "client", "producer"),
        default=None,
        help="Node kind (omit for auto-inference from domain flags).",
    )
    find.add_argument("--role", type=str, default=None, help="Filter by role.")
    find.add_argument("--exclude-role", type=str, default=None, help="Exclude by role.")
    find.add_argument("--java-kind", type=str, default=None, help="Filter by Java symbol kind.")
    find.add_argument("--annotation", type=str, default=None, help="Filter by annotation.")
    find.add_argument("--capability", type=str, default=None, help="Filter by capability.")
    find.add_argument("--framework", type=str, default=None, help="Filter by framework.")
    find.add_argument("--source-layer", type=str, default=None, help="Filter by source layer.")
    find.add_argument("--fqn-prefix", type=str, default=None, help="Filter by FQN prefix.")
    find.add_argument("--http-method", type=str, default=None, help="Filter by HTTP method (route).")
    find.add_argument("--path-prefix", type=str, default=None, help="Filter by path prefix (route).")
    find.add_argument("--client-kind", type=str, default=None, help="Filter by client kind (client).")
    find.add_argument("--calls-service", type=str, default=None, help="Filter by target service (client).")
    find.add_argument("--calls-path-prefix", type=str, default=None, help="Filter by target path prefix (client).")
    find.add_argument("--producer-kind", type=str, default=None, help="Filter by producer kind (producer).")
    find.add_argument("--topic-prefix", type=str, default=None, help="Filter by topic prefix (producer).")
    find.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Page offset (filter mode only; ignored in query mode).",
    )
    find.set_defaults(handler=_cmd_find)

    # inspect subparser (PR-JRAG-1b)
    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a node by query.",
        parents=[common],
        description=(
            "Inspect a node by resolving a query (name/FQN) and returning its full details\n"
            "including edge_summary. Uses resolve_v2 internally; on ambiguous candidates,\n"
            "returns them (no auto-pick). On not_found, returns an error envelope."
        ),
    )
    inspect.add_argument("query", help="Search query (name/FQN).")
    inspect.add_argument(
        "--kind",
        choices=("symbol", "route", "client", "producer"),
        default=None,
        help="Hint for resolve (omitted for broad search).",
    )
    inspect.add_argument("--java-kind", type=str, default=None, help="Post-filter by Java symbol kind.")
    inspect.add_argument("--role", type=str, default=None, help="Post-filter by role.")
    inspect.add_argument("--fqn-prefix", type=str, default=None, help="Post-filter by FQN prefix.")
    inspect.set_defaults(handler=_cmd_inspect)

    # routes subparser (PR-JRAG-2)
    routes = subparsers.add_parser(
        "routes",
        help="List HTTP routes.",
        parents=[common],
        description=(
            "List HTTP routes by microservice, framework, path prefix, or method. "
            "Returns route nodes (no resolve step)."
        ),
    )
    routes.add_argument("--framework", type=str, default=None, help="Filter by framework.")
    routes.add_argument("--path-prefix", type=str, default=None, help="Filter by path prefix.")
    routes.add_argument("--method", type=str, default=None, help="Filter by HTTP method.")
    routes.set_defaults(handler=_cmd_routes)

    # clients subparser (PR-JRAG-2)
    clients = subparsers.add_parser(
        "clients",
        help="List HTTP clients.",
        parents=[common],
        description=(
            "List HTTP clients by microservice, client kind, target service, or path prefix. "
            "Returns client nodes (no resolve step)."
        ),
    )
    clients.add_argument("--client-kind", type=str, default=None, help="Filter by client kind.")
    clients.add_argument("--calls-service", type=str, default=None, help="Filter by target service.")
    clients.add_argument("--path-prefix", type=str, default=None, help="Filter by path prefix.")
    clients.set_defaults(handler=_cmd_clients)

    # producers subparser (PR-JRAG-2)
    producers = subparsers.add_parser(
        "producers",
        help="List async message producers.",
        parents=[common],
        description=(
            "List async message producers by microservice, producer kind, or topic prefix. "
            "Returns producer nodes (no resolve step)."
        ),
    )
    producers.add_argument("--producer-kind", type=str, default=None, help="Filter by producer kind.")
    producers.add_argument("--topic-prefix", type=str, default=None, help="Filter by topic prefix.")
    producers.set_defaults(handler=_cmd_producers)

    # topics subparser (PR-JRAG-2)
    topics = subparsers.add_parser(
        "topics",
        help="List message topics (producer-grouped).",
        parents=[common],
        description=(
            "List message topics grouped by producer. "
            "No :Topic node exists; this command groups producers by topic name. "
            "--consumer-in resolves consumers (listener methods) via EXPOSES edges to Route(topic)."
        ),
    )
    topics.add_argument("--topic-prefix", type=str, default=None, help="Filter by topic prefix.")
    topics.add_argument("--producer-in", type=str, default=None, help="Scope producers to this microservice.")
    topics.add_argument("--consumer-in", type=str, default=None, help="Show consumers from this microservice.")
    topics.set_defaults(handler=_cmd_topics)

    # jobs subparser (PR-JRAG-2)
    jobs = subparsers.add_parser(
        "jobs",
        help="List scheduled tasks.",
        parents=[common],
        description=(
            "List scheduled task symbols (capability=SCHEDULED_TASK). "
            "Returns Symbol nodes with the SCHEDULED_TASK capability."
        ),
    )
    jobs.set_defaults(handler=_cmd_jobs)

    # listeners subparser (PR-JRAG-2)
    listeners = subparsers.add_parser(
        "listeners",
        help="List message listeners.",
        parents=[common],
        description=(
            "List message listener symbols (capability=MESSAGE_LISTENER). "
            "Returns Symbol nodes with the MESSAGE_LISTENER capability."
        ),
    )
    listeners.add_argument("--topic-prefix", type=str, default=None, help="Filter by topic prefix (on producer member).")
    listeners.set_defaults(handler=_cmd_listeners)

    # entities subparser (PR-JRAG-2)
    entities = subparsers.add_parser(
        "entities",
        help="List JPA entities.",
        parents=[common],
        description=(
            "List JPA entity symbols (role=ENTITY). "
            "Returns Symbol nodes with the ENTITY role."
        ),
    )
    entities.set_defaults(handler=_cmd_entities)

    return parser


def _resolve_cfg(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Resolve operator config (reuses the operator's cocoindex-free resolver).

    Same pattern as ``java_codebase_rag.cli._resolved_from_ns``: walks up from
    cwd to find a project root (config file or ``.java-codebase-rag/`` index),
    applies CLI ``--index-dir`` if given, and calls ``apply_to_os_environ`` so
    downstream modules see a consistent env (critically: SBERT_MODEL for
    ``jrag search`` in PR-JRAG-4).
    """
    from java_codebase_rag.config import discover_project_root, resolve_operator_config

    cfg = resolve_operator_config(
        source_root=discover_project_root(Path.cwd()),
        cli_index_dir=getattr(args, "index_dir", None),
    )
    cfg.apply_to_os_environ()
    return cfg


def _load_graph(cfg):  # type: ignore[no-untyped-def]
    """Load the LadybugGraph with actionable error envelopes.

    * missing index -> ``_IndexNotFound`` (caught in ``main`` -> envelope with
      a ``java-codebase-rag init --source-root <root>`` remediation).
    * ontology-mismatch (``RuntimeError`` from ``LadybugGraph.get``) ->
      ``_IndexStale`` (caught in ``main`` -> envelope with a rebuild hint).
    """
    from ladybug_queries import LadybugGraph

    ladybug_path = str(cfg.ladybug_path)
    if not LadybugGraph.exists(ladybug_path):
        raise _IndexNotFound(
            f"No index at {cfg.ladybug_path}. "
            "Run: java-codebase-rag init --source-root <root>"
        )
    try:
        return LadybugGraph.get(ladybug_path)
    except RuntimeError as exc:
        raise _IndexStale(str(exc)) from exc


def _cmd_status(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    cfg = _resolve_cfg(args)
    try:
        graph = _load_graph(cfg)
    except (_IndexNotFound, _IndexStale) as exc:
        env = Envelope(
            status="error",
            message=str(exc),
        )
        print(render(env, fmt=args.format))
        return 2

    meta = graph.meta()
    if "error" in meta:
        env = Envelope(
            status="error",
            message=f"Index meta read failed: {meta['error']}",
        )
        print(render(env, fmt=args.format))
        return 2

    counts = meta.get("counts") or {}
    edge_counts = meta.get("edge_counts") or {}
    # Single notional "index" node carrying kv fields + nested counts/edges
    # as top-level dict-valued fields. The renderer's inspect-shape dispatch
    # fires on ANY dict-typed value (structural signal, not name-based), so
    # ``counts`` / ``edges`` render as indented alphabetical sections without
    # abusing ``edge_summary`` (which is reserved for PR-JRAG-3 real edge
    # data). See jrag_render._render_inspect / _render_text_shape.
    env = Envelope(
        status="ok",
        nodes={
            "index": {
                "ontology_version": int(meta.get("ontology_version") or 0),
                "built_at": int(meta.get("built_at") or 0),
                "source_root": str(meta.get("source_root") or ""),
                "db_path": str(meta.get("db_path") or ""),
                "parse_errors": int(meta.get("parse_errors") or 0),
                "index_dir": str(cfg.index_dir.resolve()),
                "ladybug_path": str(cfg.ladybug_path.resolve()),
                "counts": dict(counts),
                "edges": dict(edge_counts),
            },
        },
    )
    print(render(env, fmt=args.format, noun="status", shape="inspect"))
    return 0


def _infer_kind(args: argparse.Namespace) -> str | None:
    """Infer kind from domain flags when --kind is omitted.

    Inference rules (PR-JRAG-1b):
      - --http-method or --path-prefix → route
      - --client-kind or --calls-service or --calls-path-prefix → client
      - --producer-kind or --topic-prefix → producer
      - else → symbol (default)
    Returns None if no flags are set (symbol default in callers).
    """
    if args.kind is not None:
        return args.kind
    if args.http_method or args.path_prefix:
        return "route"
    if args.client_kind or args.calls_service or args.calls_path_prefix:
        return "client"
    if args.producer_kind or args.topic_prefix:
        return "producer"
    return "symbol"


def _check_kind_contradiction(args: argparse.Namespace, inferred: str | None) -> tuple[bool, str | None]:
    """Check if domain flags contradict explicit --kind.

    Returns (is_contradiction, error_message). Contradiction pairs:
      - --kind symbol + any route flag (--http-method, --path-prefix)
      - --kind symbol + any client flag (--client-kind, --calls-service, --calls-path-prefix)
      - --kind symbol + any producer flag (--producer-kind, --topic-prefix)
      - (and similarly for route + non-route flags, etc.)
    """
    if args.kind is None:
        return False, None
    explicit = args.kind
    route_flags = args.http_method or args.path_prefix
    client_flags = args.client_kind or args.calls_service or args.calls_path_prefix
    producer_flags = args.producer_kind or args.topic_prefix
    if explicit == "symbol" and (route_flags or client_flags or producer_flags):
        return True, "--kind symbol conflicts with domain flags (route/client/producer flags require matching --kind)"
    if explicit == "route" and (client_flags or producer_flags):
        return True, "--kind route conflicts with client/producer flags"
    if explicit == "client" and (route_flags or producer_flags):
        return True, "--kind client conflicts with route/producer flags"
    if explicit == "producer" and (route_flags or client_flags):
        return True, "--kind producer conflicts with route/client flags"
    return False, None


def _cmd_find(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    cfg = _resolve_cfg(args)
    try:
        graph = _load_graph(cfg)
    except (_IndexNotFound, _IndexStale) as exc:
        env = Envelope(status="error", message=str(exc))
        print(render(env, fmt=args.format))
        return 2

    # Check kind contradiction first (before any backend work)
    inferred = _infer_kind(args)
    is_contradiction, error_msg = _check_kind_contradiction(args, inferred)
    if is_contradiction:
        env = Envelope(status="error", message=error_msg or "kind contradiction")
        print(render(env, fmt=args.format))
        return 2

    # Cap at 499 so limit+1 <= 500 (backend clamp)
    # If args.limit is None, default to 20 (from argparse)
    raw_limit = args.limit if args.limit is not None else 20
    limit = min(raw_limit, 499)

    # Query mode: positional <query> present
    if args.query:
        # find_by_name_or_fqn is Symbol-only (MATCH (s:Symbol) WHERE s.name=$needle
        # OR s.fqn=$needle). A positional <query> with a non-symbol kind (explicit
        # OR inferred from --http-method/--client-kind/--producer-kind/etc.) is a
        # usage contract violation -> status: error envelope (NOT argparse exit),
        # telling the user to drop the positional and use filter mode.
        effective_kind = inferred or "symbol"
        if effective_kind != "symbol":
            env = Envelope(
                status="error",
                message=(
                    f"query mode (positional <query>) only searches Symbols, but kind "
                    f"'{effective_kind}' was {'inferred from domain flags' if args.kind is None else 'set via --kind'}. "
                    "Drop the positional <query> and use filter mode (the domain flags) "
                    "for route/client/producer searches."
                ),
            )
            print(render(env, fmt=args.format))
            return 2
        return _cmd_find_query_mode(args, cfg, graph, limit)

    # Filter mode: build NodeFilter and call find_v2
    return _cmd_find_filter_mode(args, cfg, graph, inferred or "symbol", limit)


def _cmd_find_query_mode(
    args: argparse.Namespace,
    cfg,
    graph,
    limit: int,
) -> int:
    """Find query mode: g.find_by_name_or_fqn (Symbol-only, exact name/FQN match).

    ``find_by_name_or_fqn`` runs ``MATCH (s:Symbol) WHERE s.name=$needle OR
    s.fqn=$needle`` — Symbol-only, exact-only. There is no fuzzy/prefix/contains
    path; ``--fuzzy`` was deferred (see plans/active/PLAN-JRAG-CLI.md Out of
    scope). Query mode is gated to ``effective_kind == "symbol"`` upstream in
    ``_cmd_find``, so the only ``kinds`` filter we may pass is symbol sub-kinds
    derived from ``--java-kind``.
    """
    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook, normalize_enum
    from java_codebase_rag.jrag_render import render

    query = args.query

    # find_by_name_or_fqn is always Symbol; the only valid kinds filter is the
    # symbol sub-kind derived from --java-kind (lowercase, matching s.kind).
    # route/client/producer kinds were removed: they would never match Symbols.
    if args.java_kind:
        java_kind_norm = normalize_enum(args.java_kind, kind="java_kind")
        kinds = [java_kind_norm.lower()]
    else:
        kinds = None

    # Call find_by_name_or_fqn (exact name OR fqn match).
    rows = graph.find_by_name_or_fqn(
        query,
        kinds=kinds,
        module=args.module,
        microservice=args.service,
        limit=limit + 1,  # +1 for truncated detection
    )

    # Post-filter by role/annotation/capability (SymbolHit carries these).
    if args.role:
        role_norm = normalize_enum(args.role, kind="role")
        rows = [r for r in rows if (r.role or "").upper().replace("-", "_") == role_norm.upper()]
    if args.exclude_role:
        exclude_role_norm = normalize_enum(args.exclude_role, kind="role")
        rows = [r for r in rows if (r.role or "").upper().replace("-", "_") != exclude_role_norm.upper()]
    if args.annotation:
        rows = [r for r in rows if args.annotation in (r.annotations or [])]
    if args.capability:
        rows = [r for r in rows if args.capability in (r.capabilities or [])]

    # Build warnings for filters that cannot apply in query mode. SymbolHit
    # carries no framework/source_layer fields; rather than silently dropping
    # the user's filter, surface a warning so they know to switch to filter mode.
    warnings: list[str] = []
    if args.framework:
        warnings.append(
            "--framework ignored in query mode (applies to routes/clients/producers; use filter mode)"
        )
    if args.source_layer:
        warnings.append(
            "--source-layer ignored in query mode (applies to routes; use filter mode)"
        )

    # Convert SymbolHit rows to NodeRef-like dicts for the envelope.
    nodes = {}
    for row in rows:
        node_id = row.id
        nodes[node_id] = {
            "id": node_id,
            "kind": "symbol",
            "fqn": row.fqn,
            "name": row.name,
            "symbol_kind": row.kind,
            "microservice": row.microservice,
            "module": row.module,
            "role": row.role,
        }

    # mark_truncated operates on a list; envelope.nodes is a dict keyed by id.
    # Round-trip dict -> list -> truncate -> dict to apply the +1-fetch drop
    # (the truncated flag is computed off the list length, which equals the
    # dict size, so this is sound).
    node_list = list(nodes.values())
    display_nodes_list, truncated = mark_truncated(node_list, limit)
    display_nodes = {node["id"]: node for node in display_nodes_list}

    env = Envelope(status="ok", nodes=display_nodes, truncated=truncated, warnings=warnings)
    next_actions_hook(env)

    # Offset is not supported in query mode (find_by_name_or_fqn has no offset).
    print(render(env, fmt=args.format, noun="symbol"))
    return 0


def _cmd_find_filter_mode(
    args: argparse.Namespace,
    cfg,
    graph,
    kind: str,
    limit: int,
) -> int:
    """Find filter mode: build NodeFilter and call find_v2."""
    import mcp_v2

    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook, normalize_enum, to_envelope_rows
    from java_codebase_rag.jrag_render import render

    NodeFilter = mcp_v2.NodeFilter

    # Build NodeFilter from args
    filter_dict: dict = {}
    if args.service:
        filter_dict["microservice"] = args.service
    if args.module:
        filter_dict["module"] = args.module
    if args.role:
        filter_dict["role"] = normalize_enum(args.role, kind="role")
    if args.exclude_role:
        filter_dict["exclude_roles"] = [normalize_enum(args.exclude_role, kind="role")]
    if args.annotation:
        filter_dict["annotation"] = args.annotation
    if args.capability:
        filter_dict["capability"] = args.capability
    if args.fqn_prefix:
        filter_dict["fqn_prefix"] = args.fqn_prefix
    if args.java_kind:
        filter_dict["symbol_kind"] = normalize_enum(args.java_kind, kind="java_kind")
    if args.framework:
        filter_dict["framework"] = normalize_enum(args.framework, kind="framework")
    if args.source_layer:
        filter_dict["source_layer"] = normalize_enum(args.source_layer, kind="source_layer")
    if args.http_method:
        filter_dict["http_method"] = args.http_method.upper()
    if args.path_prefix:
        filter_dict["path_prefix"] = args.path_prefix
    if args.client_kind:
        filter_dict["client_kind"] = normalize_enum(args.client_kind, kind="client_kind")
    if args.calls_service:
        filter_dict["target_service"] = args.calls_service
    if args.calls_path_prefix:
        filter_dict["target_path_prefix"] = args.calls_path_prefix
    if args.producer_kind:
        filter_dict["producer_kind"] = normalize_enum(args.producer_kind, kind="producer_kind")
    if args.topic_prefix:
        filter_dict["topic_prefix"] = args.topic_prefix

    node_filter = NodeFilter.model_validate(filter_dict) if filter_dict else NodeFilter()

    # Call find_v2
    out = mcp_v2.find_v2(
        kind=kind,
        filter=node_filter,
        limit=limit + 1,  # +1 for has_more_results detection
        offset=args.offset,
        graph=graph,
    )

    if not out.success:
        env = Envelope(status="error", message=out.message)
        print(render(env, fmt=args.format))
        return 2

    # Convert results to envelope rows
    nodes_dict = {ref.id: to_envelope_rows([ref])[0] for ref in out.results}
    truncated = out.has_more_results or False

    env = Envelope(status="ok", nodes=nodes_dict, truncated=truncated)
    next_actions_hook(env)

    # Render with offset hint if truncated
    next_offset = args.offset + limit if truncated else None
    print(render(env, fmt=args.format, noun=kind, next_offset=next_offset))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    import mcp_v2

    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook, resolve_query
    from java_codebase_rag.jrag_render import render

    cfg = _resolve_cfg(args)
    try:
        graph = _load_graph(cfg)
    except (_IndexNotFound, _IndexStale) as exc:
        env = Envelope(status="error", message=str(exc))
        print(render(env, fmt=args.format))
        return 2

    # Resolve the query
    node, env = resolve_query(
        args.query,
        hint_kind=args.kind,
        java_kind=args.java_kind,
        role=args.role,
        fqn_prefix=args.fqn_prefix,
        cfg=cfg,
        graph=graph,
    )

    if env.status != "ok":
        print(render(env, fmt=args.format))
        return 2 if env.status == "error" else 0

    # Node resolved successfully - call describe_v2
    desc_out = mcp_v2.describe_v2(id=node.id, graph=graph)

    if not desc_out.success or desc_out.record is None:
        env = Envelope(status="error", message=desc_out.message or "describe failed")
        print(render(env, fmt=args.format))
        return 2

    # Convert NodeRecord to envelope format
    record_dict = desc_out.record.model_dump()
    node_id = record_dict.get("id") or node.id
    env = Envelope(
        status="ok",
        nodes={node_id: record_dict},
        root=node_id,
        file_location=env.file_location,  # Preserve file_location from resolve
    )
    next_actions_hook(env, root=node_id, edge_summary=record_dict.get("edge_summary"))

    # Render with inspect shape
    print(render(env, fmt=args.format, shape="inspect"))
    return 0


def _cmd_routes(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import normalize_enum

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    # Normalize framework if provided
    framework = normalize_enum(args.framework, kind="framework") if args.framework else None

    rows = graph.list_routes(
        microservice=args.service,
        framework=framework,
        path_prefix=args.path_prefix,
        method=args.method,
        limit=limit + 1,  # +1 for truncated detection
    )
    return _render_listing(rows, limit=limit, args=args, noun="route")


def _cmd_clients(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import normalize_enum

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    # Normalize client_kind via lookup table (feign → feign_method, etc.)
    client_kind = normalize_enum(args.client_kind, kind="client_kind") if args.client_kind else None

    rows = graph.list_clients(
        microservice=args.service,
        client_kind=client_kind,
        target_service=args.calls_service,
        path_prefix=args.path_prefix,
        limit=limit + 1,  # +1 for truncated detection
    )
    return _render_listing(rows, limit=limit, args=args, noun="client")


def _cmd_producers(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import normalize_enum

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    # Normalize producer_kind via lookup table (kafka → kafka_send, etc.)
    producer_kind = normalize_enum(args.producer_kind, kind="producer_kind") if args.producer_kind else None

    rows = graph.list_producers(
        microservice=args.service,
        producer_kind=producer_kind,
        topic_prefix=args.topic_prefix,
        limit=limit + 1,  # +1 for truncated detection
    )
    return _render_listing(rows, limit=limit, args=args, noun="producer")


def _cmd_topics(args: argparse.Namespace) -> int:
    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook
    from java_codebase_rag.jrag_render import render

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    # Scope producers by --producer-in if provided (else --service push-down).
    producer_microservice = args.producer_in or args.service

    # Call list_producers to get producers (grouped by topic)
    rows = graph.list_producers(
        microservice=producer_microservice,
        topic_prefix=args.topic_prefix,
        limit=limit + 1,  # +1 for truncated detection
    )

    # Group by topic name. Track no-topic producers so they surface as a
    # warning (distinguishable from "no producers at all").
    topics_dict: dict[str, dict] = {}
    no_topic_count = 0
    for producer in rows:
        topic = producer.get("topic") or ""
        if not topic:
            no_topic_count += 1
            continue
        if topic not in topics_dict:
            topics_dict[topic] = {
                "topic": topic,
                "producers": [],
                "broker": producer.get("broker") or "",
            }
        topics_dict[topic]["producers"].append(producer)

    warnings: list[str] = []
    if no_topic_count:
        warnings.append(
            f"{no_topic_count} producer(s) had no topic and were excluded"
        )

    # If --consumer-in is provided, resolve consumers for each topic group.
    # A consumer of a topic IS a listener: the edge path is
    #   listener_class -[:DECLARES]-> listener_method -[:EXPOSES]-> Route(topic)
    # (ASYNC_CALLS run Producer -> Route per java_ontology.py:415-416, so the
    # inbound-ASYNC_CALLS traversal the original PR shipped returned empty on
    # every graph — corrected here to use the EXPOSES-based resolver shared
    # with `listeners --topic-prefix`.)
    if args.consumer_in and topics_dict:
        for topic_name, topic_group in topics_dict.items():
            consumers = _resolve_topic_consumers(
                graph,
                topic=topic_name,
                microservice=args.consumer_in,
                prefix=False,  # exact match on the producer's topic literal
            )
            if consumers:
                topic_group["consumers"] = consumers

    # Convert to list and apply truncation
    topic_list = list(topics_dict.values())
    display_topics_list, truncated = mark_truncated(topic_list, limit)

    # Build envelope with topic nodes
    nodes = {}
    for i, topic in enumerate(display_topics_list):
        node_id = f"topic:{i}"
        nodes[node_id] = topic

    env = Envelope(status="ok", nodes=nodes, truncated=truncated, warnings=warnings)
    next_actions_hook(env)
    print(render(env, fmt=args.format, noun="topic"))
    return 0


def _cmd_jobs(args: argparse.Namespace) -> int:
    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    symbol_hits = graph.list_by_capability(
        capability="SCHEDULED_TASK",
        module=args.module,
        microservice=args.service,
        limit=limit + 1,  # +1 for truncated detection
    )
    rows = [_symbol_hit_to_dict(h) for h in symbol_hits]
    return _render_listing(rows, limit=limit, args=args, noun="symbol")


def _resolve_topic_consumers(
    graph,
    *,
    topic: str,
    microservice: str | None = None,
    prefix: bool = False,
) -> list[dict]:
    """Resolve listener classes that consume a topic via EXPOSES on Route.

    The graph models the listener→topic edge path as:
        listener_class -[:DECLARES]-> listener_method -[:EXPOSES]-> Route(topic)

    This is the correct consumer-resolution path for async messaging topics:
    ``ASYNC_CALLS`` run ``Producer → Route`` (java_ontology.py:415-416), so
    there is no inbound ``ASYNC_CALLS`` edge into Producer nodes to traverse
    via ``neighbors_v2(direction="in")``. The ``Route.topic`` property is not
    projected onto the ``NodeRef`` returned by ``neighbors_v2``, so a
    single-purpose Cypher lookup is used here — the same pattern as
    ``jrag_envelope._node_file_location`` (``graph._rows`` for a focused
    property fetch). This is a CLI-layer compose query, not a reimplementation
    of backend traversal logic.

    Args:
        topic: Topic string to match (exact unless ``prefix=True``).
        microservice: Optional microservice filter on the listener class.
        prefix: If True, match topic as a prefix (``STARTS WITH``);
            if False (default), exact equality.

    Returns:
        List of consumer dicts (``id``, ``fqn``, ``kind``, ``microservice``).
    """
    if not topic:
        return []
    match_clause = "r.topic STARTS WITH $topic" if prefix else "r.topic = $topic"
    params: dict = {"topic": topic}
    ms_clause = ""
    if microservice:
        ms_clause = " AND cls.microservice = $ms"
        params["ms"] = microservice
    rows = graph._rows(  # noqa: SLF001 - focused property lookup (same as _node_file_location)
        f"MATCH (cls:Symbol)-[:DECLARES]->(mth:Symbol)-[:EXPOSES]->(r:Route) "
        f"WHERE {match_clause}{ms_clause} "
        f"RETURN DISTINCT cls.id AS cid, cls.fqn AS cfqn, cls.microservice AS cms",
        params,
    )
    return [
        {
            "id": str(r.get("cid") or ""),
            "fqn": str(r.get("cfqn") or ""),
            "kind": "symbol",
            "microservice": str(r.get("cms") or ""),
        }
        for r in rows
        if r.get("cid")
    ]


def _listener_ids_for_topic_prefix(graph, listener_ids: list[str], prefix: str) -> set[str]:
    """Resolve which listener classes consume a topic with the given prefix.

    Thin wrapper over :func:`_resolve_topic_consumers` intersected with the
    pre-fetched ``listener_ids`` (from ``list_by_capability``). Retained as a
    separate function so ``_cmd_listeners`` can narrow the SymbolHit list in
    place (the capability fetch carries SymbolHit fields the resolver does not
    project). See ``_resolve_topic_consumers`` for the edge-model rationale.
    """
    if not listener_ids or not prefix:
        return set(listener_ids)
    consumers = _resolve_topic_consumers(graph, topic=prefix, prefix=True)
    matching = {c["id"] for c in consumers}
    return {lid for lid in listener_ids if lid in matching}


def _cmd_listeners(args: argparse.Namespace) -> int:
    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    symbol_hits = graph.list_by_capability(
        capability="MESSAGE_LISTENER",
        module=args.module,
        microservice=args.service,
        limit=_CONSUMER_FETCH_LIMIT,  # generous pre-filter fetch; truncation applies after
    )

    # --topic-prefix: narrow to listeners consuming a topic with that prefix.
    # The listener class itself carries no topic; its listener method EXPOSES
    # a Route whose ``topic`` property holds the consumed topic name (resolved
    # or as a constant reference). See _listener_ids_for_topic_prefix.
    if args.topic_prefix and symbol_hits:
        matching_ids = _listener_ids_for_topic_prefix(
            graph, [h.id for h in symbol_hits], args.topic_prefix
        )
        symbol_hits = [h for h in symbol_hits if h.id in matching_ids]

    # Apply the user-facing limit + 1 truncation AFTER the topic filter.
    capped = symbol_hits[: limit + 1]
    rows = [_symbol_hit_to_dict(h) for h in capped]
    return _render_listing(rows, limit=limit, args=args, noun="symbol")


def _cmd_entities(args: argparse.Namespace) -> int:
    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    symbol_hits = graph.list_by_role(
        role="ENTITY",
        module=args.module,
        microservice=args.service,
        limit=limit + 1,  # +1 for truncated detection
    )
    rows = [_symbol_hit_to_dict(h) for h in symbol_hits]
    return _render_listing(rows, limit=limit, args=args, noun="symbol")


def main(argv: list[str] | None = None) -> int:
    """Process-level entry. Returns the exit code.

    First line raises the FD soft limit (lancedb merge-insert opens many
    handles; macOS IDE-launched soft limit is 256). Returns 0 on ok, 1 on
    usage error (argparse rejects argv), 2 on handler exception. The top-level
    exception handler emits a ``status: error`` envelope to stdout AND
    ``traceback.format_exc()`` to stderr before returning 2 - this is a
    deliberate divergence from the operator CLI which swallows tracebacks.
    """
    raise_fd_limit()
    parser = build_parser()
    raw = list(argv if argv is not None else sys.argv[1:])
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        # argparse with exit_on_error=False raises SystemExit on -h/--help
        # (code 0) and ArgumentError-propagated paths. Treat 0/None as ok and
        # any other code as usage error (exit 1).
        if exc.code in (0, None):
            return 0
        return 1
    except argparse.ArgumentError as exc:
        # exit_on_error=False routes argparse usage errors here. We deliberately
        # surface them on stderr (no envelope to stdout) and exit 1 - the agent
        # gets a clear "usage error" signal distinct from internal failures (2).
        print(f"jrag: {exc}", file=sys.stderr)
        return 1
    handler = getattr(args, "handler", None)
    if handler is None:
        # No subcommand: print help to stderr, return usage error.
        parser.print_help(sys.stderr)
        return 1
    try:
        return int(handler(args))
    except Exception as exc:
        from java_codebase_rag.jrag_envelope import Envelope
        from java_codebase_rag.jrag_render import render

        env = Envelope(
            status="error",
            message=f"internal error: {exc}",
        )
        print(render(env, fmt=getattr(args, "format", "text")))
        print(traceback.format_exc(), file=sys.stderr)
        return 2


def _console_script_main() -> None:
    """Real CLI entry: terminate without interpreter finalization.

    Mirrors ``java_codebase_rag.cli._console_script_main``: a pyarrow/lance
    worker thread (loaded via lancedb in lifecycle commands) can outlive CPython
    finalization in a one-shot CLI subprocess and trip ``PyGILState_Release``
    (SIGABRT, exit -6). Flushing + ``os._exit`` skips that racy teardown - the
    command has already done its work and emitted its result. ``main()`` stays
    return-based so in-process test callers keep working.
    """
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    _console_script_main()
