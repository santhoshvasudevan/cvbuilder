"""Bounded, deterministic retrieval from the ACTIVE CandidateMemory for Agent Candidate (M5,
requirements.md Sec 6, D-015's runtime-context boundaries, D-019).

This never sends the entire CandidateMemory (or a source document, or the reference snapshot) to
an LLM -- it selects a bounded subset by plain PostgreSQL queries and hands back exactly that
subset, recording exactly which claim/engagement IDs were included (candidate_matching.models.
FitAssessment.retrieved_claim_ids/retrieved_engagement_ids is populated straight from this
result).

Included:
- every CONFIRMED, resume_eligible, narrative (non-static) MemoryClaim that has an APPROVED
  ClaimEngagementMapping to an APPROVED CareerEngagement ("approved engagement-mapped narrative
  claims");
- every CONFIRMED, resume_eligible, narrative MemoryClaim with no engagement mapping at all --
  these are claims with no single-employer identity (career-level, skill, language, ...) and are
  "relevant global claims" by construction: nothing employer-specific ever reaches this bucket,
  since an employer-specific claim either already has an approved mapping (the bucket above) or is
  a genuinely unresolved/ambiguous mapping candidate that stays excluded here (never guessed at
  retrieval time -- see D-019 and `services/engagement_mapping.py`);
- every APPROVED CareerEngagement (for the local static-requirement assessors and as read-only
  context for the LLM to cite by engagement_id);
- every CandidateRule on the ACTIVE revision (constraint/positioning planes -- read-only context
  that can shape a gap explanation, e.g. "still learning Go", but can never itself satisfy a
  MATCH/PARTIAL disposition).

Excluded, always: STATIC_ENGAGEMENT_CLAIM_TYPES (employment_dates/employment_location/position/
position_title -- CareerEngagement already owns this content, D-019), any claim that is not both
CONFIRMED and resume_eligible, and any claim whose engagement mapping is only PROPOSED/REJECTED
(not yet APPROVED).
"""

from __future__ import annotations

import dataclasses

from candidate_memory.models import CandidateMemory, CandidateRule, CareerEngagement, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES


class NoActiveCandidateMemoryError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class RetrievedClaim:
    claim_id: str
    text: str
    claim_type: str
    subject_scope: str
    engagement_id: str | None


@dataclasses.dataclass(frozen=True)
class RetrievedEngagement:
    engagement_id: str
    approved_role_title: str
    displayed_organization: str
    location: str
    is_current: bool
    duration_months: int | None


@dataclasses.dataclass(frozen=True)
class RetrievedRule:
    rule_type: str
    text: str
    scope: str


@dataclasses.dataclass(frozen=True)
class RetrievalContext:
    candidate_memory_id: int
    claims: list[RetrievedClaim]
    engagements: list[RetrievedEngagement]
    rules: list[RetrievedRule]

    @property
    def claim_ids(self) -> list[str]:
        return [claim.claim_id for claim in self.claims]

    @property
    def engagement_ids(self) -> list[str]:
        return [engagement.engagement_id for engagement in self.engagements]


def get_active_candidate_memory() -> CandidateMemory:
    try:
        return CandidateMemory.objects.get(status=CandidateMemory.Status.ACTIVE)
    except CandidateMemory.DoesNotExist as exc:
        raise NoActiveCandidateMemoryError(
            "No ACTIVE CandidateMemory revision exists -- Agent Candidate cannot run until one "
            "is activated."
        ) from exc


def retrieve_context(candidate_memory: CandidateMemory) -> RetrievalContext:
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )
    engagement_by_pk = {engagement.pk: engagement for engagement in approved_engagements}

    eligible_claims = candidate_memory.claims.filter(
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        resume_eligible=True,
    ).exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES).prefetch_related("engagement_mappings")

    retrieved_claims: list[RetrievedClaim] = []
    for claim in eligible_claims:
        approved_mapping = next(
            (
                mapping
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement_id in engagement_by_pk
            ),
            None,
        )
        has_any_mapping = any(claim.engagement_mappings.all())
        if has_any_mapping and approved_mapping is None:
            # Engagement-specific but only PROPOSED/REJECTED -- not yet operator-approved for
            # placement, so it stays excluded from this run rather than guessed.
            continue
        retrieved_claims.append(
            RetrievedClaim(
                claim_id=claim.claim_id,
                text=claim.canonical_text_en,
                claim_type=claim.claim_type,
                subject_scope=claim.subject_scope,
                engagement_id=(
                    engagement_by_pk[approved_mapping.career_engagement_id].engagement_id
                    if approved_mapping
                    else None
                ),
            )
        )

    retrieved_engagements = [
        RetrievedEngagement(
            engagement_id=engagement.engagement_id,
            approved_role_title=engagement.approved_role_title,
            displayed_organization=engagement.displayed_organization,
            location=engagement.location,
            is_current=engagement.is_current,
            duration_months=engagement.duration_months(),
        )
        for engagement in approved_engagements
    ]

    retrieved_rules = [
        RetrievedRule(rule_type=rule.rule_type, text=rule.text, scope=rule.scope)
        for rule in CandidateRule.objects.filter(candidate_memory=candidate_memory)
    ]

    return RetrievalContext(
        candidate_memory_id=candidate_memory.pk,
        claims=retrieved_claims,
        engagements=retrieved_engagements,
        rules=retrieved_rules,
    )
