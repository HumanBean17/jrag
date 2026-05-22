"""Tier-1 bank-chat integration: brownfield annotations in a realistic multi-module layout.

Annotations live in ``chat-contracts``; usage spans ``chat-assign``, ``chat-app``,
and ``chat-engine``. Unit-level edge cases stay under ``tests/fixtures/brownfield_*``.
"""
from __future__ import annotations

from pathlib import Path

import kuzu
import pytest

from kuzu_queries import KuzuGraph


def _connect(db_path: Path) -> kuzu.Connection:
    return kuzu.Connection(kuzu.Database(str(db_path), read_only=True))


def _scalar(conn: kuzu.Connection, query: str, params: dict | None = None) -> int:
    r = conn.execute(query, params or {})
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def _column(conn: kuzu.Connection, query: str, params: dict | None = None) -> list:
    r = conn.execute(query, params or {})
    out: list = []
    while r.has_next():
        out.append(r.get_next())
    return out


def test_bank_graph_meta_brownfield_percentages_positive(kuzu_db_path: Path) -> None:
    meta = KuzuGraph(str(kuzu_db_path)).meta()
    assert float(meta.get("routes_from_brownfield_pct") or 0.0) > 0.0
    assert float(meta.get("http_clients_from_brownfield_pct") or 0.0) > 0.0
    assert float(meta.get("async_producers_from_brownfield_pct") or 0.0) > 0.0


def test_bank_brownfield_http_route_on_chat_events(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    rows = _column(
        conn,
        "MATCH (rt:Route) "
        "WHERE rt.path_template = '/api/v1/chat/events' AND rt.method = 'POST' "
        "RETURN rt.id",
    )
    assert rows, "expected brownfield/MVC route for POST /api/v1/chat/events"
    n_exposes = _scalar(
        conn,
        "MATCH (s:Symbol)-[:EXPOSES]->(rt:Route) "
        "WHERE rt.path_template = '/api/v1/chat/events' AND rt.method = 'POST' "
        "RETURN count(*)",
    )
    assert n_exposes >= 1


def test_bank_codebase_http_client_on_join_operator(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    rows = _column(
        conn,
        "MATCH (c:Client) "
        "WHERE c.member_fqn CONTAINS 'ChatCoreJoinClient' "
        "AND c.member_fqn CONTAINS 'joinOperator' "
        "RETURN c.target_service, c.source_layer",
    )
    assert rows, "expected Client row for ChatCoreJoinClient#joinOperator"
    target, layer = rows[0][0], rows[0][1]
    assert str(target) == "chat-core"
    assert str(layer) == "layer_c_source"


def test_bank_compliance_listener_async_route_layer_c(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    topics = _column(
        conn,
        "MATCH (m:Symbol)-[:EXPOSES]->(rt:Route) "
        "WHERE m.fqn CONTAINS 'ComplianceReviewListener' "
        "AND m.fqn CONTAINS 'onComplianceReview' "
        "RETURN DISTINCT rt.topic",
    )
    topic_set = {str(t[0]) for t in topics}
    assert topic_set == {"banking.chat.compliance.review"}


def test_bank_event_stream_bridge_codebase_producers_container(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    rows = _column(
        conn,
        "MATCH (m:Symbol)-[:DECLARES_PRODUCER]->(pr:Producer) "
        "WHERE m.fqn CONTAINS 'EventStreamBridge' AND m.fqn CONTAINS 'sendToAudit' "
        "RETURN pr.topic, pr.source_layer",
    )
    topics = {str(r[0]) for r in rows}
    assert topics >= {"banking.chat.audit", "banking.chat.audit.dlq"}
    assert all(str(r[1]) == "layer_c_source" for r in rows)
