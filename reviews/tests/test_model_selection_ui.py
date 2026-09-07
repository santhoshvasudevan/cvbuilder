"""Deterministic coverage for the AJ/AC/AB per-run model-selection UI and its execution wiring
(2026-09-07): Gate 1's four independently-configurable selectors (AJ_ANALYZE/AC_NORMALIZE/
AC_RANK/AC_MATCH) and Gate 2's one (AB_BUILD), rendered from the real registry via
`llm_provider.services.eligibility`, plus an end-to-end proof that an explicit override actually
routes the call and is recorded on `LLMCallLog` -- all network calls mocked at the
`requests.post` HTTP boundary, matching this project's standing testing convention.
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from llm_provider.models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment
from resume_builder.models import ResumeDraft
from resume_builder.tests.factories import make_ready_for_gate2_application, valid_generation_response


def _fake_http_response(payload: dict):
    return mock.Mock(status_code=200, headers={}, json=lambda: payload)


class Gate2ModelSelectorUiTests(TestCase):
    def setUp(self):
        self.application, self.claim_id, self.engagement_id = make_ready_for_gate2_application()
        self.url = reverse("reviews:gate2", args=[self.application.pk])

        self.openrouter_provider = LLMProvider.objects.create(
            name="OpenRouter",
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.default_model = LLMModel.objects.create(
            provider=self.openrouter_provider,
            model_id="openrouter/free",
            display_name="Free Models Router",
            supports_structured_output=True,
            max_output_tokens=8192,
        )
        StageModelAssignment.objects.update_or_create(
            stage=StageModelAssignment.Stage.AB_BUILD, defaults={"model": self.default_model}
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

    def test_selector_shows_default_and_alternative_with_accessible_label(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'for="id_model_ab"')
        self.assertContains(response, 'id="id_model_ab"')
        self.assertContains(response, "OpenRouter -- Free Models Router (openrouter/free) [Default]")
        self.assertContains(response, "NVIDIA NIM -- nvidia/nemotron")

    def test_system_default_option_is_initially_selected(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn('<option value="" selected>System default</option>', html)

    def test_inactive_model_does_not_appear(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, "nvidia/retired")

    def test_default_run_records_default_selection_source(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            valid_generation_response(self.engagement_id, self.claim_id)
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }
        with mock.patch("requests.post", return_value=_fake_http_response(payload)):
            response = self.client.post(self.url, {"action": "run_ab"})
        self.assertEqual(response.status_code, 302)
        log = LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.AB_BUILD).latest("id")
        self.assertEqual(log.model_id, self.default_model.pk)
        self.assertEqual(log.selection_source, LLMCallLog.SelectionSource.DEFAULT)

    def test_explicit_override_routes_to_the_selected_model_and_leaves_default_unchanged(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            valid_generation_response(self.engagement_id, self.claim_id)
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }
        with mock.patch("requests.post", return_value=_fake_http_response(payload)) as post_mock:
            response = self.client.post(
                self.url, {"action": "run_ab", "model_ab": str(self.nvidia_model.pk)}
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(post_mock.called)

        log = LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.AB_BUILD).latest("id")
        self.assertEqual(log.model_id, self.nvidia_model.pk)
        self.assertEqual(log.selection_source, LLMCallLog.SelectionSource.OVERRIDE)

        # The global default assignment is untouched by the per-run override.
        assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AB_BUILD)
        self.assertEqual(assignment.model_id, self.default_model.pk)

    def test_invalid_selection_is_rejected_server_side_before_any_provider_call(self):
        draft_count_before = ResumeDraft.objects.count()
        with mock.patch("requests.post") as post_mock:
            response = self.client.post(self.url, {"action": "run_ab", "model_ab": "999999"})
        post_mock.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ResumeDraft.objects.count(), draft_count_before)
        self.assertContains(response, "not eligible")

    def test_submitted_valid_selection_survives_a_downstream_validation_error(self):
        # The selection itself is valid/eligible, but the underlying build fails for an unrelated
        # reason (here: a schema-invalid provider response) -- the re-rendered form must still
        # show the operator's chosen model as selected, not silently reset to the default.
        with mock.patch("requests.post", return_value=_fake_http_response({"choices": [], "usage": {}})):
            response = self.client.post(
                self.url, {"action": "run_ab", "model_ab": str(self.nvidia_model.pk)}
            )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(f'<option value="{self.nvidia_model.pk}" selected>', html)


class Gate1ModelSelectorUiTests(TestCase):
    def setUp(self):
        from candidate_matching.tests.factories import make_job_application_with_jra

        self.application = make_job_application_with_jra()
        self.url = reverse("reviews:gate1", args=[self.application.pk])

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
        for stage in (
            StageModelAssignment.Stage.AJ_ANALYZE,
            StageModelAssignment.Stage.AC_NORMALIZE,
            StageModelAssignment.Stage.AC_RANK,
            StageModelAssignment.Stage.AC_MATCH,
        ):
            StageModelAssignment.objects.update_or_create(stage=stage, defaults={"model": self.default_model})

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

    def test_all_four_gate1_selectors_present(self):
        response = self.client.get(self.url)
        for field_id in ("id_model_ac_normalize", "id_model_ac_rank", "id_model_ac_match", "id_model_aj"):
            self.assertContains(response, f'id="{field_id}"')

    def test_nvidia_alternative_appears_for_each_ac_stage(self):
        response = self.client.get(self.url)
        # nvidia/nemotron is eligible for all three AC stages plus AJ_ANALYZE -- it should appear
        # at least four times across the four selectors.
        self.assertGreaterEqual(response.content.decode().count("nvidia/nemotron"), 4)
