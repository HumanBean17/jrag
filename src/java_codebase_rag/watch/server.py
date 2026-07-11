"""Unix-socket server for the ``jrag watch`` daemon.

Accepts newline-delimited JSON requests (Task 4's protocol), dispatches each to
the matching read-command payload core (Task 5), and writes the encoded
response (Task 4's codec). Transport + dispatch only — the server stores nothing
between requests except the warm resources and the operator config.

Lifecycle (Task 11 wires this into the daemon process):

  * ``start()``  binds an ``AF_UNIX`` / ``SOCK_STREAM`` socket to
    :func:`paths.socket_path`, ``chmod 0o600``, ``listen(8)``, and spawns an
    accept thread running :meth:`serve`.
  * ``serve()``  is the accept loop. Each connection is handled INLINE (the read
    commands are fast): read newline-delimited bytes, decode, dispatch, encode,
    flush. Multiple pipelined requests on one connection are each answered; a
    connection that closes mid-line is dropped silently.
  * ``shutdown()``  closes the listening socket and joins the accept thread.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import socket
import threading
from typing import TYPE_CHECKING, Any, Callable

from java_codebase_rag.jrag import _IndexStale
from java_codebase_rag.read_payloads import (
    callers_payload,
    callees_payload,
    find_payload,
    flow_payload,
    inspect_payload,
    search_payload,
)
from java_codebase_rag.watch import paths
from java_codebase_rag.watch.lock import ProjectLock
from java_codebase_rag.watch.protocol import (
    ERR_BAD_ARGS,
    ERR_BACKEND_ERROR,
    ERR_STALE_INDEX,
    ERR_UNKNOWN_COMMAND,
    PROTOCOL_VERSION,
    ErrorShape,
    ProtocolMismatch,
    Request,
    Response,
    decode_request,
    encode_response,
)

if TYPE_CHECKING:
    # Type-only: the server never constructs these (the daemon passes them in),
    # so they are not needed at import time. Avoids coupling this transport
    # module to the heavy warm-resources/config import chain on minimal installs.
    from java_codebase_rag.config import ResolvedOperatorConfig
    from java_codebase_rag.warm import WarmResources

log = logging.getLogger(__name__)

# cmd -> payload core (Task 5). ``dispatch`` looks cmds up here; it ALSO defends
# in depth against an unknown cmd (returns ERR_UNKNOWN_COMMAND) even though
# ``decode_request`` already rejects anything outside VALID_CMDS — so a direct
# caller of ``dispatch`` (or a future divergence between the two sets) still
# gets the right error kind instead of a KeyError.
PAYLOAD_FNS: dict[str, Callable[[argparse.Namespace, Any, Any], Any]] = {
    "search": search_payload,
    "find": find_payload,
    "inspect": inspect_payload,
    "callers": callers_payload,
    "callees": callees_payload,
    "flow": flow_payload,
}


def serialize(payload: Any) -> Any:
    """Return a JSON-safe representation of a payload core's return value.

    The cold read path's ``--format json`` emits a rendered *Envelope* via
    ``Envelope.to_json()`` (after ``project_envelope``). That requires the
    handler-specific payload->Envelope projection, which lives in the ``jrag``
    read handlers (``_cmd_search`` etc.) and is NOT a separable shared function
    — it is tangled with the terminal-rendering code path. Per the task brief,
    when that path is tangled we serialize the *payload* directly and let the
    client (Task 8) reconstruct + render it locally with the same handler code:

      * pydantic models  -> ``model_dump(mode="json")`` (search/find/inspect
        ``*Output`` models, nested ``SymbolHit`` rows, etc.);
      * dict / list      -> recursed element-wise;
      * dataclass        -> ``dataclasses.asdict``;
      * everything else  -> passed through (str/int/float/bool/None already
        JSON-safe).

    NOTE for Task 8: the daemon ships the serialized *payload*, not the rendered
    envelope. The client must reconstruct the payload object and run the same
    render path the cold ``jrag`` handler runs on it. ``encode_response`` then
    ``json.dumps`` this structure, so the values here must already be JSON-safe.
    """
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return {key: serialize(val) for key, val in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [serialize(item) for item in payload]
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return dataclasses.asdict(payload)
    return payload


class WatchServer:
    """AF_UNIX socket server dispatching NDJSON requests to payload cores."""

    def __init__(self, warm: "WarmResources", cfg: "ResolvedOperatorConfig") -> None:
        self.warm = warm
        self.cfg = cfg
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Bind the Unix socket (chmod 0o600), listen, spawn the accept thread."""
        sock_path = paths.socket_path(self.cfg.index_dir)
        # Unlink a stale socket ONLY when no live daemon holds the project lock
        # (a live holder means another daemon owns this path — leave it alone).
        if sock_path.exists() and ProjectLock.read_holder(self.cfg.index_dir) is None:
            try:
                sock_path.unlink()
            except OSError:
                log.warning("Could not unlink stale socket %s", sock_path, exc_info=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(sock_path))
        try:
            sock_path.chmod(0o600)
        except OSError:
            log.warning("Could not chmod socket %s to 0o600", sock_path, exc_info=True)
        sock.listen(8)
        self._sock = sock

        self._stopping.clear()
        self._thread = threading.Thread(
            target=self.serve, name="jrag-watch-accept", daemon=True
        )
        self._thread.start()

    def serve(self) -> None:
        """Accept loop: one inline connection at a time (commands are fast)."""
        sock = self._sock
        if sock is None:
            return
        while not self._stopping.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                # Listening socket closed (shutdown) -> exit cleanly. Any other
                # OSError here is transient; the loop retries while not stopping.
                if self._stopping.is_set() or self._sock is None:
                    break
                log.debug("accept() failed; retrying", exc_info=True)
                continue
            try:
                self._handle(conn)
            except Exception:  # noqa: BLE001 — a handler crash must not kill the loop
                log.exception("Unhandled error servicing watch connection")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def shutdown(self) -> None:
        """Close the listening socket and join the accept thread."""
        self._stopping.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    # -- per-connection handling -------------------------------------------

    def _handle(self, conn: socket.socket) -> None:
        """Read newline-delimited requests, dispatch, write responses.

        Loops so multiple pipelined requests on one connection are each
        answered. A peer that closes mid-line (a partial buffer with no
        trailing newline when recv returns empty) is dropped silently.
        """
        buffer = b""
        while True:
            # Answer every complete line currently buffered.
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                response = self._respond_to_line(line)
                try:
                    conn.sendall(encode_response(response))
                except OSError:
                    return  # peer gone — stop servicing this connection
            # Need more bytes.
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return  # connection closed; any trailing partial line is dropped
            buffer += chunk

    def _respond_to_line(self, line: bytes) -> Response:
        """Decode one request line and dispatch it, mapping decode errors."""
        try:
            req = decode_request(line)
        except (ProtocolMismatch, ValueError) as exc:
            # ProtocolMismatch (wrong v) and ValueError (blank line / unknown
            # cmd as decode sees it) both surface to the client as bad_args.
            return Response(
                v=PROTOCOL_VERSION,
                ok=False,
                error=ErrorShape(ERR_BAD_ARGS, str(exc)),
            )
        return self.dispatch(req, self.warm, self.cfg)

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, req: Request, warm: "WarmResources", cfg: Any) -> Response:
        """Map a decoded :class:`Request` to a :class:`Response` via the cmd core.

        Rebuilds ``args = argparse.Namespace(**req.args)`` (the client sends the
        full parsed-namespace dict; keys are argparse ``dest`` names) and calls
        ``<cmd>_payload(args, cfg, warm.graph())``. Error mapping (pinned
        contract): ``_IndexStale`` -> ``stale_index``;
        ``(ProtocolMismatch, ValueError)`` -> ``bad_args``; any other
        ``Exception`` (incl. :class:`PayloadError`) -> ``backend_error``;
        unknown cmd -> ``unknown_command`` (defense in depth).
        """
        payload_fn = PAYLOAD_FNS.get(req.cmd)
        if payload_fn is None:
            return Response(
                v=PROTOCOL_VERSION,
                ok=False,
                error=ErrorShape(ERR_UNKNOWN_COMMAND, f"Unknown command: {req.cmd}"),
            )

        args = argparse.Namespace(**req.args)
        try:
            payload = payload_fn(args, cfg, warm.graph())
        except _IndexStale as exc:
            return Response(
                v=PROTOCOL_VERSION,
                ok=False,
                error=ErrorShape(ERR_STALE_INDEX, str(exc)),
            )
        except (ProtocolMismatch, ValueError) as exc:
            return Response(
                v=PROTOCOL_VERSION,
                ok=False,
                error=ErrorShape(ERR_BAD_ARGS, str(exc)),
            )
        except Exception as exc:  # noqa: BLE001 — includes PayloadError; -> backend_error
            return Response(
                v=PROTOCOL_VERSION,
                ok=False,
                error=ErrorShape(ERR_BACKEND_ERROR, str(exc)),
            )
        return Response(v=PROTOCOL_VERSION, ok=True, result=serialize(payload))
