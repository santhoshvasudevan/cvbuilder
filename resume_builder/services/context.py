"""M6's own bounded context builder (audit hardening, 2026-09-03; hybrid-context correction,
D-035, 2026-09-06) -- Agent Builder must never reload or send the entire CandidateMemory, and must
not even re-run M5's own retrieval query against the full eligible pool.

Three layers are combined here, each tagged with *why* it is present
(`candidate_matching.services.retrieve.RETRIEVAL_REASON_*`), then merged (never duplicated) by
`baseline_chronology.merge_retrieved_claims`:

1. **Job-relevant claims** -- exactly `FitAssessment.retrieved_claim_ids` (the bounded, ranked set
   M5 already selected -- a superset of every claim any RequirementAssessment actually cited, since
   the disposition-coverage validator only ever accepts evidence from that same set), re-verified
   fresh against the database (still CONFIRMED/resume_eligible/non-static/on the ACTIVE revision --
   a claim retired or unconfirmed since M5 ran is excluded rather than blindly trusted from a
   stored ID list).
2. **Engagement anchor claims + confirmed language evidence** (D-035) -- a deterministic, zero-LLM
   baseline computed fresh from the current ACTIVE CandidateMemory by `baseline_chronology.py`,
   entirely independent of what AC_RANK selected for this specific job. This is what removes
   career-chronology completeness from probabilistic model selection.

Engagements: every currently `APPROVED` `CareerEngagement` -- computed directly from the database,
not from `FitAssessment.retrieved_engagement_ids` (which, while it happens to already include every
approved engagement per D-035's own diagnosis, is a stored snapshot that could in principle drift;
querying live is what actually guarantees "every APPROVED engagement always reaches the baseline
chronology" rather than relying on that incidental coincidence).

Rules: recomputed via the exact same bounded/deduplicated/capped selection M5 uses
(`candidate_matching.services.rule_selection.select_bounded_rules`), applied fresh rather than
stored, since rules are not claim-specific and recomputing is deterministic and idempotent.

Bounded, predictable size: the merged claim set (job-relevant + anchors + language) is still capped
by construction -- `MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT` per engagement, all language claims (typically
few) -- and the whole context's estimated size is checked against the same
`MAX_ESTIMATED_REQUEST_TOKENS` budget M5 already enforces; exceeding it after every cap is a
configuration/data problem to surface loudly (`RetrievalBudgetExceededError`), never something to
silently truncate.
"""

from __future__ import annotations

from candidate_matching.models import FitAssessment
from candidate_matching.services.retrieval_limits import (
    MAX_ESTIMATED_REQUEST_TOKENS,
    RetrievalBudgetExceededError,
    estimate_tokens,
)
from candidate_matching.services.retrieve import (
    RETRIEVAL_REASON_JOB_RELEVANT,
    NoActiveCandidateMemoryError,
    RetrievalContext,
    RetrievedClaim,
    RetrievedEngagement,
    get_active_candidate_memory,
)
from candidate_matching.services.rule_selection import select_bounded_rules
from candidate_memory.models import CareerEngagement, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES

from .baseline_chronology import BaselineChronologyResult, build_baseline_chronology, merge_retrieved_claims


def _job_relevant_claims(fit_assessment: FitAssessment, candidate_memory_id: int) -> list[RetrievedClaim]:
    claims = (
        MemoryClaim.objects.filter(
            claim_id__in=fit_assessment.retrieved_claim_ids,
            candidate_memory_id=candidate_memory_id,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            resume_eligible=True,
        )
        .exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES)
        .prefetch_related("engagement_mappings__career_engagement")
    )

    retrieved_claims: list[RetrievedClaim] = []
    for claim in claims:
        approved_engagement_ids = tuple(
            sorted(
                mapping.career_engagement.engagement_id
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement.approval_status == CareerEngagement.ApprovalStatus.APPROVED
            )
        )
        retrieved_claims.append(
            RetrievedClaim(
                claim_id=claim.claim_id,
                text=claim.canonical_text_en,
                claim_type=claim.claim_type,
                subject_scope=claim.subject_scope,
                approved_engagement_ids=approved_engagement_ids,
                retrieval_reasons=(RETRIEVAL_REASON_JOB_RELEVANT,),
            )
        )
    return retrieved_claims


def build_builder_context(fit_assessment: FitAssessment) -> RetrievalContext:
    try:
        candidate_memory = get_active_candidate_memory()
    except NoActiveCandidateMemoryError:
        candidate_memory = None

    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )

    job_relevant_claims = (
        _job_relevant_claims(fit_assessment, candidate_memory.pk) if candidate_memory is not None else []
    )

    if candidate_memory is not None:
        baseline = build_baseline_chronology(candidate_memory.pk, approved_engagements)
    else:
        baseline = BaselineChronologyResult(
            anchor_claims_by_engagement={}, engagements_with_no_eligible_evidence=[], language_claims=[]
        )

    retrieved_claims = merge_retrieved_claims(job_relevant_claims, baseline.all_claims)
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
        for engagement in approved_engagements
    ]
    retrieved_engagements.sort(key=lambda e: e.engagement_id)

    if candidate_memory is not None:
        requirement_texts = list(
            fit_assessment.based_on_jra.requirements.values_list("text", flat=True)
        )
        rule_result = select_bounded_rules(candidate_memory, requirement_texts)
        rules = rule_result.selected
    else:
        rules = []

    context_text_len = sum(len(c.text) for c in retrieved_claims)
    context_text_len += sum(len(r.text) for r in rules)
    context_text_len += sum(
        len(e.approved_role_title) + len(e.displayed_organization) for e in retrieved_engagements
    )
    estimated_tokens = estimate_tokens(" " * context_text_len)
    if estimated_tokens > MAX_ESTIMATED_REQUEST_TOKENS:
        raise RetrievalBudgetExceededError(
            f"Estimated Agent Builder request size ({estimated_tokens} tokens) exceeds "
            f"MAX_ESTIMATED_REQUEST_TOKENS={MAX_ESTIMATED_REQUEST_TOKENS} even after the D-035 "
            "baseline chronology's own per-engagement anchor cap was applied -- lower "
            "MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT or raise the token budget deliberately; this is "
            "never silently truncated."
        )

    return RetrievalContext(
        candidate_memory_id=candidate_memory.pk if candidate_memory is not None else 0,
        claims=retrieved_claims,
        engagements=retrieved_engagements,
        rules=rules,
        engagements_without_eligible_evidence=tuple(baseline.engagements_with_no_eligible_evidence),
    )
