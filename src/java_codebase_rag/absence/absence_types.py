"""Absence diagnosis data transfer objects.

These types define the contract for explaining empty MCP tool results.
Later PRs (ABS-2, ABS-3) populate these fields; ABS-0 only declares them.
"""

from typing import Literal

from pydantic import BaseModel, Field

from java_codebase_rag.graph.graph_types import NodeRef

__all__ = [
    "AbsenceVerdict",
    "AbsenceCause",
    "ExternalReason",
    "AbsenceProof",
    "ExternalIdentity",
    "VocabularyContext",
    "FilterRelaxationDim",
    "FilterRelaxation",
    "AbsenceDiagnosis",
]

# Literal types for verdicts and causes
AbsenceVerdict = Literal["refine_query", "not_in_project", "external_dependency", "correct_empty"]
AbsenceCause = Literal["identifier_miss", "nl_miss", "filter_miss", "external", "meaningful_empty"]
ExternalReason = Literal["prefix", "phantom", "unresolved-call"]


class AbsenceProof(BaseModel):
    """Evidence backing a hard 'not_in_project' verdict.

    Attributes:
        nearest_distance: Distance to the closest symbol found (0-1)
        symbol_count_scanned: Total symbols examined during search
        thresholds_applied: The similarity thresholds used in the decision
        query_shape: Shape of the original query (currently only "identifier")
    """
    nearest_distance: float
    symbol_count_scanned: int
    thresholds_applied: dict[str, float]
    query_shape: Literal["identifier"]


class ExternalIdentity(BaseModel):
    """Identifies an external dependency that caused the empty result.

    Attributes:
        fqn: Fully qualified name of the external symbol
        reason: Why we believe this is external (prefix, phantom, unresolved call)
        source: Optional source name (e.g., "maven", "gradle")
    """
    fqn: str
    reason: ExternalReason
    source: str | None = None


class VocabularyContext(BaseModel):
    """Project vocabulary statistics to inform query refinement.

    Attributes:
        top_modules: Most frequent modules with counts
        top_microservices: Most frequent microservices with counts
        roles_present: Symbol roles present with counts
        frequent_name_tokens: Common tokens in symbol names
    """
    top_modules: list[tuple[str, int]]
    top_microservices: list[tuple[str, int]]
    roles_present: list[tuple[str, int]]
    frequent_name_tokens: list[str]


class FilterRelaxationDim(BaseModel):
    """Relaxation analysis for a single filter dimension.

    Attributes:
        dimension: The filter dimension (e.g., "microservice", "role")
        constrained_value: The value that constrained results
        matches_under_relaxation: Results if this dimension were relaxed
        suggested_value: Optional alternative value to try
    """
    dimension: str
    constrained_value: str | None
    matches_under_relaxation: int
    suggested_value: str | None


class FilterRelaxation(BaseModel):
    """Analysis of how relaxing filters would affect results.

    Attributes:
        per_dimension: List of relaxation options per dimension
    """
    per_dimension: list[FilterRelaxationDim]


class AbsenceDiagnosis(BaseModel):
    """Explains why an MCP tool returned no results.

    This is the main DTO that the 5 MCP output models optionally carry.
    In PR-ABS-0, the `absence` field stays None everywhere — later PRs
    populate it with diagnosis logic.

    Attributes:
        verdict: High-level judgment on the empty result
        cause: Specific cause that led to this verdict
        message: Human-readable explanation
        closest_symbols: Symbols closest to the query (if any)
        distances: Corresponding distance values
        proof: Evidence for not_in_project verdict
        external_identity: External dependency info
        vocabulary_context: Project vocabulary for refinement
        filter_relaxation: Filter relaxation suggestions
    """
    verdict: AbsenceVerdict
    cause: AbsenceCause
    message: str
    closest_symbols: list[NodeRef] = Field(default_factory=list)
    distances: list[float] = Field(default_factory=list)
    proof: AbsenceProof | None = None
    external_identity: ExternalIdentity | None = None
    vocabulary_context: VocabularyContext | None = None
    filter_relaxation: FilterRelaxation | None = None
