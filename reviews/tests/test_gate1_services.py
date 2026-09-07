from __future__ import annotations

from django.test import TestCase

from candidate_matching.tests.factories import (
    freeze_revision,
    make_job_application_with_jra,
    make_revision,
    scripted_agent_candidate,
    valid_assessment_response,
)
from candidate_memory.models import CandidateMemory
from job_applications.models import GateNotReadyError, JobApplication, StaleAssessmentError
from job_intake.tests.factories import scripted_analysis, valid_analysis_response

from ..models import ReviewFeedback
from ..services import FeedbackTargetError, approve_gate1, run_agent_candidate, submit_gate1_feedback


class RunAgentCandidateTests(TestCase):
    def test_runs_and_sets_current_fit_assessment(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra()
        with scripted_agent_candidate(valid_assessment_response()):
            fit_assessment = run_agent_candidate(application)
        application.refresh_from_db()
        self.assertEqual(application.current_fit_assessment_id, fit_assessment.pk)


class SubmitGate1FeedbackTests(TestCase):
    def setUp(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        self.application = make_job_application_with_jra()

    def test_rejects_a_target_outside_aj_or_ac(self):
        with self.assertRaises(FeedbackTargetError):
            submit_gate1_feedback(self.application, target="AB", comments="wrong gate")

    def test_blank_comments_are_rejected_before_any_rerun(self):
        """CLAUDE.md: rejecting a stage's output must require a review note -- proven here by
        checking no new FitAssessment version is created when the required comment is missing."""
        with self.assertRaises(FeedbackTargetError):
            submit_gate1_feedback(self.application, target=ReviewFeedback.Target.AC, comments="")
        self.assertEqual(self.application.fit_assessments.count(), 0)

    def test_whitespace_only_comments_are_also_rejected(self):
        with self.assertRaises(FeedbackTargetError):
            submit_gate1_feedback(self.application, target=ReviewFeedback.Target.AC, comments="   ")

    def test_ac_feedback_records_row_and_creates_new_fit_assessment_version(self):
        with scripted_agent_candidate(valid_assessment_response()):
            submit_gate1_feedback(self.application, target=ReviewFeedback.Target.AC, comments="please redo")

        feedback = ReviewFeedback.objects.get(job_application=self.application)
        self.assertEqual(feedback.gate, ReviewFeedback.Gate.GATE_1)
        self.assertEqual(feedback.target, ReviewFeedback.Target.AC)
        self.application.refresh_from_db()
        self.assertEqual(self.application.current_fit_assessment.version, 1)

    def test_aj_feedback_creates_new_jra_version_and_repoints_pointer(self):
        original_jra_id = self.application.current_jra_id
        # A single requirement with provenance matching `make_job_application_with_jra`'s own
        # original_input text -- this test only checks version/role_title routing, not extraction
        # quality, but (D-023) zero requirements is always rejected regardless of posting length.
        with scripted_analysis(
            valid_analysis_response(
                role_title="Updated Title",
                requirements=[
                    {
                        "category": "RESPONSIBILITY",
                        "text": "Do the role",
                        "source_context": "A pasted job posting about a role.",
                    }
                ],
                screening_risks=[],
            )
        ):
            submit_gate1_feedback(
                self.application, target=ReviewFeedback.Target.AJ, comments="wrong role extracted"
            )
        self.application.refresh_from_db()
        self.assertNotEqual(self.application.current_jra_id, original_jra_id)
        self.assertEqual(self.application.current_jra.version, 2)
        self.assertEqual(self.application.current_jra.role_title, "Updated Title")


class ApproveGate1Tests(TestCase):
    def setUp(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        self.application = make_job_application_with_jra()

    def test_cannot_approve_without_a_fit_assessment(self):
        with self.assertRaises(GateNotReadyError):
            approve_gate1(self.application)

    def test_cannot_approve_a_stale_fit_assessment(self):
        with scripted_agent_candidate(valid_assessment_response()):
            run_agent_candidate(self.application)
        # A single requirement with matching provenance -- see the AJ-feedback test above for why.
        with scripted_analysis(
            valid_analysis_response(
                requirements=[
                    {
                        "category": "RESPONSIBILITY",
                        "text": "Do the role",
                        "source_context": "A pasted job posting about a role.",
                    }
                ],
                screening_risks=[],
            )
        ):
            from job_intake.services.intake import rerun_analysis

            rerun_analysis(self.application)
        self.application.refresh_from_db()
        with self.assertRaises(StaleAssessmentError):
            approve_gate1(self.application)

    def test_approving_advances_to_preparation(self):
        with scripted_agent_candidate(valid_assessment_response()):
            run_agent_candidate(self.application)
        approve_gate1(self.application)
        self.application.refresh_from_db()
        self.assertEqual(self.application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

    def test_approval_survives_a_fresh_query_not_an_in_memory_assumption(self):
        with scripted_agent_candidate(valid_assessment_response()):
            run_agent_candidate(self.application)
        approve_gate1(self.application)

        reloaded = JobApplication.objects.get(pk=self.application.pk)
        self.assertEqual(reloaded.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)
