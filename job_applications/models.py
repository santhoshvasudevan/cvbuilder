from django.db import models


class StageIdentifier(models.TextChoices):
    """Canonical stage vocabulary owned by the workflow layer.

    See docs/ARCHITECTURE.md Section 5 ("Canonical Stage Vocabulary") and V2-D022. Shared by
    StageRun, JobApplicationStageState, and (from M2 onward) llm_provider.StageModelAssignment.
    """

    # LLM-capable stages (see LLM_CAPABLE_STAGES below).
    AJ_ANALYZE = "AJ_ANALYZE", "AJ Analyze"
    AC_ASSESS = "AC_ASSESS", "AC Assess"
    APS_POSITION = "APS_POSITION", "APS Position"
    AB_PLAN = "AB_PLAN", "AB Plan"
    AB_DRAFT = "AB_DRAFT", "AB Draft"
    AB_CRITIQUE = "AB_CRITIQUE", "AB Critique"
    AB_REFINE = "AB_REFINE", "AB Refine"
    QUALITY_EVAL = "QUALITY_EVAL", "Quality Evaluator"

    # Deterministic/workflow stages -- never assigned a StageModelAssignment.
    CANDIDATE_CONTEXT_BUILD = "CANDIDATE_CONTEXT_BUILD", "Candidate Context Build"
    VALIDATE_DRAFT = "VALIDATE_DRAFT", "Validate Draft"
    VALIDATE_REFINED = "VALIDATE_REFINED", "Validate Refined"
    GATE_1 = "GATE_1", "Human Gate 1"
    GATE_2 = "GATE_2", "Human Gate 2"
    RENDER = "RENDER", "Render"


LLM_CAPABLE_STAGES = frozenset(
    {
        StageIdentifier.AJ_ANALYZE,
        StageIdentifier.AC_ASSESS,
        StageIdentifier.APS_POSITION,
        StageIdentifier.AB_PLAN,
        StageIdentifier.AB_DRAFT,
        StageIdentifier.AB_CRITIQUE,
        StageIdentifier.AB_REFINE,
        StageIdentifier.QUALITY_EVAL,
    }
)


class JobApplication(models.Model):
    """Small, stable aggregate root (docs/ARCHITECTURE.md Section 5, V2-D022).

    Holds job identity, pipeline phase, and application outcome only. It does not hold a direct
    pointer per pipeline artifact -- see StageRun / JobApplicationStageState below.
    """

    class SourceType(models.TextChoices):
        URL = "URL", "URL"
        PASTED_TEXT = "PASTED_TEXT", "Pasted text"

    class PipelinePhase(models.TextChoices):
        NEW = "NEW", "New"
        ANALYSIS = "ANALYSIS", "Analysis"
        POSITIONING = "POSITIONING", "Positioning"
        PREPARATION = "PREPARATION", "Preparation"
        READY = "READY", "Ready"

    class ApplicationOutcome(models.TextChoices):
        NOT_APPLIED = "NOT_APPLIED", "Not applied"
        APPLIED = "APPLIED", "Applied"
        INTERVIEWING = "INTERVIEWING", "Interviewing"
        REJECTED = "REJECTED", "Rejected"

    employer = models.CharField(max_length=255)
    job_title = models.CharField(max_length=255)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_url = models.URLField(blank=True, default="")
    pipeline_phase = models.CharField(
        max_length=20, choices=PipelinePhase.choices, default=PipelinePhase.NEW
    )
    application_outcome = models.CharField(
        max_length=20,
        choices=ApplicationOutcome.choices,
        default=ApplicationOutcome.NOT_APPLIED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employer} — {self.job_title}"


class StageRun(models.Model):
    """One execution attempt/version of a workflow stage (docs/ARCHITECTURE.md Section 5).

    M1 implements the provider-independent fields only. ``provider``, ``model``,
    ``reasoning_level``, ``max_output_tokens``, and ``temperature`` (docs/ARCHITECTURE.md
    Section 5's full schema) are added by a follow-up M2 migration once llm_provider.LLMProvider
    / llm_provider.LLMModel exist -- see V2-D036. This is an implementation-sequencing note, not
    an architecture change.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        EDITED = "EDITED", "Edited"
        APPROVED = "APPROVED", "Approved"

    job_application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="stage_runs"
    )
    stage = models.CharField(max_length=40, choices=StageIdentifier.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    input_snapshot = models.JSONField(default=dict)
    raw_structured_output = models.JSONField(null=True, blank=True)
    working_output = models.JSONField(null=True, blank=True)
    lock_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.stage} [{self.status}] for JobApplication #{self.job_application_id}"


class JobApplicationStageState(models.Model):
    """Per JobApplication + stage: which StageRun is current and which is approved.

    docs/ARCHITECTURE.md Section 5, V2-D022.
    """

    job_application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="stage_states"
    )
    stage = models.CharField(max_length=40, choices=StageIdentifier.choices)
    current_stage_run = models.ForeignKey(
        StageRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_stage_run = models.ForeignKey(
        StageRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "stage"], name="unique_job_application_stage"
            )
        ]

    def __str__(self):
        return f"JobApplication #{self.job_application_id}: {self.stage}"
