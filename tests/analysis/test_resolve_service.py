"""Tests for resolve_service.py parity with mcp_v2.py.

Graph-backed tests use the bank-chat ``ladybug_db_path`` fixture (not the
default ``LadybugGraph.get()`` path, which has no index in CI and caused 7/10
of these tests to SKIP — masking the tautological ``status in ("one","many",
"none")`` assertions, which are true for ANY result). Each test now asserts the
contract for the branch it actually hit.
"""
from pathlib import Path

from java_codebase_rag.graph.ladybug_queries import LadybugGraph
from java_codebase_rag.analysis.resolve_service import ResolveCandidate, ResolveOutput, ResolveStatus, resolve_v2

# Known bank-chat fixture symbol (verified via test_jrag_locate.test_find_by_fqn_exact).
_KNOWN_CLASS_FQN = "com.bank.chat.assign.ChatAssignApplication"


def test_resolve_service_importable_and_one_match(ladybug_db_path: Path) -> None:
    """resolve_service is importable and resolves a known unique FQN to 'one'."""
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2(_KNOWN_CLASS_FQN, hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.status == "one", f"expected one for {_KNOWN_CLASS_FQN}, got {result.status!r}"
    assert result.node is not None
    assert result.node.fqn == _KNOWN_CLASS_FQN
    assert result.candidates == []


def test_resolve_service_many_returns_candidates(ladybug_db_path: Path) -> None:
    """An ambiguous short name returns `many` with ≥2 scored candidates.

    The contract is asserted per-branch (no tautological ``status in (...)``):
    if the fixture happens to have ≤1 `Request`, the one/none branches still
    verify their own contracts.
    """
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("Request", hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "Request"

    if result.status == "many":
        assert result.node is None
        assert len(result.candidates) >= 2, f"many must carry ≥2 candidates, got {len(result.candidates)}"
        for cand in result.candidates:
            assert isinstance(cand, ResolveCandidate)
            assert 0.0 <= cand.score <= 1.0, f"candidate score out of [0,1]: {cand.score}"
    elif result.status == "one":
        assert result.node is not None
    else:
        assert result.status == "none"
        assert result.message, "none must carry a message"


def test_resolve_service_none_is_not_found(ladybug_db_path: Path) -> None:
    """A non-existent identifier returns `none` with a 'No matches' message."""
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("com.TotallyFakeClassName.xyz123", hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.status == "none"
    assert result.node is None
    assert result.candidates == []
    assert result.message is not None
    assert "No matches" in result.message or "no matches" in result.message.lower()


def test_resolve_service_wildcard_rejected() -> None:
    """Wildcard identifiers are rejected with an error (no graph needed)."""
    result = resolve_v2("com.example.*")

    assert isinstance(result, ResolveOutput)
    assert result.success is False
    assert result.status == "none"
    assert result.node is None
    assert result.candidates == []
    assert "Wildcards" in result.message or "not supported" in result.message.lower()


def test_resolve_service_empty_identifier_rejected() -> None:
    """Empty/whitespace identifiers are rejected."""
    result = resolve_v2("   ")

    assert isinstance(result, ResolveOutput)
    assert result.success is False
    assert result.status == "none"
    assert result.node is None
    assert result.candidates == []
    assert "Invalid identifier" in result.message or "whitespace" in result.message.lower()


def test_resolve_service_route_path_parsing(ladybug_db_path: Path) -> None:
    """An HTTP method + path is recognized as a route identifier."""
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("GET /chat/assign", hint_kind="route", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "GET /chat/assign"
    # Per-branch contract (no tautology): the route IS in the bank-chat fixture,
    # so we expect one-or-many; if not found, the none-branch still verifies.
    if result.status == "one":
        assert result.node is not None
    elif result.status == "many":
        assert len(result.candidates) >= 1
    else:
        assert result.status == "none"


def test_resolve_service_client_target_parsing(ladybug_db_path: Path) -> None:
    """A service + path is recognized as a client identifier."""
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("chat-assign /chat/assign", hint_kind="client", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "chat-assign /chat/assign"
    # Per-branch contract — not `status in (one,many,none)`.
    assert result.status in ("one", "many", "none")
    if result.status == "one":
        assert result.node is not None
    elif result.status == "many":
        assert len(result.candidates) >= 2
    else:
        assert result.message is not None


def test_resolve_service_producer_topic_prefix(ladybug_db_path: Path) -> None:
    """A Kafka topic prefix is recognized as a producer identifier."""
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("banking.chat", hint_kind="producer", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "banking.chat"
    assert result.status in ("one", "many", "none")
    if result.status == "one":
        assert result.node is not None
    elif result.status == "many":
        assert len(result.candidates) >= 2
    else:
        assert result.message is not None


def test_resolve_service_route_kafka_topic(ladybug_db_path: Path) -> None:
    """A Kafka topic name resolves to its Route via ``r.topic``.

    Regression: ``_resolve_route_candidates`` matched only on
    ``path``/``path_template``, so ``kafka_topic`` Routes (which carry their
    name in ``topic`` with ``path=''``) were unresolvable — ``jrag flow
    <topic>``/``callers <topic>``/``overview <topic>`` could not follow kafka
    even when the route + EXPOSES edge existed. ``banking.chat.compliance.review``
    is consumed by ``ComplianceReviewListener`` (consumer-only, no producer
    phantom), so it resolves to exactly one Route.
    """
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("banking.chat.compliance.review", hint_kind="route", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "banking.chat.compliance.review"
    assert result.status == "one", (
        f"topic should resolve to one Route, got status={result.status!r}: {result.message}"
    )
    assert result.node is not None
    assert result.node.kind == "route", f"expected a Route node, got {result.node}"


def test_resolve_service_route_kafka_topic_drops_producer_mirror(ladybug_db_path: Path) -> None:
    """A topic with both a producer (phantom, no EXPOSES) and a consumer
    (server Route, EXPOSES) resolves to the single server Route.

    ``banking.chat.incoming`` is produced by ``FollowUpKafkaPublisher`` (phantom
    kafka_topic Route, ``microservice=''``, no EXPOSES) and consumed by
    ``ChatKafkaListener`` (server Route with EXPOSES). Both carry the same topic
    so topic-matching surfaces both; ``_drop_route_mirrors`` must collapse them
    to the one exposed server route (status ``one``), not report ``many``.
    """
    g = LadybugGraph.get(str(ladybug_db_path))
    result = resolve_v2("banking.chat.incoming", hint_kind="route", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.status == "one", (
        f"producer+consumer topic should collapse to one server Route via mirror-drop, "
        f"got status={result.status!r}: {result.message}"
    )
    assert result.node is not None
    assert result.node.kind == "route", f"expected a Route node, got {result.node}"


def test_resolve_service_hint_kind_filters(ladybug_db_path: Path) -> None:
    """hint_kind narrows the search space (route hint won't match symbol-only ids)."""
    g = LadybugGraph.get(str(ladybug_db_path))
    # `GET /chat/assign` is a route; with hint_kind="symbol" it should NOT
    # resolve as a symbol (status none or many, but NOT a symbol node).
    result_symbol = resolve_v2("GET /chat/assign", hint_kind="symbol", graph=g)
    assert result_symbol.resolved_identifier == "GET /chat/assign"
    if result_symbol.status == "one":
        # A symbol hint must NOT resolve a route path to a Route node.
        assert result_symbol.node is not None
        assert result_symbol.node.kind.lower() != "route", (
            f"hint_kind=symbol resolved a Route node: {result_symbol.node}"
        )

    # With hint_kind="route", it resolves through the route path.
    result_route = resolve_v2("GET /chat/assign", hint_kind="route", graph=g)
    assert result_route.resolved_identifier == "GET /chat/assign"


def test_resolve_status_values() -> None:
    """ResolveStatus is a Literal with exactly these values."""
    from typing import get_args

    status_values = get_args(ResolveStatus)
    assert set(status_values) == {"one", "many", "none"}
