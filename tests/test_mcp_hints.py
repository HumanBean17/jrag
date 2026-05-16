from __future__ import annotations

import inspect
from typing import Any

import pytest

import mcp_hints
from mcp_hints import (
    PRIORITY_DECLARES_TYPE_ROLLUP,
    PRIORITY_LEAF_FOLLOWUP,
    PRIORITY_META,
    PRIORITY_OVERRIDDEN_AXIS,
    finalize_hint_list,
    generate_hints,
)
from mcp_v2 import FindOutput, SearchOutput, describe_v2, find_v2, neighbors_v2, resolve_v2, search_v2

_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})


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


def _interface_method_with_override_rollups(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (iface:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ChatAssignmentPort"},
    )
    assert rows
    return str(rows[0]["id"])


def _concrete_override_method_id(kuzu_graph) -> str:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (t:Symbol {fqn: $fqn})-[:DECLARES]->(m:Symbol) "
        "WHERE m.kind = 'method' AND m.name = 'requestAssignment' "
        "RETURN m.id AS id LIMIT 1",
        {"fqn": "com.bank.chat.engine.assign.ConfigurableChatAssignment"},
    )
    assert rows
    return str(rows[0]["id"])


def _method_id_declares_client_and_other_out_edge(kuzu_graph) -> str | None:
    for pattern in (
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:CALLS]->() RETURN m.id AS id LIMIT 1",
        "MATCH (m:Symbol {kind: 'method'})-[:DECLARES_CLIENT]->() MATCH (m)-[:HTTP_CALLS]->() RETURN m.id AS id LIMIT 1",
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
    want = mcp_hints.TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS.format(id=tid)
    assert want in out.hints


def test_hints_describe_type_symbol_routes_via_members_emits(kuzu_graph) -> None:
    tid = _controller_class_id_with_exposes(kuzu_graph)
    out = describe_v2(tid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS.format(id=tid)
    assert want in out.hints


def test_hints_describe_method_overriders_emits(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_METHOD_OVERRIDERS.format(id=mid)
    assert want in out.hints


def test_hints_describe_method_clients_in_overriders_emits(kuzu_graph) -> None:
    mid = _interface_method_with_override_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS.format(id=mid)
    assert want in out.hints


def test_hints_describe_method_declares_client_emits(kuzu_graph) -> None:
    mid = _method_declares_client(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT.format(id=mid)
    assert want in out.hints


def test_hints_describe_method_exposes_emits(kuzu_graph) -> None:
    rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (m:Symbol)-[:EXPOSES]->(:Route) WHERE m.kind = 'method' RETURN m.id AS id LIMIT 1"
    )
    assert rows
    mid = str(rows[0]["id"])
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_METHOD_INBOUND_ROUTE.format(id=mid)
    assert want in out.hints


def test_hints_describe_method_many_calls_emits(kuzu_graph) -> None:
    mid = _controller_method_many_calls(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert mcp_hints.TPL_DESCRIBE_METHOD_MANY_CALLS in out.hints


def test_hints_describe_route_always_declaring_method(kuzu_graph) -> None:
    rid = _route_id(kuzu_graph)
    out = describe_v2(rid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_ROUTE_DECLARING.format(id=rid)
    assert out.hints == [want]


def test_hints_describe_client_always_declaring_method(kuzu_graph) -> None:
    cid = _client_id(kuzu_graph)
    out = describe_v2(cid, graph=kuzu_graph)
    assert out.success and out.record
    want = mcp_hints.TPL_DESCRIBE_CLIENT_DECLARING.format(id=cid)
    assert out.hints == [want]


def test_hints_find_empty_identifier_filter_suggests_resolve(kuzu_graph) -> None:
    out = find_v2("client", {"target_service": "__no_such_target_service__"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert "hint_kind" in inspect.signature(resolve_v2).parameters
    assert any("resolve(identifier" in h and "hint_kind='client'" in h for h in out.hints)


def test_hints_find_page_full_emits_narrow_or_paginate(kuzu_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=1, offset=0)
    assert out.success is True
    assert len(out.results) >= 1
    assert mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1) in out.hints


def test_hints_neighbors_empty_with_edge_types_emits_kind_check(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES_CLIENT"], graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert out.requested_edge_types == ["DECLARES_CLIENT"]
    assert mcp_hints.TPL_NEIGHBORS_EMPTY_KIND_CHECK in out.hints


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
    assert mcp_hints.TPL_SEARCH_WEAK in out.hints


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
    assert mcp_hints.TPL_SEARCH_WEAK not in out.hints


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


def test_hints_dedupe_collapses_identical_rendered_strings() -> None:
    out = finalize_hint_list(
        [
            (PRIORITY_META, "same"),
            (PRIORITY_DECLARES_TYPE_ROLLUP, "same"),
        ]
    )
    assert out == ["same"]


def test_hints_cap_drops_lowest_priority_over_five() -> None:
    scored = [
        (PRIORITY_META, "m1"),
        (PRIORITY_META, "m2"),
        (PRIORITY_LEAF_FOLLOWUP, "l1"),
        (PRIORITY_LEAF_FOLLOWUP, "l2"),
        (PRIORITY_OVERRIDDEN_AXIS, "o1"),
        (PRIORITY_DECLARES_TYPE_ROLLUP, "d1"),
    ]
    got = finalize_hint_list(scored)
    assert len(got) == 5
    assert "m2" not in got
    assert "d1" in got and "o1" in got


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
        assert "via members" not in h


def test_hints_clean_outputs_empty(kuzu_graph) -> None:
    mid = _method_id_without_dispatch_rollups(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints == []

    fout = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=500, offset=0)
    assert fout.success and fout.results
    assert fout.hints == []


def test_hints_error_path_success_false_empty(kuzu_graph) -> None:
    assert generate_hints("find", {"success": False, "kind": "symbol", "results": [], "filter": {}}) == []
    assert generate_hints("search", {"success": False, "results": []}) == []
    assert generate_hints("describe", {"success": False, "record": {}}) == []
    assert generate_hints("neighbors", {"success": False, "results": [], "requested_edge_types": ["CALLS"]}) == []
    serr = search_v2("q", filter={"bad_key": 1}, graph=kuzu_graph)
    assert serr.success is False and serr.hints == [] and serr.limit is None and serr.offset is None
    ferr = find_v2("symbol", {"path_prefix": "/api"}, graph=kuzu_graph)
    assert ferr.success is False and ferr.hints == [] and ferr.limit is None and ferr.offset is None


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
        hints=[],
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
        hints=[],
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
    assert (
        mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1)
        not in generate_hints(
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


@pytest.mark.parametrize(
    ("template", "fmt"),
    [
        (mcp_hints.TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS, {"id": "sym:a"}),
        (mcp_hints.TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS, {"id": "sym:a"}),
        (mcp_hints.TPL_DESCRIBE_METHOD_OVERRIDERS, {"id": "sym:a"}),
        (mcp_hints.TPL_DESCRIBE_METHOD_CLIENTS_IN_OVERRIDERS, {"id": "sym:a"}),
        (mcp_hints.TPL_DESCRIBE_METHOD_OUTBOUND_CLIENT, {"id": "sym:pkg.Type#m(int)"}),
        (mcp_hints.TPL_DESCRIBE_METHOD_INBOUND_ROUTE, {"id": "sym:pkg.Type#m(int)"}),
        (mcp_hints.TPL_DESCRIBE_METHOD_MANY_CALLS, {}),
        (mcp_hints.TPL_DESCRIBE_ROUTE_DECLARING, {"id": "route:svc:GET:/api/v1/x"}),
        (mcp_hints.TPL_DESCRIBE_CLIENT_DECLARING, {"id": "client:svc:feign:target:GET:/p"}),
        (mcp_hints.TPL_FIND_EMPTY_RESOLVE, {"kind": "client"}),
        (mcp_hints.TPL_FIND_PAGE_FULL, {"limit": 500}),
        (mcp_hints.TPL_NEIGHBORS_EMPTY_KIND_CHECK, {}),
        (mcp_hints.TPL_SEARCH_WEAK, {}),
    ],
)
def test_hints_template_rendered_length_leq_120(template: str, fmt: dict[str, Any]) -> None:
    rendered = template.format(**fmt) if fmt else template
    assert len(rendered) <= 120, rendered
