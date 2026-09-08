"""Manual operator call console tests (docs/IMPLEMENTATION_PLAN.md M2: "operator can run a
stage manually"; LLM-010/011 model comparison). Uses FakeAdapter exclusively -- no network.
"""

from django.test import TestCase
from pydantic import BaseModel

from job_applications.models import StageIdentifier
from llm_provider.models import StageModelAssignment
from llm_provider.services.console import (
    compare_models,
    run_stage_manually,
    run_with_model_override,
)
from llm_provider.types import NormalizedLLMRequest

from .factories import make_fake_provider, make_model, make_stage_assignment


class Answer(BaseModel):
    # Defaulted so FakeAdapter's default empty fixed_response ({}) validates -- these tests
    # exercise console-layer routing/orchestration, not schema validation (see test_fake_adapter
    # and test_schema_translation for that).
    text: str = "ok"


def _request():
    return NormalizedLLMRequest(
        stage=StageIdentifier.AJ_ANALYZE,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=Answer,
    )


class RunStageManuallyTests(TestCase):
    def setUp(self):
        self.provider = make_fake_provider()
        self.model = make_model(self.provider, supports_structured_output=True)
        make_stage_assignment(self.model, stage=StageIdentifier.AJ_ANALYZE)

    def test_runs_the_currently_assigned_model(self):
        result = run_stage_manually(StageIdentifier.AJ_ANALYZE, _request())
        self.assertFalse(result.is_error)

    def test_raises_when_no_assignment_exists_for_stage(self):
        with self.assertRaises(StageModelAssignment.DoesNotExist):
            run_stage_manually(StageIdentifier.AC_ASSESS, _request())


class RunWithModelOverrideTests(TestCase):
    def test_overrides_without_touching_stage_assignment(self):
        provider = make_fake_provider()
        assigned_model = make_model(provider, model_identifier="assigned", supports_structured_output=True)
        override_model = make_model(provider, model_identifier="override", supports_structured_output=True)
        assignment = make_stage_assignment(assigned_model, stage=StageIdentifier.AJ_ANALYZE)

        run_with_model_override(override_model, _request())

        assignment.refresh_from_db()
        self.assertEqual(assignment.model_id, assigned_model.id)  # unchanged


class CompareModelsTests(TestCase):
    def setUp(self):
        self.provider = make_fake_provider()
        self.model_a = make_model(self.provider, model_identifier="a", supports_structured_output=True)
        self.model_b = make_model(self.provider, model_identifier="b", supports_structured_output=True)

    def test_runs_each_model_independently_and_returns_both_results(self):
        results = compare_models([(self.model_a, _request()), (self.model_b, _request())])
        self.assertEqual(set(results.keys()), {self.model_a.id, self.model_b.id})
        self.assertFalse(results[self.model_a.id].is_error)
        self.assertFalse(results[self.model_b.id].is_error)

    def test_one_models_failure_does_not_prevent_or_alter_the_others_result(self):
        self.model_a.enabled = False
        self.model_a.save()
        results = compare_models([(self.model_a, _request()), (self.model_b, _request())])
        self.assertTrue(results[self.model_a.id].is_error)
        self.assertFalse(results[self.model_b.id].is_error)  # unaffected by model_a's failure

    def test_never_substitutes_a_different_model_when_one_fails(self):
        # No invisible fallback: results are keyed strictly by the model that was actually
        # requested for that call -- a failed model_a call must never appear under model_b's key
        # or vice versa, and compare_models never invents a third result.
        self.model_a.enabled = False
        self.model_a.save()
        results = compare_models([(self.model_a, _request()), (self.model_b, _request())])
        self.assertEqual(len(results), 2)
        self.assertIn(self.model_a.id, results)
        self.assertIn(self.model_b.id, results)

    def test_writes_one_independent_call_log_per_model(self):
        from llm_provider.models import LLMCallLog

        compare_models([(self.model_a, _request()), (self.model_b, _request())])
        self.assertEqual(LLMCallLog.objects.filter(requested_model=self.model_a).count(), 1)
        self.assertEqual(LLMCallLog.objects.filter(requested_model=self.model_b).count(), 1)
