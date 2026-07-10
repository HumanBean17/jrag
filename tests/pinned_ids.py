"""Pinned bank-chat symbol ids shared across MCP regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Bank-chat fixture anchor for CALLS-NOISE perf / ordering tests (HV34, Decision 31).
CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN = (
    "com.bank.chat.engine.processors.ClientMessageProcessor#process(ProcessingContext,InternalEvent)"
)

if TYPE_CHECKING:
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph


def client_message_processor_process_id(graph: LadybugGraph) -> str:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {fqn: $fqn}) RETURN m.id AS id LIMIT 1",
        {"fqn": CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN},
    )
    assert rows, f"missing pinned method {CLIENT_MESSAGE_PROCESSOR_PROCESS_FQN}"
    return str(rows[0]["id"])
