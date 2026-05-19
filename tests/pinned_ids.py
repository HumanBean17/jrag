"""Pinned bank-chat symbol ids shared across MCP regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_v2 import _CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN

if TYPE_CHECKING:
    from kuzu_queries import KuzuGraph


def client_message_processor_process_id(graph: KuzuGraph) -> str:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {fqn: $fqn}) RETURN m.id AS id LIMIT 1",
        {"fqn": _CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN},
    )
    assert rows, f"missing pinned method {_CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN}"
    return str(rows[0]["id"])
