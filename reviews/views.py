from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from candidate_matching.services.fit_assessment import AgentCandidateError
from candidate_matching.services.retrieve import NoActiveCandidateMemoryError
from job_applications.models import (
    GateNotReadyError,
    InvalidPhaseTransitionError,
    JobApplication,
    StaleAssessmentError,
)
from job_intake.services.fetch import FetchError
from job_intake.services.intake import AnalysisFailedError, IntakeValidationError

from .models import ReviewFeedback
from .services import FeedbackTargetError, approve_gate1, run_agent_candidate, submit_gate1_feedback


@require_http_methods(["GET", "POST"])
def gate1_view(request, application_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    error = None

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "run_ac":
                run_agent_candidate(application)
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

    return render(
        request,
        "reviews/gate1.html",
        {
            "application": application,
            "jra": jra,
            "rows": rows,
            "fit_assessment": fit_assessment,
            "is_stale": is_stale,
            "feedback_history": feedback_history,
            "error": error,
        },
    )
