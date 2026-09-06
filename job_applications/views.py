from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import JobApplication
from .services import (
    InvalidOutcomeTransitionError,
    build_dashboard_row,
    compute_dashboard_summary,
    compute_freshness,
    list_dashboard_rows,
    resolve_next_action,
    set_application_outcome,
)


@require_http_methods(["GET"])
def dashboard_view(request):
    """The job-application tracking dashboard (requirements.md Sec 17): every `JobApplication`,
    its derived status, pipeline phase, application outcome, artifact existence/currency, whether
    review is required, staleness, and a link to the correct next screen -- all computed fresh
    from durable DB state on every request, never from an in-memory session."""
    rows = list_dashboard_rows()
    summary = compute_dashboard_summary(rows)
    return render(
        request, "job_applications/dashboard.html", {"rows": rows, "summary": summary}
    )


@require_http_methods(["GET", "POST"])
def detail_view(request, pk: int):
    """JobApplication detail: lets the operator resume work from the application's actual current
    state without having to work out which app/screen comes next themselves, and hosts the
    `application_outcome` action. Every transition here still goes through
    `job_applications.services`/the model's own guarded methods -- this view never sets
    `pipeline_phase`, a current-version pointer, or `application_outcome` directly."""
    application = get_object_or_404(JobApplication, pk=pk)
    error = None

    if request.method == "POST":
        outcome = request.POST.get("application_outcome", "")
        try:
            set_application_outcome(application, outcome)
            messages.success(request, f"Application outcome set to {outcome}.")
            return redirect("job_applications:detail", pk=application.pk)
        except InvalidOutcomeTransitionError as exc:
            error = str(exc)

    application.refresh_from_db()
    row = build_dashboard_row(application)
    freshness = compute_freshness(application)

    return render(
        request,
        "job_applications/detail.html",
        {
            "application": application,
            "row": row,
            "freshness": freshness,
            "next_action": resolve_next_action(application),
            "outcome_choices": JobApplication.ApplicationOutcome.choices,
            "error": error,
        },
    )
