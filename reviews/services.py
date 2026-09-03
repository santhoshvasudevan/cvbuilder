"""Gate 1 review-action orchestration (HITL-001/002/004/005/006). Every action here is a plain,
synchronous DB-state change -- there is no paused process/agent-graph execution to resume; the next
request simply reads `JobApplication.pipeline_phase`/`current_*` fresh (HITL-004), so approval
survives a process restart trivially (nothing in-memory to lose).
"""

from __future__ import annotations

from candidate_matching.services.fit_assessment import build_fit_assessment
from job_intake.services.intake import rerun_analysis

from .models import ReviewFeedback


class FeedbackTargetError(Exception):
    pass


def run_agent_candidate(job_application):
    """Explicit, operator-initiated Agent Candidate run -- the initial run for this application,
    or a plain re-run with no feedback attached (e.g. after the underlying CandidateMemory or
    CareerEngagement registry changed). Always creates a new FitAssessment version."""
    return build_fit_assessment(job_application)


def submit_gate1_feedback(
    job_application, *, target: str, comments: str, url: str = "", pasted_text: str = ""
) -> ReviewFeedback:
    """Records the feedback, then immediately performs the targeted re-run as one synchronous
    action (this repository has no queue/broker per STACK-003 -- there is no "pending" interval to
    model separately). `target=AJ` reruns Agent Jobber (optionally against new url/pasted_text);
    `target=AC` reruns Agent Candidate against the current JobRequirementAnalysis. Either always
    produces a new, append-only version and repoints the relevant `JobApplication.current_*`
    pointer -- the previous version remains on record for audit (D-010)."""
    if target not in (ReviewFeedback.Target.AJ, ReviewFeedback.Target.AC):
        raise FeedbackTargetError(f"Gate 1 feedback must target AJ or AC, not {target!r}.")

    feedback = ReviewFeedback.objects.create(
        job_application=job_application,
        gate=ReviewFeedback.Gate.GATE_1,
        target=target,
        comments=comments,
    )

    if target == ReviewFeedback.Target.AJ:
        rerun_analysis(job_application, url=url, pasted_text=pasted_text)
    else:
        build_fit_assessment(job_application)

    return feedback


def approve_gate1(job_application) -> None:
    job_application.approve_gate1()
