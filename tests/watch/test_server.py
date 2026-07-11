"""Tests for ``watch/server.py`` — AF_UNIX socket server + per-command dispatch.

These are TRANSPORT/DISPATCH tests: a stub in-process ``WarmResources`` and a
stub ``PAYLOAD_FNS`` mapping exercise the socket line discipline, the cmd->payload
routing, and the error-kind mapping. Real payload round-trip fidelity is
verified in Task 11 (daemon), not here.
"""

from __future__ import annotations

import argparse
import json
import socket
from types import SimpleNamespace

import pytest

from java_codebase_rag.watch import server
from java_codebase_rag.watch.protocol import (
    ERR_BAD_ARGS,
    ERR_BACKEND_ERROR,
    ERR_STALE_INDEX,
    ERR_UNKNOWN_COMMAND,
    PROTOCOL_VERSION,
    Request,
    decode_response,
    encode_request,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubWarm:
    """In-process WarmResources: ``graph()`` returns a fixed sentinel."""

    def __init__(self, graph=None):
        self._graph = graph if graph is not None else "stub-graph"

    def graph(self):
        return self._graph


def _stub_returning(value):
    """Build a payload fn that returns ``value`` and records its call args."""
    def _fn(args, cfg, graph):
        _fn.last = (args, cfg, graph)
        return value
    _fn.last = None
    return _fn


def _stub_raising(exc):
    """Build a payload fn that raises ``exc``."""
    def _fn(args, cfg, graph):
        raise exc
    return _fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_cfg(tmp_path):
    return SimpleNamespace(index_dir=tmp_path / "idx")


@pytest.fixture
def stub_warm():
    return _StubWarm()


@pytest.fixture
def running_server(stub_warm, stub_cfg, monkeypatch):
    """A started WatchServer with a search stub returning a known payload."""
    monkeypatch.setattr(
        server, "PAYLOAD_FNS",
        {"search": _stub_returning({"hits": ["a", "b"]})},
    )
    ws = server.WatchServer(stub_warm, stub_cfg)
    ws.start()
    yield ws
    ws.shutdown()


def _sock_path(ws):
    return server.paths.socket_path(ws.cfg.index_dir)


def _connect(ws):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(str(_sock_path(ws)))
    return s


def _readline(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _round_trip(sock, req):
    sock.sendall(encode_request(req))
    raw = _readline(sock)
    assert raw.endswith(b"\n"), "response not newline-terminated"
    return decode_response(raw)


# ---------------------------------------------------------------------------
# serialize()
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_plain_dict_and_list_passthrough(self):
        payload = {"root_id": "x", "nodes": {"a": {"id": "a"}}, "edges": [], "truncated": False}
        assert server.serialize(payload) == payload
        assert server.serialize([1, "a", None, 2.5, True]) == [1, "a", None, 2.5, True]

    def test_pydantic_model_dumps_to_json_mode(self):
        from pydantic import BaseModel

        class M(BaseModel):
            name: str
            count: int = 3

        assert server.serialize(M(name="foo")) == {"name": "foo", "count": 3}

    def test_dict_with_nested_pydantic_is_recursed(self):
        from pydantic import BaseModel

        class Hit(BaseModel):
            fqn: str

        payload = {"describe": Hit(fqn="com.x.Y"), "node_id": "n1"}
        assert server.serialize(payload) == {
            "describe": {"fqn": "com.x.Y"}, "node_id": "n1",
        }


# ---------------------------------------------------------------------------
# dispatch() routing + error mapping
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    def test_success_returns_serialized_payload_and_rebuilds_args(self, stub_warm, stub_cfg, monkeypatch):
        search = _stub_returning({"matches": ["a"]})
        monkeypatch.setattr(server, "PAYLOAD_FNS", {"search": search})
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "x", "limit": 5}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is True
        assert resp.v == PROTOCOL_VERSION
        assert resp.result == {"matches": ["a"]}
        # args reconstructed as an argparse.Namespace before the payload call
        args_passed, cfg_passed, graph_passed = search.last
        assert isinstance(args_passed, argparse.Namespace)
        assert args_passed.query == "x"
        assert args_passed.limit == 5
        # cfg passed straight through; graph comes from warm.graph()
        assert cfg_passed is stub_cfg
        assert graph_passed == "stub-graph"

    def test_unknown_command_yields_unknown_command_kind(self, stub_warm, stub_cfg, monkeypatch):
        # dispatch defends in depth: decode_request already rejects unknown cmds,
        # but dispatch checks the table too.
        monkeypatch.setattr(server, "PAYLOAD_FNS", {})
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="bogus", args={}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is False
        assert resp.error.kind == ERR_UNKNOWN_COMMAND

    def test_index_stale_maps_to_stale_index(self, stub_warm, stub_cfg, monkeypatch):
        from java_codebase_rag.jrag import _IndexStale

        monkeypatch.setattr(
            server, "PAYLOAD_FNS",
            {"find": _stub_raising(_IndexStale("ontology too old"))},
        )
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="find", args={"query": "x"}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is False
        assert resp.error.kind == ERR_STALE_INDEX
        assert "ontology too old" in resp.error.message

    def test_value_error_maps_to_bad_args(self, stub_warm, stub_cfg, monkeypatch):
        monkeypatch.setattr(
            server, "PAYLOAD_FNS",
            {"inspect": _stub_raising(ValueError("bad arg"))},
        )
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="inspect", args={"query": "x"}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is False
        assert resp.error.kind == ERR_BAD_ARGS

    def test_generic_exception_maps_to_backend_error(self, stub_warm, stub_cfg, monkeypatch):
        monkeypatch.setattr(
            server, "PAYLOAD_FNS",
            {"callers": _stub_raising(RuntimeError("boom"))},
        )
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="callers", args={"query": "x"}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is False
        assert resp.error.kind == ERR_BACKEND_ERROR
        assert "boom" in resp.error.message

    def test_payload_error_maps_to_backend_error(self, stub_warm, stub_cfg, monkeypatch):
        # PayloadError is a plain Exception subclass, so it falls through to
        # the generic backend_error bucket (per the pinned contract).
        from java_codebase_rag.jrag_envelope import Envelope
        from java_codebase_rag.read_payloads import PayloadError

        monkeypatch.setattr(
            server, "PAYLOAD_FNS",
            {"flow": _stub_raising(PayloadError(Envelope(status="error", message="nope"), 2))},
        )
        ws = server.WatchServer(stub_warm, stub_cfg)

        resp = ws.dispatch(
            Request(v=PROTOCOL_VERSION, cmd="flow", args={"query": "/x"}),
            stub_warm, stub_cfg,
        )

        assert resp.ok is False
        assert resp.error.kind == ERR_BACKEND_ERROR


# ---------------------------------------------------------------------------
# Socket transport (start/serve/shutdown)
# ---------------------------------------------------------------------------


class TestSocketTransport:
    def test_valid_request_round_trips_payload(self, running_server):
        sock = _connect(running_server)
        try:
            resp = _round_trip(
                sock, Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "foo", "limit": 3}),
            )
        finally:
            sock.close()

        assert resp.ok is True
        assert resp.v == PROTOCOL_VERSION
        assert resp.result == {"hits": ["a", "b"]}

    def test_bad_protocol_version_yields_bad_args(self, running_server):
        # Hand-craft a v=999 line; decode_request raises ProtocolMismatch, which
        # the serve loop maps to ERR_BAD_ARGS.
        sock = _connect(running_server)
        try:
            sock.sendall(json.dumps({"v": 999, "cmd": "search", "args": {}}).encode() + b"\n")
            resp = decode_response(_readline(sock))
        finally:
            sock.close()

        assert resp.ok is False
        assert resp.error.kind == ERR_BAD_ARGS

    def test_payload_raising_yields_backend_error_over_socket(self, running_server, monkeypatch):
        monkeypatch.setattr(server, "PAYLOAD_FNS", {"search": _stub_raising(RuntimeError("db down"))})
        sock = _connect(running_server)
        try:
            resp = _round_trip(sock, Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "x"}))
        finally:
            sock.close()

        assert resp.ok is False
        assert resp.error.kind == ERR_BACKEND_ERROR
        assert "db down" in resp.error.message

    def test_two_requests_on_one_connection(self, running_server):
        # Line discipline: two sequential newline-delimited requests on a single
        # connection are each answered.
        sock = _connect(running_server)
        try:
            r1 = _round_trip(sock, Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "a"}))
            r2 = _round_trip(sock, Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "b"}))
        finally:
            sock.close()

        assert r1.ok and r2.ok
        assert r1.result == {"hits": ["a", "b"]}
        assert r2.result == {"hits": ["a", "b"]}

    def test_midline_close_is_tolerated(self, running_server):
        # A connection that closes mid-line must not crash the accept loop; a
        # fresh connection immediately after still gets answered.
        sock = _connect(running_server)
        sock.sendall(b'{"v":1,"cmd":"search","args":{"query"')  # partial, no newline
        sock.shutdown(socket.SHUT_WR)
        sock.close()

        fresh = _connect(running_server)
        try:
            resp = _round_trip(fresh, Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "x"}))
        finally:
            fresh.close()

        assert resp.ok is True
        assert resp.result == {"hits": ["a", "b"]}

    def test_socket_file_is_chmod_600(self, running_server):
        import os
        mode = os.stat(_sock_path(running_server)).st_mode & 0o777
        assert mode == 0o600
