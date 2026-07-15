"""File watcher + debounced per-type reindex dispatcher for ``jrag watch``.

``SourceWatcher`` observes the project source tree and, after a debounce window,
re-runs the minimal reindex for the changed file types:

  * a source-language change (``.java``; ``.kt`` when the kotlin grammar is
    importable) -> vectors THEN graph. The graph reindex runs as a subprocess
    under a copy-on-write snapshot (design §4.7) so concurrent graph reads keep
    being served from a sidecar copy while the single-writer subprocess
    overwrites the original. ``ladybug`` has no transactions and is
    single-writer, which is why graph builds are subprocesses and reads are
    served from a copy.
  * a matching ``.sql`` / ``.yml`` / ``.yaml`` resource change -> vectors only
    (the graph does not index SQL/YAML, so no snapshot is needed).

Change bursts are coalesced: the leading event arms a ``debounce_ms`` timer that
keeps getting re-armed while events keep arriving, and a single ``reindex`` fires
once the window goes quiet. ``reindex`` runs on a dedicated background thread so
the watchdog observer thread is never blocked.

The cocoindex flow indexes three sets (``**/*.java``,
``**/src/main/resources/db/migration/*.sql``,
``**/src/main/resources/application*.yml``/``.yaml``). There is NO shared
iterator (``iter_java_source_files`` yields ``.java`` only), so the watcher
defines this UNION and classifies each event path into a reindex kind.

All status is reported via ``on_event(kind, detail)`` callbacks (kinds:
``indexing_started`` / ``vectors`` / ``graph`` / ``indexing_done`` / ``error``);
the watcher never prints directly.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from java_codebase_rag.ast.language import LANG_BACKENDS, backend_for
from java_codebase_rag.graph.path_filtering import LayeredIgnore
from java_codebase_rag.pipeline import (
    run_cocoindex_update,
    run_incremental_graph,
    vector_stack_installed,
)

if TYPE_CHECKING:
    from java_codebase_rag.config import ResolvedOperatorConfig
    from java_codebase_rag.watch.warm import WarmResources

log = logging.getLogger(__name__)

# Suffixes the watcher treats as source files — derived from the language
# backend registry so this never drifts from what the graph builder parses.
# ``(".java", ".kt")`` when the kotlin grammar is importable; just
# ``(".java",)`` on a grammar-absent (e.g. graph-only) install. The non-source
# resource types (sql/yaml) are matched by the glob helpers below (the cocoindex
# set has no shared iterator).
INDEXED_SUFFIXES: tuple[str, ...] = tuple(
    suffix for backend in LANG_BACKENDS.values() for suffix in backend.suffixes
)
# Reindex kinds that trigger a graph rebuild. The graph indexes every registered
# source language (java today, kotlin too when its grammar imports), so this is
# the set of backend ``language_id``s. sql/yaml are vectors-only (no graph row).
_GRAPH_INDEXED_KINDS: frozenset[str] = frozenset(LANG_BACKENDS.keys())
_YAML_SUFFIXES: tuple[str, ...] = (".yml", ".yaml")

# Project-relative anchored prefixes for the two non-java resource globs:
#   **/src/main/resources/db/migration/*.sql
#   **/src/main/resources/application*.yml / .yaml
_SQL_MIGRATION_PREFIX = "src/main/resources/db/migration/"
_RESOURCES_PREFIX = "src/main/resources/"


def _is_migration_sql(rel_posix: str) -> bool:
    """True iff ``rel_posix`` is a ``.sql`` directly under a ``.../db/migration/`` dir."""
    if not rel_posix.endswith(".sql"):
        return False
    idx = rel_posix.rfind(_SQL_MIGRATION_PREFIX)
    if idx == -1:
        return False
    # The file must sit directly under migration/ (no further path segment).
    return "/" not in rel_posix[idx + len(_SQL_MIGRATION_PREFIX):]


def _is_application_yaml(rel_posix: str) -> bool:
    """True iff ``rel_posix`` is ``application*.yml``/``.yaml`` directly under
    a ``.../src/main/resources/`` dir."""
    name = rel_posix.rsplit("/", 1)[-1]
    if not name.startswith("application"):
        return False
    if not name.endswith((".yml", ".yaml")):
        return False
    idx = rel_posix.rfind(_RESOURCES_PREFIX)
    if idx == -1:
        return False
    return "/" not in rel_posix[idx + len(_RESOURCES_PREFIX):]


class _ChangeHandler(FileSystemEventHandler):
    """Bridge watchdog events to the watcher's classify+schedule path.

    Directory events are ignored. Each file event's path (and, for moves, the
    destination) is classified; if it matches an indexed type and survives
    ``LayeredIgnore`` its kind is scheduled.
    """

    def __init__(self, watcher: "SourceWatcher") -> None:
        super().__init__()
        self._watcher = watcher

    def on_any_event(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._watcher._handle_path(event.src_path)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._watcher._handle_path(dest)


class SourceWatcher:
    """Watch ``cfg.source_root`` and dispatch debounced per-type reindexes.

    Thread model: watchdog's observer thread calls the handler (which only does a
    quick classify + schedule -- never blocks). A single dedicated debounce
    worker thread runs ``reindex`` so the observer is never held up by a build.
    """

    def __init__(
        self,
        cfg: "ResolvedOperatorConfig",
        warm: "WarmResources",
        *,
        debounce_ms: int,
        backend: str,
        poll_interval_ms: int,
        on_event: Callable[[str, dict[str, Any]] | None] = None,
    ) -> None:
        self.cfg = cfg
        self.warm = warm
        # Probed once: when False (graph-only install — macOS Intel) the vectors
        # (cocoindex) reindex step is skipped entirely, so the graph reindex still
        # completes and fires ``indexing_done`` instead of bailing on a 127 stub.
        self._vector_enabled = vector_stack_installed()
        self._debounce_s = max(int(debounce_ms), 1) / 1000.0
        self._backend = backend
        # watchdog's PollingObserver takes a float timeout in SECONDS (it flows
        # straight into ``threading.Event.wait``); a timedelta crashes the emitter.
        self._poll_interval_s = max(int(poll_interval_ms), 1) / 1000.0
        self._on_event = on_event

        self._ignore = LayeredIgnore(cfg.source_root)
        self._source_root_resolved = Path(cfg.source_root).resolve()

        self._observer = self._make_observer()
        self._handler = _ChangeHandler(self)

        # Debounce state -- touched by the observer thread and the debounce worker.
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._activity = threading.Event()
        self._stop = threading.Event()
        self._debounce_thread = threading.Thread(
            target=self._debounce_loop, name="jrag-watch-debounce", daemon=True
        )

        # In-memory reindex state, refreshed after each successful reindex.
        self.last_reindex: dict[str, Any] | None = None

    # -- observer backend ----------------------------------------------------

    def _make_observer(self):
        if self._backend == "polling":
            return PollingObserver(timeout=self._poll_interval_s)
        # "watchdog" and "auto" both prefer the native observer; "auto" falls
        # back to polling in :meth:`start` if the native backend cannot start.
        return Observer()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Schedule the observer on ``cfg.source_root`` (recursive) and start the
        debounce loop. Under ``backend="auto"`` a native observer that fails to
        start (e.g. on some network filesystems) falls back to polling."""
        self._observer.schedule(self._handler, str(self.cfg.source_root), recursive=True)
        try:
            self._observer.start()
        except Exception as exc:
            if self._backend != "auto":
                raise
            self._emit(
                "error",
                {"phase": "observer", "fallback": "polling", "error": repr(exc)},
            )
            self._observer = PollingObserver(timeout=self._poll_interval_s)
            self._observer.schedule(self._handler, str(self.cfg.source_root), recursive=True)
            self._observer.start()
        self._debounce_thread.start()

    def stop(self) -> None:
        """Stop the observer and the debounce loop; join both."""
        self._stop.set()
        self._activity.set()  # unblock the debounce worker's wait
        try:
            if self._observer.is_alive():
                self._observer.stop()
                self._observer.join(timeout=5.0)
        except Exception:
            log.warning("watchdog observer stop failed", exc_info=True)
        if (
            self._debounce_thread.is_alive()
            and threading.current_thread() is not self._debounce_thread
        ):
            self._debounce_thread.join(timeout=10.0)

    # -- event classification (observer thread) ------------------------------
    #
    # The watcher fires on exactly the indexed set: the source languages claimed
    # by ``LANG_BACKENDS`` (``.java``; ``.kt`` when the kotlin grammar imports)
    # plus the SQL/YAML resource patterns below, filtered through
    # ``LayeredIgnore``. ``target/generated-sources/**/*.java`` therefore
    # correctly fires: generated sources are first-class, cocoindex does NOT
    # exclude ``target/``, so the watcher must fire to keep them fresh.
    # ``LayeredIgnore.is_ignored`` does NOT prune build-output dirs
    # (``target/``/``build``/``out``) -- that pruning lives in
    # ``iter_java_source_files``'s ``os.walk`` (``_is_build_output_dir``), used
    # by the graph builder, not by cocoindex. (Compiled ``.class`` output under
    # ``target/`` doesn't match the ``.java`` suffix anyway.)

    def _handle_path(self, src_path: object) -> None:
        """Classify one observed path and schedule its kind if indexed & not ignored."""
        kinds = self._classify(Path(str(src_path)))
        if kinds:
            self._schedule(kinds)

    def _classify(self, path: Path) -> set[str]:
        """Return the set of reindex kinds for ``path`` (empty if ignored/unknown).

        ``LayeredIgnore`` wins: an ignored path yields no kind even when its
        suffix is ``.java``. Outside-source-root paths also yield nothing.
        """
        try:
            if self._ignore.is_ignored(path):
                return set()
        except Exception:
            return set()
        try:
            rel = path.resolve().relative_to(self._source_root_resolved).as_posix()
        except ValueError:
            return set()
        # Source languages (.java, .kt when registered) dispatch via the backend
        # registry: a change yields its backend's ``language_id`` reindex kind,
        # which triggers the graph rebuild (the subprocess graph builder re-walks
        # the tree and parses each file through ``backend_for`` — so a ``.kt``
        # change reprocesses via KotlinBackend). Unknown suffixes are not source
        # files and fall through to the resource-glob checks below.
        backend = backend_for(path)
        if backend is not None:
            return {backend.language_id}
        suffix = path.suffix.lower()
        if suffix == ".sql":
            return {"sql"} if _is_migration_sql(rel) else set()
        if suffix in _YAML_SUFFIXES:
            return {"yaml"} if _is_application_yaml(rel) else set()
        return set()

    def _schedule(self, kinds: set[str]) -> None:
        """Union ``kinds`` into the debounce collector and (re)arm the debounce window."""
        if not kinds:
            return
        with self._lock:
            self._pending |= kinds
        self._activity.set()

    # -- debounce worker thread ----------------------------------------------

    def _debounce_loop(self) -> None:
        """Fire ``reindex`` once per quiet period, coalescing bursts.

        Leading edge: the first event arms a ``debounce_ms`` window. Each further
        event during the window re-arms it. When the window expires with no new
        events, the accumulated kinds are flushed to ``reindex`` in ONE call.
        """
        while not self._stop.is_set():
            # Wait for the leading edge of a burst.
            self._activity.wait()
            if self._stop.is_set():
                return
            # Re-arm while events keep arriving (debounce).
            while not self._stop.is_set():
                self._activity.clear()
                if self._activity.wait(timeout=self._debounce_s):
                    continue  # new activity -> reset the window
                break  # quiet period elapsed -> fire
            if self._stop.is_set():
                return
            with self._lock:
                kinds = self._pending
                self._pending = set()
            if kinds:
                try:
                    self.reindex(kinds)
                except Exception:  # noqa: BLE001 -- reindex emits its own error; never kill the worker
                    log.warning("reindex raised unexpectedly", exc_info=True)

    # -- reindex (runs on the debounce worker thread) ------------------------

    def reindex(self, kinds: set[str]) -> None:
        """Run one debounced reindex: vectors always (when installed); graph only when a
        registered source language (java/kotlin) changed.

        COW lifecycle (design §4.7): the graph subprocess writes the ORIGINAL
        graph while reads are served from a sidecar copy. ``begin_graph_snapshot``
        is called BEFORE the subprocess and ``commit_graph_snapshot`` ALWAYS
        (success OR failure) so a snapshot reader is never left dangling -- on
        graph failure the existing ``.graph_increment_in_progress`` crash marker
        drives the next full rebuild. Lance needs no snapshot (commits are atomic
        per version; fresh per-query reads are fine).

        Graph-only installs (macOS Intel) have no vector stack: the cocoindex
        step is skipped (``vres=None``) so the graph reindex completes and fires
        ``indexing_done`` instead of bailing on a 127 cocoindex-not-found stub.
        """
        if not kinds:
            return
        kind_list = sorted(kinds)
        self._emit("indexing_started", {"kinds": kind_list})
        try:
            # Vectors run for every indexed type (java/sql/yaml all flow through
            # cocoindex) — but only when the vector stack is installed. On a
            # graph-only install cocoindex is absent and the call would return a
            # 127 stub; skipping it keeps ``indexing_done`` reachable.
            if self._vector_enabled:
                self._emit("vectors", {"kinds": kind_list})
                vres = run_cocoindex_update(
                    self.cfg.subprocess_env(),
                    full_reprocess=False,
                    quiet=True,
                    verbose=False,
                )
            else:
                vres = None

            graph_rc = 0
            if kinds & _GRAPH_INDEXED_KINDS:
                # The graph indexes every registered source language (java,
                # kotlin when its grammar imports); reindex under a COW snapshot
                # so graph reads continue (from the sidecar) during the write.
                self._emit("graph", {"kinds": kind_list})
                try:
                    # begin/commit paired in this try/finally. begin_graph_snapshot
                    # sets ``_snapshot_path`` last, so if it raises commit no-ops;
                    # moving begin inside the try keeps the pairing locally evident.
                    self.warm.begin_graph_snapshot()
                    gres = run_incremental_graph(
                        source_root=self.cfg.source_root,
                        ladybug_path=self.cfg.ladybug_path,
                        verbose=False,
                        quiet=True,
                        env=self.cfg.subprocess_env(),
                    )
                    graph_rc = gres.returncode
                finally:
                    # ALWAYS drop the snapshot reader -- even on failure -- so it
                    # is never left dangling. No-ops when no snapshot is active.
                    self.warm.commit_graph_snapshot()

            if vres is not None and vres.returncode != 0:
                self._emit("error", {"phase": "vectors", "returncode": vres.returncode})
                return
            if graph_rc != 0:
                self._emit("error", {"phase": "graph", "returncode": graph_rc})
                return

            self.last_reindex = {"time": time.time(), "kinds": kind_list}
            self._emit("indexing_done", {"kinds": kind_list})
        except Exception as exc:  # noqa: BLE001 -- the daemon must survive any reindex error
            self._emit("error", {"phase": "reindex", "error": repr(exc)})

    # -- status --------------------------------------------------------------

    def _emit(self, kind: str, detail: dict[str, Any]) -> None:
        """Forward a status callback to the UI; never let it crash the watcher."""
        if self._on_event is None:
            return
        try:
            self._on_event(kind, detail)
        except Exception:  # noqa: BLE001 -- a UI callback must not crash the watcher
            log.warning("on_event callback raised", exc_info=True)
