from django.test import TestCase, override_settings

from ..adapters import FakeAdapter, FakeProviderNotAllowedError, OpenAIAdapter, get_adapter_for_stage
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


class FakeProviderOutsideTestsGuardTests(TestCase):
    """Audit hardening (2026-09-03): a real pipeline stage must never silently route through the
    FAKE provider type outside of an automated test run -- see `FakeProviderNotAllowedError`."""

    def test_fake_provider_is_refused_when_testing_flag_is_false(self):
        fake_model = make_model(
            provider=make_provider(name="Fake prod", provider_type=LLMProvider.ProviderType.FAKE),
            model_id="fake-model",
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=fake_model)

        with override_settings(TESTING=False):
            with self.assertRaises(FakeProviderNotAllowedError):
                get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)

    def test_fake_provider_is_allowed_when_testing_flag_is_true(self):
        fake_model = make_model(
            provider=make_provider(name="Fake prod", provider_type=LLMProvider.ProviderType.FAKE),
            model_id="fake-model",
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AB_BUILD, model=fake_model)

        with override_settings(TESTING=True):
            self.assertIsInstance(get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD), FakeAdapter)

    def test_real_provider_is_unaffected_by_the_testing_flag(self):
        openai_model = make_model(
            provider=make_provider(name="OpenAI prod 2", provider_type=LLMProvider.ProviderType.OPENAI),
            model_id="gpt-4o-mini",
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK, model=openai_model)

        with override_settings(TESTING=False):
            self.assertIsInstance(get_adapter_for_stage(StageModelAssignment.Stage.AC_RANK), OpenAIAdapter)
