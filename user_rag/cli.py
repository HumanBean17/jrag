from __future__ import annotations

import argparse
import asyncio
import json
import os
import pprint
import sys
from pathlib import Path
from typing import Any

import pr_analysis
import server
from path_filtering import LayeredIgnore


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _to_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _emit(value: Any) -> None:
    payload = _to_payload(value)
    if sys.stdout.isatty():
        print(pprint.pformat(payload, sort_dicts=True))
        return
    print(json.dumps(payload, default=_jsonable, sort_keys=True, indent=None))


def _parse_common_graph_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-root", type=str, default=None)
    parser.add_argument("--kuzu-path", type=str, default=None)
    parser.add_argument("--lancedb-path", type=str, default=None)


def _apply_graph_env(args: argparse.Namespace) -> None:
    if args.source_root:
        os.environ["LANCEDB_MCP_PROJECT_ROOT"] = str(Path(args.source_root).expanduser().resolve())
    if args.kuzu_path:
        os.environ["KUZU_DB_PATH"] = str(Path(args.kuzu_path).expanduser().resolve())
        # Reset singleton to pick up override path.
        from kuzu_queries import KuzuGraph

        KuzuGraph._instance = None
        KuzuGraph._instance_path = None
    if args.lancedb_path:
        os.environ["LANCEDB_URI"] = str(Path(args.lancedb_path).expanduser().resolve())


def _cmd_refresh(args: argparse.Namespace) -> int:
    """Return 1 for launched-subprocess failures, 2 for internal pre-launch errors."""
    _apply_graph_env(args)
    result = asyncio.run(server.run_refresh_pipeline(quiet=bool(args.quiet)))
    payload = result.model_dump()
    if payload.get("success"):
        _emit(payload)
        return 0
    _emit(payload)
    return 2 if payload.get("exit_code") is None else 1


def _cmd_meta(args: argparse.Namespace) -> int:
    _apply_graph_env(args)
    payload = server._graph_meta_output().model_dump()
    _emit(payload)
    return 0 if payload.get("success") else 2


def _cmd_tables(args: argparse.Namespace) -> int:
    _apply_graph_env(args)
    payload = server.list_code_index_tables_payload().model_dump()
    _emit(payload)
    return 0


def _cmd_diagnose_ignore(args: argparse.Namespace) -> int:
    _apply_graph_env(args)
    # Keep this after _apply_graph_env so relative paths resolve from --source-root.
    root = server._project_root()
    raw = Path(args.path)
    try:
        abs_path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    except OSError as exc:
        _emit({"success": False, "message": f"Invalid path: {exc}"})
        return 1
    li = LayeredIgnore(root)
    _emit(li.diagnose_dict(abs_path))
    return 0


def _read_diff_text(args: argparse.Namespace) -> str:
    if args.diff_file:
        return Path(args.diff_file).read_text(encoding="utf-8")
    if args.diff_stdin:
        return sys.stdin.read()
    raise ValueError("Provide exactly one of --diff-file or --diff-stdin")


def _cmd_analyze_pr(args: argparse.Namespace) -> int:
    _apply_graph_env(args)
    try:
        diff_text = _read_diff_text(args)
    except Exception as exc:
        _emit({"success": False, "message": str(exc)})
        return 1
    if not diff_text.strip():
        _emit({"success": False, "message": "Diff is empty"})
        return 1
    from kuzu_queries import KuzuGraph

    if not KuzuGraph.exists():
        _emit({"success": False, "message": "Kuzu graph not found"})
        return 1
    graph = KuzuGraph.get()
    report = pr_analysis.analyze_pr_pipeline(graph, diff_text)
    _emit(pr_analysis.pr_report_to_dict(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="java-codebase-rag")
    subparsers = parser.add_subparsers(dest="subcommand")

    refresh = subparsers.add_parser("refresh")
    _parse_common_graph_flags(refresh)
    refresh.add_argument("--quiet", action="store_true")
    refresh.set_defaults(handler=_cmd_refresh)

    meta = subparsers.add_parser("meta")
    _parse_common_graph_flags(meta)
    meta.set_defaults(handler=_cmd_meta)

    tables = subparsers.add_parser("tables")
    _parse_common_graph_flags(tables)
    tables.set_defaults(handler=_cmd_tables)

    diagnose = subparsers.add_parser("diagnose-ignore")
    _parse_common_graph_flags(diagnose)
    diagnose.add_argument("path", type=str)
    diagnose.set_defaults(handler=_cmd_diagnose_ignore)

    analyze = subparsers.add_parser("analyze-pr")
    _parse_common_graph_flags(analyze)
    group = analyze.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff-file", type=str)
    group.add_argument("--diff-stdin", action="store_true")
    analyze.set_defaults(handler=_cmd_analyze_pr)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return int(handler(args))
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        _emit({"success": False, "exit_code": 2, "message": f"internal error: {exc}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
