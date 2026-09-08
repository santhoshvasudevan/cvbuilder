from django.test import SimpleTestCase

from llm_provider.errors import (
    TRANSIENT_ERROR_CATEGORIES,
    LLMErrorCategory,
    NormalizedLLMError,
    sanitize_error_message,
)


class SanitizeErrorMessageTests(SimpleTestCase):
    def test_empty_string_returns_empty(self):
        self.assertEqual(sanitize_error_message(""), "")

    def test_collapses_whitespace(self):
        self.assertEqual(sanitize_error_message("a\n\n  b   c"), "a b c")

    def test_redacts_json_object_body(self):
        raw = 'Request failed: {"prompt": "candidate secret resume text", "key": "sk-abc123"}'
        result = sanitize_error_message(raw)
        self.assertNotIn("candidate secret resume text", result)
        self.assertNotIn("sk-abc123", result)
        self.assertIn("[redacted body]", result)

    def test_redacts_json_array_body(self):
        raw = 'Bad messages: [{"role": "user", "content": "sensitive"}]'
        result = sanitize_error_message(raw)
        self.assertNotIn("sensitive", result)

    def test_truncates_long_messages(self):
        raw = "x" * 500
        result = sanitize_error_message(raw)
        self.assertLessEqual(len(result), 220)
        self.assertTrue(result.endswith("…[truncated]"))


class NormalizedLLMErrorTests(SimpleTestCase):
    def test_from_exception_sanitizes_message(self):
        exc = ValueError('provider said: {"secret": "leaked-value-should-not-appear"}')
        error = NormalizedLLMError.from_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
        self.assertNotIn("leaked-value-should-not-appear", error.message)
        self.assertEqual(error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    def test_partial_output_received_defaults_false(self):
        error = NormalizedLLMError(category=LLMErrorCategory.TIMEOUT, message="timed out")
        self.assertFalse(error.partial_output_received)


class TransientCategoryTests(SimpleTestCase):
    def test_configuration_and_auth_are_never_transient(self):
        self.assertNotIn(LLMErrorCategory.CONFIGURATION, TRANSIENT_ERROR_CATEGORIES)
        self.assertNotIn(LLMErrorCategory.AUTH, TRANSIENT_ERROR_CATEGORIES)
        self.assertNotIn(LLMErrorCategory.SCHEMA_VALIDATION, TRANSIENT_ERROR_CATEGORIES)

    def test_rate_limit_timeout_and_provider_internal_are_transient(self):
        self.assertIn(LLMErrorCategory.RATE_LIMIT, TRANSIENT_ERROR_CATEGORIES)
        self.assertIn(LLMErrorCategory.TIMEOUT, TRANSIENT_ERROR_CATEGORIES)
        self.assertIn(LLMErrorCategory.PROVIDER_INTERNAL, TRANSIENT_ERROR_CATEGORIES)
