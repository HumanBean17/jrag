from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import mcp_hints
from _builders import build_kuzu_to
from java_ontology import EDGE_SCHEMA, FUZZY_STRATEGY_SET
from kuzu_queries import KuzuGraph
from mcp_hints import (
    _StructuredHint,
    finalize_structured_hints,
    generate_hints,
)
from mcp_v2 import (
    DescribeOutput,
    FindOutput,
    NeighborsOutput,
    ResolveOutput,
    SearchOutput,
    describe_v2,
    find_v2,
    neighbors_v2,
    resolve_v2,
    search_v2,
)
from pinned_ids import client_message_processor_process_id

_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})

_OVERRIDE_AXIS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "override_axis_rollup_smoke"


@pytest.fixture
def override_axis_graph(tmp_path: Path) -> KuzuGraph:
    db_path = tmp_path / "code_graph.kuzu"
    build_kuzu_to(_OVERRIDE_AXIS_FIXTURE, db_path, max_pass=5)
    return KuzuGraph(str(db_path))


def _type_symbol_id_with_member_clients(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[:DECLARES_CLIENT]->(:Client) "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id ORDER BY t.fqn LIMIT 1",
        {"kinds": sorted(_TYPE_KINDS)},
    )
    assert rows
    return str(rows[0]["id"])


def _controller_class_id_with_exposes(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[:EXPOSES]->(:Route) "
        "WHERE t.role = 'CONTROLLER' AND t.kind = 'class' "
        "RETURN t.id AS id LIMIT 1",
    )
    assert rows
    return str(rows[0]["id"])


def _type_symbol_id_with_member_producers(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[:DECLARES_PRODUCER]->(:Producer) "
        "WHERE t.kind IN $kinds "
        "RETURN t.id AS id ORDER BY t.fqn LIMIT 1",
        {"kinds": sorted(_TYPE_KINDS)},
    )
    if not rows:
        pytest.skip("no type with DECLARES_PRODUCER members in fixture")
    return str(rows[0]["id"])


def _interface_method_with_override_rollups(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ChatAssignmentPort"},
    )
    assert rows
    return str(rows[0]["id"])


def _method_id_declares_client_and_other_out_edge(kuzu_graph) -> str | None:
    for pattern in (
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:CALLS]->() RETURN m.id AS id LIMIT 1",
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->(:Client)-[:HTTP_CALLS]->() RETURN m.id AS id LIMIT 1",
    ):
        rows = kuzu_graph._rows(pattern)  # noqa: SLF001
        if rows:
            return str(rows[0]["id"])
    return None


def _method_declares_client(kuzu_graph) -> str:
    mid = _method_id_declares_client_and_other_out_edge(kuzu_graph)
    if mid is None:
        pytest.skip("no method with DECLARES_CLIENT + outbound edge in fixture")
    return mid


def _method_id_without_dispatch_rollups(kuzu_graph) -> str:
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


def _method_id_with_empty_describe_hints(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol) WHERE m.kind = 'method' RETURN m.id AS id LIMIT 100",
    )
    for row in rows:
        mid = str(row["id"])
        out = describe_v2(mid, graph=kuzu_graph)
        if out.success and out.record and out.hints_structured == []:
            return mid
    pytest.fail("no method with empty describe hints in fixture")


def _controller_method_many_calls(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[e:CALLS]->() WHERE m.kind = 'method' "
        "WITH m, count(e) AS nout WHERE nout >= 10 RETURN m.id AS id LIMIT 1",
    )
    assert rows
    return str(rows[0]["id"])


def _route_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (r:Route) RETURN r.id AS id ORDER BY r.id LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"])


def _client_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (c:Client) RETURN c.id AS id ORDER BY c.id LIMIT 1"
    )
    assert rows
    return str(rows[0]["id"])


def _class_symbol_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol) WHERE t.kind = 'class' RETURN t.id AS id LIMIT 1")
    assert rows
    return str(rows[0]["id"])


def test_hints_describe_type_symbol_clients_via_members_emits(kuzu_graph) -> None:
    tid = _type_symbol_id_with_member_clients(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in out.hints_structured)


def test_hints_describe_type_symbol_routes_via_members_emits(kuzu_graph) -> None:
    tid = _controller_class_id_with_exposes(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_ROUTES_VIA_MEMBERS for h in out.hints_structured)


def test_hints_describe_type_symbol_producers_via_members_emits(kuzu_graph) -> None:
    tid = _type_symbol_id_with_member_producers(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_PRODUCERS_VIA_MEMBERS for h in out.hints_structured)


def test_hints_describe_method_overriders_emits(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_OVERRIDERS for h in out.hints_structured)


def test_hints_describe_method_clients_in_overriders_emits(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_CLIENTS_IN_OVERRIDERS for h in out.hints_structured)


def test_hints_describe_method_overridden_by_declares_client_emits_dot_key(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    h = next(h for h in out.hints_structured if h.label == mcp_hints.LABEL_CLIENTS_IN_OVERRIDERS)
    assert "OVERRIDDEN_BY.DECLARES_CLIENT" in str(h.args.get("edge_types", []))


def test_hints_describe_method_producers_in_overriders_emits(override_axis_graph: KuzuGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'publish' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractproducer.AbstractProducerApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_PRODUCERS_IN_OVERRIDERS for h in out.hints_structured)


def test_hints_describe_method_routes_in_overriders_emits(override_axis_graph: KuzuGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.AbstractApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_ROUTES_IN_OVERRIDERS for h in out.hints_structured)


def test_hints_describe_method_declares_client_emits(kuzu_graph) -> None:
    mid = _method_declares_client(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_OUTBOUND_CLIENT for h in out.hints_structured)


def test_hints_describe_method_exposes_emits(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:EXPOSES]->(:Route) WHERE m.kind = 'method' RETURN m.id AS id LIMIT 1"
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_INBOUND_ROUTE for h in out.hints_structured)


def test_hints_describe_method_declares_producer_emits(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:DECLARES_PRODUCER]->(:Producer) WHERE m.kind = 'method' "
        "RETURN m.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no method with DECLARES_PRODUCER in fixture")
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_OUTBOUND_PRODUCER for h in out.hints_structured)


def test_hints_describe_method_many_calls_emits(kuzu_graph) -> None:
    mid = _controller_method_many_calls(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_HIGH_FANOUT and "many CALLS" in h.reason for h in out.hints_structured)


def test_hints_describe_route_always_declaring_method(kuzu_graph) -> None:
    rid = _route_id(kuzu_graph)
    out = describe_v2(rid, graph=kuzu_graph)
    assert out.success and out.record
    assert len(out.hints_structured) == 1
    assert out.hints_structured[0].label == mcp_hints.LABEL_DECLARING_METHOD


def test_hints_describe_client_always_declaring_method(kuzu_graph) -> None:
    cid = _client_id(kuzu_graph)
    out = describe_v2(cid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_DECLARING_METHOD for h in out.hints_structured)


def test_hints_describe_producer_always_declaring_method(kuzu_graph) -> None:
    pid = _producer_id(kuzu_graph)
    out = describe_v2(pid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_DECLARING_METHOD for h in out.hints_structured)


def test_hints_find_empty_identifier_filter_suggests_resolve(kuzu_graph) -> None:
    out = find_v2("client", {"target_service": "__no_such_target_service__"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert any(h.label == mcp_hints.LABEL_TRY_RESOLVE and h.args.get("hint_kind") == "client" for h in out.hints_structured)


def test_hints_find_empty_symbol_fqn_prefix_suggests_resolve(kuzu_graph) -> None:
    out = find_v2("symbol", {"fqn_prefix": "__no_such_prefix__"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert any(h.label == mcp_hints.LABEL_TRY_RESOLVE and h.args.get("hint_kind") == "symbol" for h in out.hints_structured)


def test_hints_find_page_full_emits_narrow_or_paginate(kuzu_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=1, offset=0)
    assert out.success is True
    assert len(out.results) >= 1
    assert any(h.label == mcp_hints.LABEL_PAGE_FULL for h in out.hints_structured)


def test_hints_find_page_full_skips_when_last_page(kuzu_graph) -> None:
    full = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=500, offset=0)
    assert full.success and full.results
    last = find_v2(
        "symbol",
        {"role": "CONTROLLER"},
        graph=kuzu_graph,
        limit=1,
        offset=len(full.results) - 1,
    )
    assert last.success and len(last.results) == 1
    assert not any(h.label == mcp_hints.LABEL_PAGE_FULL for h in last.hints_structured)


def _neighbors_hint_payload(
    results: list[dict[str, Any]],
    *,
    requested_edge_types: list[str] | None = None,
    subject_record: dict[str, Any] | None = None,
    requested_direction: str = "out",
    origin_id: str = "sym:com.example.T",
    offset: int = 0,
) -> dict[str, Any]:
    return {
        "success": True,
        "results": results,
        "requested_edge_types": requested_edge_types or ["DECLARES_CLIENT"],
        "requested_direction": requested_direction,
        "subject_record": subject_record
        if subject_record is not None
        else {"id": origin_id, "kind": "class"},
        "origin_id": origin_id,
        "offset": offset,
    }


def _type_subject_record(node_id: str, decl_kind: str = "class") -> dict[str, Any]:
    return {"id": node_id, "kind": decl_kind}


def _symbol_other(
    node_id: str,
    *,
    symbol_kind: str = "method",
) -> dict[str, Any]:
    return {"id": node_id, "kind": "symbol", "symbol_kind": symbol_kind}


def _terminal_other(node_id: str, kind: str) -> dict[str, Any]:
    return {"id": node_id, "kind": kind}


def _success_edge(
    other: dict[str, Any],
    *,
    edge_type: str = "DECLARES",
    direction: str = "out",
    origin_id: str = "sym:com.example.T",
) -> dict[str, Any]:
    return {
        "origin_id": origin_id,
        "edge_type": edge_type,
        "direction": direction,
        "other": other,
        "attrs": {},
    }


def _neighbors_empty_payload(
    subject_record: dict[str, Any],
    edge_types: list[str],
    *,
    direction: str = "out",
) -> dict[str, Any]:
    return _neighbors_hint_payload(
        [],
        requested_edge_types=edge_types,
        subject_record=subject_record,
        requested_direction=direction,
    )


def _has_structural_label(payload: dict[str, Any], label: str) -> bool:
    return any(h.label == label for h in generate_hints("neighbors", payload))


def test_hints_neighbors_empty_class_declares_client_emits_type_level_requery(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES_CLIENT"], graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert out.requested_edge_types == ["DECLARES_CLIENT"]
    assert any("lives on methods" in h.reason for h in out.hints_structured)


def _edge_result(*, strategy: str | None = None, edge_type: str = "DECLARES_CLIENT") -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if strategy is not None:
        attrs["strategy"] = strategy
    return {
        "origin_id": "sym:pkg.Type#m()",
        "edge_type": edge_type,
        "direction": "out",
        "other": {"id": "client:svc:feign:t:GET:/p", "kind": "client"},
        "attrs": attrs,
    }


def _method_id_with_fuzzy_calls(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[e:CALLS]->() "
        "WHERE e.strategy IN $strategies "
        "RETURN m.id AS id LIMIT 1",
        {"strategies": sorted(FUZZY_STRATEGY_SET)},
    )
    if not rows:
        pytest.fail("no CALLS edge with fuzzy strategy in bank fixture")
    return str(rows[0]["id"])


def test_hints_neighbors_fuzzy_strategy_layer_c_source_emits() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="layer_c_source", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert any(h.label == mcp_hints.LABEL_FUZZY_STRATEGY for h in hints)
    fuzzy = [h for h in hints if h.label == mcp_hints.LABEL_FUZZY_STRATEGY]
    assert "attrs.strategy" in fuzzy[0].reason


def test_hints_neighbors_fuzzy_strategy_annotation_absent() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="annotation", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_fuzzy_strategy_calls_phantom_emits() -> None:
    """CALLS fuzzy hint uses remaining strategies (overload_ambiguous), not removed phantom/chained."""
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="overload_ambiguous", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert any(h.label == mcp_hints.LABEL_FUZZY_STRATEGY for h in hints)


def test_hints_neighbors_declares_no_strategy_attrs_empty() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
    )
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_multi_origin_fuzzy_emits_once() -> None:
    payload = _neighbors_hint_payload(
        [
            _edge_result(strategy="overload_ambiguous", edge_type="CALLS"),
            _edge_result(strategy="annotation", edge_type="CALLS"),
        ],
        requested_edge_types=["CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert sum(1 for h in hints if h.label == mcp_hints.LABEL_FUZZY_STRATEGY) == 1


def test_hints_neighbors_calls_high_fanout(kuzu_graph) -> None:
    mid = client_message_processor_process_id(kuzu_graph)
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], limit=25, graph=kuzu_graph)
    assert out.success is True
    assert len(out.results) == 25
    total_calls = kuzu_graph.count_calls_for_symbol(mid, direction="out")
    assert total_calls >= 10
    assert any(
        h.label == mcp_hints.LABEL_HIGH_FANOUT and str(total_calls) in h.reason
        for h in out.hints_structured
    )
    assert not any(
        h.label == mcp_hints.LABEL_HIGH_FANOUT and str(len(out.results)) in h.reason
        for h in out.hints_structured
    )


def test_hints_neighbors_calls_high_fanout_suppressed_with_edge_filter(kuzu_graph) -> None:
    mid = client_message_processor_process_id(kuzu_graph)
    out = neighbors_v2(
        mid,
        direction="out",
        edge_types=["CALLS"],
        edge_filter={"callee_declaring_role": "SERVICE"},
        limit=500,
        graph=kuzu_graph,
    )
    assert out.success is True
    assert not any("CALLS on this method" in h.reason for h in out.hints_structured)


def test_hints_neighbors_layer_a_meta_no_fuzzy_hint() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="layer_a_meta", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_fuzzy_strategy_neighbors_v2_round_trip(kuzu_graph) -> None:
    mid = _method_id_with_fuzzy_calls(kuzu_graph)
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], graph=kuzu_graph, limit=50)
    assert out.success is True
    assert out.results
    strategies = [e.attrs.get("strategy") for e in out.results]
    assert any(s in FUZZY_STRATEGY_SET for s in strategies if isinstance(s, str))
    assert any(h.label == mcp_hints.LABEL_FUZZY_STRATEGY for h in out.hints_structured)
    assert any("brownfield/fallback strategy" in h.reason for h in out.hints_structured)


def _producer_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows("MATCH (p:Producer) RETURN p.id AS id ORDER BY p.id LIMIT 1")  # noqa: SLF001
    if not rows:
        pytest.fail("session fixture lacks Producer nodes (post-flip SCHEMA required)")
    return str(rows[0]["id"])


def _method_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol) WHERE m.kind = 'method' RETURN m.id AS id LIMIT 1",
    )
    assert rows
    return str(rows[0]["id"])


def _annotation_symbol_id(kuzu_graph) -> str | None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind = 'annotation' RETURN s.id AS id LIMIT 1",
    )
    if not rows:
        return None
    return str(rows[0]["id"])


def test_hints_hv1_type_level_declares_client_requery() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T", "kind": "class"},
        ["DECLARES_CLIENT"],
    )
    hints = generate_hints("neighbors", payload)
    assert any("lives on methods" in h.reason for h in hints)
    assert any("DECLARES" in str(h.args.get("edge_types", [])) for h in hints)


def test_hints_hv2_method_http_calls_wrong_subject_kind() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["HTTP_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert any("Client" in h.reason and "Route" in h.reason for h in hints)
    assert any("DECLARES_CLIENT" in str(h.args.get("edge_types", [])) for h in hints)


def test_hints_hv3_method_async_calls_wrong_subject_kind() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["ASYNC_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert any("Producer" in h.reason and "Route" in h.reason for h in hints)
    assert any("DECLARES_PRODUCER" in str(h.args.get("edge_types", [])) for h in hints)


def test_hints_hv4_producer_empty_async_out_no_structural_hints() -> None:
    payload = _neighbors_empty_payload(
        {"id": "producer:svc:kafka:t", "producer_kind": "kafka"},
        ["ASYNC_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)
    assert hints == []


def test_hints_hv5_producer_async_calls_wrong_direction() -> None:
    payload = _neighbors_empty_payload(
        {"id": "producer:svc:kafka:t", "producer_kind": "kafka"},
        ["ASYNC_CALLS"],
        direction="in",
    )
    hints = generate_hints("neighbors", payload)
    wrong_dir = [h for h in hints if h.label == mcp_hints.LABEL_WRONG_DIRECTION]
    assert wrong_dir
    assert "direction='out'" in wrong_dir[0].reason


def test_hints_hv6_client_http_calls_wrong_direction() -> None:
    payload = _neighbors_empty_payload(
        {"id": "client:svc:feign:t:GET:/p", "client_kind": "feign_method"},
        ["HTTP_CALLS"],
        direction="in",
    )
    hints = generate_hints("neighbors", payload)
    wrong_dir = [h for h in hints if h.label == mcp_hints.LABEL_WRONG_DIRECTION]
    assert wrong_dir
    assert "direction='out'" in wrong_dir[0].reason


def test_hints_hv7_route_http_calls_wrong_direction() -> None:
    payload = _neighbors_empty_payload(
        {"id": "route:svc:GET:/api", "framework": "spring_mvc"},
        ["HTTP_CALLS"],
        direction="out",
    )
    hints = generate_hints("neighbors", payload)
    wrong_dir = [h for h in hints if h.label == mcp_hints.LABEL_WRONG_DIRECTION]
    assert wrong_dir
    assert "direction='in'" in wrong_dir[0].reason


def test_hints_hv8_method_exposes_empty_no_structural_hint() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["EXPOSES"],
    )
    hints = generate_hints("neighbors", payload)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)
    assert not any("brownfield resolver" in h.reason for h in hints)


def test_hints_hv9_method_declares_client_empty_no_structural_hint() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["DECLARES_CLIENT"],
    )
    hints = generate_hints("neighbors", payload)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)
    assert not any("brownfield resolver" in h.reason for h in hints)


def test_hints_hv10_class_http_calls_wrong_subject_kind() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T", "kind": "class"},
        ["HTTP_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    # Type-level Symbol is alien_subject for HTTP_CALLS — no structural hint emitted
    assert hints == []


def test_hints_hv11_method_overrides_empty_no_structural_hint() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["OVERRIDES"],
    )
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)


def test_hints_hv12_annotation_extends_empty_no_structural_hint(kuzu_graph) -> None:
    ann_id = _annotation_symbol_id(kuzu_graph)
    if ann_id is None:
        pytest.skip("no annotation Symbol in fixture")
    assert EDGE_SCHEMA["EXTENDS"].member_only is False
    payload = _neighbors_empty_payload({"id": ann_id, "kind": "annotation"}, ["EXTENDS"])
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)


def test_hints_hv13_client_empty_http_no_structural_hints() -> None:
    payload = _neighbors_empty_payload(
        {"id": "client:svc:feign:t:GET:/p", "client_kind": "feign_method"},
        ["HTTP_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)
    assert hints == []


def test_hints_hv14_producer_empty_async_no_structural_hints() -> None:
    payload = _neighbors_empty_payload(
        {"id": "producer:svc:kafka:t", "producer_kind": "kafka"},
        ["ASYNC_CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_SUBJECT_KIND)
    assert not _has_structural_label(payload, mcp_hints.LABEL_WRONG_DIRECTION)
    assert not _has_structural_label(payload, mcp_hints.LABEL_TYPE_LEVEL_REQUERY)
    assert hints == []


def test_hints_hv15_multi_edge_http_only_wrong_kind_for_http() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["HTTP_CALLS", "DECLARES_CLIENT"],
    )
    hints = generate_hints("neighbors", payload)
    wrong_kind = [h for h in hints if "HTTP_CALLS" in h.reason and "this is a Symbol" in h.reason]
    assert len(wrong_kind) == 1
    assert not any("DECLARES_CLIENT" in h.reason and "lives on methods" in h.reason for h in hints)


def test_hints_hv16_client_nonempty_http_fuzzy_hint_unchanged(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (c:Client)-[e:HTTP_CALLS]->() "
        "WHERE e.strategy IN $strategies "
        "RETURN c.id AS id LIMIT 1",
        {"strategies": sorted(FUZZY_STRATEGY_SET)},
    )
    if not rows:
        pytest.skip("no Client HTTP_CALLS edge with fuzzy strategy in fixture")
    cid = str(rows[0]["id"])
    out = neighbors_v2(cid, direction="out", edge_types=["HTTP_CALLS"], graph=kuzu_graph, limit=50)
    assert out.success and out.results
    assert any(h.label == mcp_hints.LABEL_FUZZY_STRATEGY for h in out.hints_structured)
    assert not any("this is a" in h.reason for h in out.hints_structured)


def test_hints_hv17_class_exposes_type_level_requery() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T", "kind": "class"},
        ["EXPOSES"],
    )
    hints = generate_hints("neighbors", payload)
    assert any("lives on methods" in h.reason for h in hints)


def test_hints_hv18_route_declares_wrong_subject_kind() -> None:
    payload = _neighbors_empty_payload(
        {"id": "route:svc:GET:/api", "framework": "spring_mvc"},
        ["DECLARES"],
    )
    hints = generate_hints("neighbors", payload)
    # Route is alien_subject for DECLARES — no structural hint emitted
    assert hints == []


def _synthetic_coverage_for_edge(edge: str) -> tuple[dict[str, Any], str] | None:
    candidates: list[tuple[dict[str, Any], str]] = [
        ({"id": "sym:type", "kind": "class"}, "out"),
        ({"id": "sym:type", "kind": "class"}, "in"),
        ({"id": "sym:method", "kind": "method"}, "out"),
        ({"id": "sym:method", "kind": "method"}, "in"),
        ({"id": "sym:ann", "kind": "annotation"}, "out"),
        ({"id": "client:x", "client_kind": "feign_method"}, "out"),
        ({"id": "client:x", "client_kind": "feign_method"}, "in"),
        ({"id": "route:x", "framework": "spring_mvc"}, "out"),
        ({"id": "route:x", "framework": "spring_mvc"}, "in"),
        ({"id": "producer:x", "producer_kind": "kafka"}, "out"),
        ({"id": "producer:x", "producer_kind": "kafka"}, "in"),
    ]
    for rec, direction in candidates:
        payload = _neighbors_empty_payload(rec, [edge], direction=direction)
        hints = generate_hints("neighbors", payload)
        if hints:
            return rec, direction
    return None


# Edges that connect Symbol->Symbol produce no empty-neighbor structural hints
# (valid subject, no wrong_kind/wrong_direction/type_level_requery applies).
_SYMBOL_TO_SYMBOL_EDGES = frozenset({"DECLARES", "EXTENDS", "IMPLEMENTS", "INJECTS"})


@pytest.mark.parametrize("edge", sorted(EDGE_SCHEMA.keys() - _SYMBOL_TO_SYMBOL_EDGES))
def test_hints_hv19_edge_schema_coverage_exists_trigger_per_edge(edge: str) -> None:
    found = _synthetic_coverage_for_edge(edge)
    assert found is not None, f"no synthetic subject/direction triggers hints for {edge}"


def test_hints_neighbors_missing_subject_record_skips_structural() -> None:
    payload = {
        "success": True,
        "results": [],
        "requested_edge_types": ["HTTP_CALLS"],
        "requested_direction": "out",
        "offset": 0,
        "subject_record": None,
    }
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_offset_suppresses_empty_structural_hints() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["HTTP_CALLS"],
    )
    payload["offset"] = 3
    assert generate_hints("neighbors", payload) == []


def test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints() -> None:
    """Empty structural neighbors hints only -- success-path N1a/N1b may use DECLARES.* dot-keys."""
    payloads = [
        _neighbors_empty_payload({"id": "sym:com.example.T", "kind": "class"}, ["DECLARES_CLIENT"]),
        _neighbors_empty_payload({"id": "sym:com.example.T#m()", "kind": "method"}, ["HTTP_CALLS"]),
        _neighbors_empty_payload(
            {"id": "client:svc:feign:t:GET:/p", "client_kind": "feign_method"},
            ["HTTP_CALLS"],
        ),
    ]
    for payload in payloads:
        for hint in generate_hints("neighbors", payload):
            assert "DECLARES." not in hint.reason
            assert "OVERRIDDEN_BY." not in hint.reason


def test_hints_neighbors_success_may_emit_declares_dot_keys() -> None:
    origin = "sym:com.example.T"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:com.example.T#m()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
        subject_record=_type_subject_record(origin),
        origin_id=origin,
    )
    hints = generate_hints("neighbors", payload)
    assert any("DECLARES.DECLARES_CLIENT" in str(h.args.get("edge_types", [])) for h in hints)
    assert any("DECLARES.EXPOSES" in str(h.args.get("edge_types", [])) for h in hints)


def test_hints_neighbors_declares_methods_emits_dot_key_clients() -> None:
    origin = "sym:com.example.T"
    payload = _neighbors_hint_payload(
        [
            _success_edge(_symbol_other("sym:com.example.T#m()"), edge_type="DECLARES"),
            _success_edge(
                _symbol_other("sym:com.example.T#c()", symbol_kind="constructor"),
                edge_type="DECLARES",
            ),
        ],
        requested_edge_types=["DECLARES"],
        subject_record=_type_subject_record(origin),
        origin_id=origin,
    )
    assert any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_methods_emits_dot_key_routes() -> None:
    origin = "sym:com.example.T"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:com.example.T#m()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
        subject_record=_type_subject_record(origin),
        origin_id=origin,
    )
    assert any(h.label == mcp_hints.LABEL_ROUTES_VIA_MEMBERS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_client_homogeneous_emits_http_calls() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="DECLARES_CLIENT")],
        requested_edge_types=["DECLARES_CLIENT"],
    )
    assert any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_dot_key_client_homogeneous_emits_http_calls() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="DECLARES.DECLARES_CLIENT")],
        requested_edge_types=["DECLARES.DECLARES_CLIENT"],
    )
    assert any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_producer_homogeneous_emits_async_calls() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("producer:a", "producer"), edge_type="DECLARES_PRODUCER")],
        requested_edge_types=["DECLARES_PRODUCER"],
    )
    assert any(h.label == mcp_hints.LABEL_ASYNC_TARGETS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_dot_key_producer_homogeneous_emits_async_calls() -> None:
    payload = _neighbors_hint_payload(
        [
            _success_edge(
                _terminal_other("producer:a", "producer"),
                edge_type="DECLARES.DECLARES_PRODUCER",
            ),
        ],
        requested_edge_types=["DECLARES.DECLARES_PRODUCER"],
    )
    assert any(h.label == mcp_hints.LABEL_ASYNC_TARGETS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_declares_dot_key_exposes_homogeneous_emits_handler() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("route:a", "route"), edge_type="DECLARES.EXPOSES")],
        requested_edge_types=["DECLARES.EXPOSES"],
    )
    assert any(h.label == mcp_hints.LABEL_HANDLER for h in generate_hints("neighbors", payload))


def test_hints_neighbors_exposes_in_methods_emits_calls() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:pkg.Handler#run()"), edge_type="EXPOSES", direction="in")],
        requested_edge_types=["EXPOSES"],
        requested_direction="in",
        subject_record={"id": "route:svc:GET:/p", "framework": "spring"},
    )
    assert any(h.label == mcp_hints.LABEL_CALLERS for h in generate_hints("neighbors", payload))


def test_hints_neighbors_http_calls_in_clients_emits_declares_client() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="HTTP_CALLS", direction="in")],
        requested_edge_types=["HTTP_CALLS"],
        requested_direction="in",
        subject_record={"id": "route:svc:GET:/p", "framework": "spring"},
    )
    assert any(h.label == mcp_hints.LABEL_DECLARING_METHOD for h in generate_hints("neighbors", payload))


def test_hints_neighbors_async_calls_in_producers_emits_declares_producer() -> None:
    payload = _neighbors_hint_payload(
        [
            _success_edge(
                _terminal_other("producer:a", "producer"),
                edge_type="ASYNC_CALLS",
                direction="in",
            ),
        ],
        requested_edge_types=["ASYNC_CALLS"],
        requested_direction="in",
        subject_record={"id": "route:svc:GET:/p", "framework": "spring"},
    )
    assert any(h.label == mcp_hints.LABEL_DECLARING_METHOD for h in generate_hints("neighbors", payload))


def test_hints_neighbors_multi_edge_types_suppresses_success_hints() -> None:
    origin = "sym:com.example.T"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:com.example.T#m()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES", "DECLARES_CLIENT"],
        subject_record=_type_subject_record(origin),
        origin_id=origin,
    )
    hints = generate_hints("neighbors", payload)
    assert not any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in hints)
    assert not any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in hints)


def test_hints_neighbors_declares_from_method_origin_no_n1_rollups() -> None:
    origin = "sym:com.example.T#m()"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:com.example.T#other()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
        subject_record={"id": origin, "kind": "method"},
        origin_id=origin,
    )
    hints = generate_hints("neighbors", payload)
    assert not any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in hints)
    assert not any(h.label == mcp_hints.LABEL_ROUTES_VIA_MEMBERS for h in hints)


def test_hints_neighbors_n1a_n1b_dropped_when_rendered_exceeds_char_cap() -> None:
    long_origin = "sym:com." + ("x" * 100) + ".Type"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:pkg.T#m()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
        subject_record=_type_subject_record(long_origin),
        origin_id=long_origin,
    )
    hints = generate_hints("neighbors", payload)
    # With structured hints, dot-key hints are emitted regardless of id length
    # (the cap was on rendered string length, which no longer applies)
    assert any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in hints)


def test_hints_neighbors_mixed_endpoint_kinds_silent() -> None:
    payload = _neighbors_hint_payload(
        [
            _success_edge(_terminal_other("client:a", "client"), edge_type="DECLARES_CLIENT"),
            _success_edge(_terminal_other("route:a", "route"), edge_type="DECLARES_CLIENT"),
        ],
        requested_edge_types=["DECLARES_CLIENT"],
    )
    hints = generate_hints("neighbors", payload)
    assert not any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in hints)
    assert not any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in hints)


def test_hints_neighbors_offset_suppresses_success_hints() -> None:
    origin = "sym:com.example.T"
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:com.example.T#m()"), edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
        subject_record=_type_subject_record(origin),
        origin_id=origin,
        offset=3,
    )
    hints = generate_hints("neighbors", payload)
    assert not any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in hints)
    assert not any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in hints)


def test_hints_neighbors_success_beats_fuzzy_in_cap() -> None:
    payload = _neighbors_hint_payload(
        [
            _success_edge(
                _terminal_other("client:a", "client"),
                edge_type="DECLARES_CLIENT",
            ),
        ],
        requested_edge_types=["DECLARES_CLIENT"],
    )
    payload["results"][0]["attrs"] = {"strategy": "layer_c_source"}
    hints = generate_hints("neighbors", payload)
    http_targets = [h for h in hints if h.label == mcp_hints.LABEL_HTTP_TARGETS]
    fuzzy = [h for h in hints if h.label == mcp_hints.LABEL_FUZZY_STRATEGY]
    assert http_targets
    assert fuzzy
    assert hints.index(http_targets[0]) < hints.index(fuzzy[0])


def test_hints_neighbors_v2_declares_success_emits_dot_key_clients(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES"], graph=kuzu_graph, limit=50)
    assert out.success is True
    assert out.results
    assert any(h.label == mcp_hints.LABEL_CLIENTS_VIA_MEMBERS for h in out.hints_structured)


def test_hints_neighbors_empty_kind_check_template_removed() -> None:
    assert not hasattr(mcp_hints, "TPL_NEIGHBORS_EMPTY_KIND_CHECK")


def test_hints_neighbors_v2_empty_post_flip_method_http_calls(kuzu_graph) -> None:
    mid = _method_id(kuzu_graph)
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (:Client)-[:HTTP_CALLS]->(:Route) RETURN count(*) AS n",
    )
    n = int(rows[0]["n"])
    assert n > 0, "session fixture lacks post-flip Client->Route HTTP_CALLS edges"
    out = neighbors_v2(mid, direction="out", edge_types=["HTTP_CALLS"], graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert any("this is a Symbol" in h.reason and "HTTP_CALLS" in h.reason for h in out.hints_structured)


def test_hints_search_weak_structural_signal_emits(monkeypatch, kuzu_graph) -> None:
    rows = [
        {
            "filename": "X.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 1},
            "symbol_id": "sym:a",
            "primary_type_fqn": "x.A",
            "_rrf_score": 1.0,
            "text": "a",
        },
        {
            "filename": "Y.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 1},
            "symbol_id": "sym:b",
            "primary_type_fqn": "x.B",
            "_rrf_score": 0.95,
            "text": "b",
        },
    ]
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: rows)
    out = search_v2("q", limit=2, offset=0, graph=kuzu_graph)
    assert out.success is True
    assert len(out.results) == 2
    assert out.limit == 2
    assert any(h.label == mcp_hints.LABEL_WEAK_RESULTS for h in out.hints_structured)


def test_hints_search_dominant_top_no_weak_hint(monkeypatch, kuzu_graph) -> None:
    rows = [
        {
            "filename": "X.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 1},
            "symbol_id": "sym:a",
            "primary_type_fqn": "x.A",
            "_rrf_score": 1.0,
            "text": "a",
        },
        {
            "filename": "Y.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 1},
            "symbol_id": "sym:b",
            "primary_type_fqn": "x.B",
            "_rrf_score": 0.5,
            "text": "b",
        },
    ]
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: rows)
    out = search_v2("q", limit=2, offset=0, graph=kuzu_graph)
    assert out.success is True
    assert not any(h.label == mcp_hints.LABEL_WEAK_RESULTS for h in out.hints_structured)


def test_hints_search_limit_none_never_emits_weak_hint() -> None:
    payload = {
        "success": True,
        "limit": None,
        "offset": 0,
        "results": [
            {"chunk_id": "a", "symbol_id": "s", "fqn": "F", "score": 1.0, "snippet": ""},
            {"chunk_id": "b", "symbol_id": "s", "fqn": "F", "score": 0.99, "snippet": ""},
        ],
    }
    assert generate_hints("search", payload) == []


def _find_success_payload(
    kind: str,
    node_id: str,
    *,
    limit: int | None = None,
    has_more_results: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": True,
        "kind": kind,
        "results": [{"id": node_id, "kind": kind}],
        "filter": {},
        "offset": 0,
    }
    if limit is not None:
        payload["limit"] = limit
        payload["has_more_results"] = has_more_results
    return payload


def test_hints_find_route_success_emits_handler() -> None:
    rid = "route:svc:GET:/api/v1/chat"
    payload = _find_success_payload("route", rid)
    assert any(h.label == mcp_hints.LABEL_HANDLER for h in generate_hints("find", payload))


def test_hints_find_client_success_emits_http_calls() -> None:
    cid = "client:svc:feign:target:GET:/p"
    payload = _find_success_payload("client", cid)
    assert any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in generate_hints("find", payload))


def test_hints_find_producer_success_emits_async_calls() -> None:
    pid = "producer:svc:kafka:topic:t"
    payload = _find_success_payload("producer", pid)
    assert any(h.label == mcp_hints.LABEL_ASYNC_TARGETS for h in generate_hints("find", payload))


def test_hints_find_success_suppressed_when_page_full() -> None:
    rid = "route:svc:GET:/api/v1/chat"
    payload = _find_success_payload("route", rid, limit=1, has_more_results=True)
    hints = generate_hints("find", payload)
    assert any(h.label == mcp_hints.LABEL_PAGE_FULL for h in hints)
    assert not any(h.label == mcp_hints.LABEL_HANDLER for h in hints)


def test_hints_find_success_uses_first_result_id_when_multiple() -> None:
    first = "route:svc:GET:/first"
    second = "route:svc:GET:/second"
    payload = _find_success_payload("route", first)
    payload["results"] = [
        {"id": first, "kind": "route"},
        {"id": second, "kind": "route"},
    ]
    hints = generate_hints("find", payload)
    assert any(h.label == mcp_hints.LABEL_HANDLER and first in str(h.args.get("ids", [])) for h in hints)
    assert not any(h.label == mcp_hints.LABEL_HANDLER and second in str(h.args.get("ids", [])) for h in hints)


def test_hints_find_symbol_success_emits_no_v4_followup() -> None:
    sym_id = "sym:com.example.T"
    payload = _find_success_payload("symbol", sym_id)
    hints = generate_hints("find", payload)
    v4_labels = (mcp_hints.LABEL_HANDLER, mcp_hints.LABEL_HTTP_TARGETS, mcp_hints.LABEL_ASYNC_TARGETS)
    assert not any(h.label in v4_labels for h in hints)


def test_hints_find_success_silent_when_first_result_missing_id() -> None:
    payload = _find_success_payload("route", "route:unused")
    payload["results"] = [{"kind": "route"}]
    hints = generate_hints("find", payload)
    assert not any(h.label == mcp_hints.LABEL_HANDLER for h in hints)


def test_hints_find_v2_route_success_emits_handler(kuzu_graph) -> None:
    out = find_v2("route", {"path_prefix": "/api"}, graph=kuzu_graph, limit=500, offset=0)
    assert out.success is True
    assert out.results
    assert any(h.label == mcp_hints.LABEL_HANDLER for h in out.hints_structured)
    assert not any(h.label == mcp_hints.LABEL_PAGE_FULL for h in out.hints_structured)


def test_hints_find_page_full_requires_has_more_results_flag() -> None:
    full_page = {
        "success": True,
        "kind": "symbol",
        "results": [{"id": "sym:a"}],
        "limit": 1,
        "offset": 0,
        "filter": {},
    }
    assert not any(h.label == mcp_hints.LABEL_PAGE_FULL for h in generate_hints("find", full_page))
    assert any(h.label == mcp_hints.LABEL_PAGE_FULL for h in generate_hints(
        "find", {**full_page, "has_more_results": True}
    ))


def test_hints_kind_gate_method_payload_ignores_type_only_rollups() -> None:
    node_id = "sym:com.example.T#m()"
    rec = {
        "id": node_id,
        "kind": "symbol",
        "fqn": "com.example.T#m()",
        "data": {"kind": "method"},
        "edge_summary": {
            "DECLARES.DECLARES_CLIENT": {"in": 0, "out": 3},
            "DECLARES.EXPOSES": {"in": 0, "out": 2},
        },
    }
    hints = generate_hints("describe", {"success": True, "record": rec})
    for h in hints:
        assert "via members" not in h.label


def test_hints_clean_outputs_empty(kuzu_graph) -> None:
    mid = _method_id_with_empty_describe_hints(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints_structured == []

    count_rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.role = 'CONTROLLER' RETURN count(*) AS n",
    )
    n_controllers = int(count_rows[0]["n"])
    assert n_controllers > 0
    assert n_controllers <= 500, "fixture has >500 CONTROLLER symbols; narrow filter for clean find hints"
    fout = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=500, offset=0)
    assert fout.success and len(fout.results) == n_controllers
    assert fout.hints_structured == []


def _resolve_symbol_id_status_one(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind = 'class' RETURN s.id AS id LIMIT 1",
    )
    assert rows
    sym_id = str(rows[0]["id"])
    out = resolve_v2(sym_id, hint_kind="symbol", graph=kuzu_graph)
    if not (out.success and out.status == "one"):
        pytest.fail(f"expected status one for symbol id {sym_id!r}, got {out.status!r}")
    return sym_id


def _resolve_symbol_short_name_status_many(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.kind = 'method' RETURN s.name AS name",
    )
    counts = Counter(str(r["name"]) for r in rows if r.get("name"))
    dup_name = next((name for name, c in counts.items() if c >= 2), None)
    if dup_name is None:
        pytest.fail("no duplicated method short names in bank-chat fixture")
    out = resolve_v2(dup_name, hint_kind="symbol", graph=kuzu_graph)
    if not (out.success and out.status == "many" and len(out.candidates) >= 2):
        pytest.fail(f"expected status many for short name {dup_name!r}, got {out.status!r}")
    return dup_name


def _resolve_symbol_identifier_status_none(kuzu_graph) -> str:
    ident = "com.nonexistent.ZzzMissing"
    out = resolve_v2(ident, hint_kind="symbol", graph=kuzu_graph)
    if not (out.success and out.status == "none"):
        pytest.fail(f"expected status none for {ident!r}, got {out.status!r}")
    return ident


def test_hints_resolve_status_one_emits_empty() -> None:
    assert generate_hints("resolve", {"status": "one", "resolved_identifier": "com.foo.Bar"}) == []


def test_hints_resolve_status_none_symbol_suggests_search() -> None:
    ident = "com.foo.Bar#nonExistent"
    hints = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": ident, "hint_kind": "symbol"},
    )
    assert hints
    assert hints[0].tool == "search"
    assert ident in str(hints[0].args.get("query", ""))


def test_hints_resolve_status_none_symbol_wildcard_suppressed() -> None:
    hints = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": "com.foo.*", "hint_kind": "symbol"},
    )
    assert hints == []


def test_hints_resolve_status_none_route_suggests_find() -> None:
    seed = "/v1/operator/session/update"
    hints = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": f"POST {seed}",
            "hint_kind": "route",
            "path_prefix_seed": seed,
        },
    )
    assert hints
    assert hints[0].tool == "find"
    assert hints[0].args.get("kind") == "route"
    assert seed in str(hints[0].args.get("filter", {}).get("path_prefix", ""))


def test_hints_resolve_status_none_route_no_seed_suppressed() -> None:
    hints = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": "not-a-route-shape",
            "hint_kind": "route",
            "path_prefix_seed": None,
        },
    )
    assert hints == []


def test_hints_resolve_status_none_client_suggests_find() -> None:
    seed = "smartcare-assign-chat"
    hints = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": seed,
            "hint_kind": "client",
            "target_service_seed": seed,
        },
    )
    assert hints
    assert hints[0].tool == "find"
    assert hints[0].args.get("kind") == "client"
    assert seed in str(hints[0].args.get("filter", {}).get("target_service", ""))


def test_hints_resolve_status_none_client_no_seed_suppressed() -> None:
    hints = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": "/only/a/path",
            "hint_kind": "client",
            "target_service_seed": None,
        },
    )
    assert hints == []


def test_hints_resolve_status_many_emits_tighten() -> None:
    hints = generate_hints(
        "resolve",
        {
            "status": "many",
            "resolved_identifier": "open",
            "candidates": [{"id": "a"}, {"id": "b"}],
        },
    )
    assert hints
    assert "2 candidates" in hints[0].reason
    assert "tighten identifier" in hints[0].reason


def test_hints_resolve_status_many_truncated_cap_wording() -> None:
    hints = generate_hints(
        "resolve",
        {
            "status": "many",
            "resolved_identifier": "open",
            "candidates": [{"id": f"c{i}"} for i in range(10)],
        },
    )
    assert hints
    assert "10 candidates" in hints[0].reason


def test_hints_resolve_success_false_suppresses() -> None:
    hints = generate_hints(
        "resolve",
        {
            "success": False,
            "status": "none",
            "resolved_identifier": "com.foo.Bar",
            "hint_kind": "symbol",
        },
    )
    assert hints == []


def test_hints_resolve_payload_missing_identifier_suppressed() -> None:
    hints = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": "", "hint_kind": "symbol"},
    )
    assert hints == []


def test_hints_resolve_v2_round_trip(kuzu_graph) -> None:
    none_ident = _resolve_symbol_identifier_status_none(kuzu_graph)
    none_out = resolve_v2(none_ident, hint_kind="symbol", graph=kuzu_graph)
    assert none_out.resolved_identifier == none_ident
    assert none_out.hints_structured
    assert none_out.hints_structured[0].tool == "search"

    one_id = _resolve_symbol_id_status_one(kuzu_graph)
    one_out = resolve_v2(one_id, hint_kind="symbol", graph=kuzu_graph)
    assert one_out.resolved_identifier == one_id
    assert one_out.hints_structured == []

    wildcard_out = resolve_v2("com.foo.*Service", hint_kind="symbol", graph=kuzu_graph)
    assert wildcard_out.success is True
    assert wildcard_out.status == "none"
    assert wildcard_out.resolved_identifier == "com.foo.*Service"
    assert wildcard_out.hints_structured == []

    many_ident = _resolve_symbol_short_name_status_many(kuzu_graph)
    many_out = resolve_v2(many_ident, hint_kind="symbol", graph=kuzu_graph)
    assert many_out.resolved_identifier == many_ident
    assert many_out.hints_structured
    assert "candidates" in many_out.hints_structured[0].reason
    assert "tighten identifier" in many_out.hints_structured[0].reason

    route_ident = "POST /v1/__no_such_resolve_route__"
    route_out = resolve_v2(route_ident, hint_kind="route", graph=kuzu_graph)
    assert route_out.success is True
    assert route_out.status == "none"
    assert route_out.resolved_identifier == route_ident
    assert route_out.hints_structured
    assert route_out.hints_structured[0].tool == "find"
    assert route_out.hints_structured[0].args.get("kind") == "route"

    client_ident = "__no_such_resolve_client_target__"
    client_out = resolve_v2(client_ident, hint_kind="client", graph=kuzu_graph)
    assert client_out.success is True
    assert client_out.status == "none"
    assert client_out.resolved_identifier == client_ident
    assert client_out.hints_structured
    assert client_out.hints_structured[0].tool == "find"
    assert client_out.hints_structured[0].args.get("kind") == "client"

    invalid_out = resolve_v2("", graph=kuzu_graph)
    assert invalid_out.success is False
    assert invalid_out.resolved_identifier is None
    assert invalid_out.hints_structured == []


def test_hints_error_path_success_false_empty(kuzu_graph) -> None:
    assert generate_hints("find", {"success": False, "kind": "symbol", "results": [], "filter": {}}) == []
    assert generate_hints("search", {"success": False, "results": []}) == []
    assert generate_hints("describe", {"success": False, "record": {}}) == []
    assert generate_hints("neighbors", {"success": False, "results": [], "requested_edge_types": ["CALLS"]}) == []
    serr = search_v2("q", filter={"bad_key": 1}, graph=kuzu_graph)
    assert serr.success is False and serr.hints_structured == [] and serr.limit is None and serr.offset is None
    ferr = find_v2("symbol", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert ferr.success is False and ferr.hints_structured == [] and ferr.limit is None and ferr.offset is None


def test_find_output_pagination_echo_round_trip(kuzu_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=12, offset=7)
    assert out.success is True
    assert out.limit == 12
    assert out.offset == 7
    raw = FindOutput(
        success=True,
        results=out.results,
        limit=12,
        offset=7,
    )
    assert raw.model_dump()["limit"] == 12 and raw.model_dump()["offset"] == 7


def test_search_output_pagination_echo_round_trip(monkeypatch, kuzu_graph) -> None:
    rows = [
        {
            "filename": "X.java",
            "start": {"byte_offset": 0},
            "end": {"byte_offset": 1},
            "_rrf_score": 0.5,
            "text": "x",
        },
    ]
    monkeypatch.setattr("mcp_v2.run_search", lambda *args, **kwargs: rows)
    out = search_v2("q", limit=9, offset=4, graph=kuzu_graph)
    assert out.success is True
    assert out.limit == 9
    assert out.offset == 4
    dumped = SearchOutput(
        success=True,
        results=out.results,
        limit=9,
        offset=4,
    ).model_dump()
    assert dumped["limit"] == 9 and dumped["offset"] == 4


def test_hints_pagination_none_skips_page_derived_hints() -> None:
    assert (
        generate_hints(
            "find",
            {
                "success": True,
                "kind": "symbol",
                "results": [{"id": "x"}],
                "limit": None,
                "offset": 0,
                "filter": {},
            },
        )
        == []
    )
    assert not any(
        h.label == mcp_hints.LABEL_PAGE_FULL
        for h in generate_hints(
            "find",
            {
                "success": True,
                "kind": "symbol",
                "results": [{"id": str(i)} for i in range(5)],
                "limit": None,
                "offset": 0,
                "filter": {},
            },
        )
    )


# ---------------------------------------------------------------------------
# Describe structural hints -- helpers + tests (PR-DESCRIBE-STRUCTURAL-1)
# ---------------------------------------------------------------------------


def _interface_with_implements_in(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol)<-[:IMPLEMENTS]-(impl:Symbol) "
        "WHERE iface.kind = 'interface' "
        "WITH iface, count(impl) AS nin WHERE nin > 0 "
        "RETURN iface.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no interface with IMPLEMENTS.in > 0 in fixture")
    return str(rows[0]["id"])


def _class_with_implements_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (cls:Symbol)-[:IMPLEMENTS]->(iface:Symbol) "
        "WHERE cls.kind = 'class' "
        "WITH cls, count(iface) AS nout WHERE nout > 0 "
        "RETURN cls.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no class with IMPLEMENTS.out > 0 in fixture")
    return str(rows[0]["id"])


def _service_with_injects_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (cls:Symbol)-[:INJECTS]->(dep:Symbol) "
        "WHERE cls.kind = 'class' AND cls.role = 'SERVICE' "
        "RETURN cls.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no SERVICE class with INJECTS.out > 0 in fixture")
    return str(rows[0]["id"])


def _type_with_injects_in(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (dep:Symbol)<-[:INJECTS]-(cls:Symbol) "
        "WHERE dep.kind IN ['interface', 'class'] "
        "RETURN DISTINCT dep.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no type with INJECTS.in > 0 in fixture")
    return str(rows[0]["id"])


def _method_with_mid_calls_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[c:CALLS]->() WHERE m.kind = 'method' "
        "WITH m, count(c) AS nout WHERE nout >= 3 AND nout <= 9 "
        "RETURN m.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no method with 3 <= CALLS.out <= 9 in fixture")
    return str(rows[0]["id"])


def _method_with_overrides_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:OVERRIDES]->() WHERE m.kind = 'method' "
        "RETURN m.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no method with OVERRIDES.out > 0 in fixture")
    return str(rows[0]["id"])


def _method_with_unresolved(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[c:CALLS]->() WHERE m.kind = 'method' "
        "WITH m, count(c) AS nout WHERE nout >= 1 "
        "RETURN m.id AS id, m.fqn AS fqn LIMIT 200",
    )
    for r in rows:
        mid = str(r["id"])
        out = describe_v2(mid, graph=kuzu_graph)
        if out.record and isinstance(out.record.data, dict):
            unc = int(out.record.data.get("unresolved_call_sites_total") or 0)
            if unc > 0:
                return mid
    pytest.skip("no method with unresolved_call_sites_total > 0 in fixture")


def _client_with_http_calls_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (c:Client)-[:HTTP_CALLS]->() RETURN DISTINCT c.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no client with HTTP_CALLS.out > 0 in fixture")
    return str(rows[0]["id"])


def _producer_with_async_calls_out(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (p:Producer)-[:ASYNC_CALLS]->() RETURN DISTINCT p.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no producer with ASYNC_CALLS.out > 0 in fixture")
    return str(rows[0]["id"])


# --- Describe structural hint tests ---


def test_hints_describe_interface_implementors_emits(kuzu_graph) -> None:
    tid = _interface_with_implements_in(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_IMPLEMENTORS for h in out.hints_structured)


def test_hints_describe_class_implements_emits(kuzu_graph) -> None:
    tid = _class_with_implements_out(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_IMPLEMENTS for h in out.hints_structured)


def test_hints_describe_service_dependencies_emits(kuzu_graph) -> None:
    tid = _service_with_injects_out(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_DEPENDENCIES for h in out.hints_structured)


def test_hints_describe_type_injectors_emits(kuzu_graph) -> None:
    tid = _type_with_injects_in(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_INJECTORS for h in out.hints_structured)


def test_hints_describe_type_skips_tier1_when_rollups(kuzu_graph) -> None:
    tid = _controller_class_id_with_exposes(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints_structured
    tier1_labels = (mcp_hints.LABEL_IMPLEMENTORS, mcp_hints.LABEL_IMPLEMENTS,
                    mcp_hints.LABEL_DEPENDENCIES, mcp_hints.LABEL_INJECTORS)
    for h in out.hints_structured:
        assert h.label not in tier1_labels


def test_hints_describe_method_outbound_calls_mid_fanout_emits(kuzu_graph) -> None:
    mid = _method_with_mid_calls_out(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_OUTBOUND_CALLS for h in out.hints_structured)


def test_hints_describe_method_outbound_calls_low_fanout_non_other_emits(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[c:CALLS]->() WHERE m.kind = 'method' AND m.role <> 'OTHER' "
        "WITH m, count(c) AS nout WHERE nout >= 1 AND nout <= 2 "
        "RETURN m.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no non-OTHER method with 1-2 CALLS.out in fixture")
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_OUTBOUND_CALLS for h in out.hints_structured)


def test_hints_describe_method_super_declaration_emits(kuzu_graph) -> None:
    mid = _method_with_overrides_out(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_SUPER_DECLARATION for h in out.hints_structured)


def test_hints_describe_method_unresolved_emits(kuzu_graph) -> None:
    mid = _method_with_unresolved(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_UNRESOLVED for h in out.hints_structured)


def test_hints_describe_client_http_targets_emits(kuzu_graph) -> None:
    cid = _client_with_http_calls_out(kuzu_graph)
    out = describe_v2(cid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_HTTP_TARGETS for h in out.hints_structured)


def test_hints_describe_producer_async_targets_emits(kuzu_graph) -> None:
    pid = _producer_with_async_calls_out(kuzu_graph)
    out = describe_v2(pid, graph=kuzu_graph)
    assert out.success and out.record
    assert any(h.label == mcp_hints.LABEL_ASYNC_TARGETS for h in out.hints_structured)


# ---------------------------------------------------------------------------
# Structured hint tests (PR-1)
# ---------------------------------------------------------------------------

def _assert_structured_hint(
    hints: list[_StructuredHint],
    *,
    tool: str,
    args_subset: dict[str, Any] | None = None,
    actionable: bool = True,
    label: str | None = None,
) -> _StructuredHint:
    """Find and return a structured hint matching tool, actionable, and args subset."""
    for h in hints:
        if h.tool != tool or h.actionable != actionable:
            continue
        if args_subset is not None:
            if not all(h.args.get(k) == v for k, v in args_subset.items()):
                continue
        if label is not None and h.label != label:
            continue
        return h
    pytest.fail(
        f"no structured hint with tool={tool!r} actionable={actionable} "
        f"args_subset={args_subset!r} in {[h._asdict() for h in hints]}"
    )


def _struct(output_kind, payload) -> list[_StructuredHint]:
    return generate_hints(output_kind, payload)


# --- Describe structured hints ---


def test_structured_hint_describe_type_rollup_clients(kuzu_graph) -> None:
    tid = _type_symbol_id_with_member_clients(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]},
        actionable=True,
    )


def test_structured_hint_describe_type_rollup_routes(kuzu_graph) -> None:
    tid = _controller_class_id_with_exposes(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "out", "edge_types": ["DECLARES.EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_describe_type_rollup_producers(kuzu_graph) -> None:
    tid = _type_symbol_id_with_member_producers(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "out", "edge_types": ["DECLARES.DECLARES_PRODUCER"]},
        actionable=True,
    )


def test_structured_hint_describe_method_overriders(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["OVERRIDDEN_BY"]},
        actionable=True,
    )


def test_structured_hint_describe_method_clients_in_overriders(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_CLIENT"]},
        actionable=True,
    )


def test_structured_hint_describe_method_producers_in_overriders(override_axis_graph: KuzuGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'publish' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractproducer.AbstractProducerApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["OVERRIDDEN_BY.DECLARES_PRODUCER"]},
        actionable=True,
    )


def test_structured_hint_describe_method_routes_in_overriders(override_axis_graph: KuzuGraph) -> None:
    rows = override_axis_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'handle' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "orolla.abstractroute.AbstractApi"},
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=override_axis_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["OVERRIDDEN_BY.EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_describe_method_outbound_client(kuzu_graph) -> None:
    mid = _method_declares_client(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["DECLARES_CLIENT"]},
        actionable=True,
    )


def test_structured_hint_describe_method_outbound_producer(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:DECLARES_PRODUCER]->(:Producer) WHERE m.kind = 'method' "
        "RETURN m.id AS id LIMIT 1",
    )
    if not rows:
        pytest.skip("no method with DECLARES_PRODUCER in fixture")
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["DECLARES_PRODUCER"]},
        actionable=True,
    )


def test_structured_hint_describe_method_inbound_route(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:EXPOSES]->(:Route) WHERE m.kind = 'method' RETURN m.id AS id LIMIT 1",
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_describe_route_declaring(kuzu_graph) -> None:
    rid = _route_id(kuzu_graph)
    out = describe_v2(rid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [rid], "direction": "in", "edge_types": ["EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_describe_client_declaring(kuzu_graph) -> None:
    cid = _client_id(kuzu_graph)
    out = describe_v2(cid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [cid], "direction": "in", "edge_types": ["DECLARES_CLIENT"]},
        actionable=True,
    )


def test_structured_hint_describe_producer_declaring(kuzu_graph) -> None:
    pid = _producer_id(kuzu_graph)
    out = describe_v2(pid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [pid], "direction": "in", "edge_types": ["DECLARES_PRODUCER"]},
        actionable=True,
    )


# --- Describe structural structured hints ---


def test_structured_hints_describe_interface_implementors(kuzu_graph) -> None:
    tid = _interface_with_implements_in(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "in", "edge_types": ["IMPLEMENTS"]},
        actionable=True,
    )


def test_structured_hints_describe_class_implements(kuzu_graph) -> None:
    tid = _class_with_implements_out(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "out", "edge_types": ["IMPLEMENTS"]},
        actionable=True,
    )


def test_structured_hints_describe_service_dependencies(kuzu_graph) -> None:
    tid = _service_with_injects_out(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "out", "edge_types": ["INJECTS"]},
        actionable=True,
    )


def test_structured_hints_describe_type_injectors(kuzu_graph) -> None:
    tid = _type_with_injects_in(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [tid], "direction": "in", "edge_types": ["INJECTS"]},
        actionable=True,
    )


def test_structured_hints_describe_method_outbound_calls(kuzu_graph) -> None:
    mid = _method_with_mid_calls_out(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["CALLS"]},
        actionable=True,
    )


def test_structured_hints_describe_method_super_declaration(kuzu_graph) -> None:
    mid = _method_with_overrides_out(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["OVERRIDES"]},
        actionable=True,
    )


def test_structured_hints_describe_method_unresolved(kuzu_graph) -> None:
    mid = _method_with_unresolved(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "direction": "out", "edge_types": ["CALLS"], "include_unresolved": True},
        actionable=True,
    )


def test_structured_hints_describe_client_http_targets(kuzu_graph) -> None:
    cid = _client_with_http_calls_out(kuzu_graph)
    out = describe_v2(cid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [cid], "direction": "out", "edge_types": ["HTTP_CALLS"]},
        actionable=True,
    )


def test_structured_hints_describe_producer_async_targets(kuzu_graph) -> None:
    pid = _producer_with_async_calls_out(kuzu_graph)
    out = describe_v2(pid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [pid], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
        actionable=True,
    )


# --- Find structured hints ---


def test_structured_hint_find_route_handler(kuzu_graph) -> None:
    out = find_v2("route", {"path_prefix": "/api"}, graph=kuzu_graph, limit=500, offset=0)
    assert out.success and out.results
    rid = out.results[0].id
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [rid], "direction": "in", "edge_types": ["EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_find_client_http_targets(kuzu_graph) -> None:
    out = find_v2("client", {"target_service": "smartcare-assign-chat"}, graph=kuzu_graph, limit=500)
    if not out.results:
        pytest.skip("no client with that target in fixture")
    cid = out.results[0].id
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [cid], "direction": "out", "edge_types": ["HTTP_CALLS"]},
        actionable=True,
    )


def test_structured_hint_find_producer_async_targets(kuzu_graph) -> None:
    out = find_v2("producer", {}, graph=kuzu_graph, limit=500)
    if not out.results:
        pytest.skip("no producers in fixture")
    pid = out.results[0].id
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [pid], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
        actionable=True,
    )


def test_structured_hint_find_empty_resolve(kuzu_graph) -> None:
    out = find_v2("client", {"target_service": "__no_such_target_service__"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    _assert_structured_hint(
        out.hints_structured,
        tool="resolve",
        args_subset={"hint_kind": "client"},
        actionable=True,
    )


# --- Resolve structured hints ---


def test_structured_hint_resolve_none_search() -> None:
    struct = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": "com.foo.Bar", "hint_kind": "symbol"},
    )
    _assert_structured_hint(struct, tool="search", args_subset={"query": "com.foo.Bar"})


def test_structured_hint_resolve_none_find_route() -> None:
    struct = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": "POST /v1/test",
            "hint_kind": "route",
            "path_prefix_seed": "/v1/test",
        },
    )
    _assert_structured_hint(
        struct, tool="find", args_subset={"kind": "route", "filter": {"path_prefix": "/v1/test"}},
    )


def test_structured_hint_resolve_none_find_client() -> None:
    struct = generate_hints(
        "resolve",
        {
            "status": "none",
            "resolved_identifier": "smartcare-assign-chat",
            "hint_kind": "client",
            "target_service_seed": "smartcare-assign-chat",
        },
    )
    _assert_structured_hint(
        struct, tool="find", args_subset={"kind": "client", "filter": {"target_service": "smartcare-assign-chat"}},
    )


def test_structured_hint_resolve_many_tighten() -> None:
    struct = generate_hints(
        "resolve",
        {"status": "many", "resolved_identifier": "open", "candidates": [{"id": "a"}, {"id": "b"}]},
    )
    _assert_structured_hint(struct, tool="resolve", actionable=False)


# --- Neighbors structured hints ---


def test_structured_hint_neighbors_empty_wrong_kind() -> None:
    payload = _neighbors_empty_payload(
        {"id": "sym:com.example.T#m()", "kind": "method"},
        ["HTTP_CALLS"],
    )
    struct = generate_hints("neighbors", payload)
    if struct:
        for h in struct:
            assert h.actionable is False


def test_structured_hint_neighbors_success_declares_dot_key_clients(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES"], graph=kuzu_graph, limit=50)
    assert out.success and out.results
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [class_id], "edge_types": ["DECLARES.DECLARES_CLIENT"]},
        actionable=True,
    )


def test_structured_hint_neighbors_success_declares_dot_key_routes(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES"], graph=kuzu_graph, limit=50)
    assert out.success and out.results
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [class_id], "edge_types": ["DECLARES.EXPOSES"]},
        actionable=True,
    )


def test_structured_hint_neighbors_success_http_targets() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="DECLARES_CLIENT")],
        requested_edge_types=["DECLARES_CLIENT"],
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["HTTP_CALLS"]})
    assert h.args["ids"] == ["client:a"]
    assert h.actionable is True


def test_structured_hint_neighbors_success_async_targets() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("producer:a", "producer"), edge_type="DECLARES_PRODUCER")],
        requested_edge_types=["DECLARES_PRODUCER"],
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["ASYNC_CALLS"]})
    assert h.args["ids"] == ["producer:a"]


def test_structured_hint_neighbors_success_callers() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:pkg.Handler#run()"), edge_type="EXPOSES", direction="in")],
        requested_edge_types=["EXPOSES"],
        requested_direction="in",
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["CALLS"]})
    assert h.args["direction"] == "in"


def test_structured_hint_neighbors_success_declaring_client() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="HTTP_CALLS", direction="in")],
        requested_edge_types=["HTTP_CALLS"],
        requested_direction="in",
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["DECLARES_CLIENT"]})
    assert h.args["ids"] == ["client:a"]


def test_structured_hint_neighbors_success_declaring_producer() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("producer:a", "producer"), edge_type="ASYNC_CALLS", direction="in")],
        requested_edge_types=["ASYNC_CALLS"],
        requested_direction="in",
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["DECLARES_PRODUCER"]})
    assert h.args["ids"] == ["producer:a"]


def test_structured_hint_neighbors_success_handler() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("route:a", "route"), edge_type="DECLARES.EXPOSES")],
        requested_edge_types=["DECLARES.EXPOSES"],
    )
    struct = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["EXPOSES"]})
    assert h.args["ids"] == ["route:a"]
    assert h.args["direction"] == "in"


# --- Prose-only / meta structured hints ---


def test_structured_hint_prose_only_not_actionable() -> None:
    # weak search score
    struct = generate_hints("search", {
        "success": True, "limit": 2, "offset": 0,
        "results": [{"score": 1.0}, {"score": 0.95}],
    })
    weak = [h for h in struct if h.tool == "find" and not h.actionable]
    assert weak, "expected actionable=False find hint for weak search"

    # CALLS fanout
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:a"), edge_type="CALLS")] * 12,
        requested_edge_types=["CALLS"],
    )
    payload["calls_row_count"] = 12
    struct = generate_hints("neighbors", payload)
    fanout = [h for h in struct if h.args.get("edge_types") == ["CALLS"] and not h.actionable]
    assert fanout, "expected actionable=False CALLS fanout hint"


def test_structured_hint_describe_many_calls_not_actionable(kuzu_graph) -> None:
    mid = _controller_method_many_calls(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    _assert_structured_hint(
        out.hints_structured,
        tool="neighbors",
        args_subset={"ids": [mid], "edge_types": ["CALLS"]},
        actionable=False,
    )


# --- Cap / dedup ---


def test_structured_hints_cap_5() -> None:
    # Build a payload that generates many triggers
    node_id = "sym:com.example.T"
    rec = {
        "id": node_id,
        "kind": "symbol",
        "fqn": "com.example.T",
        "data": {"kind": "method"},
        "edge_summary": {
            "OVERRIDDEN_BY": {"in": 0, "out": 1},
            "OVERRIDDEN_BY.DECLARES_CLIENT": {"in": 0, "out": 1},
            "OVERRIDDEN_BY.DECLARES_PRODUCER": {"in": 0, "out": 1},
            "OVERRIDDEN_BY.EXPOSES": {"in": 0, "out": 1},
            "DECLARES_CLIENT": {"in": 0, "out": 1},
            "DECLARES_PRODUCER": {"in": 0, "out": 1},
            "EXPOSES": {"in": 0, "out": 1},
            "CALLS": {"in": 0, "out": 12},
        },
    }
    struct = generate_hints("describe", {"success": True, "record": rec})
    assert len(struct) <= 5


def test_structured_hints_dedup() -> None:
    scored = [
        _StructuredHint("neighbors", {"ids": ["a"], "direction": "out", "edge_types": ["CALLS"]}, True, 1),
        _StructuredHint("neighbors", {"ids": ["a"], "direction": "out", "edge_types": ["CALLS"]}, True, 4),
    ]
    result = finalize_structured_hints(scored)
    assert len(result) == 1
    assert result[0].priority == 4


def test_structured_hint_round_trip(kuzu_graph) -> None:
    """Integration: build structured hint args into an actual neighbors_v2 call."""
    rid = _route_id(kuzu_graph)
    out = describe_v2(rid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints_structured
    h = out.hints_structured[0]
    assert h.tool == "neighbors"
    assert h.label, "structured hint should have a non-empty label"
    # Actually call neighbors_v2 with the structured hint args
    nout = neighbors_v2(
        ids=h.args["ids"],
        direction=h.args["direction"],
        edge_types=h.args["edge_types"],
        graph=kuzu_graph,
    )
    assert nout.success


def test_structured_hint_label_values() -> None:
    """Verify label values match expected semantic names for key hint scenarios."""
    # describe type with clients via members
    struct = _struct("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T", "data": {"kind": "class"},
        "edge_summary": {"DECLARES.DECLARES_CLIENT": {"in": 0, "out": 3}},
    }})
    assert any(h.label == "clients via members" for h in struct)

    # describe type with routes via members
    struct = _struct("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T", "data": {"kind": "class"},
        "edge_summary": {"DECLARES.EXPOSES": {"in": 0, "out": 2}},
    }})
    assert any(h.label == "routes via members" for h in struct)

    # describe route -> declaring method
    struct = _struct("describe", {"success": True, "record": {"id": "route:a", "kind": "route", "fqn": "GET /"}})
    assert any(h.label == "declaring method" for h in struct)

    # resolve none -> try search
    struct = _struct("resolve", {"status": "none", "resolved_identifier": "com.foo.Bar", "hint_kind": "symbol"})
    assert any(h.label == "try search" for h in struct)

    # resolve none route -> try find route
    struct = _struct("resolve", {"status": "none", "resolved_identifier": "x", "hint_kind": "route", "path_prefix_seed": "/api"})
    assert any(h.label == "try find route" for h in struct)

    # resolve many -> tighten identifier
    struct = _struct("resolve", {"status": "many", "resolved_identifier": "x", "candidates": [{"id": "a"}, {"id": "b"}]})
    assert any(h.label == "tighten identifier" for h in struct)

    # find empty -> try resolve
    struct = _struct("find", {"success": True, "kind": "client", "results": [], "filter": {"target_service": "x"}, "offset": 0})
    assert any(h.label == "try resolve" for h in struct)

    # find page full
    struct = _struct("find", {"success": True, "kind": "symbol", "results": [{"id": "a"}], "filter": {}, "limit": 1, "has_more_results": True, "offset": 0})
    assert any(h.label == "page full" for h in struct)

    # search weak
    struct = _struct("search", {"success": True, "results": [
        {"score": 1.0}, {"score": 0.95},
    ], "limit": 2})
    assert any(h.label == "weak results" for h in struct)

    # describe method with overriders
    struct = _struct("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T#m()", "data": {"kind": "method"},
        "edge_summary": {"OVERRIDDEN_BY": {"in": 0, "out": 1}},
    }})
    assert any(h.label == "overriders" for h in struct)

    # describe method with outbound calls
    struct = _struct("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T#m()", "data": {"kind": "method", "role": "SERVICE"},
        "edge_summary": {"CALLS": {"in": 0, "out": 3}},
    }})
    assert any(h.label == "outbound calls" for h in struct)

    # describe method with many calls (>=10) -> not actionable
    struct = _struct("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T#m()", "data": {"kind": "method", "role": "OTHER"},
        "edge_summary": {"CALLS": {"in": 0, "out": 12}},
    }})
    assert any(h.label == "high fanout" and not h.actionable for h in struct)

    # neighbors success callers (N4)
    struct = _struct("neighbors", {
        "success": True,
        "results": [{"origin_id": "sym:T", "edge_type": "EXPOSES", "direction": "in",
                     "other": {"id": "sym:m", "kind": "symbol", "symbol_kind": "method"}, "attrs": {}}],
        "requested_edge_types": ["EXPOSES"], "requested_direction": "in", "offset": 0,
        "subject_record": {"id": "route:a", "kind": "route"}, "origin_id": "route:a",
    })
    assert any(h.label == "callers" for h in struct)


# ---------------------------------------------------------------------------
# New tests: reason char cap + no string hints field
# ---------------------------------------------------------------------------


def test_structured_hints_reason_char_cap() -> None:
    """All emitted reason strings should be <= 120 chars."""
    payloads = [
        ("describe", {"success": True, "record": {
            "id": "sym:a", "kind": "symbol", "fqn": "T", "data": {"kind": "class"},
            "edge_summary": {"DECLARES.DECLARES_CLIENT": {"in": 0, "out": 3}},
        }}),
        ("describe", {"success": True, "record": {
            "id": "sym:a", "kind": "symbol", "fqn": "T#m()", "data": {"kind": "method"},
            "edge_summary": {"OVERRIDDEN_BY": {"in": 0, "out": 1}, "CALLS": {"in": 0, "out": 12}},
        }}),
        ("describe", {"success": True, "record": {"id": "route:a", "kind": "route", "fqn": "GET /"}}),
        ("find", {"success": True, "kind": "client", "results": [], "filter": {"target_service": "x"}, "offset": 0}),
        ("search", {"success": True, "results": [{"score": 0.5}, {"score": 0.49}], "limit": 5, "offset": 0}),
        ("resolve", {"status": "many", "resolved_identifier": "x", "candidates": [{"id": "a"}, {"id": "b"}]}),
        ("resolve", {"status": "none", "resolved_identifier": "com.foo.Bar", "hint_kind": "symbol"}),
        ("neighbors", {
            "success": True, "results": [], "requested_edge_types": ["HTTP_CALLS"],
            "requested_direction": "out", "offset": 0,
            "subject_record": {"id": "sym:a", "kind": "method"},
        }),
    ]
    for output_kind, payload in payloads:
        for h in generate_hints(output_kind, payload):
            assert len(h.reason) <= 120, f"reason too long ({len(h.reason)} chars) for {output_kind}: {h.reason!r}"


def test_no_string_hints_field() -> None:
    """Verify output models have no `hints` field (only `hints_structured`)."""
    for cls in (SearchOutput, FindOutput, DescribeOutput, NeighborsOutput, ResolveOutput):
        assert "hints" not in cls.model_fields, f"{cls.__name__} still has `hints` field"
