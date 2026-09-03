"""Agent Builder orchestration (M6): ties together the freshness/gate precondition, M6's own
bounded `BuilderContext` (`services/context.py` -- built strictly from what the current FitAssessment
already selected, never the full CandidateMemory, audit hardening 2026-09-03), the LLM-backed
structured generation, the no-fabrication validator, and deterministic rendering into one
persisted, versioned `ResumeDraft`.

Mirrors `candidate_matching.services.fit_assessment.build_fit_assessment`'s shape: the LLM call
happens entirely outside any transaction (so its `LLMCallLog` audit row always commits regardless
of what happens next), rendering is computed in memory from already-validated data before any
database write, and persistence of the new version is one all-or-nothing `transaction.atomic()`
block -- a build that fails validation persists nothing at all. Audit hardening (2026-09-03):
`JobApplication` is locked with `select_for_update()` for the whole version-allocation-and-
pointer-update block, closing the same version race M5 closes.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Max

from job_applications.models import JobApplication

from ..models import ResumeDraft, ResumeElement
from ..rendering.markdown import render_resume_markdown
from ..validators.no_fabrication import NoFabricationError, validate_and_flatten
from .context import build_builder_context
from .generate import generate_resume_content


class ResumeBuilderError(Exception):
    pass


class ConcurrentModificationError(Exception):
    """See `candidate_matching.services.fit_assessment.ConcurrentModificationError` -- same
    rationale, same (effectively unreachable given the `select_for_update()` lock) safety net."""


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

    jra = job_application.current_jra
    retrieval = build_builder_context(fit_assessment)
    if fit_assessment.retrieved_claim_ids and not retrieval.claims:
        raise ResumeBuilderError(
            "None of this FitAssessment's retrieved claims are still confirmed, resume-eligible, "
            "and on the ACTIVE CandidateMemory -- the underlying memory has changed since Gate 1 "
            "was approved. Re-run Agent Candidate against the current CandidateMemory first."
        )
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

    try:
        with transaction.atomic():
            locked_application = JobApplication.objects.select_for_update().get(pk=job_application.pk)
            next_version = (
                locked_application.resume_drafts.aggregate(Max("version"))["version__max"] or 0
            ) + 1
            draft = ResumeDraft.objects.create(
                job_application=locked_application,
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
            locked_application.record_resume_draft(draft)
    except IntegrityError as exc:
        raise ConcurrentModificationError(
            "A concurrent Agent Builder run for this application raced this one -- retry."
        ) from exc

    job_application.current_resume_draft = draft
    job_application.current_resume_draft_id = draft.pk

    return draft
