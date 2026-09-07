"""Deterministic coverage for `llm_provider.services.eligibility` (2026-09-07, per-run model
selection): the one shared service the AJ/AC/AB UI and the execution path both consult, so a
model can never be offered in a selector but rejected at execution time, or vice versa.
"""

from __future__ import annotations

from django.test import TestCase

from ..models import LLMProvider, StageModelAssignment
from ..services.eligibility import eligible_models_for_stage, model_display_label
from .factories import make_model, make_provider

STAGE = StageModelAssignment.Stage.AB_BUILD


class EligibilityFilterTests(TestCase):
    def test_inactive_provider_excluded(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="Inactive OpenAI",
            credential_env_var="OPENAI_API_KEY",
            is_active=False,
        )
        make_model(provider=provider, model_id="gpt-5", supports_structured_output=True, is_active=True)
        self.assertFalse(eligible_models_for_stage(STAGE).filter(provider=provider).exists())

    def test_inactive_model_excluded(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI",
            credential_env_var="OPENAI_API_KEY",
        )
        make_model(provider=provider, model_id="gpt-5", supports_structured_output=True, is_active=False)
        self.assertFalse(eligible_models_for_stage(STAGE).filter(provider=provider).exists())

    def test_incompatible_structured_output_model_excluded(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI, name="OpenAI", credential_env_var="OPENAI_API_KEY"
        )
        make_model(
            provider=provider,
            model_id="legacy-no-structured",
            supports_structured_output=False,
            is_active=True,
        )
        self.assertFalse(eligible_models_for_stage(STAGE).filter(provider=provider).exists())

    def test_missing_credential_reference_excluded(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI",
            credential_env_var="",
            is_active=True,
        )
        make_model(provider=provider, model_id="gpt-5", supports_structured_output=True, is_active=True)
        self.assertFalse(eligible_models_for_stage(STAGE).filter(provider=provider).exists())

    def test_eligible_openrouter_free_included(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        model = make_model(
            provider=provider, model_id="openrouter/free", supports_structured_output=True, is_active=True
        )
        self.assertIn(model, eligible_models_for_stage(STAGE))

    def test_eligible_nvidia_included(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        model = make_model(
            provider=provider, model_id="nvidia/nemotron", supports_structured_output=True, is_active=True
        )
        self.assertIn(model, eligible_models_for_stage(STAGE))

    def test_retired_zai_style_model_excluded_via_is_active(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        model = make_model(
            provider=provider, model_id="z-ai/glm-5.2:free", supports_structured_output=True, is_active=False
        )
        self.assertNotIn(model, eligible_models_for_stage(STAGE))

    def test_eligibility_is_purely_is_active_driven_per_row(self):
        # Eligibility itself keys off LLMModel.is_active, not model_id -- so a second OpenRouter-
        # type provider row with its own still-active copy of the retired model id is (correctly,
        # given only this module's own contract) eligible while that row is active. The actual
        # "no active copy of the retired model anywhere" safety property is enforced by
        # `configure_openrouter_free_router`, which deactivates every row matching the retired
        # model id across every provider -- proven in test_openrouter_free_router_config.py's
        # `test_orphaned_duplicate_zai_row_under_a_different_provider_is_also_deactivated`.
        other_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="dbg",
            credential_env_var="OPENROUTER_API_KEY",
        )
        stray_active_zai = make_model(
            provider=other_provider,
            model_id="z-ai/glm-5.2:free",
            supports_structured_output=True,
            is_active=True,
        )
        self.assertIn(stray_active_zai, eligible_models_for_stage(STAGE))

    def test_fake_provider_always_excluded(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.FAKE, name="Fake")
        model = make_model(provider=provider, model_id="fake-model", supports_structured_output=True)
        self.assertNotIn(model, eligible_models_for_stage(STAGE))

    def test_newly_added_eligible_registry_model_appears_with_zero_code_change(self):
        """Adding a future paid model requires only a registry row -- no template/pipeline change.
        Simulated here by simply creating one and confirming it appears without touching this
        module or any calling code."""
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI",
            credential_env_var="OPENAI_API_KEY",
        )
        model = make_model(
            provider=provider, model_id="gpt-6-paid", supports_structured_output=True, is_active=True
        )
        self.assertIn(model, eligible_models_for_stage(StageModelAssignment.Stage.AC_MATCH))


class DisplayLabelTests(TestCase):
    def test_label_uses_display_name_when_set(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        model = make_model(
            provider=provider,
            model_id="openrouter/free",
            display_name="Free Models Router",
            supports_structured_output=True,
        )
        self.assertEqual(model_display_label(model), "OpenRouter -- Free Models Router (openrouter/free)")

    def test_label_falls_back_to_model_id_when_no_display_name(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        model = make_model(provider=provider, model_id="nvidia/nemotron", supports_structured_output=True)
        self.assertEqual(model_display_label(model), "NVIDIA NIM -- nvidia/nemotron")
