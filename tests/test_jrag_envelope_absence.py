"""Tests for PR-ABS-4: CLI envelope + renderer alignment with absence vocabulary.

These tests verify that:
1. Envelope.absence is serialized correctly (present when set, omitted when None)
2. Renderer maps all 4 verdicts to appropriate text
3. is_external_entrypoint rendering is unchanged (backward compatibility)
4. resolve-none carries absence through to the envelope
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from absence_types import AbsenceDiagnosis, AbsenceProof, ExternalIdentity
from graph_types import NodeRef
from java_codebase_rag.jrag_envelope import Envelope, resolve_query
from java_codebase_rag.jrag_render import render
from resolve_service import ResolveOutput


# ----- Test 1: Envelope.absence serialization -----

def test_envelope_to_dict_omits_absence_when_none() -> None:
    """Envelope with absence=None omits the field from to_dict()."""
    env = Envelope(status="ok", absence=None)
    out = env.to_dict()
    assert "absence" not in out
    assert out == {"status": "ok"}


def test_envelope_to_dict_includes_absence_when_present() -> None:
    """Envelope with absence set includes the serialized diagnosis."""
    diagnosis = AbsenceDiagnosis(
        verdict="not_in_project",
        cause="identifier_miss",
        message="Symbol not found in project",
        closest_symbols=[NodeRef(id="sym:1", kind="symbol", fqn="com.foo.Bar")],
        distances=[0.15],
        proof=AbsenceProof(
            nearest_distance=0.15,
            symbol_count_scanned=1000,
            thresholds_applied={"symbol": 0.3},
            query_shape="identifier",
        ),
    )
    env = Envelope(status="not_found", absence=diagnosis)
    out = env.to_dict()
    assert "absence" in out
    assert out["absence"]["verdict"] == "not_in_project"
    assert out["absence"]["message"] == "Symbol not found in project"
    assert out["absence"]["closest_symbols"][0]["fqn"] == "com.foo.Bar"


def test_envelope_to_json_includes_absence() -> None:
    """Envelope.to_json() includes absence when present."""
    diagnosis = AbsenceDiagnosis(
        verdict="external_dependency",
        cause="external",
        message="External dependency",
        external_identity=ExternalIdentity(
            fqn="org.external.Lib",
            reason="prefix",
            source="maven",
        ),
    )
    env = Envelope(status="not_found", absence=diagnosis)
    json_str = env.to_json()
    assert "absence" in json_str
    assert "external_dependency" in json_str
    assert "org.external.Lib" in json_str


# ----- Test 2: Renderer mapping verdicts -----

def test_render_not_found_with_absence_verdict() -> None:
    """_render_not_found appends verdict + message when envelope.absence is present."""
    diagnosis = AbsenceDiagnosis(
        verdict="not_in_project",
        cause="identifier_miss",
        message="No matches found",
        closest_symbols=[NodeRef(id="sym:1", kind="symbol", fqn="com.foo.SimilarThing")],
        distances=[0.25],
    )
    env = Envelope(status="not_found", absence=diagnosis, message="No matches for 'foo.Bar'.")
    out = render(env, fmt="text")
    assert "not found" in out.lower()
    # Should include verdict information
    assert "not_in_project" in out or "not in project" in out.lower()
    # Should include did-you-mean line for closest_symbols
    assert "did you mean" in out.lower() or "similar" in out.lower()


def test_render_not_found_with_closest_symbols() -> None:
    """Renderer shows did-you-mean line when closest_symbols is non-empty."""
    diagnosis = AbsenceDiagnosis(
        verdict="refine_query",
        cause="nl_miss",
        message="Query too broad",
        closest_symbols=[
            NodeRef(id="sym:1", kind="symbol", fqn="com.foo.Bar"),
            NodeRef(id="sym:2", kind="symbol", fqn="com.baz.Qux"),
        ],
        distances=[0.4, 0.5],
    )
    env = Envelope(status="not_found", absence=diagnosis)
    out = render(env, fmt="text")
    # Should show the closest symbols as suggestions
    assert "com.foo.Bar" in out or "Bar" in out
    assert "com.baz.Qux" in out or "Qux" in out


def test_render_not_found_without_closest_symbols() -> None:
    """Renderer handles empty closest_symbols gracefully."""
    diagnosis = AbsenceDiagnosis(
        verdict="correct_empty",
        cause="meaningful_empty",
        message="This is correctly empty",
        closest_symbols=[],
        distances=[],
    )
    env = Envelope(status="not_found", absence=diagnosis)
    out = render(env, fmt="text")
    assert "correct_empty" in out or "correct empty" in out.lower()


# ----- Test 3: Traversal empty block with is_external_entrypoint -----

def test_render_traversal_empty_external_entrypoint() -> None:
    """Traversal empty block renders 'external entrypoint — no in-repo callers' when is_external_entrypoint=True."""
    env = Envelope(
        status="ok",
        nodes={},
        edges=[],
        root="sym:1",
        is_external_entrypoint=True,
    )
    env.nodes["sym:1"] = {"fqn": "com.foo.Controller.handle", "microservice": "foo-svc"}
    out = render(env, fmt="text", noun="callers", shape="traversal")
    assert "external entrypoint" in out.lower()
    assert "no in-repo callers" in out.lower()
    assert "com.foo.Controller.handle" in out


def test_render_traversal_empty_with_correct_empty_verdict() -> None:
    """Traversal empty block with absence.verdict='correct_empty' renders same text as is_external_entrypoint."""
    diagnosis = AbsenceDiagnosis(
        verdict="correct_empty",
        cause="meaningful_empty",
        message="External entrypoint with no in-repo callers",
    )
    env = Envelope(
        status="ok",
        nodes={},
        edges=[],
        root="sym:1",
        absence=diagnosis,
    )
    env.nodes["sym:1"] = {"fqn": "com.foo.Controller.handle", "microservice": "foo-svc"}
    out = render(env, fmt="text", noun="callers", shape="traversal")
    # Should render similar to is_external_entryponit case
    assert "external entrypoint" in out.lower() or "correct empty" in out.lower()


def test_render_traversal_empty_with_other_verdicts() -> None:
    """Traversal empty block with other verdicts renders short verdict line."""
    # not_in_project verdict
    diagnosis = AbsenceDiagnosis(
        verdict="not_in_project",
        cause="identifier_miss",
        message="Symbol not in project",
    )
    env = Envelope(
        status="ok",
        nodes={},
        edges=[],
        root="sym:1",
        absence=diagnosis,
    )
    env.nodes["sym:1"] = {"fqn": "com.foo.Missing", "microservice": "foo-svc"}
    out = render(env, fmt="text", noun="callers", shape="traversal")
    assert "not in project" in out.lower() or "not_in_project" in out
    # Should still show the root FQN
    assert "com.foo.Missing" in out


# ----- Test 4: Listing empty with absence -----

def test_render_listing_empty_with_absence() -> None:
    """Listing empty renders verdict line before/instead of bare '0 <noun>'."""
    diagnosis = AbsenceDiagnosis(
        verdict="refine_query",
        cause="filter_miss",
        message="Filter too restrictive",
    )
    env = Envelope(status="ok", nodes={}, absence=diagnosis)
    out = render(env, fmt="text", noun="routes")
    assert "refine" in out.lower() or "refine_query" in out
    # The verdict line should replace or augment the bare "0 routes"
    lines = out.splitlines()
    assert any("refine" in line.lower() for line in lines)


# ----- Test 5: Resolve-none carries absence -----

def test_resolve_none_carries_absence_to_envelope() -> None:
    """resolve with status='none' (and out.absence) returns envelope carrying absence."""
    # Mock the graph and resolve_v2
    mock_graph = MagicMock()
    mock_output = ResolveOutput(
        success=True,
        status="none",
        node=None,
        candidates=[],
        message="No matches found",
        absence=AbsenceDiagnosis(
            verdict="not_in_project",
            cause="identifier_miss",
            message="Symbol not in project",
            closest_symbols=[NodeRef(id="sym:1", kind="symbol", fqn="com.foo.Similar")],
            distances=[0.2],
        ),
    )

    # Mock resolve_v2 to return our test output
    from resolve_service import resolve_v2
    original_resolve = resolve_v2
    import resolve_service
    resolve_service.resolve_v2 = lambda *args, **kwargs: mock_output

    try:
        _, env = resolve_query(
            "com.foo.Missing",
            graph=mock_graph,
            hint_kind=None,
            java_kind=None,
            role=None,
            fqn_contains=None,
            cfg=None,
        )
        assert env is not None
        assert env.status == "not_found"
        assert env.absence is not None
        assert env.absence.verdict == "not_in_project"
        assert env.absence.message == "Symbol not in project"
    finally:
        resolve_service.resolve_v2 = original_resolve


def test_resolve_without_absence() -> None:
    """resolve with status='none' but no absence field returns envelope without absence."""
    mock_graph = MagicMock()
    mock_output = ResolveOutput(
        success=True,
        status="none",
        node=None,
        candidates=[],
        message="No matches found",
        absence=None,  # No diagnosis
    )

    from resolve_service import resolve_v2
    original_resolve = resolve_v2
    import resolve_service
    resolve_service.resolve_v2 = lambda *args, **kwargs: mock_output

    try:
        _, env = resolve_query(
            "com.foo.Missing",
            graph=mock_graph,
            hint_kind=None,
            java_kind=None,
            role=None,
            fqn_contains=None,
            cfg=None,
        )
        assert env is not None
        assert env.status == "not_found"
        assert env.absence is None
        # Should still have the fallback message
        assert "jrag search" in env.message
    finally:
        resolve_service.resolve_v2 = original_resolve


# ----- Test 6: All four verdicts render correctly -----

def test_all_verdicts_render_in_not_found() -> None:
    """Each of the 4 verdicts renders appropriately in not_found context."""
    verdicts_messages = [
        ("refine_query", "Query needs refinement", "refine"),
        ("not_in_project", "Not in this codebase", "not in project"),
        ("external_dependency", "External library", "external"),
        ("correct_empty", "Correctly empty", "correct empty"),
    ]

    for verdict, msg, expected_text in verdicts_messages:
        diagnosis = AbsenceDiagnosis(
            verdict=verdict,
            cause="identifier_miss" if verdict == "not_in_project" else "meaningful_empty",
            message=msg,
        )
        env = Envelope(status="not_found", absence=diagnosis)
        out = render(env, fmt="text")
        assert expected_text in out.lower(), f"Verdict {verdict} should render '{expected_text}' in output: {out}"
