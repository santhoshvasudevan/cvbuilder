"""Operator-controlled M6 (AB_BUILD) inspect/edit/approve workflow (2026-09-08, D-041).

`services/build.py`'s `build_resume_draft` remains the programmatic, run-and-persist-in-one-call
entry point (kept for the existing fake-adapter test suite and any future non-interactive use).
The normal operator-facing UI drives an `AgentBuilderRun` instead: prepare (zero-call) -> execute
(exactly one provider call) -> inspect/edit/validate (zero-call) -> approve (creates the
`ResumeDraft`, only then). A successful provider call never creates a `ResumeDraft` by itself.

Reuses -- never duplicates -- `services/context.build_builder_context` (deterministic bounded
context from a pinned `FitAssessment`), `services/generate.py` (the one LLM call),
`validators/no_fabrication.py`/`validators/completeness.py`, and `rendering/markdown.py`.
"""

from __future__ import annotations

import copy
import hashlib
import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from candidate_matching.services.baseline_chronology import (
    InvalidBaselineManifestError,
    LegacyFitAssessmentManifestError,
)
from candidate_matching.services.retrieve import (
    RetrievalContext,
    RetrievedClaim,
    RetrievedEngagement,
    RetrievedRule,
)
from job_applications.models import JobApplication
from llm_provider.adapters import get_adapter_for_stage
from llm_provider.errors import sanitize_error_message

from ..models import AgentBuilderRun, ResumeDraft, ResumeElement
from ..rendering.markdown import place_achievements, render_resume_markdown
from ..schemas import AgentBuilderOutput
from ..validators.completeness import CompletenessError, ensure_completeness
from ..validators.no_fabrication import NoFabricationError, validate_and_flatten
from .context import build_builder_context
from .generate import build_request as build_ab_request

Status = AgentBuilderRun.Status
ValidationState = AgentBuilderRun.ValidationState


class AgentBuilderRunError(Exception):
    pass


class ConcurrentAgentBuilderModificationError(AgentBuilderRunError):
    pass


class AgentBuilderValidationError(AgentBuilderRunError):
    pass


class _SimpleJra:
    def __init__(self, role_title: str, employer: str):
        self.role_title = role_title
        self.employer = employer


class _SimpleAssessment:
    def __init__(self, requirement_id: str, disposition: str, explanation: str):
        self.requirement_id = requirement_id
        self.disposition = disposition
        self.explanation = explanation


def _hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _touch(run: AgentBuilderRun, *, lock_version: int) -> None:
    if run.lock_version != lock_version:
        raise ConcurrentAgentBuilderModificationError(
            f"AgentBuilderRun was modified by another request (expected lock_version "
            f"{lock_version}, actual {run.lock_version}) -- reload and retry."
        )
    run.lock_version += 1


def start_ab_run(job_application: JobApplication) -> AgentBuilderRun:
    """Zero provider calls. Pins the current FitAssessment, builds the deterministic bounded
    context (`build_builder_context`), and prepares the editable input snapshot."""
    fit_assessment = job_application.current_fit_assessment
    if fit_assessment is None:
        raise AgentBuilderRunError("No FitAssessment exists -- run Agent Candidate and approve Gate 1 first.")
    if job_application.pipeline_phase not in (
        JobApplication.PipelinePhase.PREPARATION, JobApplication.PipelinePhase.READY,
    ):
        raise AgentBuilderRunError("Gate 1 must be approved before Agent Builder can run.")
    if fit_assessment.based_on_jra_id != job_application.current_jra_id:
        raise AgentBuilderRunError(
            "The current FitAssessment is stale relative to the current JobRequirementAnalysis -- "
            "re-run and re-approve Gate 1 before building a resume draft."
        )

    try:
        retrieval = build_builder_context(fit_assessment)
    except (LegacyFitAssessmentManifestError, InvalidBaselineManifestError) as exc:
        raise AgentBuilderRunError(str(exc)) from exc

    jra = job_application.current_jra
    requirement_assessments = list(fit_assessment.requirement_assessments.all())
    prepared_input = _serialize_input(jra, requirement_assessments, retrieval)

    with transaction.atomic():
        locked_application = JobApplication.objects.select_for_update().get(pk=job_application.pk)
        next_version = (
            locked_application.agent_builder_runs.aggregate(Max("version"))["version__max"] or 0
        ) + 1
        run = AgentBuilderRun.objects.create(
            job_application=locked_application, version=next_version, based_on_fit_assessment=fit_assessment,
            prepared_input=prepared_input,
        )
    return run


def _serialize_input(jra, requirement_assessments, retrieval: RetrievalContext) -> dict:
    return {
        "role_title": jra.role_title or "", "employer": jra.employer or "",
        "requirement_assessments": [
            {"requirement_id": a.requirement_id, "disposition": a.disposition, "explanation": a.explanation}
            for a in requirement_assessments
        ],
        "claims": [
            {
                "claim_id": c.claim_id, "text": c.text, "claim_type": c.claim_type,
                "subject_scope": c.subject_scope, "approved_engagement_ids": list(c.approved_engagement_ids),
                "retrieval_reasons": list(c.retrieval_reasons),
            }
            for c in retrieval.claims
        ],
        "engagements": [
            {
                "engagement_id": e.engagement_id, "approved_role_title": e.approved_role_title,
                "displayed_organization": e.displayed_organization, "location": e.location,
                "is_current": e.is_current, "duration_months": e.duration_months,
            }
            for e in retrieval.engagements
        ],
        "rules": [
            {"rule_id": r.rule_id, "rule_type": r.rule_type, "text": r.text, "scope": r.scope}
            for r in retrieval.rules
        ],
        "engagements_without_eligible_evidence": list(retrieval.engagements_without_eligible_evidence),
        "pinned_language_claim_ids": list(retrieval.pinned_language_claim_ids),
    }


def _deserialize_retrieval(effective_input: dict) -> RetrievalContext:
    return RetrievalContext(
        candidate_memory_id=0,
        claims=[
            RetrievedClaim(
                claim_id=c["claim_id"], text=c["text"], claim_type=c["claim_type"],
                subject_scope=c["subject_scope"],
                approved_engagement_ids=tuple(c.get("approved_engagement_ids", [])),
                retrieval_reasons=tuple(c.get("retrieval_reasons", [])),
            )
            for c in effective_input["claims"]
        ],
        engagements=[
            RetrievedEngagement(
                engagement_id=e["engagement_id"], approved_role_title=e["approved_role_title"],
                displayed_organization=e["displayed_organization"], location=e["location"],
                is_current=e["is_current"], duration_months=e.get("duration_months"),
            )
            for e in effective_input["engagements"]
        ],
        rules=[
            RetrievedRule(rule_id=r["rule_id"], rule_type=r["rule_type"], text=r["text"], scope=r["scope"])
            for r in effective_input["rules"]
        ],
        engagements_without_eligible_evidence=tuple(
            effective_input.get("engagements_without_eligible_evidence", [])
        ),
        pinned_language_claim_ids=tuple(effective_input.get("pinned_language_claim_ids", [])),
    )


def edit_ab_input(run: AgentBuilderRun, *, edited_input: dict, lock_version: int) -> AgentBuilderRun:
    """Zero provider calls. Run-local only -- never touches JobRequirement/MemoryClaim/
    CareerEngagement/CandidateMemory/FitAssessment records."""
    if run.status not in (Status.READY, Status.FAILED):
        raise AgentBuilderRunError(f"AgentBuilderRun input cannot be edited in status {run.status!r}.")
    prepared = run.prepared_input or {}
    original_claim_ids = {c["claim_id"] for c in prepared.get("claims", [])}
    edited_claim_ids = {c.get("claim_id") for c in edited_input.get("claims", [])}
    if not edited_claim_ids <= original_claim_ids:
        raise AgentBuilderValidationError(
            "AB_BUILD input edits may only remove claims, never add a claim_id."
        )
    _touch(run, lock_version=lock_version)
    run.edited_input = edited_input
    run.save(update_fields=["edited_input", "lock_version", "updated_at"])
    return run


def reset_ab_input(run: AgentBuilderRun, *, lock_version: int) -> AgentBuilderRun:
    if run.status not in (Status.READY, Status.FAILED):
        raise AgentBuilderRunError(f"AgentBuilderRun input cannot be reset in status {run.status!r}.")
    _touch(run, lock_version=lock_version)
    run.edited_input = None
    run.save(update_fields=["edited_input", "lock_version", "updated_at"])
    return run


def _build_adapter(
    run: AgentBuilderRun, *, requested_model_id, requested_reasoning_effort, requested_output_budget
):
    from llm_provider.models import StageModelAssignment

    adapter = get_adapter_for_stage(
        StageModelAssignment.Stage.AB_BUILD, requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    if requested_output_budget is not None:
        if requested_output_budget < 1:
            raise AgentBuilderRunError("Requested output budget must be at least 1.")
        capability = adapter.llm_model.max_output_tokens
        if capability is not None and requested_output_budget > capability:
            raise AgentBuilderRunError(
                f"Requested output budget ({requested_output_budget}) exceeds {adapter.llm_model}'s "
                f"capability ({capability})."
            )
        adapter.effective_max_output_tokens = requested_output_budget
    return adapter


def configure_ab_run(
    run: AgentBuilderRun, *, requested_model_id=None, requested_reasoning_effort=None,
    requested_output_budget=None, lock_version: int,
) -> AgentBuilderRun:
    """Zero provider calls."""
    if run.status not in (Status.READY, Status.FAILED):
        raise AgentBuilderRunError(
            f"AgentBuilderRun is {run.status!r} -- configuration only allowed while READY/FAILED."
        )
    adapter = _build_adapter(
        run, requested_model_id=requested_model_id, requested_reasoning_effort=requested_reasoning_effort,
        requested_output_budget=requested_output_budget,
    )
    _touch(run, lock_version=lock_version)
    run.selected_provider = adapter.llm_model.provider
    run.selected_model = adapter.llm_model
    run.selection_source = adapter.selection_source
    run.reasoning_effort = adapter.effective_reasoning_effort or ""
    run.requested_output_budget = requested_output_budget
    run.effective_output_budget = adapter.effective_max_output_tokens
    run.timeout_seconds = int(adapter.effective_read_timeout_seconds)
    run.save(update_fields=[
        "selected_provider", "selected_model", "selection_source", "reasoning_effort",
        "requested_output_budget", "effective_output_budget", "timeout_seconds", "lock_version", "updated_at",
    ])
    return run


def execute_ab_run(
    run: AgentBuilderRun, *, requested_model_id=None, requested_reasoning_effort=None,
    requested_output_budget=None, correlation_id=None, lock_version: int,
) -> AgentBuilderRun:
    """Requires READY or FAILED. Exactly one logical adapter call. Never creates a ResumeDraft."""
    with transaction.atomic():
        locked = AgentBuilderRun.objects.select_for_update().get(pk=run.pk)
        if locked.lock_version != lock_version:
            raise ConcurrentAgentBuilderModificationError(
                f"AgentBuilderRun was modified by another request (expected lock_version "
                f"{lock_version}, actual {locked.lock_version}) -- reload and retry."
            )
        if locked.status not in (Status.READY, Status.FAILED):
            raise AgentBuilderRunError(f"AgentBuilderRun cannot be executed from status {locked.status!r}.")
        effective_input = locked.effective_input
        if not effective_input:
            raise AgentBuilderRunError("AgentBuilderRun has no prepared input to execute.")
        locked.input_hash = _hash(effective_input)
        locked.status = Status.RUNNING
        locked.lock_version += 1
        locked.save(update_fields=["input_hash", "status", "lock_version", "updated_at"])
        run = locked

    adapter = _build_adapter(
        run, requested_model_id=requested_model_id, requested_reasoning_effort=requested_reasoning_effort,
        requested_output_budget=requested_output_budget,
    )
    jra = _SimpleJra(effective_input["role_title"], effective_input["employer"])
    requirement_assessments = [
        _SimpleAssessment(a["requirement_id"], a["disposition"], a["explanation"])
        for a in effective_input["requirement_assessments"]
    ]
    request = build_ab_request(
        jra, requirement_assessments, _deserialize_retrieval(effective_input),
        max_output_tokens=adapter.effective_max_output_tokens,
        reasoning_effort=adapter.effective_reasoning_effort,
        correlation_id=correlation_id,
    )
    result = adapter.generate(request)

    run.refresh_from_db()
    run.selected_provider = adapter.llm_model.provider
    run.selected_model = adapter.llm_model
    run.selection_source = adapter.selection_source
    run.reasoning_effort = adapter.effective_reasoning_effort or ""
    run.requested_output_budget = requested_output_budget
    run.effective_output_budget = adapter.effective_max_output_tokens
    run.timeout_seconds = int(adapter.effective_read_timeout_seconds)
    run.executed_at = timezone.now()
    run.llm_call_log_id = result.call_log_id
    run.lock_version += 1

    if result.is_error:
        run.status = Status.FAILED
        run.failure_category = result.error.category.value
        run.failure_summary = result.error.message
        run.save(update_fields=[
            "selected_provider", "selected_model", "selection_source", "reasoning_effort",
            "requested_output_budget", "effective_output_budget", "timeout_seconds", "executed_at",
            "llm_call_log", "status", "failure_category", "failure_summary", "lock_version", "updated_at",
        ])
        return run

    output_dict = result.content.model_dump() if hasattr(result.content, "model_dump") else result.content
    errors = _validate_ab_output(run, output_dict)
    run.provider_output = output_dict
    run.provider_output_hash = _hash(output_dict)
    run.operator_output = copy.deepcopy(output_dict)
    run.validation_state = ValidationState.VALID if not errors else ValidationState.INVALID
    run.validation_errors = errors
    run.status = Status.SUCCEEDED
    run.failure_category = ""
    run.failure_summary = ""
    run.save(update_fields=[
        "selected_provider", "selected_model", "selection_source", "reasoning_effort",
        "requested_output_budget", "effective_output_budget", "timeout_seconds", "executed_at",
        "llm_call_log", "provider_output", "provider_output_hash", "operator_output",
        "validation_state", "validation_errors", "status", "failure_category", "failure_summary",
        "lock_version", "updated_at",
    ])
    return run


def _validate_ab_output(run: AgentBuilderRun, data) -> list[str]:
    if data is None:
        return ["No output to validate."]
    try:
        validated = AgentBuilderOutput.model_validate(data)
    except PydanticValidationError as exc:
        return [sanitize_error_message(str(exc))]

    effective_input = run.effective_input or {}
    retrieval = _deserialize_retrieval(effective_input)
    try:
        elements = validate_and_flatten(validated, retrieval=retrieval)
    except NoFabricationError as exc:
        return [str(exc)]
    placed = place_achievements(elements, retrieval)
    try:
        ensure_completeness(placed, retrieval)
    except CompletenessError as exc:
        return [str(exc)]
    return []


def validate_ab_output(run: AgentBuilderRun, data=None) -> list[str]:
    return _validate_ab_output(run, run.operator_output if data is None else data)


def edit_ab_output(run: AgentBuilderRun, *, edited_output: dict, lock_version: int) -> AgentBuilderRun:
    if run.status not in (Status.SUCCEEDED, Status.EDITED):
        raise AgentBuilderRunError(f"AgentBuilderRun output cannot be edited in status {run.status!r}.")
    errors = _validate_ab_output(run, edited_output)
    _touch(run, lock_version=lock_version)
    run.operator_output = edited_output
    run.validation_state = ValidationState.VALID if not errors else ValidationState.INVALID
    run.validation_errors = errors
    run.status = Status.EDITED
    run.edited_at = timezone.now()
    run.save(update_fields=[
        "operator_output", "validation_state", "validation_errors", "status", "edited_at",
        "lock_version", "updated_at",
    ])
    return run


def approve_ab_run_and_create_draft(run: AgentBuilderRun, *, lock_version: int) -> ResumeDraft:
    """Requires SUCCEEDED or EDITED with currently-valid output (re-validated fresh). Atomically
    creates the ResumeDraft + ResumeElement rows and marks the run APPROVED. Never creates a
    ResumeDraft merely because the provider responded successfully -- only this explicit action
    does."""
    with transaction.atomic():
        locked = AgentBuilderRun.objects.select_for_update().get(pk=run.pk)
        if locked.lock_version != lock_version:
            raise ConcurrentAgentBuilderModificationError(
                f"AgentBuilderRun was modified by another request (expected lock_version "
                f"{lock_version}, actual {locked.lock_version}) -- reload and retry."
            )
        if locked.status not in (Status.SUCCEEDED, Status.EDITED):
            raise AgentBuilderRunError(f"AgentBuilderRun cannot be approved from status {locked.status!r}.")
        errors = _validate_ab_output(locked, locked.operator_output)
        if errors:
            raise AgentBuilderValidationError(f"AgentBuilderRun output failed validation: {errors}")

        ab_output = AgentBuilderOutput.model_validate(locked.operator_output)
        effective_input = locked.effective_input
        retrieval = _deserialize_retrieval(effective_input)
        elements = validate_and_flatten(ab_output, retrieval=retrieval)
        placed_elements = place_achievements(elements, retrieval)
        try:
            ensure_completeness(placed_elements, retrieval)
        except CompletenessError as exc:
            raise AgentBuilderValidationError(str(exc)) from exc
        language = ab_output.positioning_guidance.resume_language or "en"
        rendered_markdown = render_resume_markdown(
            elements, recommended_title=ab_output.target_positioning.recommended_title,
            retrieval=retrieval, language=language,
        )

        job_application = JobApplication.objects.select_for_update().get(pk=locked.job_application_id)
        next_version = (job_application.resume_drafts.aggregate(Max("version"))["version__max"] or 0) + 1
        draft = ResumeDraft.objects.create(
            job_application=job_application, version=next_version,
            based_on_fit_assessment=locked.based_on_fit_assessment,
            retrieved_claim_ids=retrieval.claim_ids, retrieved_engagement_ids=retrieval.engagement_ids,
            recommended_title=ab_output.target_positioning.recommended_title,
            title_options=list(ab_output.target_positioning.title_options),
            skill_categories=[c.model_dump() for c in ab_output.skill_categories],
            positioning_guidance=ab_output.positioning_guidance.model_dump(),
            rendered_markdown=rendered_markdown,
        )
        for element in elements:
            ResumeElement.objects.create(
                resume_draft=draft, section=element.section, engagement_id=element.engagement_id,
                order=element.order, text=element.text,
                supporting_memory_claim_ids=element.supporting_memory_claim_ids,
                matched_job_requirement_ids=element.matched_job_requirement_ids,
            )
        job_application.record_resume_draft(draft)

        locked.approved_output = locked.operator_output
        locked.approved_output_hash = _hash(locked.operator_output)
        locked.validation_state = ValidationState.VALID
        locked.validation_errors = []
        locked.status = Status.APPROVED
        locked.approved_at = timezone.now()
        locked.resulting_resume_draft = draft
        locked.lock_version += 1
        locked.save(update_fields=[
            "approved_output", "approved_output_hash", "validation_state", "validation_errors",
            "status", "approved_at", "resulting_resume_draft", "lock_version", "updated_at",
        ])
    return draft
