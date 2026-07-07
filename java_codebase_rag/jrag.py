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
from java_codebase_rag._stdio import force_utf8_stdio
from java_codebase_rag._version import version_string

__all__ = ["build_parser", "main", "_console_script_main"]


class _IndexNotFound(RuntimeError):
    """Raised when no LadybugDB graph exists at the resolved path."""


class _IndexStale(RuntimeError):
    """Raised when the on-disk graph's ontology is older than required."""


# Generous limit for the topics --consumer-in / listeners --topic-contains
# compose fetches (these resolve cross-topic edges and should not silently
# truncate the listener/consumer set under typical fixture sizes).
_CONSUMER_FETCH_LIMIT = 200


# Framework tag -> the type-level annotations a Symbol's declaring type carries
# when it participates in that framework. The graph stores `framework` only on
# Route nodes (Route.framework = spring_mvc | webflux | kafka | ...); Symbol
# nodes have no framework field, so `search --framework <name>` (a symbol result
# set) maps the framework back onto the declaring type via these annotations and
# post-filters. Mirrors the indexer's own classification heuristic.
_FRAMEWORK_ANNOTATIONS: dict[str, frozenset[str]] = {
    "spring_mvc": frozenset({
        "RestController", "Controller", "RestControllerAdvice", "RequestMapping",
        "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping",
    }),
    "webflux": frozenset({
        "RestController", "Controller", "RequestMapping",
    }),
    "kafka": frozenset({"EnableKafka", "KafkaStreams", "KafkaStream"}),
    "rabbitmq": frozenset({"EnableRabbit", "RabbitListener"}),
    "jms": frozenset({"EnableJms", "JmsListener"}),
    "stream": frozenset({"EnableBinding", "StreamBridge", "EnableStream"}),
    "feign": frozenset({"FeignClient", "EnableFeignClients"}),
}


def _framework_type_fqns(graph, framework: str) -> set[str]:
    """Return the set of type-level Symbol FQNs whose annotations match the
    given framework tag (per :data:`_FRAMEWORK_ANNOTATIONS`).

    One focused Cypher lookup, cached implicitly per-process (the CLI is
    short-lived). Used by ``search --framework`` as a post-filter on the
    primary-type FQN each SearchHit carries.
    """
    anns = _FRAMEWORK_ANNOTATIONS.get(framework)
    if not anns:
        return set()
    # Ladybug has no parameterized list membership; expand the fixed annotation
    # set as ORed ``list_contains`` predicates (same pattern as trace_flow's
    # capability expansion).
    predicates = " OR ".join(f"list_contains(s.annotations, '{a}')" for a in anns)
    rows = graph._rows(  # noqa: SLF001 - focused lookup (same pattern as _resolve_topic_consumers)
        f"MATCH (s:Symbol) WHERE s.kind IN ['class','interface','annotation'] "
        f"AND ({predicates}) RETURN DISTINCT s.fqn AS fqn",
        {},
    )
    return {str(r.get("fqn") or "") for r in rows if r.get("fqn")}


def _apply_auto_scope(args: argparse.Namespace, cfg, graph) -> None:
    """Default ``args.service`` to the microservice implied by cwd (MCP parity).

    Mirrors ``server.py`` ``ScopeManager``: when cwd sits inside one
    microservice of a system-level index, behave as if the agent had typed
    ``--service <that microservice>`` so the other services' results do not
    leak in. No-op unless the command opted in via
    ``set_defaults(auto_scope=True)`` and the caller did not pass ``--service``.

    Detection reuses ``graph_enrich.detect_microservice_from_path``; the
    candidate is validated against ``graph.microservice_counts()`` and dropped
    if absent (a mislabeled non-microservice dir would otherwise yield zero
    matches). When the known set is empty/unreadable we KEEP the candidate
    (transient graph error) — same as ``server.py:130-132``. Detection returns
    ``None`` at the system root or outside it, so auto-scope never fires for
    estate-wide work.

    Records ``args._service_user`` (caller passed ``--service``) for warning
    distinction and ``args._service_auto`` (detected name) for the
    transparency notice.

    NOTE: three commands inline their graph load and bypass
    ``_load_graph_or_error`` — ``find``, ``inspect``, ``status``. ``find`` is
    opted in and calls this helper itself; ``inspect``/``status`` are NOT
    opted in (they don't use ``--service`` as a result filter today). If
    either is ever opted in, it must call this helper in its own load path.
    """
    # ``--service`` lives on the ``_common_parser``; a few commands (status,
    # microservices) use a bare ``_core_parser`` without it. Gate on opt-in
    # FIRST so those never reach the ``args.service`` read below.
    if not getattr(args, "auto_scope", False):
        return
    args._service_user = getattr(args, "service", None) is not None
    if getattr(args, "service", None) is not None:  # explicit --service wins
        return
    if getattr(args, "no_auto_scope", False) or os.environ.get("JRAG_NO_AUTO_SCOPE"):
        return
    source_root = cfg.source_root if cfg.source_root else None
    if not source_root:
        return
    from graph_enrich import detect_microservice_from_path

    candidate = detect_microservice_from_path(Path.cwd(), Path(source_root))
    if not candidate:
        return
    try:
        known = {name for name in (graph.microservice_counts() or {}) if name}
    except Exception:
        known = set()
    if known and candidate not in known:
        return
    args.service = candidate
    args._service_auto = candidate
    print(f"[jrag] auto-scope: --service {candidate} (cwd)", file=sys.stderr)


def _auto_scope_notice(args: argparse.Namespace) -> list[str]:
    """Envelope ``warnings[]`` line telling the agent results are auto-scoped.

    Models often do not see stderr (where the ``[jrag] auto-scope`` line goes),
    so this also surfaces the scope in the rendered output. Returns ``[]`` when
    auto-scope did not fire (no detected service) or the command opted out.
    """
    svc = getattr(args, "_service_auto", None)
    if not svc:
        return []
    return [
        f"auto-scope: --service {svc} (inferred from cwd; "
        f"pass --no-auto-scope to disable)"
    ]


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
        print(render(env, fmt=args.format, detail=args.detail))
        return cfg, None, 2
    # Default --service from cwd before the handler reads it (MCP parity).
    # No-op unless the command opted in via set_defaults(auto_scope=True).
    _apply_auto_scope(args, cfg, graph)
    return cfg, graph, 0


def _clamped_limit(args: argparse.Namespace) -> int:
    """Return the limit clamped so ``limit+1 <= 500`` (backend clamp)."""
    raw_limit = args.limit if args.limit is not None else 20
    return min(raw_limit, 499)


def _render_listing(rows, *, limit: int, args: argparse.Namespace, noun: str,
                    extra_hints: list[str] | None = None) -> int:
    """Apply +1-fetch truncation, build the envelope, render as a listing.

    Shared by the listing commands whose backend returns a flat row list
    (routes / clients / producers). ``rows`` must already be the limit+1
    fetch. Renders as the default shape (no ``shape=``).

    ``extra_hints`` are merged into ``agent_next_actions`` AFTER the
    edge/breadcrumb-derived hints (deduped, capped at 5). Used by listings
    whose rows map to a natural ``jrag inspect <fqn>`` drill-down
    (jobs / listeners / entities).
    """
    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook, to_envelope_rows
    from java_codebase_rag.jrag_render import render

    node_list = to_envelope_rows(rows) if rows and not isinstance(rows[0], dict) else list(rows)
    display_nodes_list, truncated = mark_truncated(node_list, limit)
    display_nodes = {node["id"]: node for node in display_nodes_list}

    env = Envelope(
        status="ok", nodes=display_nodes, truncated=truncated,
        warnings=_auto_scope_notice(args),
    )
    next_actions_hook(env, command=getattr(args, "command", None))
    if extra_hints:
        seen = set(env.agent_next_actions)
        for h in extra_hints:
            if h and h not in seen:
                seen.add(h)
                env.agent_next_actions.append(h)
        env.agent_next_actions = env.agent_next_actions[:5]
    print(render(env, fmt=args.format, detail=args.detail, noun=noun))
    return 0


def _symbol_hit_to_dict(hit) -> dict:
    """Convert a ``SymbolHit`` (dataclass) to the envelope node dict shape.

    Carries the FULL ``SymbolHit``: ``filename`` / ``start_line`` so the
    projector can compose the ``file`` field at ``--detail normal``, and
    ``signature`` / ``annotations`` / ``capabilities`` / ``modifiers`` /
    ``package`` / ``parent_id`` / ``resolved`` so ``--detail full`` is genuinely
    rich. The projector (:func:`jrag_envelope.project_node`) trims per detail
    level at render time — callers build rich and let the seam trim, inverting
    the old "trim at construction" that coupled detail to format. Empty values
    are dropped by the projector, so carrying them here is harmless. Byte
    offsets (``start_byte`` / ``end_byte``) are intentionally dropped — pure
    noise, never a display field.
    """
    return {
        "id": hit.id,
        "kind": "symbol",
        "fqn": hit.fqn,
        "name": hit.name,
        "symbol_kind": hit.kind,
        "microservice": hit.microservice,
        "module": hit.module,
        "role": hit.role,
        "filename": hit.filename,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "signature": hit.signature,
        "annotations": list(hit.annotations or []),
        "capabilities": list(hit.capabilities or []),
        "modifiers": list(hit.modifiers or []),
        "package": hit.package,
        "parent_id": hit.parent_id,
        "resolved": hit.resolved,
    }


class _EnvelopeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser subclass that routes ``error()`` to a raised exception.

    Stock argparse ``error()`` prints ``usage:`` to stderr and calls SystemExit
    — a raw, non-envelope shape that ignores ``--format json``. With
    ``exit_on_error=False`` the base class raises :class:`argparse.ArgumentError`
    instead, but STILL prints the usage text first. This override suppresses the
    usage dump so :func:`main` can emit a clean ``status: error`` envelope
    honoring ``--format`` (consistent with not_found / missing-index errors).
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        raise argparse.ArgumentError(None, message)


_PREPARSE_PARSER = argparse.ArgumentParser(add_help=False)
_PREPARSE_PARSER.add_argument("--format", default=None)
_PREPARSE_PARSER.add_argument("--detail", default=None)


def _preparse_render_flags(raw: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Extract ``--format`` / ``--detail`` from raw argv via a minimal parser.

    Used by :func:`main` to honor render flags when argparse bailed before
    populating ``args`` (missing required positional, unknown subcommand).
    Returns ``(format, detail, leftover_argv)`` where ``leftover_argv`` has the
    consumed flag tokens stripped so the first remaining non-dash token is the
    subcommand name (not a flag value like the ``json`` in ``--format json``).
    """
    try:
        ns, leftover = _PREPARSE_PARSER.parse_known_args(raw)
        return ns.format, ns.detail, list(leftover)
    except Exception:
        return None, None, list(raw)


def build_parser() -> argparse.ArgumentParser:
    """Argparse builder. Imports no backend modules.

    ``--offset`` is intentionally NOT a global flag (PR-JRAG-1a contract): it
    is added only to ``find`` / ``search`` subparsers in PR-JRAG-1b / PR-JRAG-4
    (those commands route through ``find_v2`` / ``search_v2`` which take an
    ``offset``). In 1a, no subparser has ``--offset``.
    """
    description = (
        "jrag - agent-facing CLI for graph-native code intelligence.\n\n"
        "Every <query> command resolves the identifier (FQN / simple name /\n"
        "route path / topic) as the first step and maps one/many/none onto a\n"
        "single envelope. Default output is compact text; `--format json` emits\n"
        "the envelope verbatim.\n\n"
        "Commands by group:\n"
        "  health:      status\n"
        "  locate:      find, inspect\n"
        "  listings:    routes, clients, producers, topics, jobs, listeners,\n"
        "               entities\n"
        "  traversal:   callers, callees, hierarchy, implementations, subclasses,\n"
        "               overrides, overridden-by, dependents, impact, decompose,\n"
        "               flow, dependencies, connection, outline, imports\n"
        "  orientation: microservices, map, conventions, overview\n"
        "  search:      search\n\n"
        "Run `jrag <command> --help` for command-specific options."
    )
    parser = _EnvelopeArgumentParser(
        prog="jrag",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_string(parser.prog),
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=_EnvelopeArgumentParser)

    # Common flags applied per command via parents=[_common_parser()]. NOT
    # global so commands can override defaults (e.g. fan-out commands use
    # limit=10). The helper builds a FRESH parser each call so every subparser
    # owns its own --detail Action object — argparse `parents` shares Action
    # objects by reference, and `set_defaults(detail=...)` mutates the shared
    # action's default (CPython walks `self._actions`), so a single shared
    # `common` made `status.set_defaults(detail="full")` poison every other
    # subparser into defaulting to "full". A fresh parser per subparser isolates
    # the override to the command that asked for it.
    def _common_parser() -> argparse.ArgumentParser:
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--service", type=str, default=None, help="Filter by microservice.")
        common.add_argument("--module", type=str, default=None, help="Filter by module.")
        common.add_argument(
            "--no-auto-scope",
            dest="no_auto_scope",
            action="store_true",
            default=False,
            help=(
                "Disable cwd-derived auto --service scoping so cross-service "
                "results are visible (also disabled via JRAG_NO_AUTO_SCOPE=1)."
            ),
        )
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
            "--detail",
            choices=("brief", "normal", "full"),
            default="normal",
            help=(
                "Output detail level (default normal) — ORTHOGONAL to --format: both "
                "text and json honor it. brief = identity only (name @service); "
                "normal = +module/role/file/score; full = +signature/annotations/snippet."
            ),
        )
        return common

    # Core-only parser for AGGREGATE commands (status / microservices) that have
    # no per-row filtering surface. Excludes --service / --module / --limit so
    # the surface is honest: those flags are REJECTED at parse time (clean
    # error envelope) rather than accepted-then-warned-as-no-op. Keeps
    # --index-dir / --format / --detail.
    def _core_parser() -> argparse.ArgumentParser:
        core = argparse.ArgumentParser(add_help=False)
        core.add_argument(
            "--index-dir",
            type=str,
            default=None,
            dest="index_dir",
            help="Index directory override (default: discovered from cwd).",
        )
        core.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format (default: text).",
        )
        core.add_argument(
            "--detail",
            choices=("brief", "normal", "full"),
            default="normal",
            help=(
                "Output detail level (default normal) — ORTHOGONAL to --format: both "
                "text and json honor it. brief = identity only (name @service); "
                "normal = +module/role/file/score; full = +signature/annotations/snippet."
            ),
        )
        return core

    status = subparsers.add_parser(
        "status",
        help="Print index freshness, ontology version, and counts.",
        parents=[_core_parser()],
        description=(
            "Index health and freshness. Reports ontology version, source root, "
            "built_at, parse_errors, edge counts, and the counts dictionary from "
            "GraphMeta. Exits 2 with an actionable envelope if the index is "
            "missing or stale. An aggregate view: --service / --module / --limit "
            "are NOT accepted (rejected at parse time)."
        ),
    )
    status.set_defaults(handler=_cmd_status, detail="full")

    # find subparser (PR-JRAG-1b)
    find = subparsers.add_parser(
        "find",
        help="Find nodes by query or filter.",
        parents=[_common_parser()],
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
    find.add_argument("--fqn-contains", type=str, default=None, help="Filter by FQN substring.")
    find.add_argument("--http-method", type=str, default=None, help="Filter by HTTP method (route).")
    find.add_argument("--path-contains", type=str, default=None, help="Filter by path substring (route).")
    find.add_argument("--client-kind", type=str, default=None, help="Filter by client kind (client).")
    find.add_argument("--calls-service", type=str, default=None, help="Filter by target service (client).")
    find.add_argument("--calls-path-contains", type=str, default=None, help="Filter by target path substring (client).")
    find.add_argument("--producer-kind", type=str, default=None, help="Filter by producer kind (producer).")
    find.add_argument("--topic-contains", type=str, default=None, help="Filter by topic substring (producer).")
    find.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Page offset (filter mode only; ignored in query mode).",
    )
    find.set_defaults(handler=_cmd_find, auto_scope=True)

    # inspect subparser (PR-JRAG-1b)
    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a node by query.",
        parents=[_common_parser()],
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
    inspect.add_argument("--fqn-contains", type=str, default=None, help="Post-filter by FQN substring.")
    inspect.set_defaults(handler=_cmd_inspect, detail="full")

    # http-routes subparser (PR-JRAG-2)
    http_routes = subparsers.add_parser(
        "http-routes",
        help="List HTTP routes.",
        parents=[_common_parser()],
        description=(
            "List HTTP routes by microservice, framework, path substring, or method. "
            "Returns route nodes (no resolve step). HTTP-server-route surface only — "
            "kafka topics live under `topics`."
        ),
    )
    http_routes.add_argument("--framework", type=str, default=None, help="Filter by framework.")
    http_routes.add_argument("--path-contains", type=str, default=None, help="Filter by path substring.")
    http_routes.add_argument("--method", type=str, default=None, help="Filter by HTTP method.")
    http_routes.set_defaults(handler=_cmd_routes, detail="full", auto_scope=True)

    # http-clients subparser (PR-JRAG-2)
    http_clients = subparsers.add_parser(
        "http-clients",
        help="List HTTP clients.",
        parents=[_common_parser()],
        description=(
            "List HTTP clients by microservice, client kind, target service, or path substring. "
            "Returns client nodes (no resolve step)."
        ),
    )
    http_clients.add_argument("--client-kind", type=str, default=None, help="Filter by client kind.")
    http_clients.add_argument("--calls-service", type=str, default=None, help="Filter by target service.")
    http_clients.add_argument("--path-contains", type=str, default=None, help="Filter by path substring.")
    http_clients.set_defaults(handler=_cmd_clients, detail="full", auto_scope=True)

    # producers subparser (PR-JRAG-2)
    producers = subparsers.add_parser(
        "producers",
        help="List async message producers.",
        parents=[_common_parser()],
        description=(
            "List async message producers by microservice, producer kind, or topic substring. "
            "Returns producer nodes (no resolve step)."
        ),
    )
    producers.add_argument("--producer-kind", type=str, default=None, help="Filter by producer kind.")
    producers.add_argument("--topic-contains", type=str, default=None, help="Filter by topic substring.")
    producers.set_defaults(handler=_cmd_producers, detail="full", auto_scope=True)

    # topics subparser (PR-JRAG-2)
    topics = subparsers.add_parser(
        "topics",
        help="List message topics (producer-grouped).",
        parents=[_common_parser()],
        description=(
            "List message topics grouped by producer. "
            "No :Topic node exists; this command groups producers by topic name. "
            "--consumer-in resolves consumers (listener methods) via EXPOSES edges to Route(topic)."
        ),
    )
    topics.add_argument("--topic-contains", type=str, default=None, help="Filter by topic substring.")
    topics.add_argument("--producer-in", type=str, default=None, help="Scope producers to this microservice.")
    topics.add_argument("--consumer-in", type=str, default=None, help="Show consumers from this microservice.")
    topics.set_defaults(handler=_cmd_topics, detail="full", auto_scope=True)

    # jobs subparser (PR-JRAG-2)
    jobs = subparsers.add_parser(
        "jobs",
        help="List scheduled tasks.",
        parents=[_common_parser()],
        description=(
            "List scheduled task symbols (capability=SCHEDULED_TASK). "
            "Returns Symbol nodes with the SCHEDULED_TASK capability."
        ),
    )
    jobs.set_defaults(handler=_cmd_jobs, detail="full", auto_scope=True)

    # listeners subparser (PR-JRAG-2)
    listeners = subparsers.add_parser(
        "listeners",
        help="List message listeners.",
        parents=[_common_parser()],
        description=(
            "List message listener symbols (capability=MESSAGE_LISTENER). "
            "Returns Symbol nodes with the MESSAGE_LISTENER capability."
        ),
    )
    listeners.add_argument("--topic-contains", type=str, default=None, help="Filter by topic substring (on producer member).")
    listeners.set_defaults(handler=_cmd_listeners, detail="full", auto_scope=True)

    # entities subparser (PR-JRAG-2)
    entities = subparsers.add_parser(
        "entities",
        help="List JPA entities.",
        parents=[_common_parser()],
        description=(
            "List JPA entity symbols (role=ENTITY). "
            "Returns Symbol nodes with the ENTITY role."
        ),
    )
    entities.set_defaults(handler=_cmd_entities, detail="full", auto_scope=True)

    # ---- Traversal commands (PR-JRAG-3a) ----
    # Shared resolve-disambiguation flags (PR-JRAG-1a contract: only --kind is a
    # true resolve input; the rest are client-side post-filters on resolve's
    # candidate set). Traversals are resolve-first; --offset is NOT registered
    # on any traversal subparser (none of the backends take offset).
    resolve_parent = argparse.ArgumentParser(add_help=False)
    resolve_parent.add_argument(
        "--kind",
        choices=("symbol", "route", "client", "producer"),
        default=None,
        help="Hint for resolve (omit for broad search).",
    )
    resolve_parent.add_argument("--java-kind", type=str, default=None, help="Post-filter by Java symbol kind.")
    resolve_parent.add_argument("--role", type=str, default=None, help="Post-filter by role.")
    resolve_parent.add_argument("--fqn-contains", type=str, default=None, help="Post-filter by FQN substring.")

    callers = subparsers.add_parser(
        "callers",
        help="Who calls this symbol or route?",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> then traverse the call graph inbound (who calls me?). "
            "Symbol -> g.find_callers (CALLS edges, --service/--module pushed down). "
            "Route -> g.find_route_callers; route callers are cross-service by "
            "construction, so --service narrows WHICH route resolves (a resolve-time "
            "filter) rather than filtering the resulting callers. "
            "--include-external controls whether external (JDK/Spring/Lombok) callers "
            "are excluded (default: excluded)."
        ),
    )
    callers.add_argument("query", help="Symbol FQN/name (e.g. 'pkg.Svc#method(Arg)') or route path.")
    callers.add_argument("--depth", type=int, default=1, help="Call-graph depth (default 1).")
    callers.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        help="Minimum CALLS edge confidence in [0.0, 1.0].",
    )
    callers.add_argument(
        "--include-external",
        action="store_true",
        help="Include external (JDK/Spring/Lombok) callers/callees (default excluded).",
    )
    callers.set_defaults(handler=_cmd_callers, auto_scope=True)

    callees = subparsers.add_parser(
        "callees",
        help="What does this symbol call?",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (Symbol) then traverse the call graph outbound (what do I "
            "call?). Calls g.find_callees; --include-external is symmetric with callers."
        ),
    )
    callees.add_argument("query", help="Symbol FQN/name (e.g. 'pkg.Svc#method(Arg)').")
    callees.add_argument("--depth", type=int, default=1, help="Call-graph depth (default 1).")
    callees.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        help="Minimum CALLS edge confidence in [0.0, 1.0].",
    )
    callees.add_argument(
        "--include-external",
        action="store_true",
        help="Include external (JDK/Spring/Lombok) callees (default excluded).",
    )
    callees.set_defaults(handler=_cmd_callees, auto_scope=True)

    hierarchy = subparsers.add_parser(
        "hierarchy",
        help="Type hierarchy (parents and children).",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (type Symbol) then walk EXTENDS/IMPLEMENTS both directions: "
            "out = supertypes (parents), in = subtypes (children). No --service/--module "
            "push-down (structural edges)."
        ),
    )
    hierarchy.add_argument("query", help="Class/interface FQN or name.")
    hierarchy.set_defaults(handler=_cmd_hierarchy)

    implementations = subparsers.add_parser(
        "implementations",
        help="Classes implementing an interface.",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (interface Symbol) then call g.find_implementors. "
            "--service/--module pushed down; --capability pushed down to the backend "
            "(find_implementors accepts a capability filter)."
        ),
    )
    implementations.add_argument("query", help="Interface FQN or name.")
    implementations.add_argument("--capability", type=str, default=None, help="Filter implementors by capability.")
    implementations.set_defaults(handler=_cmd_implementations, auto_scope=True)

    subclasses = subparsers.add_parser(
        "subclasses",
        help="Classes extending a type.",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (class Symbol) then call g.find_subclasses (EXTENDS inbound). "
            "--service/--module pushed down."
        ),
    )
    subclasses.add_argument("query", help="Class FQN or name.")
    subclasses.set_defaults(handler=_cmd_subclasses, auto_scope=True)

    overrides = subparsers.add_parser(
        "overrides",
        help="Methods this method overrides (dispatch UP to declaration).",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (method Symbol) then neighbors_v2([id], 'out', ['OVERRIDES']). "
            "The stored OVERRIDES edge runs overrider -> declaration (subtype method -> "
            "supertype declared method), so 'out' dispatches UP the hierarchy."
        ),
    )
    overrides.add_argument("query", help="Method FQN or name (e.g. 'pkg.Impl#method(Arg)').")
    overrides.set_defaults(handler=_cmd_overrides)

    overridden_by = subparsers.add_parser(
        "overridden-by",
        help="Methods overriding this one (dispatch DOWN to overriders).",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (method Symbol) then neighbors_v2([id], 'in', ['OVERRIDES']) "
            "(= virtual OVERRIDDEN_BY out). 'in' traverses the stored OVERRIDES edge "
            "backward, dispatching DOWN from declaration to overriders."
        ),
    )
    overridden_by.add_argument("query", help="Method FQN or name (e.g. 'pkg.Iface#method(Arg)').")
    overridden_by.set_defaults(handler=_cmd_overridden_by)

    dependents = subparsers.add_parser(
        "dependents",
        help="Who injects this type?",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (type Symbol) then call g.find_injectors (INJECTS inbound: "
            "classes that inject this type). --service/--module pushed down."
        ),
    )
    dependents.add_argument("query", help="Type FQN or name.")
    dependents.set_defaults(handler=_cmd_dependents, auto_scope=True)

    impact = subparsers.add_parser(
        "impact",
        help="Fleet-wide blast radius (INJECTS/IMPLEMENTS/EXTENDS reverse closure).",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> then call g.impact_analysis (reverse closure over "
            "INJECTS+IMPLEMENTS+EXTENDS: who breaks if this changes). --service is a "
            "CLIENT-SIDE post-filter (impact_analysis has no microservice param); "
            "surfaced as a warnings[] entry."
        ),
    )
    impact.add_argument("query", help="Symbol FQN or name.")
    impact.add_argument("--depth", type=int, default=2, help="Closure depth (default 2).")
    impact.set_defaults(handler=_cmd_impact, auto_scope=True)

    decompose = subparsers.add_parser(
        "decompose",
        help="Role-waterfall flow from an entrypoint.",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (entrypoint Symbol) then call g.trace_flow. Walks "
            "CONTROLLER -> SERVICE/COMPONENT -> CLIENT/REPOSITORY/MAPPER stages via "
            "INJECTS+EXTENDS+IMPLEMENTS (optionally + CALLS hops). --service/--module "
            "pushed down; --depth clamped to 1..3."
        ),
    )
    decompose.add_argument("query", help="Entrypoint symbol FQN or name.")
    decompose.add_argument("--depth", type=int, default=2, help="Neighbour hop count per stage (clamped 1..3, default 2).")
    decompose.add_argument(
        "--follow-calls",
        action="store_true",
        dest="follow_calls",
        help="Follow DECLARES+CALLS type-to-type hops to top up each stage.",
    )
    decompose.add_argument(
        "--max-stage",
        type=int,
        default=20,
        dest="max_stage",
        help="Cap on symbols per stage (stage_limit, default 20).",
    )
    decompose.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        help="Min CALLS confidence when --follow-calls is on.",
    )
    decompose.add_argument(
        "--include-external",
        action="store_true",
        help="Include external types reached via the CALLS hop (default excluded).",
    )
    decompose.set_defaults(handler=_cmd_decompose, auto_scope=True)

    flow = subparsers.add_parser(
        "flow",
        help="Request flow through a route (inbound callers + outbound CALLS hops).",
        parents=[_common_parser()],
        description=(
            "Resolve <query> to a Route then call g.trace_request_flow. Inbound = "
            "cross-service HTTP/async callers (Client/Producer two-hop); outbound = "
            "CALLS hops from the route handler. Intra-service is an INDEX-TIME data "
            "property: CALLS edges are intra-codebase by construction, and the query "
            "carries no microservice predicate, so the result reflects whatever the "
            "fixture indexed (no query-time constraint). --depth clamped to 1..8."
        ),
    )
    flow.add_argument(
        "query",
        help=(
            "Route path (e.g. '/chat/assign') or Kafka topic name (e.g. "
            "'banking.chat.compliance.review'). Resolved with hint_kind=route; "
            "kafka_topic Routes match on topic."
        ),
    )
    # Primary flag is --depth (consistent with callers/callees/impact/decompose).
    # --max-hops is kept as a hidden back-compat alias (same dest).
    flow.add_argument(
        "--depth", type=int, default=5, dest="depth",
        help="Max CALLS hops (clamped 1..8, default 5).",
    )
    flow.add_argument(
        "--max-hops", type=int, dest="depth",
        default=argparse.SUPPRESS, help=argparse.SUPPRESS,
    )
    flow.set_defaults(handler=_cmd_flow)

    # ---- Compose traversals + file inspection (PR-JRAG-3b) ----
    # callees (Client/Producer variant) re-uses the existing _cmd_callees
    # handler from PR-JRAG-3a; the help text below updates to advertise the
    # Client/Producer dispatch (Symbol path is unchanged). --kind picks the
    # resolve hint; the handler dispatches on the resolved node's kind.
    #
    # (The callees subparser was registered above with the Symbol-only help
    # text; we patch its description here to advertise the new variant without
    # duplicating the parser construction.)
    callees.epilog = (
        "Symbol root lists the methods this code calls (CALLS out). Client and\n"
        "Producer roots follow their call edge to the Route they target:\n"
        "  Client root   -> the :Route it requests (HTTP_CALLS out)\n"
        "  Producer root -> the :Route (kafka_topic) it publishes to (ASYNC_CALLS out)\n"
        "--include-external applies to the Symbol path; Client/Producer edges are\n"
        "structural (Client/Producer -> :Route) and have no external-exclusion analog."
    )

    dependencies = subparsers.add_parser(
        "dependencies",
        help="Types this Symbol injects (INJECTS out).",
        parents=[_common_parser(), resolve_parent],
        description=(
            "Resolve <query> (type Symbol) then neighbors_v2([id], 'out', ['INJECTS']) "
            "= the types this class injects (its direct dependencies). INJECTS is "
            "Symbol -> Symbol (declaring type -> injected type), so 'out' traverses "
            "from the injector to its dependencies. --service/--module are NOT "
            "applied (INJECTS is a structural edge with no microservice predicate); "
            "they surface as warnings[]. --include-external is accepted for surface "
            "symmetry with callers/callees but is a warned no-op here (INJECTS has "
            "no external-exclusion analog at the neighbors_v2 layer)."
        ),
    )
    dependencies.add_argument("query", help="Symbol FQN or name (e.g. 'pkg.Svc').")
    dependencies.add_argument(
        "--include-external",
        action="store_true",
        help="Accepted for symmetry; warned no-op on dependencies (INJECTS is structural).",
    )
    dependencies.set_defaults(handler=_cmd_dependencies)

    connection = subparsers.add_parser(
        "connection",
        help="Cross-service connections for a microservice (inbound/outbound).",
        parents=[_common_parser()],
        description=(
            "RESOLVE-FIRST EXCEPTION: the first positional is a microservice NAME "
            "(e.g. 'chat-core'), NOT a query — it is passed literally to list_clients/"
            "list_producers/find_route_callers; resolve_v2 is NEVER run on it.\n\n"
            "Direction (default --both): clients/producers in OTHER services "
            "targeting this service. HTTP via list_clients(target_service=<svc>) + "
            "async via find_route_callers on this service's topic Routes.\n"
            "--outbound: clients/producers IN this service. HTTP via "
            "list_clients(microservice=<svc>) + producers via "
            "list_producers(microservice=<svc>).\n"
            "--both: render both inbound and outbound sections.\n\n"
            "--http-method and --calls-service filter HTTP callers only (clients "
            "have a target_service; producers do not). Producers are KEPT under "
            "--calls-service so the async channel stays visible; a warnings[] entry "
            "is emitted when --calls-service bypasses producers."
        ),
    )
    connection.add_argument(
        "microservice",
        help="Microservice NAME (literal — NOT resolved as a query).",
    )
    connection.add_argument(
        "--inbound",
        dest="direction",
        action="store_const",
        const="inbound",
        default=None,
        help="Show only inbound connections (default is --both).",
    )
    connection.add_argument(
        "--outbound",
        dest="direction",
        action="store_const",
        const="outbound",
        help="Show only outbound connections (default is --both).",
    )
    connection.add_argument(
        "--both",
        dest="direction",
        action="store_const",
        const="both",
        help="Show both inbound and outbound sections (this is the default).",
    )
    connection.add_argument(
        "--http-method",
        type=str,
        default=None,
        help="Filter HTTP callers by method (e.g. POST). Applies to clients only.",
    )
    connection.add_argument(
        "--calls-service",
        type=str,
        default=None,
        help=(
            "Narrow to edges involving this other service. Outbound: clients with "
            "target_service == <svc> (producers kept with a warning — no service "
            "target on ASYNC channels). Inbound: callers from microservice == <svc>."
        ),
    )
    connection.set_defaults(handler=_cmd_connection)

    outline = subparsers.add_parser(
        "outline",
        help="List symbols declared in a file.",
        parents=[_common_parser()],
        description=(
            "List all Symbol nodes whose declared location is in <file>. Calls "
            "find_symbols_in_file_range(graph, filename=<file>, start_line=1, "
            "end_line=2**31-1) — the start_line=1 is required (the backend returns "
            "[] for start_line<1). --limit caps the entry count (the file's "
            "symbol table is otherwise unbounded); truncated is set when more "
            "entries exist. --offset is rejected (the backend takes no offset)."
        ),
    )
    outline.add_argument("file", help="File path as stored in the graph (POSIX-relative to source root).")
    outline.set_defaults(handler=_cmd_outline)

    imports = subparsers.add_parser(
        "imports",
        help="List imports declared in a file (tree-sitter parse + resolve_v2).",
        parents=[_common_parser()],
        description=(
            "Parse <file> with tree-sitter (ast_java.parse_java), walk its "
            "import_declaration nodes, and resolve each imported FQN via resolve_v2 "
            "against the graph. Returns one node per import: resolved graph Symbol "
            "when resolve_v2 hits, or an unresolved placeholder carrying the raw FQN "
            "otherwise. Static and wildcard imports are included (marked in the row)."
            " --offset is rejected."
        ),
    )
    imports.add_argument("file", help="File path (POSIX-relative to source root, or absolute).")
    imports.set_defaults(handler=_cmd_imports)

    # ---- Orientation commands (PR-JRAG-4) ----
    microservices = subparsers.add_parser(
        "microservices",
        help="List microservices with resolved type counts.",
        parents=[_core_parser()],
        description=(
            "List every microservice with its resolved type-symbol count. "
            "Calls g.microservice_counts(). Renders as a counts listing. "
            "An aggregate view: --service / --module / --limit are NOT accepted "
            "(rejected at parse time)."
        ),
    )
    microservices.set_defaults(handler=_cmd_microservices, detail="full")

    map_cmd = subparsers.add_parser(
        "map",
        help="Symbol counts per kind, grouped by service or module.",
        parents=[_common_parser()],
        description=(
            "Count resolved type Symbols (class/interface/enum/record/annotation) "
            "grouped by microservice or module. --by {microservice,module} selects "
            "the grouping axis (default microservice); --service / --module narrow "
            "the count to one service or module (filters, independent of --by)."
        ),
    )
    map_cmd.add_argument(
        "--by",
        dest="by",
        choices=("microservice", "module"),
        default=None,
        help="Grouping axis: microservice (default) or module. When --module is "
        "set without --by, the axis defaults to module (the user's focus is the "
        "module axis); pass --by microservice to keep microservice grouping.",
    )
    map_cmd.set_defaults(handler=_cmd_map, detail="full")

    conventions = subparsers.add_parser(
        "conventions",
        help="Dominant roles + framework tallies.",
        parents=[_common_parser()],
        description=(
            "Report the dominant roles among resolved Symbols and the route framework "
            "distribution. --service narrows the role tally to one microservice."
        ),
    )
    conventions.set_defaults(handler=_cmd_conventions, detail="full")

    overview = subparsers.add_parser(
        "overview",
        help="Bundle for a microservice, route, or topic.",
        parents=[_common_parser()],
        description=(
            "Dispatch on the positional <subject>:\n"
            "  Route path (starts with '/')  -> trace_request_flow (same as `flow`).\n"
            "  Microservice name             -> routes + clients + producers bundle.\n"
            "  Topic string                  -> producers + consumers for the topic.\n"
            "--as {microservice,route,topic} overrides auto-detection.\n"
            "Auto-detection: starts with '/' -> route; matches a known microservice -> "
            "microservice; otherwise -> topic."
        ),
    )
    overview.add_argument(
        "subject",
        nargs="?",
        default=None,
        help="Microservice name, route path (starts with '/'), or topic string.",
    )
    overview.add_argument(
        "--as",
        dest="as_type",
        choices=("microservice", "route", "topic"),
        default=None,
        help="Override auto-detection of subject type.",
    )
    overview.set_defaults(handler=_cmd_overview, detail="full")

    # ---- Search command (PR-JRAG-4) ----
    search = subparsers.add_parser(
        "search",
        help="Semantic search over Lance tables.",
        parents=[_common_parser()],
        description=(
            "Semantic search via search_v2 over the Lance index (java/sql/yaml tables). "
            "--table all searches all three. --hybrid enables vector+keyword hybrid. "
            "--offset paginates. --path-contains narrows by file path substring. "
            "Filters (NodeFilter flags) narrow results.\n\n"
            "--fuzzy is accepted but rejected IN-HANDLER with status: error (search is "
            "inherently semantic; --fuzzy is a no-op synonym). Registering the flag "
            "prevents argparse from exiting 2 before the handler can produce the envelope."
        ),
    )
    search.add_argument("query", help="Natural-language search query.")
    search.add_argument(
        "--table",
        choices=("java", "sql", "yaml", "all"),
        default="java",
        help="Lance table to search (default: java; all = java+sql+yaml).",
    )
    search.add_argument(
        "--hybrid", action="store_true", help="Enable vector+keyword hybrid search."
    )
    search.add_argument(
        "--explain", action="store_true", help="Show score breakdown per hit."
    )
    search.add_argument(
        "--path-contains", type=str, default=None, dest="path_contains",
        help="Narrow to chunks whose filename contains this substring.",
    )
    search.add_argument(
        "--fuzzy", action="store_true",
        help="Accepted but rejected in-handler (search is semantic; --fuzzy is implicit).",
    )
    search.add_argument(
        "--min-score", type=float, default=0.0, dest="min_score",
        help=(
            "Drop hits with a relevance score below this floor. Default 0.0 drops "
            "negative-score noise (chunks farther than orthogonal to the query); "
            "raise to tighten precision."
        ),
    )
    # NodeFilter flags (same set as `find` filter mode, minus the query-only ones).
    search.add_argument("--role", type=str, default=None, help="Filter by role.")
    search.add_argument("--exclude-role", type=str, default=None, dest="exclude_role", help="Exclude by role.")
    search.add_argument("--java-kind", type=str, default=None, dest="java_kind", help="Filter by Java symbol kind.")
    search.add_argument("--annotation", type=str, default=None, help="Filter by annotation.")
    search.add_argument("--capability", type=str, default=None, help="Filter by capability.")
    search.add_argument("--framework", type=str, default=None, help="Filter by framework.")
    search.add_argument("--fqn-contains", type=str, default=None, dest="fqn_contains", help="Filter by FQN substring.")
    search.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Page offset (passed to search_v2; paginated via +1-fetch).",
    )
    search.add_argument(
        "--chunks",
        action="store_true",
        help="Show every chunk (default collapses to one row per symbol/type).",
    )
    search.set_defaults(handler=_cmd_search, auto_scope=True)

    return parser


def _resolve_cfg(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Resolve operator config (reuses the operator's cocoindex-free resolver).

    Mirrors ``java_codebase_rag.cli._resolved_from_ns``: pass ``source_root=None``
    so ``resolve_operator_config`` honors ``JAVA_CODEBASE_RAG_SOURCE_ROOT`` first,
    then a YAML ``source_root`` field, then walks up from cwd to find a project
    root. Passing a discovered root explicitly here would OVERRIDE a set env var
    whenever any ancestor dir has a ``.java-codebase-rag`` marker — silently
    ignoring the documented subprocess source-root mechanism that
    ``pipeline.subprocess_env`` sets for the cocoindex child (and that operators
    set directly).

    When the anchor is an index dir with no YAML beside it, resolution follows
    that index's ``config_source`` pointer (see ``config._effective_config_dir``)
    so a config living in a sibling dir is still found from inside a microservice.
    Applies CLI ``--index-dir`` if given and calls ``apply_to_os_environ`` so
    downstream modules see a consistent env (critically SBERT_MODEL for ``jrag
    search``).
    """
    from java_codebase_rag.config import resolve_operator_config

    cfg = resolve_operator_config(
        source_root=None,
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
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    meta = graph.meta()
    if "error" in meta:
        env = Envelope(
            status="error",
            message=f"Index meta read failed: {meta['error']}",
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    counts = meta.get("counts") or {}
    edge_counts = meta.get("edge_counts") or {}
    # Single notional "index" node carrying kv fields + nested counts/edges
    # as top-level dict-valued fields. The renderer's inspect-shape dispatch
    # fires on ANY dict-typed value (structural signal, not name-based), so
    # ``counts`` / ``edges`` render as indented alphabetical sections without
    # abusing ``edge_summary`` (which is reserved for PR-JRAG-3 real edge
    # data). See jrag_render._render_inspect / _render_text_shape.
    # --service / --module / --limit are rejected at the argparse layer
    # (status uses _core_parser), so no no-op warning is needed here.
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
    print(render(env, fmt=args.format, detail=args.detail, noun="status", shape="inspect"))
    return 0


def _infer_kind(args: argparse.Namespace) -> str | None:
    """Infer kind from domain flags when --kind is omitted.

    Inference rules (PR-JRAG-1b):
      - --http-method or --path-contains → route
      - --client-kind or --calls-service or --calls-path-contains → client
      - --producer-kind or --topic-contains → producer
      - else → symbol (default)
    Returns None if no flags are set (symbol default in callers).
    """
    if args.kind is not None:
        return args.kind
    if args.http_method or args.path_contains:
        return "route"
    if args.client_kind or args.calls_service or args.calls_path_contains:
        return "client"
    if args.producer_kind or args.topic_contains:
        return "producer"
    return "symbol"


def _check_kind_contradiction(args: argparse.Namespace, inferred: str | None) -> tuple[bool, str | None]:
    """Check if domain flags contradict explicit --kind.

    Returns (is_contradiction, error_message). Contradiction pairs:
      - --kind symbol + any route flag (--http-method, --path-contains)
      - --kind symbol + any client flag (--client-kind, --calls-service, --calls-path-contains)
      - --kind symbol + any producer flag (--producer-kind, --topic-contains)
      - (and similarly for route + non-route flags, etc.)
    """
    if args.kind is None:
        return False, None
    explicit = args.kind
    route_flags = args.http_method or args.path_contains
    client_flags = args.client_kind or args.calls_service or args.calls_path_contains
    producer_flags = args.producer_kind or args.topic_contains
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
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # find inlines its graph load (not via _load_graph_or_error), so wire the
    # auto-scope default here too (MCP parity).
    _apply_auto_scope(args, cfg, graph)

    # Check kind contradiction first (before any backend work)
    inferred = _infer_kind(args)
    is_contradiction, error_msg = _check_kind_contradiction(args, inferred)
    if is_contradiction:
        env = Envelope(status="error", message=error_msg or "kind contradiction")
        print(render(env, fmt=args.format, detail=args.detail))
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
            print(render(env, fmt=args.format, detail=args.detail))
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
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook, normalize_enum
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
    # Truncation is decided by the RAW name/FQN fetch (limit+1), BEFORE
    # post-filters reduce the set — otherwise a post-filter that drops rows
    # would silently clear `truncated` even though more name matches may exist
    # beyond the fetch (silent wrong-results).
    raw_truncated = len(rows) > limit

    # Post-filter by role/annotation/capability (SymbolHit carries these).
    post_filter_active = False
    if args.role:
        post_filter_active = True
        role_norm = normalize_enum(args.role, kind="role")
        rows = [r for r in rows if (r.role or "").upper().replace("-", "_") == role_norm.upper()]
    if args.exclude_role:
        post_filter_active = True
        exclude_role_norm = normalize_enum(args.exclude_role, kind="role")
        rows = [r for r in rows if (r.role or "").upper().replace("-", "_") != exclude_role_norm.upper()]
    if args.annotation:
        post_filter_active = True
        rows = [r for r in rows if args.annotation in (r.annotations or [])]
    if args.capability:
        post_filter_active = True
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
    # When post-filters apply after a capped fetch, `truncated` reflects the
    # pre-filter name-match count and cannot know whether MORE filtered matches
    # exist beyond the fetch — surface that honestly.
    if raw_truncated and post_filter_active:
        warnings.append(
            "results truncated before --role/--annotation/--capability filters; "
            "additional filtered matches may exist beyond the fetch"
        )

    # Display at most `limit` of the (post-filtered) rows.
    display_rows = rows[:limit]
    nodes = {}
    for row in display_rows:
        node_id = row.id
        # Carry the full SymbolHit field set (signature/annotations/modifiers/
        # package/raw location columns). The projector trims to the requested
        # detail level (signature/annotations/... appear only at ``full``),
        # so populating them here is what makes ``find <fqn> --detail full``
        # honor the contract (jrag_render keeps signature/annotations at full).
        # Without this, find --detail full showed only identity+classification
        # because the node never carried the content fields.
        nodes[node_id] = {
            "id": node_id,
            "kind": "symbol",
            "fqn": row.fqn,
            "name": row.name,
            "symbol_kind": row.kind,
            "microservice": row.microservice,
            "module": row.module,
            "role": row.role,
            "package": row.package,
            "signature": row.signature,
            "annotations": list(row.annotations or []),
            "capabilities": list(row.capabilities or []),
            "modifiers": list(row.modifiers or []),
            "filename": row.filename,
            "start_line": row.start_line,
            "end_line": row.end_line,
        }

    env = Envelope(
        status="ok", nodes=nodes, truncated=raw_truncated,
        warnings=warnings + _auto_scope_notice(args),
    )
    next_actions_hook(env)

    # Empty-result discoverability: query mode is exact-match only (name OR fqn),
    # so a partial like `find ChatManagement` legitimately returns 0. Surface a
    # cross-ref so the agent knows the substring fallback exists instead of
    # seeing a bare `0 symbol`. Carried as both a `message` (renders inline) and
    # an `agent_next_action` (renders as `next:` / JSON). A literal FQN-shaped
    # query (contains '.') almost certainly won't substring-match either, so the
    # hint applies regardless of shape.
    if not nodes and query:
        hint = f"no exact match for {query!r} — try `jrag find --fqn-contains {query}` for substring"
        env.message = hint
        env.agent_next_actions = [f"jrag find --fqn-contains {query}"]

    # Offset is not supported in query mode (find_by_name_or_fqn has no offset).
    print(render(env, fmt=args.format, detail=args.detail, noun="symbol"))
    return 0


def _build_node_filter_or_error(filter_dict: dict):
    """Build a ``NodeFilter`` from ``filter_dict``; on pydantic validation
    failure return ``(None, error_envelope)`` so the caller can render a clean
    ``status: error`` envelope instead of letting the ValidationError propagate
    to the top-level handler (which renders "internal error" + a traceback).

    A bad enum (e.g. ``--role FOO``) should be a user-facing validation error,
    not an internal crash. Returns ``(node_filter, None)`` on success.
    """
    import mcp_v2

    from java_codebase_rag.jrag_envelope import Envelope
    from pydantic import ValidationError

    try:
        nf = mcp_v2.NodeFilter.model_validate(filter_dict) if filter_dict else mcp_v2.NodeFilter()
        return nf, None
    except ValidationError as exc:
        parts: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []) if x != "")
            msg = str(err.get("msg") or "").strip()
            parts.append(f"{loc}: {msg}" if loc else msg)
        message = "; ".join(parts) if parts else str(exc)
        return None, Envelope(status="error", message=f"invalid filter: {message}")


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
    if args.fqn_contains:
        filter_dict["fqn_contains"] = args.fqn_contains
    if args.java_kind:
        filter_dict["symbol_kind"] = normalize_enum(args.java_kind, kind="java_kind")
    if args.framework:
        filter_dict["framework"] = normalize_enum(args.framework, kind="framework")
    if args.source_layer:
        filter_dict["source_layer"] = normalize_enum(args.source_layer, kind="source_layer")
    if args.http_method:
        filter_dict["http_method"] = args.http_method.upper()
    if args.path_contains:
        filter_dict["path_contains"] = args.path_contains
    if args.client_kind:
        filter_dict["client_kind"] = normalize_enum(args.client_kind, kind="client_kind")
    if args.calls_service:
        filter_dict["target_service"] = args.calls_service
    if args.calls_path_contains:
        filter_dict["target_path_contains"] = args.calls_path_contains
    if args.producer_kind:
        filter_dict["producer_kind"] = normalize_enum(args.producer_kind, kind="producer_kind")
    if args.topic_contains:
        filter_dict["topic_contains"] = args.topic_contains

    node_filter, err_env = _build_node_filter_or_error(filter_dict)
    if err_env is not None:
        print(render(err_env, fmt=args.format, detail=args.detail))
        return 2

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
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # Convert results to envelope rows. Slice to `limit`: find_v2 was called with
    # limit+1, so when exactly user_limit+1 matches exist `out.results` carries
    # one extra row that must be dropped (off-by-one guard). `truncated` is True
    # when the backend reports more OR the +1 row is present.
    results = list(out.results)
    truncated = bool(out.has_more_results) or len(results) > limit
    display_refs = results[:limit]
    nodes_dict = {ref.id: to_envelope_rows([ref])[0] for ref in display_refs}

    env = Envelope(
        status="ok", nodes=nodes_dict, truncated=truncated,
        warnings=_auto_scope_notice(args),
    )
    next_actions_hook(env)

    # Render with offset hint if truncated
    next_offset = args.offset + limit if truncated else None
    print(render(env, fmt=args.format, detail=args.detail, noun=kind, next_offset=next_offset))
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
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # Resolve the query. Forward --service/--module so an ambiguous name
    # (same name across services) disambiguates by microservice/module, the
    # same way find and the traversal commands do. Without this, inspect
    # silently ignored these inherited flags (resolve_query accepts them).
    node, env = resolve_query(
        args.query,
        hint_kind=args.kind,
        java_kind=args.java_kind,
        role=args.role,
        fqn_contains=args.fqn_contains,
        cfg=cfg,
        graph=graph,
        microservice=args.service or "",
        module=args.module or "",
    )

    if env.status != "ok":
        print(render(env, fmt=args.format, detail=args.detail))
        return 2 if env.status == "error" else 0

    # Node resolved successfully - call describe_v2
    desc_out = mcp_v2.describe_v2(id=node.id, graph=graph)

    if not desc_out.success or desc_out.record is None:
        env = Envelope(status="error", message=desc_out.message or "describe failed")
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # Convert NodeRecord to envelope format.
    #
    # NodeRecord nests the symbol's payload inside a ``data`` sub-dict (kind,
    # name, package, module, microservice, role, signature, annotations,
    # capabilities, modifiers, filename, start_line, ...). The envelope
    # projector's identity/classification keys (``_BRIEF_NODE_KEYS`` /
    # ``_NORMAL_NODE_KEYS``) live at the TOP level, so without flattening they
    # never reach the nested data and inspect renders only the outer kind/fqn
    # at every level (brief == normal, the bug). Flatten ``data`` to the top
    # level so:
    #   * brief picks up kind/fqn/name/microservice (identity).
    #   * normal additionally picks up module/role/symbol_kind/file
    #     (classification + location).
    #   * full additionally keeps signature/annotations/modifiers/package/
    #     capabilities/edge_summary (content).
    #
    # The outer ``kind`` is the node category ("symbol"); the inner
    # ``data.kind`` is the symbol sub-kind ("class"/"interface"/"method"/...),
    # renamed ``symbol_kind`` to match find/search/listings (which use
    # ``symbol_kind`` for the sub-kind and reserve ``kind`` for the category).
    record_dict = desc_out.record.model_dump()
    node_id = record_dict.get("id") or node.id
    data = record_dict.get("data") or {}
    flat: dict[str, Any] = {
        "kind": record_dict.get("kind") or "symbol",
        "fqn": record_dict.get("fqn") or data.get("fqn") or node.fqn,
    }
    # Promote inner data fields. Skip ``kind`` here — renamed to symbol_kind.
    for src_key, dest_key in (
        ("name", "name"),
        ("kind", "symbol_kind"),
        ("package", "package"),
        ("module", "module"),
        ("microservice", "microservice"),
        ("role", "role"),
        ("signature", "signature"),
        ("annotations", "annotations"),
        ("capabilities", "capabilities"),
        ("modifiers", "modifiers"),
        ("filename", "filename"),
        ("start_line", "start_line"),
        ("end_line", "end_line"),
    ):
        val = data.get(src_key)
        if val not in (None, "", [], {}):
            flat[dest_key] = val
    # edge_summary is a top-level field on NodeRecord (not inside data) — keep
    # it so --detail full renders it as a nested kv-block (brief/normal drop
    # it via the scalar allow-list since the inspect subject has identity and
    # so takes the strict-scalar projection branch).
    if record_dict.get("edge_summary"):
        flat["edge_summary"] = record_dict["edge_summary"]

    env = Envelope(
        status="ok",
        nodes={node_id: flat},
        root=node_id,
        file_location=env.file_location,  # Preserve file_location from resolve
    )
    next_actions_hook(env, root=node_id, edge_summary=record_dict.get("edge_summary"))

    # Render with inspect shape
    print(render(env, fmt=args.format, detail=args.detail, shape="inspect"))
    return 0


def _backfill_service_from_filename(row: dict) -> None:
    """Derive ``microservice`` / ``module`` from ``filename`` when empty.

    Kafka-topic Route nodes are created without ``microservice``/``module`` in
    the graph builder, so the routes listing rendered them with no ``@service``
    (or as blank lines when the topic was also empty). The filename carries the
    info reliably (``<microservice>/<module>/src/...`` or
    ``<microservice>/src/...``) — the same path-based resolution graph_enrich
    uses — so backfill from it for display without forcing a reindex.
    """
    fn = str(row.get("filename") or "").strip()
    if not fn:
        return
    parts = fn.split("/")
    if "src" not in parts:
        return
    idx = parts.index("src")
    if idx >= 1 and not (row.get("microservice") or "").strip():
        row["microservice"] = parts[0]
    if idx >= 2 and not (row.get("module") or "").strip():
        row["module"] = parts[1]


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
        path_contains=args.path_contains,
        method=args.method,
        limit=limit + 1,  # +1 for truncated detection
        # `http-routes` is the HTTP-server-route surface (external entrypoints
        # you'd run `callers` on): exclude kafka topics (→ `topics`) and client
        # http_endpoint mirrors (call-sites). Pinned to include_kafka=False —
        # the backend default (True) would re-admit kafka topics.
        server_exposed=True,
        include_kafka=False,
    )
    for row in rows:
        _backfill_service_from_filename(row)
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
        path_contains=args.path_contains,
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
        topic_contains=args.topic_contains,
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
        topic_contains=args.topic_contains,
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
    # list_producers has no module kwarg (only microservice/topic_contains); --module
    # would be silently dropped — surface it (use --producer-in to scope by svc).
    if getattr(args, "module", None):
        warnings.append(
            "--module is not applied on topics (list_producers has no module param; "
            "use --producer-in to scope producers by microservice)"
        )

    # If --consumer-in is provided, resolve consumers for each topic group.
    # A consumer of a topic IS a listener: the edge path is
    #   listener_class -[:DECLARES]-> listener_method -[:EXPOSES]-> Route(topic)
    # (ASYNC_CALLS run Producer -> Route per java_ontology.py:415-416, so the
    # inbound-ASYNC_CALLS traversal the original PR shipped returned empty on
    # every graph — corrected here to use the EXPOSES-based resolver shared
    # with `listeners --topic-contains`.)
    if args.consumer_in and topics_dict:
        for topic_name, topic_group in topics_dict.items():
            consumers = _resolve_topic_consumers(
                graph,
                topic=topic_name,
                microservice=args.consumer_in,
                contains=False,  # exact match on the producer's topic literal
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

    env = Envelope(
        status="ok", nodes=nodes, truncated=truncated,
        warnings=warnings + _auto_scope_notice(args),
    )
    next_actions_hook(env, command=getattr(args, "command", None))
    print(render(env, fmt=args.format, detail=args.detail, noun="topic"))
    return 0


def _inspect_hints_for_rows(rows: list[dict], *, limit: int = 2) -> list[str]:
    """Build ``jrag inspect <fqn>`` hints for the first ``limit`` rows that
    carry an FQN. Used by jobs/listeners/entities to surface a per-row
    drill-down (text renderer shows up to 2 as ``next:`` lines; JSON carries
    up to 5 — callers pass ``limit=2`` for the visible cap).
    """
    hints: list[str] = []
    for r in rows:
        fqn = r.get("fqn") if isinstance(r, dict) else getattr(r, "fqn", None)
        if fqn:
            hints.append(f"jrag inspect {fqn}")
        if len(hints) >= limit:
            break
    return hints


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
    # Per-row drill-down: the agent's natural next step on a job/listener/entity
    # is to inspect it (signature, edges, callers). Cap visible hints at 2 so
    # text output stays tight; the JSON cap (5) is applied in _render_listing.
    hints = _inspect_hints_for_rows(rows[:limit], limit=2)
    return _render_listing(rows, limit=limit, args=args, noun="symbol", extra_hints=hints)


def _resolve_topic_consumers(
    graph,
    *,
    topic: str,
    microservice: str | None = None,
    contains: bool = False,
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
        topic: Topic string to match (exact unless ``contains=True``).
        microservice: Optional microservice filter on the listener class.
        contains: If True, match topic as a substring (``CONTAINS``);
            if False (default), exact equality.

    Returns:
        List of consumer dicts (``id``, ``fqn``, ``kind``, ``microservice``).
    """
    if not topic:
        return []
    match_clause = "r.topic CONTAINS $topic" if contains else "r.topic = $topic"
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


def _listener_ids_for_topic_contains(graph, listener_ids: list[str], contains: str) -> set[str]:
    """Resolve which listener classes consume a topic containing the given substring.

    Thin wrapper over :func:`_resolve_topic_consumers` intersected with the
    pre-fetched ``listener_ids`` (from ``list_by_capability``). Retained as a
    separate function so ``_cmd_listeners`` can narrow the SymbolHit list in
    place (the capability fetch carries SymbolHit fields the resolver does not
    project). See ``_resolve_topic_consumers`` for the edge-model rationale.
    """
    if not listener_ids or not contains:
        return set(listener_ids)
    consumers = _resolve_topic_consumers(graph, topic=contains, contains=True)
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

    # --topic-contains: narrow to listeners consuming a topic containing that substring.
    # The listener class itself carries no topic; its listener method EXPOSES
    # a Route whose ``topic`` property holds the consumed topic name (resolved
    # or as a constant reference). See _listener_ids_for_topic_contains.
    if args.topic_contains and symbol_hits:
        matching_ids = _listener_ids_for_topic_contains(
            graph, [h.id for h in symbol_hits], args.topic_contains
        )
        symbol_hits = [h for h in symbol_hits if h.id in matching_ids]

    # Apply the user-facing limit + 1 truncation AFTER the topic filter.
    capped = symbol_hits[: limit + 1]
    rows = [_symbol_hit_to_dict(h) for h in capped]
    hints = _inspect_hints_for_rows(rows[:limit], limit=2)
    return _render_listing(rows, limit=limit, args=args, noun="symbol", extra_hints=hints)


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
    hints = _inspect_hints_for_rows(rows[:limit], limit=2)
    return _render_listing(rows, limit=limit, args=args, noun="symbol", extra_hints=hints)


# ============================================================================
# PR-JRAG-3a: traversal helpers + 11 traversal command handlers.
#
# Every traversal is resolve-first (resolve_query), then calls a LadybugGraph
# method (or neighbors_v2 for the override axis), then renders via the
# traversal shape (envelope.root + edge rows). --offset is NOT supported on
# any traversal subparser. --limit uses +1-fetch where the method takes a
# limit; client-side slice otherwise.
#
# Backend signatures verified against source (ladybug_queries.py / mcp_v2.py /
# java_ontology.py) at PR-JRAG-3a time. Adaptations from the brief:
#  * find_implementors / find_subclasses / find_injectors DO accept a
#    `capability` kwarg (the brief claimed they did not); --capability is
#    PUSHED DOWN on `implementations` (more efficient + matches the global
#    principle "pushed down where the method takes it").
#  * OVERRIDES edge direction confirmed: overrider -> declaration (subtype
#    method -> supertype method), so `out`=dispatch UP (overrides) and
#    `in`=dispatch DOWN (overridden-by). Brief was correct.
# ============================================================================


def _resolve_traversal_node(
    args: argparse.Namespace,
    *,
    cfg,
    graph,
    hint_kind,
    apply_scope: bool = False,
):
    """Resolve-first frame shared by every traversal command.

    Returns ``(node, env, rc)``. On resolve failure (ambiguous / not_found /
    error), renders the envelope and returns ``(None, env, rc)`` with rc=2 on
    error, 0 on ambiguous/not_found (matches the inspect command convention).

    ``apply_scope`` opts a command into pushing ``--service``/``--module`` down
    into resolve as resolve-time filters (via :func:`resolve_query`). Most
    traversal commands keep the default ``False`` to preserve their existing
    resolve semantics (structural-edge commands warn-and-ignore ``--service``;
    symbol traversals use ``--service`` as a result filter via find_callers/
    find_callees). ``callers`` opts in so ``--service`` narrows WHICH route
    resolves for the cross-service route-caller flow.
    """
    from java_codebase_rag.jrag_envelope import resolve_query
    from java_codebase_rag.jrag_render import render

    node, env = resolve_query(
        args.query,
        hint_kind=hint_kind,
        java_kind=getattr(args, "java_kind", None),
        role=getattr(args, "role", None),
        fqn_contains=getattr(args, "fqn_contains", None),
        cfg=cfg,
        graph=graph,
        microservice=(getattr(args, "service", None) or "") if apply_scope else "",
        module=(getattr(args, "module", None) or "") if apply_scope else "",
    )
    if env.status != "ok":
        print(render(env, fmt=args.format, detail=args.detail))
        return None, env, 2 if env.status == "error" else 0
    return node, env, 0


def _noderef_to_node_dict(ref) -> dict:
    """NodeRef (pydantic, from neighbors_v2 / resolve) -> envelope node dict."""
    return ref.model_dump()


def _dedupe_traversal_edges(edges: list[dict]) -> list[dict]:
    """Drop edges with an empty ``other_id`` and dedupe by ``(other_id, edge_type)``.

    ``find_callees`` can emit the same callee twice (a method reached via
    multiple call sites / strategies), and a CLIENT-role aggregation can surface
    a Client row whose id never resolved. Both produce noisy traversal rows:
    duplicates inflate the count, and an empty ``other_id`` becomes a phantom
    edge the id-free renderer cannot key. Keep the FIRST occurrence (results are
    confidence-sorted, so the first is the highest-confidence edge).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for e in edges:
        oid = e.get("other_id")
        if not oid:
            continue
        key = (str(oid), str(e.get("edge_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _emit_traversal(
    args: argparse.Namespace,
    *,
    root_id: str,
    nodes: dict[str, dict],
    edges: list[dict],
    noun: str,
    warnings: list[str] | None = None,
    truncated: bool = False,
    is_external_entrypoint: bool = False,
    extra_hints: list[str] | None = None,
) -> int:
    """Build the traversal envelope (root + nodes + edges) and render.

    The traversal shape requires ``envelope.root`` so the renderer uses the
    traversal shape (root + edge rows). ``next_offset`` is left None on every
    traversal (non-offset -> "truncated: more results - narrow your query").
    ``is_external_entrypoint`` flags a server-exposed route with zero in-repo
    callers so the renderer emits an honest "external entrypoint" note instead
    of a bare, bug-looking ``0 callers`` line.

    ``extra_hints`` are merged into ``agent_next_actions`` AFTER the
    edge-derived hints (deduped, capped at 5). Used by commands with a known
    cross-ref for an empty/edge case (e.g. ``subclasses <interface>`` ->
    ``jrag implementations <fqn>``).
    """
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    env = Envelope(
        status="ok",
        nodes=dict(nodes),
        edges=list(edges),
        root=root_id,
        warnings=(warnings or []) + _auto_scope_notice(args),
        truncated=truncated,
        is_external_entrypoint=is_external_entrypoint,
    )
    next_actions_hook(env, root=root_id, result_edges=edges, command=getattr(args, "command", None))
    if extra_hints:
        seen = set(env.agent_next_actions)
        for h in extra_hints:
            if h and h not in seen:
                seen.add(h)
                env.agent_next_actions.append(h)
        env.agent_next_actions = env.agent_next_actions[:5]
    print(render(env, fmt=args.format, detail=args.detail, noun=noun))
    return 0


def _require_kind(
    node,
    *,
    expected: str,
    kinds: tuple[str, ...],
    args: argparse.Namespace,
    hint: str = "",
    java_kinds: tuple[str, ...] | None = None,
    roles: tuple[str, ...] | None = None,
) -> int | None:
    """Kind guard shared by traversal handlers (DRY for the 11x guard block).

    Returns ``None`` when ``node.kind`` is in ``kinds`` (caller proceeds). On
    mismatch, prints a ``status: error`` envelope and returns 2. ``expected``
    is the human-readable root description (e.g. ``"overrides expects a method
    Symbol root"``); ``hint`` is an optional trailing suggestion (e.g. ``"Use
    --kind symbol to narrow resolve."``). Callers whose kind-dispatch is more
    complex (e.g. ``callers`` accepts Symbol OR Route and routes between them)
    keep an inline guard.

    ``java_kinds`` / ``roles`` add an OPTIONAL Java-level check applied AFTER
    the graph-label check passes. Graph ``kind=="symbol"`` covers class,
    interface, enum, AND method alike, so a label-only guard lets a class
    through a command that expects an interface (e.g. ``implementations``).
    When provided, ``node.symbol_kind`` must be in ``java_kinds`` (lowercased,
    dashes->underscores; e.g. ``("interface",)``) and ``node.role`` in
    ``roles`` (case-insensitive); otherwise a clear ``status: error`` is
    emitted instead of the silent empty result the backend returns.
    """
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    def _emit(msg: str) -> int:
        if hint:
            msg = f"{msg} {hint}"
        print(render(Envelope(status="error", message=msg), fmt=args.format, detail=args.detail))
        return 2

    if node.kind not in kinds:
        return _emit(f"{expected}; resolved kind is {node.kind!r}.")

    # Java-level guard (optional): symbol_kind / role on the resolved NodeRef.
    # symbol_kind is stored LOWERCASE (class/method/interface/...); normalize
    # both sides to lowercase + dashes->underscores before comparing.
    if java_kinds:
        actual = (node.symbol_kind or "").lower().replace("-", "_")
        want = tuple(k.lower().replace("-", "_") for k in java_kinds)
        if actual not in want:
            return _emit(
                f"{expected}; resolved Java kind is {node.symbol_kind!r} "
                f"(expected {' or '.join(java_kinds)})."
            )
    if roles:
        actual_role = (node.role or "").upper()
        want_roles = tuple(r.upper() for r in roles)
        if actual_role not in want_roles:
            return _emit(
                f"{expected}; resolved role is {node.role!r} "
                f"(expected {' or '.join(roles)})."
            )
    return None


def _validate_known_microservice(graph, name: str, args: argparse.Namespace) -> int | None:
    """Return ``None`` when ``name`` is a known microservice; else emit a
    ``status: error`` envelope and return 2.

    Used by ``connection``/``overview`` so a BOGUS microservice surfaces a clear
    "unknown microservice 'X'; run `jrag microservices`" error instead of an
    empty ``status: ok`` (which reads as "this service genuinely has no
    connections/entries" — a silent wrong answer).
    """
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    try:
        known = graph.microservice_counts()
    except Exception:
        known = {}
    if name in known:
        return None
    msg = f"unknown microservice {name!r}; run `jrag microservices` to list known services"
    print(render(Envelope(status="error", message=msg), fmt=args.format, detail=args.detail))
    return 2


def _warn_unapplied_scope(args: argparse.Namespace, *, reason: str) -> list[str]:
    """Build warnings[] for --service/--module that cannot be applied.

    Used by hierarchy/overrides/overridden-by/flow, where the backend query
    has no microservice/module predicate (structural edges / index-time data
    property). The plan principle "inapplicable flags never silently ignored"
    requires surfacing these as warnings rather than dropping them.
    """
    warnings: list[str] = []
    if args.service:
        warnings.append(f"--service is not applied on this command ({reason})")
    if getattr(args, "module", None):
        warnings.append(f"--module is not applied on this command ({reason})")
    return warnings


def _warn_inapplicable_common(
    args: argparse.Namespace, *, service: bool, module: bool, limit: bool
) -> list[str]:
    """Warn when common flags that don't apply to a command are set.

    Companion to :func:`_warn_unapplied_scope` for the aggregate / orientation
    commands (status / microservices / map / conventions) which inherit the
    ``common`` parent parser (``--service`` / ``--module`` / ``--limit``) but
    don't apply all of them. Each kwarg names whether THAT flag is inapplicable
    for this command (``True`` -> warn if the user set it). The plan principle
    "inapplicable flags never silently ignored" requires the warning; with the
    renderer now printing ``warning:`` lines, this is visible to text consumers
    too (not just ``--format json``).
    """
    warnings: list[str] = []
    if service and args.service:
        warnings.append("--service is not applied on this command")
    if module and getattr(args, "module", None):
        warnings.append("--module is not applied on this command")
    if limit and getattr(args, "limit", None) is not None and args.limit != 20:
        warnings.append("--limit is not applied on this command")
    return warnings


def _cmd_callers(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(
        args, cfg=cfg, graph=graph, hint_kind=args.kind, apply_scope=True
    )
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    root_dict = _noderef_to_node_dict(node)
    root_id = node.id

    # Route root -> find_route_callers. Route callers are inherently cross-
    # service (a Client in microservice A calls a server Route in microservice B),
    # so --service is NOT applied as a caller-microservice post-filter here;
    # it has already narrowed resolve (which route was selected) via
    # _resolve_traversal_node -> resolve_query.
    if node.kind == "route":
        route_callers = graph.find_route_callers(route_id=root_id)
        warnings: list[str] = []
        # No backend limit on find_route_callers; client-side slice for truncation.
        truncated = len(route_callers) > limit
        display = route_callers[:limit]
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for rc in display:
            caller_id = rc.caller_node_id
            if rc.caller_node_kind == "client":
                edge_type = "HTTP_CALLS"
            else:
                edge_type = "ASYNC_CALLS"
            # The caller's identity is the declaring Symbol (the method that owns
            # the Client/Producer), not the call-site path — mirrors
            # trace_request_flow, which surfaces declaring_symbol_fqn. The
            # path/topic the caller hits is kept as raw_uri/topic so the agent
            # sees both WHO calls and WHAT they hit.
            node = {
                "id": caller_id,
                "kind": rc.caller_node_kind,
                "fqn": rc.declaring_symbol_fqn or caller_id,
                "microservice": rc.caller_microservice,
            }
            if rc.target_service:
                node["target_service"] = rc.target_service
            if rc.caller_node_kind == "client" and rc.raw_uri:
                node["raw_uri"] = rc.raw_uri
            elif rc.caller_node_kind != "client" and rc.topic:
                node["topic"] = rc.topic
            nodes[caller_id] = node
            edges.append(
                {"other_id": caller_id, "edge_type": edge_type, "confidence": rc.confidence}
            )
        # Include the root (Route) node so the zero-callers rendering surfaces
        # the route path rather than a bare "0 callers" line.
        nodes[root_id] = root_dict
        # External-entrypoint detection: a server-exposed HTTP route (kind
        # http_endpoint with an inbound EXPOSES edge from a controller Symbol)
        # genuinely has zero in-repo callers — the route IS the entrypoint. Flag
        # it so the renderer says so instead of emitting a bug-looking bare
        # "0 callers". NodeRef.kind is the node label ("route"), not the stored
        # http_endpoint/kafka_topic property, so fetch the property directly.
        # Kafka topics are excluded: their empty-callers case has different
        # semantics (a topic with no producers is not an HTTP entrypoint).
        is_external_entrypoint = False
        if not display:
            kind_row = graph._rows(  # noqa: SLF001 - same pattern as jrag_envelope._node_file_location
                "MATCH (r:Route) WHERE r.id = $rid RETURN r.kind AS kind LIMIT 1",
                {"rid": root_id},
            )
            route_kind = str(kind_row[0].get("kind") or "") if kind_row else ""
            if route_kind == "http_endpoint" and graph.find_route_handlers(route_id=root_id):
                is_external_entrypoint = True
        return _emit_traversal(
            args, root_id=root_id, nodes=nodes, edges=edges,
            noun="callers", warnings=warnings, truncated=truncated,
            is_external_entrypoint=is_external_entrypoint,
        )

    # Symbol root -> find_callers (push down --service/--module/depth/etc.).
    if node.kind != "symbol":
        from java_codebase_rag.jrag_envelope import Envelope
        from java_codebase_rag.jrag_render import render

        env = Envelope(
            status="error",
            message=(
                f"callers expects a Symbol or Route root; resolved node kind is "
                f"{node.kind!r}. Use --kind to narrow resolve."
            ),
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    depth = getattr(args, "depth", 1)
    min_conf = getattr(args, "min_confidence", 0.0)
    exclude_external = not getattr(args, "include_external", False)
    call_edges = graph.find_callers(
        node.fqn,
        depth=depth,
        limit=limit + 1,
        min_confidence=min_conf,
        exclude_external=exclude_external,
        module=args.module,
        microservice=args.service,
    )
    from java_codebase_rag.jrag_envelope import mark_truncated

    display, truncated = mark_truncated(call_edges, limit)
    nodes = {}
    edges = []
    for ce in display:
        nodes[ce.src.id] = _symbol_hit_to_dict(ce.src)
        edges.append(
            {"other_id": ce.src.id, "edge_type": "CALLS", "confidence": ce.confidence}
        )
    nodes[root_id] = root_dict
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="callers", truncated=truncated,
    )


def _cmd_callees(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    # PR-JRAG-3b: accept Symbol (CALLS), Client (HTTP_CALLS), and Producer
    # (ASYNC_CALLS) roots. The Symbol path is unchanged from PR-JRAG-3a.
    guard = _require_kind(
        node,
        expected="callees expects a Symbol, Client, or Producer root",
        kinds=("symbol", "client", "producer"),
        args=args,
        hint="Use --kind to narrow resolve.",
    )
    if guard is not None:
        return guard

    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated
    from java_codebase_rag.jrag_render import render

    # Client root -> HTTP_CALLS out (Client -> :Route).
    # Producer root -> ASYNC_CALLS out (Producer -> :Route, the kafka_topic
    # Route this producer publishes to — NOT a :Producer node).
    if node.kind in ("client", "producer"):
        import mcp_v2

        edge_types = ["HTTP_CALLS"] if node.kind == "client" else ["ASYNC_CALLS"]
        out = mcp_v2.neighbors_v2(
            [node.id], direction="out", edge_types=edge_types,
            limit=limit + 1, graph=graph,
        )
        if not out.success:
            print(render(Envelope(status="error", message=out.message or "neighbors_v2 failed"), fmt=args.format, detail=args.detail))
            return 2
        root_id = node.id
        nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
        edges: list[dict] = []
        for e in out.results:
            nodes[e.other.id] = _noderef_to_node_dict(e.other)
            edges.append(
                {
                    "other_id": e.other.id,
                    "edge_type": e.edge_type,
                    "confidence": e.attrs.get("confidence"),
                }
            )
        truncated = bool(out.has_more_results) or len(edges) > limit
        if len(edges) > limit:
            edges = edges[:limit]
        # --include-external is accepted but does not apply on Client/Producer
        # roots (the edges are to :Route, which is always in-graph; there is no
        # external-exclusion analog). Surface as a warning so the flag is not
        # silently dropped (plan principle: inapplicable flags never silently ignored).
        warnings: list[str] = []
        if getattr(args, "include_external", False):
            warnings.append(
                "--include-external does not apply to Client/Producer roots "
                "(HTTP_CALLS/ASYNC_CALLS reach :Route, which is always in-graph)"
            )
        edges = _dedupe_traversal_edges(edges)
        truncated = truncated or len(edges) > limit
        edges = edges[:limit]
        return _emit_traversal(
            args, root_id=root_id, nodes=nodes, edges=edges,
            noun="callees", warnings=warnings, truncated=truncated,
        )

    depth = getattr(args, "depth", 1)
    min_conf = getattr(args, "min_confidence", 0.0)
    exclude_external = not getattr(args, "include_external", False)
    # CLIENT-role type Symbol (e.g. a Feign client interface): its "callees" are
    # the outbound HTTP endpoints its declared client methods call, NOT CALLS
    # edges from the interface (a Feign interface declares methods but its
    # methods' HTTP_CALLS edges carry the real outbound surface). Aggregate the
    # declared Client nodes and their HTTP_CALLS targets so `jrag callees
    # 'ChatCoreFeignClient'` shows the routes it hits.
    if (node.role or "") == "CLIENT":
        root_id = node.id
        client_rows = graph._rows(  # noqa: SLF001 - one-shot aggregation query
            "MATCH (iface:Symbol {id: $sid})-[:DECLARES]->(m:Symbol)"
            "-[:DECLARES_CLIENT]->(c:Client) "
            "OPTIONAL MATCH (c)-[e:HTTP_CALLS]->(r:Route) "
            "RETURN c.id AS cid, c.member_fqn AS cfqn, c.path AS cpath, "
            "c.method AS cmethod, c.microservice AS cms, "
            "r.id AS rid, r.method AS rmethod, r.path AS rpath, "
            "r.path_template AS rpt, r.microservice AS rms, "
            "e.confidence AS conf",
            {"sid": root_id},
        )
        nodes = {root_id: _noderef_to_node_dict(node)}
        edges: list[dict] = []
        for row in client_rows:
            rid = str(row.get("rid") or "")
            if rid:
                target_id = rid
                rmethod = str(row.get("rmethod") or "")
                rpath = str(row.get("rpt") or row.get("rpath") or "")
                nodes[target_id] = {
                    "id": target_id,
                    "kind": "route",
                    "fqn": f"{rmethod} {rpath}".strip(),
                    "microservice": str(row.get("rms") or ""),
                }
                edge_type = "HTTP_CALLS"
            else:
                # Client with no resolved HTTP_CALLS edge: surface the client
                # node + its declared path so the outbound intent is visible.
                target_id = str(row.get("cid") or "")
                if not target_id:
                    continue
                cmethod = str(row.get("cmethod") or "")
                cpath = str(row.get("cpath") or "")
                nodes[target_id] = {
                    "id": target_id,
                    "kind": "client",
                    "fqn": f"{cmethod} {cpath}".strip() or str(row.get("cfqn") or ""),
                    "microservice": str(row.get("cms") or ""),
                }
                edge_type = "HTTP_CALLS"
            edges.append({
                "other_id": target_id,
                "edge_type": edge_type,
                "confidence": float(row.get("conf") or 0.0) or None,
            })
        edges = _dedupe_traversal_edges(edges)
        truncated = len(edges) > limit
        edges = edges[:limit]
        return _emit_traversal(
            args, root_id=root_id, nodes=nodes, edges=edges,
            noun="callees", truncated=truncated,
        )

    call_edges = graph.find_callees(
        node.fqn,
        depth=depth,
        limit=limit + 1,
        min_confidence=min_conf,
        exclude_external=exclude_external,
        module=args.module,
        microservice=args.service,
    )
    display, truncated = mark_truncated(call_edges, limit)
    root_id = node.id
    nodes = {root_id: _noderef_to_node_dict(node)}
    edges = []
    for ce in display:
        nodes[ce.dst.id] = _symbol_hit_to_dict(ce.dst)
        edges.append(
            {"other_id": ce.dst.id, "edge_type": "CALLS", "confidence": ce.confidence}
        )
    edges = _dedupe_traversal_edges(edges)
    truncated = truncated or len(edges) > limit
    edges = edges[:limit]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="callees", truncated=truncated,
    )


def _cmd_hierarchy(args: argparse.Namespace) -> int:
    import mcp_v2

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    guard = _require_kind(
        node, expected="hierarchy expects a type Symbol root", kinds=("symbol",), args=args,
    )
    if guard is not None:
        return guard

    warnings = _warn_unapplied_scope(
        args, reason="neighbors_v2 walks structural EXTENDS/IMPLEMENTS edges with no microservice predicate"
    )

    root_id = node.id
    # Fetch both directions with limit+1 for +1-fetch truncation on each axis.
    fetch = limit + 1
    up = mcp_v2.neighbors_v2(
        [root_id], direction="out", edge_types=["EXTENDS", "IMPLEMENTS"],
        limit=fetch, graph=graph,
    )
    dn = mcp_v2.neighbors_v2(
        [root_id], direction="in", edge_types=["EXTENDS", "IMPLEMENTS"],
        limit=fetch, graph=graph,
    )
    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    if not up.success:
        print(render(Envelope(status="error", message=up.message or "neighbors_v2 failed"), fmt=args.format, detail=args.detail))
        return 2

    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    # Build up/down edges separately so the limit applies PER DIRECTION
    # (Fix 5: combined-list truncation could starve `down` behind a full `up`).
    up_edges: list[dict] = []
    for e in up.results:
        nodes[e.other.id] = _noderef_to_node_dict(e.other)
        up_edges.append({"other_id": e.other.id, "edge_type": e.edge_type, "direction": "up"})
    dn_edges: list[dict] = []
    for e in dn.results:
        nodes[e.other.id] = _noderef_to_node_dict(e.other)
        dn_edges.append({"other_id": e.other.id, "edge_type": e.edge_type, "direction": "down"})

    # Per-direction +1-fetch truncation: each side independently drops its
    # overflow row and flags truncation if it had limit+1 rows.
    truncated = len(up_edges) > limit or len(dn_edges) > limit
    up_display = up_edges[:limit]
    dn_display = dn_edges[:limit]
    display_edges = up_display + dn_display
    # Drop nodes no longer referenced after per-direction truncation (keep root).
    referenced = {root_id} | {e["other_id"] for e in display_edges}
    nodes = {nid: nd for nid, nd in nodes.items() if nid in referenced}
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=display_edges,
        noun="hierarchy", warnings=warnings, truncated=truncated,
    )


def _cmd_implementations(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    guard = _require_kind(
        node, expected="implementations expects an interface Symbol root", kinds=("symbol",),
        java_kinds=("interface",), args=args,
    )
    if guard is not None:
        return guard

    from java_codebase_rag.jrag_envelope import mark_truncated

    # ADAPTATION: find_implementors DOES accept a `capability` kwarg (brief
    # claimed otherwise). Push --capability down (matches the global principle
    # "pushed down where the method takes it"); --service/--module also pushed.
    impls = graph.find_implementors(
        node.fqn,
        microservice=args.service,
        module=args.module,
        capability=args.capability,
        limit=limit + 1,
    )
    display, truncated = mark_truncated(impls, limit)
    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for hit in display:
        nodes[hit.id] = _symbol_hit_to_dict(hit)
        edges.append({"other_id": hit.id, "edge_type": "IMPLEMENTS"})
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="implementations", truncated=truncated,
    )


def _cmd_subclasses(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    guard = _require_kind(
        node, expected="subclasses expects a class Symbol root", kinds=("symbol",),
        java_kinds=("class", "interface"), args=args,
    )
    if guard is not None:
        return guard

    from java_codebase_rag.jrag_envelope import mark_truncated

    subs = graph.find_subclasses(
        node.fqn,
        microservice=args.service,
        module=args.module,
        limit=limit + 1,
    )
    display, truncated = mark_truncated(subs, limit)
    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for hit in display:
        nodes[hit.id] = _symbol_hit_to_dict(hit)
        edges.append({"other_id": hit.id, "edge_type": "EXTENDS"})
    # Cross-ref hint: when the root is an interface, classes implementing it
    # arrive via IMPLEMENTS (not EXTENDS) — `find_subclasses` (EXTENDS inbound)
    # only catches sub-interfaces, so the common agent question "what classes
    # implement this interface?" is answered by `implementations <fqn>`. Surface
    # that as a `next:` hint whenever the root is an interface (helpful even
    # when a few sub-interfaces exist, and essential when results are empty).
    extra_hints: list[str] | None = None
    if (node.symbol_kind or "").lower() == "interface":
        extra_hints = [f"jrag implementations {node.fqn}"]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="subclasses", truncated=truncated, extra_hints=extra_hints,
    )


def _cmd_overrides(args: argparse.Namespace) -> int:
    import mcp_v2

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    guard = _require_kind(
        node, expected="overrides expects a method Symbol root", kinds=("symbol",),
        java_kinds=("method",), args=args,
    )
    if guard is not None:
        return guard

    warnings = _warn_unapplied_scope(
        args, reason="OVERRIDES is a structural method-to-method edge with no microservice predicate"
    )

    root_id = node.id
    # OVERRIDES edge runs overrider -> declaration (subtype -> supertype method).
    # direction="out" dispatches UP (the declarations this method overrides).
    out = mcp_v2.neighbors_v2(
        [root_id], direction="out", edge_types=["OVERRIDES"],
        limit=limit + 1, graph=graph,
    )
    if not out.success:
        print(render(Envelope(status="error", message=out.message or "neighbors_v2 failed"), fmt=args.format, detail=args.detail))
        return 2

    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for e in out.results:
        nodes[e.other.id] = _noderef_to_node_dict(e.other)
        # No `direction` key: overrides is a flat list, not a tree. Setting
        # direction="up" would trip the renderer's has_direction guard and
        # mis-label these rows as `↑ supertypes:` (hierarchy). Flat is correct.
        edges.append({"other_id": e.other.id, "edge_type": "OVERRIDES"})
    truncated = bool(out.has_more_results) or len(edges) > limit
    if len(edges) > limit:
        edges = edges[:limit]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="overrides", warnings=warnings, truncated=truncated,
    )


def _cmd_overridden_by(args: argparse.Namespace) -> int:
    import mcp_v2

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    guard = _require_kind(
        node, expected="overridden-by expects a method Symbol root", kinds=("symbol",),
        java_kinds=("method",), args=args,
    )
    if guard is not None:
        return guard

    warnings = _warn_unapplied_scope(
        args, reason="OVERRIDES is a structural method-to-method edge with no microservice predicate"
    )

    root_id = node.id
    # direction="in" on OVERRIDES = virtual OVERRIDDEN_BY out (dispatch DOWN:
    # from declaration to its overriders).
    out = mcp_v2.neighbors_v2(
        [root_id], direction="in", edge_types=["OVERRIDES"],
        limit=limit + 1, graph=graph,
    )
    if not out.success:
        print(render(Envelope(status="error", message=out.message or "neighbors_v2 failed"), fmt=args.format, detail=args.detail))
        return 2

    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for e in out.results:
        nodes[e.other.id] = _noderef_to_node_dict(e.other)
        # No `direction` key — see _cmd_overrides: a `direction` value would
        # route these into the hierarchy renderer (`↓ subtypes:`), mis-labeling
        # a flat overridden-by list.
        edges.append({"other_id": e.other.id, "edge_type": "OVERRIDES"})
    truncated = bool(out.has_more_results) or len(edges) > limit
    if len(edges) > limit:
        edges = edges[:limit]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="overridden-by", warnings=warnings, truncated=truncated,
    )


def _cmd_dependents(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    guard = _require_kind(
        node, expected="dependents expects a type Symbol root", kinds=("symbol",), args=args,
    )
    if guard is not None:
        return guard

    from java_codebase_rag.jrag_envelope import mark_truncated

    inj = graph.find_injectors(
        node.fqn,
        microservice=args.service,
        module=args.module,
        limit=limit + 1,
    )
    display, truncated = mark_truncated(inj, limit)
    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for eh in display:
        nodes[eh.src.id] = _symbol_hit_to_dict(eh.src)
        edges.append(
            {
                "other_id": eh.src.id,
                "edge_type": "INJECTS",
                "mechanism": eh.mechanism,
                "annotation": eh.annotation,
                "field_or_param": eh.field_or_param,
            }
        )
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="dependents", truncated=truncated,
    )


def _cmd_impact(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)
    depth = getattr(args, "depth", 2)

    from java_codebase_rag.jrag_envelope import mark_truncated

    impacts = graph.impact_analysis(node.fqn, depth=depth, limit=limit + 1)
    warnings: list[str] = []
    if args.service:
        # Filter client-side (impact_analysis has no microservice param). The
        # explanatory warning fires only when the caller EXPLICITLY passed
        # --service: under cwd-derived auto-scope the filter still applies
        # (that's the point — keep the blast-radius inside the working
        # service) but the "post-filter" caveat would be noise the agent
        # didn't ask for, so it's gated on ``_service_user``.
        impacts = [h for h in impacts if (h.microservice or "") == args.service]
        if getattr(args, "_service_user", False):
            warnings.append(
                "--service is a post-filter on impact (impact_analysis has no microservice param)"
            )
    if getattr(args, "module", None):
        # impact_analysis has no module param either; warn rather than drop silently.
        warnings.append(
            "--module is not applied on impact (impact_analysis has no module param)"
        )
    display, truncated = mark_truncated(impacts, limit)
    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for hit in display:
        nodes[hit.id] = _symbol_hit_to_dict(hit)
        edges.append({"other_id": hit.id, "edge_type": "IMPACTS"})
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="impact", warnings=warnings, truncated=truncated,
    )


def _cmd_decompose(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc

    guard = _require_kind(
        node, expected="decompose expects an entrypoint Symbol root", kinds=("symbol",), args=args,
    )
    if guard is not None:
        return guard

    # trace_flow clamps depth internally to 1..3; mirror here for the help text.
    depth = max(1, min(3, getattr(args, "depth", 2)))
    # decompose walks a TYPE role-waterfall (CONTROLLER -> SERVICE/COMPONENT ->
    # CLIENT/REPOSITORY/MAPPER) via INJECTS/EXTENDS/IMPLEMENTS, which are
    # type-to-type edges. A METHOD seed has no such edges, so trace_flow would
    # return only stage 0 (the seed itself). Promote a method seed to its owning
    # type so the waterfall is meaningful; point the agent at `callees` for the
    # method's direct call chain. (root stays the resolved method node.)
    seed_fqn = node.fqn
    warnings: list[str] = []
    if seed_fqn and "#" in seed_fqn:
        owning_type = seed_fqn.split("#", 1)[0]
        warnings.append(
            f"decompose is a type role-waterfall; promoted method seed "
            f"'{seed_fqn}' to its owning type '{owning_type}'. "
            f"Use `jrag callees {seed_fqn}` for the method's direct call chain."
        )
        seed_fqn = owning_type
    stages = graph.trace_flow(
        seed_fqns=[seed_fqn],
        depth=depth,
        follow_calls=getattr(args, "follow_calls", False),
        stage_limit=getattr(args, "max_stage", 20),
        min_call_confidence=getattr(args, "min_confidence", 0.0),
        exclude_external=not getattr(args, "include_external", False),
        microservice=args.service,
        module=args.module,
    )
    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for stage_idx, stage in enumerate(stages):
        for ss in stage:
            nodes[ss.symbol.id] = _symbol_hit_to_dict(ss.symbol)
            via = ss.via[0] if ss.via else None
            edge_type = via.edge_type if via else ("SEED" if stage_idx == 0 else "STAGE")
            edge_row = {
                "other_id": ss.symbol.id,
                "edge_type": edge_type,
                "stage": stage_idx,
                # Role carries through to the renderer so the waterfall can
                # label each stage with the role allow-list it matched.
                "role": ss.symbol.role or "",
            }
            if via and via.from_fqn:
                edge_row["from_fqn"] = via.from_fqn
            edges.append(edge_row)
    # --limit is inherited from common but does not cap decompose (trace_flow
    # is stage-limited via --max-stage, not a total edge count). Warn when the
    # user explicitly set --limit away from the default so they get a signal
    # rather than a silent multi-stage dump (Fix 4).
    if args.limit is not None and args.limit != 20:
        warnings.append(
            "--limit does not apply to decompose; use --max-stage to cap per-stage breadth"
        )
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="decompose", warnings=warnings,
    )


def _cmd_flow(args: argparse.Namespace) -> int:
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    # flow requires a Route root; force hint_kind="route".
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind="route")
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    guard = _require_kind(
        node, expected="flow requires a Route root", kinds=("route",), args=args,
        hint="Pass a route path (e.g. /chat/assign).",
    )
    if guard is not None:
        return guard

    warnings = _warn_unapplied_scope(
        args, reason="trace_request_flow carries no microservice predicate; intra-codebase is an index-time data property"
    )

    max_hops = max(1, min(8, getattr(args, "depth", 5)))
    flow_data = graph.trace_request_flow(entry_route_id=node.id, max_hops=max_hops)

    root_id = node.id
    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    # Inbound: cross-service HTTP/async callers (Client/Producer two-hop).
    for row in flow_data.get("inbound", []):
        caller_id = str(row.get("caller_node_id") or "")
        if not caller_id:
            continue
        kind = str(row.get("caller_node_kind") or "")
        nodes[caller_id] = {
            "id": caller_id,
            "kind": kind,
            "fqn": str(row.get("declaring_symbol_fqn") or ""),
            "microservice": str(row.get("microservice") or ""),
        }
        edges.append(
            {
                "other_id": caller_id,
                "edge_type": "HTTP_CALLS" if kind == "client" else "ASYNC_CALLS",
                "confidence": float(row.get("confidence") or 0.0),
            }
        )
    # Outbound: CALLS hops from the route handler (intra-service by construction).
    for row in flow_data.get("outbound", []):
        next_id = str(row.get("next_symbol_id") or "")
        if not next_id:
            continue
        nodes[next_id] = {
            "id": next_id,
            "kind": "symbol",
            "fqn": str(row.get("next_fqn") or ""),
            "microservice": str(row.get("next_microservice") or ""),
        }
        edges.append({"other_id": next_id, "edge_type": "CALLS"})

    # Client-side slice for truncation (trace_request_flow has no limit param).
    truncated = len(edges) > limit
    if truncated:
        edges = edges[:limit]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="flow", warnings=warnings, truncated=truncated,
    )


# ============================================================================
# PR-JRAG-3b: compose traversals + connection + outline/imports.
#
# callees Client/Producer variant (above) re-uses _cmd_callees. The four new
# handlers below cover: dependencies (INJECTS out), connection (multi-section
# microservice view, resolve-first EXCEPTION), outline (file -> symbols),
# imports (file -> tree-sitter parse -> resolve_v2 per FQN).
#
# Backend signatures verified at PR-JRAG-3b time:
#  * neighbors_v2(ids, direction, edge_types, limit=25, offset=0, ...) returns
#    NeighborsOutput.results: list[Edge] where Edge.other: NodeRef,
#    Edge.edge_type: str, Edge.attrs: dict (mcp_v2.py:1284).
#  * find_symbols_in_file_range(graph, *, filename, start_line, end_line)
#    returns list[SymbolHit]; start_line<1 returns [] (ladybug_queries.py:302).
#  * parse_java(source, *, filename, verbose) -> JavaFileAst with
#    explicit_imports: dict[str, str] (simple_name -> FQN) (ast_java.py:2612).
#  * INJECTS is Symbol -> Symbol (java_ontology.py:216); out = types this
#    symbol injects = direct dependencies.
#  * HTTP_CALLS is Client -> Route (java_ontology.py:352); ASYNC_CALLS is
#    Producer -> Route (java_ontology.py:386). Both confirmed.
# ============================================================================


def _cmd_dependencies(args: argparse.Namespace) -> int:
    import mcp_v2

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    node, _renv, rrc = _resolve_traversal_node(args, cfg=cfg, graph=graph, hint_kind=args.kind)
    if rrc or node is None:
        return rrc
    limit = _clamped_limit(args)

    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    # INJECTS is Symbol -> Symbol; Client/Producer/Route roots have no
    # injection edges (the edge type only fires on type Symbols).
    guard = _require_kind(
        node, expected="dependencies expects a Symbol root (INJECTS is Symbol -> Symbol)",
        kinds=("symbol",), args=args,
    )
    if guard is not None:
        return guard

    warnings = _warn_unapplied_scope(
        args, reason="neighbors_v2 walks structural INJECTS edges with no microservice predicate"
    )
    # --include-external is accepted for surface symmetry with callers/callees
    # but is a warned no-op here (INJECTS has no external-exclusion analog at
    # the neighbors_v2 layer; the edge is structural Symbol -> Symbol).
    if getattr(args, "include_external", False):
        warnings.append(
            "--include-external does not apply to dependencies "
            "(INJECTS is structural Symbol -> Symbol with no external-exclusion analog)"
        )

    root_id = node.id
    out = mcp_v2.neighbors_v2(
        [root_id], direction="out", edge_types=["INJECTS"],
        limit=limit + 1, graph=graph,
    )
    if not out.success:
        print(render(Envelope(status="error", message=out.message or "neighbors_v2 failed"), fmt=args.format, detail=args.detail))
        return 2

    nodes: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for e in out.results:
        nodes[e.other.id] = _noderef_to_node_dict(e.other)
        # Carry the injection metadata from the edge attrs (mechanism/annotation/
        # field_or_param) so the renderer and JSON consumers see how the dep is
        # injected.
        edge_row = {"other_id": e.other.id, "edge_type": "INJECTS"}
        for k in ("mechanism", "annotation", "field_or_param", "dst_fqn", "resolved"):
            if k in e.attrs:
                edge_row[k] = e.attrs[k]
        edges.append(edge_row)
    truncated = bool(out.has_more_results) or len(edges) > limit
    if len(edges) > limit:
        edges = edges[:limit]
    return _emit_traversal(
        args, root_id=root_id, nodes=nodes, edges=edges,
        noun="dependencies", warnings=warnings, truncated=truncated,
    )


def _client_dict_to_node(c: dict) -> dict:
    """list_clients dict -> envelope node dict (kind=client)."""
    return {
        "id": str(c.get("id") or ""),
        "kind": "client",
        "fqn": str(c.get("member_fqn") or c.get("path") or ""),
        "name": str(c.get("path") or ""),
        "client_kind": str(c.get("client_kind") or ""),
        "target_service": str(c.get("target_service") or ""),
        "method": str(c.get("method") or ""),
        "path": str(c.get("path") or ""),
        "microservice": str(c.get("microservice") or ""),
        "module": str(c.get("module") or ""),
    }


def _producer_dict_to_node(p: dict) -> dict:
    """list_producers dict -> envelope node dict (kind=producer)."""
    return {
        "id": str(p.get("id") or ""),
        "kind": "producer",
        "fqn": str(p.get("member_fqn") or p.get("topic") or ""),
        "name": str(p.get("topic") or ""),
        "producer_kind": str(p.get("producer_kind") or ""),
        "topic": str(p.get("topic") or ""),
        "broker": str(p.get("broker") or ""),
        "microservice": str(p.get("microservice") or ""),
        "module": str(p.get("module") or ""),
    }


def _cmd_connection(args: argparse.Namespace) -> int:
    """connection <microservice> — multi-section inbound:/outbound: view.

    RESOLVE-FIRST EXCEPTION: the first positional is a microservice NAME (used
    literally for list_clients / list_producers / find_route_callers); resolve_v2
    is NEVER run on it (the agent spec calls this out loudly in --help).
    """
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc
    limit = _clamped_limit(args)

    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    # Validate the microservice against the known set so a bogus name surfaces a
    # clear error instead of an empty inbound:/outbound: view (silent wrong
    # answer). Done AFTER graph load so `jrag connection X --format json` on a
    # missing index still reports the index error, not the microservice error.
    rc_ms = _validate_known_microservice(graph, args.microservice, args)
    if rc_ms is not None:
        return rc_ms

    microservice = args.microservice
    # argparse stores --inbound/--outbound/--both into `direction` via
    # action="store_const"; default is None when no flag is given (-> inbound,
    # per the brief: --inbound is the default direction).
    direction = getattr(args, "direction", None) or "both"
    http_method = (args.http_method or "").upper() or None
    calls_service = args.calls_service

    show_inbound = direction in ("inbound", "both")
    show_outbound = direction in ("outbound", "both")

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    warnings: list[str] = []

    # Filter predicates (applied client-side; --module is the only structural
    # common flag that's a bit meaningful here, but list_clients/list_producers
    # already take microservice; --module has no analog and is warned).
    if args.module:
        warnings.append("--module is not applied on connection (use --calls-service to narrow)")

    # --calls-service on outbound: clients are filtered STRICTLY (target_service
    # == calls_service); producers have no service target (they target topics),
    # so they bypass the filter and we emit a single warning so the agent knows
    # the async channel wasn't narrowed. The previous `or not target_service`
    # escape hatch matched unresolved clients (empty target_service, e.g.
    # AuditLogClient#logAssignment) — that was silent-wrong-results.
    producers_bypass_calls_service = bool(calls_service) and show_outbound

    def _http_method_match(row: dict) -> bool:
        if not http_method:
            return True
        return (str(row.get("method") or "").upper()) == http_method

    def _calls_service_match_out_client(row: dict) -> bool:
        # STRICT: a client is kept iff target_service == calls_service exactly.
        # Unresolved clients (empty target_service) are EXCLUDED — they did not
        # resolve to a specific target service, so we cannot confirm they call
        # --calls-service and must not surface them as a match.
        if not calls_service:
            return True
        return str(row.get("target_service") or "") == calls_service

    def _calls_service_match_in(caller_microservice: str) -> bool:
        if not calls_service:
            return True
        return caller_microservice == calls_service

    # --- Inbound: clients/producers in OTHER services targeting <microservice> ---
    if show_inbound:
        # HTTP: list_clients(target_service=microservice) gives every client
        # declaring a call into this service. Filter out clients IN this
        # microservice (those are intra-service, not inbound).
        http_in = graph.list_clients(target_service=microservice, limit=limit + 1)
        http_in = [c for c in http_in if (c.get("microservice") or "") != microservice]
        http_in = [c for c in http_in if _http_method_match(c) and _calls_service_match_in(c.get("microservice") or "")]
        for c in http_in[:limit + 1]:
            cid = c["id"]
            nodes[cid] = _client_dict_to_node(c)
            edges.append({"other_id": cid, "edge_type": "HTTP_CALLS", "section": "inbound"})

        # Async: topic Routes consumed by this microservice's listeners are
        # reached by producers in OTHER services via ASYNC_CALLS. The path is
        #   listener_method -[:EXPOSES]-> Route(topic) <-[:ASYNC_CALLS]- Producer
        # find_route_callers gives both client and producer callers for a route,
        # so we (a) enumerate this service's listener classes, (b) for each,
        # resolve the Route(s) it EXPOSES, (c) call find_route_callers on each
        # topic Route, (d) keep producer callers from other services.
        try:
            listener_hits = graph.list_by_capability(
                capability="MESSAGE_LISTENER",
                microservice=microservice,
                limit=_CONSUMER_FETCH_LIMIT,
            )
        except Exception as e:  # noqa: BLE001 - best-effort multi-section view
            # Don't swallow silently: surface the failure so an empty async
            # inbound section is distinguishable from "no listeners". HTTP
            # inbound above is unaffected; the command still returns its other
            # sections. (The bare `except: listener_hits = []` this replaces
            # produced silent wrong-results — status:ok with no async + no clue.)
            warnings.append(f"listener lookup failed; async inbound section skipped: {e}")
            listener_hits = []
        topic_route_ids: set[str] = set()
        for h in listener_hits:
            # listener method -> EXPOSES -> Route(topic). Resolve via a focused
            # Cypher lookup (Route.id for the EXPOSES target).
            rows = graph._rows(  # noqa: SLF001 - focused lookup, same pattern as _node_file_location
                "MATCH (mth:Symbol)-[:EXPOSES]->(r:Route) WHERE mth.id = $mid RETURN r.id AS rid",
                {"mid": h.id},
            )
            for r in rows:
                rid = str(r.get("rid") or "")
                if rid:
                    topic_route_ids.add(rid)
        # Cache list_producers() per caller_microservice so the inbound-async
        # loop issues ONE fetch per external service (not one per producer id).
        producer_cache: dict[str, list[dict]] = {}
        for rid in topic_route_ids:
            callers = graph.find_route_callers(route_id=rid)
            for c in callers:
                if c.caller_node_kind != "producer":
                    continue
                if (c.caller_microservice or "") == microservice:
                    continue  # intra-service
                if not _calls_service_match_in(c.caller_microservice or ""):
                    continue
                pid = c.caller_node_id
                if pid in nodes:
                    # Already rendered (e.g. duplicated via multiple topic routes)
                    edges.append({"other_id": pid, "edge_type": "ASYNC_CALLS", "section": "inbound", "confidence": c.confidence})
                    continue
                # Fetch producer dict for richer node data (cached per service).
                caller_ms = c.caller_microservice or ""
                if caller_ms not in producer_cache:
                    producer_cache[caller_ms] = graph.list_producers(
                        microservice=caller_ms or None, limit=_CONSUMER_FETCH_LIMIT,
                    )
                prod_dict = next((p for p in producer_cache[caller_ms] if p.get("id") == pid), None)
                if prod_dict:
                    nodes[pid] = _producer_dict_to_node(prod_dict)
                else:
                    nodes[pid] = {
                        "id": pid,
                        "kind": "producer",
                        "fqn": c.topic or "",
                        "name": c.topic or "",
                        "topic": c.topic or "",
                        "broker": c.broker or "",
                        "microservice": c.caller_microservice or "",
                    }
                edges.append({"other_id": pid, "edge_type": "ASYNC_CALLS", "section": "inbound", "confidence": c.confidence})

    # --- Outbound: clients/producers IN this microservice (calling out) ---
    if show_outbound:
        clients_out = graph.list_clients(microservice=microservice, limit=limit + 1)
        # Clients: apply --http-method AND --calls-service strictly (no empty-
        # target escape; unresolved clients are EXCLUDED under --calls-service).
        clients_out = [c for c in clients_out if _http_method_match(c) and _calls_service_match_out_client(c)]
        for c in clients_out[:limit + 1]:
            cid = c["id"]
            nodes[cid] = _client_dict_to_node(c)
            edges.append({"other_id": cid, "edge_type": "HTTP_CALLS", "section": "outbound"})

        producers_out = graph.list_producers(microservice=microservice, limit=limit + 1)
        # Producers bypass --calls-service (no service target on ASYNC channels);
        # emit ONE warning so the agent knows the async channel wasn't narrowed.
        if producers_bypass_calls_service and producers_out:
            warnings.append(
                f"--calls-service does not filter producers (no target_service on "
                f"ASYNC channels); {len(producers_out)} producer(s) kept visible"
            )
        for p in producers_out[:limit + 1]:
            pid = p["id"]
            nodes[pid] = _producer_dict_to_node(p)
            edges.append({"other_id": pid, "edge_type": "ASYNC_CALLS", "section": "outbound"})

    # Synthesize a microservice "root" node so the renderer uses the traversal
    # shape (root + edges) and the section-grouped rendering fires. The synthetic
    # id is namespaced to avoid colliding with real node ids.
    root_id = f"microservice:{microservice}"
    nodes[root_id] = {
        "id": root_id,
        "kind": "microservice",
        "fqn": microservice,
        "name": microservice,
        "microservice": microservice,
    }

    # Per-section truncation: cap each section at `limit` (drop overflow rows
    # and flag truncation if either side overflowed). We collected limit+1
    # rows above; slice here.
    inbound_edges = [e for e in edges if e.get("section") == "inbound"]
    outbound_edges = [e for e in edges if e.get("section") == "outbound"]
    truncated = len(inbound_edges) > limit or len(outbound_edges) > limit
    inbound_edges = inbound_edges[:limit]
    outbound_edges = outbound_edges[:limit]
    display_edges = inbound_edges + outbound_edges
    # Drop unreferenced node ids (keep the synthetic root).
    referenced = {root_id} | {e["other_id"] for e in display_edges}
    nodes = {nid: nd for nid, nd in nodes.items() if nid in referenced}

    env = Envelope(
        status="ok",
        nodes=nodes,
        edges=display_edges,
        root=root_id,
        warnings=warnings,
        truncated=truncated,
    )
    next_actions_hook(env, root=root_id, result_edges=display_edges)
    print(render(env, fmt=args.format, detail=args.detail, noun="connection"))
    return 0


def _resolve_source_path(cfg, file_arg: str) -> Path | None:
    """Resolve <file> to an existing path: absolute, else cfg.source_root/<file>.

    Returns None when neither exists (callers render a graceful envelope).
    """
    p = Path(file_arg)
    if p.is_absolute() and p.is_file():
        return p
    src = Path(cfg.source_root) if cfg.source_root else Path.cwd()
    candidate = src / file_arg
    if candidate.is_file():
        return candidate
    return None


def _cmd_outline(args: argparse.Namespace) -> int:
    """outline <file> — list every Symbol whose declared location is in <file>.

    Calls find_symbols_in_file_range(graph, filename=<file>, start_line=1,
    end_line=2**31-1). start_line MUST be >=1 (the backend returns [] for
    start_line<1). ``--limit`` caps the entry count (the file's symbol table
    is otherwise unbounded); ``truncated`` is set when more entries exist.
    """
    from ladybug_queries import find_symbols_in_file_range

    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook
    from java_codebase_rag.jrag_render import render

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    # PARITY with `imports`: resolve <file> via _resolve_source_path so a bare
    # class name or non-existent path yields the SAME "file not found" error
    # instead of a silent empty success. The graph stores filenames as
    # POSIX-relative paths from source root, so once the path resolves on disk
    # we re-derive that relative form for the exact-match query.
    file_path = _resolve_source_path(cfg, args.file)
    if file_path is None:
        env = Envelope(
            status="error",
            message=(
                f"file not found: {args.file!r} (looked at the literal path and at "
                f"<source_root>/{args.file})"
            ),
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2
    filename = args.file
    src_root = Path(cfg.source_root) if cfg.source_root else None
    if src_root is not None:
        try:
            filename = file_path.resolve().relative_to(src_root.resolve()).as_posix()
        except ValueError:
            # File lives outside source_root (e.g. an absolute path elsewhere);
            # fall back to the user's literal input — the graph may still match.
            filename = args.file
    try:
        hits = find_symbols_in_file_range(
            graph,
            filename=filename,
            start_line=1,
            end_line=2**31 - 1,
        )
    except Exception as exc:
        env = Envelope(status="error", message=f"outline failed: {exc}")
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    rows = [_symbol_hit_to_dict(h) for h in hits]
    limit = _clamped_limit(args)
    display, truncated = mark_truncated(rows, limit)
    nodes = {n["id"]: n for n in display}

    env = Envelope(status="ok", nodes=nodes, truncated=truncated)
    next_actions_hook(env)
    # Drill-down: the first declared symbol (class/interface) is the natural
    # thing to inspect from an outline. Per-row inspect hints for the leading
    # entries give the agent a concrete next step.
    env.agent_next_actions = _inspect_hints_for_rows(display, limit=2)
    print(render(env, fmt=args.format, detail=args.detail, noun="symbol"))
    return 0


def _cmd_imports(args: argparse.Namespace) -> int:
    """imports <file> — tree-sitter parse + resolve_v2 per imported FQN.

    Reads <file> from disk (cfg.source_root / <file> for relative paths),
    parses with ast_java.parse_java, walks explicit_imports (dict: simple_name
    -> FQN), then resolves each FQN via resolve_v2 against the graph. Returns
    a node per import: resolved graph Symbol when resolve_v2 hits (status=one),
    or an unresolved placeholder carrying the raw FQN otherwise.
    """
    from ast_java import parse_java
    from resolve_service import resolve_v2

    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    file_path = _resolve_source_path(cfg, args.file)
    if file_path is None:
        env = Envelope(
            status="error",
            message=(
                f"file not found: {args.file!r} (looked at the literal path and at "
                f"<source_root>/{args.file})"
            ),
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    try:
        src = file_path.read_bytes()
    except OSError as exc:
        env = Envelope(status="error", message=f"could not read {file_path}: {exc}")
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # parse_java is robust to invalid source (returns an empty JavaFileAst on
    # parse errors, never raises). It builds imports from the
    # `import_declaration` tree-sitter nodes via `_import_declaration_is_static`
    # (ast_java.py:905) and the scoped_identifier child walk (ast_java.py:2658).
    # explicit_imports: dict[str, str] = simple_name -> FQN (non-wildcard,
    # non-static); we also surface wildcard/static imports as unresolved rows so
    # the agent sees the full import block.
    ast = parse_java(src, filename=args.file)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    warnings: list[str] = []
    # Mirror outline: --limit is accepted (common flag) but imports returns the
    # full import block; surface a warning when the user explicitly set --limit
    # away from the default so they know it has no effect.
    if args.limit is not None and args.limit != 20:
        warnings.append("--limit does not apply to imports (the full import block is returned)")

    # Static + wildcard imports: rendered as unresolved rows (resolve_v2 only
    # matches type Symbols, not methods or wildcards).
    unresolved_imports: list[dict] = []
    for ident in ast.wildcard_imports:
        unresolved_imports.append({"fqn": f"{ident}.*", "kind": "wildcard"})
    for simple, fqn in ast.file_imports.static_methods.items():
        unresolved_imports.append({"fqn": fqn, "kind": "static_method", "name": simple})
    for prefix in ast.file_imports.static_wildcards:
        unresolved_imports.append({"fqn": f"{prefix}.*", "kind": "static_wildcard"})

    # Explicit type imports: resolve each via resolve_v2.
    resolved_count = 0
    unresolved_count = 0
    for simple, fqn in ast.explicit_imports.items():
        out = resolve_v2(fqn, hint_kind="symbol", graph=graph)
        if out.status == "one" and out.node is not None:
            ref = out.node
            node_dict = _noderef_to_node_dict(ref)
            node_dict["import_fqn"] = fqn
            node_dict["import_simple"] = simple
            nodes[ref.id] = node_dict
            edges.append({"other_id": ref.id, "edge_type": "IMPORTS", "resolved": True})
            resolved_count += 1
        else:
            # Use a stable synthetic id so unresolved imports round-trip JSON.
            synthetic_id = f"import:{fqn}"
            nodes[synthetic_id] = {
                "id": synthetic_id,
                "kind": "unresolved_import",
                "fqn": fqn,
                "name": simple,
                "import_simple": simple,
                "import_fqn": fqn,
            }
            edges.append({"other_id": synthetic_id, "edge_type": "IMPORTS", "resolved": False})
            unresolved_count += 1

    # Append unresolved static/wildcard imports as additional rows.
    for entry in unresolved_imports:
        fqn = entry["fqn"]
        synthetic_id = f"import:{fqn}"
        nodes[synthetic_id] = {
            "id": synthetic_id,
            "kind": "unresolved_import",
            "fqn": fqn,
            "name": fqn.rsplit(".", 1)[-1],
            "import_kind": entry.get("kind", ""),
        }
        edges.append({"other_id": synthetic_id, "edge_type": "IMPORTS", "resolved": False})

    if ast.parse_error:
        warnings.append("tree-sitter reported a parse_error for this file (imports extracted best-effort)")

    env = Envelope(status="ok", nodes=nodes, edges=edges, warnings=warnings)
    next_actions_hook(env, result_edges=edges)
    print(render(env, fmt=args.format, detail=args.detail, noun="import"))
    return 0


# ============================================================================
# PR-JRAG-4: orientation commands (microservices / map / conventions / overview)
# + semantic search.
#
# Orientation commands compose counts and listings from LadybugGraph methods
# and focused Cypher lookups (graph._rows). They render as inspect-shape
# (kv-block + nested dict sections) so the agent sees compact structured data.
#
# Search dispatches to search_v2 (mcp_v2.search_v2) after building a NodeFilter
# from flags. --fuzzy is registered on the parser but rejected IN-HANDLER with
# status: error (not argparse exit) so the envelope carries the message.
# ============================================================================


def _cmd_microservices(args: argparse.Namespace) -> int:
    """microservices — list every microservice with its resolved type count."""
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    counts = graph.microservice_counts()
    # --service / --module / --limit are rejected at the argparse layer
    # (microservices uses _core_parser), so no no-op warning is needed here.
    env = Envelope(
        status="ok",
        nodes={"microservices": {"counts": dict(counts)}},
    )
    next_actions_hook(env)
    # Natural follow-ups: drill into one service's structure (map) or its
    # conventions (role/framework distribution).
    env.agent_next_actions = ["jrag map", "jrag conventions"][:5]
    print(render(env, fmt=args.format, detail=args.detail, noun="microservices", shape="inspect"))
    return 0


def _cmd_map(args: argparse.Namespace) -> int:
    """map [--by microservice|module] [--service] [--module] — counts per kind.

    ``--by`` selects the grouping axis (default microservice). ``--service`` /
    ``--module`` narrow the count to one service / module (filters, independent
    of the axis). Previously ``--module`` was overloaded to also switch the
    axis, which made "group by ALL modules" unreachable.
    """
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    # Grouping axis: explicit --by wins; otherwise --module implies module axis
    # (the user's focus is the module they named), else default microservice.
    # This keeps the `group_by` label honest about the actual grouping: before,
    # `map --module X` labeled `group_by: microservice` while the user was
    # asking about a module — the label now matches what they see.
    group_col = args.by or ("module" if args.module else "microservice")
    scope_clauses: list[str] = []
    params: dict = {}
    if args.service:
        scope_clauses.append("s.microservice = $ms")
        params["ms"] = args.service
    if args.module:
        scope_clauses.append("s.module = $mod")
        params["mod"] = args.module
    scope_clause = " AND " + " AND ".join(scope_clauses) if scope_clauses else ""

    rows = graph._rows(  # noqa: SLF001 - counts compose query (same pattern as _scope_counts)
        f"MATCH (s:Symbol) WHERE s.resolved "
        f"AND s.kind IN ['class','interface','enum','record','annotation']"
        f"{scope_clause} "
        f"RETURN s.{group_col} AS scope, s.kind AS kind, count(*) AS n",
        params,
    )
    grouped: dict[str, dict[str, int]] = {}
    for r in rows:
        scope = str(r.get("scope") or "(unscoped)")
        kind = str(r.get("kind") or "(unknown)")
        grouped.setdefault(scope, {})[kind] = int(r.get("n") or 0)

    # --service/--module are applied above (scope_clauses); --limit is not (this
    # is an aggregate count, not a row fetch).
    warnings = _warn_inapplicable_common(args, service=False, module=False, limit=True)
    env = Envelope(
        status="ok",
        nodes={"map": {"group_by": group_col, "counts": grouped}},
        warnings=warnings,
    )
    next_actions_hook(env)
    # Drill-down: the agent's next step on a count is to inspect the structure
    # of a specific scope. Suggest `jrag overview <first scope>` (or the explicit
    # --service scope when present) so the agent has a concrete next command.
    first_scope = next(iter(grouped), None) if grouped else None
    drill_scope = args.service or (first_scope if group_col == "microservice" else None)
    hints: list[str] = []
    if drill_scope:
        hints.append(f"jrag overview {drill_scope}")
    hints.append("jrag conventions")
    env.agent_next_actions = hints[:5]
    print(render(env, fmt=args.format, detail=args.detail, noun="map", shape="inspect"))
    return 0


def _cmd_conventions(args: argparse.Namespace) -> int:
    """conventions [--service] — dominant roles + framework tallies."""
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    _, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    scope_clause = ""
    params: dict = {}
    if args.service:
        scope_clause = " AND s.microservice = $ms"
        params["ms"] = args.service

    role_rows = graph._rows(  # noqa: SLF001 - counts compose query
        f"MATCH (s:Symbol) WHERE s.resolved AND s.role IS NOT NULL AND s.role <> ''"
        f"{scope_clause} "
        f"RETURN s.role AS role, count(*) AS n ORDER BY n DESC",
        params,
    )
    role_counts: dict[str, int] = {}
    for r in role_rows:
        role = str(r.get("role") or "")
        if role:
            role_counts[role] = int(r.get("n") or 0)

    # Framework tallies: a direct count of route nodes by framework for
    # accuracy. --service is forwarded here too (previously the route framework
    # tally was global even when --service narrowed the role tally — half-scoped
    # output). Frameworks are NOT hardcoded; they are derived from the data
    # (r.framework on Route nodes).
    fw_scope = " AND r.microservice = $ms" if args.service else ""
    fw_rows = graph._rows(  # noqa: SLF001 - counts compose query
        f"MATCH (r:Route) WHERE r.framework IS NOT NULL AND r.framework <> ''"
        f"{fw_scope} "
        f"RETURN r.framework AS framework, count(*) AS n ORDER BY n DESC",
        params,
    )
    framework_counts: dict[str, int] = {}
    for r in fw_rows:
        fw = str(r.get("framework") or "")
        if fw:
            framework_counts[fw] = int(r.get("n") or 0)

    # --service is applied above; --module/--limit are not (no module clause;
    # aggregate count).
    warnings = _warn_inapplicable_common(args, service=False, module=True, limit=True)
    env = Envelope(
        status="ok",
        nodes={"conventions": {"roles": role_counts, "frameworks": framework_counts}},
        warnings=warnings,
    )
    next_actions_hook(env)
    # Drill-down: list the concrete symbols behind the dominant role so the
    # agent can inspect one (e.g. the top role's instances).
    hints: list[str] = []
    top_role = next(iter(role_counts), None) if role_counts else None
    drill_scope = args.service or ""
    if top_role:
        # Suggest finding symbols of the top role (scoped when --service set).
        scope_suffix = f" --service {drill_scope}" if drill_scope else ""
        hints.append(f"jrag find --role {top_role}{scope_suffix}")
    hints.append("jrag map")
    env.agent_next_actions = hints[:5]
    print(render(env, fmt=args.format, detail=args.detail, noun="conventions", shape="inspect"))
    return 0


def _overview_detect_type(subject: str, graph) -> str:
    """Auto-detect the subject type for `overview`.

    Returns "route" | "microservice" | "topic". Heuristics:
      * Starts with '/' → route.
      * Matches a known microservice name (microservice_counts keys) → microservice.
      * Else → topic (catch-all for messaging strings).
    """
    if subject.startswith("/"):
        return "route"
    try:
        ms_counts = graph.microservice_counts()
    except Exception:
        ms_counts = {}
    if subject in ms_counts:
        return "microservice"
    return "topic"


def _overview_microservice(args: argparse.Namespace, graph, microservice: str) -> int:
    """overview microservice bundle: counts + routes + clients + producers.

    The node is built WITHOUT top-level identity (kind/fqn/name) on purpose:
    that makes it a rollup to the envelope projector, which then keeps the
    nested dict/list sections (``bundle`` + sample lists) at every detail
    level. The command-side sample sizing below is what varies the output by
    detail: brief = bundle counts only, normal = +3 samples, full = +5 samples.
    Without both (rollup detection AND command-side sizing), brief/normal/full
    would all render identically because the projection would either strip the
    bundle to empty (subject node) or keep all samples equally (rollup node).
    """
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    limit = _clamped_limit(args)
    routes = graph.list_routes(microservice=microservice, limit=limit + 1)
    clients = graph.list_clients(microservice=microservice, limit=limit + 1)
    producers = graph.list_producers(microservice=microservice, limit=limit + 1)

    bundle = {
        "microservice": microservice,
        "routes": len(routes),
        "clients": len(clients),
        "producers": len(producers),
    }
    # Include sample entities (entities + listeners + jobs) for the service.
    try:
        entities = graph.list_by_role(
            role="ENTITY", microservice=microservice, limit=limit + 1
        )
        bundle["entities"] = len(entities)
    except Exception:
        pass

    # Sample sizing by detail: brief drops samples entirely (counts only);
    # normal caps at 3 (signal of what's there); full keeps 5 (richer picture).
    detail = args.detail
    sample_cap = 0 if detail == "brief" else (3 if detail == "normal" else 5)
    # No fqn/name/path/topic/member_fqn → project_node treats this as a rollup
    # and keeps the nested sections (bundle + sample lists) at every detail
    # level. ``kind`` stays for self-identification (it's a type tag, not in
    # the rollup-identity check).
    node: dict = {
        "kind": "microservice",
        "microservice": microservice,
        "bundle": bundle,
    }
    if sample_cap:
        node["route_sample"] = [
            {"path": r.get("path", ""), "framework": r.get("framework", "")}
            for r in routes[:sample_cap]
        ]
        node["client_sample"] = [
            {"fqn": c.get("member_fqn", ""), "target_service": c.get("target_service", "")}
            for c in clients[:sample_cap]
        ]
        node["producer_sample"] = [
            {"topic": p.get("topic", ""), "producer_kind": p.get("producer_kind", "")}
            for p in producers[:sample_cap]
        ]

    env = Envelope(
        status="ok",
        nodes={f"microservice:{microservice}": node},
    )
    next_actions_hook(env)
    print(render(env, fmt=args.format, detail=args.detail, noun="overview", shape="inspect"))
    return 0


def _overview_route(args: argparse.Namespace, cfg, graph, route_path: str) -> int:
    """overview route: resolve + trace_request_flow (same as `flow`)."""
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook, resolve_query
    from java_codebase_rag.jrag_render import render

    limit = _clamped_limit(args)
    node, renv = resolve_query(
        route_path, hint_kind="route", java_kind=None, role=None, fqn_contains=None,
        cfg=cfg, graph=graph,
    )
    if renv.status != "ok" or node is None:
        print(render(renv, fmt=args.format, detail=args.detail))
        return 2 if renv.status == "error" else 0

    if node.kind != "route":
        env = Envelope(
            status="error",
            message=f"overview --as route expects a Route; resolved kind is {node.kind!r}.",
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    max_hops = max(1, min(8, 5))
    flow_data = graph.trace_request_flow(entry_route_id=node.id, max_hops=max_hops)
    root_id = node.id
    nodes_dict: dict[str, dict] = {root_id: _noderef_to_node_dict(node)}
    edges: list[dict] = []
    for row in flow_data.get("inbound", []):
        caller_id = str(row.get("caller_node_id") or "")
        if not caller_id:
            continue
        kind = str(row.get("caller_node_kind") or "")
        nodes_dict[caller_id] = {
            "id": caller_id, "kind": kind,
            "fqn": str(row.get("declaring_symbol_fqn") or ""),
            "microservice": str(row.get("microservice") or ""),
        }
        edges.append({
            "other_id": caller_id,
            "edge_type": "HTTP_CALLS" if kind == "client" else "ASYNC_CALLS",
            "confidence": float(row.get("confidence") or 0.0),
        })
    for row in flow_data.get("outbound", []):
        next_id = str(row.get("next_symbol_id") or "")
        if not next_id:
            continue
        nodes_dict[next_id] = {
            "id": next_id, "kind": "symbol",
            "fqn": str(row.get("next_fqn") or ""),
            "microservice": str(row.get("next_microservice") or ""),
        }
        edges.append({"other_id": next_id, "edge_type": "CALLS"})
    truncated = len(edges) > limit
    if truncated:
        edges = edges[:limit]
    env = Envelope(status="ok", nodes=nodes_dict, edges=edges, root=root_id, truncated=truncated)
    next_actions_hook(env, root=root_id, result_edges=edges)
    print(render(env, fmt=args.format, detail=args.detail, noun="overview"))
    return 0


def _overview_topic(args: argparse.Namespace, graph, topic: str) -> int:
    """overview topic: producers + consumers for a topic string.

    Built without top-level identity (kind/fqn/name) so the projector treats
    the node as a rollup and keeps the nested sections (``bundle`` +
    producers/consumers lists) at every detail level. Command-side sample
    sizing varies the output by detail: brief = counts only, normal = +3
    samples, full = +limit samples.
    """
    from java_codebase_rag.jrag_envelope import Envelope, next_actions_hook
    from java_codebase_rag.jrag_render import render

    limit = _clamped_limit(args)
    # Producers: exact topic match first, then substring match as fallback.
    producers = graph.list_producers(topic_contains=topic, limit=limit + 1)
    if not producers and len(topic) >= 3:
        # Try a shorter substring if the exact topic yields nothing.
        producers = graph.list_producers(topic_contains=topic[:3], limit=limit + 1)
        producers = [p for p in producers if topic in str(p.get("topic") or "")]

    # Consumers: listener classes consuming this topic via EXPOSES on Route.
    consumers = _resolve_topic_consumers(graph, topic=topic, contains=False)
    if not consumers:
        consumers = _resolve_topic_consumers(graph, topic=topic, contains=True)

    detail = args.detail
    sample_cap = 0 if detail == "brief" else (3 if detail == "normal" else limit)
    # NOTE: no top-level ``topic``/fqn/name here — ``topic`` IS a rollup-
    # identity key, so its presence would make project_node treat this as a
    # subject and strip the bundle/producers/consumers sections at brief/
    # normal. ``kind`` is fine (type tag, not in the rollup-identity check),
    # so it stays for self-identification. The topic name travels in the dict
    # key ("topic:<name>") and inside ``bundle.topic``.
    topic_node: dict = {
        "kind": "topic",
        "bundle": {
            "topic": topic,
            "producers": len(producers),
            "consumers": len(consumers),
        },
    }
    if sample_cap:
        topic_node["producers"] = [
            {
                "fqn": str(p.get("member_fqn") or ""),
                "topic": str(p.get("topic") or ""),
                "producer_kind": str(p.get("producer_kind") or ""),
                "microservice": str(p.get("microservice") or ""),
            }
            for p in producers[:sample_cap]
        ]
        topic_node["consumers"] = [
            {
                "fqn": c.get("fqn", ""),
                "kind": c.get("kind", "symbol"),
                "microservice": c.get("microservice", ""),
            }
            for c in consumers[:sample_cap]
        ]
    env = Envelope(
        status="ok",
        nodes={f"topic:{topic}": topic_node},
    )
    next_actions_hook(env)
    print(render(env, fmt=args.format, detail=args.detail, noun="overview", shape="inspect"))
    return 0


def _cmd_overview(args: argparse.Namespace) -> int:
    """overview <microservice|route-path|topic> [--as ...] — dispatch on type."""
    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    from java_codebase_rag.jrag_envelope import Envelope
    from java_codebase_rag.jrag_render import render

    subject = args.subject
    # --service is inherited from the common parser. Treat a provided --service
    # as the subject when no positional was given (so `overview --service
    # chat-assign` works like `overview chat-assign`), and ALWAYS validate it
    # against the known set so a bogus name errors clearly instead of producing
    # an empty bundle that reads as "service has no entries".
    if args.service and not subject:
        subject = args.service
    if args.service:
        rc_ms = _validate_known_microservice(graph, args.service, args)
        if rc_ms is not None:
            return rc_ms

    if not subject:
        # Subject is optional on the parser (nargs='?') so we can emit a helpful
        # explanation instead of argparse's opaque "the following arguments are
        # required: subject". Prints to stderr (usage guidance) + a status:error
        # envelope to stdout, exit 2.
        msg = (
            "overview requires a <subject>: a microservice name (e.g. 'chat-core'), "
            "a route path (e.g. '/api/v1/chat/events'), or a topic string "
            "(e.g. 'banking.chat.audit'). Use --as {microservice,route,topic} to "
            "override auto-detection."
        )
        print(render(Envelope(status="error", message=msg), fmt=args.format, detail=args.detail))
        return 2
    as_type = getattr(args, "as_type", None)
    if as_type is None:
        as_type = _overview_detect_type(subject, graph)

    # NOTE: we do NOT validate `subject` against the known microservice set here.
    # Auto-detect only returns "microservice" when the subject IS in
    # microservice_counts, so that path is already known-good; and an explicit
    # `--as microservice` is a deliberate force (e.g. on a route-shaped string)
    # that must NOT be rejected. The bogus-microservice guard for overview is
    # carried entirely by the --service flag validation above.
    if as_type == "route":
        return _overview_route(args, cfg, graph, subject)
    if as_type == "microservice":
        return _overview_microservice(args, graph, subject)
    return _overview_topic(args, graph, subject)


# ============================================================================
# Search (PR-JRAG-4)
# ============================================================================


def _zero_result_guidance(args: argparse.Namespace, graph) -> str | None:
    """Hint where matches live when a filtered search returns 0 results.

    Runs ONE cheap unfiltered probe (limit 10) and tallies the filtered
    dimension across the probe hits, so an agent who filtered to e.g.
    ``--role SERVICE`` and got nothing learns the matches are under
    COMPONENT/OTHER instead of guessing. Returns None when no guidance
    applies: no recognizable single-dimension filter set, the probe is
    empty (truly no matches for this query), or the probe itself errored
    (non-fatal — the empty result still renders).
    """
    import mcp_v2
    from collections import Counter

    from java_codebase_rag.jrag_envelope import normalize_enum

    # Only the common single-dimension filters get guidance; first set wins.
    dims: list[tuple[str, str, str, str]] = []
    if args.role:
        dims.append(("role", "role", "roles", normalize_enum(args.role, kind="role")))
    if args.service:
        dims.append(("microservice", "service", "services", args.service))
    if args.module:
        dims.append(("module", "module", "modules", args.module))
    if not dims:
        return None
    attr, flag, plural, value = dims[0]

    try:
        probe = mcp_v2.search_v2(
            args.query,
            table=args.table,
            hybrid=args.hybrid,
            limit=10,
            offset=0,
            path_contains=args.path_contains,
            filter=None,
            explain=False,
            graph=graph,
        )
    except Exception:
        return None
    if not probe.success or not probe.results:
        return None

    counts: Counter = Counter(getattr(h, attr, None) for h in probe.results)
    counts.pop(None, None)
    if not counts:
        return None
    total = sum(counts.values())
    top = counts.most_common(3)
    alts = ", ".join(f"{v} ({c})" for v, c in top)
    suggestion = top[0][0]
    return (
        f"0 results with --{flag} {value}; {total} matches exist under other {plural}: "
        f"{alts} — try --{flag} {suggestion}"
    )


def _cmd_search(args: argparse.Namespace) -> int:
    """search <query> — semantic search via search_v2 over Lance tables.

    Builds a NodeFilter from flags, calls search_v2 with limit+1 for +1-fetch
    truncation, and renders. --fuzzy is rejected IN-HANDLER (not argparse-exit)
    so the error carries the canonical envelope shape.
    """
    import mcp_v2

    from java_codebase_rag.jrag_envelope import Envelope, mark_truncated, next_actions_hook, normalize_enum
    from java_codebase_rag.jrag_render import render

    # --fuzzy: registered on the parser (so argparse doesn't exit 2), but rejected
    # IN-HANDLER with status: error (search is inherently semantic; --fuzzy is
    # a no-op synonym, not a real mode toggle).
    if getattr(args, "fuzzy", False):
        env = Envelope(
            status="error",
            message="search is semantic; --fuzzy is implicit",
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    cfg, graph, rc = _load_graph_or_error(args)
    if rc:
        return rc

    limit = min(args.limit if args.limit is not None else 20, 499)

    # --limit 0: short-circuit to a clean empty page. mark_truncated(rows, 0)
    # would otherwise report truncated=True (a unit test pins the helper's
    # current behavior, so we fix this in the handler, not the helper), and
    # there is nothing to search — skip the embedding-model load entirely.
    if limit == 0:
        env = Envelope(
            status="ok", nodes={}, truncated=False,
            warnings=_auto_scope_notice(args),
        )
        next_actions_hook(env)
        print(render(env, fmt=args.format, detail=args.detail, noun="search"))
        return 0

    # Build NodeFilter from flags (same set as `find` filter mode).
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
    if args.fqn_contains:
        filter_dict["fqn_contains"] = args.fqn_contains
    if args.java_kind:
        filter_dict["symbol_kind"] = normalize_enum(args.java_kind, kind="java_kind")
    # NOTE: --framework is intentionally NOT placed in the NodeFilter. The graph
    # stores `framework` only on Route nodes (Route.framework), so the
    # NodeFilter `framework` field is validated against the route-only Framework
    # Literal AND rejected by the symbol-kind applicability guard. Applying it to
    # a symbol result set requires mapping the framework tag back onto the
    # declaring type via its annotations — done as a client-side POST-filter
    # below (`_framework_post_filter`) after the search hits come back.
    framework_want = normalize_enum(args.framework, kind="framework") if args.framework else None
    if framework_want and framework_want not in _FRAMEWORK_ANNOTATIONS:
        # Catch an unknown framework BEFORE the search runs (saves the embedding
        # model load + Lance scan). The valid set is the same one NodeFilter
        # validates against for routes — surfaced as a clean error envelope.
        valid = ", ".join(sorted(_FRAMEWORK_ANNOTATIONS))
        env = Envelope(
            status="error",
            message=(
                f"invalid framework: {args.framework!r} (normalized to {framework_want!r}); "
                f"expected one of: {valid}"
            ),
        )
        print(render(env, fmt=args.format, detail=args.detail))
        return 2
    node_filter, err_env = _build_node_filter_or_error(filter_dict)
    if err_env is not None:
        print(render(err_env, fmt=args.format, detail=args.detail))
        return 2

    out = mcp_v2.search_v2(
        args.query,
        table=args.table,
        hybrid=args.hybrid,
        limit=limit + 1,  # +1 for truncated detection
        offset=args.offset,
        path_contains=args.path_contains,
        filter=node_filter,
        explain=args.explain,
        graph=graph,
        dedup=not getattr(args, "chunks", False),
    )

    if not out.success:
        env = Envelope(status="error", message=out.message or "search failed")
        print(render(env, fmt=args.format, detail=args.detail))
        return 2

    # Convert SearchHit list to envelope node dicts.
    # Score floor (default 0.0): drop negative-score noise — chunks farther than
    # orthogonal to the query (l2_distance_to_score < 0) are never a real match.
    # Applied BEFORE truncation so the floor tightens precision without the +1
    # row leaking past it. SearchHit now carries filename/start_line; the
    # envelope projector (_compose_file) folds those into the `file` display
    # field so each rendered hit shows its file path (filename:start_line).
    min_score = getattr(args, "min_score", 0.0) or 0.0
    hit_dicts: list[dict] = []
    for hit in out.results:
        if float(getattr(hit, "score", 0.0)) < min_score:
            continue
        d = hit.model_dump() if hasattr(hit, "model_dump") else dict(hit)
        # Ensure an `id` key for envelope nodes (SearchHit carries chunk_id +
        # optional symbol_id; use chunk_id as the envelope node id).
        if "id" not in d:
            d["id"] = d.get("chunk_id") or d.get("symbol_id") or d.get("fqn") or ""
        if "kind" not in d:
            d["kind"] = "search_hit"
        # Add explain token when --explain is set
        if args.explain:
            from search_lancedb import explain_score_components
            comps = d.get("score_components")
            d["explain"] = explain_score_components(
                comps,
                role=d.get("role"),
                hybrid=bool(args.hybrid),
                graph_expanded=False,
            )
        hit_dicts.append(d)

    # --framework POST-filter: the graph stores `framework` only on Route nodes,
    # so we map the requested framework tag back onto the symbol's declaring
    # type via its annotations (e.g. spring_mvc -> @RestController) and keep
    # only hits whose primary type declares one of those annotations. Applied
    # BEFORE truncation so the cap bounds the visible (filtered) page.
    framework_dropped = 0
    if framework_want and hit_dicts:
        framework_fqns = _framework_type_fqns(graph, framework_want)
        kept: list[dict] = []
        for d in hit_dicts:
            type_fqn = d.get("fqn") or ""
            if type_fqn and type_fqn in framework_fqns:
                kept.append(d)
            else:
                framework_dropped += 1
        hit_dicts = kept

    display, truncated = mark_truncated(hit_dicts, limit)
    nodes = {n["id"]: n for n in display} if display else {}

    warnings: list[str] = []
    if framework_want and framework_dropped and not display:
        warnings.append(
            f"--framework {framework_want!r} filtered out all {framework_dropped} hit(s); "
            f"no symbol's declaring type matched the framework's characteristic annotations"
        )
    # Zero-result guidance: when a structural filter emptied the page, run one
    # cheap unfiltered probe and point at where matches actually live (e.g.
    # "--role SERVICE" returned 0 but matches are under COMPONENT/OTHER).
    if not hit_dicts and filter_dict:
        guidance = _zero_result_guidance(args, graph)
        if guidance:
            warnings.append(guidance)
    env = Envelope(
        status="ok", nodes=nodes, truncated=truncated,
        warnings=warnings + _auto_scope_notice(args),
    )
    next_actions_hook(env)
    # Per-hit drill-down: the top search hit's primary type is the natural
    # thing to inspect (signature, edges, callers). Two visible hints in text,
    # up to 5 in JSON.
    if display:
        env.agent_next_actions = _inspect_hints_for_rows(display, limit=2)
    next_offset = args.offset + limit if truncated else None
    print(render(env, fmt=args.format, detail=args.detail, noun="search", next_offset=next_offset))
    return 0


def _suppress_runtime_stderr_noise() -> None:
    """Silence known-benign stderr noise from the embedding/LanceDB stack.

    The CLI loads sentence_transformers + LanceDB per invocation; both emit
    benign stderr noise that an agent-facing tool should not dump on the caller:

      * tqdm ``Loading weights`` progress bar (sentence_transformers model load)
      * HuggingFace hub progress bars / telemetry
      * torch multiprocessing ``leaked semaphore objects`` ``resource_tracker``
        UserWarning emitted at shutdown

    Real diagnostics (the top-level handler's ``traceback.format_exc()``) still
    go to stderr. Env vars are set with ``setdefault`` so an explicit caller
    override wins. The ``resource_tracker`` warning is raised inside a spawned
    child process; under the spawn start method (macOS default) the child
    re-initializes ``warnings`` and does NOT inherit the parent's
    ``warnings.filterwarnings``, so we route it through ``PYTHONWARNINGS`` (env
    vars ARE inherited by spawned children) as well as the parent filter.
    """
    for key, val in (
        ("TQDM_DISABLE", "1"),
        ("TRANSFORMERS_VERBOSITY", "error"),
        ("HF_HUB_DISABLE_PROGRESS_BARS", "1"),
        ("HF_HUB_DISABLE_TELEMETRY", "1"),
        # mcp_v2._log_fail_loud operator diagnostic — the CLI surfaces the same
        # failure as a clean status:error envelope, so silence the stderr line.
        ("JAVA_CODEBASE_RAG_FAIL_LOUD", "0"),
    ):
        os.environ.setdefault(key, val)
    existing_pw = os.environ.get("PYTHONWARNINGS", "")
    extra_pw = "ignore:resource_tracker:UserWarning"
    if extra_pw not in existing_pw:
        os.environ["PYTHONWARNINGS"] = f"{existing_pw},{extra_pw}" if existing_pw else extra_pw
    import warnings

    warnings.filterwarnings("ignore", message=r"resource_tracker.*", category=UserWarning)


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
    _suppress_runtime_stderr_noise()
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
        # exit_on_error=False + _EnvelopeArgumentParser routes argparse usage
        # errors here (missing required positional, unrecognized flag, bad
        # choices) WITHOUT dumping usage text. We emit a clean status:error
        # envelope to STDOUT honoring --format so JSON consumers get a
        # parseable result (parity with overview/find missing-arg paths), AND
        # mirror a terse line to STDERR so shell users / `2>&1` pipelines and
        # the existing "non-empty stderr on usage error" tests still see it.
        # Exit non-zero: this is a usage error, distinct from a not_found
        # envelope (exit 0, the resolve found nothing).
        from java_codebase_rag.jrag_envelope import Envelope
        from java_codebase_rag.jrag_render import render

        fmt, detail, leftover = _preparse_render_flags(raw)
        fmt = fmt or "text"
        detail = detail or "normal"
        # The subcommand is the first non-dash token in the leftover (flag
        # values already consumed by the pre-parser), so we don't mis-prefix
        # with a value like ``json`` from ``--format json``.
        cmd = next((t for t in leftover if not t.startswith("-")), None)
        msg = str(exc).strip() or "usage error"
        if cmd and not msg.startswith(cmd):
            msg = f"{cmd}: {msg}"
        env = Envelope(status="error", message=msg)
        print(render(env, fmt=fmt, detail=detail))
        print(f"jrag: error: {msg}", file=sys.stderr)
        return 2
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
    force_utf8_stdio()
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    _console_script_main()
