from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import JobApplication
from .services import (
    InvalidOutcomeTransitionError,
    RevisionNotAuthorizedError,
    begin_new_version_from_ready,
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


@require_http_methods(["POST"])
def begin_revision_view(request, pk: int):
    """The canonical, operator-facing UI action for the READY-revision workflow (D-037): POST
    only, CSRF-protected (the default Django middleware, exercised by the form's `{% csrf_token
    %}` in `detail.html`), and requires the application to actually be `READY` -- every guarantee
    is re-checked fresh here via `job_applications.services.begin_new_version_from_ready`, never
    trusted from what the page happened to render. A GET (even to this URL) is rejected outright
    by `require_http_methods`; there is no way to trigger this action without a POST."""
    application = get_object_or_404(JobApplication, pk=pk)
    try:
        begin_new_version_from_ready(application)
        messages.success(
            request,
            "A new revision has begun: this application is back at Gate 1 -- re-run Agent "
            "Candidate to create a new FitAssessment version and review it there.",
        )
    except RevisionNotAuthorizedError as exc:
        messages.error(request, str(exc))
    return redirect("job_applications:detail", pk=application.pk)
