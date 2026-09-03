"""Deterministic, LLM-free assessment for static/structural job requirements (M5 point 3,
D-019). Classification of a `JobRequirement.text` into one of the kinds below is a heuristic,
regex-based v1 implementation -- like D-004's fetch-extraction heuristic, this is a best-effort
classifier, not a claim that every possible real-world phrasing is recognized. A requirement this
classifier does not recognize simply falls through to the LLM-backed narrative path (`services/
assess.py`) unchanged; nothing here can cause a requirement to go unassessed.

Every assessment produced here cites only `supporting_engagement_ids` -- never an LLM call, and
never a static profile value (dates, location, employer identity) sent to one. This is the
"requirements... assessed locally" half of D-019's static-profile boundary.
"""

from __future__ import annotations

import dataclasses
import enum
import re

from candidate_memory.models import CareerEngagement
from candidate_memory.services.career_engagement import total_non_overlapping_experience_months
from candidate_memory.services.static_profile_boundary import (
    assess_location_requirement_locally,
    assess_tenure_requirement_locally,
)

from ..models import RequirementAssessment


class StaticRequirementKind(str, enum.Enum):
    TOTAL_EXPERIENCE = "TOTAL_EXPERIENCE"
    TENURE = "TENURE"
    CURRENT_PAST_STATUS = "CURRENT_PAST_STATUS"
    LOCATION = "LOCATION"
    EMPLOYER_CLIENT_RELATIONSHIP = "EMPLOYER_CLIENT_RELATIONSHIP"


_YEARS_RE = re.compile(r"\b(\d+)\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_TOTAL_KEYWORDS_RE = re.compile(r"\b(total|overall|combined|cumulative)\b", re.IGNORECASE)
_EXPERIENCE_KEYWORD_RE = re.compile(r"\bexperience\b", re.IGNORECASE)
_CURRENT_STATUS_RE = re.compile(
    r"\b(currently employed|currently working|active(?:ly)? employed|must (?:be|still) "
    r"(?:be )?currently|no longer (?:be )?employed|not currently employed)\b",
    re.IGNORECASE,
)
_LOCATION_RE = re.compile(
    r"\b(?:based in|located in|on-?site in|must reside in|must be located in)\s+([A-Za-z][A-Za-z ,.'-]*)",
    re.IGNORECASE,
)
_EMPLOYER_RELATIONSHIP_RE = re.compile(
    r"\b(direct employment|direct hire|not through a (?:staffing|consultancy|consulting) "
    r"firm|no third[- ]party|no agenc(?:y|ies)|no consultanc(?:y|ies)|w-?2 employee|"
    r"permanent employee\b.*\bnot\b.*\bcontractor)\b",
    re.IGNORECASE,
)


def classify(requirement_text: str) -> StaticRequirementKind | None:
    """Ordered, first-match classification -- order matters because some phrasings could
    otherwise match more than one pattern (e.g. "5+ years total experience" contains both a years
    figure and the word "experience")."""
    if _LOCATION_RE.search(requirement_text):
        return StaticRequirementKind.LOCATION
    if _CURRENT_STATUS_RE.search(requirement_text):
        return StaticRequirementKind.CURRENT_PAST_STATUS
    if _EMPLOYER_RELATIONSHIP_RE.search(requirement_text):
        return StaticRequirementKind.EMPLOYER_CLIENT_RELATIONSHIP
    years_match = _YEARS_RE.search(requirement_text)
    if years_match and _EXPERIENCE_KEYWORD_RE.search(requirement_text):
        if _TOTAL_KEYWORDS_RE.search(requirement_text):
            return StaticRequirementKind.TOTAL_EXPERIENCE
        return StaticRequirementKind.TENURE
    return None


@dataclasses.dataclass(frozen=True)
class LocalAssessmentResult:
    disposition: str
    explanation: str
    gap_or_limitation: str
    supporting_engagement_ids: list[str]


def _required_years(requirement_text: str) -> int | None:
    match = _YEARS_RE.search(requirement_text)
    return int(match.group(1)) if match else None


def assess(
    kind: StaticRequirementKind, requirement_text: str, engagements: list[CareerEngagement]
) -> LocalAssessmentResult:
    if kind == StaticRequirementKind.TOTAL_EXPERIENCE:
        return _assess_total_experience(requirement_text, engagements)
    if kind == StaticRequirementKind.TENURE:
        return _assess_tenure(requirement_text, engagements)
    if kind == StaticRequirementKind.CURRENT_PAST_STATUS:
        return _assess_current_status(requirement_text, engagements)
    if kind == StaticRequirementKind.LOCATION:
        return _assess_location(requirement_text, engagements)
    if kind == StaticRequirementKind.EMPLOYER_CLIENT_RELATIONSHIP:
        return _assess_employer_relationship(requirement_text, engagements)
    raise ValueError(f"Unhandled StaticRequirementKind: {kind}")


def _assess_total_experience(text: str, engagements: list[CareerEngagement]) -> LocalAssessmentResult:
    required_years = _required_years(text)
    result = total_non_overlapping_experience_months(engagements)
    if required_years is None:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.UNKNOWN,
            explanation="Could not determine the required number of years from the requirement text.",
            gap_or_limitation="Requirement phrasing did not state a parseable years figure.",
            supporting_engagement_ids=[],
        )
    required_months = required_years * 12
    total_months = result.total_months
    disposition = (
        RequirementAssessment.Disposition.MATCH
        if total_months >= required_months
        else RequirementAssessment.Disposition.GAP
    )
    explanation = (
        f"Total non-overlapping experience across approved engagements is {total_months} months; "
        f"requirement is {required_months} months ({required_years} years)."
    )
    gap = (
        ""
        if disposition == RequirementAssessment.Disposition.MATCH
        else f"Total experience ({total_months} months) falls short of the required {required_months} months."
    )
    return LocalAssessmentResult(
        disposition=disposition,
        explanation=explanation,
        gap_or_limitation=gap,
        supporting_engagement_ids=list(result.included_engagement_ids),
    )


def _assess_tenure(text: str, engagements: list[CareerEngagement]) -> LocalAssessmentResult:
    required_years = _required_years(text)
    if required_years is None:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.UNKNOWN,
            explanation="Could not determine the required number of years from the requirement text.",
            gap_or_limitation="Requirement phrasing did not state a parseable years figure.",
            supporting_engagement_ids=[],
        )
    required_months = required_years * 12
    matching = [
        engagement
        for engagement in engagements
        if assess_tenure_requirement_locally(engagement, minimum_months=required_months)
    ]
    if matching:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.MATCH,
            explanation=(
                f"At least one approved engagement meets the {required_years}-year tenure "
                f"requirement: {', '.join(e.engagement_id for e in matching)}."
            ),
            gap_or_limitation="",
            supporting_engagement_ids=[e.engagement_id for e in matching],
        )
    result = total_non_overlapping_experience_months(engagements)
    if result.total_months >= required_months:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.PARTIAL,
            explanation=(
                f"No single approved engagement reaches {required_years} years alone, but total "
                f"non-overlapping experience ({result.total_months} months) does."
            ),
            gap_or_limitation=(
                "Tenure requirement likely intends a single continuous role, which no one "
                "engagement satisfies on its own."
            ),
            supporting_engagement_ids=list(result.included_engagement_ids),
        )
    return LocalAssessmentResult(
        disposition=RequirementAssessment.Disposition.GAP,
        explanation=(
            f"No approved engagement, and no combination, reaches the required {required_years} "
            "years of tenure."
        ),
        gap_or_limitation=f"Longest available tenure falls short of {required_years} years.",
        supporting_engagement_ids=[],
    )


def _assess_current_status(text: str, engagements: list[CareerEngagement]) -> LocalAssessmentResult:
    requires_current = "no longer" not in text.lower() and "not currently" not in text.lower()
    current_engagements = [engagement for engagement in engagements if engagement.is_current]
    if requires_current:
        if current_engagements:
            return LocalAssessmentResult(
                disposition=RequirementAssessment.Disposition.MATCH,
                explanation=(
                    f"Currently active engagement(s) on record: "
                    f"{', '.join(e.engagement_id for e in current_engagements)}."
                ),
                gap_or_limitation="",
                supporting_engagement_ids=[e.engagement_id for e in current_engagements],
            )
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.GAP,
            explanation="No approved engagement is currently active.",
            gap_or_limitation="Requirement expects the candidate to be currently employed/active.",
            supporting_engagement_ids=[],
        )
    if current_engagements:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.GAP,
            explanation=(
                "Requirement expects the candidate to not currently be employed, but "
                f"{', '.join(e.engagement_id for e in current_engagements)} is/are current."
            ),
            gap_or_limitation="Candidate has a currently active engagement.",
            supporting_engagement_ids=[e.engagement_id for e in current_engagements],
        )
    return LocalAssessmentResult(
        disposition=RequirementAssessment.Disposition.MATCH,
        explanation="No approved engagement is currently active.",
        gap_or_limitation="",
        supporting_engagement_ids=[],
    )


def _assess_location(text: str, engagements: list[CareerEngagement]) -> LocalAssessmentResult:
    match = _LOCATION_RE.search(text)
    required_location = match.group(1).strip().rstrip(".,") if match else ""
    if not required_location:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.UNKNOWN,
            explanation="Could not extract a specific location from the requirement text.",
            gap_or_limitation="Requirement phrasing did not state a parseable location.",
            supporting_engagement_ids=[],
        )
    matching = [
        engagement
        for engagement in engagements
        if engagement.location
        and assess_location_requirement_locally(engagement, required_location=required_location)
    ]
    if matching:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.MATCH,
            explanation=(
                f"Approved engagement location(s) matching {required_location!r}: "
                f"{', '.join(e.engagement_id for e in matching)}."
            ),
            gap_or_limitation="",
            supporting_engagement_ids=[e.engagement_id for e in matching],
        )
    return LocalAssessmentResult(
        disposition=RequirementAssessment.Disposition.GAP,
        explanation=f"No approved engagement's recorded location matches {required_location!r}.",
        gap_or_limitation=f"No engagement location matches the required {required_location!r}.",
        supporting_engagement_ids=[],
    )


def _assess_employer_relationship(text: str, engagements: list[CareerEngagement]) -> LocalAssessmentResult:
    direct_engagements = [engagement for engagement in engagements if not engagement.client_organization]
    if direct_engagements:
        return LocalAssessmentResult(
            disposition=RequirementAssessment.Disposition.MATCH,
            explanation=(
                "At least one approved engagement is a direct employment relationship (no "
                f"separate client organisation): {', '.join(e.engagement_id for e in direct_engagements)}."
            ),
            gap_or_limitation="",
            supporting_engagement_ids=[e.engagement_id for e in direct_engagements],
        )
    return LocalAssessmentResult(
        disposition=RequirementAssessment.Disposition.GAP,
        explanation=(
            "All approved engagements on record are client/consulting assignments -- none is a "
            "direct employment relationship."
        ),
        gap_or_limitation="No direct-employment engagement on record.",
        supporting_engagement_ids=[],
    )
