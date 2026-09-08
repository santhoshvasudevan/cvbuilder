from __future__ import annotations

from django.test import Client, TestCase
from django.urls import reverse

from job_applications.models import JobApplication
from resume_builder.tests.factories import (
    make_ready_for_gate2_application,
    scripted_generation,
    valid_generation_response,
)


class Gate2ViewTests(TestCase):
    def setUp(self):
        self.application, self.claim_id, self.engagement_id = make_ready_for_gate2_application()
        self.url = reverse("reviews:gate2", args=[self.application.pk])

    def test_get_renders_with_no_draft_yet(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Agent Builder draft exists yet")
        # 2026-09-08, D-041: the single-shot "Run Agent Builder" trigger is replaced by the M6
        # review workflow's own entry point -- a successful provider call no longer automatically
        # creates a ResumeDraft from this page.
        self.assertContains(response, "Start M6 review")
        self.assertNotContains(response, ">Run Agent Builder<")

    def test_run_ab_action_produces_a_draft_and_redirects(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            response = self.client.post(self.url, {"action": "run_ab"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertIsNotNone(self.application.current_resume_draft)

    def test_get_after_run_shows_rendered_markdown_and_evidence(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
        response = self.client.get(self.url)
        self.assertContains(response, "Senior Backend Engineer")
        self.assertContains(response, self.claim_id)

    def test_approve_action_advances_to_ready(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
        response = self.client.post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_approve_without_draft_shows_error(self):
        response = self.client.post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "run Agent Builder first")

    def test_get_after_run_links_claim_id_to_its_candidate_memory_detail_page(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
        response = self.client.get(self.url)
        self.assertContains(response, "/candidate-memory/revisions/")
        self.assertContains(response, self.claim_id)

    def test_double_approve_is_idempotent_not_an_error(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
        self.client.post(self.url, {"action": "approve"})
        response = self.client.post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_feedback_action_records_and_reruns(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
            response = self.client.post(self.url, {"action": "feedback", "comments": "Tighten the summary."})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.current_resume_draft.version, 2)

    def test_feedback_with_blank_comments_shows_error_and_does_not_rerun(self):
        with scripted_generation(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(self.url, {"action": "run_ab"})
            response = self.client.post(self.url, {"action": "feedback", "comments": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Feedback comments are required")
        self.application.refresh_from_db()
        self.assertEqual(self.application.current_resume_draft.version, 1)


class Gate2CsrfProtectionTests(TestCase):
    def test_post_without_csrf_token_is_rejected(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("reviews:gate2", args=[application.pk]), {"action": "run_ab"}
        )
        self.assertEqual(response.status_code, 403)
