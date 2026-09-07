"""Gate 1 review-action orchestration (HITL-001/002/004/005/006). Every action here is a plain,
synchronous DB-state change -- there is no paused process/agent-graph execution to resume; the next
request simply reads `JobApplication.pipeline_phase`/`current_*` fresh (HITL-004), so approval
survives a process restart trivially (nothing in-memory to lose).
"""

from __future__ import annotations

from candidate_matching.services.fit_assessment import build_fit_assessment
from job_intake.services.intake import rerun_analysis
from resume_builder.services.build import build_resume_draft

from .models import ReviewFeedback


class FeedbackTargetError(Exception):
    pass


def run_agent_candidate(
    job_application,
    *,
    requested_models: dict[str, int] | None = None,
    requested_reasoning_efforts: dict[str, str] | None = None,
):
    """Explicit, operator-initiated Agent Candidate run -- the initial run for this application,
    or a plain re-run with no feedback attached (e.g. after the underlying CandidateMemory or
    CareerEngagement registry changed). Always creates a new FitAssessment version.

    `requested_models`/`requested_reasoning_efforts`, when given, are per-run operator override
    maps keyed by `StageModelAssignment.Stage` value (2026-09-07, per-run model selection; paid
    GPT-5.4 model defaults), covering AC_NORMALIZE/AC_RANK/AC_MATCH -- Agent Candidate's three
    independently-configurable LLM calls. Never persisted as a new stage default."""
    return build_fit_assessment(
        job_application,
        requested_models=requested_models,
        requested_reasoning_efforts=requested_reasoning_efforts,
    )


def submit_gate1_feedback(
    job_application,
    *,
    target: str,
    comments: str,
    url: str = "",
    pasted_text: str = "",
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
    requested_models: dict[str, int] | None = None,
    requested_reasoning_efforts: dict[str, str] | None = None,
) -> ReviewFeedback:
    """Records the feedback, then immediately performs the targeted re-run as one synchronous
    action (this repository has no queue/broker per STACK-003 -- there is no "pending" interval to
    model separately). `target=AJ` reruns Agent Jobber (optionally against new url/pasted_text);
    `target=AC` reruns Agent Candidate against the current JobRequirementAnalysis. Either always
    produces a new, append-only version and repoints the relevant `JobApplication.current_*`
    pointer -- the previous version remains on record for audit (D-010).

    A review note (`comments`) is required -- rejecting/requesting rework of a stage's output
    without stating why is never allowed, matching CLAUDE.md's "rejecting a stage must require a
    review note" invariant. Blank/whitespace-only comments raise `FeedbackTargetError` before any
    re-run is attempted."""
    if target not in (ReviewFeedback.Target.AJ, ReviewFeedback.Target.AC):
        raise FeedbackTargetError(f"Gate 1 feedback must target AJ or AC, not {target!r}.")
    if not comments.strip():
        raise FeedbackTargetError("Feedback comments are required -- state why this stage is being re-run.")

    feedback = ReviewFeedback.objects.create(
        job_application=job_application,
        gate=ReviewFeedback.Gate.GATE_1,
        target=target,
        comments=comments,
    )

    if target == ReviewFeedback.Target.AJ:
        rerun_analysis(
            job_application,
            url=url,
            pasted_text=pasted_text,
            requested_model_id=requested_model_id,
            requested_reasoning_effort=requested_reasoning_effort,
        )
    else:
        build_fit_assessment(
            job_application,
            requested_models=requested_models,
            requested_reasoning_efforts=requested_reasoning_efforts,
        )

    return feedback


def approve_gate1(job_application) -> None:
    job_application.approve_gate1()


def run_agent_builder(
    job_application,
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
):
    """Explicit, operator-initiated Agent Builder run -- the initial run for this application, or
    a plain re-run with no feedback attached. Always creates a new ResumeDraft version.

    `requested_model_id`/`requested_reasoning_effort`, when given, are per-run operator overrides
    for AB_BUILD (2026-09-07, per-run model selection; paid GPT-5.4 model defaults) -- never
    persisted as a new stage default."""
    return build_resume_draft(
        job_application,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )


def submit_gate2_feedback(
    job_application,
    *,
    comments: str,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
) -> ReviewFeedback:
    """Gate 2 feedback always targets Agent Builder (AB) -- Gate 2 review is about resume content,
    not the underlying fit assessment; feedback about a wrong disposition or missing evidence
    belongs at Gate 1 instead. Records the feedback, then immediately reruns Agent Builder as one
    synchronous action, producing a new, append-only ResumeDraft version (D-010).

    A review note (`comments`) is required -- see `submit_gate1_feedback`'s docstring for the same
    rationale; blank/whitespace-only comments raise `FeedbackTargetError` before any re-run."""
    if not comments.strip():
        raise FeedbackTargetError("Feedback comments are required -- state why this draft is being re-run.")
    feedback = ReviewFeedback.objects.create(
        job_application=job_application,
        gate=ReviewFeedback.Gate.GATE_2,
        target=ReviewFeedback.Target.AB,
        comments=comments,
    )
    build_resume_draft(
        job_application,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    return feedback


def approve_gate2(job_application) -> None:
    job_application.approve_gate2()
