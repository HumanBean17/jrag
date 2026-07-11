"""The ``jrag watch`` daemon process: assemble the watch components and run them.

``WatchDaemon`` is the capstone that wires together the building blocks built in
Tasks 2-10:

  * :class:`watch.lock.ProjectLock` — single-writer mutual exclusion per project.
  * :class:`watch.warm.WarmResources` — the warm embedding model + the read-only
    graph reader (and the graph copy-on-write snapshot lifecycle).
  * :class:`watch.server.WatchServer` — the AF_UNIX socket server that dispatches
    read commands to the payload cores and ships the serialized payload.
  * :class:`watch.watcher.SourceWatcher` — the file watcher + debounced per-type
    reindex dispatcher.

Lifecycle (``run_foreground``):

  1. Acquire the project lock. Held elsewhere -> stderr line + ``return 2``.
     Unsupported platform -> stderr line + ``return 2``.
  2. EAGERLY warm the embedding model so a load failure fails fast (stderr +
     ``on_event("error", …)`` + lock release + ``return 2``).
  3. Install SIGINT/SIGTERM handlers that set a stop flag.
  4. ``server.start()`` then ``watcher.start()``.
  5. Write the state file (``paths.state_path``) so ``--status``/``--stop`` from
     another process can see current truth.
  6. Render a ``rich`` Live status panel (watcher state, last reindex, queries
     served) and block on a wait loop until the stop flag is set.
  7. Tear down in order — ``watcher.stop()`` → ``server.shutdown()`` →
     ``lock.release()`` → unlink socket + state file — and terminate with
     ``os._exit(0)``. The explicit ``os._exit`` (mirroring
     ``jrag._console_script_main``) skips interpreter finalization, which dodges
     a racy pyarrow/lance worker-thread SIGABRT once the daemon has served a
     ``search`` (the read path loads lancedb in-process). ``run_foreground``
     therefore NEVER returns normally on the serving path.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from java_codebase_rag.watch import paths
from java_codebase_rag.watch.lock import (
    LockHeldError,
    ProjectLock,
    WatchUnsupportedPlatform,
)
from java_codebase_rag.watch.server import WatchServer
from java_codebase_rag.watch.warm import WarmResources
from java_codebase_rag.watch.watcher import SourceWatcher

if TYPE_CHECKING:
    from java_codebase_rag.config import ResolvedOperatorConfig

# State-file rewrites are throttled so a busy reindex burst does not hammer disk.
# The initial write (``force=True``) and the last reindex both go through
# immediately; ``--status`` readers tolerate a slightly-stale ``last_reindex``.
_STATE_WRITE_MIN_INTERVAL_S = 1.0
# The blocking loop's tick: how often the Live panel refreshes and how quickly a
# stop signal is observed. 0.5 s is responsive without burning CPU.
_LOOP_TICK_S = 0.5

log = logging.getLogger(__name__)


def _read_state_file(index_dir) -> dict[str, Any] | None:
    """Return the parsed daemon state JSON, or ``None`` if missing/unreadable.

    Used by ``jrag watch --status`` (which must not acquire the lock) to render
    the last reindex. A corrupt/partial file yields ``None`` rather than raising.
    """
    path = paths.state_path(index_dir)
    try:
        raw = path.read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, OSError):
        return None
    return obj if isinstance(obj, dict) else None


class WatchDaemon:
    """Assemble the watch components and serve until interrupted.

    The daemon process holds the project lock for its entire lifetime; the state
    file is the cross-process truth that ``--status``/``--stop`` read.
    """

    def __init__(self, cfg: "ResolvedOperatorConfig") -> None:
        self.cfg = cfg
        self.lock = ProjectLock(cfg.index_dir)
        self.warm = WarmResources(cfg)
        self.server = WatchServer(self.warm, cfg)
        self.watcher = SourceWatcher(
            cfg,
            self.warm,
            debounce_ms=cfg.watch_debounce_ms,
            backend=cfg.watch_backend,
            poll_interval_ms=cfg.watch_poll_interval_ms,
            on_event=self._record,
        )

        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._last_state_write = 0.0
        self._state: dict[str, Any] = {
            "started_at": None,
            "pid": None,
            "socket": str(paths.socket_path(cfg.index_dir)),
            "last_reindex_at": None,
            "last_reindex_kind": None,
            "reindex_count": 0,
            # ``queries_served`` is left at 0 for v1: wiring a query-count
            # callback out of ``WatchServer`` (Task 7, approved) is out of scope
            # for this task's commit surface and the brief marks it optional.
            "queries_served": 0,
        }

    # ------------------------------------------------------------------
    # public lifecycle
    # ------------------------------------------------------------------

    def run_foreground(self) -> int:
        """Serve until SIGINT/SIGTERM, then tear down and ``os._exit(0)``.

        Early failure paths (lock held, unsupported platform, model-load error)
        return ``2`` normally — they occur before the server accepts connections,
        so no lance worker threads exist and interpreter finalization is safe.

        The serving path (after ``server.start()``) terminates ONLY via
        ``os._exit(0)`` in :meth:`_shutdown`; the trailing ``return 0`` is
        unreachable and exists to satisfy the ``-> int`` contract.
        """
        # 1. Acquire the project lock (single writer per project).
        try:
            self.lock.acquire()
        except LockHeldError as exc:
            print(f"jrag watch: index in use by PID {exc.pid}", file=sys.stderr)
            return 2
        except WatchUnsupportedPlatform:
            print("jrag watch: watch mode requires macOS/Linux", file=sys.stderr)
            return 2

        # 2. Eagerly warm the model so a load failure fails fast (before the
        #    server accepts a single query). The model is the only heavy,
        #    failure-prone resource that is not lazy on the read path.
        try:
            self.warm.model()
        except Exception as exc:  # noqa: BLE001 — report any load failure, then bail
            print(f"jrag watch: failed to load embedding model: {exc}", file=sys.stderr)
            self._record("error", {"phase": "model_load", "error": repr(exc)})
            self.lock.release()
            return 2

        # 3. Install stop-signal handlers (main thread only).
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        # 4a. Server start: bind the socket before starting the watcher so a
        #     query the moment the panel renders is already servable.
        #
        # We hold the EXCLUSIVE project lock, so we are the unique legitimate
        # owner of this socket path: any pre-existing socket file is a corpse
        # from a crashed prior daemon and MUST be cleared before bind, else
        # AF_UNIX bind() fails with EADDRINUSE. ``server.start`` also defends
        # against a stale socket for callers that do NOT hold the lock, but its
        # guard (``read_holder is None``) is inert here precisely because we
        # hold the lock — so the daemon clears its own stale socket itself.
        stale_sock = paths.socket_path(self.cfg.index_dir)
        try:
            stale_sock.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("could not unlink stale socket %s", stale_sock, exc_info=True)
        try:
            self.server.start()
        except Exception as exc:  # noqa: BLE001 — socket bind failure is fatal-but-reported
            print(f"jrag watch: failed to start server: {exc}", file=sys.stderr)
            self.lock.release()
            self._cleanup_runtime_files()
            return 2
        # 4b. Watcher start.
        try:
            self.watcher.start()
        except Exception as exc:  # noqa: BLE001 — reported; server already up so shut it down
            print(f"jrag watch: failed to start watcher: {exc}", file=sys.stderr)
            self.server.shutdown()
            self._cleanup_runtime_files()
            self.lock.release()
            return 2

        # 5. Write the initial state file (force, so --status sees truth at once).
        self._state["started_at"] = time.time()
        self._state["pid"] = os.getpid()
        self._write_state()

        # 6 + 7. Serve, then tear down. _shutdown ends with os._exit(0) so the
        #        finally never falls through; the return is unreachable.
        try:
            self._serve_until_stopped()
        finally:
            self._shutdown()
        return 0  # pragma: no cover — os._exit in _shutdown

    # ------------------------------------------------------------------
    # event recording (called from the watcher debounce thread + the UI loop)
    # ------------------------------------------------------------------

    def _record(self, kind: str, detail: dict[str, Any]) -> None:
        """Update in-memory state from a watcher event; throttle state rewrites.

        Called on the watcher's debounce worker thread, so all state mutation is
        under ``_state_lock``. The state file is rewritten at most once per
        ``_STATE_WRITE_MIN_INTERVAL_S`` so ``--status`` readers see recent truth
        without disk churn during a reindex burst.
        """
        with self._state_lock:
            if kind == "indexing_done":
                self._state["last_reindex_at"] = time.time()
                self._state["last_reindex_kind"] = "+".join(detail.get("kinds", []))
                self._state["reindex_count"] += 1
            elif kind == "indexing_started":
                self._state["last_reindex_kind"] = (
                    "indexing:" + "+".join(detail.get("kinds", []))
                )
            elif kind == "error":
                self._state["last_error"] = {
                    "phase": detail.get("phase"),
                    "at": time.time(),
                    "detail": detail,
                }
            self._maybe_write_state_locked()

    # ------------------------------------------------------------------
    # serve loop + status panel
    # ------------------------------------------------------------------

    def _serve_until_stopped(self) -> None:
        """Render the status panel and block until the stop flag is set.

        On a non-TTY stdio (detached, piped, tests) the Live region is skipped in
        favor of a single startup line — ``rich.Live`` on a pipe reprints the
        whole panel on every update and would flood the redirect log.
        """
        from rich.console import Console

        console = Console()
        live = None
        if console.is_terminal:
            try:
                from rich.live import Live

                live = Live(
                    self._render_panel(),
                    console=console,
                    refresh_per_second=4,
                    transient=False,
                )
                live.start()
            except Exception:  # noqa: BLE001 — Live is cosmetic; never block serving
                live = None
        if live is None:
            print(
                f"jrag watch: serving on {self._state['socket']} "
                f"(pid {os.getpid()})",
                flush=True,
            )

        try:
            while not self._stop.is_set():
                if live is not None:
                    try:
                        live.update(self._render_panel())
                    except Exception:  # noqa: BLE001 — cosmetic
                        pass
                # Event.wait returns True as soon as the flag is set, so a stop
                # signal is observed within one tick rather than the full window.
                self._stop.wait(_LOOP_TICK_S)
        finally:
            if live is not None:
                try:
                    live.stop()
                except Exception:  # noqa: BLE001 — cosmetic
                    pass

    def _render_panel(self):
        """Build the ``rich`` status table from the current in-memory state."""
        from rich.table import Table

        with self._state_lock:
            state = dict(self._state)
        table = Table(title=f"jrag watch (pid {os.getpid()})", show_header=False, box=None)
        table.add_row("socket", str(state.get("socket")))
        table.add_row("reindex count", str(state.get("reindex_count", 0)))
        last_kind = state.get("last_reindex_kind")
        last_at = state.get("last_reindex_at")
        if last_kind:
            when = time.strftime("%H:%M:%S", time.localtime(last_at)) if last_at else "—"
            table.add_row("last reindex", f"{last_kind} ({when})")
        else:
            table.add_row("last reindex", "—")
        table.add_row("queries served", str(state.get("queries_served", 0)))
        return table

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        """Tear down watcher → server → lock, remove runtime files, ``os._exit(0)``.

        Each step is best-effort: a failure in one must not skip the rest, and
        the process MUST terminate via ``os._exit(0)`` (never a normal return)
        to avoid the lance worker-thread SIGABRT at finalization once the server
        has served a ``search`` query.
        """
        try:
            self.watcher.stop()
        except Exception:  # noqa: BLE001 — teardown must continue
            log.warning("watcher.stop raised during shutdown", exc_info=True)
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            log.warning("server.shutdown raised during shutdown", exc_info=True)
        try:
            self.lock.release()
        except Exception:  # noqa: BLE001
            log.warning("lock.release raised during shutdown", exc_info=True)
        self._cleanup_runtime_files()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _on_signal(self, signum, frame) -> None:  # noqa: ARG002 — signal API
        """SIGINT/SIGTERM handler: flag the serve loop to stop (main thread)."""
        self._stop.set()

    def _cleanup_runtime_files(self) -> None:
        """Remove the socket and state file (idempotent, best-effort)."""
        for path in (
            paths.socket_path(self.cfg.index_dir),
            paths.state_path(self.cfg.index_dir),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("could not unlink %s", path, exc_info=True)

    def _write_state(self) -> None:
        """Unconditionally write the state JSON now (initial write).

        Acquires ``_state_lock``; the throttled re-write path is
        :meth:`_maybe_write_state_locked`, called by :meth:`_record` which
        already holds the lock.
        """
        with self._state_lock:
            self._write_state_locked()

    def _maybe_write_state_locked(self) -> None:
        """Throttled state write; caller MUST hold ``_state_lock``."""
        now = time.monotonic()
        if now - self._last_state_write >= _STATE_WRITE_MIN_INTERVAL_S:
            self._write_state_locked()

    def _write_state_locked(self) -> None:
        """Write the JSON state file (best-effort); caller MUST hold ``_state_lock``."""
        path = paths.state_path(self.cfg.index_dir)
        data = dict(self._state)
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, path)
            self._last_state_write = time.monotonic()
        except OSError:
            log.warning("could not write state file %s", path, exc_info=True)
