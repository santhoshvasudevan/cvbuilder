from typing import Literal

from django.test import TestCase
from pydantic import BaseModel

from ..adapters import FakeAdapter
from ..models import LLMCallLog, StageModelAssignment
from ..types import NormalizedLLMRequest, TokenUsage
from .factories import make_model


class _TinyOutput(BaseModel):
    answer: str
    confidence: Literal["low", "medium", "high"]


class FakeAdapterCallLogTests(TestCase):
    def test_generate_writes_exactly_one_call_log_row_with_correct_fields(self):
        model = make_model(model_id="fake-1")
        adapter = FakeAdapter(
            model,
            fixed_response={"answer": "42", "confidence": "high"},
            fixed_usage=TokenUsage(input_tokens=11, cached_input_tokens=2, output_tokens=7, total_tokens=18),
        )
        request = NormalizedLLMRequest(
            stage=StageModelAssignment.Stage.MEMORY_BUILD,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_TinyOutput,
        )

        result = adapter.generate(request)

        self.assertFalse(result.is_error)
        self.assertIsInstance(result.content, _TinyOutput)
        self.assertEqual(result.content.answer, "42")

        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.provider, model.provider)
        self.assertEqual(log.model, model)
        self.assertEqual(log.stage, StageModelAssignment.Stage.MEMORY_BUILD)
        self.assertEqual(log.input_tokens, 11)
        self.assertEqual(log.cached_input_tokens, 2)
        self.assertEqual(log.output_tokens, 7)
        self.assertEqual(log.total_tokens, 18)
        self.assertEqual(log.retry_count, 0)
        self.assertEqual(log.error_category, "")
        self.assertIsNotNone(log.latency_ms)

    def test_invalid_fixed_response_is_reported_as_schema_validation_error_and_logged(self):
        model = make_model(model_id="fake-2")
        adapter = FakeAdapter(model, fixed_response={"answer": "42", "confidence": "extremely-high"})
        request = NormalizedLLMRequest(
            stage=StageModelAssignment.Stage.AC_MATCH,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_TinyOutput,
        )

        result = adapter.generate(request)

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "SCHEMA_VALIDATION")
        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.error_category, "SCHEMA_VALIDATION")
        self.assertNotIn("extremely-high", log.error_message)
