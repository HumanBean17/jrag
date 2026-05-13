from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from _builders import build_kuzu_to
from kuzu_queries import KuzuGraph
from mcp_v2 import (
    _TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP,
    describe_v2,
    neighbors_v2,
    search_v2,
)
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

_ROLLUP_TYPE_KINDS = sorted(_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP)

_OVERRIDE_AXIS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "override_axis_rollup_smoke"


@pytest.fixture
def override_axis_graph(tmp_path: Path) -> KuzuGraph:
    db_path = tmp_path / "code_graph.kuzu"
    build_kuzu_to(_OVERRIDE_AXIS_FIXTURE, db_path, max_pass=5)
    return KuzuGraph(str(db_path))


def _collect_ids(cell: Any) -> list[str]:
    if cell is None:
        return []
    if isinstance(cell, list):
        return [str(x) for x in cell if x is not None and str(x) != ""]
    s = str(cell)
    return [s] if s else []


def _dispatch_down_override_method_ids(graph: KuzuGraph, method_id: str) -> list[str]:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(t:Symbol) "
        "MATCH (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t) "
        "MATCH (impl)-[:DECLARES]->(mover:Symbol) "
        "WHERE mover.signature = m.signature AND mover.id <> m.id "
        "RETURN collect(DISTINCT mover.id) AS ids",
        {"id": method_id},
    )
    if not rows:
        return []
    return list(dict.fromkeys(_collect_ids(rows[0].get("ids"))))


def _dispatch_up_declaration_method_ids(graph: KuzuGraph, method_id: str) -> list[str]:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol {id: $id})<-[:DECLARES]-(impl:Symbol) "
        "MATCH (impl)-[:IMPLEMENTS|EXTENDS]->(parent:Symbol) "
        "MATCH (parent)-[:DECLARES]->(decl_m:Symbol) "
        "WHERE decl_m.signature = m.signature AND decl_m.id <> m.id "
        "RETURN collect(DISTINCT decl_m.id) AS ids",
        {"id": method_id},
    )
    if not rows:
        return []
    return list(dict.fromkeys(_collect_ids(rows[0].get("ids"))))


def _edge_row_count_from_methods(graph: KuzuGraph, method_ids: list[str], rel: str) -> int:
    total = 0
    for mid in method_ids:
        rows = graph._rows(  # noqa: SLF001
            f"MATCH (x:Symbol {{id: $mid}})-[e:{rel}]->() RETURN count(e) AS n",
            {"mid": mid},
        )
        total += int(rows[0].get("n") or 0) if rows else 0
    return total


def _method_id_without_dispatch_rollups(kuzu_graph: KuzuGraph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol) "
        "WHERE m.kind = 'method' "
        "AND NOT list_contains(COALESCE(m.modifiers, []), 'static') "
        "AND NOT EXISTS { "
        "MATCH (m)<-[:DECLARES]-(t:Symbol), (impl:Symbol)-[:IMPLEMENTS|EXTENDS]->(t), "
        "(impl)-[:DECLARES]->(mover:Symbol) "
        "WHERE mover.signature = m.signature AND mover.id <> m.id } "
        "AND NOT EXISTS { "
        "MATCH (m)<-[:DECLARES]-(impl:Symbol), (impl)-[:IMPLEMENTS|EXTENDS]->(parent:Symbol), "
        "(parent)-[:DECLARES]->(decl:Symbol) "
        "WHERE decl.signature = m.signature AND decl.id <> m.id } "
        "RETURN m.id AS id LIMIT 1",
    )
    assert rows
    return str(rows[0]["id"])


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


def test_describe_class_with_brownfield_clients_emits_composed_key(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->(:Client) "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    assert rows
    tid = str(rows[0]["id"])
    n = int(rows[0]["n"] or 0)
    assert n >= 1
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary["DECLARES.DECLARES_CLIENT"]["out"] == n


def test_describe_controller_class_emits_composed_exposes(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->(:Route) "
        "WHERE t.role = 'CONTROLLER' AND t.kind = 'class' "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
    )
    assert rows
    tid = str(rows[0]["id"])
    n = int(rows[0]["n"] or 0)
    assert n >= 1
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary["DECLARES.EXPOSES"]["out"] == n


def test_describe_method_symbol_no_composed_keys(kuzu_graph) -> None:
    node_id, _ = _controller_method_with_calls(kuzu_graph)
    out = describe_v2(node_id, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "DECLARES.DECLARES_CLIENT" not in es
    assert "DECLARES.EXPOSES" not in es


def test_describe_pojo_no_composed_keys(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(:Symbol) "
        "WHERE t.kind IN $kinds "
        "AND NOT EXISTS { MATCH (t)-[:DECLARES]->(m:Symbol)-[:DECLARES_CLIENT]->() } "
        "AND NOT EXISTS { MATCH (t)-[:DECLARES]->(m:Symbol)-[:EXPOSES]->() } "
        "RETURN t.id AS id LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    assert rows
    tid = str(rows[0]["id"])
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "DECLARES.DECLARES_CLIENT" not in es
    assert "DECLARES.EXPOSES" not in es


def test_describe_interface_method_with_annotated_impl_emits_rollup(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ChatAssignmentPort"},
    )
    assert rows
    mid = str(rows[0]["id"])
    impl_ids = _dispatch_down_override_method_ids(kuzu_graph, mid)
    assert impl_ids
    want_ob = len(impl_ids)
    want_dc = _edge_row_count_from_methods(kuzu_graph, impl_ids, "DECLARES_CLIENT")
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert es.get("OVERRIDDEN_BY") == {"in": 0, "out": want_ob}
    assert es.get("OVERRIDDEN_BY.DECLARES_CLIENT") == {"in": 0, "out": want_dc}
    with pytest.raises(ValidationError):
        neighbors_v2(mid, direction="out", edge_types=["OVERRIDDEN_BY"], graph=kuzu_graph)


def test_describe_concrete_override_emits_overrides_rollup(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ConfigurableChatAssignment"},
    )
    assert rows
    mid = str(rows[0]["id"])
    decl_ids = _dispatch_up_declaration_method_ids(kuzu_graph, mid)
    assert decl_ids
    want_ov = len(decl_ids)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary.get("OVERRIDES") == {"in": 0, "out": want_ov}


def test_describe_method_no_overrides_silent(kuzu_graph) -> None:
    mid = _method_id_without_dispatch_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "OVERRIDDEN_BY" not in es
    assert "OVERRIDDEN_BY.DECLARES_CLIENT" not in es
    assert "OVERRIDDEN_BY.EXPOSES" not in es
    assert "OVERRIDES" not in es


def test_describe_abstract_method_with_route_override_emits_exposes(override_axis_graph: KuzuGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.AbstractApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    impl_ids = _dispatch_down_override_method_ids(override_axis_graph, mid)
    assert impl_ids
    want_ob = len(impl_ids)
    want_ex = _edge_row_count_from_methods(override_axis_graph, impl_ids, "EXPOSES")
    assert want_ex >= 1
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert es.get("OVERRIDDEN_BY") == {"in": 0, "out": want_ob}
    assert es.get("OVERRIDDEN_BY.EXPOSES") == {"in": 0, "out": want_ex}


def test_describe_interface_method_diamond_override_counts_once_per_upstream(
    override_axis_graph: KuzuGraph,
) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'shared' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.diamond.DiamondC"},
    )
    assert rows
    mid = str(rows[0]["id"])
    decl_ids = _dispatch_up_declaration_method_ids(override_axis_graph, mid)
    assert len(decl_ids) == 2
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary.get("OVERRIDES") == {"in": 0, "out": 2}
