"""Agent Candidate orchestration (M5): ties together the D-015 bounded retrieval pipeline
(`services/bounded_retrieval.py`), the local static-requirement assessors (D-019), the LLM-backed
narrative assessment, and the disposition-coverage validator into one persisted, versioned
`FitAssessment`.

Mirrors `job_intake.services.intake.run_intake`'s shape: every LLM call happens entirely outside
any transaction (so its `LLMCallLog` audit row always commits regardless of what happens next),
and persistence of the new version is one all-or-nothing `transaction.atomic()` block. Audit
hardening (2026-09-03): `JobApplication` is locked with `select_for_update()` for the whole
version-allocation-and-pointer-update block, closing the race where two concurrent runs could
compute the same next version number.
"""

from __future__ import annotations

import dataclasses

from django.db import IntegrityError, transaction
from django.db.models import Max

from candidate_memory.models import CareerEngagement
from job_applications.models import JobApplication

from ..models import FitAssessment, RequirementAssessment
from ..validators.disposition_coverage import AssessmentItemData, ensure_full_coverage, sanitize_items
from . import static_requirements
from .assess import assess_requirements
from .baseline_chronology import build_baseline_chronology, build_baseline_manifest, merge_retrieved_claims
from .bounded_retrieval import RankingFailedError, build_bounded_context
from .normalize import NormalizationFailedError
from .retrieval_limits import RetrievalBudgetExceededError
from .retrieve import RETRIEVAL_REASON_JOB_RELEVANT, get_active_candidate_memory


class AgentCandidateError(Exception):
    pass


class ConcurrentModificationError(Exception):
    """Raised when a version-number race is detected despite the `select_for_update()` lock --
    should be effectively unreachable in practice, but is handled as a clear domain error rather
    than an unhandled `IntegrityError`/500 if it ever occurs (e.g. a second process bypassing the
    lock via a different database connection pool configuration)."""


def build_fit_assessment(job_application) -> FitAssessment:
    if job_application.current_jra is None:
        raise AgentCandidateError(
            "JobApplication has no current JobRequirementAnalysis -- run Agent Jobber first."
        )

    candidate_memory = get_active_candidate_memory()
    jra = job_application.current_jra
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )

    requirements = list(jra.requirements.all())
    if not requirements:
        raise AgentCandidateError(
            f"JobRequirementAnalysis {jra.pk} (v{jra.version}) has zero JobRequirements -- Agent "
            "Candidate refuses to run against an empty analysis (2026-09-04 AJ hardening, D-022: "
            "this guards every current JRA, including one created before this check existed). "
            "Re-run Agent Jobber first."
        )
    ordered_ids = [requirement.requirement_id for requirement in requirements]

    local_items: list[AssessmentItemData] = []
    narrative_requirements: list[dict] = []
    for requirement in requirements:
        kind = static_requirements.classify(requirement.text)
        if kind is None:
            narrative_requirements.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "category": requirement.category,
                    "text": requirement.text,
                }
            )
            continue
        local_result = static_requirements.assess(kind, requirement.text, approved_engagements)
        local_items.append(
            AssessmentItemData(
                requirement_id=requirement.requirement_id,
                disposition=local_result.disposition,
                explanation=local_result.explanation,
                gap_or_limitation=local_result.gap_or_limitation,
                supporting_memory_claim_ids=[],
                supporting_engagement_ids=local_result.supporting_engagement_ids,
            )
        )

    try:
        retrieval, manifest = build_bounded_context(
            candidate_memory, narrative_requirements, posting_language=jra.posting_language
        )
    except (RankingFailedError, NormalizationFailedError, RetrievalBudgetExceededError) as exc:
        raise AgentCandidateError(f"Bounded retrieval failed: {exc}") from exc

    llm_result = assess_requirements(retrieval, narrative_requirements)
    if llm_result.is_error:
        raise AgentCandidateError(f"Agent Candidate assessment failed: {llm_result.error.message}")
    llm_items = [
        AssessmentItemData(
            requirement_id=item.requirement_id,
            disposition=item.disposition,
            explanation=item.explanation,
            gap_or_limitation=item.gap_or_limitation,
            supporting_memory_claim_ids=list(item.supporting_memory_claim_ids),
            supporting_engagement_ids=list(item.supporting_engagement_ids),
        )
        for item in llm_result.content.requirement_assessments
    ]

    valid_claim_ids = set(retrieval.claim_ids)
    valid_engagement_ids = set(retrieval.engagement_ids)
    # Local items already cite only approved-engagement IDs by construction, but are sanitized too
    # for defense in depth and so both paths go through the exact same evidence-attachment check.
    sanitized_items = sanitize_items(
        local_items + llm_items, valid_claim_ids=valid_claim_ids, valid_engagement_ids=valid_engagement_ids
    )
    all_items = ensure_full_coverage(sanitized_items, ordered_ids)

    # D-037 pinned-evidence-identity correction: the baseline-chronology manifest is computed here,
    # once, against this exact CandidateMemory revision and the CareerEngagement/
    # ClaimEngagementMapping state as it stands at this precise moment -- never recomputed at M6
    # (Agent Builder) time. `retrieval.claims` (M5's own job-relevant selection, already computed
    # by `build_bounded_context` above) is re-tagged JOB_RELEVANT and merged with the engagement-
    # anchor/language baseline so the persisted manifest is the *complete* evidence roster Agent
    # Builder will ever see for this FitAssessment.
    job_relevant_claims = [
        dataclasses.replace(claim, retrieval_reasons=(RETRIEVAL_REASON_JOB_RELEVANT,))
        for claim in retrieval.claims
    ]
    baseline = build_baseline_chronology(candidate_memory.pk, approved_engagements)
    merged_claims = merge_retrieved_claims(job_relevant_claims, baseline.all_claims)
    baseline_manifest = build_baseline_manifest(
        candidate_memory=candidate_memory,
        approved_engagements=approved_engagements,
        baseline=baseline,
        merged_claims=merged_claims,
    )

    try:
        with transaction.atomic():
            locked_application = JobApplication.objects.select_for_update().get(pk=job_application.pk)
            next_version = (
                locked_application.fit_assessments.aggregate(Max("version"))["version__max"] or 0
            ) + 1
            fit_assessment = FitAssessment.objects.create(
                job_application=locked_application,
                version=next_version,
                based_on_jra=jra,
                based_on_candidate_memory=candidate_memory,
                retrieved_claim_ids=retrieval.claim_ids,
                retrieved_engagement_ids=retrieval.engagement_ids,
                retrieval_manifest=manifest.as_dict(),
                baseline_chronology_manifest=baseline_manifest,
            )
            for item in all_items:
                RequirementAssessment.objects.create(
                    fit_assessment=fit_assessment,
                    requirement_id=item.requirement_id,
                    disposition=item.disposition,
                    supporting_memory_claim_ids=item.supporting_memory_claim_ids,
                    supporting_engagement_ids=item.supporting_engagement_ids,
                    explanation=item.explanation,
                    gap_or_limitation=item.gap_or_limitation,
                )
            locked_application.record_fit_assessment(fit_assessment)
    except IntegrityError as exc:
        raise ConcurrentModificationError(
            "A concurrent Agent Candidate run for this application raced this one -- retry."
        ) from exc

    # `locked_application` (fetched fresh under the lock) is what was actually updated -- mirror
    # the change onto the caller's own in-memory instance too, so `job_application` reflects it
    # immediately without requiring every caller to remember to `refresh_from_db()`.
    job_application.current_fit_assessment = fit_assessment
    job_application.current_fit_assessment_id = fit_assessment.pk

    return fit_assessment
