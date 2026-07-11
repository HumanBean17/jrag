"""Warm-resources holder for the ``jrag watch`` daemon.

``WarmResources`` keeps two expensive objects resident for the daemon's lifetime:

  * the ``SentenceTransformer`` embedding model (loaded once, reused for every
    query), and
  * a read-only ``LadybugGraph`` over ``cfg.ladybug_path``.

It also owns the graph copy-on-write snapshot lifecycle (design §4.7): while a
graph-reindex subprocess writes the ORIGINAL ``code_graph.lbug``, the daemon serves
graph reads from a file COPY (sidecar) so readers and the single-writer subprocess
never collide. The ``ladybug`` engine has no transaction API and is single-writer,
which is why graph builds run as subprocesses and reads are served from a copy.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from java_codebase_rag.config import ResolvedOperatorConfig
from java_codebase_rag.graph.ladybug_queries import LadybugGraph
from java_codebase_rag.mcp.mcp_v2 import _get_sentence_transformer

log = logging.getLogger(__name__)


class WarmResources:
    """Holder for the warm embedding model + the read-only graph reader.

    The model is cached automatically by ``mcp_v2``'s module global, so ``model()``
    returning the same instance across calls is the warmth win. ``graph()`` returns
    the snapshot reader when a snapshot is active, else the original reader. Both are
    safe to call from server threads (the underlying singletons are lock-guarded).

    The snapshot flip (``begin_graph_snapshot`` / ``commit_graph_snapshot``) is
    serialized against ``graph()`` reads by ``self._lock`` so a reader can never
    observe a torn state (e.g. read ``_snapshot_path=None`` after ``begin`` has
    already reset the original singleton but before it set the sidecar, then
    re-cache the original a subprocess is concurrently overwriting). Lock ordering:
    ``WarmResources._lock`` is always the OUTER lock; ``LadybugGraph._lock`` (taken
    inside ``get``/``reset_for_path``) is INNER — never reversed. The lock is held
    only across the in-process flip + read, NEVER across the long reindex
    subprocess (the watcher calls ``begin`` before it and ``commit`` after).
    """

    def __init__(self, cfg: ResolvedOperatorConfig) -> None:
        self.cfg = cfg
        self._snapshot_path: Path | None = None
        # Serializes the snapshot flip against graph() reads (see class docstring).
        self._lock = threading.Lock()

    def model(self):
        """Return the warm ``SentenceTransformer`` (cached by the module global)."""
        return _get_sentence_transformer(self.cfg.embedding_model, self.cfg.embedding_device)

    def graph(self) -> LadybugGraph:
        """Return the current graph reader: the sidecar if a snapshot is active, else the original.

        The read of ``_snapshot_path`` and the matching ``LadybugGraph.get(...)`` are
        atomic w.r.t. the snapshot flip (held under ``self._lock``) so a reader always
        pairs the right path with the right cached singleton.
        """
        with self._lock:
            if self._snapshot_path is not None:
                return LadybugGraph.get(str(self._snapshot_path))
            return LadybugGraph.get(str(self.cfg.ladybug_path))

    def begin_graph_snapshot(self) -> None:
        """Copy the graph to a sidecar and serve subsequent ``graph()`` reads from it.

        Drops the cached original reader (so the subprocess is free to overwrite the
        original file) and switches ``graph()`` to a fresh reader on the sidecar copy.
        The whole flip runs under ``self._lock`` so no ``graph()`` read can observe an
        intermediate state (original reset but sidecar not yet set).
        """
        with self._lock:
            sidecar = self.cfg.ladybug_path.with_suffix(".lbug.snapshot")
            shutil.copy2(self.cfg.ladybug_path, sidecar)
            LadybugGraph.reset_for_path(str(self.cfg.ladybug_path))
            self._snapshot_path = sidecar

    def commit_graph_snapshot(self) -> None:
        """Drop the sidecar reader, remove the sidecar, and reopen the updated original.

        After this call ``graph()`` reads the original again (which the subprocess has
        just rewritten), and the sidecar file is gone. Idempotent no-op when no
        snapshot is active. The whole flip runs under ``self._lock`` so no ``graph()``
        read can observe the sidecar mid-teardown.
        """
        with self._lock:
            if self._snapshot_path is None:
                return
            sidecar = self._snapshot_path
            LadybugGraph.reset_for_path(str(sidecar))
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                log.warning("Failed to remove graph snapshot sidecar %s", sidecar, exc_info=True)
            finally:
                # Clear state and reopen the original even if unlink failed, so a
                # possibly-deleted sidecar isn't kept serving reads.
                self._snapshot_path = None
                LadybugGraph.reset_for_path(str(self.cfg.ladybug_path))
