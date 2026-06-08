"""Tests for the read-only `KuzuGraph` helpers used by the MCP server.

We exercise every public method on `kuzu_queries.KuzuGraph` against the
bank-chat-system corpus. The fixture provides:

  * one **interface with multiple in-corpus implementations**:
        EventProcessor (chat-engine.processors.*)
  * one **abstract / Spring Data parent** that resolves to a phantom:
        JpaRepository  (extended by every chat-assign repository)
  * **constructor-injected** services (e.g. ChatManagementService) so the
    INJECTS edges are dense and easy to walk
  * **stereotyped roles in both styles**: @RestController, @Service,
    @Component, @Entity, plus DTO inference by class-name suffix
    (Body / Request / Response / ...)

⚠️  Assertions are deliberately loose (presence / >= 1) so they don't
break when the fixture grows. See `tests/README.md`.
"""
from __future__ import annotations

from pathlib import Path

import kuzu
import pytest

from ast_java import ONTOLOGY_VERSION
from kuzu_queries import KuzuGraph, _is_external_fqn


def _names(symbols) -> set[str]:
    return {s.name for s in symbols}


def _modules(symbols) -> set[str]:
    return {s.module for s in symbols if s.module}


def _microservices(symbols) -> set[str]:
    return {s.microservice for s in symbols if s.microservice}


# ---------------- meta ----------------


def test_meta(kuzu_graph) -> None:
    meta = kuzu_graph.meta()
    assert "error" not in meta, meta
    assert meta["ontology_version"] >= 1
    assert meta["built_at"] > 0
    assert meta["counts"]["types"] > 0
    assert meta["counts"]["injects"] > 0
    assert meta["counts"].get("calls", 0) > 0
    assert meta["counts"].get("declares", 0) > 0
    assert meta.get("routes_total", 0) >= 1
    assert isinstance(meta.get("routes_by_framework"), dict)


def test_module_counts_keys(kuzu_graph) -> None:
    counts = kuzu_graph.module_counts()
    assert counts.get("chat-assign", 0) > 0
    # Multi-module reactor child modules should appear by their build-marker
    # directory name.
    assert any(k in counts for k in ("chat-app", "chat-engine", "chat-domain"))


def test_microservice_counts_keys(kuzu_graph) -> None:
    counts = kuzu_graph.microservice_counts()
    # Both microservice roots should be represented; the multi-module
    # reactor (`chat-core`) groups chat-app/chat-engine/chat-domain/
    # chat-contracts under one microservice key.
    assert counts.get("chat-assign", 0) > 0
    assert counts.get("chat-core", 0) > 0


# ---------------- find_by_name_or_fqn ----------------


def test_find_by_name_or_fqn_simple_name(kuzu_graph) -> None:
    rows = kuzu_graph.find_by_name_or_fqn("ChatManagementService")
    assert any(r.kind == "class" and r.fqn.endswith(".ChatManagementService") for r in rows), rows


def test_find_by_name_or_fqn_fqn(kuzu_graph) -> None:
    rows = kuzu_graph.find_by_name_or_fqn(
        "com.bank.chat.assign.service.ChatManagementService"
    )
    assert len(rows) == 1
    # Single-module microservice → module and microservice collapse to the same name.
    assert rows[0].module == "chat-assign"
    assert rows[0].microservice == "chat-assign"
    assert rows[0].role == "SERVICE"


# ---------------- find_implementors / find_subclasses ----------------


def test_find_implementors_event_processor(kuzu_graph) -> None:
    """`EventProcessor` is implemented by all *Processor classes in chat-engine."""
    rows = kuzu_graph.find_implementors("EventProcessor")
    names = _names(rows)
    # We assert the *existence* of multiple impls and a couple of
    # canonical ones, not the exact set — the fixture may grow.
    assert len(rows) >= 5, names
    # These two are stable, simple cases.
    for required in ("ClientMessageProcessor", "FallbackEventProcessor"):
        assert required in names, names
    # All implementors must live in the chat-engine module of the chat-core microservice.
    assert _modules(rows) == {"chat-engine"}
    assert _microservices(rows) == {"chat-core"}


def test_find_subclasses_via_jpa_repository_phantom(kuzu_graph) -> None:
    """Spring Data repositories EXTEND `JpaRepository` (a phantom).

    We exercise `find_subclasses` against the phantom to prove the helper
    works even when the parent is an external/unresolved type.
    """
    rows = kuzu_graph.find_subclasses("JpaRepository")
    names = _names(rows)
    # chat-assign/repo defines five JpaRepository subinterfaces; we only
    # require >=2 to stay robust to fixture changes.
    assert len(rows) >= 2, names


# ---------------- find_injectors ----------------


def test_find_injectors_for_repository(kuzu_graph) -> None:
    """`AssignChatRepository` is injected via constructor into the service layer."""
    edges = kuzu_graph.find_injectors("AssignChatRepository")
    assert len(edges) >= 1, edges
    consumers = {e.src.name for e in edges}
    assert "ChatManagementService" in consumers, consumers
    # Every edge to AssignChatRepository must point at the right type.
    for e in edges:
        assert e.dst.name == "AssignChatRepository"
        assert e.mechanism in {"constructor", "field", "setter", "lombok_required_args"}


def test_find_injectors_module_filter(kuzu_graph) -> None:
    edges_in_assign = kuzu_graph.find_injectors(
        "AssignChatRepository", module="chat-assign"
    )
    edges_in_other = kuzu_graph.find_injectors(
        "AssignChatRepository", module="chat-engine"
    )
    assert edges_in_assign, edges_in_assign
    assert edges_in_other == []


def test_find_injectors_microservice_filter(kuzu_graph) -> None:
    """Microservice scoping must isolate chat-assign from chat-core."""
    edges_in_assign = kuzu_graph.find_injectors(
        "AssignChatRepository", microservice="chat-assign"
    )
    edges_in_core = kuzu_graph.find_injectors(
        "AssignChatRepository", microservice="chat-core"
    )
    assert edges_in_assign, edges_in_assign
    assert edges_in_core == []


# ---------------- list_by_role / list_by_annotation ----------------


def test_list_by_role_controller(kuzu_graph) -> None:
    controllers = kuzu_graph.list_by_role("CONTROLLER")
    names = _names(controllers)
    # Both microservices contribute controllers; we only require >=2 to
    # stay loose, plus check a representative one is present.
    assert len(controllers) >= 2, names
    assert any(n.endswith("Controller") for n in names), names


def test_list_by_role_repository_is_empty_or_phantoms_only(kuzu_graph) -> None:
    """Spring Data repositories in the corpus aren't @Repository-annotated.

    This pins behaviour the README documents: role inference is
    annotation-driven. If you ever change it to also tag interfaces that
    extend Repository / JpaRepository, expect to update this test.
    """
    rows = kuzu_graph.list_by_role("REPOSITORY")
    # Assert the helper *runs*; permit 0 results because the fixture has
    # no @Repository annotation, and that's the documented contract.
    assert isinstance(rows, list)


def test_list_by_annotation_transactional(kuzu_graph) -> None:
    """`@Transactional` is on methods inside ChatManagementService etc.

    The graph stores annotations on the type *and* on each method, so we
    expect to find at least one symbol carrying the annotation.
    """
    rows = kuzu_graph.list_by_annotation("Transactional")
    assert len(rows) >= 1, rows


# ---------------- neighbors / impact_analysis ----------------


def test_neighbors_walks_inject_chain(kuzu_graph) -> None:
    """`ChatManagementService` injects 6+ collaborators (constructor params)."""
    rows = kuzu_graph.neighbors(
        "ChatManagementService",
        depth=1,
        edge_types=["INJECTS"],
        direction="out",
    )
    assert len(rows) >= 3, _names(rows)


def test_neighbors_direction_in_for_repository(kuzu_graph) -> None:
    """Reverse direction: who points *at* AssignChatRepository?"""
    rows = kuzu_graph.neighbors(
        "AssignChatRepository",
        depth=1,
        edge_types=["INJECTS"],
        direction="in",
    )
    names = _names(rows)
    assert "ChatManagementService" in names, names


def test_impact_analysis_finds_consumers(kuzu_graph) -> None:
    """`AssignChatRepository` consumers should appear in impact_analysis (depth=2)."""
    rows = kuzu_graph.impact_analysis("AssignChatRepository", depth=2)
    assert "ChatManagementService" in _names(rows)


# ---------------- expand_fqns / trace_flow ----------------


def test_expand_fqns_returns_neighbor_fqns(kuzu_graph) -> None:
    fqns = kuzu_graph.expand_fqns(
        ["com.bank.chat.assign.service.ChatManagementService"],
        depth=1,
    )
    assert any(f.endswith("AssignChatRepository") for f in fqns), fqns


def test_trace_flow_from_controller_seed(kuzu_graph) -> None:
    """A CONTROLLER-stage seed must produce subsequent SERVICE / integration stages."""
    seeds = ["com.bank.chat.assign.web.ChatManagementController"]
    stages = kuzu_graph.trace_flow(seeds, depth=2, stage_limit=20)
    assert stages, "trace_flow returned no stages for a known controller seed"
    stage0 = stages[0]
    assert any(s.symbol.role == "CONTROLLER" for s in stage0)
    # `via=[]` is the documented invariant for the seed stage.
    assert all(s.via == [] for s in stage0)
    # Stage 1+ should *only* contain non-seed symbols, each carrying at
    # least one ViaEdge labelled with a known relation.
    for later in stages[1:]:
        for entry in later:
            assert entry.via, entry
            for v in entry.via:
                assert v.edge_type in {"INJECTS", "EXTENDS", "IMPLEMENTS", "CALLS"}


def _type_part_fqn(method_fqn: str) -> str:
    return method_fqn.split("#", 1)[0]


def test_trace_flow_follow_calls_false_type_only_edges(kuzu_graph) -> None:
    seeds = ["com.bank.chat.assign.web.ChatManagementController"]
    stages = kuzu_graph.trace_flow(seeds, depth=2, stage_limit=20, follow_calls=False)
    assert stages
    for later in stages[1:]:
        for entry in later:
            for v in entry.via:
                assert v.edge_type in {"INJECTS", "EXTENDS", "IMPLEMENTS"}


def test_trace_flow_structural_edges_not_starved_by_calls(kuzu_graph) -> None:
    """Structural-first budget contract: per hop INJECTS/EXTENDS/IMPLEMENTS fill
    `stage_limit` first, and the CALLS branch only tops up the remaining slots.

    Even with a tight cap and `follow_calls=True` (which adds a wide DECLARES+CALLS
    fan-out underneath the controller), at least one stage-1 entry must carry a
    structural via-edge — i.e. CALLS does not squeeze INJECTS out of the bucket.
    """
    seeds = ["com.bank.chat.assign.web.ChatManagementController"]
    stages = kuzu_graph.trace_flow(seeds, depth=2, stage_limit=4, follow_calls=True)
    assert len(stages) >= 2, stages
    stage1 = stages[1]
    assert stage1, "stage-1 should be non-empty for a known controller seed"
    assert len(stage1) <= 4, [s.symbol.fqn for s in stage1]
    structural_edges = {"INJECTS", "EXTENDS", "IMPLEMENTS"}
    has_structural = any(
        any(v.edge_type in structural_edges for v in entry.via)
        for entry in stage1
    )
    assert has_structural, [
        (e.symbol.fqn, [v.edge_type for v in e.via]) for e in stage1
    ]


def test_find_callers_no_phantom_chained_strategy(kuzu_graph) -> None:
    edges = kuzu_graph.find_callers("save", depth=1, limit=100)
    for e in edges:
        assert e.strategy not in ("phantom", "chained_receiver")


def test_find_callers_assign_method(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    edges = kuzu_graph.find_callers(needle, depth=1, limit=50)
    caller_types = {_type_part_fqn(e.src.fqn) for e in edges}
    assert "com.bank.chat.assign.web.ChatManagementController" in caller_types, caller_types


def test_find_callees_assign_method(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    edges = kuzu_graph.find_callees(needle, depth=1, limit=80)
    callee_names = {e.dst.name for e in edges}
    assert "save" in callee_names or "findByConversationId" in callee_names or "resolveSplitName" in callee_names, (
        callee_names
    )


def test_find_callers_type_form_via_declares(kuzu_graph) -> None:
    edges = kuzu_graph.find_callers("com.bank.chat.assign.repo.AssignChatRepository", depth=1, limit=100)
    assert edges, "expected at least one caller of a repository method"
    assert any("ChatManagement" in e.src.fqn for e in edges), [e.src.fqn for e in edges]


def test_min_confidence_filter_drops_edges(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    all_e = kuzu_graph.find_callees(needle, depth=2, limit=200, min_confidence=0.0)
    hi = kuzu_graph.find_callees(needle, depth=2, limit=200, min_confidence=0.99)
    assert len(all_e) >= len(hi)


def test_exclude_external_filters_known_prefix(kuzu_graph) -> None:
    needle = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
    with_ext = kuzu_graph.find_callees(needle, depth=3, limit=300, exclude_external=False)
    no_ext = kuzu_graph.find_callees(needle, depth=3, limit=300, exclude_external=True)
    assert len(with_ext) >= len(no_ext)
    assert not any(_is_external_fqn(e.dst.fqn) for e in no_ext)


def test_expand_methods_from_service_seed(kuzu_graph) -> None:
    extra = kuzu_graph.expand_methods(
        ["com.bank.chat.assign.service.ChatManagementService"],
        depth=1,
        limit=50,
    )
    assert isinstance(extra, list)
    assert all(isinstance(t, tuple) and len(t) == 2 for t in extra), extra
    for fqn, conf in extra:
        assert isinstance(fqn, str) and fqn
        assert 0.0 <= conf <= 1.0
    if extra:
        # CALLS edges carry positive confidence when the graph has callees from this seed.
        assert any(conf > 0.0 for _, conf in extra), extra


def test_expand_methods_default_excludes_external_prefixes(kuzu_graph) -> None:
    extra = kuzu_graph.expand_methods(
        ["com.bank.chat.assign.service.ChatManagementService"],
        depth=2,
        limit=200,
    )
    assert not any(_is_external_fqn(t[0]) for t in extra), extra


def test_expand_methods_exclude_external_false_can_include_more(kuzu_graph) -> None:
    seed = ["com.bank.chat.assign.service.ChatManagementService"]
    with_ext = kuzu_graph.expand_methods(seed, depth=2, limit=300, exclude_external=False)
    no_ext = kuzu_graph.expand_methods(seed, depth=2, limit=300, exclude_external=True)
    assert len(with_ext) >= len(no_ext)


def test_trace_flow_empty_seeds_returns_empty(kuzu_graph) -> None:
    assert kuzu_graph.trace_flow([], depth=1) == []


def _open_stale_ontology_graph(tmp_path: Path, ontology_version: int) -> Path:
    db_path = tmp_path / f"stale_ontology_{ontology_version}.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    conn.execute(
        "CREATE NODE TABLE GraphMeta("
        "key STRING PRIMARY KEY, "
        "ontology_version INT64, built_at INT64, source_root STRING, "
        "counts_json STRING, parse_errors INT64)"
    )
    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: 0, "
        "source_root: '', counts_json: '{}', parse_errors: 0})",
        {"k": "graph", "ov": ontology_version},
    )
    del conn, db
    return db_path


def test_kuzu_graph_refuses_ontology_version_below_required(tmp_path: Path) -> None:
    """v13 graphs refuse to open when ``ONTOLOGY_VERSION`` is current (e.g. 15).

    Overlaps ``test_kuzu_graph_get_raises_when_graph_ontology_too_old`` when
    ``ONTOLOGY_VERSION - 1 == 13``; kept as an explicit v13 regression anchor.
    """
    assert ONTOLOGY_VERSION >= 14
    db_path = _open_stale_ontology_graph(tmp_path, 13)

    prev_inst = KuzuGraph._instance
    prev_path = KuzuGraph._instance_path
    try:
        KuzuGraph._instance = None
        KuzuGraph._instance_path = None
        ver = ONTOLOGY_VERSION
        with pytest.raises(
            RuntimeError,
            match=rf"(?i)ontology.*{ver}|required version {ver}",
        ):
            KuzuGraph.get(str(db_path))
    finally:
        KuzuGraph._instance = prev_inst
        KuzuGraph._instance_path = prev_path


def test_kuzu_graph_get_raises_when_graph_ontology_too_old(tmp_path: Path) -> None:
    """N4 / proposal §5.3: stale graphs must fail loudly on open."""
    stale = max(0, ONTOLOGY_VERSION - 1)
    db_path = _open_stale_ontology_graph(tmp_path, stale)

    prev_inst = KuzuGraph._instance
    prev_path = KuzuGraph._instance_path
    try:
        KuzuGraph._instance = None
        KuzuGraph._instance_path = None
        with pytest.raises(RuntimeError, match="(?i)ontology"):
            KuzuGraph.get(str(db_path))
    finally:
        KuzuGraph._instance = prev_inst
        KuzuGraph._instance_path = prev_path


def test_list_routes_filter_by_framework(kuzu_graph_route_extraction_smoke) -> None:
    g = kuzu_graph_route_extraction_smoke
    feign = g.list_routes(framework="feign", limit=200)
    assert feign == []
    mvc = g.list_routes(framework="spring_mvc", limit=50)
    assert mvc
    assert all(r["framework"] == "spring_mvc" for r in mvc)


def test_find_route_handlers_endpoint_route(kuzu_graph_route_extraction_smoke) -> None:
    g = kuzu_graph_route_extraction_smoke
    rows = g.list_routes(
        framework="spring_mvc",
        microservice="service-a",
        path_prefix="/api/users",
        method="GET",
        limit=10,
    )
    assert rows, "expected service-a MVC route"
    rid = rows[0]["id"]
    handlers = g.find_route_handlers(route_id=rid)
    assert len(handlers) == 1
    fqns = {h["symbol"]["fqn"] for h in handlers}
    assert len(fqns) == 1


def test_find_route_handlers_feign_route_returns_empty(kuzu_graph_route_extraction_smoke) -> None:
    g = kuzu_graph_route_extraction_smoke
    rows = g.list_routes(framework="feign", path_prefix="/dupbase/same", limit=10)
    assert rows == []


def test_get_route_by_path_microservice_isolated(kuzu_graph_route_extraction_smoke) -> None:
    g = kuzu_graph_route_extraction_smoke
    tpl = "/api/users"
    ra = g.get_route_by_path(microservice="service-a", path_template=tpl, method="GET")
    rb = g.get_route_by_path(microservice="service-b", path_template=tpl, method="GET")
    assert ra is not None and rb is not None
    assert ra["microservice"] == "service-a"
    assert rb["microservice"] == "service-b"
    assert ra["id"] != rb["id"]


def test_find_route_callers_includes_producer_callers(kuzu_db_path_cross_service_smoke: Path) -> None:
    g = KuzuGraph(str(kuzu_db_path_cross_service_smoke))
    topic_routes = [r for r in g.list_routes(limit=100) if str(r.get("topic") or "")]
    callers: list = []
    for route in topic_routes:
        callers = g.find_route_callers(route["id"])
        if any(c.caller_node_kind == "producer" for c in callers):
            break
    assert any(c.caller_node_kind == "producer" for c in callers)


def test_find_route_callers_returns_route_caller_client_node(kuzu_db_path_cross_service_smoke: Path) -> None:
    from kuzu_queries import RouteCaller

    g = KuzuGraph(str(kuzu_db_path_cross_service_smoke))
    routes = g.list_routes(limit=50)
    callers: list[RouteCaller] = []
    for route in routes:
        callers = g.find_route_callers(route["id"])
        if callers:
            break
    assert callers
    http_callers = [c for c in callers if c.match]
    assert any(c.caller_node_kind == "client" for c in http_callers)
    assert all(c.caller_node_id for c in http_callers)


def test_trace_request_flow_inbound_includes_caller_node_id(kuzu_db_path_cross_service_smoke: Path) -> None:
    g = KuzuGraph(str(kuzu_db_path_cross_service_smoke))
    route_id = None
    for route in g.list_routes(limit=50):
        flow = g.trace_request_flow(route["id"], max_hops=2)
        inbound = flow.get("inbound") or []
        if inbound:
            route_id = route["id"]
            break
    assert route_id is not None
    flow = g.trace_request_flow(route_id, max_hops=2)
    inbound = flow.get("inbound") or []
    assert inbound
    assert any(row.get("caller_node_id") for row in inbound)
