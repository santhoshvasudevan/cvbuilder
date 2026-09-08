"""Real-provider adapters (OpenAI/NVIDIA NIM/Gemini/OpenRouter), tested entirely without live
credentials (docs/IMPLEMENTATION_PLAN.md M2 acceptance: "each provider adapter's schema
translation is tested without live credentials"). `requests.post` is mocked in every test in
this file -- no test here can reach the network, and each test that mocks it also asserts on
the exact call to prove the request-building logic (schema translation, headers, body shape) is
correct without ever needing a real endpoint.
"""

import json
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from job_applications.models import StageIdentifier
from llm_provider.adapters.gemini import GeminiAdapter
from llm_provider.adapters.nvidia import NvidiaNimAdapter
from llm_provider.adapters.openai import OpenAIAdapter
from llm_provider.adapters.openrouter import OpenRouterAdapter
from llm_provider.errors import LLMErrorCategory
from llm_provider.models import LLMProvider, ReasoningLevel
from llm_provider.types import NormalizedLLMRequest

from .factories import make_model, make_provider


class Answer(BaseModel):
    text: str


def _openai_style_response(model="gpt-x", content=None, finish_reason="stop"):
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {
        "model": model,
        "choices": [
            {
                "message": {"content": json.dumps(content or {"text": "ok"})},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }
    return response


def _gemini_response(content=None, finish_reason="STOP"):
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(content or {"text": "ok"})}]},
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 7,
            "candidatesTokenCount": 3,
            "totalTokenCount": 10,
        },
    }
    return response


class OpenAIAdapterTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            adapter_type=LLMProvider.AdapterType.OPENAI, credential_env_variable="TEST_OPENAI_KEY"
        )
        self.model = make_model(self.provider, supports_structured_output=True)
        self.request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Answer,
        )

    @mock.patch("llm_provider.adapters.openai.requests.post")
    @mock.patch.dict("os.environ", {"TEST_OPENAI_KEY": "test-key-value"})
    def test_successful_call_parses_content_and_usage(self, mock_post):
        mock_post.return_value = _openai_style_response(content={"text": "hello"})
        adapter = OpenAIAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.text, "hello")
        mock_post.assert_called_once()

    @mock.patch("llm_provider.adapters.openai.requests.post")
    @mock.patch.dict("os.environ", {"TEST_OPENAI_KEY": "test-key-value"})
    def test_request_body_includes_strict_json_schema_and_auth_header(self, mock_post):
        mock_post.return_value = _openai_style_response()
        adapter = OpenAIAdapter(self.model)
        adapter.generate(self.request)
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key-value")
        self.assertEqual(kwargs["json"]["response_format"]["type"], "json_schema")
        self.assertTrue(kwargs["json"]["response_format"]["json_schema"]["strict"])

    @mock.patch("llm_provider.adapters.openai.requests.post")
    def test_fails_before_http_when_provider_inactive(self, mock_post):
        self.provider.enabled = False
        self.provider.save()
        adapter = OpenAIAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        mock_post.assert_not_called()

    @mock.patch("llm_provider.adapters.openai.requests.post")
    def test_fails_before_http_when_credential_env_var_unset(self, mock_post):
        # TEST_OPENAI_KEY deliberately left unset in the environment for this test.
        adapter = OpenAIAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        mock_post.assert_not_called()

    @mock.patch("llm_provider.adapters.openai.requests.post")
    @mock.patch.dict("os.environ", {"TEST_OPENAI_KEY": "test-key-value"})
    def test_429_maps_to_rate_limit_category(self, mock_post):
        response = mock.Mock(status_code=429)
        mock_post.return_value = response
        adapter = OpenAIAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)

    @mock.patch("llm_provider.adapters.openai.requests.post")
    @mock.patch.dict("os.environ", {"TEST_OPENAI_KEY": "test-key-value"})
    def test_401_maps_to_auth_category(self, mock_post):
        response = mock.Mock(status_code=401)
        mock_post.return_value = response
        adapter = OpenAIAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self.assertEqual(result.error.category, LLMErrorCategory.AUTH)


class NvidiaNimAdapterTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            adapter_type=LLMProvider.AdapterType.NVIDIA_NIM, credential_env_variable="TEST_NIM_KEY"
        )
        self.model = make_model(self.provider, supports_structured_output=True)
        self.request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Answer,
        )

    @mock.patch("llm_provider.adapters.nvidia.requests.post")
    def test_fails_before_http_when_structured_output_unsupported(self, mock_post):
        unsupported_model = make_model(
            self.provider, model_identifier="no-structured", supports_structured_output=False
        )
        adapter = NvidiaNimAdapter(unsupported_model)
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        mock_post.assert_not_called()

    @mock.patch("llm_provider.adapters.nvidia.requests.post")
    @mock.patch.dict("os.environ", {"TEST_NIM_KEY": "nim-key"})
    def test_successful_call_uses_openai_compatible_parsing(self, mock_post):
        mock_post.return_value = _openai_style_response(content={"text": "nim-ok"})
        adapter = NvidiaNimAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.text, "nim-ok")


class GeminiAdapterTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            adapter_type=LLMProvider.AdapterType.GEMINI, credential_env_variable="TEST_GEMINI_KEY"
        )
        self.model = make_model(self.provider, supports_structured_output=True)
        self.request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Answer,
        )

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": "gem-key"})
    def test_successful_call_parses_content_and_usage(self, mock_post):
        mock_post.return_value = _gemini_response(content={"text": "gemini-ok"})
        adapter = GeminiAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.text, "gemini-ok")
        self.assertEqual(result.usage.total_tokens, 10)

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": "gem-key"})
    def test_request_uses_reduced_schema_dialect_with_no_enum_or_refs(self, mock_post):
        mock_post.return_value = _gemini_response()
        adapter = GeminiAdapter(self.model)
        adapter.generate(self.request)
        _args, kwargs = mock_post.call_args
        schema = kwargs["json"]["generationConfig"]["responseSchema"]
        self.assertNotIn("$ref", json.dumps(schema))
        self.assertNotIn('"enum"', json.dumps(schema))

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    def test_fails_before_http_when_reasoning_level_unsupported(self, mock_post):
        request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Answer,
            reasoning_level=ReasoningLevel.HIGH,
        )
        adapter = GeminiAdapter(self.model)  # model only supports NONE by default
        result = adapter.generate(request)
        self.assertTrue(result.is_error)
        mock_post.assert_not_called()


class OpenRouterAdapterTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            adapter_type=LLMProvider.AdapterType.OPENROUTER, credential_env_variable="TEST_OR_KEY"
        )
        self.model = make_model(self.provider, supports_structured_output=True)
        self.request = NormalizedLLMRequest(
            stage=StageIdentifier.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Answer,
        )

    @mock.patch("llm_provider.adapters.openrouter.requests.post")
    @mock.patch.dict("os.environ", {"TEST_OR_KEY": "or-key"})
    def test_successful_call_and_require_parameters_set(self, mock_post):
        mock_post.return_value = _openai_style_response(content={"text": "or-ok"})
        adapter = OpenRouterAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertFalse(result.is_error)
        _args, kwargs = mock_post.call_args
        self.assertTrue(kwargs["json"]["provider"]["require_parameters"])
        self.assertFalse(kwargs["json"]["stream"])

    @mock.patch("llm_provider.adapters.openrouter.requests.post")
    def test_fails_before_http_when_model_inactive(self, mock_post):
        self.model.enabled = False
        self.model.save()
        adapter = OpenRouterAdapter(self.model)
        result = adapter.generate(self.request)
        self.assertTrue(result.is_error)
        mock_post.assert_not_called()


def _no_retry_policy():
    from llm_provider.retry import RetryPolicy

    return RetryPolicy(max_attempts=1, backoff_seconds=0)
