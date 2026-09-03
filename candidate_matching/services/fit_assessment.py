"""Agent Candidate orchestration (M5): ties together retrieval, the local static-requirement
assessors (D-019), the LLM-backed narrative assessment, and the disposition-coverage validator
into one persisted, versioned `FitAssessment`.

Mirrors `job_intake.services.intake.run_intake`'s shape: the LLM call happens entirely outside any
transaction (so its `LLMCallLog` audit row always commits regardless of what happens next), and
persistence of the new version is one all-or-nothing `transaction.atomic()` block.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from candidate_memory.models import CareerEngagement

from ..models import FitAssessment, RequirementAssessment
from ..validators.disposition_coverage import AssessmentItemData, ensure_full_coverage, sanitize_items
from . import static_requirements
from .assess import assess_requirements
from .retrieve import get_active_candidate_memory, retrieve_context


class AgentCandidateError(Exception):
    pass


def build_fit_assessment(job_application) -> FitAssessment:
    jra = job_application.current_jra
    if jra is None:
        raise AgentCandidateError(
            "JobApplication has no current JobRequirementAnalysis -- run Agent Jobber first."
        )

    candidate_memory = get_active_candidate_memory()
    retrieval = retrieve_context(candidate_memory)
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )

    requirements = list(jra.requirements.all())
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

    with transaction.atomic():
        next_version = (
            job_application.fit_assessments.aggregate(Max("version"))["version__max"] or 0
        ) + 1
        fit_assessment = FitAssessment.objects.create(
            job_application=job_application,
            version=next_version,
            based_on_jra=jra,
            retrieved_claim_ids=retrieval.claim_ids,
            retrieved_engagement_ids=retrieval.engagement_ids,
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
        job_application.record_fit_assessment(fit_assessment)

    return fit_assessment
