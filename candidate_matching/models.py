"""Agent Candidate's persisted output (requirements.md Sec 6/16, D-014, D-019, M5).

`FitAssessment` is append-only/versioned per D-010, mirroring `job_intake.JobRequirementAnalysis`
exactly: a completed version's own fields and its `RequirementAssessment` children are never
edited in place -- a re-run (Gate-1 feedback targeting Agent Candidate, or a re-run triggered by a
new JobRequirementAnalysis version) creates a new version instead.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from llm_provider.models import LLMCallLog


class FitAssessment(models.Model):
    job_application = models.ForeignKey(
        "job_applications.JobApplication", on_delete=models.CASCADE, related_name="fit_assessments"
    )
    version = models.PositiveIntegerField()
    based_on_jra = models.ForeignKey(
        "job_intake.JobRequirementAnalysis",
        on_delete=models.PROTECT,
        related_name="fit_assessments",
        help_text="The exact JobRequirementAnalysis version this assessment was built against "
        "(D-006 freshness identity -- compared against JobApplication.current_jra_id, never a "
        "timestamp).",
    )
    based_on_candidate_memory = models.ForeignKey(
        "candidate_memory.CandidateMemory",
        on_delete=models.PROTECT,
        related_name="fit_assessments",
        null=True,
        blank=True,
        help_text="D-037 pinned-evidence-identity correction: the exact CandidateMemory revision "
        "that was ACTIVE when this FitAssessment was created -- the frozen identity Agent Builder "
        "(M6) must use for this assessment forever after, regardless of which CandidateMemory "
        "revision is ACTIVE at M6 execution time. Nullable only for pre-D-037 legacy rows (e.g. "
        "FitAssessment id 9), which never recorded this identity and therefore have no valid "
        "baseline_chronology_manifest either -- every FitAssessment created through "
        "candidate_matching.services.fit_assessment.build_fit_assessment from this correction "
        "onward always sets this field; the production M6 path "
        "(resume_builder.services.context.build_builder_context) fails closed on a legacy row "
        "with this field null rather than treating null as 'use whatever is ACTIVE now'.",
    )
    baseline_chronology_manifest = models.JSONField(
        default=dict,
        blank=True,
        help_text="D-037 pinned-evidence-identity correction: the deterministic, zero-LLM "
        "baseline-chronology manifest computed once, at this FitAssessment's own creation time, "
        "against based_on_candidate_memory and the CareerEngagement/ClaimEngagementMapping state "
        "as it stood at that exact moment -- see candidate_matching.services.baseline_chronology."
        "build_baseline_manifest for the schema and candidate_matching.services.fit_assessment for "
        "where it is computed and persisted atomically with this row. Never recomputed at M6 time: "
        "resume_builder.services.context.build_builder_context reads this exact persisted value, "
        "never live CareerEngagement.approval_status/ClaimEngagementMapping.status/the currently "
        "ACTIVE CandidateMemory. An empty dict (the default) means 'no manifest recorded' -- true "
        "only for pre-D-037 legacy rows; a row created after this correction always has a non-"
        "empty, schema-validated manifest.",
    )
    retrieved_claim_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Exactly which confirmed, resume-eligible MemoryClaim IDs were retrieved and "
        "made available to Agent Candidate for this run -- an explicit, inspectable audit record "
        "of the bounded context actually provided, never the full CandidateMemory.",
    )
    retrieved_engagement_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Exactly which APPROVED CareerEngagement IDs were made available for this run.",
    )
    retrieval_manifest = models.JSONField(
        default=dict,
        blank=True,
        help_text="Inspectable record of the bounded retrieval pipeline for this run (audit "
        "hardening, 2026-09-03): eligible/duplicate/candidate/selected counts, excluded-with-"
        "reason counts, per-requirement selected claim IDs, rule counts, the estimated request "
        "token size, and the cap configuration in effect -- see "
        "candidate_matching.services.bounded_retrieval.RetrievalManifest. Never raw provider "
        "prompts or responses.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "version"], name="unique_fit_assessment_version_per_application"
            )
        ]
        ordering = ["job_application_id", "version"]

    def __str__(self) -> str:
        return f"FitAssessment(app={self.job_application_id}, v{self.version})"

    def save(self, *args, **kwargs):
        # Append-only (D-010), the same honest, application-layer-only guarantee as
        # JobRequirementAnalysis -- see that model's save()/delete() docstring for the known,
        # accepted bypasses (QuerySet.update()/bulk_update()/raw SQL, cascade deletion).
        if self.pk is not None:
            raise ImmutableFitAssessmentError(
                "FitAssessment is append-only once created -- create a new version instead of "
                "editing this one."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableFitAssessmentError(
            "FitAssessment is append-only -- it is never deleted through the normal ORM instance "
            "API. (This guard does not cover cascade deletion triggered by deleting the owning "
            "JobApplication, nor QuerySet-level/raw-SQL bulk deletes -- see save() above.)"
        )


class ImmutableFitAssessmentError(Exception):
    pass


class RequirementAssessment(models.Model):
    class Disposition(models.TextChoices):
        MATCH = "MATCH", "Match"
        PARTIAL = "PARTIAL", "Partial"
        GAP = "GAP", "Gap"
        UNKNOWN = "UNKNOWN", "Unknown"

    fit_assessment = models.ForeignKey(
        FitAssessment, on_delete=models.CASCADE, related_name="requirement_assessments"
    )
    # Copied from job_intake.JobRequirement.requirement_id (e.g. "JR-001") rather than FK'd --
    # this row must remain fully readable/auditable even if a future data-retention policy ever
    # needs to prune old JobRequirement rows independently, and it keeps this app from taking a
    # hard schema dependency on job_intake's internal PK shape.
    requirement_id = models.CharField(max_length=16)
    disposition = models.CharField(max_length=10, choices=Disposition.choices)
    supporting_memory_claim_ids = models.JSONField(default=list, blank=True)
    supporting_engagement_ids = models.JSONField(default=list, blank=True)
    explanation = models.TextField(blank=True, default="")
    gap_or_limitation = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fit_assessment", "requirement_id"], name="unique_requirement_assessment_per_fit"
            )
        ]
        ordering = ["fit_assessment_id", "requirement_id"]

    def __str__(self) -> str:
        return f"{self.requirement_id}: {self.disposition}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableFitAssessmentError(
                "RequirementAssessment rows belong to an append-only FitAssessment version and "
                "are never edited in place."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableFitAssessmentError(
            "RequirementAssessment rows are never deleted through the normal ORM instance API."
        )

    @property
    def has_evidence(self) -> bool:
        return bool(self.supporting_memory_claim_ids) or bool(self.supporting_engagement_ids)


# --- Operator-controlled, persistent, resumable M5 staged workflow (2026-09-08, D-041) ----------
#
# `build_fit_assessment` (services/fit_assessment.py) remains the programmatic, all-three-calls-
# in-one-go entry point (kept for the fake-adapter test suite and any future non-interactive use);
# the normal operator-facing UI path is `services/staged_run.py`, which drives an
# `AgentCandidateRun`/`AgentCandidateStage` through three individually-authorized provider calls.
# See docs/DECISIONS.md D-041 for the full rationale and docs/ARCHITECTURE.md for a state diagram.


class AgentCandidateRun(models.Model):
    """One M5 attempt for one `JobApplication`: pins the exact `JobRequirementAnalysis` and
    `CandidateMemory` revision this attempt is judged against, for the lifetime of the run --
    mirrors `FitAssessment.based_on_jra`/`based_on_candidate_memory`'s own pinning, just one step
    earlier (at run start rather than at finalization). Append-only per `JobApplication`
    (`version`, unique together) -- a new attempt is always a new run, never an edit of a previous
    one, exactly like `FitAssessment`/`JobRequirementAnalysis` (D-010)."""

    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    job_application = models.ForeignKey(
        "job_applications.JobApplication", on_delete=models.CASCADE, related_name="agent_candidate_runs"
    )
    version = models.PositiveIntegerField()
    based_on_jra = models.ForeignKey(
        "job_intake.JobRequirementAnalysis", on_delete=models.PROTECT, related_name="agent_candidate_runs"
    )
    based_on_candidate_memory = models.ForeignKey(
        "candidate_memory.CandidateMemory", on_delete=models.PROTECT, related_name="agent_candidate_runs",
        help_text="The CandidateMemory revision that was ACTIVE when this run started -- pinned "
        "for the run's entire lifetime, mirroring FitAssessment.based_on_candidate_memory (D-037). "
        "A later CandidateMemory activation never changes what an in-progress or completed run "
        "is judged against.",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    resulting_fit_assessment = models.ForeignKey(
        FitAssessment, on_delete=models.PROTECT, null=True, blank=True, related_name="agent_candidate_run",
        help_text="Set only by finalize_run, atomically with FitAssessment creation. Null for every "
        "run that has not yet reached AC_MATCH approval + explicit finalization.",
    )
    baseline_chronology_manifest = models.JSONField(
        default=dict, blank=True,
        help_text="Computed once, at AC_RANK-approval time (Phase E), independent of which claims "
        "AC_RANK itself selected -- see candidate_matching.services.baseline_chronology. Copied "
        "verbatim onto FitAssessment.baseline_chronology_manifest at finalization.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "version"], name="unique_ac_run_version_per_application"
            )
        ]
        ordering = ["job_application_id", "version"]

    def __str__(self) -> str:
        return f"AgentCandidateRun(app={self.job_application_id}, v{self.version}, {self.status})"


class AgentCandidateStage(models.Model):
    """One of the three individually-authorized M5 provider calls (AC_NORMALIZE/AC_RANK/AC_MATCH)
    belonging to one `AgentCandidateRun`. Exactly one row per (run, stage) -- there is no separate
    "revision" row for the stage itself; an operator re-preparing/re-editing/re-running a stage
    mutates this same row's state (its full edit/output history is preserved in `revisions`,
    append-only, never deleted -- see `AgentCandidateStageRevision`)."""

    class Stage(models.TextChoices):
        AC_NORMALIZE = (
            "AC_NORMALIZE",
            "Agent Candidate - Requirement Normalization (bounded query expansion, D-015/D-020)",
        )
        AC_RANK = "AC_RANK", "Agent Candidate - Relevance Ranking (D-015 bounded step)"
        AC_MATCH = "AC_MATCH", "Agent Candidate - Match"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft (blocked -- a prior stage is not yet approved)"
        READY = "READY", "Ready to run"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        EDITED = "EDITED", "Edited (operator output differs from provider output)"
        APPROVED = "APPROVED", "Approved"
        INVALIDATED = "INVALIDATED", "Invalidated (an upstream stage was re-edited)"

    class ValidationState(models.TextChoices):
        UNVALIDATED = "UNVALIDATED", "Not yet validated"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    run = models.ForeignKey(AgentCandidateRun, on_delete=models.CASCADE, related_name="stages")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    stage_order = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    # -- Input: the run-local, editable snapshot (Phase D) -----------------------------------
    prepared_input = models.JSONField(
        null=True, blank=True,
        help_text="The deterministically-computed input for this stage (never touches canonical "
        "JobRequirement/MemoryClaim/CandidateMemory/JobRequirementAnalysis records -- a run-local "
        "snapshot only). Set once, when the stage becomes READY; immutable thereafter -- an "
        "operator edit is layered on top via edited_input, never written back here.",
    )
    edited_input = models.JSONField(
        null=True, blank=True,
        help_text="The operator's current edit of prepared_input, or null if unedited. The "
        "*effective* input this stage's provider call actually uses is edited_input if set, else "
        "prepared_input.",
    )
    input_hash = models.CharField(
        max_length=64, blank=True,
        help_text="sha256 of the effective input actually sent, recorded at execution time.",
    )

    # -- Model/provider configuration snapshot (Phase B) --------------------------------------
    selected_provider = models.ForeignKey(
        "llm_provider.LLMProvider", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+",
    )
    selected_model = models.ForeignKey(
        "llm_provider.LLMModel", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    selection_source = models.CharField(max_length=10, blank=True, choices=LLMCallLog.SelectionSource.choices)
    reasoning_effort = models.CharField(max_length=10, blank=True)
    requested_output_budget = models.PositiveIntegerField(null=True, blank=True)
    effective_output_budget = models.PositiveIntegerField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)

    # -- Output: immutable provider result + editable operator copy + frozen approval --------
    provider_output = models.JSONField(
        null=True, blank=True,
        help_text="The immutable, original structured provider output (schema-validated). Never "
        "overwritten once set -- an operator edit is stored in operator_output/revisions, never "
        "here.",
    )
    provider_output_hash = models.CharField(max_length=64, blank=True)
    operator_output = models.JSONField(
        null=True, blank=True,
        help_text="The current editable copy -- starts as a plain copy of provider_output on "
        "SUCCEEDED, may be edited any number of times (each edit recorded as a new "
        "AgentCandidateStageRevision, never deleting a prior one).",
    )
    approved_output = models.JSONField(
        null=True, blank=True,
        help_text="A frozen copy of operator_output at the moment of approval. Never mutated after "
        "approval -- a later edit invalidates the approval (status back to EDITED/DRAFT for "
        "downstream) rather than changing this field in place.",
    )
    approved_output_hash = models.CharField(max_length=64, blank=True)

    validation_state = models.CharField(
        max_length=12, choices=ValidationState.choices, default=ValidationState.UNVALIDATED, blank=True
    )
    validation_errors = models.JSONField(default=list, blank=True)

    llm_call_log = models.ForeignKey(
        "llm_provider.LLMCallLog", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="agent_candidate_stages",
    )

    failure_category = models.CharField(max_length=20, blank=True)
    failure_summary = models.TextField(
        blank=True, help_text="Sanitized message only -- never raw provider request/response content."
    )

    executed_at = models.DateTimeField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    lock_version = models.PositiveIntegerField(
        default=0,
        help_text="Optimistic-concurrency guard: every mutating service call must be given the "
        "lock_version it last read and increments it on success; a stale value raises "
        "ConcurrentStageModificationError rather than silently overwriting a concurrent change.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "stage"], name="unique_stage_per_run"),
            models.UniqueConstraint(fields=["run", "stage_order"], name="unique_stage_order_per_run"),
            models.CheckConstraint(
                check=~Q(status="APPROVED") | Q(validation_state="VALID"),
                name="approved_stage_requires_valid_output",
            ),
        ]
        ordering = ["run_id", "stage_order"]

    def __str__(self) -> str:
        return f"AgentCandidateStage(run={self.run_id}, {self.stage}, {self.status})"

    @property
    def effective_input(self):
        return self.edited_input if self.edited_input is not None else self.prepared_input


class AgentCandidateStageRevision(models.Model):
    """Append-only edit history for one `AgentCandidateStage` -- never deleted, never edited in
    place (mirrors `FitAssessment`/`RequirementAssessment`'s own append-only convention). One row
    per input edit or output edit; `kind`/`revision_number` together give a stable, ordered audit
    trail an operator (or a future UI) can always replay, satisfying "historical revisions remain
    available" without needing a separate row-per-stage-attempt model."""

    class Kind(models.TextChoices):
        INPUT = "INPUT", "Input edit"
        OUTPUT = "OUTPUT", "Output edit"

    stage = models.ForeignKey(AgentCandidateStage, on_delete=models.CASCADE, related_name="revisions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    revision_number = models.PositiveIntegerField()
    data = models.JSONField()
    validation_state = models.CharField(max_length=12, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["stage", "kind", "revision_number"], name="unique_stage_revision"
            )
        ]
        ordering = ["stage_id", "kind", "revision_number"]

    def __str__(self) -> str:
        return f"AgentCandidateStageRevision(stage={self.stage_id}, {self.kind}#{self.revision_number})"
