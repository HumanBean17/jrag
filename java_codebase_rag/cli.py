from __future__ import annotations

# Heavy imports (`server`, `pr_analysis`, `path_filtering.LayeredIgnore`,
# `build_ast_graph`) stay lazy inside handlers so `java-codebase-rag --help` stays fast.

import argparse
import asyncio
import json
import os
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
from java_codebase_rag._fdlimit import raise_fd_limit
from java_codebase_rag.pipeline import (
    clip,
    is_cocoindex_preflight_blocker,
    is_graph_preflight_blocker,
    run_build_ast_graph,
    run_cocoindex_drop,
    run_cocoindex_update,
    run_incremental_graph,
)
from java_ontology import VALID_UNRESOLVED_CALL_REASONS

LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL = "https://github.com/HumanBean17/java-codebase-rag/issues/73"

_INCREMENT_WARNING_LINES = (
    "WARNING: AST graph (LadybugDB) incremental rebuild is not yet implemented.",
    "The graph reflects the index state from the last `init` or `reprocess`,",
    "which means `find`, `neighbors`, and `describe` may return stale results",
    "for files changed since then.",
    "",
    "Lance vector index has been updated incrementally and is current.",
    "",
    "For an up-to-date graph, run:",
    "    java-codebase-rag reprocess",
    "",
    "Track progress on LadybugDB incremental rebuild:",
    f"    {LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL}",
)

_REFRESH_DEPRECATION = (
    "WARN: 'refresh' is deprecated; use 'reprocess'. "
    "This alias will be removed in the next release."
)

_REPROCESS_DRIFT_VECTORS_ONLY = (
    "java-codebase-rag reprocess: rebuilt vectors only; graph (code_graph.lbug) was NOT rebuilt "
    "and may now reflect a stale source snapshot."
)


def _reprocess_drift_graph_only_line(index_dir: Path) -> str:
    return (
        "java-codebase-rag reprocess: rebuilt graph only; vectors (Lance tables under "
        f"{index_dir}) were NOT rebuilt and may now reflect a stale source snapshot."
    )


_VECTORS_SKIPPED_GRAPH_ONLY = (
    "java-codebase-rag: vectors skipped — vector stack not installed on this platform "
    "(graph-only mode). The graph is built/refreshed; semantic search is unavailable."
)


def _reprocess_exit_code(payload: dict[str, Any]) -> int:
    if payload.get("success"):
        return 0
    phases_run = payload.get("phases_run") or []
    if not phases_run:
        return 2
    return 1


# Preflight detection delegates to pipeline.is_cocoindex_preflight_blocker /
# is_graph_preflight_blocker, which are co-located with the stub CompletedProcess shapes
# they must match (missing cocoindex / flow / build_ast_graph.py).
def _is_cocoindex_preflight_blocker(coco: Any) -> bool:
    """True when ``run_cocoindex_update`` returned without spawning cocoindex."""
    return is_cocoindex_preflight_blocker(coco)


def _is_graph_preflight_blocker(g: Any) -> bool:
    """True when ``run_build_ast_graph`` returned without spawning the builder."""
    return is_graph_preflight_blocker(g)


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
    from java_codebase_rag.cli_format import bold

    root = cfg.source_root.resolve()
    idx = cfg.index_dir.resolve()
    print(
        bold(f"java-codebase-rag {subcommand} {_PIPELINE_SEP} source={root} {_PIPELINE_SEP} index={idx}"),
        file=sys.stderr,
        flush=True,
    )


def _pipeline_footer(subcommand: str, started: float, exit_code: int) -> None:
    from java_codebase_rag.cli_format import bold, styled_check, styled_cross

    elapsed = time.perf_counter() - started
    marker = styled_check() if exit_code == 0 else styled_cross()
    print(
        f"{marker} {bold(f'java-codebase-rag {subcommand} {_PIPELINE_SEP} finished in {elapsed:.2f}s')}"
        + (f" (exit={exit_code})" if exit_code != 0 else ""),
        file=sys.stderr,
        flush=True,
    )


def _run_with_pipeline_progress(
    subcommand: str,
    cfg: ResolvedOperatorConfig,
    *,
    quiet: bool,
    verbose: bool = False,
    work: Callable[["PipelineProgress | None"], int],
) -> int:
    """Run ``work`` under the unified progress renderer (default TTY mode only).

    ``work`` receives a :class:`PipelineProgress` whose ``on_progress`` callback
    should be forwarded to the graph/vectors pipeline helpers so their
    ``JCIRAG_PROGRESS`` events feed the renderer. In ``--quiet`` or ``--verbose``
    mode the context is ``None`` (no Live region: quiet is silent, verbose
    raw-relays subprocess output).
    """
    if quiet or verbose:
        return int(work(None))
    from java_codebase_rag.progress import build_index_progress_context

    # PR-3 owns all three tasks in order: Vectors → Optimize → Graph. The vectors
    # task is fed by the cocoindex child's per-file ticks + approximate total
    # (subprocess transport, parsed by ProgressRelay); the optimize task is fed
    # in-process by lance_optimize; the graph task is fed by the build_ast_graph
    # child (subprocess transport). A task only becomes visible/running once its
    # first event arrives.
    renderer, on_progress, console = build_index_progress_context()
    progress = PipelineProgress(renderer=renderer)
    progress.on_progress = on_progress
    progress.console = console

    _pipeline_header(subcommand, cfg)
    t0 = time.perf_counter()
    code = 0
    # start() always flips _started (the non-TTY fallback is a no-op for Live but
    # still needs the flag so apply() routes to the concise-line printer). The
    # TTY Live region is entered inside start() only when the console is a TTY.
    renderer.start()
    try:
        code = int(work(progress))
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
        renderer.stop()
        _pipeline_footer(subcommand, t0, code)


class PipelineProgress:
    """Progress context handed to ``work``: the renderer + a ready ``on_progress``.

    ``on_progress``/``console`` are wired by :func:`_run_with_pipeline_progress`
    and should be forwarded to the pipeline helpers' ``on_progress`` /
    ``on_progress_console`` parameters. ``console`` is the renderer's stderr
    ``rich.Console`` so the subprocess drain routes non-progress lines through
    ``console.print`` while the Live region is up (single-writer invariant).
    """

    def __init__(self, *, renderer: "object | None") -> None:
        self.renderer = renderer
        self.on_progress: "Callable | None" = None
        self.console: "object | None" = None


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


def _emit_increment_ladybug_warning() -> None:
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
    p.add_argument("--index-dir", type=str, default=None, help="Index directory (Lance + LadybugDB + cocoindex state)")
    p.add_argument("--embedding-model", type=str, default=None, help="Override SBERT_MODEL / YAML embedding.model")
    p.add_argument("--embedding-device", type=str, default=None, help="Override SBERT_DEVICE / YAML embedding.device")


def _add_verbosity_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--quiet", "-q",
        action="store_true",
        dest="quiet",
        help="Suppress stderr progress relay; stdout payload unchanged.",
    )
    g.add_argument(
        "--verbose", "-v",
        action="store_true",
        dest="verbose",
        help="Show full subprocess output (Lance warnings, brownfield events, progress bars).",
    )


def _cmd_init(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    # Check for parent config or index
    from java_codebase_rag.config import discover_project_root, find_yaml_config_file
    parent_config_dir = discover_project_root(cfg.source_root.parent)
    if parent_config_dir is not None:
        parent_config = find_yaml_config_file(parent_config_dir)
        if parent_config is not None:
            print(
                f"Warning: found existing config at {parent_config}. "
                f"Creating a new project here will create a separate index.",
                file=sys.stderr,
            )
        else:
            print(
                f"Warning: found existing index at {parent_config_dir / '.java-codebase-rag'}. "
                f"Creating a new project here will create a separate index.",
                file=sys.stderr,
            )
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

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        verbose = bool(args.verbose)
        coco = run_cocoindex_update(
            env,
            full_reprocess=False,
            quiet=bool(args.quiet),
            verbose=verbose,
            lance_project_root=None if args.quiet else cfg.source_root,
            on_progress=progress.on_progress if progress is not None else None,
            on_progress_console=progress.console if progress is not None else None,
        )
        # Graph-only install (cocoindex absent, e.g. macOS Intel): skip the vectors phase
        # and proceed to the graph build rather than failing — the graph layer is the
        # supported surface there. A genuine non-zero cocoindex exit still fails.
        vectors_skipped = _is_cocoindex_preflight_blocker(coco)
        if coco.returncode != 0 and not vectors_skipped:
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
        if vectors_skipped:
            print(_VECTORS_SKIPPED_GRAPH_ONLY, file=sys.stderr, flush=True)
        if not args.quiet:
            print(file=sys.stderr, flush=True)
        g = run_build_ast_graph(
            source_root=cfg.source_root,
            ladybug_path=cfg.ladybug_path,
            verbose=verbose,
            quiet=bool(args.quiet),
            env=env,
            on_progress=progress.on_progress if progress is not None else None,
            on_progress_console=progress.console if progress is not None else None,
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
        _emit(
            {
                "success": True,
                "message": (
                    "init completed (graph-only; vectors skipped — vector stack not installed)"
                    if vectors_skipped
                    else "init completed"
                ),
            }
        )
        return 0

    return _run_with_pipeline_progress(
        "init", cfg, quiet=bool(args.quiet), verbose=bool(args.verbose), work=work
    )


def _cmd_increment(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()

    # Check for --vectors-only flag
    vectors_only = bool(getattr(args, "vectors_only", False))
    if vectors_only:
        _emit_increment_ladybug_warning()

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        coco = run_cocoindex_update(
            env,
            full_reprocess=False,
            quiet=bool(args.quiet),
            verbose=bool(args.verbose),
            lance_project_root=None if args.quiet else cfg.source_root,
            on_progress=progress.on_progress if progress is not None else None,
            on_progress_console=progress.console if progress is not None else None,
        )
        vectors_skipped = _is_cocoindex_preflight_blocker(coco)
        if coco.returncode != 0 and not vectors_skipped:
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
        if vectors_skipped:
            print(_VECTORS_SKIPPED_GRAPH_ONLY, file=sys.stderr, flush=True)

        # If --vectors-only is set, skip graph update
        if vectors_only:
            if vectors_skipped:
                _emit(
                    {
                        "success": True,
                        "message": "increment skipped: vector stack not installed (graph-only mode)",
                    }
                )
                return 0
            _emit({"success": True, "message": "increment completed (Lance only; graph may be stale — see stderr)"})
            return 0

        # Run incremental graph update
        g = run_incremental_graph(
            source_root=cfg.source_root,
            ladybug_path=cfg.ladybug_path,
            verbose=bool(args.verbose),
            quiet=bool(args.quiet),
            env=env,
            on_progress=progress.on_progress if progress is not None else None,
            on_progress_console=progress.console if progress is not None else None,
        )

        # Check if incremental fell back to full rebuild
        if g.returncode == 0 and g.stdout:
            # Parse stdout to check for full_fallback mode
            # The incremental_rebuild function returns a JSON payload with mode field
            try:
                result = json.loads(g.stdout.strip())
                if result.get("mode") == "full_fallback":
                    print(
                        "[increment] fell back to full graph rebuild — this is normal after schema changes or first run",
                        file=sys.stderr,
                        flush=True,
                    )
            except (json.JSONDecodeError, ValueError):
                # If parsing fails, continue silently
                pass

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

        _emit(
            {
                "success": True,
                "message": (
                    "increment completed (graph only; vectors skipped — vector stack not installed)"
                    if vectors_skipped
                    else "increment completed (Lance + graph updated)"
                ),
            }
        )
        return 0

    return _run_with_pipeline_progress(
        "increment", cfg, quiet=bool(args.quiet), verbose=bool(args.verbose), work=work
    )


def _cmd_reprocess(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        verbose = bool(args.verbose)
        vectors_only = bool(getattr(args, "vectors_only", False))
        graph_only = bool(getattr(args, "graph_only", False))

        if vectors_only:
            coco = run_cocoindex_update(
                env, full_reprocess=True, quiet=bool(args.quiet), verbose=verbose,
                on_progress=progress.on_progress if progress is not None else None,
                on_progress_console=progress.console if progress is not None else None,
            )
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
                ladybug_path=cfg.ladybug_path,
                verbose=verbose,
                quiet=bool(args.quiet),
                env=env,
                on_progress=progress.on_progress if progress is not None else None,
                on_progress_console=progress.console if progress is not None else None,
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

        import server  # lazy: pulls sentence_transformers/torch/lancedb/ladybug

        result = asyncio.run(
            server.run_refresh_pipeline(
                quiet=bool(args.quiet),
                verbose=verbose,
                on_progress=progress.on_progress if progress is not None else None,
                on_progress_console=progress.console if progress is not None else None,
            )
        )
        payload = result.model_dump()
        _emit_reprocess_outcome(payload)
        return _reprocess_exit_code(payload)

    return _run_with_pipeline_progress(
        "reprocess", cfg, quiet=bool(args.quiet), verbose=bool(args.verbose), work=work
    )


def _cmd_install(args: argparse.Namespace) -> int:
    from java_codebase_rag.installer import run_install

    return run_install(
        non_interactive=bool(args.non_interactive),
        agents=args.agent,  # list of str (may be empty)
        scope=args.scope,
        model=args.model,
        surface=args.surface,
        source_root=None,  # None means cwd; installer confirms interactively
        quiet=bool(args.quiet),
        verbose=bool(args.verbose),
    )


def _cmd_update(args: argparse.Namespace) -> int:
    from java_codebase_rag.installer import run_update

    return run_update(
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        quiet=bool(args.quiet),
        verbose=bool(args.verbose),
    )


def _rm_any(path: Path) -> None:
    """Remove ``path`` whether it is a regular file, directory, or symlink.

    ``code_graph.lbug`` is a single regular file in this repo, but kuzu may lay
    the graph out as a directory; ``cocoindex.db`` is always a directory.
    ``shutil.rmtree`` is a silent no-op on a regular file and ``Path.unlink``
    raises ``IsADirectoryError`` on a directory, so a type-blind delete left
    index artifacts on disk (issue #346). A symlinked directory is unlinked, not
    recursed into, so the link target is never followed. Failures are warned to
    stderr rather than swallowed, so erase does not report success while leaving
    an artifact behind (the exact failure mode issue #346 reported).
    """
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"warning: failed to remove {path}: {exc}", file=sys.stderr)


def _cmd_erase(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    # Lazy import: build_ast_graph transitively pulls numpy/ladybug/pyarrow/
    # tree_sitter (~54ms), and these filenames are only needed on the erase path.
    # Keeping it out of the top-level import lets `java-codebase-rag --help` (and
    # every other command) stay fast -- see the lazy-import invariant atop this file.
    from build_ast_graph import BUILDER_OWNED_INDEX_FILES
    builder_paths = [cfg.ladybug_path.parent / name for name in BUILDER_OWNED_INDEX_FILES]
    to_describe: list[Path] = [cfg.ladybug_path, cfg.cocoindex_db, *builder_paths]
    if cfg.index_dir.is_dir():
        try:
            import lancedb

            db = lancedb.connect(str(cfg.index_dir.resolve()))
            for name in db.list_tables():
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
        try:
            ans = input("Delete these paths? [y/N]: ").strip().lower()
        except EOFError:
            # Non-interactive stdin that nonetheless reported isatty() == True
            # (the Windows NUL device is a character device, so isatty() lies).
            # Treat it as a refusal instead of crashing with an EOF traceback.
            print(
                "java-codebase-rag erase: non-interactive stdin; pass --yes to confirm.",
                file=sys.stderr,
            )
            return 2
        if ans not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2

    def work(progress: "PipelineProgress | None") -> int:
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
        # Remove the LadybugDB graph, the cocoindex state store, and every
        # builder-owned bookkeeping file next to code_graph.lbug (the content-hash
        # store, its atomic-write temp, and the incremental crash marker). Each is
        # removed by type (see _rm_any): code_graph.lbug is a file here but may be
        # a dir under kuzu, while cocoindex.db is a directory — a type-blind delete
        # silently no-oped on one or the other, and the builder files were never
        # targeted at all (issues #346 / #349 / #350). The list comes from
        # build_ast_graph.BUILDER_OWNED_INDEX_FILES so erase and the builder cannot drift.
        _rm_any(cfg.ladybug_path)
        _rm_any(cfg.cocoindex_db)
        for builder_path in builder_paths:
            _rm_any(builder_path)
        if cfg.index_dir.is_dir():
            try:
                import lancedb

                db = lancedb.connect(str(cfg.index_dir.resolve()))
                for name in list(db.list_tables()):
                    try:
                        db.drop_table(name)
                    except Exception as exc:
                        print(f"warning: failed to drop Lance table {name!r}: {exc}", file=sys.stderr)
            except Exception:
                pass
        _emit({"success": True, "message": "erase completed"})
        return 0

    return _run_with_pipeline_progress("erase", cfg, quiet=bool(args.quiet), verbose=bool(getattr(args, "verbose", False)), work=work)


def _cmd_meta(args: argparse.Namespace) -> int:
    import server  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from ladybug_queries import LadybugGraph  # lazy

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    payload = server._graph_meta_output().model_dump()
    payload["embedding_model"] = cfg.embedding_model
    payload["embedding_device"] = cfg.embedding_device
    payload["embedding_model_source"] = cfg.embedding_model_source
    payload["embedding_device_source"] = cfg.embedding_device_source
    payload["index_dir"] = str(cfg.index_dir.resolve())
    payload["ladybug_path"] = str(cfg.ladybug_path.resolve())
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
    from ladybug_queries import LadybugGraph  # lazy

    if not LadybugGraph.exists():
        _emit({"success": False, "message": "LadybugDB graph not found"})
        return 1
    graph = LadybugGraph.get()
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
    from ladybug_queries import LadybugGraph  # lazy

    if not LadybugGraph.exists():
        _emit({"success": False, "message": "LadybugDB graph not found"})
        return 1
    graph = LadybugGraph.get()
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
    from ladybug_queries import LadybugGraph  # lazy

    if not LadybugGraph.exists():
        _emit({"success": False, "message": "LadybugDB graph not found"})
        return 1
    graph = LadybugGraph.get()
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
        "  increment       Pick up changes since the last index update (Lance + graph).\n"
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
            "already contains a LadybugDB graph or Lance tables. Exit 2 on refusal."
        ),
    )
    _add_index_embedding_flags(init)
    _add_verbosity_flags(init)
    init.set_defaults(handler=_cmd_init)

    install = subparsers.add_parser(
        "install",
        help="Interactive setup wizard: config, MCP registration, skill/agent deployment, indexing.",
        description=(
            "Interactive setup wizard that guides users through: Java source detection, "
            "embedding model selection, agent host configuration, artifact deployment, "
            "and YAML config generation. Use --non-interactive for CI/automation."
        ),
    )
    install.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without prompts (requires --agent).",
    )
    install.add_argument(
        "--agent",
        choices=["claude-code", "qwen-code", "gigacode"],
        default=[],
        action="append",
        help="Agent host to configure (can be passed multiple times).",
    )
    install.add_argument(
        "--scope",
        choices=["project", "user"],
        default=None,
        help="Installation scope (default: project).",
    )
    install.add_argument(
        "--model",
        type=str,
        default=None,
        help="Embedding model path or 'auto' (default: auto).",
    )
    install.add_argument(
        "--surface",
        choices=["mcp", "cli"],
        default=None,
        help=(
            "Agent surface to install: 'mcp' (stdio MCP server + explore-codebase "
            "skill + explorer-rag-enhanced subagent) or 'cli' (jrag console-script "
            "skill + explorer-rag-cli subagent, no MCP entry). Omit to choose "
            "interactively; non-interactive mode defaults to 'mcp'."
        ),
    )
    _add_verbosity_flags(install)
    install.set_defaults(handler=_cmd_install)

    update = subparsers.add_parser(
        "update",
        help="Refresh shipped artifacts (skill, agent, MCP entry) after pip upgrade.",
        description=(
            "Post-upgrade refresh: overwrites skill and agent files with the latest "
            "shipped versions and updates the MCP command path. If an index exists, "
            "also runs an incremental Lance + graph catch-up (same as `increment`). "
            "Use --dry-run to preview changes without writing. Requires a prior `install` run."
        ),
    )
    update.add_argument(
        "--force",
        action="store_true",
        help="Overwrite all artifacts even if content matches.",
    )
    update.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing files.",
    )
    _add_verbosity_flags(update)
    update.set_defaults(handler=_cmd_update)

    increment = subparsers.add_parser(
        "increment",
        help="Pick up changes since the last index update.",
        description="Runs cocoindex catch-up and incremental LadybugDB graph update. Use --vectors-only to skip graph update.",
    )
    _add_index_embedding_flags(increment)
    _add_verbosity_flags(increment)
    increment.add_argument(
        "--vectors-only",
        action="store_true",
        help="Run only cocoindex catch-up (Lance); skip graph update.",
    )
    increment.set_defaults(handler=_cmd_increment)

    reprocess = subparsers.add_parser(
        "reprocess",
        help="Rebuild vectors and/or LadybugDB (default: both full phases).",
        description=(
            "Default: full Lance reprocess (cocoindex --full-reprocess) then full LadybugDB graph rebuild. "
            "Use --vectors-only or --graph-only to run a single phase (mutually exclusive)."
        ),
    )
    _add_index_embedding_flags(reprocess)
    _add_verbosity_flags(reprocess)
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
        description="Runs cocoindex drop, removes LadybugDB, and drops Lance tables. Requires --yes or TTY confirmation.",
    )
    _add_index_embedding_flags(erase)
    erase.add_argument("--yes", action="store_true", help="Confirm destructive deletion (required in CI)")
    erase.add_argument(
        "--quiet", "-q",
        action="store_true",
        dest="quiet",
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
    raise_fd_limit()
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


def _console_script_main() -> None:
    """Real CLI entry: terminate without interpreter finalization.

    A pyarrow/lance worker thread (loaded via lancedb in lifecycle commands) can
    outlive CPython finalization in a one-shot CLI subprocess and trip
    ``PyGILState_Release`` (SIGABRT, exit -6). Flushing + ``os._exit`` skips that
    racy teardown — the command has already done its work and emitted its result.
    ``main()`` stays return-based so in-process test callers (``cli.main(...)``)
    keep working.
    """
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    _console_script_main()
