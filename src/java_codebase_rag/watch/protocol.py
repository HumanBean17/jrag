"""IPC request/response contract + NDJSON codec for jrag watch daemon.

Pure data + codec logic: dataclasses, protocol version, valid commands,
encode/decode to newline-delimited JSON, and protocol mismatch exception.
No sockets, no I/O, no dependency on other watch modules.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any

PROTOCOL_VERSION: int = 1

VALID_CMDS: frozenset[str] = frozenset({
    "search",
    "find",
    "inspect",
    "callers",
    "callees",
    "flow",
})

# Error kind constants — used as Response.ErrorShape.kind values.
ERR_UNKNOWN_COMMAND = "unknown_command"
ERR_BAD_ARGS = "bad_args"
ERR_BACKEND_ERROR = "backend_error"
ERR_STALE_INDEX = "stale_index"
ERR_BUSY = "busy"


class ProtocolMismatch(Exception):
    """Raised when the protocol version in a request/response doesn't match PROTOCOL_VERSION."""

    def __init__(self, got: int):
        self.got = got
        super().__init__(f"Protocol version mismatch: expected {PROTOCOL_VERSION}, got {got}")


@dataclass
class Request:
    """IPC request from client to daemon."""

    v: int
    cmd: str
    args: dict[str, Any]


@dataclass
class ErrorShape:
    """Error detail in a failed Response."""

    kind: str
    message: str


@dataclass
class Response:
    """IPC response from daemon to client."""

    v: int
    ok: bool
    result: Any | None = None
    error: ErrorShape | None = None


def encode_request(r: Request) -> bytes:
    """Encode a Request to newline-delimited JSON."""
    return (json.dumps(asdict(r), default=str) + "\n").encode("utf-8")


def decode_request(line: bytes) -> Request:
    """Decode a Request from newline-delimited JSON.

    Raises:
        ProtocolMismatch: if the protocol version doesn't match
        ValueError: if the command is invalid or line is blank
    """
    if not line or line.strip() == b"":
        raise ValueError("Blank line")

    parsed = json.loads(line.decode("utf-8"))

    if parsed["v"] != PROTOCOL_VERSION:
        raise ProtocolMismatch(got=parsed["v"])

    if parsed["cmd"] not in VALID_CMDS:
        raise ValueError(f"Unknown command: {parsed['cmd']}")

    return Request(**parsed)


def encode_response(r: Response) -> bytes:
    """Encode a Response to newline-delimited JSON."""
    return (json.dumps(asdict(r), default=str) + "\n").encode("utf-8")


def decode_response(line: bytes) -> Response:
    """Decode a Response from newline-delimited JSON.

    Raises:
        ProtocolMismatch: if the protocol version doesn't match
        ValueError: if the line is blank
    """
    if not line or line.strip() == b"":
        raise ValueError("Blank line")

    parsed = json.loads(line.decode("utf-8"))

    if parsed["v"] != PROTOCOL_VERSION:
        raise ProtocolMismatch(got=parsed["v"])

    # Reconstruct ErrorShape if present
    error = None
    if parsed.get("error"):
        error = ErrorShape(**parsed["error"])

    return Response(
        v=parsed["v"],
        ok=parsed["ok"],
        result=parsed.get("result"),
        error=error,
    )
