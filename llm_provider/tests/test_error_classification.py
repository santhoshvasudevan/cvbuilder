"""Response-status classification for the OpenAI-compatible (OpenAI/NVIDIA NIM) and Gemini
response parsers -- no network involved, just a minimal fake response stub with the attributes
each parser reads. A 404/410 (model not found/gone) must classify as CONFIGURATION, not the
misleading generic SCHEMA_VALIDATION bucket, since it's a registry/configuration problem (wrong
or stale model_id), never retried either way (CONFIGURATION is not in TRANSIENT_ERROR_CATEGORIES,
same as SCHEMA_VALIDATION was)."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..adapters.gemini import GeminiAdapter
from ..adapters.openai import parse_openai_style_chat_completion
from ..errors import TRANSIENT_ERROR_CATEGORIES, LLMErrorCategory


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class OpenAiStyleClassificationTests(SimpleTestCase):
    def test_404_is_configuration_not_schema_validation(self):
        result = parse_openai_style_chat_completion(_FakeResponse(404))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)

    def test_410_is_configuration_not_schema_validation(self):
        result = parse_openai_style_chat_completion(_FakeResponse(410))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_message_never_includes_raw_response_body(self):
        result = parse_openai_style_chat_completion(
            _FakeResponse(410, {"error": {"message": "candidate secret leak test"}})
        )
        self.assertNotIn("candidate secret leak test", result.error.message)

    def test_other_4xx_still_classified_as_schema_validation(self):
        result = parse_openai_style_chat_completion(_FakeResponse(422))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_finish_reason_length_with_empty_content_is_configuration_not_schema_validation(self):
        """Operator-decision repair: a reasoning model that spends its entire output budget on
        internal thinking before emitting content must be classified as a configuration issue
        (token limit too low for this model/prompt), not the generic JSON-decode-error message --
        and CONFIGURATION is never retried (not in TRANSIENT_ERROR_CATEGORIES)."""
        result = parse_openai_style_chat_completion(
            _FakeResponse(
                200,
                {
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 4096, "total_tokens": 4196},
                },
            )
        )
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)
        self.assertIn("finish_reason=length", result.error.message)
        # Usage is still recorded even on this failure -- token spend is real and must be visible.
        self.assertEqual(result.usage.output_tokens, 4096)

    def test_finish_reason_stop_with_unparseable_content_stays_schema_validation(self):
        """Truncation-specific classification must not swallow genuinely malformed JSON that
        wasn't caused by hitting the token limit."""
        result = parse_openai_style_chat_completion(
            _FakeResponse(
                200,
                {"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]},
            )
        )
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_finish_reason_length_message_never_includes_raw_content(self):
        result = parse_openai_style_chat_completion(
            _FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": "candidate secret leak test"},
                            "finish_reason": "length",
                        }
                    ]
                },
            )
        )
        self.assertNotIn("candidate secret leak test", result.error.message)

    def test_reasoning_style_extra_field_is_never_treated_as_final_content(self):
        """Even if a response includes some other reasoning/thinking-shaped field alongside the
        real `message.content`, only `content` is ever parsed as the structured answer."""
        result = parse_openai_style_chat_completion(
            _FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"ok": true}',
                                "reasoning_content": "internal chain-of-thought, never trusted",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.content, {"ok": True})

    def test_401_still_classified_as_auth(self):
        result = parse_openai_style_chat_completion(_FakeResponse(401))
        self.assertEqual(result.error.category, LLMErrorCategory.AUTH)

    def test_429_still_classified_as_rate_limit(self):
        result = parse_openai_style_chat_completion(_FakeResponse(429))
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)

    def test_5xx_still_classified_as_provider_internal(self):
        result = parse_openai_style_chat_completion(_FakeResponse(503))
        self.assertEqual(result.error.category, LLMErrorCategory.PROVIDER_INTERNAL)


class GeminiClassificationTests(SimpleTestCase):
    def test_404_is_configuration_not_schema_validation(self):
        result = GeminiAdapter._parse_response(_FakeResponse(404))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_410_is_configuration_not_schema_validation(self):
        result = GeminiAdapter._parse_response(_FakeResponse(410))
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_message_never_includes_raw_response_body(self):
        result = GeminiAdapter._parse_response(
            _FakeResponse(410, {"error": {"message": "candidate secret leak test"}})
        )
        self.assertNotIn("candidate secret leak test", result.error.message)

    def test_other_4xx_still_classified_as_schema_validation(self):
        result = GeminiAdapter._parse_response(_FakeResponse(400))
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)
