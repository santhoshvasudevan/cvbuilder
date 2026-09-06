"""M7 integration services: dashboard status derivation, chain-wide freshness (HITL-007), next-
valid-action resolution, and the operator-controlled `application_outcome` action
(requirements.md Sec 17, D-006).

Everything here is read-only computation over already-persisted state, with the single exception
of `set_application_outcome`, which is the one new state-changing action this module owns. No
function here ever sets `pipeline_phase`, a current-version pointer, or a confirmation field --
those remain owned exclusively by `JobApplication`'s own guarded transition methods and
`ResumeDraft.confirm()`, called only from `reviews.services`/`resume_builder.services.build`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import reverse

from .models import JobApplication


class InvalidOutcomeTransitionError(Exception):
    pass


class RevisionNotAuthorizedError(Exception):
    pass


def begin_new_version_from_ready(application: JobApplication) -> None:
    """The canonical, explicit-authorization entry point for the READY-revision workflow (D-035
    investigation, 2026-09-06): generating a new artifact chain for an application that has
    already reached `READY` (e.g. the target posting was updated, or the operator wants Agent
    Candidate/Agent Builder re-run against a corrected CandidateMemory).

    This function performs **no mutation** -- it is a required, explicit precondition checkpoint a
    caller (a future dedicated UI control, or an operator-driven script) must call and have
    succeed *before* re-entering Gate 1 for a `READY` application, so that "start a new version"
    is always a deliberate, named action distinct from Gate 1's ordinary re-run/feedback
    controls (which already place no `pipeline_phase` precondition on their own, by design, since
    they must also work mid-pipeline). Formalizing this as its own function -- rather than leaving
    the workflow only reachable as an unlabeled side effect of navigating to Gate 1's page --
    is what makes the authorization explicit and auditable at the call site, without requiring any
    new persisted state or a migration.

    Every actual state-changing step of the workflow this authorizes is already implemented and
    already enforces every other required guarantee on its own:

    - `reviews.services.run_agent_candidate` / `submit_gate1_feedback` (targeting AC, or AJ to
      first re-analyze the posting) -- always creates a new, append-only `FitAssessment` (or
      `JobRequirementAnalysis`) version and repoints `JobApplication.current_*`; the previous
      `ResumeDraft`/`FitAssessment`/`JobRequirementAnalysis` versions are never modified (D-010),
      preserved exactly as immutable history.
    - `job_applications.models.JobApplication.approve_gate1` -- refuses a stale `FitAssessment`
      (D-006 freshness, `based_on_jra_id` vs. `current_jra_id`), and only transitions through the
      real `ANALYSIS`/`PREPARATION`/`READY` phases via its own guarded method -- never a direct
      `pipeline_phase` field write.
    - `reviews.services.run_agent_builder` / `submit_gate2_feedback` -- always creates a new,
      append-only `ResumeDraft` version (D-010); the prior `ResumeDraft` (e.g. `ResumeDraft` 4 for
      the real `JobApplication` 9) is never edited, deleted, or regenerated in place.
    - `job_applications.models.JobApplication.approve_gate2` -- the same D-006 freshness guard
      (`based_on_fit_assessment_id` vs. `current_fit_assessment_id`) and guarded phase transition.

    Raises `RevisionNotAuthorizedError` if `application` is not currently `READY` -- this workflow
    exists specifically for *revising a completed* application; an application still mid-pipeline
    already has its ordinary Gate 1/Gate 2 controls available with no separate authorization step
    needed.
    """
    if application.pipeline_phase != JobApplication.PipelinePhase.READY:
        raise RevisionNotAuthorizedError(
            f"begin_new_version_from_ready requires pipeline_phase=READY, got "
            f"{application.pipeline_phase!r} -- this application is still mid-pipeline and already "
            "has its ordinary Gate 1/Gate 2 controls available with no separate authorization step."
        )


@dataclass(frozen=True)
class ChainFreshness:
    """Chain-wide freshness (HITL-007): each downstream artifact is compared against its own
    immutable upstream identity reference (D-006), never a timestamp. `fit_assessment_stale` is
    the FitAssessment-vs-JRA link; `draft_stale_direct` is the ResumeDraft-vs-FitAssessment link;
    `draft_stale` also folds in the *transitive* case -- a draft can be perfectly in sync with its
    own FitAssessment, yet that FitAssessment itself has since gone stale relative to a newer JRA
    (e.g. a Gate-1 "re-analyze the posting" feedback action taken after Gate 2 was already
    approved) -- the whole chain must be treated as stale in that case, not just the last pair.
    """

    fit_assessment_stale: bool
    draft_stale_direct: bool
    draft_stale: bool


def compute_freshness(application: JobApplication) -> ChainFreshness:
    jra_id = application.current_jra_id
    fit_assessment = application.current_fit_assessment
    draft = application.current_resume_draft

    fit_assessment_stale = fit_assessment is not None and fit_assessment.based_on_jra_id != jra_id
    draft_stale_direct = (
        draft is not None and draft.based_on_fit_assessment_id != application.current_fit_assessment_id
    )
    return ChainFreshness(
        fit_assessment_stale=fit_assessment_stale,
        draft_stale_direct=draft_stale_direct,
        draft_stale=draft_stale_direct or fit_assessment_stale,
    )


def derive_dashboard_status(application: JobApplication) -> str:
    """The one human-readable status the dashboard shows (requirements.md Sec 17): once the
    operator has recorded an external outcome, that outcome *is* the status -- regenerating an
    artifact afterward never reverts it, because nothing here or in `JobApplication` ever writes
    `application_outcome` except `set_application_outcome` below. While the outcome is still
    `NOT_APPLIED`, the status is the current pipeline phase's own label."""
    if application.application_outcome != JobApplication.ApplicationOutcome.NOT_APPLIED:
        return application.get_application_outcome_display()
    return application.get_pipeline_phase_display()


@dataclass(frozen=True)
class NextAction:
    """Advisory UI guidance only -- the actual guard against an invalid transition always remains
    the canonical service/model methods (`approve_gate1`/`approve_gate2`/`build_fit_assessment`/
    `build_resume_draft`/`resume_builder.services.delivery.get_downloadable_draft`), which are
    re-checked fresh on every request regardless of what this function returns."""

    code: str
    label: str
    url_name: str = ""
    url_kwargs: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        if not self.url_name:
            return ""
        return reverse(self.url_name, kwargs=self.url_kwargs)


def resolve_next_action(application: JobApplication) -> NextAction:
    if application.current_jra_id is None:
        return NextAction(
            code="NO_ANALYSIS",
            label="No analysis on record for this application -- start a new job intake to "
            "create one (an existing application with no analysis has no supported in-place "
            "recovery action).",
        )

    freshness = compute_freshness(application)
    fit_assessment = application.current_fit_assessment

    if fit_assessment is None or freshness.fit_assessment_stale:
        return NextAction(
            code="GATE1",
            label="Run Agent Candidate" if fit_assessment is None else "Resolve staleness at Gate 1",
            url_name="reviews:gate1",
            url_kwargs={"application_id": application.pk},
        )

    if application.pipeline_phase == JobApplication.PipelinePhase.ANALYSIS:
        return NextAction(
            code="GATE1",
            label="Review Gate 1",
            url_name="reviews:gate1",
            url_kwargs={"application_id": application.pk},
        )

    draft = application.current_resume_draft

    if application.pipeline_phase == JobApplication.PipelinePhase.PREPARATION:
        if draft is None or freshness.draft_stale:
            return NextAction(
                code="GATE2",
                label="Run Agent Builder" if draft is None else "Resolve staleness at Gate 2",
                url_name="reviews:gate2",
                url_kwargs={"application_id": application.pk},
            )
        return NextAction(
            code="GATE2",
            label="Review Gate 2",
            url_name="reviews:gate2",
            url_kwargs={"application_id": application.pk},
        )

    if application.pipeline_phase == JobApplication.PipelinePhase.READY:
        if freshness.draft_stale:
            return NextAction(
                code="GATE1",
                label="Upstream changed since approval -- resolve staleness at Gate 1",
                url_name="reviews:gate1",
                url_kwargs={"application_id": application.pk},
            )
        return NextAction(
            code="FINAL",
            label="View final resume",
            url_name="resume_builder:preview",
            url_kwargs={"application_id": application.pk},
        )

    return NextAction(code="NONE", label="No action available")


def review_required(application: JobApplication) -> bool:
    """True exactly when an artifact is sitting fresh and unapproved at a gate, waiting on the
    operator -- not when the next step is to *run* an agent (nothing to review yet) and not once
    a gate is already approved and nothing upstream has changed."""
    freshness = compute_freshness(application)
    fit_assessment = application.current_fit_assessment

    if (
        application.pipeline_phase == JobApplication.PipelinePhase.ANALYSIS
        and fit_assessment is not None
        and not freshness.fit_assessment_stale
    ):
        return True

    draft = application.current_resume_draft
    if (
        application.pipeline_phase == JobApplication.PipelinePhase.PREPARATION
        and draft is not None
        and not freshness.draft_stale
    ):
        return True

    return False


@dataclass(frozen=True)
class DashboardRow:
    application: JobApplication
    employer: str
    role_title: str
    status: str
    jra_exists: bool
    jra_version: int | None
    fit_assessment_exists: bool
    fit_assessment_current: bool
    resume_draft_exists: bool
    resume_draft_current: bool
    resume_draft_confirmed: bool
    gate1_approved: bool
    gate2_approved: bool
    review_required: bool
    is_stale: bool
    next_action: NextAction


def build_dashboard_row(application: JobApplication) -> DashboardRow:
    jra = application.current_jra
    fit_assessment = application.current_fit_assessment
    draft = application.current_resume_draft
    freshness = compute_freshness(application)

    gate1_approved = application.pipeline_phase in (
        JobApplication.PipelinePhase.PREPARATION,
        JobApplication.PipelinePhase.READY,
    )
    gate2_approved = application.pipeline_phase == JobApplication.PipelinePhase.READY

    return DashboardRow(
        application=application,
        employer=jra.employer if jra else "",
        role_title=jra.role_title if jra else "",
        status=derive_dashboard_status(application),
        jra_exists=jra is not None,
        jra_version=jra.version if jra else None,
        fit_assessment_exists=fit_assessment is not None,
        fit_assessment_current=fit_assessment is not None and not freshness.fit_assessment_stale,
        resume_draft_exists=draft is not None,
        resume_draft_current=draft is not None and not freshness.draft_stale,
        resume_draft_confirmed=draft is not None and draft.is_confirmed,
        gate1_approved=gate1_approved,
        gate2_approved=gate2_approved,
        review_required=review_required(application),
        is_stale=freshness.fit_assessment_stale or freshness.draft_stale,
        next_action=resolve_next_action(application),
    )


def list_dashboard_rows() -> list[DashboardRow]:
    applications = (
        JobApplication.objects.select_related(
            "current_jra", "current_fit_assessment", "current_resume_draft"
        )
        .order_by("-updated_at")
    )
    return [build_dashboard_row(application) for application in applications]


@dataclass(frozen=True)
class DashboardSummary:
    """Summary counts by meaningful workflow state (M7 UX follow-up), computed from the same rows
    the table itself renders so the two can never disagree. Buckets are deliberately not mutually
    exclusive (e.g. an application can be both `needs_review` and counted in `total`) -- each stat
    answers its own "how many need this kind of attention" question rather than trying to be a
    single mutually-exclusive partition."""

    total: int
    not_started: int
    needs_review: int
    stale: int
    ready_deliverable: int
    outcome_recorded: int


def compute_dashboard_summary(rows: list[DashboardRow]) -> DashboardSummary:
    return DashboardSummary(
        total=len(rows),
        not_started=sum(1 for row in rows if not row.jra_exists),
        needs_review=sum(1 for row in rows if row.review_required),
        stale=sum(1 for row in rows if row.is_stale),
        ready_deliverable=sum(
            1 for row in rows if row.gate2_approved and not row.is_stale
        ),
        outcome_recorded=sum(
            1
            for row in rows
            if row.application.application_outcome != JobApplication.ApplicationOutcome.NOT_APPLIED
        ),
    )


_ALLOWED_OUTCOMES = frozenset(JobApplication.ApplicationOutcome.values)


def set_application_outcome(application: JobApplication, outcome: str) -> None:
    """The one documented operator action for `application_outcome` (requirements.md Sec 17,
    DASH-003): explicit, independent of `pipeline_phase`, and never touched by any pipeline
    build/gate-approval code path (verified by inspection -- see docs/CURRENT_STATE.md's M7
    section). Restricted to a `JobApplication` that has actually reached `READY` (a final,
    confirmed, non-stale resume draft exists) -- marking an application `APPLIED` before there is
    even a deliverable to have applied with is not a meaningful transition, matching the same
    "never offer/allow an invalid next action" rule the dashboard's own next-action resolution
    follows above.
    """
    if outcome not in _ALLOWED_OUTCOMES:
        raise InvalidOutcomeTransitionError(f"{outcome!r} is not a recognized application outcome.")

    if outcome != JobApplication.ApplicationOutcome.NOT_APPLIED:
        if application.pipeline_phase != JobApplication.PipelinePhase.READY:
            raise InvalidOutcomeTransitionError(
                "Cannot record an application outcome before Gate 2 is approved and a final "
                "resume draft is READY."
            )
        freshness = compute_freshness(application)
        if freshness.draft_stale:
            raise InvalidOutcomeTransitionError(
                "The current resume draft is stale relative to upstream changes -- resolve "
                "staleness before recording an application outcome."
            )

    application.application_outcome = outcome
    application.save(update_fields=["application_outcome", "updated_at"])
