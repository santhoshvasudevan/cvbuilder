from django.test import TestCase

from job_applications.models import StageIdentifier
from llm_provider.adapters import ADAPTER_CLASSES, get_adapter_for_model, get_adapter_for_stage
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.adapters.gemini import GeminiAdapter
from llm_provider.adapters.nvidia import NvidiaNimAdapter
from llm_provider.adapters.openai import OpenAIAdapter
from llm_provider.adapters.openrouter import OpenRouterAdapter
from llm_provider.errors import InactiveProviderError
from llm_provider.models import LLMProvider, StageModelAssignment

from .factories import make_fake_provider, make_model, make_provider, make_stage_assignment


class AdapterRegistryTests(TestCase):
    def test_every_adapter_type_has_a_registered_class(self):
        for adapter_type, _label in LLMProvider.AdapterType.choices:
            self.assertIn(adapter_type, ADAPTER_CLASSES)

    def test_registered_classes_match_expected_adapters(self):
        self.assertIs(ADAPTER_CLASSES[LLMProvider.AdapterType.OPENAI], OpenAIAdapter)
        self.assertIs(ADAPTER_CLASSES[LLMProvider.AdapterType.NVIDIA_NIM], NvidiaNimAdapter)
        self.assertIs(ADAPTER_CLASSES[LLMProvider.AdapterType.GEMINI], GeminiAdapter)
        self.assertIs(ADAPTER_CLASSES[LLMProvider.AdapterType.OPENROUTER], OpenRouterAdapter)
        self.assertIs(ADAPTER_CLASSES[LLMProvider.AdapterType.FAKE], FakeAdapter)


class GetAdapterForStageTests(TestCase):
    def setUp(self):
        self.provider = make_fake_provider()
        self.model = make_model(self.provider, supports_structured_output=True)

    def test_returns_adapter_for_assigned_model(self):
        make_stage_assignment(self.model, stage=StageIdentifier.AJ_ANALYZE)
        adapter = get_adapter_for_stage(StageIdentifier.AJ_ANALYZE)
        self.assertIsInstance(adapter, FakeAdapter)
        self.assertEqual(adapter.llm_model.id, self.model.id)

    def test_raises_does_not_exist_when_no_assignment(self):
        with self.assertRaises(StageModelAssignment.DoesNotExist):
            get_adapter_for_stage(StageIdentifier.AC_ASSESS)

    def test_raises_configuration_error_before_returning_adapter_when_provider_inactive(self):
        make_stage_assignment(self.model, stage=StageIdentifier.AJ_ANALYZE)
        self.provider.enabled = False
        self.provider.save()
        with self.assertRaises(InactiveProviderError):
            get_adapter_for_stage(StageIdentifier.AJ_ANALYZE)

    def test_never_falls_back_to_a_different_stage_or_model(self):
        # Assign AJ_ANALYZE to one model, AC_ASSESS to a different one; routing for AJ_ANALYZE
        # must never return AC_ASSESS's model or vice versa.
        other_provider = make_fake_provider(name="Other Fake")
        other_model = make_model(other_provider, model_identifier="other-fake-model")
        make_stage_assignment(self.model, stage=StageIdentifier.AJ_ANALYZE)
        make_stage_assignment(other_model, stage=StageIdentifier.AC_ASSESS)

        aj_adapter = get_adapter_for_stage(StageIdentifier.AJ_ANALYZE)
        ac_adapter = get_adapter_for_stage(StageIdentifier.AC_ASSESS)
        self.assertEqual(aj_adapter.llm_model.id, self.model.id)
        self.assertEqual(ac_adapter.llm_model.id, other_model.id)
        self.assertNotEqual(aj_adapter.llm_model.id, ac_adapter.llm_model.id)


class GetAdapterForModelTests(TestCase):
    def test_builds_adapter_without_requiring_a_stage_assignment(self):
        provider = make_fake_provider()
        model = make_model(provider)
        adapter = get_adapter_for_model(model)
        self.assertIsInstance(adapter, FakeAdapter)
        self.assertEqual(adapter.llm_model.id, model.id)

    def test_real_provider_type_resolves_to_matching_adapter_class(self):
        provider = make_provider(adapter_type=LLMProvider.AdapterType.GEMINI)
        model = make_model(provider)
        adapter = get_adapter_for_model(model)
        self.assertIsInstance(adapter, GeminiAdapter)
