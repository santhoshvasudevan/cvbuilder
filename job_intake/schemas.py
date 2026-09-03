"""Canonical Pydantic structured-output contract for Agent Jobber (requirements.md Sec 5/16,
docs/ARCHITECTURE.md Sec 4 `JobRequirementAnalysis`/`JobRequirement`, D-014).

Passed as `NormalizedLLMRequest.output_schema` to the M2 llm_provider adapter interface only --
this module never imports a provider SDK, only pydantic and stdlib. The model never supplies a
requirement numbering scheme: `services/analyze.py`'s caller assigns stable `JR-NNN` IDs from this
schema's `requirements` list order, entirely in application code (point 25 of the M4 spec) -- there
is deliberately no `requirement_id`/index field here for the model to (mis)populate.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RequirementCategory(str, Enum):
    MANDATORY = "MANDATORY"
    PREFERRED = "PREFERRED"
    RESPONSIBILITY = "RESPONSIBILITY"
    ATS_SIGNAL = "ATS_SIGNAL"
    IMPLIED_EXPECTATION = "IMPLIED_EXPECTATION"


class ExtractedRequirement(BaseModel):
    """One material requirement/responsibility/signal extracted from the posting.

    `category=IMPLIED_EXPECTATION` marks something the posting does not state outright (e.g. a
    seniority signal, unstated tooling assumption) -- the UI must always label these as inferred
    and never render them as a quoted fact (requirements.md Sec 5/16). `source_context`, when
    given, is context/quotation from the posting; for an implied expectation it is grounding
    context, never a claim that the posting said this literally.
    """

    category: RequirementCategory
    text: str = Field(description="The requirement/responsibility/signal itself, in English.")
    source_context: str = Field(
        default="", description="Optional supporting quotation/context from the posting."
    )

    @field_validator("text")
    @classmethod
    def _text_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank.")
        return value


class AgentJobberAnalysis(BaseModel):
    """Agent Jobber's complete structured output for one job posting (requirements.md Sec 5)."""

    employer: str = Field(default="", description="Employer/company name, as stated in the posting.")
    role_title: str = Field(default="", description="The role/job title, as stated in the posting.")
    posting_language: str = Field(
        description="The posting's own detected language (e.g. 'en', 'de') -- never assumed English."
    )
    location: str = Field(default="", description="Location, as stated, if any.")
    work_arrangement: str = Field(
        default="", description="Remote/hybrid/onsite or similar, only if the posting states it."
    )
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    screening_risks: list[str] = Field(
        default_factory=list,
        description="Things that might get a candidate filtered out (recruiter-lens judgment, "
        "not necessarily stated outright in the posting).",
    )

    @field_validator("posting_language")
    @classmethod
    def _posting_language_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("posting_language must not be blank -- always detect and report it.")
        return value
