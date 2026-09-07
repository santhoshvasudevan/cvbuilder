"""`JobApplication` (D-012, APPROVED): the aggregate/root entity for one tracked job vacancy.

M4 added `current_jra`, M5 added `current_fit_assessment`, and M6 (this update) adds
`current_resume_draft` -- the last of the three current-version pointers D-012 describes,
completing the aggregate.
"""

from __future__ import annotations

from django.db import models, transaction


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
    current_fit_assessment = models.ForeignKey(
        "candidate_matching.FitAssessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The FitAssessment version currently considered authoritative for this "
        "application (M5). Set whenever a new version is built (initial run or a Gate-1 "
        "feedback re-run) -- 'current' tracks the latest version, not necessarily an "
        "operator-approved one. Freshness (D-006) compares this pointer's own current_jra_id "
        "against JobApplication.current_jra_id, never a timestamp.",
    )
    current_resume_draft = models.ForeignKey(
        "resume_builder.ResumeDraft",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The ResumeDraft version currently considered authoritative for this "
        "application (M6). Set whenever a new version is built -- 'current' tracks the latest "
        "version, not necessarily the confirmed one. Freshness (D-006) compares this pointer's "
        "own based_on_fit_assessment_id against JobApplication.current_fit_assessment_id.",
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

    def record_jra(self, jra) -> None:
        """Pointer update only, no phase check -- used by a Gate-1 feedback re-run targeting
        Agent Jobber (M5), which creates a new JobRequirementAnalysis version for an application
        already past NEW. `pipeline_phase` is left untouched; a mismatch between this pointer and
        any existing FitAssessment's `based_on_jra_id` is a freshness signal (D-006), not
        something this method resolves itself."""
        self.current_jra = jra
        self.save(update_fields=["current_jra", "updated_at"])

    def record_fit_assessment(self, fit_assessment) -> None:
        """Pointer update only, no phase check -- called whenever a new FitAssessment version is
        built (the initial Agent Candidate run, or a Gate-1 feedback re-run targeting Agent
        Candidate). 'Current' tracks the latest version, mirroring `current_jra`'s own semantics
        (D-012) -- Gate-1 approval is a separate, later action (`approve_gate1`)."""
        self.current_fit_assessment = fit_assessment
        self.save(update_fields=["current_fit_assessment", "updated_at"])

    def approve_gate1(self) -> None:
        """Human Review Gate 1 approval (HITL-001/002/004/005): a plain DB-state transition,
        checked fresh -- never a paused process. Requires a `current_fit_assessment` that is
        actually based on the current `current_jra` (D-006 freshness) -- approving a stale
        assessment is refused outright, in every phase, not just from ANALYSIS.

        Audit hardening (2026-09-03): locks this row with `select_for_update()` for the whole
        check-and-transition so two concurrent approvals can never race into an inconsistent
        state, and is idempotent -- calling this again once already in PREPARATION/READY (i.e.
        Gate 1 was already approved and nothing about the current, non-stale FitAssessment has
        changed since) is a safe no-op, not an error, so a double-click or a retried request
        never surfaces a confusing failure for an action that already succeeded.
        """
        with transaction.atomic():
            locked = JobApplication.objects.select_for_update().get(pk=self.pk)
            if locked.pipeline_phase not in (
                self.PipelinePhase.ANALYSIS, self.PipelinePhase.PREPARATION, self.PipelinePhase.READY,
            ):
                raise InvalidPhaseTransitionError(
                    f"Cannot approve Gate 1 from phase {locked.pipeline_phase!r}."
                )
            if locked.current_fit_assessment_id is None:
                raise GateNotReadyError("No FitAssessment exists yet -- run Agent Candidate first.")
            if locked.current_fit_assessment.based_on_jra_id != locked.current_jra_id:
                raise StaleAssessmentError(
                    "The current FitAssessment is based on a superseded JobRequirementAnalysis "
                    "version -- re-run Agent Candidate against the current analysis before approving."
                )
            if locked.pipeline_phase == self.PipelinePhase.ANALYSIS:
                locked.pipeline_phase = self.PipelinePhase.PREPARATION
                locked.save(update_fields=["pipeline_phase", "updated_at"])
        self.pipeline_phase = locked.pipeline_phase

    def record_resume_draft(self, resume_draft) -> None:
        """Pointer update only, no phase check -- called whenever a new ResumeDraft version is
        built (the initial Agent Builder run, or a Gate-2 feedback re-run)."""
        self.current_resume_draft = resume_draft
        self.save(update_fields=["current_resume_draft", "updated_at"])

    def approve_gate2(self) -> None:
        """Human Review Gate 2 approval (HITL-003): requires a `current_resume_draft` that is
        actually based on the current `current_fit_assessment` (D-006 freshness) -- approving a
        stale draft is refused outright. Confirms the draft itself (`ResumeDraft.confirm()`) and
        advances the phase together, atomically.

        Audit hardening (2026-09-03): `select_for_update()`-locked for the whole check-and-
        transition, and idempotent -- calling this again once already READY with the same,
        already-confirmed, non-stale draft is a safe no-op rather than an error (mirrors
        `approve_gate1`)."""
        with transaction.atomic():
            locked = JobApplication.objects.select_for_update().get(pk=self.pk)
            if locked.pipeline_phase not in (self.PipelinePhase.PREPARATION, self.PipelinePhase.READY):
                raise InvalidPhaseTransitionError(
                    f"Cannot approve Gate 2 from phase {locked.pipeline_phase!r}."
                )
            if locked.current_resume_draft_id is None:
                raise GateNotReadyError("No ResumeDraft exists yet -- run Agent Builder first.")
            if locked.current_resume_draft.based_on_fit_assessment_id != locked.current_fit_assessment_id:
                raise StaleAssessmentError(
                    "The current ResumeDraft is based on a superseded FitAssessment version -- "
                    "re-run Agent Builder against the current assessment before approving."
                )
            if locked.pipeline_phase == self.PipelinePhase.PREPARATION:
                if not locked.current_resume_draft.is_confirmed:
                    locked.current_resume_draft.confirm()
                locked.pipeline_phase = self.PipelinePhase.READY
                locked.save(update_fields=["pipeline_phase", "updated_at"])
        self.pipeline_phase = locked.pipeline_phase

    def begin_revision_from_ready(self) -> None:
        """D-037 corrective fix (supersedes D-036's no-op `begin_new_version_from_ready`): the
        sole legal READY -> ANALYSIS backward transition, reachable only through the explicit,
        named `job_applications.services.begin_new_version_from_ready` authorization checkpoint --
        never a bare `pipeline_phase` write. This reuses the existing phase enum rather than
        introducing a new state: an application in ANALYSIS with a non-null, non-stale
        `current_fit_assessment`/`current_resume_draft` is already a state `resolve_next_action`
        (job_applications/services.py) handles correctly ("Review Gate 1"), and `approve_gate1`'s
        own ANALYSIS -> PREPARATION transition is exactly the real, enforced "Gate 1 must approve
        again" gate this workflow needs -- `resume_builder.services.build.build_resume_draft`
        already refuses to run while phase is ANALYSIS (it requires PREPARATION or READY), so an
        operator cannot skip straight to Agent Builder on the strength of the old, now-stale-phase
        FitAssessment.

        No pointer is touched here: `current_jra`/`current_fit_assessment`/`current_resume_draft`/
        `application_outcome` are left exactly as they stood at READY. Creating the actual new
        `FitAssessment` version (via `candidate_matching.services.fit_assessment.
        build_fit_assessment`, e.g. through `reviews.services.run_agent_candidate`) and re-approving
        Gate 1/Gate 2 are separate, already-existing, already-guarded actions -- this method only
        reopens the gate; it never runs anything through it itself.

        Refuses (`InvalidPhaseTransitionError`) unless currently READY, and refuses
        (`StaleAssessmentError`) if the chain is already stale relative to upstream changes (e.g. a
        Gate-1 feedback re-run after Gate 2 was already approved) -- that is a different, already-
        handled recovery path (`resolve_next_action`'s existing "Upstream changed since approval"
        guidance), not this deliberate, clean-chain revision workflow. Locked with
        `select_for_update()` for the whole check-and-transition, mirroring `approve_gate1`/
        `approve_gate2`, so a duplicate/concurrent submission is safely rejected rather than
        double-applied.
        """
        with transaction.atomic():
            locked = JobApplication.objects.select_for_update().get(pk=self.pk)
            if locked.pipeline_phase != self.PipelinePhase.READY:
                raise InvalidPhaseTransitionError(
                    f"Cannot begin a new revision from phase {locked.pipeline_phase!r} -- only a "
                    "READY application may begin a deliberate new revision (an application still "
                    "mid-pipeline already has its ordinary Gate 1/Gate 2 controls available)."
                )
            fit_assessment = locked.current_fit_assessment
            draft = locked.current_resume_draft
            fit_assessment_stale = (
                fit_assessment is not None and fit_assessment.based_on_jra_id != locked.current_jra_id
            )
            draft_stale = (
                draft is not None
                and fit_assessment is not None
                and draft.based_on_fit_assessment_id != fit_assessment.pk
            )
            if fit_assessment_stale or draft_stale:
                raise StaleAssessmentError(
                    "This application is READY but already stale relative to an upstream change -- "
                    "resolve staleness at Gate 1 through the ordinary staleness-recovery action "
                    "instead of beginning a new deliberate revision."
                )
            locked.pipeline_phase = self.PipelinePhase.ANALYSIS
            locked.save(update_fields=["pipeline_phase", "updated_at"])
        self.pipeline_phase = locked.pipeline_phase


class InvalidPhaseTransitionError(Exception):
    pass


class GateNotReadyError(Exception):
    pass


class StaleAssessmentError(Exception):
    pass
