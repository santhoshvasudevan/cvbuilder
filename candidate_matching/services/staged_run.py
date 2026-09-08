"""Operator-controlled, persistent, resumable M5 staged workflow (2026-09-08, D-041).

`services/fit_assessment.py`'s `build_fit_assessment` remains the programmatic, all-three-calls-
in-one-go entry point (kept for the existing fake-adapter test suite and any future non-
interactive use). The normal operator-facing UI never calls it -- it drives an
`AgentCandidateRun`/`AgentCandidateStage` (`../models.py`) through three individually-authorized
provider calls (AC_NORMALIZE -> AC_RANK -> AC_MATCH), each requiring an explicit operator click to
run and an explicit operator approval before the next stage's input is even prepared.

This module reuses -- never duplicates -- the exact same building blocks
`build_fit_assessment`/`bounded_retrieval.build_bounded_context` already call:
`normalize.py`/`rank.py`/`assess.py` (the three LLM calls), `candidate_generation.py`/`dedup.py`
(deterministic BM25 candidate-pool construction), `retrieve.py` (eligibility), `rule_selection.py`,
`baseline_chronology.py` (D-035/D-037's deterministic engagement-anchor/language-evidence layer),
and `validators/disposition_coverage.py` (no-fabrication/coverage). The two capping helpers
`bounded_retrieval.py` keeps private (`_cap_ranking_pool`/`_cap_selected`) are imported directly
from that module rather than re-implemented, since splitting AC_NORMALIZE/AC_RANK/AC_MATCH into
three independently-executable stages requires calling them from a different orchestration shape,
not a different algorithm.

State machine (`AgentCandidateStage.Status`): DRAFT (blocked, no prepared input yet) -> READY
(prepared, awaiting explicit "Run") -> RUNNING -> SUCCEEDED (or FAILED) -> EDITED (only if the
operator edits the output) -> APPROVED. Approving AC_NORMALIZE/AC_RANK deterministically prepares
the next stage's input (zero provider calls) and sets it to READY; approving AC_MATCH prepares
nothing further -- `finalize_run` is a separate, explicit action. Editing an already-APPROVED
stage's output invalidates every downstream stage (back to INVALIDATED, prepared_input cleared),
never silently reusing stale downstream artifacts -- see `_invalidate_downstream`.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from candidate_memory.models import CareerEngagement
from job_applications.models import JobApplication
from llm_provider.adapters import get_adapter_for_stage
from llm_provider.errors import sanitize_error_message
from llm_provider.types import NormalizedLLMRequest

from ..models import (
    AgentCandidateRun,
    AgentCandidateStage,
    AgentCandidateStageRevision,
    FitAssessment,
    RequirementAssessment,
)
from ..schemas import (
    AgentCandidateAssessment,
    RelevanceRankingOutput,
    RequirementNormalizationItem,
    RequirementNormalizationOutput,
)
from ..validators.disposition_coverage import AssessmentItemData, ensure_full_coverage, sanitize_items
from . import assess, bounded_retrieval, candidate_generation, normalize, rank, static_requirements
from .baseline_chronology import build_baseline_chronology, build_baseline_manifest, merge_retrieved_claims
from .dedup import DedupedClaim, deduplicate_claims
from .retrieve import (
    RETRIEVAL_REASON_JOB_RELEVANT,
    RetrievalContext,
    RetrievedClaim,
    RetrievedEngagement,
    RetrievedRule,
    get_active_candidate_memory,
    retrieve_eligible_pool,
)
from .rule_selection import select_bounded_rules

Stage = AgentCandidateStage.Stage
Status = AgentCandidateStage.Status
ValidationState = AgentCandidateStage.ValidationState
Kind = AgentCandidateStageRevision.Kind

STAGE_ORDER = (Stage.AC_NORMALIZE, Stage.AC_RANK, Stage.AC_MATCH)
_NEXT_STAGE = {Stage.AC_NORMALIZE: Stage.AC_RANK, Stage.AC_RANK: Stage.AC_MATCH}
_SCHEMA_BY_STAGE = {
    Stage.AC_NORMALIZE: RequirementNormalizationOutput,
    Stage.AC_RANK: RelevanceRankingOutput,
    Stage.AC_MATCH: AgentCandidateAssessment,
}


class StagedRunError(Exception):
    """Base class for every domain error this module raises."""


class StageSequenceError(StagedRunError):
    """Raised when an action is attempted on a stage before/after its allowed status window."""


class ConcurrentStageModificationError(StagedRunError):
    """Raised when a caller's `lock_version` does not match the stage's current `lock_version` --
    another request (a second browser tab, a concurrent operator action) modified this stage
    first. The caller must reload the stage and retry with the fresh `lock_version`."""


class StageValidationError(StagedRunError):
    """Raised when an edited input/output, or an approval attempt, fails schema or domain
    (no-fabrication/evidence-attachment) validation."""


def _hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _touch(stage: AgentCandidateStage, *, lock_version: int) -> None:
    if stage.lock_version != lock_version:
        raise ConcurrentStageModificationError(
            f"Stage {stage.stage!r} was modified by another request (expected lock_version "
            f"{lock_version}, actual {stage.lock_version}) -- reload and retry."
        )
    stage.lock_version += 1


def _next_revision_number(stage: AgentCandidateStage, kind: str) -> int:
    current_max = stage.revisions.filter(kind=kind).aggregate(
        Max("revision_number")
    )["revision_number__max"]
    return 0 if current_max is None else current_max + 1


def _record_revision(
    stage: AgentCandidateStage,
    *,
    kind: str,
    data,
    validation_state: str = "",
    validation_errors=None,
) -> None:
    revision_number = _next_revision_number(stage, kind)
    AgentCandidateStageRevision.objects.create(
        stage=stage, kind=kind, revision_number=revision_number, data=data,
        validation_state=validation_state, validation_errors=validation_errors or [],
    )


# --- Start / inspect ----------------------------------------------------------------------------


def start_run(job_application: JobApplication) -> AgentCandidateRun:
    """Zero provider calls. Pins the current `JobRequirementAnalysis` and the ACTIVE
    `CandidateMemory`, creates all three stage rows, and prepares AC_NORMALIZE's input (the only
    stage a fresh run can immediately run) -- AC_RANK/AC_MATCH stay DRAFT (blocked) until their
    own predecessor is approved."""
    if job_application.current_jra is None:
        raise StagedRunError(
            "JobApplication has no current JobRequirementAnalysis -- run Agent Jobber first."
        )
    jra = job_application.current_jra
    requirements = list(jra.requirements.all())
    if not requirements:
        raise StagedRunError(
            f"JobRequirementAnalysis {jra.pk} (v{jra.version}) has zero JobRequirements -- Agent "
            "Candidate refuses to start a run against an empty analysis."
        )
    candidate_memory = get_active_candidate_memory()

    narrative_requirements = [r for r in requirements if static_requirements.classify(r.text) is None]

    with transaction.atomic():
        locked_application = JobApplication.objects.select_for_update().get(pk=job_application.pk)
        next_version = (
            locked_application.agent_candidate_runs.aggregate(Max("version"))["version__max"] or 0
        ) + 1
        run = AgentCandidateRun.objects.create(
            job_application=locked_application,
            version=next_version,
            based_on_jra=jra,
            based_on_candidate_memory=candidate_memory,
        )
        prepared_input = {
            "posting_language": jra.posting_language,
            "requirements": [
                {"requirement_id": r.requirement_id, "text": r.text} for r in narrative_requirements
            ],
        }
        normalize_stage = AgentCandidateStage.objects.create(
            run=run, stage=Stage.AC_NORMALIZE, stage_order=1, status=Status.READY,
            prepared_input=prepared_input,
        )
        _record_revision(normalize_stage, kind=Kind.INPUT, data=prepared_input)
        AgentCandidateStage.objects.create(run=run, stage=Stage.AC_RANK, stage_order=2)
        AgentCandidateStage.objects.create(run=run, stage=Stage.AC_MATCH, stage_order=3)
    return run


def get_stage(run: AgentCandidateRun, stage: str) -> AgentCandidateStage:
    return run.stages.get(stage=stage)


def cancel_run(run: AgentCandidateRun) -> AgentCandidateRun:
    """Zero provider calls. Only an IN_PROGRESS run may be cancelled -- a stale browser tab acting
    on an already-COMPLETED/CANCELLED run gets an explicit error, never a silent no-op."""
    if run.status != AgentCandidateRun.Status.IN_PROGRESS:
        raise StagedRunError(f"Run is {run.status!r} -- only an IN_PROGRESS run can be cancelled.")
    run.status = AgentCandidateRun.Status.CANCELLED
    run.cancelled_at = timezone.now()
    run.save(update_fields=["status", "cancelled_at", "updated_at"])
    return run


def reconcile_stale_running_stage(stage: AgentCandidateStage) -> AgentCandidateStage:
    """Recovery for a stage left RUNNING after a process interruption (a crash mid-call, before
    the result could be persisted) -- there is no way to distinguish that from a genuinely
    in-flight call in another process, so this is always an explicit operator action, never
    triggered automatically by a page load/refresh. Marks the stage FAILED so it can be explicitly
    rerun, never left permanently stuck."""
    if stage.status != Status.RUNNING:
        raise StageSequenceError(
            f"Stage {stage.stage!r} is {stage.status!r}, not RUNNING -- nothing to reconcile."
        )
    stage.status = Status.FAILED
    stage.failure_category = "INTERRUPTED"
    stage.failure_summary = "Execution did not complete (process interruption) -- rerun explicitly."
    stage.lock_version += 1
    stage.save(
        update_fields=["status", "failure_category", "failure_summary", "lock_version", "updated_at"]
    )
    return stage


# --- Prepare / revise stage input (Phase C.2) ----------------------------------------------------


def _validate_stage_input_edit(stage: AgentCandidateStage, edited_input: dict) -> None:
    prepared = stage.prepared_input or {}
    if not isinstance(edited_input, dict):
        raise StageValidationError("Edited input must be a JSON object.")

    original_req_ids = {r["requirement_id"] for r in prepared.get("requirements", [])}
    edited_req_ids = {r.get("requirement_id") for r in edited_input.get("requirements", [])}
    if edited_req_ids != original_req_ids:
        raise StageValidationError(
            f"{stage.stage} input edits may not add, remove, or rename a requirement_id "
            "-- canonical requirement identity is never editable, only the text/selection fields."
        )

    if stage.stage == Stage.AC_RANK:
        original_ids = {c["claim_id"] for c in prepared.get("candidate_pool", [])}
        edited_ids = {c.get("claim_id") for c in edited_input.get("candidate_pool", [])}
        if not edited_ids <= original_ids:
            raise StageValidationError(
                "AC_RANK input edits may only remove candidate_pool entries, never add a "
                "claim_id outside the deterministically-generated pool."
            )
    elif stage.stage == Stage.AC_MATCH:
        original_claim_ids = {c["claim_id"] for c in prepared.get("claims", [])}
        edited_claim_ids = {c.get("claim_id") for c in edited_input.get("claims", [])}
        if not edited_claim_ids <= original_claim_ids:
            raise StageValidationError(
                "AC_MATCH input edits may only remove claims, never add a claim_id outside the "
                "pinned CandidateMemory's deterministically-retrieved set."
            )
        original_engagement_ids = {e["engagement_id"] for e in prepared.get("engagements", [])}
        edited_engagement_ids = {e.get("engagement_id") for e in edited_input.get("engagements", [])}
        if not edited_engagement_ids <= original_engagement_ids:
            raise StageValidationError("AC_MATCH input edits may not add an engagement_id.")


def edit_stage_input(
    stage: AgentCandidateStage, *, edited_input: dict, lock_version: int
) -> AgentCandidateStage:
    """Zero provider calls. `edited_input` is a run-local snapshot only -- it never touches
    JobRequirement/MemoryClaim/CareerEngagement/CandidateMemory/JobRequirementAnalysis."""
    if stage.status not in (Status.READY, Status.FAILED):
        raise StageSequenceError(
            f"Stage {stage.stage!r} input cannot be edited in status {stage.status!r}."
        )
    _validate_stage_input_edit(stage, edited_input)
    _touch(stage, lock_version=lock_version)
    stage.edited_input = edited_input
    stage.save(update_fields=["edited_input", "lock_version", "updated_at"])
    _record_revision(stage, kind=Kind.INPUT, data=edited_input)
    return stage


def reset_stage_input(stage: AgentCandidateStage, *, lock_version: int) -> AgentCandidateStage:
    """Zero provider calls. Discards the operator's edit and reverts to the deterministically
    prepared input."""
    if stage.status not in (Status.READY, Status.FAILED):
        raise StageSequenceError(
            f"Stage {stage.stage!r} input cannot be reset in status {stage.status!r}."
        )
    _touch(stage, lock_version=lock_version)
    stage.edited_input = None
    stage.save(update_fields=["edited_input", "lock_version", "updated_at"])
    return stage


# --- Configure (zero-call preview) / execute (exactly one call) ---------------------------------


def _build_adapter(
    stage: AgentCandidateStage,
    *,
    requested_model_id,
    requested_reasoning_effort,
    requested_output_budget,
):
    adapter = get_adapter_for_stage(
        stage.stage,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    if requested_output_budget is not None:
        if requested_output_budget < 1:
            raise StagedRunError("Requested output budget must be at least 1.")
        capability = adapter.llm_model.max_output_tokens
        if capability is not None and requested_output_budget > capability:
            raise StagedRunError(
                f"Requested output budget ({requested_output_budget}) exceeds {adapter.llm_model}'s "
                f"capability ({capability})."
            )
        adapter.effective_max_output_tokens = requested_output_budget
    return adapter


def configure_stage(
    stage: AgentCandidateStage,
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
    requested_output_budget: int | None = None,
    lock_version: int,
) -> AgentCandidateStage:
    """Zero provider calls. Validates and stores this run's provider/model/reasoning/budget
    choice for one stage -- never persisted as a new global `StageModelAssignment` default. Safe
    to call repeatedly (e.g. every time the operator changes a selector) before the stage is run."""
    if stage.status not in (Status.READY, Status.FAILED):
        raise StageSequenceError(
            f"Stage {stage.stage!r} is {stage.status!r} -- configuration is only allowed while "
            "READY (not yet run) or FAILED (about to be explicitly rerun)."
        )
    adapter = _build_adapter(
        stage, requested_model_id=requested_model_id, requested_reasoning_effort=requested_reasoning_effort,
        requested_output_budget=requested_output_budget,
    )
    _touch(stage, lock_version=lock_version)
    stage.selected_provider = adapter.llm_model.provider
    stage.selected_model = adapter.llm_model
    stage.selection_source = adapter.selection_source
    stage.reasoning_effort = adapter.effective_reasoning_effort or ""
    stage.requested_output_budget = requested_output_budget
    stage.effective_output_budget = adapter.effective_max_output_tokens
    stage.timeout_seconds = int(adapter.effective_read_timeout_seconds)
    stage.save(update_fields=[
        "selected_provider", "selected_model", "selection_source", "reasoning_effort",
        "requested_output_budget", "effective_output_budget", "timeout_seconds",
        "lock_version", "updated_at",
    ])
    return stage


def _build_normalized_request(
    stage: AgentCandidateStage, effective_input: dict, *, adapter, correlation_id
) -> NormalizedLLMRequest:
    if stage.stage == Stage.AC_NORMALIZE:
        return normalize.build_request(
            effective_input["requirements"], posting_language=effective_input["posting_language"],
            max_output_tokens=adapter.effective_max_output_tokens,
            reasoning_effort=adapter.effective_reasoning_effort, correlation_id=correlation_id,
        )
    if stage.stage == Stage.AC_RANK:
        pool = [
            DedupedClaim(
                claim_id=c["claim_id"], text=c["text"], claim_type=c["claim_type"],
                subject_scope=c["subject_scope"],
                approved_engagement_ids=tuple(c.get("approved_engagement_ids", [])),
                grouped_claim_ids=tuple(c.get("grouped_claim_ids") or (c["claim_id"],)),
            )
            for c in effective_input["candidate_pool"]
        ]
        return rank.build_request(
            pool, effective_input["requirements"], max_output_tokens=adapter.effective_max_output_tokens,
            reasoning_effort=adapter.effective_reasoning_effort, correlation_id=correlation_id,
        )
    if stage.stage == Stage.AC_MATCH:
        retrieval = RetrievalContext(
            candidate_memory_id=stage.run.based_on_candidate_memory_id,
            claims=[
                RetrievedClaim(
                    claim_id=c["claim_id"], text=c["text"], claim_type=c["claim_type"],
                    subject_scope=c["subject_scope"],
                    approved_engagement_ids=tuple(c.get("approved_engagement_ids", [])),
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
                RetrievedRule(
                    rule_id=r["rule_id"], rule_type=r["rule_type"], text=r["text"], scope=r["scope"]
                )
                for r in effective_input["rules"]
            ],
        )
        return assess.build_request(
            retrieval, effective_input["requirements"], max_output_tokens=adapter.effective_max_output_tokens,
            reasoning_effort=adapter.effective_reasoning_effort, correlation_id=correlation_id,
        )
    raise StagedRunError(f"Unknown stage {stage.stage!r}")


def execute_stage(
    stage: AgentCandidateStage,
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
    requested_output_budget: int | None = None,
    correlation_id: str | None = None,
    lock_version: int,
) -> AgentCandidateStage:
    """Requires READY (first run) or FAILED (an explicit rerun). Locks the stage against duplicate
    submission (`select_for_update` + the RUNNING transition, committed before the call), makes
    exactly one logical adapter invocation (the adapter's own bounded retries, never a second
    logical call), and never executes the next stage."""
    with transaction.atomic():
        locked = AgentCandidateStage.objects.select_for_update().get(pk=stage.pk)
        if locked.lock_version != lock_version:
            raise ConcurrentStageModificationError(
                f"Stage {locked.stage!r} was modified by another request (expected lock_version "
                f"{lock_version}, actual {locked.lock_version}) -- reload and retry."
            )
        if locked.status not in (Status.READY, Status.FAILED):
            raise StageSequenceError(
                f"Stage {locked.stage!r} cannot be executed from status {locked.status!r} -- it "
                "must be READY or FAILED (an explicit rerun)."
            )
        effective_input = locked.effective_input
        if not effective_input:
            raise StageSequenceError(f"Stage {locked.stage!r} has no prepared input to execute.")
        locked.input_hash = _hash(effective_input)
        locked.status = Status.RUNNING
        locked.lock_version += 1
        locked.save(update_fields=["input_hash", "status", "lock_version", "updated_at"])
        stage = locked

    # -- outside any transaction, mirroring every other stage in this codebase: the LLMCallLog
    # audit row (written inside adapter.generate()) always commits regardless of what happens
    # next, and a failure here never rolls back the RUNNING->FAILED/SUCCEEDED transition below.
    adapter = _build_adapter(
        stage,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
        requested_output_budget=requested_output_budget,
    )
    request = _build_normalized_request(
        stage, effective_input, adapter=adapter, correlation_id=correlation_id
    )
    result = adapter.generate(request)

    stage.refresh_from_db()
    stage.selected_provider = adapter.llm_model.provider
    stage.selected_model = adapter.llm_model
    stage.selection_source = adapter.selection_source
    stage.reasoning_effort = adapter.effective_reasoning_effort or ""
    stage.requested_output_budget = requested_output_budget
    stage.effective_output_budget = adapter.effective_max_output_tokens
    stage.timeout_seconds = int(adapter.effective_read_timeout_seconds)
    stage.executed_at = timezone.now()
    stage.llm_call_log_id = result.call_log_id
    stage.lock_version += 1

    if result.is_error:
        stage.status = Status.FAILED
        stage.failure_category = result.error.category.value
        stage.failure_summary = result.error.message
        stage.save(update_fields=[
            "selected_provider", "selected_model", "selection_source", "reasoning_effort",
            "requested_output_budget", "effective_output_budget", "timeout_seconds", "executed_at",
            "llm_call_log", "status", "failure_category", "failure_summary", "lock_version", "updated_at",
        ])
        return stage

    output_dict = result.content.model_dump() if hasattr(result.content, "model_dump") else result.content
    errors = _validate_output(stage, output_dict)
    stage.provider_output = output_dict
    stage.provider_output_hash = _hash(output_dict)
    # A deep copy, never the same object as provider_output -- an in-place mutation of the
    # operator's editable copy (e.g. a view building an edited dict by mutating a fetched copy
    # before calling edit_stage_output) must never silently corrupt the immutable original, even
    # in memory before either field is next saved/reloaded.
    stage.operator_output = copy.deepcopy(output_dict)
    stage.validation_state = ValidationState.VALID if not errors else ValidationState.INVALID
    stage.validation_errors = errors
    stage.status = Status.SUCCEEDED
    stage.failure_category = ""
    stage.failure_summary = ""
    stage.save(update_fields=[
        "selected_provider", "selected_model", "selection_source", "reasoning_effort",
        "requested_output_budget", "effective_output_budget", "timeout_seconds", "executed_at",
        "llm_call_log", "provider_output", "provider_output_hash", "operator_output",
        "validation_state", "validation_errors", "status", "failure_category", "failure_summary",
        "lock_version", "updated_at",
    ])
    _record_revision(
        stage,
        kind=Kind.OUTPUT,
        data=output_dict,
        validation_state=stage.validation_state,
        validation_errors=errors,
    )
    return stage


# --- Edit / validate / approve output (Phase C.4/C.5) --------------------------------------------


def _validate_output(stage: AgentCandidateStage, data) -> list[str]:
    if data is None:
        return ["No output to validate."]
    schema = _SCHEMA_BY_STAGE[stage.stage]
    try:
        validated = schema.model_validate(data)
    except PydanticValidationError as exc:
        return [sanitize_error_message(str(exc))]

    errors: list[str] = []
    effective_input = stage.effective_input or {}

    if stage.stage == Stage.AC_NORMALIZE:
        expected_ids = {r["requirement_id"] for r in effective_input.get("requirements", [])}
        returned_ids = {item.requirement_id for item in validated.items}
        if returned_ids != expected_ids:
            missing = sorted(expected_ids - returned_ids)
            added = sorted(returned_ids - expected_ids)
            errors.append(
                f"requirement_id set does not match the input (missing={missing}, unexpected={added})."
            )
        if len(returned_ids) != len(validated.items):
            errors.append("duplicate requirement_id in output.")

    elif stage.stage == Stage.AC_RANK:
        pool_ids = {c["claim_id"] for c in effective_input.get("candidate_pool", [])}
        for item in validated.rankings:
            fabricated = [cid for cid in item.relevant_claim_ids if cid not in pool_ids]
            if fabricated:
                errors.append(
                    f"{item.requirement_id}: fabricated claim_id(s) {fabricated} not in the candidate pool."
                )

    elif stage.stage == Stage.AC_MATCH:
        claim_ids = {c["claim_id"] for c in effective_input.get("claims", [])}
        engagement_ids = {e["engagement_id"] for e in effective_input.get("engagements", [])}
        for item in validated.requirement_assessments:
            fabricated_claims = [
                cid for cid in item.supporting_memory_claim_ids if cid not in claim_ids
            ]
            fabricated_engagements = [
                eid for eid in item.supporting_engagement_ids if eid not in engagement_ids
            ]
            if fabricated_claims or fabricated_engagements:
                errors.append(
                    f"{item.requirement_id}: fabricated evidence id(s) claims={fabricated_claims} "
                    f"engagements={fabricated_engagements}."
                )
            if item.disposition in ("MATCH", "PARTIAL") and not (
                item.supporting_memory_claim_ids or item.supporting_engagement_ids
            ):
                errors.append(
                    f"{item.requirement_id}: {item.disposition} requires at least one cited "
                    "claim/engagement id."
                )

    return errors


def validate_stage_output(stage: AgentCandidateStage, data=None) -> list[str]:
    """Pure, zero-provider-call, zero-write validation -- validates `data` (defaulting to the
    stage's current `operator_output`) without persisting anything."""
    return _validate_output(stage, stage.operator_output if data is None else data)


def edit_stage_output(
    stage: AgentCandidateStage, *, edited_output: dict, lock_version: int
) -> AgentCandidateStage:
    """Zero provider calls. `provider_output` is never overwritten -- this only ever writes
    `operator_output` plus a new, append-only `AgentCandidateStageRevision`. If the stage was
    already APPROVED, this edit invalidates that approval and every downstream stage
    (`_invalidate_downstream`) -- the operator must explicitly re-approve and, for each downstream
    stage, explicitly re-run it."""
    if stage.status not in (Status.SUCCEEDED, Status.EDITED, Status.APPROVED):
        raise StageSequenceError(f"Stage {stage.stage!r} output cannot be edited in status {stage.status!r}.")
    was_approved = stage.status == Status.APPROVED
    errors = _validate_output(stage, edited_output)

    _touch(stage, lock_version=lock_version)
    stage.operator_output = edited_output
    stage.validation_state = ValidationState.VALID if not errors else ValidationState.INVALID
    stage.validation_errors = errors
    stage.status = Status.EDITED
    stage.edited_at = timezone.now()
    stage.approved_at = None
    stage.save(update_fields=[
        "operator_output", "validation_state", "validation_errors", "status", "edited_at",
        "approved_at", "lock_version", "updated_at",
    ])
    _record_revision(
        stage,
        kind=Kind.OUTPUT,
        data=edited_output,
        validation_state=stage.validation_state,
        validation_errors=errors,
    )

    if was_approved:
        _invalidate_downstream(stage)
    return stage


def _invalidate_downstream(stage: AgentCandidateStage) -> None:
    """Never deletes a stage row or its revision history -- clears only the live/current fields
    and marks each downstream stage INVALIDATED, forcing the operator to explicitly re-prepare
    (via re-approving the upstream stage), re-run, and re-approve every one of them. Never
    silently reuses a downstream artifact computed against the old upstream output."""
    downstream = AgentCandidateStage.objects.select_for_update().filter(
        run_id=stage.run_id, stage_order__gt=stage.stage_order
    ).order_by("stage_order")
    for ds in downstream:
        ds.status = Status.INVALIDATED
        ds.prepared_input = None
        ds.edited_input = None
        ds.provider_output = None
        ds.provider_output_hash = ""
        ds.operator_output = None
        ds.approved_output = None
        ds.approved_output_hash = ""
        ds.validation_state = ValidationState.UNVALIDATED
        ds.validation_errors = []
        ds.llm_call_log = None
        ds.executed_at = None
        ds.edited_at = None
        ds.approved_at = None
        ds.lock_version += 1
        ds.save()


def approve_stage(stage: AgentCandidateStage, *, lock_version: int) -> AgentCandidateStage:
    """Zero provider calls. Requires SUCCEEDED or EDITED with currently-valid output (re-validated
    fresh here, never trusting a stale flag). Freezes `approved_output`/its hash, deterministically
    prepares the next stage's input (AC_NORMALIZE/AC_RANK only -- AC_MATCH's approval prepares
    nothing further; `finalize_run` is the separate, explicit action), and never executes the next
    stage itself."""
    with transaction.atomic():
        locked = AgentCandidateStage.objects.select_for_update().get(pk=stage.pk)
        if locked.lock_version != lock_version:
            raise ConcurrentStageModificationError(
                f"Stage {locked.stage!r} was modified by another request (expected lock_version "
                f"{lock_version}, actual {locked.lock_version}) -- reload and retry."
            )
        if locked.status not in (Status.SUCCEEDED, Status.EDITED):
            raise StageSequenceError(
                f"Stage {locked.stage!r} cannot be approved from status {locked.status!r} -- it "
                "must be SUCCEEDED or EDITED, with valid output."
            )
        errors = _validate_output(locked, locked.operator_output)
        if errors:
            raise StageValidationError(
                f"Stage {locked.stage!r} output failed validation and cannot be approved: {errors}"
            )
        locked.approved_output = locked.operator_output
        locked.approved_output_hash = _hash(locked.operator_output)
        locked.validation_state = ValidationState.VALID
        locked.validation_errors = []
        locked.status = Status.APPROVED
        locked.approved_at = timezone.now()
        locked.lock_version += 1
        locked.save(update_fields=[
            "approved_output", "approved_output_hash", "validation_state", "validation_errors",
            "status", "approved_at", "lock_version", "updated_at",
        ])
        _prepare_next_stage(locked.run, locked)
        stage = locked
    return stage


# --- Deterministic inter-stage preparation (Phase E) ----------------------------------------------


def _prepare_next_stage(run: AgentCandidateRun, approved_stage: AgentCandidateStage) -> None:
    next_stage_name = _NEXT_STAGE.get(approved_stage.stage)
    if next_stage_name is None:
        return  # AC_MATCH has no next stage; finalize_run is a separate explicit action.

    next_stage = AgentCandidateStage.objects.select_for_update().get(run=run, stage=next_stage_name)

    if approved_stage.stage == Stage.AC_NORMALIZE:
        normalization_output = RequirementNormalizationOutput.model_validate(approved_stage.approved_output)
        normalization_by_id = {item.requirement_id: item for item in normalization_output.items}
        prepared = _prepare_rank_input(run, normalization_by_id)
    else:  # AC_RANK just approved -> prepare AC_MATCH
        ranking_output = RelevanceRankingOutput.model_validate(approved_stage.approved_output)
        candidate_pool_snapshot = (approved_stage.effective_input or {}).get("candidate_pool", [])
        prepared, baseline_manifest = _prepare_match_input(run, ranking_output, candidate_pool_snapshot)
        run.baseline_chronology_manifest = baseline_manifest
        run.save(update_fields=["baseline_chronology_manifest", "updated_at"])

    next_stage.prepared_input = prepared
    next_stage.edited_input = None
    next_stage.status = Status.READY
    next_stage.lock_version += 1
    next_stage.save(update_fields=["prepared_input", "edited_input", "status", "lock_version", "updated_at"])
    _record_revision(next_stage, kind=Kind.INPUT, data=prepared)


def _prepare_rank_input(
    run: AgentCandidateRun, normalization_by_id: dict[str, RequirementNormalizationItem]
) -> dict:
    """Deterministic BM25/candidate-pool construction (Phase E) -- reuses
    `candidate_generation.py`/`dedup.py`/`bounded_retrieval.py`'s own capping helper exactly like
    the programmatic `build_bounded_context` path does; never duplicated."""
    candidate_memory = run.based_on_candidate_memory
    requirements = [
        r for r in run.based_on_jra.requirements.all() if r.requirement_id in normalization_by_id
    ]
    eligible = retrieve_eligible_pool(candidate_memory)
    deduped = deduplicate_claims(eligible.claims)

    search_requirements = []
    for requirement in requirements:
        normalization = normalization_by_id[requirement.requirement_id]
        search_requirements.append({
            "requirement_id": requirement.requirement_id,
            "text": normalize.build_search_text(requirement.text, normalization),
        })
    per_requirement = candidate_generation.generate_candidates(search_requirements, deduped)
    raw_pool = candidate_generation.union_candidate_pool(per_requirement)
    candidate_pool, _excluded = bounded_retrieval._cap_ranking_pool(raw_pool, per_requirement)

    return {
        "candidate_pool": [
            {
                "claim_id": c.claim_id, "text": c.text, "claim_type": c.claim_type,
                "subject_scope": c.subject_scope,
                "approved_engagement_ids": list(c.approved_engagement_ids),
                "grouped_claim_ids": list(c.grouped_claim_ids),
            }
            for c in candidate_pool
        ],
        "requirements": [{"requirement_id": r.requirement_id, "text": r.text} for r in requirements],
    }


def _prepare_match_input(
    run: AgentCandidateRun, ranking_output: RelevanceRankingOutput, candidate_pool_snapshot: list[dict]
) -> tuple[dict, dict]:
    """Validates ranked claim_ids against the pinned candidate pool (never a live re-query), caps
    the final selection, and adds the deterministic baseline-chronology layer (D-035/D-037) --
    computed independent of which claims AC_RANK itself selected, so Continental/Maruti/German-
    language evidence inclusion never depends on AC_RANK's own choice. Returns
    `(match_input, baseline_chronology_manifest)`."""
    candidate_memory = run.based_on_candidate_memory
    requirements = [
        r for r in run.based_on_jra.requirements.all() if static_requirements.classify(r.text) is None
    ]
    candidate_pool_ids = {c["claim_id"] for c in candidate_pool_snapshot}

    ranking_by_requirement = {item.requirement_id: item for item in ranking_output.rankings}
    selected_by_requirement: dict[str, list[str]] = {}
    for requirement in requirements:
        item = ranking_by_requirement.get(requirement.requirement_id)
        valid_ids = [cid for cid in (item.relevant_claim_ids if item else []) if cid in candidate_pool_ids]
        selected_by_requirement[requirement.requirement_id] = valid_ids

    selected_ids, _excluded = bounded_retrieval._cap_selected(selected_by_requirement)

    eligible = retrieve_eligible_pool(candidate_memory)
    deduped = deduplicate_claims(eligible.claims)
    by_id = {c.claim_id: c for c in deduped}
    final_claims = [
        RetrievedClaim(
            claim_id=cid, text=by_id[cid].text, claim_type=by_id[cid].claim_type,
            subject_scope=by_id[cid].subject_scope,
            approved_engagement_ids=by_id[cid].approved_engagement_ids,
        )
        for cid in sorted(selected_ids) if cid in by_id
    ]

    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )
    rule_result = select_bounded_rules(candidate_memory, [r.text for r in requirements])

    match_input = {
        "claims": [
            {
                "claim_id": c.claim_id, "text": c.text, "claim_type": c.claim_type,
                "subject_scope": c.subject_scope, "approved_engagement_ids": list(c.approved_engagement_ids),
            }
            for c in final_claims
        ],
        "engagements": [
            {
                "engagement_id": e.engagement_id, "approved_role_title": e.approved_role_title,
                "displayed_organization": e.displayed_organization, "location": e.location,
                "is_current": e.is_current, "duration_months": e.duration_months,
            }
            for e in eligible.engagements
        ],
        "rules": [
            {"rule_id": r.rule_id, "rule_type": r.rule_type, "text": r.text, "scope": r.scope}
            for r in rule_result.selected
        ],
        "requirements": [
            {"requirement_id": r.requirement_id, "category": r.category, "text": r.text} for r in requirements
        ],
    }

    job_relevant_claims = [
        dataclasses.replace(c, retrieval_reasons=(RETRIEVAL_REASON_JOB_RELEVANT,)) for c in final_claims
    ]
    baseline = build_baseline_chronology(candidate_memory.pk, approved_engagements)
    merged_claims = merge_retrieved_claims(job_relevant_claims, baseline.all_claims)
    baseline_manifest = build_baseline_manifest(
        candidate_memory=candidate_memory, approved_engagements=approved_engagements,
        baseline=baseline, merged_claims=merged_claims,
    )
    return match_input, baseline_manifest


# --- Finalize (Phase C.6) -------------------------------------------------------------------------


def finalize_run(run: AgentCandidateRun, *, lock_version: int) -> FitAssessment:
    """Requires AC_MATCH APPROVED. Atomically creates the new `FitAssessment` and every
    `RequirementAssessment` row, pins the `CandidateMemory` identity already pinned on `run`,
    persists the baseline-chronology manifest computed at AC_RANK-approval time, updates
    `JobApplication.current_fit_assessment`, and marks the run COMPLETED. Leaves Gate 1 unapproved
    and never runs M6."""
    match_stage = run.stages.get(stage=Stage.AC_MATCH)
    if run.status != AgentCandidateRun.Status.IN_PROGRESS:
        raise StagedRunError(f"Run is {run.status!r} -- cannot finalize.")
    if match_stage.status != Status.APPROVED:
        raise StageSequenceError("AC_MATCH must be APPROVED before finalization.")

    jra = run.based_on_jra
    requirements = list(jra.requirements.all())
    ordered_ids = [r.requirement_id for r in requirements]
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )

    local_items: list[AssessmentItemData] = []
    for requirement in requirements:
        kind = static_requirements.classify(requirement.text)
        if kind is None:
            continue
        local_result = static_requirements.assess(kind, requirement.text, approved_engagements)
        local_items.append(AssessmentItemData(
            requirement_id=requirement.requirement_id, disposition=local_result.disposition,
            explanation=local_result.explanation, gap_or_limitation=local_result.gap_or_limitation,
            supporting_memory_claim_ids=[], supporting_engagement_ids=local_result.supporting_engagement_ids,
        ))

    llm_output = AgentCandidateAssessment.model_validate(match_stage.approved_output)
    llm_items = [
        AssessmentItemData(
            requirement_id=item.requirement_id, disposition=item.disposition, explanation=item.explanation,
            gap_or_limitation=item.gap_or_limitation,
            supporting_memory_claim_ids=list(item.supporting_memory_claim_ids),
            supporting_engagement_ids=list(item.supporting_engagement_ids),
        )
        for item in llm_output.requirement_assessments
    ]

    match_input = match_stage.effective_input or {}
    valid_claim_ids = {c["claim_id"] for c in match_input.get("claims", [])}
    valid_engagement_ids = {e["engagement_id"] for e in match_input.get("engagements", [])}
    sanitized_items = sanitize_items(
        local_items + llm_items, valid_claim_ids=valid_claim_ids, valid_engagement_ids=valid_engagement_ids
    )
    all_items = ensure_full_coverage(sanitized_items, ordered_ids)

    retrieved_claim_ids = sorted(valid_claim_ids)
    retrieved_engagement_ids = sorted(valid_engagement_ids)
    retrieval_manifest = {
        "produced_by": "staged_run.finalize_run",
        "selected_claim_count": len(retrieved_claim_ids),
        "selected_engagement_count": len(retrieved_engagement_ids),
    }

    with transaction.atomic():
        locked_stage = AgentCandidateStage.objects.select_for_update().get(pk=match_stage.pk)
        if locked_stage.lock_version != lock_version:
            raise ConcurrentStageModificationError(
                f"Stage {locked_stage.stage!r} was modified by another request (expected "
                f"lock_version {lock_version}, actual {locked_stage.lock_version}) -- reload and retry."
            )
        if locked_stage.status != Status.APPROVED:
            raise StageSequenceError("AC_MATCH must be APPROVED before finalization.")

        locked_application = JobApplication.objects.select_for_update().get(pk=run.job_application_id)
        next_fa_version = (
            locked_application.fit_assessments.aggregate(Max("version"))["version__max"] or 0
        ) + 1
        fit_assessment = FitAssessment.objects.create(
            job_application=locked_application, version=next_fa_version, based_on_jra=jra,
            based_on_candidate_memory=run.based_on_candidate_memory,
            retrieved_claim_ids=retrieved_claim_ids,
            retrieved_engagement_ids=retrieved_engagement_ids,
            retrieval_manifest=retrieval_manifest,
            baseline_chronology_manifest=run.baseline_chronology_manifest,
        )
        for item in all_items:
            RequirementAssessment.objects.create(
                fit_assessment=fit_assessment, requirement_id=item.requirement_id,
                disposition=item.disposition,
                supporting_memory_claim_ids=item.supporting_memory_claim_ids,
                supporting_engagement_ids=item.supporting_engagement_ids,
                explanation=item.explanation,
                gap_or_limitation=item.gap_or_limitation,
            )
        locked_application.record_fit_assessment(fit_assessment)

        run.resulting_fit_assessment = fit_assessment
        run.status = AgentCandidateRun.Status.COMPLETED
        run.completed_at = timezone.now()
        run.save(update_fields=["resulting_fit_assessment", "status", "completed_at", "updated_at"])

    run.job_application = locked_application
    return fit_assessment
