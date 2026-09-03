"""`JobApplication` (D-012, APPROVED): the aggregate/root entity for one tracked job vacancy.

Only the M4-required shape is implemented here. `current_fit_assessment`/`current_resume_draft`
FKs are deliberately NOT added yet -- `FitAssessment` (M5, `candidate_matching`) and `ResumeDraft`
(M6, `resume_builder`) don't exist yet, so forward-referencing them now would mean either a
premature migration dependency or a string FK to a model that can't validate. This mirrors the
same precedent M1 already set for this app itself (see docs/IMPLEMENTATION_PLAN.md M1): each
current-version pointer is added in the milestone that introduces the model it points to. Adding
those two FKs is explicitly M5/M6 scope, not M4.
"""

from __future__ import annotations

from django.db import models


class JobApplication(models.Model):
    class PipelinePhase(models.TextChoices):
        NEW = "NEW", "New"
        ANALYSIS = "ANALYSIS", "Analysis"
        PREPARATION = "PREPARATION", "Preparation"
        READY = "READY", "Ready"

    class ApplicationOutcome(models.TextChoices):
        NOT_APPLIED = "NOT_APPLIED", "Not applied"
        APPLIED = "APPLIED", "Applied"
        INTERVIEWING = "INTERVIEWING", "Interviewing"
        REJECTED = "REJECTED", "Rejected"

    # String FK (not yet a real model import) would be needed if job_intake depended on this app;
    # instead job_intake.JobRequirementAnalysis FKs to JobApplication, and this points back to
    # "the current one" -- so the FK target already exists by the time this is defined.
    current_jra = models.ForeignKey(
        "job_intake.JobRequirementAnalysis",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The JobRequirementAnalysis version currently considered authoritative for "
        "this application. Set only after a full successful analysis persists (see "
        "job_intake.services.intake) -- never points at a partially-built or failed version.",
    )
    pipeline_phase = models.CharField(
        max_length=20, choices=PipelinePhase.choices, default=PipelinePhase.NEW
    )
    application_outcome = models.CharField(
        max_length=20, choices=ApplicationOutcome.choices, default=ApplicationOutcome.NOT_APPLIED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"JobApplication({self.pk}, {self.pipeline_phase})"

    def advance_to_analysis(self, *, jra) -> None:
        """The one M4-owned phase transition: NEW -> ANALYSIS, with `current_jra` set atomically
        in the same call. Rejects any other starting phase rather than silently allowing a
        backwards or repeated transition -- a re-analysis path (Gate-1 feedback) is M5 scope and
        will need its own, separately-considered transition rule, not this one reused blindly.
        """
        if self.pipeline_phase != self.PipelinePhase.NEW:
            raise InvalidPhaseTransitionError(
                f"Cannot advance to ANALYSIS from phase {self.pipeline_phase!r} -- only NEW may "
                "transition this way."
            )
        self.current_jra = jra
        self.pipeline_phase = self.PipelinePhase.ANALYSIS
        self.save(update_fields=["current_jra", "pipeline_phase", "updated_at"])


class InvalidPhaseTransitionError(Exception):
    pass
