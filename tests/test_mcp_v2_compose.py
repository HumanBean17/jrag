from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from _builders import build_ladybug_to
from ladybug_queries import LadybugGraph
from mcp_v2 import (
    _NEIGHBOR_EDGE_TYPES_ADAPTER,
    _TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP,
    describe_v2,
    neighbors_v2,
    search_v2,
)
from server import _graph_meta_output


def _vector_stack_available() -> bool:
    """True when the optional vector stack (torch/sentence-transformers/lancedb) is installed.

    Search tests that monkeypatch ``mcp_v2.run_search`` still drive the vector path
    (which embeds the query), so they need the stack even when run_search is faked.
    Skip them on graph-only installs (macOS Intel). Mirrors tests/test_mcp_v2.py.
    """
    return all(importlib.util.find_spec(m) is not None for m in ("sentence_transformers", "lancedb"))


needs_vectors = pytest.mark.skipif(
    not _vector_stack_available(),
    reason="vector stack not installed (graph-only install; macOS Intel)",
)


_EDGE_TYPES = (
    "ASYNC_CALLS",
    "CALLS",
    "DECLARES",
    "DECLARES_CLIENT",
    "DECLARES_PRODUCER",
    "EXPOSES",
    "EXTENDS",
    "HTTP_CALLS",
    "IMPLEMENTS",
    "INJECTS",
    "OVERRIDES",
)

_ROLLUP_TYPE_KINDS = sorted(_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP)

_OVERRIDE_AXIS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "override_axis_rollup_smoke"


@pytest.fixture
def override_axis_graph(tmp_path: Path) -> LadybugGraph:
    db_path = tmp_path / "code_graph.lbug"
    build_ladybug_to(_OVERRIDE_AXIS_FIXTURE, db_path, max_pass=5)
    return LadybugGraph(str(db_path))


def _collect_ids(cell: Any) -> list[str]:
    if cell is None:
        return []
    if isinstance(cell, list):
        return [str(x) for x in cell if x is not None and str(x) != ""]
    s = str(cell)
    return [s] if s else []


def _dispatch_down_override_method_ids(graph: LadybugGraph, method_id: str) -> list[str]:
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


def _dispatch_up_declaration_method_ids(graph: LadybugGraph, method_id: str) -> list[str]:
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


def _edge_row_count_from_methods(graph: LadybugGraph, method_ids: list[str], rel: str) -> int:
    total = 0
    for mid in method_ids:
        rows = graph._rows(  # noqa: SLF001
            f"MATCH (x:Symbol {{id: $mid}})-[e:{rel}]->() RETURN count(e) AS n",
            {"mid": mid},
        )
        total += int(rows[0].get("n") or 0) if rows else 0
    return total


def _method_id_without_dispatch_rollups(ladybug_graph: LadybugGraph) -> str:
    rows = ladybug_graph._rows(  # noqa: SLF001
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


def _controller_method_with_calls(ladybug_graph) -> tuple[str, str]:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol) "
        "WHERE t.role = 'CONTROLLER' AND m.kind IN ['method', 'constructor'] "
        "AND (EXISTS { MATCH (:Symbol)-[:CALLS]->(m) } OR EXISTS { MATCH (m)-[:CALLS]->(:Symbol) }) "
        "RETURN m.id AS id, m.fqn AS fqn "
        "ORDER BY m.fqn LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"]), str(rows[0]["fqn"])


def _method_with_incoming_calls(ladybug_graph) -> tuple[str, str]:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (src:Symbol)-[:CALLS]->(dst:Symbol) "
        "RETURN dst.id AS id LIMIT 1"
    )
    assert rows
    node_id = str(rows[0]["id"])
    fqn_rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.id = $id RETURN s.fqn AS fqn LIMIT 1",
        {"id": node_id},
    )
    assert fqn_rows
    return node_id, str(fqn_rows[0]["fqn"])


def _route_with_handler(ladybug_graph) -> str:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (:Symbol)-[:EXPOSES]->(r:Route) RETURN r.id AS id ORDER BY r.id LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"])


def test_describe_edge_summary_for_controller(ladybug_graph) -> None:
    node_id, fqn = _controller_method_with_calls(ladybug_graph)
    out = describe_v2(node_id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    calls = out.record.edge_summary.get("CALLS", {"in": 0, "out": 0})
    callers = ladybug_graph.find_callers(fqn, limit=1000, exclude_external=False)
    callees = ladybug_graph.find_callees(fqn, limit=1000, exclude_external=False)
    assert int(calls.get("in", 0)) == len(callers)
    assert int(calls.get("out", 0)) == len(callees)


def test_describe_edge_summary_omits_zero_count_types(ladybug_graph) -> None:
    node_id, _ = _controller_method_with_calls(ladybug_graph)
    out = describe_v2(node_id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    for edge_type in _EDGE_TYPES:
        if edge_type in out.record.edge_summary:
            continue
        rows = ladybug_graph._rows(  # noqa: SLF001
            f"MATCH (n {{id: $id}})-[e:{edge_type}]->() RETURN count(e) AS n "
            f"UNION ALL "
            f"MATCH (n {{id: $id}})<-[e:{edge_type}]-() RETURN count(e) AS n",
            {"id": node_id}
        )
        assert sum(int(r.get("n") or 0) for r in rows) == 0


def test_describe_edge_summary_for_route(ladybug_graph) -> None:
    route_id = _route_with_handler(ladybug_graph)
    out = describe_v2(route_id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.kind == "route"
    assert out.record.edge_summary is not None
    exposes = out.record.edge_summary.get("EXPOSES", {"in": 0, "out": 0})
    assert int(exposes.get("in", 0)) >= 1


@needs_vectors
def test_search_populates_symbol_id_when_chunk_rooted_in_symbol(monkeypatch, ladybug_graph) -> None:
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
    out = search_v2("query", graph=ladybug_graph)
    assert out.success is True
    rooted = [hit for hit in out.results if hit.fqn is not None]
    assert rooted
    assert all(hit.symbol_id is not None for hit in rooted)


def test_meta_returns_per_edge_type_counts() -> None:
    out = _graph_meta_output()
    assert out.success is True
    assert set(out.edge_counts.keys()) == set(_EDGE_TYPES)
    assert all(int(v) >= 0 for v in out.edge_counts.values())


@needs_vectors
def test_search_describe_neighbors_chain_end_to_end(ladybug_graph, monkeypatch) -> None:
    node_id, _ = _method_with_incoming_calls(ladybug_graph)
    rows = ladybug_graph._rows(  # noqa: SLF001
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
    search_out = search_v2("assign", graph=ladybug_graph)
    assert search_out.success is True
    assert search_out.results
    top_symbol_id = search_out.results[0].symbol_id
    assert top_symbol_id is not None
    describe_out = describe_v2(top_symbol_id, graph=ladybug_graph)
    assert describe_out.success is True
    assert describe_out.record is not None
    neighbors_out = neighbors_v2(top_symbol_id, direction="in", edge_types=["CALLS"], graph=ladybug_graph)
    assert neighbors_out.success is True
    assert neighbors_out.results


def test_describe_type_rollups_include_declares_producer(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:DECLARES_PRODUCER]->(:Producer) "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    if not rows:
        pytest.skip("no type with DECLARES_PRODUCER members in fixture")
    tid = str(rows[0]["id"])
    n = int(rows[0]["n"] or 0)
    assert n >= 1
    out = describe_v2(tid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary["DECLARES.DECLARES_PRODUCER"]["out"] == n


def test_describe_class_with_brownfield_clients_emits_composed_key(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:DECLARES_CLIENT]->(:Client) "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    assert rows
    tid = str(rows[0]["id"])
    n = int(rows[0]["n"] or 0)
    assert n >= 1
    out = describe_v2(tid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary["DECLARES.DECLARES_CLIENT"]["out"] == n


def test_describe_controller_class_emits_composed_exposes(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->(:Route) "
        "WHERE t.role = 'CONTROLLER' AND t.kind = 'class' "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
    )
    assert rows
    tid = str(rows[0]["id"])
    n = int(rows[0]["n"] or 0)
    assert n >= 1
    out = describe_v2(tid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary["DECLARES.EXPOSES"]["out"] == n


def test_describe_method_symbol_no_composed_keys(ladybug_graph) -> None:
    node_id, _ = _controller_method_with_calls(ladybug_graph)
    out = describe_v2(node_id, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "DECLARES.DECLARES_CLIENT" not in es
    assert "DECLARES.EXPOSES" not in es


def test_describe_pojo_no_composed_keys(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(:Symbol) "
        "WHERE t.kind IN $kinds "
        "AND NOT EXISTS { MATCH (t)-[:DECLARES]->(m:Symbol)-[:DECLARES_CLIENT]->() } "
        "AND NOT EXISTS { MATCH (t)-[:DECLARES]->(m:Symbol)-[:EXPOSES]->() } "
        "RETURN t.id AS id LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    assert rows
    tid = str(rows[0]["id"])
    out = describe_v2(tid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "DECLARES.DECLARES_CLIENT" not in es
    assert "DECLARES.EXPOSES" not in es


def test_describe_interface_method_with_annotated_impl_emits_rollup(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ChatAssignmentPort"},
    )
    assert rows
    mid = str(rows[0]["id"])
    impl_ids = _dispatch_down_override_method_ids(ladybug_graph, mid)
    assert impl_ids
    want_ob = len(impl_ids)
    want_dc = _edge_row_count_from_methods(ladybug_graph, impl_ids, "DECLARES_CLIENT")
    out = describe_v2(mid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert es.get("OVERRIDDEN_BY") == {"in": 0, "out": want_ob}
    assert es.get("OVERRIDDEN_BY.DECLARES_CLIENT") == {"in": 0, "out": want_dc}
    out_ob = neighbors_v2(mid, direction="out", edge_types=["OVERRIDDEN_BY"], graph=ladybug_graph)
    assert out_ob.success is True


def test_describe_concrete_override_emits_overrides_rollup(ladybug_graph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ConfigurableChatAssignment"},
    )
    assert rows
    mid = str(rows[0]["id"])
    decl_ids = _dispatch_up_declaration_method_ids(ladybug_graph, mid)
    assert decl_ids
    want_ov = len(decl_ids)
    out = describe_v2(mid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary.get("OVERRIDES") == {"in": 0, "out": want_ov}


def test_describe_method_no_overrides_silent(ladybug_graph) -> None:
    mid = _method_id_without_dispatch_rollups(ladybug_graph)
    out = describe_v2(mid, graph=ladybug_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert "OVERRIDDEN_BY" not in es
    assert "OVERRIDDEN_BY.DECLARES_CLIENT" not in es
    assert "OVERRIDDEN_BY.EXPOSES" not in es
    assert "OVERRIDES" not in es


def test_describe_abstract_method_with_producer_override_emits_declares_producer(
    override_axis_graph: LadybugGraph,
) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'publish' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractproducer.AbstractProducerApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    impl_ids = _dispatch_down_override_method_ids(override_axis_graph, mid)
    assert impl_ids
    want_ob = len(impl_ids)
    want_dp = _edge_row_count_from_methods(override_axis_graph, impl_ids, "DECLARES_PRODUCER")
    assert want_dp >= 1
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    es = out.record.edge_summary
    assert es.get("OVERRIDDEN_BY") == {"in": 0, "out": want_ob}
    assert es.get("OVERRIDDEN_BY.DECLARES_PRODUCER") == {"in": 0, "out": want_dp}


def test_describe_abstract_method_with_route_override_emits_exposes(override_axis_graph: LadybugGraph) -> None:
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


def test_describe_method_edge_summary_overrides_merges_stored_in_with_dispatch_up_out(
    override_axis_graph: LadybugGraph,
) -> None:
    """Middle override: incoming [:OVERRIDES] from subclass + rollup dispatch-up must not zero `in`."""
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.MiddleApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    assert out.record.edge_summary.get("OVERRIDES") == {"in": 1, "out": 1}


def test_describe_interface_method_diamond_override_counts_once_per_upstream(
    override_axis_graph: LadybugGraph,
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


def test_overrides_stored_neighbors_in_matches_override_axis_impl_ids(override_axis_graph: LadybugGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.AbstractApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    want = sorted(_dispatch_down_override_method_ids(override_axis_graph, mid))
    out = neighbors_v2(mid, direction="in", edge_types=["OVERRIDES"], graph=override_axis_graph)
    assert out.success is True
    got = sorted({e.other.id for e in out.results})
    assert got == want


def test_overrides_stored_neighbors_out_matches_override_axis_decl_ids(override_axis_graph: LadybugGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'shared' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.diamond.DiamondC"},
    )
    assert rows
    mid = str(rows[0]["id"])
    want = sorted(_dispatch_up_declaration_method_ids(override_axis_graph, mid))
    out = neighbors_v2(mid, direction="out", edge_types=["OVERRIDES"], graph=override_axis_graph)
    assert out.success is True
    got = sorted({e.other.id for e in out.results})
    assert got == want


def test_overrides_rel_schema_round_trips(override_axis_graph: LadybugGraph) -> None:
    import ladybug

    conn = ladybug.Connection(ladybug.Database(override_axis_graph.db_path, read_only=True))
    tables = set()
    r = conn.execute("CALL show_tables() RETURN *;")
    while r.has_next():
        row = r.get_next()
        tables.add(str(row[1]))
    assert "OVERRIDES" in tables
    n = 0
    r2 = conn.execute("MATCH ()-[e:OVERRIDES]->() RETURN count(e) AS n")
    if r2.has_next():
        n = int(r2.get_next()[0] or 0)
    assert n > 0


def test_neighbors_edge_type_adapter_accepts_overrides() -> None:
    _NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python(["OVERRIDES"])


_OVERRIDE_AXIS_COMPOSED_KEYS = (
    "OVERRIDDEN_BY",
    "OVERRIDDEN_BY.DECLARES_CLIENT",
    "OVERRIDDEN_BY.DECLARES_PRODUCER",
    "OVERRIDDEN_BY.EXPOSES",
)


def _request_assignment_method_id(graph: LadybugGraph) -> str:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ChatAssignmentPort"},
    )
    assert rows
    return str(rows[0]["id"])


def test_override_axis_rollup_dispatch_matches_signature_walk_on_fixtures(
    ladybug_graph: LadybugGraph,
    override_axis_graph: LadybugGraph,
) -> None:
    """Guard: stored [:OVERRIDES] dispatch ids stay aligned with legacy signature walk on fixtures."""
    cases = [
        (ladybug_graph, _request_assignment_method_id(ladybug_graph)),
        (
            override_axis_graph,
            _override_axis_smoke_method_id(
                override_axis_graph,
                fqn="orolla.abstractroute.AbstractApi",
                method_name="handle",
            ),
        ),
    ]
    for graph, mid in cases:
        stored = sorted(graph._override_impl_ids_from_stored(mid))  # noqa: SLF001
        signature = sorted(_dispatch_down_override_method_ids(graph, mid))
        assert stored == signature


def test_neighbors_accepts_overridden_by_dot_keys() -> None:
    for key in _OVERRIDE_AXIS_COMPOSED_KEYS:
        _NEIGHBOR_EDGE_TYPES_ADAPTER.validate_python([key])


def test_neighbors_overridden_by_dot_key_returns_overriders(ladybug_graph: LadybugGraph) -> None:
    mid = _request_assignment_method_id(ladybug_graph)
    want = sorted(_dispatch_down_override_method_ids(ladybug_graph, mid))
    assert want
    out_virtual = neighbors_v2(mid, direction="out", edge_types=["OVERRIDDEN_BY"], graph=ladybug_graph)
    out_stored = neighbors_v2(mid, direction="in", edge_types=["OVERRIDES"], graph=ladybug_graph)
    assert out_virtual.success is True
    assert out_stored.success is True
    got_virtual = sorted({e.other.id for e in out_virtual.results})
    got_stored = sorted({e.other.id for e in out_stored.results})
    assert got_virtual == want
    assert got_stored == want
    assert all(e.edge_type == "OVERRIDDEN_BY" for e in out_virtual.results)
    assert all(e.other.kind == "symbol" for e in out_virtual.results)
    assert all("via_id" not in e.attrs for e in out_virtual.results)


def test_neighbors_overridden_by_dot_key_declares_client(ladybug_graph: LadybugGraph) -> None:
    mid = _request_assignment_method_id(ladybug_graph)
    out = neighbors_v2(
        mid, direction="out", edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"], graph=ladybug_graph, limit=500
    )
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "OVERRIDDEN_BY.DECLARES_CLIENT" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "client" for e in out.results)


def test_neighbors_overridden_by_dot_key_declares_producer(override_axis_graph: LadybugGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'publish' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractproducer.AbstractProducerApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["OVERRIDDEN_BY.DECLARES_PRODUCER"],
        graph=override_axis_graph,
        limit=500,
    )
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "OVERRIDDEN_BY.DECLARES_PRODUCER" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "producer" for e in out.results)


def test_neighbors_overridden_by_dot_key_exposes(override_axis_graph: LadybugGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.AbstractApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["OVERRIDDEN_BY.EXPOSES"],
        graph=override_axis_graph,
        limit=500,
    )
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "OVERRIDDEN_BY.EXPOSES" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "route" for e in out.results)


def test_neighbors_overridden_by_dot_key_count_matches_edge_summary(ladybug_graph: LadybugGraph) -> None:
    mid = _request_assignment_method_id(ladybug_graph)
    d = describe_v2(mid, graph=ladybug_graph)
    n = neighbors_v2(
        mid,
        direction="out",
        edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"],
        graph=ladybug_graph,
        limit=500,
    )
    assert d.success and d.record and d.record.edge_summary
    summary = d.record.edge_summary.get("OVERRIDDEN_BY.DECLARES_CLIENT")
    assert summary is not None
    assert n.success is True
    assert len(n.results) == summary["out"]


def test_neighbors_overridden_by_dot_key_type_origin_rejected(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    out = neighbors_v2(
        tid, direction="out", edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"], graph=ladybug_graph
    )
    assert out.success is False
    assert out.message is not None
    assert "method Symbol origin" in out.message


def test_neighbors_mixed_composed_families_on_type_rejected(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    out = neighbors_v2(
        tid,
        direction="out",
        edge_types=["DECLARES.DECLARES_CLIENT", "OVERRIDDEN_BY.DECLARES_CLIENT"],
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message is not None
    assert "method Symbol origin" in out.message


def test_neighbors_mixed_composed_families_on_method_rejected(ladybug_graph: LadybugGraph) -> None:
    mid = _request_assignment_method_id(ladybug_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["DECLARES.DECLARES_CLIENT", "OVERRIDDEN_BY.DECLARES_CLIENT"],
        graph=ladybug_graph,
    )
    assert out.success is False
    assert out.message is not None
    assert "type Symbol origin" in out.message


def test_neighbors_overridden_by_dot_key_static_method_rejected(ladybug_graph: LadybugGraph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol) "
        "WHERE m.kind = 'method' AND list_contains(COALESCE(m.modifiers, []), 'static') "
        "RETURN m.id AS id LIMIT 1",
    )
    assert rows
    mid = str(rows[0]["id"])
    out = neighbors_v2(
        mid, direction="out", edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"], graph=ladybug_graph
    )
    assert out.success is False
    assert out.message is not None
    assert "non-static" in out.message


def test_neighbors_overridden_by_dot_key_constructor_rejected(ladybug_graph: LadybugGraph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(c:Symbol) "
        "WHERE c.kind = 'constructor' "
        "RETURN c.id AS id LIMIT 1",
    )
    assert rows
    cid = str(rows[0]["id"])
    out = neighbors_v2(
        cid, direction="out", edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"], graph=ladybug_graph
    )
    assert out.success is False
    assert out.message is not None
    assert "constructor" in out.message


def test_neighbors_overridden_by_dot_key_inbound_rejected(ladybug_graph: LadybugGraph) -> None:
    mid = _request_assignment_method_id(ladybug_graph)
    out = neighbors_v2(
        mid, direction="in", edge_types=["OVERRIDDEN_BY.DECLARES_CLIENT"], graph=ladybug_graph
    )
    assert out.success is False
    assert out.message is not None
    assert 'direction="out"' in out.message


def _override_axis_smoke_method_id(graph: LadybugGraph, *, fqn: str, method_name: str) -> str:
    rows = graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = $name "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": fqn, "name": method_name},
    )
    assert rows
    return str(rows[0]["id"])


def _override_parity_graph_method_pairs(
    composed_key: str,
    ladybug_graph: LadybugGraph,
    override_axis_graph: LadybugGraph,
) -> list[tuple[LadybugGraph, str]]:
    # OVERRIDDEN_BY.DECLARES_CLIENT: bank-chat only — smoke corpus has no DECLARES_CLIENT on overriders.
    pairs: list[tuple[LadybugGraph, str]] = [(ladybug_graph, _request_assignment_method_id(ladybug_graph))]
    if composed_key in ("OVERRIDDEN_BY", "OVERRIDDEN_BY.EXPOSES"):
        pairs.append(
            (
                override_axis_graph,
                _override_axis_smoke_method_id(
                    override_axis_graph,
                    fqn="orolla.abstractroute.AbstractApi",
                    method_name="handle",
                ),
            )
        )
    if composed_key == "OVERRIDDEN_BY.DECLARES_PRODUCER":
        pairs.append(
            (
                override_axis_graph,
                _override_axis_smoke_method_id(
                    override_axis_graph,
                    fqn="orolla.abstractproducer.AbstractProducerApi",
                    method_name="publish",
                ),
            )
        )
    return pairs


@pytest.mark.parametrize("composed_key", _OVERRIDE_AXIS_COMPOSED_KEYS)
def test_neighbors_overridden_by_rollup_traversal_parity_blocking(
    ladybug_graph: LadybugGraph,
    override_axis_graph: LadybugGraph,
    composed_key: str,
) -> None:
    checked = False
    for graph, mid in _override_parity_graph_method_pairs(
        composed_key, ladybug_graph, override_axis_graph
    ):
        d = describe_v2(mid, graph=graph)
        n = neighbors_v2(
            mid, direction="out", edge_types=[composed_key], graph=graph, limit=5000
        )
        assert d.success and d.record and d.record.edge_summary
        summary = d.record.edge_summary.get(composed_key)
        if summary is None or int(summary.get("out", 0) or 0) == 0:
            continue
        checked = True
        assert n.success is True
        assert len(n.results) == summary["out"]
    assert checked, f"no fixture method with non-zero {composed_key} rollup"


def _type_id_with_composed_key(ladybug_graph: LadybugGraph, rel: str, composed_key: str) -> tuple[str, int]:
    rows = ladybug_graph._rows(  # noqa: SLF001
        f"MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:{rel}]->() "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
        {"kinds": _ROLLUP_TYPE_KINDS},
    )
    assert rows, f"no type with {composed_key} in fixture"
    return str(rows[0]["id"]), int(rows[0]["n"] or 0)


def test_neighbors_declares_dot_key_client(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    out = neighbors_v2(tid, direction="out", edge_types=["DECLARES.DECLARES_CLIENT"], graph=ladybug_graph, limit=500)
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "DECLARES.DECLARES_CLIENT" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "client" for e in out.results)


def test_neighbors_declares_dot_key_producer(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(
        ladybug_graph, "DECLARES_PRODUCER", "DECLARES.DECLARES_PRODUCER"
    )
    out = neighbors_v2(
        tid, direction="out", edge_types=["DECLARES.DECLARES_PRODUCER"], graph=ladybug_graph, limit=500
    )
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "DECLARES.DECLARES_PRODUCER" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "producer" for e in out.results)


def test_neighbors_declares_dot_key_exposes(ladybug_graph: LadybugGraph) -> None:
    rows = ladybug_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[e:EXPOSES]->(:Route) "
        "WHERE t.role = 'CONTROLLER' AND t.kind = 'class' "
        "RETURN t.id AS id, count(e) AS n ORDER BY n DESC LIMIT 1",
    )
    assert rows
    tid = str(rows[0]["id"])
    out = neighbors_v2(tid, direction="out", edge_types=["DECLARES.EXPOSES"], graph=ladybug_graph, limit=500)
    assert out.success is True
    assert len(out.results) >= 1
    assert all(e.edge_type == "DECLARES.EXPOSES" for e in out.results)
    assert all(e.attrs.get("via_id") for e in out.results)
    assert all(e.other.kind == "route" for e in out.results)


def test_neighbors_dot_key_mixed_with_flat(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    out = neighbors_v2(
        tid,
        direction="out",
        edge_types=["DECLARES", "DECLARES.DECLARES_CLIENT"],
        graph=ladybug_graph,
        limit=500,
    )
    assert out.success is True
    edge_types_seen = {e.edge_type for e in out.results}
    assert "DECLARES" in edge_types_seen
    assert "DECLARES.DECLARES_CLIENT" in edge_types_seen
    assert any(e.other.kind == "symbol" for e in out.results)
    assert any(e.other.kind == "client" for e in out.results)


def test_neighbors_dot_key_inbound_rejected(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    out = neighbors_v2(tid, direction="in", edge_types=["DECLARES.DECLARES_CLIENT"], graph=ladybug_graph)
    assert out.success is False
    assert out.message is not None
    assert 'direction="out"' in out.message


def test_neighbors_dot_key_method_origin_rejected(ladybug_graph: LadybugGraph) -> None:
    node_id, _ = _controller_method_with_calls(ladybug_graph)
    out = neighbors_v2(
        node_id, direction="out", edge_types=["DECLARES.DECLARES_CLIENT"], graph=ladybug_graph
    )
    assert out.success is False
    assert out.message is not None
    assert "type Symbol origin" in out.message


def test_neighbors_dot_key_count_matches_edge_summary(ladybug_graph: LadybugGraph) -> None:
    tid, _ = _type_id_with_composed_key(ladybug_graph, "DECLARES_CLIENT", "DECLARES.DECLARES_CLIENT")
    d = describe_v2(tid, graph=ladybug_graph)
    n = neighbors_v2(
        tid, direction="out", edge_types=["DECLARES.DECLARES_CLIENT"], graph=ladybug_graph, limit=500
    )
    assert d.success and d.record and d.record.edge_summary
    summary = d.record.edge_summary.get("DECLARES.DECLARES_CLIENT")
    assert summary is not None
    assert n.success is True
    assert len(n.results) == summary["out"]


def test_overrides_edge_set_deterministic_double_build(tmp_path: Path) -> None:
    def edge_pairs(db_path: Path) -> list[tuple[str, str]]:
        g = LadybugGraph(str(db_path))
        rows = g._rows(  # noqa: SLF001
            "MATCH (a:Symbol)-[e:OVERRIDES]->(b:Symbol) "
            "RETURN a.id AS src, b.id AS dst ORDER BY src, dst",
        )
        return [(str(r["src"]), str(r["dst"])) for r in rows]

    p1 = tmp_path / "g1.lbug"
    p2 = tmp_path / "g2.lbug"
    build_ladybug_to(_OVERRIDE_AXIS_FIXTURE, p1, max_pass=5)
    build_ladybug_to(_OVERRIDE_AXIS_FIXTURE, p2, max_pass=5)
    assert edge_pairs(p1) == edge_pairs(p2)


def test_describe_client_edge_summary_includes_http_calls_out(
    ladybug_db_path_cross_service_smoke: Path,
) -> None:
    from ladybug_queries import LadybugGraph

    g = LadybugGraph(str(ladybug_db_path_cross_service_smoke))
    rows = g._rows(  # noqa: SLF001
        "MATCH (c:Client)-[:HTTP_CALLS]->() RETURN c.id AS id LIMIT 1",
        {},
    )
    assert rows
    cid = str(rows[0]["id"])
    out = describe_v2(cid, graph=g)
    assert out.success is True
    assert out.record is not None
    assert out.record.edge_summary is not None
    http_out = out.record.edge_summary.get("HTTP_CALLS", {"in": 0, "out": 0})
    assert int(http_out.get("out", 0)) >= 1
