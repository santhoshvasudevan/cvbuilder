from __future__ import annotations

from django.test import Client, TestCase
from django.urls import reverse

from candidate_matching.tests.factories import (
    freeze_revision,
    make_job_application_with_jra,
    make_revision,
    scripted_assessment,
    valid_assessment_response,
)
from candidate_memory.models import CandidateMemory
from job_applications.models import JobApplication


class Gate1ViewTests(TestCase):
    def setUp(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        self.application = make_job_application_with_jra()
        self.url = reverse("reviews:gate1", args=[self.application.pk])

    def test_get_renders_with_no_fit_assessment_yet(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Agent Candidate assessment exists yet.")

    def test_run_ac_action_produces_an_assessment_and_redirects(self):
        with scripted_assessment(valid_assessment_response()):
            response = self.client.post(self.url, {"action": "run_ac"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertIsNotNone(self.application.current_fit_assessment)

    def test_get_after_run_shows_the_disposition(self):
        # The fixture response cites no real evidence (no MemoryClaim/CareerEngagement exists in
        # this test's ACTIVE revision), so the honest, correctly-sanitized outcome is a disclosed
        # UNKNOWN downgrade, not a bare MATCH -- see disposition_coverage.sanitize_items.
        with scripted_assessment(valid_assessment_response()):
            self.client.post(self.url, {"action": "run_ac"})
        response = self.client.get(self.url)
        self.assertContains(response, "UNKNOWN")
        self.assertContains(response, "downgraded to UNKNOWN")

    def test_approve_action_advances_phase(self):
        with scripted_assessment(valid_assessment_response()):
            self.client.post(self.url, {"action": "run_ac"})
        response = self.client.post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

    def test_approve_without_assessment_shows_error_not_a_crash(self):
        response = self.client.post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "run Agent Candidate first")

    def test_ac_feedback_action_records_feedback_and_reruns(self):
        with scripted_assessment(valid_assessment_response()):
            self.client.post(self.url, {"action": "run_ac"})
            response = self.client.post(
                self.url, {"action": "feedback", "target": "AC", "comments": "please redo"}
            )
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.current_fit_assessment.version, 2)

    def test_unknown_action_shows_error(self):
        response = self.client.post(self.url, {"action": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unknown action")


class CsrfProtectionTests(TestCase):
    def test_post_without_csrf_token_is_rejected(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra()
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("reviews:gate1", args=[application.pk]), {"action": "run_ac"}
        )
        self.assertEqual(response.status_code, 403)
