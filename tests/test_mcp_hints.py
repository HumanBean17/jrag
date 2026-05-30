from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from _builders import build_kuzu_to
from java_ontology import FUZZY_STRATEGY_SET
from kuzu_queries import KuzuGraph
from mcp_hints import (
    _StructuredHint,
    finalize_structured_hints,
    generate_hints,
)
from mcp_v2 import (
    FindOutput,
    SearchOutput,
    _hints_or_skip,
    set_hints_enabled,
    describe_v2,
    find_v2,
    neighbors_v2,
    resolve_v2,
)

_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})

_OVERRIDE_AXIS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "override_axis_rollup_smoke"


def _hints(output_kind, payload):
    """Convenience wrapper — returns string hints only (backward compat for existing tests)."""
    struct, advisories = generate_hints(output_kind, payload)
    return advisories


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
        if out.success and out.record and out.hints_structured == [] and out.advisories == []:
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






















def test_hints_clean_outputs_empty(kuzu_graph) -> None:
    mid = _method_id_with_empty_describe_hints(kuzu_graph)
    out = describe_v2(mid, graph=kuzu_graph)
    assert out.success and out.record
    assert out.hints_structured == []
    assert out.advisories == []

    count_rows = kuzu_graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol) WHERE s.role = 'CONTROLLER' RETURN count(*) AS n",
    )
    n_controllers = int(count_rows[0]["n"])
    assert n_controllers > 0
    assert n_controllers <= 500, "fixture has >500 CONTROLLER symbols; narrow filter for clean find hints"
    fout = find_v2("symbol", {"role": "CONTROLLER"}, graph=kuzu_graph, limit=500, offset=0)
    assert fout.success and len(fout.results) == n_controllers
    assert fout.hints_structured == []
    assert fout.advisories == []


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












# ---------------------------------------------------------------------------
# Describe structural hints — helpers + tests (PR-DESCRIBE-STRUCTURAL-1)
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
        "RETURN cls.id AS id",
    )
    if not rows:
        pytest.skip("no class with IMPLEMENTS.out > 0 in fixture")
    # Find a class whose IMPLEMENTS hint is not suppressed by type rollup
    # (DECLARES_CLIENT/EXPOSES/DECLARES_PRODUCER suppresses IMPLEMENTS).
    for row in rows:
        tid = str(row["id"])
        out = describe_v2(tid, graph=kuzu_graph)
        if any(
            h.tool == "neighbors" and h.args.get("edge_types") == ["IMPLEMENTS"]
            for h in out.hints_structured
        ):
            return tid
    pytest.skip("no class with unsuppressed IMPLEMENTS hint in fixture")


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
        f"args_subset={args_subset!r} in {[h.model_dump() for h in hints]}"
    )


def _struct(output_kind, payload) -> list[_StructuredHint]:
    return generate_hints(output_kind, payload)[0]


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
    struct, _ = generate_hints(
        "resolve",
        {"status": "none", "resolved_identifier": "com.foo.Bar", "hint_kind": "symbol"},
    )
    _assert_structured_hint(struct, tool="search", args_subset={"query": "com.foo.Bar"})


def test_structured_hint_resolve_none_find_route() -> None:
    struct, _ = generate_hints(
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
    struct, _ = generate_hints(
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
    struct, _ = generate_hints(
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
    struct, _ = generate_hints("neighbors", payload)
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
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["HTTP_CALLS"]})
    assert h.args["ids"] == ["client:a"]
    assert h.actionable is True


def test_structured_hint_neighbors_success_async_targets() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("producer:a", "producer"), edge_type="DECLARES_PRODUCER")],
        requested_edge_types=["DECLARES_PRODUCER"],
    )
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["ASYNC_CALLS"]})
    assert h.args["ids"] == ["producer:a"]


def test_structured_hint_neighbors_success_callers() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:pkg.Handler#run()"), edge_type="EXPOSES", direction="in")],
        requested_edge_types=["EXPOSES"],
        requested_direction="in",
    )
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["CALLS"]})
    assert h.args["direction"] == "in"


def test_structured_hint_neighbors_success_declaring_client() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("client:a", "client"), edge_type="HTTP_CALLS", direction="in")],
        requested_edge_types=["HTTP_CALLS"],
        requested_direction="in",
    )
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["DECLARES_CLIENT"]})
    assert h.args["ids"] == ["client:a"]


def test_structured_hint_neighbors_success_declaring_producer() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("producer:a", "producer"), edge_type="ASYNC_CALLS", direction="in")],
        requested_edge_types=["ASYNC_CALLS"],
        requested_direction="in",
    )
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["DECLARES_PRODUCER"]})
    assert h.args["ids"] == ["producer:a"]


def test_structured_hint_neighbors_success_handler() -> None:
    payload = _neighbors_hint_payload(
        [_success_edge(_terminal_other("route:a", "route"), edge_type="DECLARES.EXPOSES")],
        requested_edge_types=["DECLARES.EXPOSES"],
    )
    struct, _ = generate_hints("neighbors", payload)
    h = _assert_structured_hint(struct, tool="neighbors", args_subset={"edge_types": ["EXPOSES"]})
    assert h.args["ids"] == ["route:a"]
    assert h.args["direction"] == "in"


# --- Prose-only / meta structured hints ---


def test_structured_hint_prose_only_not_actionable() -> None:
    # weak search score
    struct, _ = generate_hints("search", {
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
    struct, _ = generate_hints("neighbors", payload)
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


# --- Cap / dedup / parity ---


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
    struct, _ = generate_hints("describe", {"success": True, "record": rec})
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
    assert h.reason, "structured hint should have a non-empty reason"
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

    # describe route → declaring method
    struct = _struct("describe", {"success": True, "record": {"id": "route:a", "kind": "route", "fqn": "GET /"}})
    assert any(h.label == "declaring method" for h in struct)

    # resolve none → try search
    struct = _struct("resolve", {"status": "none", "resolved_identifier": "com.foo.Bar", "hint_kind": "symbol"})
    assert any(h.label == "try search" for h in struct)

    # resolve none route → try find route
    struct = _struct("resolve", {"status": "none", "resolved_identifier": "x", "hint_kind": "route", "path_prefix_seed": "/api"})
    assert any(h.label == "try find route" for h in struct)

    # resolve many → tighten identifier
    struct = _struct("resolve", {"status": "many", "resolved_identifier": "x", "candidates": [{"id": "a"}, {"id": "b"}]})
    assert any(h.label == "tighten identifier" for h in struct)

    # find empty → try resolve
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

    # describe method with many calls (≥10) → not actionable
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


# --- New tests for PR-1: reason, advisories, no hints field ---

def test_structured_hints_reason_content() -> None:
    """Verify reason carries expected text for key scenarios."""
    # weak search score should have reason
    struct, _ = generate_hints("search", {
        "success": True, "limit": 2, "offset": 0,
        "results": [{"score": 1.0}, {"score": 0.95}],
    })
    weak = [h for h in struct if h.tool == "find" and not h.actionable]
    assert weak, "expected actionable=False find hint for weak search"
    assert weak[0].reason, "weak search hint should have a reason"
    assert "score" in weak[0].reason.lower() or "variance" in weak[0].reason.lower()

    # CALLS fanout should have reason
    payload = _neighbors_hint_payload(
        [_success_edge(_symbol_other("sym:a"), edge_type="CALLS")] * 12,
        requested_edge_types=["CALLS"],
    )
    payload["calls_row_count"] = 12
    struct, _ = generate_hints("neighbors", payload)
    fanout = [h for h in struct if h.args.get("edge_types") == ["CALLS"] and not h.actionable]
    assert fanout, "expected actionable=False CALLS fanout hint"
    assert fanout[0].reason, "fanout hint should have a reason"
    assert "fanout" in fanout[0].reason.lower() or "noisy" in fanout[0].reason.lower() or "call" in fanout[0].reason.lower()


def test_structured_hints_reason_char_cap() -> None:
    """All reason strings should be ≤ 120 chars."""
    # Build a payload that generates many hints
    node_id = "sym:com.example.T"
    rec = {
        "id": node_id,
        "kind": "symbol",
        "fqn": "com.example.T",
        "data": {"kind": "method"},
        "edge_summary": {
            "OVERRIDDEN_BY": {"in": 0, "out": 1},
            "DECLARES_CLIENT": {"in": 0, "out": 1},
            "CALLS": {"in": 0, "out": 12},
        },
    }
    struct, _ = generate_hints("describe", {"success": True, "record": rec})
    for h in struct:
        assert len(h.reason) <= 120, f"reason too long: {h.reason!r}"


def test_no_string_hints_field() -> None:
    """Verify no output model has hints field."""
    from mcp_v2 import (
        DescribeOutput,
        NeighborsOutput,
        ResolveOutput,
    )
    for model_class in [FindOutput, SearchOutput, DescribeOutput, NeighborsOutput, ResolveOutput]:
        assert "hints" not in model_class.model_fields, f"{model_class.__name__} still has hints field"
        assert "hints_structured" in model_class.model_fields, f"{model_class.__name__} missing hints_structured field"
        assert "advisories" in model_class.model_fields, f"{model_class.__name__} missing advisories field"


def test_advisories_content() -> None:
    """Verify advisory strings appear for fuzzy strategy, brownfield absence, etc."""
    # fuzzy strategy on CALLS
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="layer_c_source", edge_type="CALLS")],
        requested_edge_types=["CALLS"],
    )
    _, advisories = generate_hints("neighbors", payload)
    assert advisories
    assert any("strategy" in a.lower() for a in advisories)

    # brownfield absence (Producer with ASYNC_CALLS and no outgoing edges)
    payload = _neighbors_empty_payload(
        {"id": "producer:svc:kafka:t", "producer_kind": "kafka"},
        ["ASYNC_CALLS"],
    )
    _, advisories = generate_hints("neighbors", payload)
    assert advisories
    assert any("brownfield" in a.lower() for a in advisories)


def test_advisories_absent_when_no_pure_info() -> None:
    """Verify advisories == [] for scenarios with only tool-call hints."""
    # describe route has only structured hints (declaring method)
    struct, advisories = generate_hints("describe", {
        "success": True,
        "record": {"id": "route:a", "kind": "route", "fqn": "GET /"},
    })
    assert struct, "expected structured hints for describe route"
    assert advisories == [], f"expected no advisories for pure tool-call hints, got: {advisories}"


def test_structured_hints_no_empty_args() -> None:
    """Verify no structured hint has empty args without concrete tool call."""
    # All hints from describe type with clients should have concrete tool call args
    struct, _ = generate_hints("describe", {"success": True, "record": {
        "id": "sym:a", "kind": "symbol", "fqn": "T", "data": {"kind": "class"},
        "edge_summary": {"DECLARES.DECLARES_CLIENT": {"in": 0, "out": 3}},
    }})
    for h in struct:
        assert h.args, f"structured hint has empty args: {h._asdict()}"


def test_advisories_char_cap() -> None:
    """All advisory strings should be ≤ 200 chars."""
    # Generate hints with potential advisories
    payload = _neighbors_hint_payload(
        [_edge_result(strategy="layer_c_source", edge_type="CALLS")] * 5,
        requested_edge_types=["CALLS"],
    )
    _, advisories = generate_hints("neighbors", payload)
    for a in advisories:
        assert len(a) <= 200, f"advisory too long: {a!r}"


def test_hints_or_skip_skips_when_disabled() -> None:
    """_hints_or_skip returns empty lists and never calls generate_hints when disabled."""
    set_hints_enabled(False)
    try:
        struct, advisories = _hints_or_skip("search", {"success": True, "results": []})
        assert struct == []
        assert advisories == []
    finally:
        set_hints_enabled(True)  # restore default for other tests
