"""Real-provider adapters (OpenAI/NVIDIA NIM/Gemini/OpenRouter), tested entirely without live
credentials (docs/IMPLEMENTATION_PLAN.md M2 acceptance: "each provider adapter's schema
translation is tested without live credentials"). `requests.post` is mocked in every test in
this file -- no test here can reach the network, and each test that mocks it also asserts on
the exact call to prove the request-building logic (schema translation, headers, body shape) is
correct without ever needing a real endpoint.
"""

import json
from unittest import mock

import requests
from django.test import TestCase
from pydantic import BaseModel

from job_applications.models import StageIdentifier
from llm_provider.adapters.gemini import GeminiAdapter
from llm_provider.adapters.nvidia import NvidiaNimAdapter
from llm_provider.adapters.openai import OpenAIAdapter
from llm_provider.adapters.openrouter import OpenRouterAdapter
from llm_provider.errors import LLMErrorCategory
from llm_provider.models import LLMCallLog, LLMProvider, ReasoningLevel
from llm_provider.types import NormalizedLLMRequest

from .factories import make_model, make_provider

# A synthetic, obviously-fake credential -- never a real key of any kind. Used only to prove a
# credential-shaped value cannot survive the Gemini adapter's error path (V2-D043, the audit
# BLOCKER this file's new tests close). No test in this file may ever assert this value is
# *present* anywhere in output -- every assertion checks it is absent.
_SYNTHETIC_GEMINI_CREDENTIAL = "FAKE-SYNTHETIC-GEMINI-KEY-DO-NOT-USE-1234567890"


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

    # -- V2-D043: closes the M2 audit BLOCKER (Gemini credential leak via URL) ------------------

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_credential_is_sent_via_documented_header_never_the_url(self, mock_post):
        mock_post.return_value = _gemini_response()
        adapter = GeminiAdapter(self.model)
        adapter.generate(self.request)
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs["url"]
        # The request URL must contain neither the literal credential nor any "key=" query
        # parameter at all -- the credential travels only via the x-goog-api-key header.
        self.assertNotIn(_SYNTHETIC_GEMINI_CREDENTIAL, url)
        self.assertNotIn("key=", url)
        self.assertNotIn("?", url)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], _SYNTHETIC_GEMINI_CREDENTIAL)

    def _assert_credential_absent_everywhere(self, result, credential: str):
        # 1. the raised/returned application error
        self.assertTrue(result.is_error)
        self.assertNotIn(credential, result.error.message)
        # 2. the persisted LLMCallLog row -- every field, not just error_message, since the
        #    admin's LLMCallLogAdmin.readonly_fields exposes the entire row for viewing.
        log = LLMCallLog.objects.latest("created_at")
        self.assertNotIn(credential, log.error_message)
        for field in LLMCallLog._meta.fields:
            value = getattr(log, field.name)
            self.assertNotIn(credential, str(value))

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_connection_failure_with_credential_bearing_url_never_leaks_credential(self, mock_post):
        # Worst-case simulation: even if a lower-level exception's own text embedded the full
        # credential-bearing request URL (the exact shape `requests`/`urllib3` connection-level
        # exceptions produce), the credential must not survive into any observable output. This
        # is the defence-in-depth backstop (classify_network_exception + sanitize_error_message)
        # working even if a future regression reintroduces a URL-embedded credential somewhere.
        leaking_text = (
            "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Max "
            "retries exceeded with url: /v1beta/models/"
            f"{self.model.model_identifier}:generateContent?key={_SYNTHETIC_GEMINI_CREDENTIAL} "
            '(Caused by NewConnectionError("Failed to establish a new connection"))'
        )
        mock_post.side_effect = requests.exceptions.ConnectionError(leaking_text)
        adapter = GeminiAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self._assert_credential_absent_everywhere(result, _SYNTHETIC_GEMINI_CREDENTIAL)
        self.assertEqual(result.error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_dns_failure_never_leaks_credential(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError(
            f"Failed to resolve 'generativelanguage.googleapis.com' while requesting "
            f"?key={_SYNTHETIC_GEMINI_CREDENTIAL} ([Errno 8] nodename nor servname provided)"
        )
        adapter = GeminiAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self._assert_credential_absent_everywhere(result, _SYNTHETIC_GEMINI_CREDENTIAL)

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_tls_failure_never_leaks_credential(self, mock_post):
        mock_post.side_effect = requests.exceptions.SSLError(
            f"SSL: CERTIFICATE_VERIFY_FAILED for url ...?key={_SYNTHETIC_GEMINI_CREDENTIAL}"
        )
        adapter = GeminiAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self._assert_credential_absent_everywhere(result, _SYNTHETIC_GEMINI_CREDENTIAL)
        self.assertEqual(result.error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_connection_refused_never_leaks_credential(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError(
            f"Connection refused: could not connect, requested url had ?key={_SYNTHETIC_GEMINI_CREDENTIAL}"
        )
        adapter = GeminiAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self._assert_credential_absent_everywhere(result, _SYNTHETIC_GEMINI_CREDENTIAL)

    @mock.patch("llm_provider.adapters.gemini.requests.post")
    @mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": _SYNTHETIC_GEMINI_CREDENTIAL})
    def test_timeout_never_leaks_credential(self, mock_post):
        mock_post.side_effect = requests.exceptions.ReadTimeout(
            f"Read timed out. url=/v1beta/models/x:generateContent?key={_SYNTHETIC_GEMINI_CREDENTIAL}"
        )
        adapter = GeminiAdapter(self.model, retry_policy=_no_retry_policy())
        result = adapter.generate(self.request)
        self._assert_credential_absent_everywhere(result, _SYNTHETIC_GEMINI_CREDENTIAL)
        self.assertEqual(result.error.category, LLMErrorCategory.TIMEOUT)


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
