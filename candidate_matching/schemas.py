"""Agent Candidate's structured LLM output contract (D-014, D-019). Deliberately narrow: the
model only ever sees the bounded retrieval context (`services/retrieve.py`) -- narrative claims
and read-only engagement summaries -- and only ever cites IDs back; it never receives or produces
an employer/title/location/date field (those belong exclusively to `CareerEngagement`, D-019, and
are never part of this schema).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .services.normalization_limits import (
    MAX_DIAGNOSTIC_TERMS,
    MAX_EQUIVALENTS,
    MAX_NORMALIZATION_ITEMS,
    MAX_PRESERVED_TERMS,
    MAX_TERM_CHARS,
    MAX_TEXT_CHARS,
)


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


class RequirementNormalizationItem(BaseModel):
    """One JobRequirement's bounded, canonical-English search representation (2026-09-04 recall
    repair, D-015/D-020) -- a retrieval *hint*, never evidence. This is the only shape the new
    AC_NORMALIZE stage is allowed to produce: no field here can carry a claim, a candidate fact, or
    anything about the candidate's actual history -- it describes only what the requirement means
    and which words a matching claim might use, in English, so `services/candidate_generation.py`
    can score against vocabulary the requirement itself never used (a paraphrase or a foreign-
    language original). `extra="forbid"` plus a hard length/count bound on every field is what
    makes "excessive expansion" a schema-validation failure (caught uniformly by
    `llm_provider.adapters.base.BaseLLMAdapter.generate`) rather than a silent truncation."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    canonical_english_text: str = Field(max_length=MAX_TEXT_CHARS)
    diagnostic_terms: list[str] = Field(default_factory=list, max_length=MAX_DIAGNOSTIC_TERMS)
    equivalents: list[str] = Field(default_factory=list, max_length=MAX_EQUIVALENTS)
    preserved_technical_terms: list[str] = Field(default_factory=list, max_length=MAX_PRESERVED_TERMS)
    source_language: str

    @field_validator("diagnostic_terms", "equivalents", "preserved_technical_terms")
    @classmethod
    def _bound_each_term_length(cls, value: list[str]) -> list[str]:
        for term in value:
            if len(term) > MAX_TERM_CHARS:
                raise ValueError(f"term exceeds {MAX_TERM_CHARS} characters: {term!r}")
        return value


class RequirementNormalizationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RequirementNormalizationItem] = Field(max_length=MAX_NORMALIZATION_ITEMS)
