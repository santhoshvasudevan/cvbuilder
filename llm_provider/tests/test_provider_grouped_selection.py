"""Provider-aware model selection in the M5/M6 UI (2026-09-07, D-039 correction): proves the
direct-OpenAI and OpenRouter-hosted GPT-5.4 records are shown as distinct, clearly-labeled groups,
and that a model/provider mismatch is structurally impossible -- there is no separate "provider"
input a submission could disagree with the selected model on; the one submitted value is always an
`LLMModel` primary key, which already carries its own provider unambiguously.
"""

from __future__ import annotations

from django.test import TestCase

from ..models import LLMProvider, StageModelAssignment
from ..services.eligibility import grouped_model_choices, provider_group_label
from ..services.gpt54_defaults import (
    GPT54_MODEL_ID,
    OPENROUTER_GPT54_MODEL_ID,
    configure_gpt54_defaults,
)
from ..services.model_selection import ModelNotEligibleForStageError, resolve_stage_model
from .factories import make_model, make_provider

STAGE = StageModelAssignment.Stage.AC_MATCH


class ProviderGroupLabelTests(TestCase):
    def test_direct_openai_provider_is_labeled_direct_api(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.OPENAI, name="OpenAI")
        self.assertEqual(provider_group_label(provider), "OpenAI — Direct API")

    def test_openrouter_provider_is_labeled_plainly(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.OPENROUTER, name="OpenRouter")
        self.assertEqual(provider_group_label(provider), "OpenRouter")

    def test_nvidia_provider_is_labeled_plainly(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.NVIDIA_NIM, name="NVIDIA NIM")
        self.assertEqual(provider_group_label(provider), "NVIDIA NIM")


class GroupedModelChoicesTests(TestCase):
    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_direct_and_openrouter_gpt54_records_are_in_separate_groups(self):
        groups = dict(grouped_model_choices(StageModelAssignment.Stage.AC_MATCH))
        self.assertIn("OpenAI — Direct API", groups)
        self.assertIn("OpenRouter", groups)

        direct_ids = {label for _pk, label in groups["OpenAI — Direct API"]}
        openrouter_ids = {label for _pk, label in groups["OpenRouter"]}
        self.assertTrue(any("gpt-5.4" in label and "openai/" not in label for label in direct_ids))
        self.assertTrue(any("openai/gpt-5.4" in label for label in openrouter_ids))

    def test_each_option_value_is_an_unambiguous_llmmodel_primary_key(self):
        """The grouped structure is presentation-only -- every option's value is still the exact
        same `LLMModel.pk` `resolve_stage_model` expects, never a separate provider+model_id pair
        that could drift apart."""
        from ..models import LLMModel

        groups = grouped_model_choices(StageModelAssignment.Stage.AC_MATCH)
        all_pks = {pk for _label, options in groups for pk, _label in options}
        for pk in all_pks:
            self.assertTrue(LLMModel.objects.filter(pk=pk).exists())


class ModelProviderMismatchIsStructurallyImpossibleTests(TestCase):
    """There is no way to submit 'direct OpenAI provider' with 'openai/gpt-5.4' (an OpenRouter
    id), or vice versa -- the UI has exactly one selection field (the LLMModel primary key), so
    the only reachable "mismatch" is selecting a model id that does not exist, or that belongs to
    an inactive/ineligible provider -- both already rejected server-side before any provider call.
    """

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_selecting_the_direct_model_resolves_to_the_direct_openai_provider(self):
        from ..models import LLMModel

        direct_model = LLMModel.objects.get(
            model_id=GPT54_MODEL_ID, provider__provider_type=LLMProvider.ProviderType.OPENAI
        )
        selection = resolve_stage_model(STAGE, requested_model_id=direct_model.pk)
        self.assertEqual(selection.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)
        self.assertEqual(selection.model.model_id, GPT54_MODEL_ID)

    def test_selecting_the_openrouter_model_resolves_to_the_openrouter_provider(self):
        from ..models import LLMModel

        openrouter_model = LLMModel.objects.get(
            model_id=OPENROUTER_GPT54_MODEL_ID, provider__provider_type=LLMProvider.ProviderType.OPENROUTER
        )
        selection = resolve_stage_model(STAGE, requested_model_id=openrouter_model.pk)
        self.assertEqual(selection.model.provider.provider_type, LLMProvider.ProviderType.OPENROUTER)
        self.assertEqual(selection.model.model_id, OPENROUTER_GPT54_MODEL_ID)

    def test_a_model_belonging_to_an_inactive_provider_is_rejected_before_any_call(self):
        inactive_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="Retired OpenAI project",
            credential_env_var="RETIRED_KEY",
            is_active=False,
        )
        inactive_model = make_model(
            provider=inactive_provider,
            model_id="gpt-5.4-retired",
            supports_structured_output=True,
        )
        with self.assertRaises(ModelNotEligibleForStageError):
            resolve_stage_model(STAGE, requested_model_id=inactive_model.pk)

    def test_a_nonexistent_model_id_is_rejected_before_any_call(self):
        with self.assertRaises(ModelNotEligibleForStageError):
            resolve_stage_model(STAGE, requested_model_id=999_999)
