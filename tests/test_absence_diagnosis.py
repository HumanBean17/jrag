"""Tests for absence_diagnosis.py — the stateless diagnose() classifier (PR-ABS-2).

Unit matrix (cause × verdict) per the task brief. Mirrors test_absence_vocab.py's
style: build a real VocabularyIndex from the session ladybug_graph fixture and
call diagnose(...) with synthetic inputs (graph-backed only where did-you-mean /
external / vocabulary_context need real data).

Conservative-absence guard (false-absent): a symbol that exists must NEVER yield
``not_in_project``. Two tests pin this: the middle-band case and the explicit
exact-existing-symbol case.
"""

from __future__ import annotations

from typing import Any

import pytest

# These imports will fail until absence_diagnosis.py is created (RED).
from absence_diagnosis import _neighbors_meaningful_empty, diagnose
from absence_types import AbsenceDiagnosis
from absence_vocab import VocabularyIndex
from graph_types import NodeRef


# ---- helpers -----------------------------------------------------------------


class _StubConfig:
    """Minimal config stand-in exposing only the absence knobs diagnose reads."""

    absence_close_threshold: float = 0.85
    absence_absent_floor: float = 0.40
    absence_candidate_count: int = 5
    absence_ngram_q: int = 3
    absence_diag_enabled: bool = True


@pytest.fixture(scope="session")
def vocab(ladybug_graph) -> VocabularyIndex:
    """A VocabularyIndex built from the session bank-chat graph."""
    return VocabularyIndex.build(ladybug_graph, q=3)


@pytest.fixture(scope="session")
def graph(ladybug_graph):
    return ladybug_graph


@pytest.fixture
def cfg() -> _StubConfig:
    return _StubConfig()


def _diagnose(**overrides: Any) -> AbsenceDiagnosis | None:
    """Call diagnose with defaults; override per-call kwargs."""
    base = dict(
        tool="search",
        query=None,
        filt=None,
        filter_kind=None,
        root_node=None,
        scope={},
        vocab=None,
        graph=None,
        cfg=None,
    )
    base.update(overrides)
    return diagnose(**base)


# ---- identifier path: did-you-mean + thresholds ------------------------------


class TestIdentifierDidYouMean:
    """search/resolve: identifier-shaped query → did-you-mean + threshold verdict."""

    def test_typo_close_hit_refine_query(self, graph, vocab, cfg):
        """A known typo (ChatManagementServic) near ChatManagementService → refine_query.

        closest_symbols non-empty, ChatManagementService present, best distance small.
        """
        diag = _diagnose(
            tool="search",
            query="ChatManagementServic",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"
        assert diag.cause == "identifier_miss"
        assert len(diag.closest_symbols) > 0
        names = {s.name for s in diag.closest_symbols}
        assert "ChatManagementService" in names
        # best distance small (similarity was ~0.97 → distance <= 0.10)
        assert diag.distances
        assert min(diag.distances) <= 0.10

    def test_nothing_close_yields_not_in_project(self, graph, vocab, cfg):
        """A name with no close match (foobarbaznope) → not_in_project with proof.

        closest_symbols still returned (nearest-by-name); best distance >= absent_floor.
        """
        diag = _diagnose(
            tool="search",
            query="foobarbaznope",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "not_in_project"
        assert diag.proof is not None
        assert diag.proof.query_shape == "identifier"
        assert diag.proof.symbol_count_scanned == vocab.symbol_count
        assert diag.proof.nearest_distance >= cfg.absence_absent_floor
        # closest_symbols still populated even on not_in_project
        assert len(diag.closest_symbols) > 0
        assert diag.distances
        assert min(diag.distances) >= cfg.absence_absent_floor

    def test_middle_band_refine_query_not_not_in_project(self, graph, vocab, cfg):
        """A plausible-but-absent identifier (ChatManagement, sim ~0.80) → refine_query.

        This is the middle band: between absent_floor and close_threshold. Must NOT
        be ``not_in_project`` — the conservative false-absent guard.
        """
        diag = _diagnose(
            tool="search",
            query="ChatManagement",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"
        assert diag.verdict != "not_in_project"
        assert diag.closest_symbols  # populated regardless


# ---- false-absent guard (explicit) -------------------------------------------


class TestFalseAbsentGuard:
    """A symbol that EXISTS must never yield not_in_project when queried exactly."""

    def test_existing_symbol_exact_never_not_in_project(self, graph, vocab, cfg):
        """Querying an existing symbol (ChatManagementService) exactly → refine_query.

        The catastrophic failure mode is declaring a real symbol absent. Pin that
        an exact existing name never reaches not_in_project.
        """
        diag = _diagnose(
            tool="search",
            query="ChatManagementService",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict != "not_in_project"
        assert diag.verdict == "refine_query"
        # exact match → distance 0
        assert diag.distances
        assert min(diag.distances) == pytest.approx(0.0, abs=1e-6)

    def test_existing_symbol_under_floor_threshold_still_safe(self, graph, vocab, cfg):
        """Even with an absurdly high absent_floor, an existing symbol stays safe.

        Pins that the not_in_project branch is gated on identifier-shape AND best
        similarity, and that an exact existing hit (similarity 1.0) clears any
        plausible floor.
        """
        cfg.absence_absent_floor = 0.95  # deliberately harsh
        diag = _diagnose(
            tool="search",
            query="ChatManagementService",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"

    def test_existing_symbol_by_fqn_never_not_in_project(self, graph, vocab, cfg):
        """Querying an existing symbol by its full FQN → refine_query.

        Guards the FQN false-absent gap: ``vocab.lookup`` matches on simple_name,
        so an FQN query's similarity to the simple-name-only normalized_name could
        in principle dip below the floor for a deeply-nested name. The exact-match
        guard must force refine_query regardless.
        """
        # Find a real resolved symbol and query its full FQN.
        rec = next(
            r for r in vocab.records
            if r.resolved and r.fqn.count(".") >= 3 and len(r.fqn) > 30
        )
        diag = _diagnose(
            tool="search",
            query=rec.fqn,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict != "not_in_project"


# ---- empty vocab guard --------------------------------------------------------


class TestEmptyVocabGuard:
    """Empty/unindexed vocab → refine_query (never not_in_project)."""

    def test_empty_vocab_identifier_query_yields_refine_query(self, cfg):
        """Empty vocab (symbol_count == 0) + identifier query → refine_query.

        The spec mandates refine_query for empty/unindexed projects ("Never a false
        not_in_project"). An empty vocab yields best_sim=0.0, which without this guard
        would incorrectly emit not_in_project with symbol_count_scanned=0.
        """
        empty_vocab = VocabularyIndex(records=[], ngram_index={}, q=3)
        diag = _diagnose(
            tool="search",
            query="SomeIdentifier",
            vocab=empty_vocab,
            graph=None,  # Not used when vocab is empty
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"
        assert diag.verdict != "not_in_project"
        assert diag.cause == "identifier_miss"
        # Message should mention empty/unindexed index
        assert "empty" in diag.message.lower() or "unindexed" in diag.message.lower()

    def test_empty_vocab_find_identifier_filter_yields_refine_query(self, cfg):
        """Empty vocab + find with identifier filter → refine_query."""
        empty_vocab = VocabularyIndex(records=[], ngram_index={}, q=3)
        diag = _diagnose(
            tool="find",
            filt={"fqn_contains": "SomeClass"},
            filter_kind="symbol",
            vocab=empty_vocab,
            graph=None,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"
        assert diag.verdict != "not_in_project"
        assert diag.cause == "identifier_miss"


# ---- NL path -----------------------------------------------------------------


class TestNlMiss:
    """search: natural-language query → nl_miss with vocabulary_context."""

    def test_nl_query_yields_nl_miss_with_context(self, graph, vocab, cfg):
        diag = _diagnose(
            tool="search",
            query="how does chat routing work",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.cause == "nl_miss"
        assert diag.verdict == "refine_query"
        assert diag.vocabulary_context is not None
        # top modules / microservices populated from the corpus
        assert diag.vocabulary_context.top_modules
        assert diag.vocabulary_context.top_microservices
        # NO did-you-mean for NL
        assert diag.closest_symbols == []


# ---- find (filter) path ------------------------------------------------------


class TestFindFilterMiss:
    """find: identifier-shaped and broad filters → filter_miss (+ relaxation)."""

    def test_identifier_filter_excluded_by_scope_filter_miss(self, graph, vocab, cfg):
        """find fqn_contains=<existing symbol> excluded by a scope dim → filter_miss.

        The symbol exists (close hit) but the scope filter excludes it, so the
        diagnosis points at the filter, not absence. filter_relaxation.per_dimension
        non-empty (shows where matches live).
        """
        diag = _diagnose(
            tool="find",
            filt={"fqn_contains": "ChatManagementService"},
            filter_kind="symbol",
            scope={"microservice": "nonexistent-svc"},
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.cause == "filter_miss"
        assert diag.verdict == "refine_query"
        assert diag.filter_relaxation is not None
        assert diag.filter_relaxation.per_dimension
        # the relaxed dim should be the constrained one
        dims = {d.dimension for d in diag.filter_relaxation.per_dimension}
        assert "microservice" in dims

    def test_broad_filter_absent_role_filter_miss(self, graph, vocab, cfg):
        """find role=REPOSITORY (no such role in corpus) → filter_miss."""
        diag = _diagnose(
            tool="find",
            filt={"role": "REPOSITORY"},
            filter_kind="symbol",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.cause == "filter_miss"
        assert diag.filter_relaxation is not None
        assert diag.filter_relaxation.per_dimension
        dims = {d.dimension for d in diag.filter_relaxation.per_dimension}
        assert "role" in dims


# ---- external-wins -----------------------------------------------------------


class TestExternalDependency:
    """external/phantom targets → external_dependency (external-wins precedence)."""

    def test_external_prefix_target(self, graph, vocab, cfg):
        diag = _diagnose(
            tool="search",
            query="java.util.List",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "external_dependency"
        assert diag.cause == "external"
        assert diag.external_identity is not None
        assert diag.external_identity.reason == "prefix"

    def test_phantom_target_in_corpus(self, graph, vocab, cfg):
        diag = _diagnose(
            tool="search",
            query="RestTemplate",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "external_dependency"
        assert diag.external_identity is not None
        assert diag.external_identity.reason == "phantom"

    def test_unresolved_call_site_root_node_external_dependency(self, graph, vocab, cfg):
        """unresolved_call_site root_node → external_dependency, reason=unresolved-call."""
        ucs = _find_unresolved_call_site(graph)
        if not ucs:
            pytest.skip("corpus has no unresolved_call_site node")
        diag = _diagnose(
            tool="neighbors",
            root_node=ucs,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "external_dependency"
        assert diag.cause == "external"
        assert diag.external_identity is not None
        assert diag.external_identity.reason == "unresolved-call"


# ---- neighbors path ----------------------------------------------------------


def _find_http_route_with_handlers(graph) -> str | None:
    """Find a real http_endpoint Route id that has inbound handlers."""
    rows = graph._rows(  # noqa: SLF001 - test helper, same pattern as conftest
        "MATCH (r:Route) WHERE r.kind = 'http_endpoint' RETURN r.id AS id LIMIT 10"
    )
    for row in rows:
        rid = str(row.get("id") or "")
        if rid and graph.find_route_handlers(route_id=rid):
            return rid
    return None


def _find_symbol_with_edges(graph) -> NodeRef:
    """Find a real Symbol node that has at least one edge."""
    rows = graph._rows(  # noqa: SLF001
        "MATCH (s:Symbol)--() RETURN s.id AS id, s.name AS name, s.fqn AS fqn, "
        "s.kind AS kind LIMIT 1"
    )
    assert rows, "corpus has no symbol with edges"
    row = rows[0]
    return NodeRef(
        id=str(row.get("id") or ""),
        kind="symbol",
        fqn=str(row.get("fqn") or ""),
        name=str(row.get("name") or "") or None,
        symbol_kind=str(row.get("kind") or "") or None,
    )


def _find_kafka_topic_route(graph) -> str | None:
    """Find a real kafka_topic Route id (NOT http_endpoint)."""
    rows = graph._rows(  # noqa: SLF001 - test helper, same pattern as conftest
        "MATCH (r:Route) WHERE r.kind = 'kafka_topic' RETURN r.id AS id LIMIT 10"
    )
    for row in rows:
        rid = str(row.get("id") or "")
        if rid:
            return rid
    return None


def _find_unresolved_call_site(graph) -> NodeRef | None:
    """Find a real UnresolvedCallSite node."""
    rows = graph._rows(  # noqa: SLF001
        "MATCH (ucs:UnresolvedCallSite) RETURN ucs.id AS id, ucs.callee_simple AS callee LIMIT 1"
    )
    if not rows:
        return None
    row = rows[0]
    return NodeRef(
        id=str(row.get("id") or ""),
        kind="unresolved_call_site",
        fqn=str(row.get("callee") or ""),  # Use callee_simple as fqn
        name=str(row.get("callee") or "") or None,
        symbol_kind=None,
    )


class TestNeighbors:
    """neighbors: leaf/entrypoint → correct_empty; wrong edge type → refine_query."""

    def test_route_entrypoint_correct_empty(self, graph, vocab, cfg):
        rid = _find_http_route_with_handlers(graph)
        assert rid, "corpus has no http route with handlers"
        root = NodeRef(id=rid, kind="route", fqn="GET /x")
        diag = _diagnose(
            tool="neighbors",
            root_node=root,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "correct_empty"
        assert diag.cause == "meaningful_empty"

    def test_symbol_with_edges_refine_query(self, graph, vocab, cfg):
        root = _find_symbol_with_edges(graph)
        diag = _diagnose(
            tool="neighbors",
            root_node=root,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"

    def test_kafka_topic_route_refine_query_not_correct_empty(self, graph, vocab, cfg):
        """kafka_topic route with zero neighbors → refine_query (not correct_empty)."""
        rid = _find_kafka_topic_route(graph)
        if not rid:
            pytest.skip("corpus has no kafka_topic route")
        root = NodeRef(id=rid, kind="route", fqn="kafka:topic")
        diag = _diagnose(
            tool="neighbors",
            root_node=root,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        # kafka_topic routes are NOT external entrypoints → refine_query, not correct_empty
        assert diag.verdict == "refine_query"
        assert diag.cause == "identifier_miss"


class TestNeighborsMeaningfulEmptyPredicate:
    """Direct tests of _neighbors_meaningful_empty to harden route classification."""

    def test_zero_edge_kafka_topic_route_returns_false(self):
        """A kafka_topic route with zero edges must return False (not meaningful empty).

        This test isolates the route-classification logic from fixture edge counts.
        Before the indentation fix, this test would fail because the kafka route
        would fall through to the edge-count check and incorrectly return True.
        """
        # Stub graph that returns kafka_topic kind and zero edge count
        class StubGraph:
            def _rows(self, cypher, params):
                if "k" in cypher:  # route-kind query
                    return [{"k": "kafka_topic"}]
                if "count" in cypher:  # edge-count query
                    return [{"c": 0}]
                return []

            def find_route_handlers(self, route_id):
                return []  # no handlers

        graph = StubGraph()
        root_node = NodeRef(id="kafka-route-1", kind="route", fqn="kafka:topic")

        # kafka_topic routes are NOT meaningful empty
        result = _neighbors_meaningful_empty(root_node, graph)
        assert result is False, "kafka_topic route must return False (refine_query)"

    def test_http_endpoint_with_handlers_returns_true(self):
        """An http_endpoint route with handlers must return True (meaningful empty)."""
        class StubGraph:
            def _rows(self, cypher, params):
                if "k" in cypher:
                    return [{"k": "http_endpoint"}]
                return []

            def find_route_handlers(self, route_id):
                # Return list of dicts as the real implementation does
                return [{"symbol": {"fqn": "Handler"}}]

        graph = StubGraph()
        root_node = NodeRef(id="http-route-1", kind="route", fqn="GET /api")

        # http_endpoint with handlers IS meaningful empty
        result = _neighbors_meaningful_empty(root_node, graph)
        assert result is True, "http_endpoint with handlers must return True (correct_empty)"

    def test_http_endpoint_without_handlers_returns_false(self):
        """An http_endpoint route without handlers must return False (not meaningful empty)."""
        class StubGraph:
            def _rows(self, cypher, params):
                if "k" in cypher:
                    return [{"k": "http_endpoint"}]
                return []

            def find_route_handlers(self, route_id):
                return []  # no handlers

        graph = StubGraph()
        root_node = NodeRef(id="http-route-2", kind="route", fqn="GET /api")

        # http_endpoint without handlers is NOT meaningful empty
        result = _neighbors_meaningful_empty(root_node, graph)
        assert result is False, "http_endpoint without handlers must return False (refine_query)"


# ---- describe path -----------------------------------------------------------


class TestDescribe:
    """describe: by node_id → refine_query with no did-you-mean."""

    def test_describe_by_node_id_refine_query_no_did_you_mean(self, graph, vocab, cfg):
        diag = _diagnose(
            tool="describe",
            query="sym:deadbeefNoSuchNodeId",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is not None
        assert diag.verdict == "refine_query"
        # node_id miss → no did-you-mean
        assert diag.closest_symbols == []


# ---- master toggle + exception guard -----------------------------------------


class TestRobustness:
    """Master toggle and exception guard: diagnosis never fails the tool."""

    def test_master_toggle_off_returns_none(self, graph, vocab):
        cfg = _StubConfig()
        cfg.absence_diag_enabled = False
        diag = _diagnose(
            tool="search",
            query="ChatManagementService",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        assert diag is None

    def test_exception_guard_returns_refine_query(self, graph, vocab, cfg, monkeypatch):
        """If vocab.lookup raises, diagnose returns a refine_query (no escape)."""
        monkeypatch.setattr(vocab, "lookup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        diag = _diagnose(
            tool="search",
            query="ChatManagementServic",
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
        # Must not raise; returns a minimal refine_query (or None).
        assert diag is None or diag.verdict == "refine_query"
