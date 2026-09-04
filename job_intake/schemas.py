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
    # Use MANDATORY only when the posting states or clearly requires it (e.g. "must have",
    # "required", "X+ years required") -- never for ordinary desirable content (2026-09-04 AJ
    # hardening, D-022).
    MANDATORY = "MANDATORY"
    # Use PREFERRED for anything the posting frames as preferred/desirable/advantageous/a plus/
    # nice-to-have -- never promoted to MANDATORY just because it sounds important.
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


class ScreeningRisk(BaseModel):
    """One explicit hiring constraint or condition stated by the posting itself (2026-09-04 AJ
    hardening, D-022): a work-authorization requirement, a mandatory on-call rotation, a security-
    clearance requirement, a relocation requirement, and similar things a recruiter would flag as
    needing operator attention -- never a restatement of an ordinary responsibility/qualification
    as if it were a candidate's gap. Agent Jobber has no Candidate Memory context (D-022): it
    cannot know what "the candidate" does or doesn't have, so it must never phrase a risk as a
    judgment about a candidate ("lack of...", "no experience with...", "insufficient...") -- only
    as a condition the posting itself states. `source_context` is mandatory here (unlike
    `ExtractedRequirement`'s optional one) precisely because an unstated risk is not an explicit
    constraint at all -- it would be exactly the kind of ungrounded inference this schema exists
    to forbid.
    """

    text: str = Field(description="The explicit hiring constraint/condition, in English.")
    source_context: str = Field(
        description="The exact quotation from the posting stating this constraint/condition."
    )

    @field_validator("text", "source_context")
    @classmethod
    def _fields_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text and source_context must not be blank.")
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
    requirements: list[ExtractedRequirement] = Field(
        default_factory=list,
        description="Every explicit responsibility, qualification, skill, and experience "
        "expectation the posting states becomes one atomic item here -- MANDATORY, PREFERRED, "
        "RESPONSIBILITY, ATS_SIGNAL, or IMPLIED_EXPECTATION. Never rephrase this content as a "
        "screening risk instead.",
    )
    screening_risks: list[ScreeningRisk] = Field(
        default_factory=list,
        description="Only explicit hiring constraints/conditions the posting itself states -- "
        "never a candidate-gap restatement of a responsibility or qualification.",
    )

    @field_validator("posting_language")
    @classmethod
    def _posting_language_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("posting_language must not be blank -- always detect and report it.")
        return value
