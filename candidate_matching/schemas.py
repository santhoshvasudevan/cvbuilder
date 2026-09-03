"""Agent Candidate's structured LLM output contract (D-014, D-019). Deliberately narrow: the
model only ever sees the bounded retrieval context (`services/retrieve.py`) -- narrative claims
and read-only engagement summaries -- and only ever cites IDs back; it never receives or produces
an employer/title/location/date field (those belong exclusively to `CareerEngagement`, D-019, and
are never part of this schema).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RequirementAssessmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    disposition: str = Field(description="One of MATCH, PARTIAL, GAP, UNKNOWN.")
    explanation: str
    gap_or_limitation: str = ""
    supporting_memory_claim_ids: list[str] = Field(default_factory=list)
    supporting_engagement_ids: list[str] = Field(default_factory=list)


class AgentCandidateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_assessments: list[RequirementAssessmentItem]


class RequirementRelevanceItem(BaseModel):
    """One JobRequirement's relevance verdict over the bounded candidate pool (D-015's bounded
    relevance-ranking step, audit hardening 2026-09-03). An empty `relevant_claim_ids` list is a
    real, explicit answer ("nothing in the candidate pool is relevant to this requirement") --
    never treated as a schema omission, and never allowed to imply MATCH on its own."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    relevant_claim_ids: list[str] = Field(default_factory=list)


class RelevanceRankingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rankings: list[RequirementRelevanceItem]
