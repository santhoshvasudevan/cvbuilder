from django.test import TestCase

from ..adapters import FakeAdapter, OpenAIAdapter, get_adapter_for_stage
from ..models import LLMProvider, StageModelAssignment
from .factories import make_model, make_provider, make_stage_assignment


class StageRoutingTests(TestCase):
    def test_routes_to_the_adapter_class_matching_the_assigned_model_s_provider(self):
        openai_provider = make_provider(name="OpenAI prod", provider_type=LLMProvider.ProviderType.OPENAI)
        openai_model = make_model(provider=openai_provider, model_id="gpt-4o-mini")
        make_stage_assignment(stage=StageModelAssignment.Stage.AJ_ANALYZE, model=openai_model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)

        self.assertIsInstance(adapter, OpenAIAdapter)
        self.assertEqual(adapter.llm_model, openai_model)

    def test_reassigning_the_stage_changes_routing_with_zero_code_change(self):
        openai_model = make_model(
            provider=make_provider(name="OpenAI prod", provider_type=LLMProvider.ProviderType.OPENAI),
            model_id="gpt-4o-mini",
        )
        fake_model = make_model(
            provider=make_provider(name="Fake prod", provider_type=LLMProvider.ProviderType.FAKE),
            model_id="fake-model",
        )
        assignment = make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=openai_model)
        self.assertIsInstance(get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH), OpenAIAdapter)

        # The only action taken here is a data change (re-pointing the FK) -- no import, no
        # branch, no new code path is touched to make routing follow it.
        assignment.model = fake_model
        assignment.save()

        self.assertIsInstance(get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH), FakeAdapter)

    def test_unassigned_stage_raises_lookup_error(self):
        with self.assertRaises(StageModelAssignment.DoesNotExist):
            get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)
