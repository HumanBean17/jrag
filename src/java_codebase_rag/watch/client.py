"""IPC client for the ``jrag watch`` daemon: try-the-daemon, cold-fall-back.

The cold read path is BYTE-IDENTICAL to today: when no daemon is alive (the
common case — including every ``jrag`` subprocess invocation that is not a
``jrag watch`` process), :func:`get_payload` falls back to the same
``<cmd>_payload`` core the handler already calls, loading the graph via
``_load_graph(cfg)`` (a cache hit on the already-loaded singleton). The daemon
is a pure accelerator: it never changes observable output, only latency.

Contract (pinned by task-8 brief):

* :func:`is_daemon_alive` -> ``socket_path`` exists AND
  :meth:`ProjectLock.read_holder` returns a live pid.
* :func:`request` -> ``response.result`` dict; raises :class:`DaemonUnavailable`
  (no daemon / version mismatch / hung) or :class:`DaemonError` (``ok=False``).
* :func:`get_payload` -> the single seam each read handler calls. Tries the
  daemon; on :class:`DaemonUnavailable`/:class:`DaemonError` runs the cold
  ``cold_core(argparse.Namespace(**args), cfg, _load_graph(cfg))``. On success it
  RECONSTRUCTS the payload object from the daemon's serialized dict so the
  handler's downstream project+render runs unchanged.

Reconstruction is the lossless inverse of :func:`watch.server.serialize`:
pydantic ``*Output`` models round-trip via ``model_dump(mode="json")`` /
``model_validate``; the traversal payloads (callers/callees/flow) are plain
dicts and pass through. The one non-pydantic case is ``find`` query mode, whose
``rows`` are :class:`SymbolHit` dataclasses accessed by attribute in the renderer
(``row.id``); those are rebuilt via ``SymbolHit(**row)`` so the renderer is
untouched. (The task-8 brief literal said "pass-through" for query mode; that
would break the renderer's attribute access on the hot path, so the rows are
rebuilt instead — see task-8 report.)
"""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any, Callable

from java_codebase_rag.jrag import _load_graph
from java_codebase_rag.watch.lock import ProjectLock
from java_codebase_rag.watch.paths import socket_path
from java_codebase_rag.watch.protocol import (
    PROTOCOL_VERSION,
    ProtocolMismatch,
    Request,
    decode_response,
    encode_request,
)

if TYPE_CHECKING:
    from pathlib import Path

# Connect/read budget so a hung daemon falls back to cold rather than blocking
# the read command. 2s is generous for a local AF_UNIX round trip (the daemon
# answers inline; a healthy response is sub-millisecond).
_DAEMON_TIMEOUT = 2.0


class DaemonUnavailable(Exception):
    """No live daemon, an unreachable/hung daemon, or a protocol-version mismatch.

    Always triggers the cold fallback in :func:`get_payload`.
    """


class DaemonError(Exception):
    """The daemon answered ``ok=False`` (e.g. ``backend_error`` / ``stale_index``).

    Attributes:
        kind: the ``Response.error.kind`` string (``"backend_error"`` etc.).
        message: the ``Response.error.message`` string.
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(f"{kind}: {message}")


def is_daemon_alive(index_dir: "Path") -> bool:
    """True iff the daemon's socket exists AND a live holder pid is recorded.

    The pid check (:meth:`ProjectLock.read_holder`) returns ``None`` for a
    missing/empty pid file or a dead/stale pid, so a leftover socket alone does
    not count as "alive" — a crashed daemon's socket is ignored and the read
    falls back to cold.
    """
    if not socket_path(index_dir).exists():
        return False
    return ProjectLock.read_holder(index_dir) is not None


def request(index_dir: "Path", cmd: str, args: dict) -> dict:
    """Send one command to the daemon and return its ``result`` payload dict.

    Raises:
        DaemonUnavailable: not alive, unreachable/hung (timeout), closed
            mid-response, or speaking a different protocol version (an older
            daemon). All of these trigger the cold fallback.
        DaemonError: the daemon responded ``ok=False``.
    """
    if not is_daemon_alive(index_dir):
        raise DaemonUnavailable(f"no live daemon for {index_dir}")

    sock_path = socket_path(index_dir)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_DAEMON_TIMEOUT)
            sock.connect(str(sock_path))
            sock.sendall(encode_request(Request(v=PROTOCOL_VERSION, cmd=cmd, args=args)))
            line = _readline(sock)
    except OSError as exc:
        # Timeout, connection refused, etc. -> cold fallback (never block).
        raise DaemonUnavailable(f"daemon unreachable at {sock_path}: {exc}") from exc

    try:
        response = decode_response(line)
    except (ProtocolMismatch, ValueError) as exc:
        # Version mismatch (older daemon) or a blank/partial line (daemon gone)
        # -> cold fallback rather than crashing the read command.
        raise DaemonUnavailable(f"daemon response undecodable: {exc}") from exc

    if not response.ok:
        err = response.error
        raise DaemonError(
            err.kind if err is not None else "unknown",
            err.message if err is not None else "",
        )
    return response.result


def get_payload(cmd: str, args: dict, cfg, *, cold_core: Callable[..., Any]) -> Any:
    """Return the payload for ``cmd``, trying the daemon first then cold-falling-back.

    ``args`` is the command's full parsed-namespace dict (``vars(args)``);
    ``cold_core`` is the matching ``<cmd>_payload(args, cfg, graph)`` core.

    * Daemon success: the serialized payload dict is RECONSTRUCTED into the same
      object the cold core returns, so the handler's render path is unchanged.
    * DaemonUnavailable / DaemonError: the cold core runs against
      ``_load_graph(cfg)`` — byte-identical to today (the daemon is absent in
      every non-``watch`` invocation, and an ``ok=False`` frame re-surfaces as
      the cold core's own ``PayloadError`` → identical error envelope + rc).
    """
    try:
        result = request(cfg.index_dir, cmd, args)
    except (DaemonUnavailable, DaemonError):
        return cold_core(argparse_namespace(args), cfg, _load_graph(cfg))
    return _reconstruct(cmd, result)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _readline(sock: socket.socket) -> bytes:
    """Read until newline (the response is one NDJSON line). Returns whatever was
    received before newline/EOF; the caller decodes (and maps blanks to
    ``DaemonUnavailable``)."""
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _reconstruct(cmd: str, result: Any) -> Any:
    """Rebuild the payload object the cold core returns from the daemon's JSON dict.

    Inverse of :func:`watch.server.serialize`. See module docstring for the
    find query-mode ``SymbolHit`` rebuild rationale.
    """
    if cmd == "search":
        from java_codebase_rag.mcp.mcp_v2 import SearchOutput

        return SearchOutput.model_validate(result)

    if cmd == "inspect":
        from java_codebase_rag.mcp.mcp_v2 import DescribeOutput

        return {
            "describe": DescribeOutput.model_validate(result["describe"]),
            "node_id": result["node_id"],
            "node_fqn": result["node_fqn"],
            "file_location": result["file_location"],
        }

    if cmd == "find":
        # The find payload is always a {"mode": "query"|"filter", ...} dict.
        mode = result.get("mode")
        if mode == "filter":
            from java_codebase_rag.mcp.mcp_v2 import FindOutput

            return {
                "mode": "filter",
                "kind": result["kind"],
                "out": FindOutput.model_validate(result["out"]),
                "limit": result["limit"],
            }
        # query mode: rows are SymbolHit dataclasses (attribute access in the
        # renderer), rebuilt here so rendering is byte-identical.
        from java_codebase_rag.graph.ladybug_queries import SymbolHit

        return {
            "mode": "query",
            "rows": [SymbolHit(**row) for row in result["rows"]],
            "raw_truncated": result["raw_truncated"],
            "post_filter_active": result["post_filter_active"],
            "limit": result["limit"],
            "query": result["query"],
            "kinds": result["kinds"],
        }

    # callers / callees / flow: plain dicts already (node/edge values are dicts
    # of JSON-native scalars); pass through unchanged.
    return result


def argparse_namespace(args: dict):
    """Build an ``argparse.Namespace`` from the args dict (deferred import).

    Imported lazily so this module's top level stays free of the ``argparse``
    dependency needed only on the cold path; also a single seam for the cold
    core's ``args`` reconstruction."""
    import argparse

    return argparse.Namespace(**args)
