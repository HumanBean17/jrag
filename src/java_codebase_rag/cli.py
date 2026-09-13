from __future__ import annotations

# Heavy imports (`server`, `pr_analysis`, `path_filtering.LayeredIgnore`,
# `build_ast_graph`) stay lazy inside handlers so `jrag --help` stays fast.

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
    OPERATOR_OWNED_INDEX_FILES,
    ResolvedOperatorConfig,
    describe_path_sizes,
    emit_legacy_env_hints_if_present,
    emit_legacy_yaml_hint_if_needed,
    index_dir_has_existing_artifacts,
    resolve_operator_config,
    retrieval_mode_from_env,
    write_config_source_pointer,
)
from java_codebase_rag._fdlimit import raise_fd_limit
from java_codebase_rag._version import version_string
from java_codebase_rag.i18n import tr
from java_codebase_rag.pipeline import (
    RETRIEVAL_BM25_HINT as _RETRIEVAL_BM25_HINT,
    VECTORS_SKIPPED_BM25 as _VECTORS_SKIPPED_BM25,
    VECTORS_SKIPPED_GRAPH_ONLY as _VECTORS_SKIPPED_GRAPH_ONLY,
    retrieval_bm25_hint as _pipeline_retrieval_bm25_hint,
    vectors_skipped_bm25 as _vectors_skipped_bm25,
    vectors_skipped_graph_only as _vectors_skipped_graph_only,
    clip,
    is_cocoindex_preflight_blocker,
    is_graph_preflight_blocker,
    run_build_ast_graph,
    run_cocoindex_drop,
    run_cocoindex_update,
    run_incremental_graph,
)
from java_codebase_rag.graph.java_ontology import VALID_UNRESOLVED_CALL_REASONS

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
    "    jrag reprocess",
    "",
    "Track progress on LadybugDB incremental rebuild:",
    f"    {LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL}",
)

_REFRESH_DEPRECATION = (
    "WARN: 'refresh' is deprecated; use 'reprocess'. "
    "This alias will be removed in the next release."
)

_REPROCESS_DRIFT_VECTORS_ONLY = (
    "jrag reprocess: rebuilt vectors only; graph (code_graph.lbug) was NOT rebuilt "
    "and may now reflect a stale source snapshot."
)


# Localized lazy twins of the frozen module constants above (the constants
# stay English for any consumer that pins them; runtime CLI paths localize).
# Same pattern as pipeline.vectors_skipped_* — see the note there.
def _increment_warning_lines() -> list[str]:
    from java_codebase_rag.i18n import tr

    return tr(
        "MSG_INCREMENT_WARNING", url=LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL
    ).split("\n")


def _refresh_deprecation() -> str:
    from java_codebase_rag.i18n import tr

    return tr("MSG_REFRESH_DEPRECATION")


def _reprocess_drift_vectors_only() -> str:
    from java_codebase_rag.i18n import tr

    return tr("MSG_REPROCESS_DRIFT_VECTORS_ONLY")


def _reprocess_drift_graph_only_line(index_dir: Path) -> str:
    from java_codebase_rag.i18n import tr

    return tr("MSG_REPROCESS_DRIFT_GRAPH_ONLY", index_dir=index_dir)


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
    from java_codebase_rag.i18n import tr

    if mode == "vectors":
        print(tr("MSG_REBUILT_VECTORS"))
        print(tr("MSG_SKIPPED_GRAPH"))
    else:
        print(tr("MSG_REBUILT_GRAPH"))
        print(tr("MSG_SKIPPED_VECTORS"))


def _reprocess_success_message(mode: str | None, payload: dict[str, Any]) -> str:
    """Concise message for a successful reprocess.

    Prefers an explicit ``message`` set by the pipeline (e.g. the graph-only-
    install note from ``run_refresh_pipeline``); otherwise derives from the
    selective mode. Mirrors ``init``/``increment``, which emit a one-line
    success payload rather than re-dumping captured subprocess logs.
    """
    explicit = payload.get("message")
    if isinstance(explicit, str) and explicit:
        return explicit
    from java_codebase_rag.i18n import tr

    if mode == "vectors":
        return tr("MSG_REPROCESS_COMPLETED_VECTORS")
    if mode == "graph":
        return tr("MSG_REPROCESS_COMPLETED_GRAPH")
    return tr("MSG_REPROCESS_COMPLETED")


def _emit_reprocess_outcome(payload: dict[str, Any], *, selective_tty_mode: str | None = None) -> None:
    if not payload.get("success"):
        # Failure: emit the full payload so captured subprocess stdout/stderr
        # (cocoindex/graph logs) survive for debugging.
        _emit(payload)
        return
    # Success: the progress renderer / stderr already surfaced the phase logs,
    # so emit a concise structured payload (matching init/increment) instead of
    # re-dumping them as JSON noise. In a TTY the partial modes additionally
    # print a one-line Rebuilt/Skipped summary.
    if selective_tty_mode and sys.stdout.isatty():
        _emit_reprocess_selective_tty(mode=selective_tty_mode)
        return
    _emit({"success": True, "message": _reprocess_success_message(selective_tty_mode, payload)})


_PIPELINE_SEP = "\u00b7"


def _pipeline_header(subcommand: str, cfg: ResolvedOperatorConfig) -> None:
    from java_codebase_rag.cli_format import bold

    root = cfg.source_root.resolve()
    idx = cfg.index_dir.resolve()
    print(
        bold(f"jrag {subcommand} {_PIPELINE_SEP} source={root} {_PIPELINE_SEP} index={idx}"),
        file=sys.stderr,
        flush=True,
    )


def _pipeline_footer(subcommand: str, started: float, exit_code: int) -> None:
    from java_codebase_rag.cli_format import bold, styled_check, styled_cross

    elapsed = time.perf_counter() - started
    marker = styled_check() if exit_code == 0 else styled_cross()
    print(
        f"{marker} {bold(f'jrag {subcommand} {_PIPELINE_SEP} finished in {elapsed:.2f}s')}"
        + (f" (exit={exit_code})" if exit_code != 0 else ""),
        file=sys.stderr,
        flush=True,
    )


# Subcommands that build/refresh an index and therefore should record which YAML
# was used (so a later discovery run from a sibling/cwd can relocate it).
_CONFIG_SOURCE_RECORDING_SUBCOMMANDS = frozenset({"init", "increment", "reprocess"})


def _maybe_record_config_source(
    subcommand: str, cfg: ResolvedOperatorConfig, code: int
) -> None:
    """On a successful index build, remember the YAML path inside the index dir."""
    if code == 0 and subcommand in _CONFIG_SOURCE_RECORDING_SUBCOMMANDS:
        write_config_source_pointer(
            index_dir=cfg.index_dir, yaml_config_path=cfg.yaml_config_path
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
        code = int(work(None))
        _maybe_record_config_source(subcommand, cfg, code)
        return code
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
        _maybe_record_config_source(subcommand, cfg, code)


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
    for line in _increment_warning_lines():
        print(line, file=sys.stderr)


def _parse_source_root(ns: argparse.Namespace) -> Path | None:
    if ns.source_root:
        return Path(ns.source_root).expanduser().resolve()
    return None


def _resolved_from_ns(ns: argparse.Namespace) -> ResolvedOperatorConfig:
    from java_codebase_rag.i18n import cli_lang_override, set_locale

    root = _parse_source_root(ns)
    cfg = resolve_operator_config(
        source_root=root,
        cli_index_dir=ns.index_dir,
        cli_embedding_model=getattr(ns, "embedding_model", None),
        cli_embedding_device=getattr(ns, "embedding_device", None),
        # Interface language: the after-verb flag, else the dispatch pre-scan
        # stash (before-verb flag, stripped from argv before parse).
        cli_language=getattr(ns, "lang", None) or cli_lang_override(),
    )
    # Authoritative locale post-resolution (flag > env > YAML > default).
    set_locale(cfg.language)
    return cfg


def _startup_hints(cfg: ResolvedOperatorConfig) -> None:
    emit_legacy_env_hints_if_present()
    emit_legacy_yaml_hint_if_needed(cfg.source_root)


def _add_index_embedding_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source-root", type=str, default=None, help=tr("HELP_FLAG_SOURCE_ROOT"))
    p.add_argument("--index-dir", type=str, default=None, help=tr("HELP_MISC_73"))
    p.add_argument("--embedding-model", type=str, default=None, help=tr("HELP_FLAG_EMBEDDING_MODEL"))
    p.add_argument("--embedding-device", type=str, default=None, help=tr("HELP_FLAG_EMBEDDING_DEVICE"))


def _add_verbosity_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--quiet", "-q",
        action="store_true",
        dest="quiet",
        help=tr("HELP_FLAG_QUIET"),
    )
    g.add_argument(
        "--verbose", "-v",
        action="store_true",
        dest="verbose",
        help=tr("HELP_FLAG_VERBOSE"),
    )


def _add_lang_flag(p: argparse.ArgumentParser) -> None:
    """Register the interface-language flag on an operator subparser (spec D3).

    The before-verb form is handled by the dispatch pre-scan
    (``cli_dispatch``), which strips and stashes it before routing.
    """
    from java_codebase_rag.i18n import tr

    p.add_argument(
        "--lang",
        "-L",
        choices=("en", "ru"),
        dest="lang",
        default=None,
        help=tr("HELP_FLAG_LANG"),
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
                tr(
                    "MSG_WARN_EXISTING_CONFIG",
                    path=parent_config,
                ),
                file=sys.stderr,
            )
        else:
            print(
                tr(
                    "MSG_WARN_EXISTING_INDEX",
                    path=parent_config_dir / ".java-codebase-rag",
                ),
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
                    "Use `jrag reprocess` to rebuild in place, "
                    "or `jrag erase --yes` then `init` for a clean slate."
                ),
                "non_empty_paths": paths,
            }
        )
        return 2
    cfg.index_dir.mkdir(parents=True, exist_ok=True)

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        verbose = bool(args.verbose)
        bm25_mode = cfg.retrieval == "bm25"
        vectors_skipped = False
        if bm25_mode:
            # bm25 retrieval: there are no vectors to build, so cocoindex is never
            # spawned. Same operator-facing skip line and graph-only proceed as the
            # stack-absent branch below.
            print(_vectors_skipped_bm25(), file=sys.stderr, flush=True)
        else:
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
                # Remediation hint (suppressed when the mode is already bm25 —
                # the guard is unreachable here today, kept honest on purpose).
                if retrieval_mode_from_env() != "bm25":
                    print(_pipeline_retrieval_bm25_hint(), file=sys.stderr, flush=True)
                return 1
            if vectors_skipped:
                print(_vectors_skipped_graph_only(), file=sys.stderr, flush=True)
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
        if bm25_mode:
            message = "init completed (graph-only; vectors skipped — retrieval mode is bm25)"
        elif vectors_skipped:
            message = "init completed (graph-only; vectors skipped — vector stack not installed)"
        else:
            message = "init completed"
        _emit({"success": True, "message": message})
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
    if vectors_only and cfg.retrieval != "bm25":
        # Under bm25 the vectors phase below is skipped, so the warning's
        # "Lance vector index ... is current" line would be false.
        _emit_increment_ladybug_warning()

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        bm25_mode = cfg.retrieval == "bm25"
        vectors_skipped = False
        if bm25_mode:
            # bm25 retrieval: there are no vectors to build, so cocoindex is never
            # spawned. Same operator-facing skip line as the stack-absent branch below.
            print(_vectors_skipped_bm25(), file=sys.stderr, flush=True)
        else:
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
                if retrieval_mode_from_env() != "bm25":
                    print(_pipeline_retrieval_bm25_hint(), file=sys.stderr, flush=True)
                return 1
            if vectors_skipped:
                print(_vectors_skipped_graph_only(), file=sys.stderr, flush=True)

        # If --vectors-only is set, skip graph update
        if vectors_only:
            if bm25_mode:
                _emit(
                    {
                        "success": True,
                        "message": "increment skipped: retrieval mode is bm25 (no vectors phase)",
                    }
                )
                return 0
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
                        tr(
                            "MSG_INCREMENT_FALLBACK",
                        ),
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

        if bm25_mode:
            message = "increment completed (graph only; vectors skipped — retrieval mode is bm25)"
        elif vectors_skipped:
            message = (
                "increment completed (graph only; vectors skipped — vector stack not installed)"
            )
        else:
            message = "increment completed (Lance + graph updated)"
        _emit({"success": True, "message": message})
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
        bm25_mode = cfg.retrieval == "bm25"

        if vectors_only:
            if bm25_mode:
                # No vectors phase exists under bm25: --vectors-only is a clean no-op.
                payload: dict[str, Any] = {
                    "success": True,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "message": "reprocess skipped: retrieval mode is bm25 (no vectors phase)",
                    "graph_exit_code": None,
                    "graph_stdout": "",
                    "graph_stderr": "",
                    "phases_run": [],
                }
                _emit_reprocess_outcome(payload)
                return _reprocess_exit_code(payload)
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
                print(_reprocess_drift_vectors_only(), file=sys.stderr)
            _emit_reprocess_outcome(payload, selective_tty_mode="vectors" if ok else None)
            if not ok and retrieval_mode_from_env() != "bm25":
                print(_pipeline_retrieval_bm25_hint(), file=sys.stderr, flush=True)
            return _reprocess_exit_code(payload)

        if graph_only or bm25_mode:
            # Under bm25 a full reprocess is a graph-only rebuild (there is no
            # vectors phase to refresh), so it rides the --graph-only path — but
            # says so with the bm25 skip line instead of the stale-vectors drift
            # note, which does not apply when no vectors exist.
            if bm25_mode:
                print(_vectors_skipped_bm25(), file=sys.stderr, flush=True)
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
                "message": (
                    tr("MSG_REPROCESS_COMPLETED_BM25")
                    if ok and bm25_mode
                    else None if ok
                    else f"graph builder exit {g.returncode}"
                ),
                "graph_exit_code": g.returncode,
                "graph_stdout": clip(g.stdout, 4000),
                "graph_stderr": clip(g.stderr, 4000),
                "phases_run": ["graph"],
            }
            if ok and not bm25_mode:
                print(_reprocess_drift_graph_only_line(cfg.index_dir), file=sys.stderr)
            _emit_reprocess_outcome(
                payload, selective_tty_mode=None if bm25_mode else ("graph" if ok else None)
            )
            return _reprocess_exit_code(payload)

        from java_codebase_rag.mcp import server  # lazy: pulls sentence_transformers/torch/lancedb/ladybug

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
        retrieval=getattr(args, "retrieval", None),
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
        surface=args.surface,
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
        from java_codebase_rag.i18n import tr

        print(tr("MSG_WARN_RM_FAILED", path=path, exc=exc), file=sys.stderr)


def _cmd_erase(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    # Lazy import: build_ast_graph transitively pulls numpy/ladybug/pyarrow/
    # tree_sitter (~54ms), and these filenames are only needed on the erase path.
    # Keeping it out of the top-level import lets `jrag --help` (and
    # every other command) stay fast -- see the lazy-import invariant atop this file.
    from java_codebase_rag.graph.build_ast_graph import BUILDER_OWNED_INDEX_FILES
    builder_paths = [cfg.ladybug_path.parent / name for name in BUILDER_OWNED_INDEX_FILES]
    operator_paths = [cfg.index_dir / name for name in OPERATOR_OWNED_INDEX_FILES]
    to_describe: list[Path] = [
        cfg.ladybug_path,
        cfg.cocoindex_db,
        *builder_paths,
        *operator_paths,
    ]
    if cfg.index_dir.is_dir():
        try:
            import lancedb

            db = lancedb.connect(str(cfg.index_dir.resolve()))
            for name in db.list_tables():
                to_describe.append(cfg.index_dir / name)
        except Exception:
            pass
    rows = describe_path_sizes(to_describe)
    from java_codebase_rag.i18n import tr

    summary_lines = (
        [f"  {p}: {sz} bytes" for p, sz in rows] or [tr("MSG_ERASE_NOTHING")]
    )
    print(tr("MSG_ERASE_WILL_DELETE"), file=sys.stderr)
    print("\n".join(summary_lines), file=sys.stderr)
    if not args.yes:
        if not sys.stdin.isatty():
            print(tr("MSG_ERASE_NON_INTERACTIVE"), file=sys.stderr)
            return 2
        try:
            ans = input(tr("MSG_ERASE_CONFIRM")).strip().lower()
        except EOFError:
            # Non-interactive stdin that nonetheless reported isatty() == True
            # (the Windows NUL device is a character device, so isatty() lies).
            # Treat it as a refusal instead of crashing with an EOF traceback.
            print(tr("MSG_ERASE_NON_INTERACTIVE"), file=sys.stderr)
            return 2
        if ans not in ("y", "yes"):
            print(tr("MSG_ERASE_ABORTED"), file=sys.stderr)
            return 2

    def work(progress: "PipelineProgress | None") -> int:
        env = cfg.subprocess_env()
        drop = run_cocoindex_drop(env, quiet=bool(args.quiet))
        if drop.returncode == 127:
            print(tr("MSG_ERASE_COCO_MISSING"), file=sys.stderr)
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
        # Operator-owned index files (the config_source pointer recording which
        # YAML built this index) — owned by the CLI/installer, not the graph
        # builder, so removed here rather than via BUILDER_OWNED_INDEX_FILES.
        for operator_path in operator_paths:
            _rm_any(operator_path)
        if cfg.index_dir.is_dir():
            try:
                # Scan-based: drops every *.lance dir, including tables the
                # store cannot list (e.g. a corrupt manifest) and the legacy
                # SQL/YAML dirs from pre-removal indexes.
                from java_codebase_rag.lance_optimize import drop_all_tables_by_scan

                dropped = drop_all_tables_by_scan(cfg.index_dir.resolve())
                if dropped and not bool(args.quiet):
                    from java_codebase_rag.i18n import tr as _tr

                    print(
                        _tr("MSG_ERASE_DROPPED", tables=", ".join(dropped)),
                        file=sys.stderr,
                    )
            except Exception:
                pass
        _emit({"success": True, "message": "erase completed"})
        return 0

    return _run_with_pipeline_progress("erase", cfg, quiet=bool(args.quiet), verbose=bool(getattr(args, "verbose", False)), work=work)


def _cmd_meta(args: argparse.Namespace) -> int:
    from java_codebase_rag.mcp import server  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph  # lazy

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
    from java_codebase_rag.mcp import server  # lazy

    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    payload = server.list_code_index_tables_payload().model_dump()
    _emit(payload)
    return 0


def _cmd_diagnose_ignore(args: argparse.Namespace) -> int:
    from java_codebase_rag.mcp import server  # lazy
    from java_codebase_rag.graph.path_filtering import LayeredIgnore  # lazy

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
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph  # lazy

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
    # Drop the raw caller symbol id: the row already carries the agent-facing
    # ``caller_fqn``, so ``caller_id`` is redundant noise in an operator-facing
    # report. The call-site ``id`` (``ucs:``) is kept — it's each site's
    # primary key, not a caller reference.
    sites = [
        {k: v for k, v in row.items() if k != "caller_id"}
        for row in rows
    ]
    _emit({"success": True, "count": len(sites), "sites": sites})
    return 0


def _cmd_unresolved_calls_stats(args: argparse.Namespace) -> int:
    cfg = _resolved_from_ns(args)
    _startup_hints(cfg)
    cfg.apply_to_os_environ()
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph  # lazy

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
    from java_codebase_rag.analysis import pr_analysis  # lazy
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph  # lazy

    if not LadybugGraph.exists():
        _emit({"success": False, "message": "LadybugDB graph not found"})
        return 1
    graph = LadybugGraph.get()
    report = pr_analysis.analyze_pr_pipeline(graph, diff_text)
    _emit(pr_analysis.pr_report_to_dict(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    description = tr("HELP_DESC_CLI_MAIN")
    parser = argparse.ArgumentParser(
        prog="java-codebase-rag",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_string(parser.prog),
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    init = subparsers.add_parser(
        "init",
        help=tr("HELP_CMD_INIT"),
        description=(
            tr("HELP_MISC_74")
        ),
    )
    _add_index_embedding_flags(init)
    _add_verbosity_flags(init)
    init.set_defaults(handler=_cmd_init)

    install = subparsers.add_parser(
        "install",
        help=tr("HELP_CMD_INSTALL"),
        description=(
            tr("HELP_MISC_75")
        ),
    )
    install.add_argument(
        "--non-interactive",
        action="store_true",
        help=tr("HELP_FLAG_NON_INTERACTIVE"),
    )
    install.add_argument(
        "--agent",
        choices=["claude-code", "qwen-code", "gigacode"],
        default=[],
        action="append",
        help=tr("HELP_FLAG_AGENT"),
    )
    install.add_argument(
        "--scope",
        choices=["project", "user"],
        default=None,
        help=tr("HELP_FLAG_SCOPE"),
    )
    install.add_argument(
        "--model",
        type=str,
        default=None,
        help=tr("HELP_FLAG_MODEL"),
    )
    install.add_argument(
        "--retrieval",
        choices=["vectors", "bm25"],
        default=None,
        help=(
            tr("HELP_FLAG_RETRIEVAL")
        ),
    )
    install.add_argument(
        "--surface",
        choices=["mcp", "cli"],
        default=None,
        help=(
            tr("HELP_FLAG_SURFACE")
        ),
    )
    _add_verbosity_flags(install)
    install.set_defaults(handler=_cmd_install)

    update = subparsers.add_parser(
        "update",
        help=tr("HELP_CMD_UPDATE"),
        description=(
            tr("HELP_MISC_76")
        ),
    )
    update.add_argument(
        "--force",
        action="store_true",
        help=tr("HELP_FLAG_FORCE"),
    )
    update.add_argument(
        "--dry-run",
        action="store_true",
        help=tr("HELP_FLAG_DRY_RUN"),
    )
    update.add_argument(
        "--surface",
        choices=["mcp", "cli"],
        default=None,
        help=(
            tr("HELP_MISC_77")
        ),
    )
    _add_verbosity_flags(update)
    update.set_defaults(handler=_cmd_update)

    increment = subparsers.add_parser(
        "increment",
        help=tr("HELP_CMD_INCREMENT"),
        description=tr("HELP_MISC_78"),
    )
    _add_index_embedding_flags(increment)
    _add_verbosity_flags(increment)
    increment.add_argument(
        "--vectors-only",
        action="store_true",
        help=tr("HELP_FLAG_VECTORS_ONLY"),
    )
    increment.set_defaults(handler=_cmd_increment)

    reprocess = subparsers.add_parser(
        "reprocess",
        help=tr("HELP_CMD_REPROCESS"),
        description=(
            tr("HELP_MISC_79")
        ),
    )
    _add_index_embedding_flags(reprocess)
    _add_verbosity_flags(reprocess)
    _rex = reprocess.add_mutually_exclusive_group()
    _rex.add_argument(
        "--vectors-only",
        action="store_true",
        help=tr("HELP_MISC_80"),
    )
    _rex.add_argument(
        "--graph-only",
        action="store_true",
        help=tr("HELP_FLAG_GRAPH_ONLY"),
    )
    reprocess.set_defaults(handler=_cmd_reprocess)

    erase = subparsers.add_parser(
        "erase",
        help=tr("HELP_CMD_ERASE"),
        description=tr("HELP_MISC_81"),
    )
    _add_index_embedding_flags(erase)
    erase.add_argument("--yes", action="store_true", help=tr("HELP_FLAG_YES"))
    _add_verbosity_flags(erase)
    erase.set_defaults(handler=_cmd_erase)

    meta = subparsers.add_parser("meta", help=tr("HELP_CMD_META"))
    _add_index_embedding_flags(meta)
    meta.set_defaults(handler=_cmd_meta)

    tables = subparsers.add_parser("tables", help=tr("HELP_CMD_TABLES"))
    _add_index_embedding_flags(tables)
    tables.set_defaults(handler=_cmd_tables)

    diagnose = subparsers.add_parser(
        "diagnose-ignore",
        help=tr("HELP_CMD_DIAGNOSE_IGNORE"),
    )
    _add_index_embedding_flags(diagnose)
    diagnose.add_argument("path", type=str)
    diagnose.set_defaults(handler=_cmd_diagnose_ignore)

    analyze = subparsers.add_parser("analyze-pr", help=tr("HELP_CMD_ANALYZE_PR"))
    _add_index_embedding_flags(analyze)
    group = analyze.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff-file", type=str)
    group.add_argument("--diff-stdin", action="store_true")
    analyze.set_defaults(handler=_cmd_analyze_pr)

    unresolved = subparsers.add_parser(
        "unresolved-calls",
        help=tr("HELP_CMD_UNRESOLVED_CALLS"),
    )
    _add_index_embedding_flags(unresolved)
    unresolved_sub = unresolved.add_subparsers(dest="unresolved_command", required=True)

    uc_list = unresolved_sub.add_parser("list", help=tr("HELP_CMD_LIST"))
    _add_index_embedding_flags(uc_list)
    uc_list.add_argument("--method-id", type=str, default=None, help=tr("HELP_FLAG_METHOD_ID"))
    uc_list.add_argument(
        "--reason",
        type=str,
        default=None,
        choices=sorted(VALID_UNRESOLVED_CALL_REASONS),
        help=tr("HELP_FLAG_REASON"),
    )
    uc_list.add_argument("--microservice", type=str, default=None)
    uc_list.add_argument("--callee-simple", type=str, default=None, dest="callee_simple")
    uc_list.add_argument("--limit", type=int, default=100)
    uc_list.set_defaults(handler=_cmd_unresolved_calls_list)

    uc_stats = unresolved_sub.add_parser("stats", help=tr("HELP_CMD_STATS"))
    _add_index_embedding_flags(uc_stats)
    uc_stats.add_argument(
        "--by",
        type=str,
        choices=("reason", "microservice", "caller_role"),
        default="reason",
    )
    uc_stats.set_defaults(handler=_cmd_unresolved_calls_stats)

    # ``--lang`` on every registered subparser AND sub-subparser
    # (``unresolved-calls list|stats``), so the after-verb form parses on all
    # verbs uniformly. The walk recurses through nested _SubParsersActions;
    # the ``dest == "lang"`` guard makes re-visiting a parser a no-op.
    def _register_lang_everywhere(root: argparse.ArgumentParser) -> None:
        if any(act.dest == "lang" for act in root._actions):
            return
        _add_lang_flag(root)
        for action in root._actions:
            if isinstance(action, argparse._SubParsersAction):
                for choice_parser in action.choices.values():
                    _register_lang_everywhere(choice_parser)

    _register_lang_everywhere(parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    raise_fd_limit()
    raw = list(argv if argv is not None else sys.argv[1:])
    from java_codebase_rag.i18n import cli_lang_override, init_help_locale, scan_lang

    # Locale must be known before the refresh-deprecation print AND before
    # build_parser() (argparse help strings are baked at construction).
    # scan_lang catches the after-verb flag form; the stash carries the
    # dispatch pre-scan's before-verb value (console path).
    init_help_locale(scan_lang(raw) or cli_lang_override())
    if raw and raw[0] == "refresh":
        print(_refresh_deprecation(), file=sys.stderr)
        raw[0] = "reprocess"
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        return int(e.code) if isinstance(e.code, int) else 2
    except argparse.ArgumentError as exc:
        from java_codebase_rag.i18n import tr

        print(f"{tr('LBL_CLI_ARG_ERROR_STDERR')}{exc}", file=sys.stderr)
        return 2
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        from java_codebase_rag.i18n import tr

        _emit({"success": False, "exit_code": 2, "message": tr("ERR_INTERNAL", exc=exc)})
        return 2


def _console_script_main() -> None:
    """Real CLI entry: terminate without interpreter finalization.

    A pyarrow/lance worker thread (loaded via lancedb in lifecycle commands) can
    outlive CPython finalization in a one-shot CLI subprocess and trip
    ``PyGILState_Release`` (SIGABRT, exit -6). Flushing + ``os._exit`` skips that
    racy teardown — the command has already done its work and emitted its result.
    ``main()`` stays return-based so in-process test callers (``cli.main(...)``)
    keep working.

    ``KeyboardInterrupt`` (Ctrl+C during a long indexing step) is caught here
    rather than left to propagate: an uncaught interrupt bypasses this function
    and runs full interpreter finalization (traceback + thread teardown),
    whereas routing it through the same flush + ``os._exit`` path gives a clean,
    immediate exit (code 130) and avoids the finalization-time SIGABRT noted
    above for commands that loaded lancedb.
    """
    try:
        rc = main()
    except KeyboardInterrupt:
        from java_codebase_rag.i18n import tr

        sys.stderr.write(tr("MSG_INTERRUPTED"))
        sys.stderr.flush()
        rc = 130
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    _console_script_main()
