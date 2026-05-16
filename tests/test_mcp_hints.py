from __future__ import annotations

import inspect
from collections import Counter
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
from java_ontology import FUZZY_STRATEGY_SET
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
        if out.success and out.record and out.hints == []:
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


def test_hints_find_empty_symbol_fqn_prefix_suggests_resolve(kuzu_graph) -> None:
    out = find_v2("symbol", {"fqn_prefix": "__no_such_prefix__"}, graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert any("resolve(identifier" in h and "hint_kind='symbol'" in h for h in out.hints)


def test_hints_find_page_full_emits_narrow_or_paginate(kuzu_graph) -> None:
    out = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=1, offset=0)
    assert out.success is True
    assert len(out.results) >= 1
    assert mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1) in out.hints


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
    assert mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1) not in last.hints


def test_hints_neighbors_empty_with_edge_types_emits_kind_check(kuzu_graph) -> None:
    class_id = _class_symbol_id(kuzu_graph)
    out = neighbors_v2(class_id, direction="out", edge_types=["DECLARES_CLIENT"], graph=kuzu_graph)
    assert out.success is True
    assert out.results == []
    assert out.requested_edge_types == ["DECLARES_CLIENT"]
    assert mcp_hints.TPL_NEIGHBORS_EMPTY_KIND_CHECK in out.hints


def _neighbors_hint_payload(
    results: list[dict[str, Any]],
    *,
    requested_edge_types: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "results": results,
        "requested_edge_types": requested_edge_types or ["DECLARES_CLIENT"],
    }


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
    payload = _neighbors_hint_payload([_edge_result(strategy="layer_c_source")])
    hints = generate_hints("neighbors", payload)
    assert mcp_hints.TPL_NEIGHBORS_FUZZY_STRATEGY in hints
    assert "attrs.strategy" in hints[0]


def test_hints_neighbors_fuzzy_strategy_annotation_absent() -> None:
    payload = _neighbors_hint_payload([_edge_result(strategy="annotation")])
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_fuzzy_strategy_calls_phantom_emits() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="phantom", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert mcp_hints.TPL_NEIGHBORS_FUZZY_STRATEGY in hints


def test_hints_neighbors_declares_no_strategy_attrs_empty() -> None:
    payload = _neighbors_hint_payload(
        [_edge_result(edge_type="DECLARES")],
        requested_edge_types=["DECLARES"],
    )
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_multi_origin_fuzzy_emits_once() -> None:
    payload = _neighbors_hint_payload(
        [
            _edge_result(strategy="phantom", edge_type="CALLS"),
            _edge_result(strategy="annotation", edge_type="CALLS"),
        ],
        requested_edge_types=["CALLS"],
    )
    hints = generate_hints("neighbors", payload)
    assert hints.count(mcp_hints.TPL_NEIGHBORS_FUZZY_STRATEGY) == 1


def test_hints_neighbors_layer_a_meta_no_fuzzy_hint() -> None:
    payload = _neighbors_hint_payload([_edge_result(strategy="layer_a_meta")])
    assert generate_hints("neighbors", payload) == []


def test_hints_neighbors_fuzzy_strategy_neighbors_v2_round_trip(kuzu_graph) -> None:
    mid = _method_id_with_fuzzy_calls(kuzu_graph)
    out = neighbors_v2(mid, direction="out", edge_types=["CALLS"], graph=kuzu_graph, limit=50)
    assert out.success is True
    assert out.results
    strategies = [e.attrs.get("strategy") for e in out.results]
    assert any(s in FUZZY_STRATEGY_SET for s in strategies if isinstance(s, str))
    assert mcp_hints.TPL_NEIGHBORS_FUZZY_STRATEGY in out.hints
    assert "brownfield/fallback strategy" in out.hints[0]


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


def test_hints_cap_same_priority_keeps_emission_order() -> None:
    scored = [
        (PRIORITY_META, "z-meta"),
        (PRIORITY_META, "a-meta"),
        (PRIORITY_META, "b-meta"),
        (PRIORITY_META, "c-meta"),
        (PRIORITY_META, "d-meta"),
        (PRIORITY_META, "e-meta"),
    ]
    got = finalize_hint_list(scored)
    assert len(got) == 5
    assert "z-meta" in got
    assert "e-meta" not in got


def test_hints_find_page_full_requires_has_more_results_flag() -> None:
    full_page = {
        "success": True,
        "kind": "symbol",
        "results": [{"id": "sym:a"}],
        "limit": 1,
        "offset": 0,
        "filter": {},
    }
    assert mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1) not in generate_hints("find", full_page)
    assert mcp_hints.TPL_FIND_PAGE_FULL.format(limit=1) in generate_hints(
        "find", {**full_page, "has_more_results": True}
    )


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
    mid = _method_id_with_empty_describe_hints(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints == []

    count_rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.role = 'CONTROLLER' RETURN count(*) AS n",
    )
    n_controllers = int(count_rows[0]["n"])
    assert n_controllers > 0
    assert n_controllers <= 500, "fixture has >500 CONTROLLER symbols; narrow filter for clean find hints"
    fout = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=500, offset=0)
    assert fout.success and len(fout.results) == n_controllers
    assert fout.hints == []


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
    assert "search(query=" in hints[0]
    assert ident in hints[0]


def test_hints_resolve_status_none_symbol_drop_on_overflow() -> None:
    ident = "x" * 80
    hints = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": ident, "hint_kind": "symbol"},
    )
    assert hints == []


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
    assert "find(kind='route'" in hints[0]
    assert seed in hints[0]


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
    assert "find(kind='client'" in hints[0]
    assert seed in hints[0]


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
    assert "2 candidates" in hints[0]
    assert "tighten identifier" in hints[0]


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
    assert "10 candidates" in hints[0]


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
    assert none_out.hints
    assert "search(query=" in none_out.hints[0]

    one_id = _resolve_symbol_id_status_one(kuzu_graph)
    one_out = resolve_v2(one_id, hint_kind="symbol", graph=kuzu_graph)
    assert one_out.resolved_identifier == one_id
    assert one_out.hints == []

    wildcard_out = resolve_v2("com.foo.*Service", hint_kind="symbol", graph=kuzu_graph)
    assert wildcard_out.success is True
    assert wildcard_out.status == "none"
    assert wildcard_out.resolved_identifier == "com.foo.*Service"
    assert wildcard_out.hints == []

    many_ident = _resolve_symbol_short_name_status_many(kuzu_graph)
    many_out = resolve_v2(many_ident, hint_kind="symbol", graph=kuzu_graph)
    assert many_out.resolved_identifier == many_ident
    assert many_out.hints
    assert "candidates" in many_out.hints[0]
    assert "tighten identifier" in many_out.hints[0]

    route_ident = "POST /v1/__no_such_resolve_route__"
    route_out = resolve_v2(route_ident, hint_kind="route", graph=kuzu_graph)
    assert route_out.success is True
    assert route_out.status == "none"
    assert route_out.resolved_identifier == route_ident
    assert route_out.hints
    assert "find(kind='route'" in route_out.hints[0]

    client_ident = "__no_such_resolve_client_target__"
    client_out = resolve_v2(client_ident, hint_kind="client", graph=kuzu_graph)
    assert client_out.success is True
    assert client_out.status == "none"
    assert client_out.resolved_identifier == client_ident
    assert client_out.hints
    assert "find(kind='client'" in client_out.hints[0]

    invalid_out = resolve_v2("", graph=kuzu_graph)
    assert invalid_out.success is False
    assert invalid_out.resolved_identifier is None
    assert invalid_out.hints == []


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
        (mcp_hints.TPL_RESOLVE_NONE_TRY_SEARCH, {"identifier": "com.example.Type#m()"}),
        (
            mcp_hints.TPL_RESOLVE_NONE_TRY_FIND_ROUTE,
            {"seed": "/v1/operator/session/update"},
        ),
        (mcp_hints.TPL_RESOLVE_NONE_TRY_FIND_CLIENT, {"seed": "smartcare-assign-chat"}),
        (mcp_hints.TPL_RESOLVE_MANY_TIGHTEN, {"n": 10}),
        (mcp_hints.TPL_NEIGHBORS_FUZZY_STRATEGY, {}),
    ],
)
def test_hints_template_rendered_length_leq_120(template: str, fmt: dict[str, Any]) -> None:
    rendered = template.format(**fmt) if fmt else template
    assert len(rendered) <= 120, rendered
