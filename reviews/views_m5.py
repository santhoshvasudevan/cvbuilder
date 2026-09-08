"""The operator-controlled, persistent, resumable M5 staged workflow UI (2026-09-08, D-041):
three individually-authorized pages (AC_NORMALIZE/AC_RANK/AC_MATCH), each requiring an explicit
operator click to make its one provider call. Every mutating action here is POST-only, CSRF-
protected (the default Django middleware + `{% csrf_token %}` in every form), and redirects on
success (POST/Redirect/GET) -- mirroring `reviews/views.py`'s existing gate1_view/gate2_view
pattern exactly. All business logic lives in `candidate_matching.services.staged_run`; this module
only translates HTTP <-> that service layer, never re-implements a service-layer rule."""

from __future__ import annotations

import difflib
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from candidate_matching.models import AgentCandidateRun, AgentCandidateStage
from candidate_matching.services import staged_run
from job_applications.models import JobApplication
from llm_provider.models import ReasoningEffort
from llm_provider.services.eligibility import grouped_model_choices
from llm_provider.services.model_selection import parse_requested_model_id, parse_requested_reasoning_effort

Stage = AgentCandidateStage.Stage
Status = AgentCandidateStage.Status

_STAGE_BY_SLUG = {"normalize": Stage.AC_NORMALIZE, "rank": Stage.AC_RANK, "match": Stage.AC_MATCH}
_SLUG_BY_STAGE = {v: k for k, v in _STAGE_BY_SLUG.items()}
_STAGE_TITLE = {
    Stage.AC_NORMALIZE: "Requirement Normalization (AC_NORMALIZE)",
    Stage.AC_RANK: "Relevance Ranking (AC_RANK)",
    Stage.AC_MATCH: "Fit Assessment (AC_MATCH)",
}

_DOMAIN_ERRORS = (
    staged_run.StagedRunError,
    staged_run.StageSequenceError,
    staged_run.ConcurrentStageModificationError,
    staged_run.StageValidationError,
)


def _pretty(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True) if data is not None else ""


def _diff(original: dict | None, edited: dict | None) -> str:
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
def m5_start_view(request, application_id: int):
    """Zero side effects beyond creating the run + preparing AC_NORMALIZE's input (Phase C.1) --
    never a provider call. Redirects straight to the AC_NORMALIZE page."""
    application = get_object_or_404(JobApplication, pk=application_id)
    try:
        run = staged_run.start_run(application)
    except staged_run.StagedRunError as exc:
        messages.error(request, str(exc))
        return redirect("reviews:gate1", application_id=application.pk)
    return redirect("reviews:m5_normalize", application_id=application.pk, run_id=run.pk)


@require_http_methods(["POST"])
def m5_cancel_view(request, application_id: int, run_id: int):
    application = get_object_or_404(JobApplication, pk=application_id)
    run = get_object_or_404(AgentCandidateRun, pk=run_id, job_application=application)
    try:
        staged_run.cancel_run(run)
        messages.success(request, "M5 run cancelled.")
    except staged_run.StagedRunError as exc:
        messages.error(request, str(exc))
    return redirect("reviews:gate1", application_id=application.pk)


def _stage_model_and_reasoning_choices(stage_name: str, stage: AgentCandidateStage) -> tuple[dict, dict]:
    groups = []
    for group_label, options in grouped_model_choices(stage_name):
        marked = [
            (pk, f"{label} [Current]" if stage.selected_model_id == pk else label) for pk, label in options
        ]
        groups.append((group_label, marked))
    model_choices = {"groups": groups, "current": stage.selected_model_id}

    choices = []
    for value, label in ReasoningEffort.choices:
        if value == stage.reasoning_effort:
            label = f"{label} [Current]"
        choices.append((value, label))
    reasoning_choices = {"choices": choices, "current": stage.reasoning_effort}
    return model_choices, reasoning_choices


def _render_stage(
    request,
    application: JobApplication,
    run: AgentCandidateRun,
    stage_name: str,
    *,
    error=None,
    submitted=None,
):
    stage = staged_run.get_stage(run, stage_name)
    all_stages = list(run.stages.order_by("stage_order"))
    model_choices, reasoning_choices = _stage_model_and_reasoning_choices(stage_name, stage)

    effective_input = stage.effective_input
    diff_text = _diff(stage.provider_output, stage.operator_output)
    submitted = submitted or {}

    is_stale = run.based_on_jra_id != application.current_jra_id

    return render(
        request,
        "reviews/m5_stage.html",
        {
            "application": application,
            "run": run,
            "all_stages": all_stages,
            "stage": stage,
            "stage_slug": _SLUG_BY_STAGE[stage_name],
            "stage_title": _STAGE_TITLE[stage_name],
            "is_normalize": stage_name == Stage.AC_NORMALIZE,
            "is_rank": stage_name == Stage.AC_RANK,
            "is_match": stage_name == Stage.AC_MATCH,
            "is_stale": is_stale,
            "effective_input_json": _pretty(effective_input),
            "prepared_input_json": _pretty(stage.prepared_input),
            "is_input_edited": stage.edited_input is not None,
            "provider_output_json": _pretty(stage.provider_output),
            "operator_output_json": submitted.get("operator_output_json", _pretty(stage.operator_output)),
            "diff_text": diff_text,
            "model_choices": model_choices,
            "reasoning_choices": reasoning_choices,
            "error": error,
            "submitted": submitted,
            "next_stage_url_name": (
                "reviews:m5_" + _SLUG_BY_STAGE[staged_run._NEXT_STAGE[stage_name]]
                if stage_name in staged_run._NEXT_STAGE else None
            ),
        },
    )


def _stage_view(request, application_id: int, run_id: int, stage_name: str):
    application = get_object_or_404(JobApplication, pk=application_id)
    run = get_object_or_404(AgentCandidateRun, pk=run_id, job_application=application)
    stage = staged_run.get_stage(run, stage_name)
    error = None
    submitted = None

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
                    raise staged_run.StageValidationError(f"Edited input is not valid JSON: {exc}") from exc
                staged_run.edit_stage_input(stage, edited_input=edited_input, lock_version=lock_version)
                messages.success(request, "Input saved.")
                return redirect(request.path)

            if action == "reset_input":
                staged_run.reset_stage_input(stage, lock_version=lock_version)
                messages.success(request, "Input reset to the prepared (deterministic) version.")
                return redirect(request.path)

            if action == "run":
                requested_model_id = parse_requested_model_id(request.POST.get("model", ""))
                requested_reasoning_effort = parse_requested_reasoning_effort(
                    request.POST.get("reasoning", "")
                )
                raw_budget = request.POST.get("output_budget", "").strip()
                requested_output_budget = int(raw_budget) if raw_budget else None
                staged_run.execute_stage(
                    stage,
                    requested_model_id=requested_model_id,
                    requested_reasoning_effort=requested_reasoning_effort,
                    requested_output_budget=requested_output_budget,
                    correlation_id=str(application.pk),
                    lock_version=lock_version,
                )
                messages.success(request, f"{stage_name} executed.")
                return redirect(request.path)

            if action == "edit_output":
                raw = request.POST.get("edited_output_json", "")
                try:
                    edited_output = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise staged_run.StageValidationError(f"Edited output is not valid JSON: {exc}") from exc
                staged_run.edit_stage_output(stage, edited_output=edited_output, lock_version=lock_version)
                messages.success(request, "Output saved.")
                return redirect(request.path)

            if action == "approve":
                stage = staged_run.approve_stage(stage, lock_version=lock_version)
                if stage_name == Stage.AC_MATCH:
                    staged_run.finalize_run(run, lock_version=stage.lock_version)
                    messages.success(request, "Fit Assessment created. Gate 1 is ready for review.")
                    return redirect("reviews:gate1", application_id=application.pk)
                messages.success(request, "Approved -- next stage prepared.")
                next_stage = staged_run._NEXT_STAGE[stage_name]
                return redirect(
                    "reviews:m5_" + _SLUG_BY_STAGE[next_stage],
                    application_id=application.pk,
                    run_id=run.pk,
                )

            if action == "reconcile":
                staged_run.reconcile_stale_running_stage(stage)
                messages.success(request, "Stage reset to FAILED -- rerun explicitly when ready.")
                return redirect(request.path)

            error = f"Unknown action: {action!r}"
        except _DOMAIN_ERRORS as exc:
            error = str(exc)
            submitted = {"operator_output_json": request.POST.get("edited_output_json", "")}

    stage.refresh_from_db()
    return _render_stage(request, application, run, stage_name, error=error, submitted=submitted)


@require_http_methods(["GET", "POST"])
def m5_normalize_view(request, application_id: int, run_id: int):
    return _stage_view(request, application_id, run_id, Stage.AC_NORMALIZE)


@require_http_methods(["GET", "POST"])
def m5_rank_view(request, application_id: int, run_id: int):
    return _stage_view(request, application_id, run_id, Stage.AC_RANK)


@require_http_methods(["GET", "POST"])
def m5_match_view(request, application_id: int, run_id: int):
    return _stage_view(request, application_id, run_id, Stage.AC_MATCH)
