from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from job_applications.models import JobApplication
from llm_provider.services.model_selection import ModelNotEligibleForStageError, parse_requested_model_id

from .forms import JobPostingIntakeForm
from .models import JobRequirement
from .services.fetch import FetchError
from .services.intake import AnalysisFailedError, IntakeValidationError, resolve_posting_source, run_intake


@require_http_methods(["GET", "POST"])
def intake_view(request):
    if request.method == "GET":
        return render(request, "job_intake/intake.html", {"form": JobPostingIntakeForm()})

    form = JobPostingIntakeForm(request.POST)
    submitted_url = request.POST.get("url", "")

    if not form.is_valid():
        return render(request, "job_intake/intake.html", {"form": form})

    try:
        requested_model_id = parse_requested_model_id(form.cleaned_data["model_aj"])
    except ModelNotEligibleForStageError as exc:
        return render(
            request, "job_intake/intake.html",
            {"form": form, "error": str(exc)},
        )

    try:
        resolved = resolve_posting_source(
            url=form.cleaned_data["url"], pasted_text=form.cleaned_data["pasted_text"]
        )
    except IntakeValidationError as exc:
        return render(
            request, "job_intake/intake.html",
            {"form": form, "error": str(exc)},
        )
    except FetchError as exc:
        # Zero LLM calls made -- offer the pasted-text fallback with the entered URL preserved.
        return render(
            request, "job_intake/intake.html",
            {
                "form": JobPostingIntakeForm(
                    initial={"url": submitted_url, "model_aj": form.cleaned_data["model_aj"]}
                ),
                "fetch_error": exc.safe_message,
                "offer_paste_fallback": True,
            },
        )

    try:
        application = run_intake(resolved, requested_model_id=requested_model_id)
    except (AnalysisFailedError, ModelNotEligibleForStageError) as exc:
        return render(
            request, "job_intake/intake.html",
            {
                "form": JobPostingIntakeForm(
                    initial={"url": submitted_url, "model_aj": form.cleaned_data["model_aj"]}
                ),
                "error": f"Analysis failed: {exc}",
            },
        )

    messages.success(request, "Job posting analyzed.")
    return redirect("job_intake:analysis_detail", application_id=application.pk)


@require_http_methods(["GET"])
def analysis_detail_view(request, application_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    jra = application.current_jra
    requirements = jra.requirements.all() if jra else JobRequirement.objects.none()
    # Read-only display check (2026-09-04 AJ hardening, D-022) -- never mutates the JRA, including
    # a pre-existing one saved before the zero-requirement sanity check existed (e.g. legacy JRA
    # id=9): a JRA with no requirements has nothing for Gate 1/Agent Candidate to assess and is
    # flagged here rather than only failing later when M5 is attempted.
    is_incomplete = jra is not None and not requirements.exists()
    return render(
        request, "job_intake/analysis_detail.html",
        {
            "application": application,
            "jra": jra,
            "requirements": requirements,
            "is_incomplete": is_incomplete,
        },
    )
