"""M6's own bounded context builder (audit hardening, 2026-09-03) -- Agent Builder must never
reload or send the entire CandidateMemory, and must not even re-run M5's own retrieval query
against the full eligible pool. Instead it builds strictly from what the *current, approved*
FitAssessment already selected:

- claims: exactly `FitAssessment.retrieved_claim_ids` (the bounded, ranked set M5 already
  selected -- this is a superset of every claim any RequirementAssessment actually cited, since
  the disposition-coverage validator only ever accepts evidence from that same set), re-verified
  fresh against the database (still CONFIRMED/resume_eligible/non-static/on the ACTIVE revision --
  a claim retired or unconfirmed since M5 ran is excluded rather than blindly trusted from a
  stored ID list);
- engagements: exactly `FitAssessment.retrieved_engagement_ids`, re-verified still APPROVED;
- rules: recomputed via the exact same bounded/deduplicated/capped selection M5 uses
  (`candidate_matching.services.rule_selection.select_bounded_rules`), applied fresh rather than
  stored, since rules are not claim-specific and recomputing is deterministic and idempotent.
"""

from __future__ import annotations

from candidate_matching.models import FitAssessment
from candidate_matching.services.retrieve import RetrievalContext, RetrievedClaim, RetrievedEngagement
from candidate_matching.services.rule_selection import select_bounded_rules
from candidate_memory.models import CandidateMemory, CareerEngagement, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES


def build_builder_context(fit_assessment: FitAssessment) -> RetrievalContext:
    claims = list(
        MemoryClaim.objects.filter(
            claim_id__in=fit_assessment.retrieved_claim_ids,
            candidate_memory__status=CandidateMemory.Status.ACTIVE,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            resume_eligible=True,
        )
        .exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES)
        .prefetch_related("engagement_mappings__career_engagement")
    )

    engagement_ids = set(fit_assessment.retrieved_engagement_ids)
    approved_engagements = {
        engagement.engagement_id: engagement
        for engagement in CareerEngagement.objects.filter(
            engagement_id__in=engagement_ids, approval_status=CareerEngagement.ApprovalStatus.APPROVED
        )
    }

    retrieved_claims: list[RetrievedClaim] = []
    for claim in claims:
        approved_engagement_ids = tuple(
            sorted(
                mapping.career_engagement.engagement_id
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement.engagement_id in approved_engagements
            )
        )
        retrieved_claims.append(
            RetrievedClaim(
                claim_id=claim.claim_id,
                text=claim.canonical_text_en,
                claim_type=claim.claim_type,
                subject_scope=claim.subject_scope,
                approved_engagement_ids=approved_engagement_ids,
            )
        )
    retrieved_claims.sort(key=lambda c: c.claim_id)

    retrieved_engagements = [
        RetrievedEngagement(
            engagement_id=engagement.engagement_id,
            approved_role_title=engagement.approved_role_title,
            displayed_organization=engagement.displayed_organization,
            location=engagement.location,
            is_current=engagement.is_current,
            duration_months=engagement.duration_months(),
        )
        for engagement in approved_engagements.values()
    ]
    retrieved_engagements.sort(key=lambda e: e.engagement_id)

    candidate_memory = claims[0].candidate_memory if claims else None
    if candidate_memory is not None:
        requirement_texts = list(
            fit_assessment.based_on_jra.requirements.values_list("text", flat=True)
        )
        rule_result = select_bounded_rules(candidate_memory, requirement_texts)
        rules = rule_result.selected
    else:
        rules = []

    return RetrievalContext(
        candidate_memory_id=candidate_memory.pk if candidate_memory else 0,
        claims=retrieved_claims,
        engagements=retrieved_engagements,
        rules=rules,
    )
