"""Phase J UI/security tests for the M6 (AB_BUILD) review page (`reviews/views_m6.py`,
2026-09-08, D-041)."""

from __future__ import annotations

from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMCallLog
from resume_builder.models import AgentBuilderRun, ResumeDraft
from resume_builder.tests.factories import (
    make_fake_stage_assignment,
    make_ready_for_gate2_application,
    valid_generation_response,
)


def _adapter_patch(response):
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage, *, requested_model_id=None, requested_reasoning_effort=None):
        return FakeAdapter(model, fixed_response=response)

    return mock.patch("resume_builder.services.staged_build.get_adapter_for_stage", _get_adapter_for_stage)


class M6ReviewViewTests(TestCase):
    def setUp(self):
        self.application, self.claim_id, self.engagement_id = make_ready_for_gate2_application()
        self.start_url = reverse("reviews:m6_start", args=[self.application.pk])

    def _start_run(self) -> AgentBuilderRun:
        response = self.client.post(self.start_url)
        self.assertEqual(response.status_code, 302)
        return AgentBuilderRun.objects.get(job_application=self.application)

    def test_start_view_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.start_url).status_code, 405)

    def test_starting_makes_zero_provider_calls(self):
        baseline = LLMCallLog.objects.count()
        self._start_run()
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_get_review_page_never_causes_an_llm_call(self):
        run = self._start_run()
        url = reverse("reviews:m6_review", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_csrf_is_required_for_run_action(self):
        run = self._start_run()
        url = reverse("reviews:m6_review", args=[self.application.pk, run.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(url, {"action": "run", "lock_version": "0"})
        self.assertEqual(response.status_code, 403)

    def test_only_run_action_invokes_the_adapter(self):
        run = self._start_run()
        url = reverse("reviews:m6_review", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        with _adapter_patch(valid_generation_response(self.engagement_id, self.claim_id)):
            response = self.client.post(url, {"action": "run", "lock_version": str(run.lock_version)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LLMCallLog.objects.count(), baseline + 1)

    def test_approve_creates_draft_and_redirects_to_gate2(self):
        run = self._start_run()
        url = reverse("reviews:m6_review", args=[self.application.pk, run.pk])
        with _adapter_patch(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(url, {"action": "run", "lock_version": str(run.lock_version)})
        run.refresh_from_db()
        baseline = ResumeDraft.objects.count()
        response = self.client.post(url, {"action": "approve", "lock_version": str(run.lock_version)})
        self.assertRedirects(response, reverse("reviews:gate2", args=[self.application.pk]))
        self.assertEqual(ResumeDraft.objects.count(), baseline + 1)

    def test_provider_success_alone_does_not_create_a_draft(self):
        run = self._start_run()
        url = reverse("reviews:m6_review", args=[self.application.pk, run.pk])
        baseline = ResumeDraft.objects.count()
        with _adapter_patch(valid_generation_response(self.engagement_id, self.claim_id)):
            self.client.post(url, {"action": "run", "lock_version": str(run.lock_version)})
        self.assertEqual(ResumeDraft.objects.count(), baseline)
