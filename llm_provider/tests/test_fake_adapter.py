"""End-to-end tests through BaseLLMAdapter.generate() via FakeAdapter -- the only adapter tests
that exercise the *shared* call path (validation -> retry -> schema re-validation -> audit log)
without any network access at all. Real-provider adapters are tested separately for schema
translation and config guards (test_real_adapter_config_guards.py), always with `requests.post`
mocked so zero live calls occur anywhere in this suite.
"""

from django.test import TestCase
from pydantic import BaseModel

from job_applications.models import JobApplication, StageIdentifier, StageRun
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.errors import LLMErrorCategory, NormalizedLLMError
from llm_provider.models import LLMCallLog, ReasoningLevel
from llm_provider.types import NormalizedLLMRequest, TokenUsage

from .factories import make_fake_provider, make_model


class Recommendation(BaseModel):
    title: str
    score: int


class FakeAdapterGenerateTests(TestCase):
    def setUp(self):
        self.provider = make_fake_provider()
        self.model = make_model(
            self.provider,
            model_identifier="fake-1",
            supports_structured_output=True,
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.MEDIUM],
            max_output_tokens=2000,
        )
        self.request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hello"}],
            output_schema=Recommendation,
        )

    def test_successful_call_returns_validated_pydantic_instance(self):
        adapter = FakeAdapter(self.model, fixed_response={"title": "Strong fit", "score": 9})
        result = adapter.generate(self.request)
        self.assertFalse(result.is_error)
        self.assertIsInstance(result.content, Recommendation)
        self.assertEqual(result.content.title, "Strong fit")

    def test_malformed_response_is_rejected_as_schema_validation_error(self):
        adapter = FakeAdapter(self.model, fixed_response={"title": "missing score"})
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_call_log_written_on_success(self):
        adapter = FakeAdapter(
            self.model,
            fixed_response={"title": "x", "score": 1},
            fixed_usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        adapter.generate(self.request)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.stage, StageIdentifier.AJ_ANALYZE)
        self.assertEqual(log.requested_provider_id, self.provider.id)
        self.assertEqual(log.requested_model_id, self.model.id)
        self.assertEqual(log.total_tokens, 15)
        self.assertEqual(log.error_category, "")

    def test_call_log_written_on_error_with_no_sensitive_content(self):
        from llm_provider.types import NormalizedLLMResult

        # AUTH is non-transient (llm_provider.errors.TRANSIENT_ERROR_CATEGORIES) so this never
        # enters the real retry/backoff loop -- keeps the test fast and deterministic.
        adapter = FakeAdapter(
            self.model,
            fixed_error=NormalizedLLMResult(
                error=NormalizedLLMError(category=LLMErrorCategory.AUTH, message="auth failed")
            ),
        )
        adapter.generate(self.request)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.error_category, LLMErrorCategory.AUTH.value)
        self.assertEqual(log.error_message, "auth failed")
        self.assertNotIn("hello", log.error_message)

    def test_no_prompt_or_response_body_ever_reaches_the_call_log(self):
        adapter = FakeAdapter(
            self.model, fixed_response={"title": "candidate specific secret content", "score": 7}
        )
        adapter.generate(self.request)
        log = LLMCallLog.objects.get()
        string_fields = [getattr(log, f.name) for f in LLMCallLog._meta.fields]
        stored_text = " ".join(str(value) for value in string_fields if isinstance(value, str))
        self.assertNotIn("candidate specific secret content", stored_text)
        self.assertNotIn("hello", stored_text)  # the request message content

    def test_fails_before_any_call_when_provider_inactive(self):
        self.provider.enabled = False
        self.provider.save()
        adapter = FakeAdapter(self.model, fixed_response={"title": "x", "score": 1})
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertEqual(adapter.call_count, 0)  # _call_once (the "HTTP" boundary) never ran

    def test_fails_before_any_call_when_reasoning_level_unsupported(self):
        adapter = FakeAdapter(self.model, fixed_response={"title": "x", "score": 1})
        request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hello"}],
            output_schema=Recommendation,
            reasoning_level=ReasoningLevel.XHIGH,
        )
        result = adapter.generate(request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertEqual(adapter.call_count, 0)

    def test_fails_before_any_call_when_budget_exceeds_capability(self):
        adapter = FakeAdapter(self.model, fixed_response={"title": "x", "score": 1})
        request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hello"}],
            output_schema=Recommendation,
            max_output_tokens=999_999,
        )
        result = adapter.generate(request)
        self.assertTrue(result.is_error)
        self.assertEqual(adapter.call_count, 0)

    def test_stage_run_correlation_is_recorded_when_provided(self):
        job = JobApplication.objects.create(
            employer="Acme",
            job_title="Engineer",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/1",
        )
        stage_run = StageRun.objects.create(
            job_application=job, stage=StageIdentifier.AJ_ANALYZE, input_snapshot={}
        )
        adapter = FakeAdapter(self.model, fixed_response={"title": "x", "score": 1}, stage_run=stage_run)
        adapter.generate(self.request)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.stage_run_id, stage_run.id)

    def test_standalone_call_without_stage_run_is_allowed(self):
        adapter = FakeAdapter(self.model, fixed_response={"title": "x", "score": 1})
        adapter.generate(self.request)
        log = LLMCallLog.objects.get()
        self.assertIsNone(log.stage_run)
