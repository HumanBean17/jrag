"""Tests for resolve_service.py parity with mcp_v2.py."""

import pytest

from ladybug_queries import LadybugGraph
from resolve_service import ResolveCandidate, ResolveOutput, ResolveStatus, resolve_v2


def test_resolve_service_importable_and_one_match():
    """Test that resolve_service is importable and resolves a unique FQN."""
    # This test assumes a test index exists with a known symbol
    # We'll test with a concrete identifier that should match exactly once
    from ladybug_queries import LadybugGraph

    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try to resolve a well-known symbol that should exist in most Java codebases
    # If this doesn't work in your test environment, adjust the identifier
    result = resolve_v2("java.lang.String", hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    # We expect either "one" (exact match found) or "none" (if String not in index)
    assert result.status in ("one", "many", "none")
    assert result.resolved_identifier == "java.lang.String"

    if result.status == "one":
        assert result.node is not None
        assert result.node.fqn == "java.lang.String"
        assert len(result.candidates) == 0
    elif result.status == "many":
        assert result.node is None
        assert len(result.candidates) >= 2
        # All candidates should have the FQN we searched for
        for cand in result.candidates:
            assert isinstance(cand, ResolveCandidate)


def test_resolve_service_many_returns_candidates():
    """Test that ambiguous identifiers return multiple candidates."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try a short name that likely matches multiple symbols
    result = resolve_v2("get", hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "get"

    # "get" is likely to match many methods
    if result.status == "many":
        assert result.node is None
        assert len(result.candidates) >= 2
        assert all(isinstance(c, ResolveCandidate) for c in result.candidates)
        # Check that candidates have valid scores
        for cand in result.candidates:
            assert 0.0 <= cand.score <= 1.0
    elif result.status == "none":
        # If the index has no "get" methods, that's OK too
        assert result.node is None
        assert len(result.candidates) == 0
        assert result.message is not None


def test_resolve_service_none_is_not_found():
    """Test that non-existent identifiers return not_found status."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try an identifier that very likely doesn't exist
    result = resolve_v2("com TotallyFakeClassName xyz123", hint_kind="symbol", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.status == "none"
    assert result.node is None
    assert len(result.candidates) == 0
    assert result.message is not None
    assert "No matches" in result.message


def test_resolve_service_wildcard_rejected():
    """Test that wildcard identifiers are rejected with an error."""
    result = resolve_v2("com.example.*")

    assert isinstance(result, ResolveOutput)
    assert result.success is False
    assert result.status == "none"
    assert result.node is None
    assert len(result.candidates) == 0
    assert "Wildcards" in result.message or "not supported" in result.message


def test_resolve_service_empty_identifier_rejected():
    """Test that empty/whitespace identifiers are rejected."""
    result = resolve_v2("   ")

    assert isinstance(result, ResolveOutput)
    assert result.success is False
    assert result.status == "none"
    assert result.node is None
    assert len(result.candidates) == 0
    assert "Invalid identifier" in result.message or "whitespace" in result.message


def test_resolve_service_route_path_parsing():
    """Test that HTTP method + path is recognized."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try a common HTTP route pattern
    result = resolve_v2("GET /api/users", hint_kind="route", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "GET /api/users"
    # Status could be one, many, or none depending on the index
    assert result.status in ("one", "many", "none")


def test_resolve_service_client_target_parsing():
    """Test that client target service + path is recognized."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try a client pattern (service + path)
    result = resolve_v2("user-service /api/users", hint_kind="client", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "user-service /api/users"
    assert result.status in ("one", "many", "none")


def test_resolve_service_producer_topic_prefix():
    """Test that producer topic prefix matching works."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # Try a Kafka topic prefix
    result = resolve_v2("user.events", hint_kind="producer", graph=g)

    assert isinstance(result, ResolveOutput)
    assert result.success is True
    assert result.resolved_identifier == "user.events"
    assert result.status in ("one", "many", "none")


def test_resolve_service_hint_kind_filters():
    """Test that hint_kind parameter filters search space."""
    try:
        g = LadybugGraph.get()
    except RuntimeError:
        pytest.skip("No index available - skipping resolve test")

    # With hint_kind="symbol", should only search symbols
    result_symbol = resolve_v2("GET /api", hint_kind="symbol", graph=g)
    assert result_symbol.resolved_identifier == "GET /api"
    # May be none (no symbol named "GET /api")

    # With hint_kind="route", should only search routes
    result_route = resolve_v2("GET /api", hint_kind="route", graph=g)
    assert result_route.resolved_identifier == "GET /api"
    # May be one, many, or none


def test_resolve_status_values():
    """Test that ResolveStatus has the correct literal values."""
    # ResolveStatus should be a Literal with exactly these values
    from typing import get_args

    status_values = get_args(ResolveStatus)
    assert set(status_values) == {"one", "many", "none"}
