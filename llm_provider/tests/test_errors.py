from django.test import SimpleTestCase

from ..errors import LLMErrorCategory, NormalizedLLMError, sanitize_error_message


class SanitizeErrorMessageTests(SimpleTestCase):
    def test_empty_input(self):
        self.assertEqual(sanitize_error_message(""), "")

    def test_redacts_json_body_shaped_content(self):
        raw = 'Request failed: {"api_key": "sk-secret-value", "prompt": "candidate details here"}'
        sanitized = sanitize_error_message(raw)
        self.assertNotIn("sk-secret-value", sanitized)
        self.assertNotIn("candidate details", sanitized)
        self.assertIn("[redacted body]", sanitized)

    def test_truncates_long_messages(self):
        raw = "x" * 1000
        sanitized = sanitize_error_message(raw)
        self.assertLessEqual(len(sanitized), 220)
        self.assertTrue(sanitized.endswith("…[truncated]"))

    def test_collapses_whitespace(self):
        raw = "line one\n\nline   two\t\tline three"
        sanitized = sanitize_error_message(raw)
        self.assertNotIn("\n", sanitized)
        self.assertNotIn("\t", sanitized)


class NormalizedLLMErrorTests(SimpleTestCase):
    def test_from_exception_sanitizes_message(self):
        exc = ValueError('bad response: {"secret": "leak-me"}')
        error = NormalizedLLMError.from_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
        self.assertEqual(error.category, LLMErrorCategory.PROVIDER_INTERNAL)
        self.assertNotIn("leak-me", error.message)
        self.assertFalse(error.partial_output_received)

    def test_from_exception_records_partial_output_flag(self):
        error = NormalizedLLMError.from_exception(
            LLMErrorCategory.TIMEOUT, ValueError("stream cut off"), partial_output_received=True
        )
        self.assertTrue(error.partial_output_received)
