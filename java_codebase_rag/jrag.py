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
