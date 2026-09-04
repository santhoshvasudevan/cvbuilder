"""Null/invalid final-content handling for the shared OpenAI-compatible response parser
(2026-09-04, D-026).

The real incident that motivated this file: an OpenRouter reasoning-enabled smoke test returned a
genuine HTTP 200 response shaped like `_REAL_INCIDENT_PAYLOAD` below -- the model spent its entire
64-token output budget "thinking" and returned `message.content=None` with `finish_reason=
"length"`. `parse_openai_style_chat_completion`'s own `except (KeyError, IndexError, json.
JSONDecodeError)` clause did not catch the `TypeError` that `json.loads(None)` raises, so the
failure went uncaught: it bypassed `BaseLLMAdapter._write_call_log()` entirely (no audit trail of
a real, token-spending call) and never reached the `finish_reason == "length"` -> CONFIGURATION
classification the code already anticipated in a comment.

These tests first reproduce the exact response shape (and its null/missing/non-string/empty/
malformed-content variants) directly against the shared parser, then re-prove the same behavior
end-to-end through representative NVIDIA NIM, OpenAI, and OpenRouter adapter paths -- all three
share `parse_openai_style_chat_completion` -- including that exactly one sanitized `LLMCallLog`
row is written per logical call and that no prompt/reasoning/credential content ever reaches it.

**Amendment (2026-09-04, same-day correction)**: the first pass at this fix let a `finish_reason=
"length"` response through as a *success* whenever its (possibly truncated) content still happened
to parse as valid JSON -- e.g. `test_malformed_json_string_with_finish_reason_length_stays_schema_
validation` (removed below) asserted `SCHEMA_VALIDATION` for that combination instead of the
correct `CONFIGURATION`. That was wrong: a `length` finish reason unconditionally means the
provider stopped because it hit the output-token limit, not because it finished, so truncated
content must never be accepted as a genuine final answer merely because it happens to parse. The
parser and the tests below (`test_valid_json_string_with_finish_reason_length_is_configuration_
not_success` and its adapter-level counterpart) now enforce `finish_reason == "length"` ->
`CONFIGURATION` unconditionally, checked before any content parsing is attempted, regardless of
what shape or validity `message.content` has.
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase
from pydantic import BaseModel

from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter, parse_openai_style_chat_completion
from ..adapters.openrouter import OpenRouterAdapter
from ..errors import TRANSIENT_ERROR_CATEGORIES, LLMErrorCategory
from ..models import LLMCallLog, LLMProvider
from ..retry import is_retryable
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


_REAL_INCIDENT_PAYLOAD = {
    "choices": [
        {
            "finish_reason": "length",
            "message": {"content": None, "reasoning_details": []},
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 64, "total_tokens": 74},
}


class SharedParserNullContentTests(SimpleTestCase):
    """Direct, no-network coverage of `parse_openai_style_chat_completion` itself."""

    def test_real_incident_payload_no_longer_raises_and_is_configuration(self):
        result = parse_openai_style_chat_completion(_FakeResponse(payload=_REAL_INCIDENT_PAYLOAD))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertIn("finish_reason=length", result.error.message)

    def test_null_content_with_finish_reason_stop_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_missing_content_key_with_finish_reason_length_is_configuration(self):
        payload = {"choices": [{"finish_reason": "length", "message": {}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_missing_content_key_with_finish_reason_stop_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_numeric_content_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": 42}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_list_content_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": ["a", "b"]}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_object_content_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": {"nested": True}}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_empty_string_content_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_whitespace_only_content_is_malformed_response(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "   \n\t "}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_whitespace_only_content_with_finish_reason_length_is_configuration(self):
        payload = {"choices": [{"finish_reason": "length", "message": {"content": "   "}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_malformed_json_string_with_finish_reason_stop_stays_schema_validation(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_malformed_json_string_with_finish_reason_length_is_configuration(self):
        """D-026 correction (2026-09-04): `finish_reason=length` reports output-budget exhaustion
        unconditionally -- it must never fall through to the malformed-content bucket just because
        the (possibly truncated) content also happens to be unparseable JSON."""
        payload = {"choices": [{"finish_reason": "length", "message": {"content": "not json"}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_numeric_content_with_finish_reason_length_is_configuration(self):
        payload = {"choices": [{"finish_reason": "length", "message": {"content": 42}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_valid_json_string_with_finish_reason_stop_succeeds(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertFalse(result.is_error)
        self.assertEqual(result.content, {"ok": True})

    def test_valid_json_string_with_finish_reason_length_is_configuration_not_success(self):
        """The critical case a first pass at this fix got wrong: truncated output whose partial
        text happens to still parse as valid JSON must never be accepted as a genuine final
        answer -- `finish_reason=length` means the provider stopped because it hit the token
        limit, not because it finished; content must never be trusted merely because it parses."""
        payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"ok": true}'}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertIn("finish_reason=length", result.error.message)

    def test_reasoning_fields_never_substitute_for_missing_content(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning": "internal chain of thought, never trusted",
                        "reasoning_content": "also never trusted",
                        "reasoning_details": [{"type": "reasoning.text", "text": "secret"}],
                    },
                }
            ]
        }
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn("internal chain of thought", result.error.message)
        self.assertNotIn("secret", result.error.message)

    def test_usage_preserved_on_null_content_length_failure(self):
        result = parse_openai_style_chat_completion(_FakeResponse(payload=_REAL_INCIDENT_PAYLOAD))
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 64)
        self.assertEqual(result.usage.total_tokens, 74)

    def test_usage_preserved_on_null_content_non_length_failure(self):
        payload = {
            "choices": [{"finish_reason": "stop", "message": {"content": None}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertEqual(result.usage.total_tokens, 4)

    def test_null_content_length_failure_is_never_retryable(self):
        result = parse_openai_style_chat_completion(_FakeResponse(payload=_REAL_INCIDENT_PAYLOAD))
        self.assertFalse(is_retryable(result))
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)

    def test_null_content_non_length_failure_is_never_retryable(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertFalse(is_retryable(result))

    def test_malformed_json_failure_is_never_retryable(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
        result = parse_openai_style_chat_completion(_FakeResponse(payload=payload))
        self.assertFalse(is_retryable(result))


_PLANTED_PROMPT = "PLANTED_PROMPT_MARKER_never_persisted"
_PLANTED_REASONING = "PLANTED_REASONING_MARKER_never_persisted"
_PLANTED_API_KEY = "sk-PLANTED_SECRET_KEY_never_persisted"


class _NullContentOutput(BaseModel):
    ok: bool = True


def _request() -> NormalizedLLMRequest:
    return NormalizedLLMRequest(
        stage="MEMORY_BUILD",
        messages=[{"role": "user", "content": _PLANTED_PROMPT}],
        output_schema=_NullContentOutput,
        max_output_tokens=64,
        reasoning_enabled=True,
    )


def _assert_log_never_contains_planted_secrets(test: TestCase, log: LLMCallLog) -> None:
    for field in LLMCallLog._meta.fields:
        value = str(getattr(log, field.name))
        test.assertNotIn(_PLANTED_PROMPT, value)
        test.assertNotIn(_PLANTED_REASONING, value)
        test.assertNotIn(_PLANTED_API_KEY, value)


class _AdapterNullContentAuditMixin:
    """Shared assertions run against each of NVIDIA NIM / OpenAI / OpenRouter -- subclasses set
    `self.adapter_cls`, `self.env_var`, and `self.model` in `setUp`."""

    def test_real_incident_payload_writes_exactly_one_sanitized_call_log(self):
        with mock.patch.dict("os.environ", {self.env_var: _PLANTED_API_KEY}):
            with mock.patch(
                "requests.post", return_value=_FakeResponse(payload=_REAL_INCIDENT_PAYLOAD)
            ) as post_mock:
                result = self.adapter_cls(self.model).generate(_request())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertEqual(post_mock.call_count, 1)  # never retried
        self.assertEqual(result.retry_count, 0)

        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.error_category, LLMErrorCategory.CONFIGURATION.value)
        self.assertEqual(log.output_tokens, 64)
        self.assertEqual(log.total_tokens, 74)
        self.assertEqual(log.retry_count, 0)
        self.assertIn("finish_reason=length", log.error_message)
        _assert_log_never_contains_planted_secrets(self, log)

    def test_reasoning_text_in_response_never_reaches_the_call_log(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning": _PLANTED_REASONING},
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 64, "total_tokens": 69},
        }
        with mock.patch.dict("os.environ", {self.env_var: _PLANTED_API_KEY}):
            with mock.patch("requests.post", return_value=_FakeResponse(payload=payload)):
                self.adapter_cls(self.model).generate(_request())

        self.assertEqual(LLMCallLog.objects.count(), 1)
        _assert_log_never_contains_planted_secrets(self, LLMCallLog.objects.get())

    def test_malformed_content_failure_also_writes_exactly_one_call_log(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
        with mock.patch.dict("os.environ", {self.env_var: _PLANTED_API_KEY}):
            with mock.patch(
                "requests.post", return_value=_FakeResponse(payload=payload)
            ) as post_mock:
                result = self.adapter_cls(self.model).generate(_request())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)
        self.assertEqual(post_mock.call_count, 1)  # never retried
        self.assertEqual(LLMCallLog.objects.count(), 1)

    def test_valid_json_content_with_finish_reason_length_is_never_accepted_as_success(self):
        """D-026 correction: truncated output whose partial text happens to still parse as valid
        JSON must never be accepted as a genuine final answer -- proven end-to-end through the
        real adapter/logging path, not just at the parser level."""
        payload = {
            "choices": [{"finish_reason": "length", "message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 64, "total_tokens": 69},
        }
        with mock.patch.dict("os.environ", {self.env_var: _PLANTED_API_KEY}):
            with mock.patch(
                "requests.post", return_value=_FakeResponse(payload=payload)
            ) as post_mock:
                result = self.adapter_cls(self.model).generate(_request())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertEqual(post_mock.call_count, 1)  # never retried
        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        self.assertEqual(log.error_category, LLMErrorCategory.CONFIGURATION.value)
        self.assertIn("finish_reason=length", log.error_message)

    def test_valid_non_truncated_response_still_succeeds(self):
        """Sanity check that this correction changes nothing about the ordinary success path."""
        payload = {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        with mock.patch.dict("os.environ", {self.env_var: _PLANTED_API_KEY}):
            with mock.patch("requests.post", return_value=_FakeResponse(payload=payload)):
                result = self.adapter_cls(self.model).generate(_request())

        self.assertFalse(result.is_error)
        self.assertEqual(result.content.ok, True)
        self.assertEqual(LLMCallLog.objects.count(), 1)
        self.assertEqual(LLMCallLog.objects.get().error_category, "")


class NvidiaNullContentAuditTests(_AdapterNullContentAuditMixin, TestCase):
    def setUp(self):
        self.env_var = "TEST_NVIDIA_NULL_CONTENT_KEY"
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA null-content test",
            credential_env_var=self.env_var,
        )
        self.model = make_model(provider=provider, model_id="nvidia/nemotron-3-super-120b-a12b")
        self.adapter_cls = NvidiaNimAdapter


class OpenAiNullContentAuditTests(_AdapterNullContentAuditMixin, TestCase):
    def setUp(self):
        self.env_var = "TEST_OPENAI_NULL_CONTENT_KEY"
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI null-content test",
            credential_env_var=self.env_var,
        )
        self.model = make_model(provider=provider, model_id="gpt-4o-mini")
        self.adapter_cls = OpenAIAdapter


class OpenRouterNullContentAuditTests(_AdapterNullContentAuditMixin, TestCase):
    def setUp(self):
        self.env_var = "TEST_OPENROUTER_NULL_CONTENT_KEY"
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter null-content test",
            credential_env_var=self.env_var,
        )
        self.model = make_model(
            provider=provider, model_id="z-ai/glm-5.2:free", supports_reasoning=True
        )
        self.adapter_cls = OpenRouterAdapter
