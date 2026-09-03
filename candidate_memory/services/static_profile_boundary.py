"""The deterministic static-profile boundary (D-019) between operator-owned structured facts and
the not-yet-implemented M5 (Agent Candidate) and M6 (Agent Builder) stages.

Nothing here starts M5 or M6: there is no `JobRequirement`/`FitAssessment`/`ResumeDraft` model, no
retrieval, and no LLM call anywhere in this module. It exists so the boundary those later stages
must respect is real, importable, and testable *now*:

- employment identity, client organisation, titles, locations, and dates are operator-owned
  structured facts (`CareerEngagement`) -- never generated, rewritten, or inferred by an LLM;
- an LLM may only ever select evidence and tailor narrative wording; its planned output schema
  (`EngagementNarrativeOutput`) has no field for any of the static facts above, and `extra="forbid"`
  means a provider that tried to include one would fail schema validation outright, not merely be
  ignored by prompt convention;
- a rendered resume experience header comes exclusively from an `APPROVED` `CareerEngagement`
  record (`render_engagement_header`), resolved fresh from the database every time, never cached
  from or influenced by generated text; an unknown or unapproved `engagement_id` fails validation
  rather than rendering a placeholder.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models import CareerEngagement

_MONTH_NAMES = (
    "",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


class RequirementEvidenceReference(BaseModel):
    """The planned M5 `RequirementAssessment` evidence-attachment shape (requirements.md Sec 8/16,
    extended by D-019): a disposition may cite confirmed `MemoryClaim`s, approved
    `CareerEngagement`s, or both -- e.g. a tenure/location/employment-relationship requirement is
    satisfied purely by an engagement's own structured fields, with no narrative claim needed at
    all. `extra="forbid"` so a future caller cannot smuggle an unrelated field through this
    contract unnoticed."""

    model_config = ConfigDict(extra="forbid")

    supporting_memory_claim_ids: list[str] = Field(default_factory=list)
    supporting_engagement_ids: list[str] = Field(default_factory=list)


class EngagementBullet(BaseModel):
    """One planned M6 Agent Builder output bullet for a given engagement. Deliberately has no
    employer/title/location/date field -- see module docstring."""

    model_config = ConfigDict(extra="forbid")

    text: str
    supporting_memory_claim_ids: list[str] = Field(min_length=1)


class EngagementNarrativeOutput(BaseModel):
    """The planned M6 Agent Builder output unit for one engagement: an `engagement_id` (never the
    employer/title/location/dates themselves) plus evidence-backed tailored bullets."""

    model_config = ConfigDict(extra="forbid")

    engagement_id: str
    bullets: list[EngagementBullet]


class UnknownOrUnapprovedEngagementError(Exception):
    pass


def resolve_approved_engagement(engagement_id: str) -> CareerEngagement:
    """Fail-closed lookup the planned M6 renderer must use for every `engagement_id` an LLM output
    references: an ID that does not exist, or that exists but is not `APPROVED`, is refused
    outright rather than rendered with a placeholder or guessed static field."""
    try:
        engagement = CareerEngagement.objects.get(engagement_id=engagement_id)
    except CareerEngagement.DoesNotExist as exc:
        raise UnknownOrUnapprovedEngagementError(
            f"No CareerEngagement with engagement_id={engagement_id!r} exists."
        ) from exc
    if engagement.approval_status != CareerEngagement.ApprovalStatus.APPROVED:
        raise UnknownOrUnapprovedEngagementError(
            f"CareerEngagement {engagement_id} is {engagement.approval_status}, not APPROVED -- "
            "it cannot be rendered."
        )
    return engagement


def render_engagement_header(engagement_id: str, *, language: str = "en") -> str:
    """Deterministic resume experience-section header. Employer, title, location, and dates come
    exclusively from the approved `CareerEngagement` record resolved by `engagement_id` -- never
    from any LLM-supplied field, and never machine-translated (`title_for_language` only ever
    returns an operator-stored alternative or the English default)."""
    engagement = resolve_approved_engagement(engagement_id)
    title = engagement.title_for_language(language)
    header = f"{title}, {engagement.displayed_organization} ({_format_date_range(engagement)})"
    if engagement.location:
        header += f" -- {engagement.location}"
    return header


def _format_date_range(engagement: CareerEngagement) -> str:
    start = _format_month_year(engagement.start_year, engagement.start_month)
    if engagement.end_status == CareerEngagement.EndStatus.PRESENT:
        end = "Present"
    elif engagement.end_status == CareerEngagement.EndStatus.KNOWN:
        end = _format_month_year(engagement.end_year, engagement.end_month)
    else:
        end = "Unknown"
    return f"{start} - {end}"


def _format_month_year(year: int, month: int | None) -> str:
    if month is None:
        return str(year)
    return f"{_MONTH_NAMES[month]} {year}"


def assess_tenure_requirement_locally(
    engagement: CareerEngagement, *, minimum_months: int, as_of: datetime.date | None = None
) -> bool:
    """A static, structural requirement (e.g. "minimum 3 years in a similar role") assessed purely
    from `CareerEngagement`'s own stored fields -- no LLM call, per D-019."""
    duration = engagement.duration_months(as_of=as_of)
    if duration is None:
        return False
    return duration >= minimum_months


def assess_location_requirement_locally(engagement: CareerEngagement, *, required_location: str) -> bool:
    """A static location requirement assessed purely from the stored, operator-approved
    `location` field -- no LLM call, no geocoding/fuzzy matching."""
    return _normalize(engagement.location) == _normalize(required_location)


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())
