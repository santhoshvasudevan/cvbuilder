from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from candidate_matching.services.fit_assessment import AgentCandidateError
from candidate_matching.services.fit_assessment import (
    ConcurrentModificationError as FitAssessmentConcurrentModificationError,
)
from candidate_matching.services.retrieve import NoActiveCandidateMemoryError
from candidate_memory.models import MemoryClaim
from job_applications.models import (
    GateNotReadyError,
    InvalidPhaseTransitionError,
    JobApplication,
    StaleAssessmentError,
)
from job_intake.services.fetch import FetchError
from job_intake.services.intake import AnalysisFailedError, IntakeValidationError
from job_intake.services.intake import ConcurrentModificationError as JraConcurrentModificationError
from llm_provider.models import ReasoningEffort, StageModelAssignment
from llm_provider.services.console import build_stage_card
from llm_provider.services.eligibility import grouped_model_choices
from llm_provider.services.model_selection import (
    ModelNotEligibleForStageError,
    ReasoningNotEligibleForStageError,
    parse_requested_model_id,
    parse_requested_reasoning_effort,
)
from resume_builder.services.build import (
    ConcurrentModificationError as ResumeDraftConcurrentModificationError,
)
from resume_builder.services.build import ResumeBuilderError

from .models import ReviewFeedback
from .services import (
    FeedbackTargetError,
    approve_gate1,
    approve_gate2,
    run_agent_builder,
    run_agent_candidate,
    submit_gate1_feedback,
    submit_gate2_feedback,
)

GATE1_MODEL_STAGES = (
    StageModelAssignment.Stage.AC_NORMALIZE,
    StageModelAssignment.Stage.AC_RANK,
    StageModelAssignment.Stage.AC_MATCH,
    StageModelAssignment.Stage.AJ_ANALYZE,
)
GATE2_MODEL_STAGES = (StageModelAssignment.Stage.AB_BUILD,)


def _stage_cards(application: JobApplication, stages) -> dict:
    """Builds the operator-facing stage-card context (attempt history, tokens, latency, paid/free,
    sanitized error guidance -- CLAUDE.md's M5/M6 stage-console requirement) for every stage in
    `stages`, scoped to this one `application`'s own attempts."""
    correlation_id = str(application.pk)
    return {stage: build_stage_card(stage, correlation_id=correlation_id) for stage in stages}


def _stage_model_choices(stages) -> dict:
    """Builds the per-stage selector context (`{stage: {"groups": [(provider_label, [(pk, label),
    ...]), ...], "current_default": model_or_None}}`) both gate templates render one
    provider-grouped `<select>` from (2026-09-07, D-039 correction: provider-aware selection --
    "OpenAI — Direct API" and "OpenRouter" render as distinct `<optgroup>`s) -- one shared helper
    so Gate 1's four independently-configurable calls (AJ_ANALYZE/AC_NORMALIZE/AC_RANK/AC_MATCH)
    and Gate 2's one (AB_BUILD) are built the exact same way."""
    context = {}
    for stage in stages:
        default_assignment = (
            StageModelAssignment.objects.select_related("model__provider").filter(stage=stage).first()
        )
        default_model = default_assignment.model if default_assignment else None
        groups = []
        for group_label, options in grouped_model_choices(stage):
            marked = [
                (pk, f"{label} [Default]" if default_model is not None and pk == default_model.pk else label)
                for pk, label in options
            ]
            groups.append((group_label, marked))
        context[stage] = {"groups": groups, "current_default": default_model}
    return context


def _stage_reasoning_choices(stages) -> dict:
    """Mirrors `_stage_model_choices` for the reasoning-effort selector (2026-09-07, paid GPT-5.4
    model defaults): one `<select>` per stage, always offering the complete `ReasoningEffort` enum
    (the resolved model may not support reasoning at all, but that is enforced server-side at
    execution time -- `llm_provider.services.model_selection.resolve_stage_model` -- never by
    hiding the selector, since the model itself may also be about to be overridden in the same
    submission)."""
    context = {}
    for stage in stages:
        default_assignment = StageModelAssignment.objects.filter(stage=stage).first()
        default_effort = default_assignment.default_reasoning_effort if default_assignment else ""
        choices = []
        for value, label in ReasoningEffort.choices:
            if value == default_effort:
                label = f"{label} [Default]"
            choices.append((value, label))
        context[stage] = {"choices": choices, "current_default": default_effort}
    return context


@require_http_methods(["GET", "POST"])
def gate1_view(request, application_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    error = None

    submitted_models = {
        StageModelAssignment.Stage.AC_NORMALIZE: request.POST.get("model_ac_normalize", ""),
        StageModelAssignment.Stage.AC_RANK: request.POST.get("model_ac_rank", ""),
        StageModelAssignment.Stage.AC_MATCH: request.POST.get("model_ac_match", ""),
        StageModelAssignment.Stage.AJ_ANALYZE: request.POST.get("model_aj", ""),
    }
    submitted_reasoning = {
        StageModelAssignment.Stage.AC_NORMALIZE: request.POST.get("reasoning_ac_normalize", ""),
        StageModelAssignment.Stage.AC_RANK: request.POST.get("reasoning_ac_rank", ""),
        StageModelAssignment.Stage.AC_MATCH: request.POST.get("reasoning_ac_match", ""),
        StageModelAssignment.Stage.AJ_ANALYZE: request.POST.get("reasoning_aj", ""),
    }

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            ac_requested_models = {
                stage: parse_requested_model_id(submitted_models[stage])
                for stage in (
                    StageModelAssignment.Stage.AC_NORMALIZE,
                    StageModelAssignment.Stage.AC_RANK,
                    StageModelAssignment.Stage.AC_MATCH,
                )
            }
            ac_requested_reasoning = {
                stage: parse_requested_reasoning_effort(submitted_reasoning[stage])
                for stage in (
                    StageModelAssignment.Stage.AC_NORMALIZE,
                    StageModelAssignment.Stage.AC_RANK,
                    StageModelAssignment.Stage.AC_MATCH,
                )
            }
            if action == "run_ac":
                run_agent_candidate(
                    application,
                    requested_models=ac_requested_models,
                    requested_reasoning_efforts=ac_requested_reasoning,
                )
                messages.success(request, "Agent Candidate assessment produced.")
            elif action == "feedback":
                target = request.POST.get("target", "")
                comments = request.POST.get("comments", "")
                submit_gate1_feedback(
                    application,
                    target=target,
                    comments=comments,
                    url=request.POST.get("url", ""),
                    pasted_text=request.POST.get("pasted_text", ""),
                    requested_model_id=parse_requested_model_id(
                        submitted_models[StageModelAssignment.Stage.AJ_ANALYZE]
                    ),
                    requested_reasoning_effort=parse_requested_reasoning_effort(
                        submitted_reasoning[StageModelAssignment.Stage.AJ_ANALYZE]
                    ),
                    requested_models=ac_requested_models,
                    requested_reasoning_efforts=ac_requested_reasoning,
                )
                messages.success(request, f"Feedback recorded and {target} re-run.")
            elif action == "approve":
                approve_gate1(application)
                messages.success(request, "Gate 1 approved -- advanced to Preparation.")
            else:
                error = f"Unknown action: {action!r}"
        except (
            FeedbackTargetError,
            AgentCandidateError,
            NoActiveCandidateMemoryError,
            InvalidPhaseTransitionError,
            GateNotReadyError,
            StaleAssessmentError,
            IntakeValidationError,
            AnalysisFailedError,
            FitAssessmentConcurrentModificationError,
            JraConcurrentModificationError,
            ModelNotEligibleForStageError,
            ReasoningNotEligibleForStageError,
        ) as exc:
            error = str(exc)
        except FetchError as exc:
            error = exc.safe_message

        if error is None:
            return redirect("reviews:gate1", application_id=application.pk)

    application.refresh_from_db()
    jra = application.current_jra
    requirements = list(jra.requirements.all()) if jra else []
    fit_assessment = application.current_fit_assessment
    assessments_by_requirement = {}
    is_stale = False
    if fit_assessment is not None:
        assessments_by_requirement = {
            row.requirement_id: row for row in fit_assessment.requirement_assessments.all()
        }
        is_stale = jra is not None and fit_assessment.based_on_jra_id != jra.pk

    rows = [
        {"requirement": requirement, "assessment": assessments_by_requirement.get(requirement.requirement_id)}
        for requirement in requirements
    ]
    feedback_history = ReviewFeedback.objects.filter(
        job_application=application, gate=ReviewFeedback.Gate.GATE_1
    )
    retrieval_manifest = fit_assessment.retrieval_manifest if fit_assessment is not None else None

    return render(
        request,
        "reviews/gate1.html",
        {
            "application": application,
            "jra": jra,
            "requirements": requirements,
            "rows": rows,
            "fit_assessment": fit_assessment,
            "retrieval_manifest": retrieval_manifest,
            "is_stale": is_stale,
            "feedback_history": feedback_history,
            "error": error,
            "model_stages": _stage_model_choices(GATE1_MODEL_STAGES),
            "submitted_models": submitted_models,
            "reasoning_stages": _stage_reasoning_choices(GATE1_MODEL_STAGES),
            "submitted_reasoning": submitted_reasoning,
            "stage_cards": _stage_cards(application, GATE1_MODEL_STAGES),
        },
    )


@require_http_methods(["GET", "POST"])
def gate2_view(request, application_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    error = None
    submitted_model_ab = request.POST.get("model_ab", "")
    submitted_reasoning_ab = request.POST.get("reasoning_ab", "")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            requested_model_id = parse_requested_model_id(submitted_model_ab)
            requested_reasoning_effort = parse_requested_reasoning_effort(submitted_reasoning_ab)
            if action == "run_ab":
                run_agent_builder(
                    application,
                    requested_model_id=requested_model_id,
                    requested_reasoning_effort=requested_reasoning_effort,
                )
                messages.success(request, "Agent Builder draft produced.")
            elif action == "feedback":
                submit_gate2_feedback(
                    application,
                    comments=request.POST.get("comments", ""),
                    requested_model_id=requested_model_id,
                    requested_reasoning_effort=requested_reasoning_effort,
                )
                messages.success(request, "Feedback recorded and Agent Builder re-run.")
            elif action == "approve":
                approve_gate2(application)
                messages.success(request, "Gate 2 approved -- resume is Ready.")
            else:
                error = f"Unknown action: {action!r}"
        except (
            ResumeBuilderError,
            NoActiveCandidateMemoryError,
            InvalidPhaseTransitionError,
            GateNotReadyError,
            StaleAssessmentError,
            ResumeDraftConcurrentModificationError,
            ModelNotEligibleForStageError,
            ReasoningNotEligibleForStageError,
            FeedbackTargetError,
        ) as exc:
            error = str(exc)

        if error is None:
            return redirect("reviews:gate2", application_id=application.pk)

    application.refresh_from_db()
    fit_assessment = application.current_fit_assessment
    draft = application.current_resume_draft
    is_stale = False
    upstream_stale = False
    if draft is not None:
        is_stale = fit_assessment is not None and draft.based_on_fit_assessment_id != fit_assessment.pk
    if fit_assessment is not None:
        upstream_stale = fit_assessment.based_on_jra_id != application.current_jra_id
    elements = list(draft.elements.all()) if draft is not None else []

    # Evidence-inspection support: which CandidateMemory *version* each cited claim lives on (for
    # a direct link to its quotation/detail page), and whether each claim is engagement-matched or
    # merely global/supplementary for the element it's cited on.
    all_claim_ids = {claim_id for element in elements for claim_id in element.supporting_memory_claim_ids}
    claim_versions = dict(
        MemoryClaim.objects.filter(claim_id__in=all_claim_ids)
        .values_list("claim_id", "candidate_memory__version")
    )
    approved_engagements_by_claim: dict[str, set[str]] = {}
    if all_claim_ids:
        for claim in MemoryClaim.objects.filter(claim_id__in=all_claim_ids).prefetch_related(
            "engagement_mappings__career_engagement"
        ):
            approved_engagements_by_claim[claim.claim_id] = {
                mapping.career_engagement.engagement_id
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
            }

    evidence_rows = []
    for element in elements:
        claim_rows = []
        for claim_id in element.supporting_memory_claim_ids:
            is_global = element.engagement_id not in approved_engagements_by_claim.get(claim_id, set())
            claim_rows.append(
                {
                    "claim_id": claim_id,
                    "candidate_memory_version": claim_versions.get(claim_id),
                    "is_global": is_global,
                }
            )
        evidence_rows.append({"element": element, "claim_rows": claim_rows})

    feedback_history = ReviewFeedback.objects.filter(
        job_application=application, gate=ReviewFeedback.Gate.GATE_2
    )

    return render(
        request,
        "reviews/gate2.html",
        {
            "application": application,
            "draft": draft,
            "evidence_rows": evidence_rows,
            "is_stale": is_stale,
            "upstream_stale": upstream_stale,
            "feedback_history": feedback_history,
            "error": error,
            "model_stages": _stage_model_choices(GATE2_MODEL_STAGES),
            "submitted_model_ab": submitted_model_ab,
            "reasoning_stages": _stage_reasoning_choices(GATE2_MODEL_STAGES),
            "submitted_reasoning_ab": submitted_reasoning_ab,
            "stage_cards": _stage_cards(application, GATE2_MODEL_STAGES),
        },
    )
