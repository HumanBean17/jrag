from __future__ import annotations

# Heavy imports (`server`, `pr_analysis`, `path_filtering.LayeredIgnore`) stay lazy
# inside handlers so `java-codebase-rag --help` stays fast.

import argparse
import asyncio
import json
import pprint
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from java_codebase_rag.config import (
    ResolvedOperatorConfig,
    describe_path_sizes,
    emit_legacy_env_hints_if_present,
    emit_legacy_yaml_hint_if_needed,
    index_dir_has_existing_artifacts,
    resolve_operator_config,
)
from java_codebase_rag.pipeline import clip, run_build_ast_graph, run_cocoindex_drop, run_cocoindex_update
from java_ontology import VALID_UNRESOLVED_CALL_REASONS

KUZU_INCREMENTAL_TRACKING_ISSUE_URL = "https://github.com/HumanBean17/java-codebase-rag/issues/73"

_INCREMENT_WARNING_LINES = (
    "WARNING: AST graph (Kuzu) incremental rebuild is not yet implemented.",
    "The graph reflects the index state from the last `init` or `reprocess`,",
    "which means `find`, `neighbors`, and `describe` may return stale results",
    "for files changed since then.",
    "",
    "Lance vector index has been updated incrementally and is current.",
    "",
    "For an up-to-date graph, run:",
    "    java-codebase-rag reprocess",
    "",
    "Track progress on Kuzu incremental rebuild:",
    f"    {KUZU_INCREMENTAL_TRACKING_ISSUE_URL}",
)

_REFRESH_DEPRECATION = (
    "WARN: 'refresh' is deprecated; use 'reprocess'. "
    "This alias will be removed in the next release."
)

_REPROCESS_DRIFT_VECTORS_ONLY = (
    "java-codebase-rag reprocess: rebuilt vectors only; graph (code_graph.kuzu) was NOT rebuilt "
    "and may now reflect a stale source snapshot."
)


def _reprocess_drift_graph_only_line(index_dir: Path) -> str:
    return (
        "java-codebase-rag reprocess: rebuilt graph only; vectors (Lance tables under "
        f"{index_dir}) were NOT rebuilt and may now reflect a stale source snapshot."
    )


def _reprocess_exit_code(payload: dict[str, Any]) -> int:
    if payload.get("success"):
        return 0
    phases_run = payload.get("phases_run") or []
    if not phases_run:
        return 2
    return 1


# Preflight detection must stay aligned with stub CompletedProcess shapes in
# java_codebase_rag/pipeline.py (missing cocoindex / flow / build_ast_graph.py).
def _is_cocoindex_preflight_blocker(coco: Any) -> bool:
    """True when ``run_cocoindex_update`` returned without spawning cocoindex."""
    return bool(coco.returncode in (126, 127) and len(getattr(coco, "args", ()) or ()) <= 1)


def _is_graph_preflight_blocker(g: Any) -> bool:
    """True when ``run_build_ast_graph`` returned without spawning the builder."""
    return bool(g.returncode in (126, 127) and len(getattr(g, "args", ()) or ()) <= 1)


def _emit_reprocess_selective_tty(*, mode: str) -> None:
    if mode == "vectors":
        print("Rebuilt: vectors")
        print("Skipped: graph (use `java-codebase-rag reprocess --graph-only` or `reprocess` to refresh)")
    else:
        print("Rebuilt: graph")
        print("Skipped: vectors (use `java-codebase-rag reprocess --vectors-only` or `reprocess` to refresh)")


def _emit_reprocess_outcome(payload: dict[str, Any], *, selective_tty_mode: str | None = None) -> None:
    if payload.get("success") and selective_tty_mode and sys.stdout.isatty():
        _emit_reprocess_selective_tty(mode=selective_tty_mode)
        return
    _emit(payload)


_PIPELINE_SEP = "\u00b7"


def _pipeline_header(subcommand: str, cfg: ResolvedOperatorConfig) -> None:
    root = cfg.source_root.resolve()
    idx = cfg.index_dir.resolve()
    print(
        f"java-codebase-rag {subcommand} {_PIPELINE_SEP} source={root} {_PIPELINE_SEP} index={idx}",
        file=sys.stderr,
        flush=True,
    )


def _pipeline_footer(subcommand: str, started: float, exit_code: int) -> None:
    elapsed = time.perf_counter() - started
    print(
        f"java-codebase-rag {subcommand} {_PIPELINE_SEP} finished in {elapsed:.2f}s (exit={exit_code})",
        file=sys.stderr,
        flush=True,
    )


def _run_with_pipeline_progress(
    subcommand: str,
    cfg: ResolvedOperatorConfig,
    *,
    quiet: bool,
    work: Callable[[], int],
) -> int:
    if quiet:
        return int(work())
    _pipeline_header(subcommand, cfg)
    t0 = time.perf_counter()
    code = 0
    try:
        code = int(work())
        return code
    except BaseException as exc:
        # Keep footer aligned with process outcome (main maps unhandled Exception -> exit 2).
        if isinstance(exc, SystemExit):
            c = exc.code
            if isinstance(c, int):
                code = c
            elif c in (None, False):
                code = 0
            else:
                code = 1
        elif code == 0:
            code = 2
        raise
    finally:
        _pipeline_footer(subcommand, t0, code)


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


def _emit_increment_kuzu_warning() -> None:
    for line in _INCREMENT_WARNING_LINES:
        print(line, file=sys.stderr)


def _parse_source_root(ns: argparse.Namespace) -> Path | None:
    if ns.source_root:
        return Path(ns.source_root).expanduser().resolve()
    return None


def _resolved_from_ns(ns: argparse.Namespace) -> ResolvedOperatorConfig:
    root = _parse_source_root(ns)
    return resolve_operator_config(
        source_root=root,
        cli_index_dir=ns.index_dir,
        cli_embedding_model=getattr(ns, "embedding_model", None),
        cli_embedding_device=getattr(ns, "embedding_device", None),
    )


def _startup_hints(cfg: ResolvedOperatorConfig) -> None:
    emit_legacy_env_hints_if_present()
    emit_legacy_yaml_hint_if_needed(cfg.source_root)


def _add_index_embedding_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source-root", type=str, default=None, help="Java repository root (default: cwd)")
    p.add_argument("--index-dir", type=str, default=None, help="Index directory (Lance + Kuzu + cocoindex state)")
    p.add_argument("--embedding-model", type=str, default=None, help="Override SBERT_MODEL / YAML embedding.model")
    p.add_argument("--embedding-device", type=str, default=None, help="Override SBERT_DEVICE / YAML embedding.device")


def _cmd_init(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    occupied, paths = index_dir_has_existing_artifacts(cfg.index_dir)
    if occupied:
        _emit(
            {
                "success": False,
                "message": (
                    "init refused: index paths already exist. "
                    "Use `java-codebase-rag reprocess` to rebuild in place, "
                    "or `java-codebase-rag erase --yes` then `init` for a clean slate."
                ),
                "non_empty_paths": paths,
            }
        )
        return 2
    cfg.index_dir.mkdir(parents=True, exist_ok=True)

    def work() -> int:
        env = cfg.subprocess_env()
        coco = run_cocoindex_update(
            env,
            full_reprocess=False,
            quiet=bool(args.quiet),
            lance_project_root=None if args.quiet else cfg.source_root,
        )
        if coco.returncode != 0:
            _emit(
                {
                    "success": False,
                    "exit_code": coco.returncode,
                    "stdout": clip(coco.stdout, 8000),
                    "stderr": clip(coco.stderr, 8000),
                    "message": f"cocoindex exit {coco.returncode}",
                }
            )
            return 1
        g = run_build_ast_graph(
            source_root=cfg.source_root,
            kuzu_path=cfg.kuzu_path,
            verbose=not args.quiet,
            env=env,
        )
        if g.returncode != 0:
            _emit(
                {
                    "success": False,
                    "exit_code": g.returncode,
                    "stdout": clip(g.stdout, 4000),
                    "stderr": clip(g.stderr, 4000),
                    "message": f"graph builder exit {g.returncode}",
                }
            )
            return 1
        _emit({"success": True, "message": "init completed"})
        return 0

    return _run_with_pipeline_progress("init", cfg, quiet=bool(args.quiet), work=work)


def _cmd_increment(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    _emit_increment_kuzu_warning()

    def work() -> int:
        env = cfg.subprocess_env()
        coco = run_cocoindex_update(
            env,
            full_reprocess=False,
            quiet=bool(args.quiet),
            lance_project_root=None if args.quiet else cfg.source_root,
        )
        if coco.returncode != 0:
            _emit(
                {
                    "success": False,
                    "exit_code": coco.returncode,
                    "stdout": clip(coco.stdout, 8000),
                    "stderr": clip(coco.stderr, 8000),
                    "message": f"cocoindex exit {coco.returncode}",
                }
            )
            return 1
        _emit({"success": True, "message": "increment completed (Lance only; graph may be stale — see stderr)"})
        return 0

    return _run_with_pipeline_progress("increment", cfg, quiet=bool(args.quiet), work=work)


def _cmd_reprocess(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()

    def work() -> int:
        env = cfg.subprocess_env()
        vectors_only = bool(getattr(args, "vectors_only", False))
        graph_only = bool(getattr(args, "graph_only", False))

        if vectors_only:
            coco = run_cocoindex_update(env, full_reprocess=True, quiet=bool(args.quiet))
            if _is_cocoindex_preflight_blocker(coco):
                payload: dict[str, Any] = {
                    "success": False,
                    "exit_code": None,
                    "stdout": clip(coco.stdout, 8000),
                    "stderr": clip(coco.stderr, 8000),
                    "message": coco.stderr.strip() or f"cocoindex setup exit {coco.returncode}",
                    "graph_exit_code": None,
                    "graph_stdout": "",
                    "graph_stderr": "",
                    "phases_run": [],
                }
                _emit_reprocess_outcome(payload)
                return _reprocess_exit_code(payload)
            ok = coco.returncode == 0
            payload = {
                "success": ok,
                "exit_code": coco.returncode,
                "stdout": clip(coco.stdout, 8000),
                "stderr": clip(coco.stderr, 8000),
                "message": None if ok else f"cocoindex exit {coco.returncode}",
                "graph_exit_code": None,
                "graph_stdout": "",
                "graph_stderr": "",
                "phases_run": ["vectors"],
            }
            if ok:
                print(_REPROCESS_DRIFT_VECTORS_ONLY, file=sys.stderr)
            _emit_reprocess_outcome(payload, selective_tty_mode="vectors" if ok else None)
            return _reprocess_exit_code(payload)

        if graph_only:
            g = run_build_ast_graph(
                source_root=cfg.source_root,
                kuzu_path=cfg.kuzu_path,
                verbose=not args.quiet,
                env=env,
            )
            if _is_graph_preflight_blocker(g):
                payload = {
                    "success": False,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "message": g.stderr.strip() or f"graph builder setup exit {g.returncode}",
                    "graph_exit_code": None,
                    "graph_stdout": clip(g.stdout, 4000),
                    "graph_stderr": clip(g.stderr, 4000),
                    "phases_run": [],
                }
                _emit_reprocess_outcome(payload)
                return _reprocess_exit_code(payload)
            ok = g.returncode == 0
            payload = {
                "success": ok,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": None if ok else f"graph builder exit {g.returncode}",
                "graph_exit_code": g.returncode,
                "graph_stdout": clip(g.stdout, 4000),
                "graph_stderr": clip(g.stderr, 4000),
                "phases_run": ["graph"],
            }
            if ok:
                print(_reprocess_drift_graph_only_line(cfg.index_dir), file=sys.stderr)
            _emit_reprocess_outcome(payload, selective_tty_mode="graph" if ok else None)
            return _reprocess_exit_code(payload)

        import server  # lazy: pulls sentence_transformers/torch/lancedb/kuzu

        result = asyncio.run(server.run_refresh_pipeline(quiet=bool(args.quiet)))
        payload = result.model_dump()
        _emit_reprocess_outcome(payload)
        return _reprocess_exit_code(payload)

    return _run_with_pipeline_progress("reprocess", cfg, quiet=bool(args.quiet), work=work)


def _cmd_erase(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    to_describe: list[Path] = [cfg.kuzu_path, cfg.cocoindex_db]
    if cfg.index_dir.is_dir():
        try:
            import lancedb

            db = lancedb.connect(str(cfg.index_dir.resolve()))
            for name in db.table_names():
                to_describe.append(cfg.index_dir / name)
        except Exception:
            pass
    rows = describe_path_sizes(to_describe)
    summary_lines = [f"  {p}: {sz} bytes" for p, sz in rows] or ["  (nothing to delete under resolved index dir)"]
    print("Will delete:", file=sys.stderr)
    print("\n".join(summary_lines), file=sys.stderr)
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "java-codebase-rag erase: non-interactive stdin; pass --yes to confirm.",
                file=sys.stderr,
            )
            return 2
        ans = input("Delete these paths? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2

    def work() -> int:
        env = cfg.subprocess_env()
        drop = run_cocoindex_drop(env, quiet=bool(args.quiet))
        if drop.returncode == 127:
            print(
                "java-codebase-rag erase: cocoindex CLI not found next to this Python; "
                "skipped `cocoindex drop` — cocoindex.db (if any) was not removed by CocoIndex.",
                file=sys.stderr,
            )
        elif drop.returncode != 0:
            print(clip(drop.stderr, 4000), file=sys.stderr)
        if cfg.kuzu_path.exists():
            shutil.rmtree(cfg.kuzu_path, ignore_errors=True)
        if cfg.cocoindex_db.exists():
            try:
                cfg.cocoindex_db.unlink()
            except OSError:
                pass
        if cfg.index_dir.is_dir():
            try:
                import lancedb

                db = lancedb.connect(str(cfg.index_dir.resolve()))
                for name in list(db.table_names()):
                    try:
                        db.drop_table(name)
                    except Exception as exc:
                        print(f"warning: failed to drop Lance table {name!r}: {exc}", file=sys.stderr)
            except Exception:
                pass
        _emit({"success": True, "message": "erase completed"})
        return 0

    return _run_with_pipeline_progress("erase", cfg, quiet=bool(args.quiet), work=work)


def _cmd_meta(args: argparse.Namespace) -> int:
    import server  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from kuzu_queries import KuzuGraph  # lazy

    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    payload = server._graph_meta_output().model_dump()
    payload["embedding_model"] = cfg.embedding_model
    payload["embedding_device"] = cfg.embedding_device
    payload["embedding_model_source"] = cfg.embedding_model_source
    payload["embedding_device_source"] = cfg.embedding_device_source
    payload["index_dir"] = str(cfg.index_dir.resolve())
    payload["kuzu_path"] = str(cfg.kuzu_path.resolve())
    payload["index_dir_source"] = cfg.index_dir_source
    payload["hints_enabled"] = cfg.hints_enabled
    payload["hints_enabled_source"] = cfg.hints_enabled_source
    _emit(payload)
    return 0 if payload.get("success") else 2


def _cmd_tables(args: argparse.Namespace) -> int:
    import server  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    payload = server.list_code_index_tables_payload().model_dump()
    _emit(payload)
    return 0


def _cmd_diagnose_ignore(args: argparse.Namespace) -> int:
    import server  # lazy
    from path_filtering import LayeredIgnore  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
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


def _cmd_unresolved_calls_list(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from kuzu_queries import KuzuGraph  # lazy

    if not KuzuGraph.exists():
        _emit({"success": False, "message": "Kuzu graph not found"})
        return 1
    graph = KuzuGraph.get()
    rows = graph.list_unresolved_call_sites(
        method_id=args.method_id,
        reason=args.reason,
        microservice=args.microservice,
        callee_simple=args.callee_simple,
        limit=int(args.limit),
    )
    _emit({"success": True, "count": len(rows), "sites": rows})
    return 0


def _cmd_unresolved_calls_stats(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from kuzu_queries import KuzuGraph  # lazy

    if not KuzuGraph.exists():
        _emit({"success": False, "message": "Kuzu graph not found"})
        return 1
    graph = KuzuGraph.get()
    buckets = graph.stats_unresolved_call_sites(by=args.by)
    total = sum(int(r.get("n") or 0) for r in buckets)
    _emit({"success": True, "total": total, "by": args.by, "buckets": buckets})
    return 0


def _cmd_analyze_pr(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    try:
        diff_text = _read_diff_text(args)
    except Exception as exc:
        _emit({"success": False, "message": str(exc)})
        return 1
    if not diff_text.strip():
        _emit({"success": False, "message": "Diff is empty"})
        return 1
    import pr_analysis  # lazy
    from kuzu_queries import KuzuGraph  # lazy

    if not KuzuGraph.exists():
        _emit({"success": False, "message": "Kuzu graph not found"})
        return 1
    graph = KuzuGraph.get()
    report = pr_analysis.analyze_pr_pipeline(graph, diff_text)
    _emit(pr_analysis.pr_report_to_dict(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    description = (
        "java-codebase-rag — graph-native code intelligence for Java microservices.\n\n"
        "Lifecycle commands stream subprocess progress to stderr (including relayed child stdout); "
        "--quiet suppresses that stream; stdout remains the machine-readable payload.\n\n"
        "Lifecycle (manage the index):\n"
        "  init            Create a fresh index from a Java repository.\n"
        "  increment       Pick up changes since the last index update (Lance only).\n"
        "  reprocess       Full vector + graph rebuild (default); optional --vectors-only / --graph-only.\n"
        "  erase           Delete the index from disk.\n\n"
        "Introspection (inspect the index):\n"
        "  meta            Print ontology version, edge counts, and table summary.\n"
        "  tables          List Lance tables and row counts.\n"
        "  diagnose-ignore Show which ignore-pattern layer decided a path's fate.\n"
        "  unresolved-calls  List or aggregate receiver-failure call sites (not in CALLS).\n\n"
        "Analysis (work with code changes):\n"
        "  analyze-pr      Compute blast-radius + risk score for a unified diff.\n\n"
        "Run `java-codebase-rag <command> --help` for command-specific options."
    )
    parser = argparse.ArgumentParser(
        prog="java-codebase-rag",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        exit_on_error=False,
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    init = subparsers.add_parser(
        "init",
        help="Create a fresh index from a Java repository.",
        description=(
            "First-time index creation. Refuses if the resolved index directory "
            "already contains a Kuzu graph or Lance tables. Exit 2 on refusal."
        ),
    )
    _add_index_embedding_flags(init)
    init.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress relay; stdout payload unchanged.",
    )
    init.set_defaults(handler=_cmd_init)

    increment = subparsers.add_parser(
        "increment",
        help="Pick up changes since the last index update.",
        description="Runs cocoindex catch-up (no full reprocess). Does not rebuild Kuzu; see stderr warning.",
    )
    _add_index_embedding_flags(increment)
    increment.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress relay; stdout payload unchanged.",
    )
    increment.set_defaults(handler=_cmd_increment)

    reprocess = subparsers.add_parser(
        "reprocess",
        help="Rebuild vectors and/or Kuzu (default: both full phases).",
        description=(
            "Default: full Lance reprocess (cocoindex --full-reprocess) then full Kuzu graph rebuild. "
            "Use --vectors-only or --graph-only to run a single phase (mutually exclusive)."
        ),
    )
    _add_index_embedding_flags(reprocess)
    reprocess.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress relay; stdout payload unchanged.",
    )
    _rex = reprocess.add_mutually_exclusive_group()
    _rex.add_argument(
        "--vectors-only",
        action="store_true",
        help="Run only the Lance/cocoindex full reprocess phase (no graph builder).",
    )
    _rex.add_argument(
        "--graph-only",
        action="store_true",
        help="Run only build_ast_graph.py (no cocoindex / Lance reprocess).",
    )
    reprocess.set_defaults(handler=_cmd_reprocess)

    erase = subparsers.add_parser(
        "erase",
        help="Delete the index from disk.",
        description="Runs cocoindex drop, removes Kuzu, and drops Lance tables. Requires --yes or TTY confirmation.",
    )
    _add_index_embedding_flags(erase)
    erase.add_argument("--yes", action="store_true", help="Confirm destructive deletion (required in CI)")
    erase.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress relay; stdout payload unchanged.",
    )
    erase.set_defaults(handler=_cmd_erase)

    meta = subparsers.add_parser("meta", help="Print graph meta and embedding resolution.")
    _add_index_embedding_flags(meta)
    meta.set_defaults(handler=_cmd_meta)

    tables = subparsers.add_parser("tables", help="List Lance tables and row counts.")
    _add_index_embedding_flags(tables)
    tables.set_defaults(handler=_cmd_tables)

    diagnose = subparsers.add_parser(
        "diagnose-ignore",
        help="Show which ignore-pattern layer decided the fate of a path.",
    )
    _add_index_embedding_flags(diagnose)
    diagnose.add_argument("path", type=str)
    diagnose.set_defaults(handler=_cmd_diagnose_ignore)

    analyze = subparsers.add_parser("analyze-pr", help="Blast-radius + risk score for a unified diff.")
    _add_index_embedding_flags(analyze)
    group = analyze.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff-file", type=str)
    group.add_argument("--diff-stdin", action="store_true")
    analyze.set_defaults(handler=_cmd_analyze_pr)

    unresolved = subparsers.add_parser(
        "unresolved-calls",
        help="List or aggregate UnresolvedCallSite rows (receiver-failure call sites).",
    )
    _add_index_embedding_flags(unresolved)
    unresolved_sub = unresolved.add_subparsers(dest="unresolved_command", required=True)

    uc_list = unresolved_sub.add_parser("list", help="List unresolved call sites.")
    _add_index_embedding_flags(uc_list)
    uc_list.add_argument("--method-id", type=str, default=None, help="Caller Symbol id")
    uc_list.add_argument(
        "--reason",
        type=str,
        default=None,
        choices=sorted(VALID_UNRESOLVED_CALL_REASONS),
        help="Filter by UnresolvedCallSite.reason",
    )
    uc_list.add_argument("--microservice", type=str, default=None)
    uc_list.add_argument("--callee-simple", type=str, default=None, dest="callee_simple")
    uc_list.add_argument("--limit", type=int, default=100)
    uc_list.set_defaults(handler=_cmd_unresolved_calls_list)

    uc_stats = unresolved_sub.add_parser("stats", help="Aggregate unresolved call site counts.")
    _add_index_embedding_flags(uc_stats)
    uc_stats.add_argument(
        "--by",
        type=str,
        choices=("reason", "microservice", "caller_role"),
        default="reason",
    )
    uc_stats.set_defaults(handler=_cmd_unresolved_calls_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    if raw and raw[0] == "refresh":
        print(_REFRESH_DEPRECATION, file=sys.stderr)
        raw[0] = "reprocess"
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        return int(e.code) if isinstance(e.code, int) else 2
    except argparse.ArgumentError as exc:
        print(f"java-codebase-rag: {exc}", file=sys.stderr)
        return 2
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        _emit({"success": False, "exit_code": 2, "message": f"internal error: {exc}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
