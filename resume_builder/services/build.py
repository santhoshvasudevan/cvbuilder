"""Agent Builder orchestration (M6): ties together the freshness/gate precondition, retrieval
(reused from `candidate_matching` -- the same bounded, D-019-respecting subset), the LLM-backed
structured generation, the no-fabrication validator, and deterministic rendering into one
persisted, versioned `ResumeDraft`.

Mirrors `candidate_matching.services.fit_assessment.build_fit_assessment`'s shape: the LLM call
happens entirely outside any transaction (so its `LLMCallLog` audit row always commits regardless
of what happens next), rendering is computed in memory from already-validated data before any
database write, and persistence of the new version is one all-or-nothing `transaction.atomic()`
block -- a build that fails validation persists nothing at all.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from candidate_matching.services.retrieve import get_active_candidate_memory, retrieve_context
from job_applications.models import JobApplication

from ..models import ResumeDraft, ResumeElement
from ..rendering.markdown import render_resume_markdown
from ..validators.no_fabrication import NoFabricationError, validate_and_flatten
from .generate import generate_resume_content


class ResumeBuilderError(Exception):
    pass


def build_resume_draft(job_application) -> ResumeDraft:
    fit_assessment = job_application.current_fit_assessment
    if fit_assessment is None:
        raise ResumeBuilderError(
            "No FitAssessment exists -- run Agent Candidate and approve Gate 1 first."
        )
    if job_application.pipeline_phase not in (
        JobApplication.PipelinePhase.PREPARATION,
        JobApplication.PipelinePhase.READY,
    ):
        raise ResumeBuilderError("Gate 1 must be approved before Agent Builder can run.")
    if fit_assessment.based_on_jra_id != job_application.current_jra_id:
        raise ResumeBuilderError(
            "The current FitAssessment is stale relative to the current JobRequirementAnalysis -- "
            "re-run and re-approve Gate 1 before building a resume draft."
        )

    candidate_memory = get_active_candidate_memory()
    retrieval = retrieve_context(candidate_memory)
    jra = job_application.current_jra
    requirement_assessments = list(fit_assessment.requirement_assessments.all())

    llm_result = generate_resume_content(jra, requirement_assessments, retrieval)
    if llm_result.is_error:
        raise ResumeBuilderError(f"Agent Builder generation failed: {llm_result.error.message}")
    ab_output = llm_result.content

    try:
        elements = validate_and_flatten(ab_output, retrieval=retrieval)
    except NoFabricationError as exc:
        raise ResumeBuilderError(str(exc)) from exc

    language = ab_output.positioning_guidance.resume_language or "en"
    rendered_markdown = render_resume_markdown(
        elements,
        recommended_title=ab_output.target_positioning.recommended_title,
        retrieval=retrieval,
        language=language,
    )

    with transaction.atomic():
        next_version = (
            job_application.resume_drafts.aggregate(Max("version"))["version__max"] or 0
        ) + 1
        draft = ResumeDraft.objects.create(
            job_application=job_application,
            version=next_version,
            based_on_fit_assessment=fit_assessment,
            retrieved_claim_ids=retrieval.claim_ids,
            retrieved_engagement_ids=retrieval.engagement_ids,
            recommended_title=ab_output.target_positioning.recommended_title,
            title_options=list(ab_output.target_positioning.title_options),
            skill_categories=[category.model_dump() for category in ab_output.skill_categories],
            positioning_guidance=ab_output.positioning_guidance.model_dump(),
            rendered_markdown=rendered_markdown,
        )
        for element in elements:
            ResumeElement.objects.create(
                resume_draft=draft,
                section=element.section,
                engagement_id=element.engagement_id,
                order=element.order,
                text=element.text,
                supporting_memory_claim_ids=element.supporting_memory_claim_ids,
                matched_job_requirement_ids=element.matched_job_requirement_ids,
            )
        job_application.record_resume_draft(draft)

    return draft
