"""Tests for absence_types.py DTOs and absence field on output models."""

import pytest
from pydantic import ValidationError

# These imports will fail until absence_types.py is created
from absence_types import (
    AbsenceDiagnosis,
    AbsenceProof,
    ExternalIdentity,
    ExternalReason,
    VocabularyContext,
    FilterRelaxation,
    FilterRelaxationDim,
    AbsenceVerdict,
    AbsenceCause,
)

# Import the 5 MCP output models
from mcp_v2 import SearchOutput, FindOutput, DescribeOutput, NeighborsOutput
from resolve_service import ResolveOutput


class TestAbsenceTypesDTOs:
    """Tests for absence_types.py data transfer objects."""

    def test_absence_diagnosis_minimal_construct(self):
        """AbsenceDiagnosis constructs with only verdict/cause/message; optional payloads default to None/[]."""
        diagnosis = AbsenceDiagnosis(
            verdict="refine_query",
            cause="identifier_miss",
            message="Query too broad"
        )

        assert diagnosis.verdict == "refine_query"
        assert diagnosis.cause == "identifier_miss"
        assert diagnosis.message == "Query too broad"
        assert diagnosis.closest_symbols == []
        assert diagnosis.distances == []
        assert diagnosis.proof is None
        assert diagnosis.external_identity is None
        assert diagnosis.vocabulary_context is None
        assert diagnosis.filter_relaxation is None

    def test_absence_proof_roundtrip(self):
        """AbsenceProof round-trips via .model_dump() -> re-parse (pydantic equality)."""
        original = AbsenceProof(
            nearest_distance=0.92,
            symbol_count_scanned=1500,
            thresholds_applied={"close": 0.85, "absent_floor": 0.40},
            query_shape="identifier"
        )

        dumped = original.model_dump()
        parsed = AbsenceProof(**dumped)

        assert parsed == original

    def test_external_identity_roundtrip(self):
        """ExternalIdentity round-trips via .model_dump() -> re-parse."""
        original = ExternalIdentity(
            fqn="com.example.external.LibraryClass",
            reason="prefix",
            source="maven"
        )

        dumped = original.model_dump()
        parsed = ExternalIdentity(**dumped)

        assert parsed == original

    def test_vocabulary_context_roundtrip(self):
        """VocabularyContext round-trips via .model_dump() -> re-parse."""
        original = VocabularyContext(
            top_modules=[("com.example.service", 50), ("com.example.util", 30)],
            top_microservices=[("auth-service", 80), ("user-service", 45)],
            roles_present=[("Controller", 25), ("Service", 60)],
            frequent_name_tokens=["user", "auth", "service"]
        )

        dumped = original.model_dump()
        parsed = VocabularyContext(**dumped)

        assert parsed == original

    def test_filter_relaxation_roundtrip(self):
        """FilterRelaxation round-trips via .model_dump() -> re-parse."""
        original = FilterRelaxation(
            per_dimension=[
                FilterRelaxationDim(
                    dimension="microservice",
                    constrained_value="auth-service",
                    matches_under_relaxation=5,
                    suggested_value=None
                )
            ]
        )

        dumped = original.model_dump()
        parsed = FilterRelaxation(**dumped)

        assert parsed == original


class TestAbsenceFieldOnOutputModels:
    """Tests that the 5 MCP output models accept the optional absence field."""

    def test_search_output_accepts_absence_field(self):
        """SearchOutput accepts absence=None (default) and an AbsenceDiagnosis instance."""
        # Test default (None)
        output_default = SearchOutput(
            success=True,
            message="test",
            results=[],
            total=0
        )
        assert output_default.absence is None

        # Test with AbsenceDiagnosis
        diagnosis = AbsenceDiagnosis(
            verdict="not_in_project",
            cause="identifier_miss",
            message="Symbol not found"
        )
        output_with_absence = SearchOutput(
            success=True,
            message="test",
            results=[],
            total=0,
            absence=diagnosis
        )
        assert output_with_absence.absence == diagnosis
        assert output_with_absence.model_dump()["absence"] is not None

    def test_find_output_accepts_absence_field(self):
        """FindOutput accepts absence=None (default) and an AbsenceDiagnosis instance."""
        # Test default (None)
        output_default = FindOutput(
            success=True,
            message="test",
            results=[]
        )
        assert output_default.absence is None

        # Test with AbsenceDiagnosis
        diagnosis = AbsenceDiagnosis(
            verdict="refine_query",
            cause="nl_miss",
            message="No matches found"
        )
        output_with_absence = FindOutput(
            success=True,
            message="test",
            results=[],
            absence=diagnosis
        )
        assert output_with_absence.absence == diagnosis
        assert output_with_absence.model_dump()["absence"] is not None

    def test_describe_output_accepts_absence_field(self):
        """DescribeOutput accepts absence=None (default) and an AbsenceDiagnosis instance."""
        # Test default (None)
        output_default = DescribeOutput(
            success=True,
            message="test",
            node="test-id"
        )
        assert output_default.absence is None

        # Test with AbsenceDiagnosis
        diagnosis = AbsenceDiagnosis(
            verdict="correct_empty",
            cause="meaningful_empty",
            message="Empty result is expected"
        )
        output_with_absence = DescribeOutput(
            success=True,
            message="test",
            node="test-id",
            absence=diagnosis
        )
        assert output_with_absence.absence == diagnosis
        assert output_with_absence.model_dump()["absence"] is not None

    def test_neighbors_output_accepts_absence_field(self):
        """NeighborsOutput accepts absence=None (default) and an AbsenceDiagnosis instance."""
        # Test default (None)
        output_default = NeighborsOutput(
            success=True,
            message="test",
            neighbors=[]
        )
        assert output_default.absence is None

        # Test with AbsenceDiagnosis
        diagnosis = AbsenceDiagnosis(
            verdict="external_dependency",
            cause="external",
            message="External symbol"
        )
        output_with_absence = NeighborsOutput(
            success=True,
            message="test",
            neighbors=[],
            absence=diagnosis
        )
        assert output_with_absence.absence == diagnosis
        assert output_with_absence.model_dump()["absence"] is not None

    def test_resolve_output_accepts_absence_field_without_extra_forbid_error(self):
        """ResolveOutput.model_dump() with an absence set does not raise under extra='forbid'."""
        # Test default (None)
        output_default = ResolveOutput(
            success=True,
            status="one",
            resolved_identifier="Test",
            node=None,
            candidates=[]
        )
        assert output_default.absence is None

        # Test with AbsenceDiagnosis - should not raise due to extra='forbid'
        diagnosis = AbsenceDiagnosis(
            verdict="refine_query",
            cause="filter_miss",
            message="Filter too restrictive"
        )
        output_with_absence = ResolveOutput(
            success=True,
            status="none",
            resolved_identifier="Test",
            node=None,
            candidates=[],
            absence=diagnosis
        )
        assert output_with_absence.absence == diagnosis

        # This should not raise ValidationError due to extra='forbid'
        dumped = output_with_absence.model_dump()
        assert dumped["absence"] is not None
