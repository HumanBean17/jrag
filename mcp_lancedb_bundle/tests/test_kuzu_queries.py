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

import pytest


def _names(symbols) -> set[str]:
    return {s.name for s in symbols}


def _services(symbols) -> set[str]:
    return {s.service for s in symbols if s.service}


# ---------------- meta ----------------


def test_meta(kuzu_graph) -> None:
    meta = kuzu_graph.meta()
    assert "error" not in meta, meta
    assert meta["ontology_version"] >= 1
    assert meta["built_at"] > 0
    assert meta["counts"]["types"] > 0
    assert meta["counts"]["injects"] > 0


def test_service_counts_keys(kuzu_graph) -> None:
    counts = kuzu_graph.service_counts()
    assert counts.get("chat-assign", 0) > 0
    # Multi-module reactor names should appear
    assert any(k in counts for k in ("chat-app", "chat-engine", "chat-domain"))


# ---------------- find_by_name_or_fqn ----------------


def test_find_by_name_or_fqn_simple_name(kuzu_graph) -> None:
    rows = kuzu_graph.find_by_name_or_fqn("ChatManagementService")
    assert any(r.kind == "class" and r.fqn.endswith(".ChatManagementService") for r in rows), rows


def test_find_by_name_or_fqn_fqn(kuzu_graph) -> None:
    rows = kuzu_graph.find_by_name_or_fqn(
        "com.bank.chat.assign.service.ChatManagementService"
    )
    assert len(rows) == 1
    assert rows[0].service == "chat-assign"
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
    # All implementors must live in the chat-engine module.
    assert _services(rows) == {"chat-engine"}


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


def test_find_injectors_service_filter(kuzu_graph) -> None:
    edges_in_assign = kuzu_graph.find_injectors(
        "AssignChatRepository", service="chat-assign"
    )
    edges_in_other = kuzu_graph.find_injectors(
        "AssignChatRepository", service="chat-engine"
    )
    assert edges_in_assign, edges_in_assign
    assert edges_in_other == []


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
                assert v.edge_type in {"INJECTS", "EXTENDS", "IMPLEMENTS"}


def test_trace_flow_empty_seeds_returns_empty(kuzu_graph) -> None:
    assert kuzu_graph.trace_flow([], depth=1) == []
