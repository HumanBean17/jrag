from __future__ import annotations

from typing import Any

from mcp_v2 import describe_v2, neighbors_v2, search_v2
from server import _graph_meta_output


_EDGE_TYPES = (
    "ASYNC_CALLS",
    "CALLS",
    "DECLARES",
    "DECLARES_CLIENT",
    "EXPOSES",
    "EXTENDS",
    "HTTP_CALLS",
    "IMPLEMENTS",
    "INJECTS",
)


def _controller_method_with_calls(kuzu_graph) -> tuple[str, str]:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol) "
        "WHERE t.role = 'CONTROLLER' AND m.kind IN ['method', 'constructor'] "
        "AND (EXISTS { MATCH (:Symbol)-[:CALLS]->(m) } OR EXISTS { MATCH (m)-[:CALLS]->(:Symbol) }) "
        "RETURN m.id AS id, m.fqn AS fqn "
        "ORDER BY m.fqn LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"]), str(rows[0]["fqn"])


def _method_with_incoming_calls(kuzu_graph) -> tuple[str, str]:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) "
        "RETURN dst.id AS id LIMIT 1"
    )
    assert rows
    node_id = str(rows[0]["id"])
    fqn_rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.id = $id RETURN s.fqn AS fqn LIMIT 1",
        {"id": node_id},
    )
    assert fqn_rows
    return node_id, str(fqn_rows[0]["fqn"])


def _route_with_handler(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (:Symbol)-[:EXPOSES]->(r:Route) RETURN r.id AS id ORDER BY r.id LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"])


def test_describe_edge_summary_for_controller(kuzu_graph) -> None:
    node_id, fqn = _controller_method_with_calls(kuzu_graph)
    out = describe_v2(node_id, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    calls = out.record.edge_summary.get("CALLS", {"in": 0, "out": 0})
    callers = kuzu_graph.find_callers(fqn, limit=1000, exclude_external=False)
    callees = kuzu_graph.find_callees(fqn, limit=1000, exclude_external=False)
    assert int(calls.get("in", 0)) == len(callers)
    assert int(calls.get("out", 0)) == len(callees)


def test_describe_edge_summary_omits_zero_count_types(kuzu_graph) -> None:
    node_id, _ = _controller_method_with_calls(kuzu_graph)
    out = describe_v2(node_id, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    for edge_type in _EDGE_TYPES:
        if edge_type in out.record.edge_summary:
            continue
        rows = kuzu_graph._rows(  # noqa: SLF001
            f"MATCH (n {{id: $id}})-[e:{edge_type}]->() RETURN count(e) AS n "
            f"UNION ALL "
            f"MATCH (n {{id: $id}})<-[e:{edge_type}]-() RETURN count(e) AS n",
            {"id": node_id}
        )
        assert sum(int(r.get("n") or 0) for r in rows) == 0


def test_describe_edge_summary_for_route(kuzu_graph) -> None:
    route_id = _route_with_handler(kuzu_graph)
    out = describe_v2(route_id, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "route"
    assert out.record.edge_summary is not None
    exposes = out.record.edge_summary.get("EXPOSES", {"in": 0, "out": 0})
    assert int(exposes.get("in", 0)) >= 1


def test_search_populates_symbol_id_when_chunk_rooted_in_symbol(monkeypatch, kuzu_graph) -> None:
    rows: list[dict[str, Any]] = [
        {
            "filename": "A.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 10},
            "symbol_id": "sym:one",
            "primary_type_fqn": "com.example.A",
            "_rrf_score": 0.9,
            "text": "A",
        },
        {
            "filename": "B.java",
            "start": {"byte_offset": 10},
            "end": {"byte_offset": 20},
            "metadata": {"symbol_id": "sym:two"},
            "primary_type_fqn": "com.example.B",
            "_rrf_score": 0.8,
            "text": "B",
        },
        {
            "filename": "C.java",
            "start": {"byte_offset": 30},
            "end": {"byte_offset": 40},
            "metadata": '{"symbol_id":"sym:three"}',
            "primary_type_fqn": "com.example.C",
            "_rrf_score": 0.75,
            "text": "C",
        },
        {
            "filename": "raw.txt",
            "start": {"byte_offset": 20},
            "end": {"byte_offset": 30},
            "_rrf_score": 0.7,
            "text": "raw",
        },
    ]
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: rows)
    out = search_v2("query", graph=kuzu_graph)
    assert out.success is True
    rooted = [hit for hit in out.results if hit.fqn is not None]
    assert rooted
    assert all(hit.symbol_id is not None for hit in rooted)


def test_meta_returns_per_edge_type_counts() -> None:
    out = _graph_meta_output()
    assert out.success is True
    assert set(out.edge_counts.keys()) == set(_EDGE_TYPES)
    assert all(int(v) >= 0 for v in out.edge_counts.values())


def test_search_describe_neighbors_chain_end_to_end(kuzu_graph, monkeypatch) -> None:
    node_id, _ = _method_with_incoming_calls(kuzu_graph)
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {id: $id}) RETURN m.fqn AS fqn, m.role AS role, m.module AS module, "
        "m.microservice AS microservice, m.filename AS filename",
        {"id": node_id},
    )
    assert rows
    row = rows[0]
    monkeypatch.setattr(
        "mcp_v2.run_search",
        lambda *args, **kwargs: [
            {
                "filename": str(row.get("filename") or "x.java"),
                "start": {"byte_offset": 0},
                "end": {"byte_offset": 1},
                "symbol_id": node_id,
                "primary_type_fqn": str(row.get("fqn") or ""),
                "role": str(row.get("role") or ""),
                "module": str(row.get("module") or ""),
                "microservice": str(row.get("microservice") or ""),
                "_rrf_score": 0.95,
                "text": "match",
            }
        ],
    )
    search_out = search_v2("assign", graph=kuzu_graph)
    assert search_out.success is True
    assert search_out.results
    top_symbol_id = search_out.results[0].symbol_id
    assert top_symbol_id is not None
    describe_out = describe_v2(top_symbol_id, graph=kuzu_graph)
    assert describe_out.success is True
    assert describe_out.record is not None
    neighbors_out = neighbors_v2(top_symbol_id, direction="in", edge_types=["CALLS"], graph=kuzu_graph)
    assert neighbors_out.success is True
    assert neighbors_out.results
