"""Tests for ``watch/client.py`` — IPC client + cold fallback seam.

These exercise the client against an in-process FAKE server: a real bound
``AF_UNIX`` socket speaking the watch protocol with a stub responder. No real
index, no real daemon — the fake server crafts arbitrary response frames
(success, ``ok=False``, wrong-version) so every branch of the pinned contract
is covered.

The cold-fallback seam (:func:`get_payload`) is verified by pointing ``cfg`` at
an index dir with NO socket (-> ``DaemonUnavailable``) and asserting the
``cold_core`` callable runs with an ``argparse.Namespace`` + the result of
``_load_graph(cfg)`` (monkeypatched to a sentinel so no real graph is loaded).

The find query-mode ``SymbolHit`` rebuild (the task-8 brief said "pass-through";
the renderer's attribute access makes that break byte-identity, so rows are
rebuilt — see task-8 report) is pinned here.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from java_codebase_rag.watch import client
from java_codebase_rag.watch.client import (
    DaemonError,
    DaemonUnavailable,
    get_payload,
    is_daemon_alive,
    request,
)
from java_codebase_rag.watch.paths import pid_path, socket_path
from java_codebase_rag.watch.protocol import (
    ERR_BACKEND_ERROR,
    PROTOCOL_VERSION,
    ErrorShape,
    Request,
    Response,
    decode_request,
    encode_response,
)


# ---------------------------------------------------------------------------
# in-process fake AF_UNIX server
# ---------------------------------------------------------------------------


class _FakeServer:
    """A real bound ``AF_UNIX`` socket speaking the protocol with a stub responder.

    For each accepted connection the responder receives the raw request line
    (bytes) and returns the raw response line (bytes) to write back (or ``None``
    to write nothing). This lets a test craft any frame: a normal encoded
    ``Response``, an ``ok=False`` frame, or a wrong-version frame (to trigger
    ``ProtocolMismatch``). Each received request line is appended to
    ``self.requests`` so a test can assert what the client sent.
    """

    def __init__(self, index_dir: Path, responder):
        self.index_dir = index_dir
        self.responder = responder
        self.requests: list[bytes] = []
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        sp = socket_path(self.index_dir)
        if sp.exists():
            sp.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(sp))
        sock.listen(4)
        sock.settimeout(0.2)  # poll-style accept so _stop is checked promptly
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="fake-watch-server", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # listening socket closed (stop)
            try:
                line = self._readline(conn)
                if line:
                    self.requests.append(line)
                out = self.responder(line)
                if out is not None:
                    conn.sendall(out)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _readline(conn: socket.socket) -> bytes:
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        return buf

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        sp = socket_path(self.index_dir)
        if sp.exists():
            try:
                sp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _write_live_pid(index_dir: Path) -> None:
    """Write our own (alive) pid into the pid file so read_holder returns a live pid."""
    pid_path(index_dir).write_text(f"{os.getpid()}\n", encoding="utf-8")


def _dead_pid() -> int:
    """Return a pid guaranteed dead + reaped (spawn child, wait, take its pid)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _write_dead_pid(index_dir: Path) -> None:
    pid_path(index_dir).write_text(f"{_dead_pid()}\n", encoding="utf-8")


@pytest.fixture
def index_dir(tmp_path):
    """A unique index dir per test (-> unique socket/pid filenames in runtime dir)."""
    d = tmp_path / "idx"
    yield d
    # tidy any pid file we wrote (socket is unlinked by _FakeServer.stop)
    try:
        pid_path(d).unlink(missing_ok=True)
    except OSError:
        pass


def _ok_result(value):
    """Build a responder returning ``encode_response(ok=True, result=value)``."""
    return lambda line: encode_response(Response(v=PROTOCOL_VERSION, ok=True, result=value))


# ---------------------------------------------------------------------------
# (a) is_daemon_alive
# ---------------------------------------------------------------------------


class TestIsDaemonAlive:
    def test_false_when_no_socket_and_no_pid(self, index_dir):
        assert is_daemon_alive(index_dir) is False

    def test_false_when_socket_exists_but_no_pid_file(self, index_dir):
        srv = _FakeServer(index_dir, lambda line: None)
        srv.start()
        try:
            # socket bound, but no pid file at all
            assert is_daemon_alive(index_dir) is False
        finally:
            srv.stop()

    def test_false_when_socket_exists_but_pid_is_dead(self, index_dir):
        srv = _FakeServer(index_dir, lambda line: None)
        srv.start()
        try:
            _write_dead_pid(index_dir)
            assert is_daemon_alive(index_dir) is False
        finally:
            srv.stop()

    def test_true_when_socket_and_live_pid(self, index_dir):
        srv = _FakeServer(index_dir, lambda line: None)
        srv.start()
        try:
            _write_live_pid(index_dir)
            assert is_daemon_alive(index_dir) is True
        finally:
            srv.stop()


# ---------------------------------------------------------------------------
# (b)/(c) request
# ---------------------------------------------------------------------------


class TestRequest:
    def test_returns_result_for_valid_command(self, index_dir):
        payload = {"matches": ["a", "b"], "count": 2}
        srv = _FakeServer(index_dir, _ok_result(payload))
        srv.start()
        _write_live_pid(index_dir)
        try:
            result = request(index_dir, "search", {"query": "x", "limit": 5})
        finally:
            srv.stop()
        assert result == payload
        # the client sent a properly versioned, newline-delimited request
        assert len(srv.requests) == 1
        sent = decode_request(srv.requests[0])
        assert sent == Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "x", "limit": 5})

    def test_raises_daemon_unavailable_when_not_alive(self, index_dir):
        # no socket, no pid
        with pytest.raises(DaemonUnavailable):
            request(index_dir, "search", {"query": "x"})

    def test_raises_daemon_error_on_ok_false_backend_error(self, index_dir):
        def responder(line):
            return encode_response(
                Response(v=PROTOCOL_VERSION, ok=False, error=ErrorShape(ERR_BACKEND_ERROR, "kaboom"))
            )

        srv = _FakeServer(index_dir, responder)
        srv.start()
        _write_live_pid(index_dir)
        try:
            with pytest.raises(DaemonError) as ei:
                request(index_dir, "search", {"query": "x"})
        finally:
            srv.stop()
        assert ei.value.kind == ERR_BACKEND_ERROR
        assert ei.value.message == "kaboom"

    def test_protocol_mismatch_raises_daemon_unavailable(self, index_dir):
        # An OLDER daemon answers with a different protocol version -> the client
        # must map ProtocolMismatch to DaemonUnavailable (cold fallback), not crash.
        def responder(line):
            bad = json.dumps({"v": 999, "ok": True, "result": {}}) + "\n"
            return bad.encode("utf-8")

        srv = _FakeServer(index_dir, responder)
        srv.start()
        _write_live_pid(index_dir)
        try:
            with pytest.raises(DaemonUnavailable):
                request(index_dir, "search", {"query": "x"})
        finally:
            srv.stop()

    def test_daemon_closing_mid_response_raises_daemon_unavailable(self, index_dir):
        # Daemon accepts then closes without a newline -> blank/partial line ->
        # DaemonUnavailable (cold fallback), not a crash.
        srv = _FakeServer(index_dir, lambda line: b"")
        srv.start()
        _write_live_pid(index_dir)
        try:
            with pytest.raises(DaemonUnavailable):
                request(index_dir, "search", {"query": "x"})
        finally:
            srv.stop()


# ---------------------------------------------------------------------------
# (d)/(e)/(f) get_payload
# ---------------------------------------------------------------------------


class TestGetPayload:
    def test_returns_reconstructed_payload_when_alive(self, index_dir):
        # search: serialized SearchOutput -> reconstructed SearchOutput object.
        serialized = {"success": True, "results": [], "message": None}
        srv = _FakeServer(index_dir, _ok_result(serialized))
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        cold_calls = []

        def cold_core(args, cfg, graph):
            cold_calls.append(True)
            return "COLD"

        try:
            out = get_payload("search", {"query": "x", "limit": 5}, cfg, cold_core=cold_core)
        finally:
            srv.stop()

        from java_codebase_rag.mcp.mcp_v2 import SearchOutput

        assert isinstance(out, SearchOutput)
        assert out.success is True
        assert out.results == []
        assert cold_calls == [], "cold_core must not run when the daemon answered"

    def test_cold_fallback_when_no_daemon(self, index_dir, monkeypatch):
        # no socket -> DaemonUnavailable -> cold fallback via cold_core + _load_graph.
        cfg = SimpleNamespace(index_dir=index_dir)
        sentinel_graph = object()
        monkeypatch.setattr(client, "_load_graph", lambda c: sentinel_graph)
        seen = {}

        def cold_core(args, cfg, graph):
            seen["args"] = args
            seen["cfg"] = cfg
            seen["graph"] = graph
            return "COLD-SENTINEL"

        out = get_payload("callers", {"query": "foo", "limit": 10}, cfg, cold_core=cold_core)

        assert out == "COLD-SENTINEL"
        # cold_core received an argparse.Namespace rebuilt from the args dict...
        assert isinstance(seen["args"], argparse.Namespace)
        assert seen["args"].query == "foo"
        assert seen["args"].limit == 10
        # ...the same cfg, and the graph from _load_graph(cfg).
        assert seen["cfg"] is cfg
        assert seen["graph"] is sentinel_graph

    def test_protocol_mismatch_triggers_cold_fallback(self, index_dir, monkeypatch):
        def responder(line):
            return (json.dumps({"v": 999, "ok": True, "result": {}}) + "\n").encode("utf-8")

        srv = _FakeServer(index_dir, responder)
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        monkeypatch.setattr(client, "_load_graph", lambda c: "graph")
        called = []

        def cold_core(args, cfg, graph):
            called.append(True)
            return "COLD"

        try:
            out = get_payload("search", {"query": "x"}, cfg, cold_core=cold_core)
        finally:
            srv.stop()
        assert out == "COLD"
        assert called == [True]

    def test_cold_fallback_on_daemon_error(self, index_dir, monkeypatch):
        # ok=False (backend_error) -> COLD fallback, so error envelopes/rc stay
        # byte-identical (the cold core re-raises PayloadError -> handler renders it).
        def responder(line):
            return encode_response(
                Response(v=PROTOCOL_VERSION, ok=False, error=ErrorShape(ERR_BACKEND_ERROR, "boom"))
            )

        srv = _FakeServer(index_dir, responder)
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        monkeypatch.setattr(client, "_load_graph", lambda c: "graph")

        out = get_payload("search", {"query": "x"}, cfg, cold_core=lambda a, c, g: "COLD")
        try:
            pass
        finally:
            srv.stop()
        assert out == "COLD"

    def test_find_query_mode_reconstructs_symbol_hits(self, index_dir):
        # The renderer does `row.id`/`row.fqn` (attribute access on SymbolHit),
        # so the daemon's serialized rows MUST be rebuilt into SymbolHit objects
        # (not passed through as dicts) -- otherwise the hot path breaks. Pin it.
        from java_codebase_rag.graph.ladybug_queries import SymbolHit

        row = {
            "id": "s1", "kind": "class", "name": "Foo", "fqn": "com.x.Foo",
            "package": "com.x", "module": "m", "microservice": "svc",
            "filename": "Foo.java", "start_line": 1, "end_line": 2,
            "start_byte": 0, "end_byte": 10, "modifiers": ["public"],
            "annotations": [], "capabilities": [], "role": "SERVICE",
            "signature": "void foo()", "parent_id": "", "resolved": True,
        }
        serialized = {
            "mode": "query", "rows": [row], "raw_truncated": False,
            "post_filter_active": False, "limit": 20, "query": "Foo", "kinds": None,
            "matched_mode": "exact", "identifier_matched": True,
        }
        srv = _FakeServer(index_dir, _ok_result(serialized))
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        try:
            payload = get_payload("find", {"query": "Foo"}, cfg, cold_core=lambda a, c, g: "COLD")
        finally:
            srv.stop()

        assert payload["mode"] == "query"
        assert isinstance(payload["rows"][0], SymbolHit)
        assert payload["rows"][0].id == "s1"
        assert payload["rows"][0].fqn == "com.x.Foo"
        assert payload["rows"][0].modifiers == ["public"]
        assert payload["query"] == "Foo"
        assert payload["kinds"] is None

    def test_find_filter_mode_reconstructs_find_output(self, index_dir):
        from java_codebase_rag.mcp.mcp_v2 import FindOutput

        serialized = {
            "mode": "filter", "kind": "symbol",
            "out": {"success": True, "results": [], "message": None, "limit": 20},
            "limit": 20,
        }
        srv = _FakeServer(index_dir, _ok_result(serialized))
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        try:
            payload = get_payload("find", {"role": "SERVICE"}, cfg, cold_core=lambda a, c, g: "COLD")
        finally:
            srv.stop()

        assert payload["mode"] == "filter"
        assert payload["kind"] == "symbol"
        assert isinstance(payload["out"], FindOutput)
        assert payload["out"].success is True
        assert payload["limit"] == 20

    def test_inspect_reconstructs_describe_output(self, index_dir):
        from java_codebase_rag.mcp.mcp_v2 import DescribeOutput

        serialized = {
            "describe": {"success": True, "record": None, "message": None},
            "node_id": "n1", "node_fqn": "com.x.Foo", "file_location": "src/Foo.java:1",
        }
        srv = _FakeServer(index_dir, _ok_result(serialized))
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        try:
            payload = get_payload("inspect", {"query": "Foo"}, cfg, cold_core=lambda a, c, g: "COLD")
        finally:
            srv.stop()

        assert isinstance(payload["describe"], DescribeOutput)
        assert payload["describe"].success is True
        assert payload["node_id"] == "n1"
        assert payload["node_fqn"] == "com.x.Foo"
        assert payload["file_location"] == "src/Foo.java:1"

    def test_traversal_payload_passes_through_unchanged(self, index_dir):
        # callers/callees/flow are plain dicts already; no reconstruction needed.
        serialized = {
            "root_id": "r1", "nodes": {"r1": {"id": "r1", "kind": "symbol"}},
            "edges": [{"other_id": "x", "edge_type": "CALLS", "confidence": 0.9}],
            "noun": "callers", "warnings": [], "truncated": False,
            "is_external_entrypoint": False,
        }
        srv = _FakeServer(index_dir, _ok_result(serialized))
        srv.start()
        _write_live_pid(index_dir)
        cfg = SimpleNamespace(index_dir=index_dir)
        try:
            payload = get_payload("callers", {"query": "r1"}, cfg, cold_core=lambda a, c, g: "COLD")
        finally:
            srv.stop()
        assert payload == serialized
