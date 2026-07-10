"""Tests for watch/protocol.py — IPC request/response contract + NDJSON codec."""

import json
from dataclasses import dataclass

import pytest

from java_codebase_rag.watch.protocol import (
    PROTOCOL_VERSION,
    VALID_CMDS,
    ErrorShape,
    ProtocolMismatch,
    Request,
    Response,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


class TestRequestRoundTrip:
    """Test Request encoding/decoding for all VALID_CMDS."""

    def test_all_valid_commands(self):
        """Every command in VALID_CMDS should round-trip correctly."""
        for cmd in VALID_CMDS:
            args = {"query": f"test-{cmd}", "limit": 10}
            req = Request(v=PROTOCOL_VERSION, cmd=cmd, args=args)

            encoded = encode_request(req)
            decoded = decode_request(encoded)

            assert decoded == req
            assert decoded.cmd == cmd
            assert decoded.args == args


class TestResponseRoundTrip:
    """Test Response encoding/decoding for success and error cases."""

    def test_success_response(self):
        """Success response with result should round-trip."""
        result = {"matches": ["foo.java", "bar.java"], "total": 2}
        resp = Response(v=PROTOCOL_VERSION, ok=True, result=result)

        encoded = encode_response(resp)
        decoded = decode_response(encoded)

        assert decoded == resp
        assert decoded.ok is True
        assert decoded.result == result
        assert decoded.error is None

    def test_error_response(self):
        """Error response with ErrorShape should round-trip."""
        error = ErrorShape(kind="backend_error", message="Connection failed")
        resp = Response(v=PROTOCOL_VERSION, ok=False, error=error)

        encoded = encode_response(resp)
        decoded = decode_response(encoded)

        assert decoded == resp
        assert decoded.ok is False
        assert decoded.error == error
        assert decoded.result is None


class TestProtocolVersionMismatch:
    """Test protocol version validation."""

    def test_decode_request_with_bad_version(self):
        """decode_request should raise ProtocolMismatch for wrong version."""
        bad_version = 999
        req = Request(v=bad_version, cmd="search", args={"query": "test"})
        encoded = encode_request(req)

        # Manually corrupt the version in the encoded JSON
        parsed = json.loads(encoded.decode("utf-8"))
        parsed["v"] = bad_version
        corrupted = json.dumps(parsed).encode("utf-8") + b"\n"

        with pytest.raises(ProtocolMismatch) as exc_info:
            decode_request(corrupted)

        assert exc_info.value.got == bad_version

    def test_decode_response_with_bad_version(self):
        """decode_response should raise ProtocolMismatch for wrong version."""
        bad_version = 999
        resp = Response(v=bad_version, ok=True, result={"test": "data"})
        encoded = encode_response(resp)

        # Manually corrupt the version in the encoded JSON
        parsed = json.loads(encoded.decode("utf-8"))
        parsed["v"] = bad_version
        corrupted = json.dumps(parsed).encode("utf-8") + b"\n"

        with pytest.raises(ProtocolMismatch) as exc_info:
            decode_response(corrupted)

        assert exc_info.value.got == bad_version


class TestInvalidCommand:
    """Test command validation."""

    def test_unknown_command_raises_value_error(self):
        """decode_request should raise ValueError for unknown command."""
        req_dict = {
            "v": PROTOCOL_VERSION,
            "cmd": "not_a_real_command",
            "args": {"test": "data"}
        }
        encoded = json.dumps(req_dict).encode("utf-8") + b"\n"

        with pytest.raises(ValueError, match="command"):
            decode_request(encoded)


class TestEncodingFormat:
    """Test encoding format constraints."""

    def test_encoded_request_ends_with_newline(self):
        """Every encoded request should end with \\n."""
        req = Request(v=PROTOCOL_VERSION, cmd="search", args={"query": "test"})
        encoded = encode_request(req)

        assert encoded.endswith(b"\n")
        # Should be a single line (no embedded newlines)
        lines = encoded.decode("utf-8").split("\n")
        assert len(lines) == 2  # content + empty string after final \n

    def test_encoded_response_ends_with_newline(self):
        """Every encoded response should end with \\n."""
        resp = Response(v=PROTOCOL_VERSION, ok=True, result={"status": "ok"})
        encoded = encode_response(resp)

        assert encoded.endswith(b"\n")
        # Should be a single line (no embedded newlines)
        lines = encoded.decode("utf-8").split("\n")
        assert len(lines) == 2  # content + empty string after final \n

    def test_blank_line_raises_value_error(self):
        """Blank lines should raise ValueError in decode functions."""
        blank_line = b"\n"
        empty_line = b""

        with pytest.raises(ValueError):
            decode_request(blank_line)

        with pytest.raises(ValueError):
            decode_request(empty_line)

        with pytest.raises(ValueError):
            decode_response(blank_line)

        with pytest.raises(ValueError):
            decode_response(empty_line)


class TestErrorKinds:
    """Test error kind constants."""

    def test_error_shape_with_all_kinds(self):
        """All documented error kinds should work in ErrorShape."""
        error_kinds = [
            "unknown_command",
            "bad_args",
            "backend_error",
            "stale_index",
            "busy",
        ]

        for kind in error_kinds:
            error = ErrorShape(kind=kind, message=f"Test {kind}")
            resp = Response(v=PROTOCOL_VERSION, ok=False, error=error)

            encoded = encode_response(resp)
            decoded = decode_response(encoded)

            assert decoded.error.kind == kind
            assert decoded.error.message == f"Test {kind}"
