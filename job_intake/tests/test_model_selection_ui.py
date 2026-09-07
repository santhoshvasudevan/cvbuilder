"""Deterministic coverage for the AJ_ANALYZE per-run model-selection UI on the intake form
(2026-09-07): selector rendering from the real registry, default-vs-override precedence recorded
on `LLMCallLog`, and server-side rejection of an ineligible selection. All network calls mocked at
the `requests.post` HTTP boundary."""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from job_applications.models import JobApplication
from llm_provider.models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment

from .factories import DEFAULT_POSTING_TEXT, valid_analysis_response


def _fake_http_response(payload: dict):
    return mock.Mock(status_code=200, headers={}, json=lambda: payload)


class IntakeModelSelectorUiTests(TestCase):
    def setUp(self):
        self.provider = LLMProvider.objects.create(
            name="OpenRouter",
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.default_model = LLMModel.objects.create(
            provider=self.provider,
            model_id="openrouter/free",
            display_name="Free Models Router",
            supports_structured_output=True,
            max_output_tokens=8192,
        )
        StageModelAssignment.objects.create(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=self.default_model
        )

        self.nvidia_provider = LLMProvider.objects.create(
            name="NVIDIA NIM",
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        self.nvidia_model = LLMModel.objects.create(
            provider=self.nvidia_provider,
            model_id="nvidia/nemotron",
            supports_structured_output=True,
            max_output_tokens=32768,
        )
        self.inactive_model = LLMModel.objects.create(
            provider=self.nvidia_provider,
            model_id="nvidia/retired",
            supports_structured_output=True,
            is_active=False,
        )

        self.env_patch = mock.patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "dummy-router-key", "NVIDIA_NIM_API_KEY": "dummy-nvidia-key"},
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.url = reverse("job_intake:intake")

    def test_get_renders_selector_with_default_marked_and_alternative_present(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'for="id_model_aj"')
        self.assertContains(response, 'id="id_model_aj"')
        self.assertContains(response, "OpenRouter -- Free Models Router (openrouter/free) [Default]")
        self.assertContains(response, "NVIDIA NIM -- nvidia/nemotron")

    def test_inactive_model_never_appears(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, "nvidia/retired")

    def test_default_submission_records_default_selection_source(self):
        payload = {
            "choices": [
                {"message": {"content": json.dumps(valid_analysis_response())}, "finish_reason": "stop"}
            ],
            "usage": {},
        }
        with mock.patch("requests.post", return_value=_fake_http_response(payload)):
            response = self.client.post(self.url, {"url": "", "pasted_text": DEFAULT_POSTING_TEXT})
        self.assertEqual(response.status_code, 302)
        log = LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.AJ_ANALYZE).latest("id")
        self.assertEqual(log.model_id, self.default_model.pk)
        self.assertEqual(log.selection_source, LLMCallLog.SelectionSource.DEFAULT)

    def test_explicit_override_routes_to_selected_model_without_changing_the_default(self):
        payload = {
            "choices": [
                {"message": {"content": json.dumps(valid_analysis_response())}, "finish_reason": "stop"}
            ],
            "usage": {},
        }
        with mock.patch("requests.post", return_value=_fake_http_response(payload)) as post_mock:
            response = self.client.post(
                self.url,
                {"url": "", "pasted_text": DEFAULT_POSTING_TEXT, "model_aj": str(self.nvidia_model.pk)},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(post_mock.called)

        log = LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.AJ_ANALYZE).latest("id")
        self.assertEqual(log.model_id, self.nvidia_model.pk)
        self.assertEqual(log.selection_source, LLMCallLog.SelectionSource.OVERRIDE)

        assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AJ_ANALYZE)
        self.assertEqual(assignment.model_id, self.default_model.pk)

    def test_invalid_selection_is_rejected_before_any_provider_call(self):
        # The form's `model_aj` ChoiceField is populated from eligible_models_for_stage() fresh on
        # every request, so an inactive/ineligible model id is rejected by ordinary Django form
        # validation before the view ever calls resolve_stage_model -- still "server-side, before
        # any provider call," just via a different (earlier) layer than the raw-HTML reviews forms.
        applications_before = JobApplication.objects.count()
        with mock.patch("requests.post") as post_mock:
            response = self.client.post(
                self.url,
                {"url": "", "pasted_text": DEFAULT_POSTING_TEXT, "model_aj": str(self.inactive_model.pk)},
            )
        post_mock.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JobApplication.objects.count(), applications_before)
        self.assertContains(response, "not one of the available choices")
