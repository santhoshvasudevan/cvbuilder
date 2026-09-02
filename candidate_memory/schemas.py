"""Canonical Pydantic structured-output contracts for Candidate Memory extraction
(requirements.md Sec 4/16, docs/ARCHITECTURE.md Sec 8, D-015).

These are the schemas passed as `NormalizedLLMRequest.output_schema` to the M2 llm_provider
adapter interface -- this module never imports a provider SDK, only `pydantic` and stdlib.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class ContentPlane(str, Enum):
    """Content-plane classification contract (requirements.md Sec 16/docs/ARCHITECTURE.md Sec 8).

    EVIDENCE may become a resume-eligible MemoryClaim. CONSTRAINT and POSITIONING become a
    CandidateRule and must never become a factual claim.
    """

    EVIDENCE = "EVIDENCE"
    CONSTRAINT = "CONSTRAINT"
    POSITIONING = "POSITIONING"


class ExperienceLevel(str, Enum):
    AWARENESS = "AWARENESS"
    LEARNING = "LEARNING"
    PROTOTYPE = "PROTOTYPE"
    PROFESSIONAL_DELIVERY = "PROFESSIONAL_DELIVERY"
    PRODUCTION_OPERATION = "PRODUCTION_OPERATION"
    ARCHITECTURE_OWNERSHIP = "ARCHITECTURE_OWNERSHIP"
    LEADERSHIP = "LEADERSHIP"


class RuleType(str, Enum):
    CAUTION = "CAUTION"
    PROHIBITION = "PROHIBITION"
    PREFERENCE = "PREFERENCE"
    POSITIONING = "POSITIONING"
    LEARNING_STATUS = "LEARNING_STATUS"


class EmploymentDatesValue(BaseModel):
    """Structured comparison payload for `claim_type == "employment_dates"`
    (audit repair: the extraction-to-conflict pipeline). Only this typed shape -- never a loose
    dict -- is what `services/comparable_values.py` will persist into `MemoryClaim.structured_value`
    and compare across sources. Unknown date components must stay unknown (`None`), never invented;
    an open-ended role must be explicitly `end_status="ONGOING"`, never inferred from a merely
    absent end date, which instead is `end_status="UNKNOWN"` (e.g. Ford: start known, end not yet
    determined -- not the same claim as "this role is ongoing")."""

    start_year: int
    start_month: int | None = Field(default=None, ge=1, le=12)
    end_status: str = Field(
        default="UNKNOWN", description="One of KNOWN / ONGOING / UNKNOWN (see class docstring)."
    )
    end_year: int | None = None
    end_month: int | None = Field(default=None, ge=1, le=12)
    precision: str = Field(
        default="YEAR_MONTH", description="YEAR (month unknown) or YEAR_MONTH (both known)."
    )


class EmploymentLocationValue(BaseModel):
    """Structured comparison payload for `claim_type == "employment_location"`. `city` is the
    normalized comparison key; `country`/`country_code` are optional context, never fabricated
    beyond what the source states."""

    city: str
    country: str | None = None
    country_code: str | None = None

    @field_validator("city")
    @classmethod
    def _city_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("city must not be blank -- omit employment_location entirely if unknown.")
        return value


class LanguageProficiencyValue(BaseModel):
    """Structured comparison payload for `claim_type == "language_proficiency"`. `attained_level`
    and `in_progress_level` are deliberately separate fields -- "B1 attained, B2 in progress" must
    never collapse into a single attained-B2 claim (operator resolution 2026-09-02, item 5/6)."""

    language: str
    attained_level: str | None = None
    in_progress_level: str | None = None

    @field_validator("language")
    @classmethod
    def _language_must_be_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "language must not be blank -- omit language_proficiency entirely if unknown."
            )
        return value


class SourcePassage(BaseModel):
    """Provenance-information contract: the exact quotation and line range within one bounded
    chunk that supports an extracted item. Line numbers are relative to the *original* source
    document (the chunker preserves this -- see services/chunking.py), not the chunk."""

    quote: str
    start_line: int
    end_line: int
    language: str = Field(description="'en' or 'de'")


class ExtractedItem(BaseModel):
    """Atomic fact / CandidateRule extraction contract, covering both the evidence plane
    (MemoryClaim-shaped) and the constraint/positioning planes (CandidateRule-shaped) in one
    schema, discriminated by `plane`. A Python-level validator (services/classification.py)
    enforces that plane-appropriate fields are actually populated -- the LLM's own schema
    adherence is necessary but never sufficient (source approval is not extraction approval).
    """

    plane: ContentPlane
    canonical_text_en: str = Field(description="English canonical text, even if the support is German.")
    support: SourcePassage

    # Evidence-plane fields (required when plane == EVIDENCE).
    claim_type: str | None = None
    subject_scope: str | None = None
    experience_level: ExperienceLevel | None = None
    resume_eligible: bool = False
    duplicate_group_hint: str | None = Field(
        default=None,
        description="Duplicate/group candidate identification contract: a short normalized key "
        "the model believes identifies this fact across languages/passages, e.g. "
        "'ford_solutions_architect_role'. Two items sharing this hint are the same underlying "
        "fact, not two separate claims.",
    )
    legal_employer: str | None = Field(
        default=None,
        description="Set only when the source distinguishes a staffing/consultancy legal "
        "employer from the client organization the work was actually performed for (operator "
        "resolution 2026-09-02, item 1/2), e.g. 'Ambigai Consultancy Services'.",
    )
    client_organization: str | None = Field(
        default=None,
        description="The client organization the work was performed for, when distinct from the "
        "legal employer (see legal_employer). Never invent this split when the source does not "
        "state it -- leave both fields None for an ordinary direct-employment claim.",
    )

    # Comparable-claim-type structured payloads (audit repair: extraction-to-conflict pipeline).
    # Exactly one of these three is populated, matching claim_type -- e.g. claim_type
    # "employment_dates" populates employment_dates and leaves the other two null. Leave the
    # matching field null rather than guessing if the excerpt does not clearly state it; a missing
    # payload for a comparable claim_type is treated downstream as "fails closed", never as a
    # silent match against every other claim of that type.
    employment_dates: EmploymentDatesValue | None = None
    employment_location: EmploymentLocationValue | None = None
    language_proficiency: LanguageProficiencyValue | None = None

    # Constraint/positioning-plane fields (required when plane != EVIDENCE).
    rule_type: RuleType | None = None
    scope: str | None = None


class ChunkExtractionResult(BaseModel):
    """Top-level structured-output contract for one bounded chunk."""

    items: list[ExtractedItem] = Field(default_factory=list)


class ConflictCandidate(BaseModel):
    """Conflict-candidate contract: produced by an optional LLM-assisted second pass comparing
    claims that share a (subject_scope, claim_type) group but were not already resolved by the
    deterministic detector (services/conflicts.py) or an operator resolution."""

    conflict_key: str
    description: str
    claim_a_stable_key: str
    claim_b_stable_key: str
    contradictory: bool


class ChunkClassificationOnly(BaseModel):
    """Standalone content-plane classification contract for a single passage, kept separate from
    full extraction for cases where only the plane decision is needed (e.g. re-classifying a
    passage a human flagged as misclassified)."""

    plane: ContentPlane
    support: SourcePassage
