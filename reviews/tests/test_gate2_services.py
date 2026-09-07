from __future__ import annotations

from django.test import TestCase

from job_applications.models import GateNotReadyError, JobApplication, StaleAssessmentError
from resume_builder.tests.factories import (
    make_ready_for_gate2_application,
    scripted_generation,
    valid_generation_response,
)

from ..models import ReviewFeedback
from ..services import FeedbackTargetError, approve_gate2, run_agent_builder, submit_gate2_feedback


class RunAgentBuilderTests(TestCase):
    def test_runs_and_sets_current_resume_draft(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            draft = run_agent_builder(application)
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft_id, draft.pk)


class SubmitGate2FeedbackTests(TestCase):
    def test_records_feedback_and_creates_a_new_draft_version(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)
            submit_gate2_feedback(application, comments="Tighten the summary.")

        feedback = ReviewFeedback.objects.get(job_application=application)
        self.assertEqual(feedback.gate, ReviewFeedback.Gate.GATE_2)
        self.assertEqual(feedback.target, ReviewFeedback.Target.AB)
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft.version, 2)

    def test_blank_comments_are_rejected_before_any_rerun(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)
            with self.assertRaises(FeedbackTargetError):
                submit_gate2_feedback(application, comments="")
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft.version, 1)

    def test_whitespace_only_comments_are_also_rejected(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)
            with self.assertRaises(FeedbackTargetError):
                submit_gate2_feedback(application, comments="   ")


class ApproveGate2Tests(TestCase):
    def test_cannot_approve_without_a_draft(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        with self.assertRaises(GateNotReadyError):
            approve_gate2(application)

    def test_approving_advances_to_ready(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)
        approve_gate2(application)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_cannot_approve_a_stale_draft(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)

        from candidate_matching.models import FitAssessment

        new_fit_assessment = FitAssessment.objects.create(
            job_application=application, version=2, based_on_jra=application.current_jra
        )
        application.record_fit_assessment(new_fit_assessment)

        with self.assertRaises(StaleAssessmentError):
            approve_gate2(application)
