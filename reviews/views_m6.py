"""The operator-controlled M6 (AB_BUILD) inspect/edit/approve review page (2026-09-08, D-041).
M6 remains one LLM call, but the operator can inspect/edit its structured input before running,
and must explicitly approve its (possibly-edited) structured output before a ResumeDraft is ever
created -- mirroring reviews/views_m5.py's pattern for the single AB_BUILD stage."""

from __future__ import annotations

import difflib
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from job_applications.models import JobApplication
from llm_provider.models import ReasoningEffort, StageModelAssignment
from llm_provider.services.eligibility import grouped_model_choices
from llm_provider.services.model_selection import parse_requested_model_id, parse_requested_reasoning_effort
from resume_builder.models import AgentBuilderRun
from resume_builder.services import staged_build

_DOMAIN_ERRORS = (
    staged_build.AgentBuilderRunError,
    staged_build.ConcurrentAgentBuilderModificationError,
    staged_build.AgentBuilderValidationError,
)


def _pretty(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True) if data is not None else ""


def _diff(original, edited) -> str:
    if original is None or edited is None:
        return ""
    original_lines = _pretty(original).splitlines(keepends=True)
    edited_lines = _pretty(edited).splitlines(keepends=True)
    if original_lines == edited_lines:
        return ""
    return "".join(
        difflib.unified_diff(
            original_lines, edited_lines, fromfile="provider_output", tofile="operator_output"
        )
    )


@require_http_methods(["POST"])
def m6_start_view(request, application_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    try:
        run = staged_build.start_ab_run(application)
    except staged_build.AgentBuilderRunError as exc:
        messages.error(request, str(exc))
        return redirect("reviews:gate2", application_id=application.pk)
    return redirect("reviews:m6_review", application_id=application.pk, run_id=run.pk)


@require_http_methods(["GET", "POST"])
def m6_review_view(request, application_id: int, run_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    run = get_object_or_404(AgentBuilderRun, pk=run_id, job_application=application)
    error = None

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            lock_version = int(request.POST.get("lock_version", "-1"))
        except ValueError:
            lock_version = -1

        try:
            if action == "edit_input":
                raw = request.POST.get("edited_input_json", "")
                try:
                    edited_input = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise staged_build.AgentBuilderValidationError(
                        f"Edited input is not valid JSON: {exc}"
                    ) from exc
                staged_build.edit_ab_input(run, edited_input=edited_input, lock_version=lock_version)
                messages.success(request, "Input saved.")
                return redirect(request.path)

            if action == "reset_input":
                staged_build.reset_ab_input(run, lock_version=lock_version)
                messages.success(request, "Input reset to the prepared version.")
                return redirect(request.path)

            if action == "run":
                requested_model_id = parse_requested_model_id(request.POST.get("model", ""))
                requested_reasoning_effort = parse_requested_reasoning_effort(
                    request.POST.get("reasoning", "")
                )
                raw_budget = request.POST.get("output_budget", "").strip()
                requested_output_budget = int(raw_budget) if raw_budget else None
                staged_build.execute_ab_run(
                    run,
                    requested_model_id=requested_model_id,
                    requested_reasoning_effort=requested_reasoning_effort,
                    requested_output_budget=requested_output_budget,
                    correlation_id=str(application.pk),
                    lock_version=lock_version,
                )
                messages.success(request, "AB_BUILD executed.")
                return redirect(request.path)

            if action == "edit_output":
                raw = request.POST.get("edited_output_json", "")
                try:
                    edited_output = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise staged_build.AgentBuilderValidationError(
                        f"Edited output is not valid JSON: {exc}"
                    ) from exc
                staged_build.edit_ab_output(run, edited_output=edited_output, lock_version=lock_version)
                messages.success(request, "Output saved.")
                return redirect(request.path)

            if action == "approve":
                staged_build.approve_ab_run_and_create_draft(run, lock_version=lock_version)
                messages.success(request, "ResumeDraft created. Gate 2 is ready for review.")
                return redirect("reviews:gate2", application_id=application.pk)

            error = f"Unknown action: {action!r}"
        except _DOMAIN_ERRORS as exc:
            error = str(exc)

    run.refresh_from_db()
    groups = []
    for group_label, options in grouped_model_choices(StageModelAssignment.Stage.AB_BUILD):
        marked = [
            (pk, f"{label} [Current]" if run.selected_model_id == pk else label) for pk, label in options
        ]
        groups.append((group_label, marked))
    reasoning_choices = []
    for value, label in ReasoningEffort.choices:
        if value == run.reasoning_effort:
            label = f"{label} [Current]"
        reasoning_choices.append((value, label))

    return render(
        request,
        "reviews/m6_review.html",
        {
            "application": application,
            "run": run,
            "error": error,
            "effective_input_json": _pretty(run.effective_input),
            "is_input_edited": run.edited_input is not None,
            "provider_output_json": _pretty(run.provider_output),
            "operator_output_json": _pretty(run.operator_output),
            "diff_text": _diff(run.provider_output, run.operator_output),
            "model_groups": groups,
            "reasoning_choices": reasoning_choices,
        },
    )
