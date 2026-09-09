from django.test import SimpleTestCase

from llm_provider.errors import (
    TRANSIENT_ERROR_CATEGORIES,
    LLMErrorCategory,
    NormalizedLLMError,
    classify_network_exception,
    sanitize_error_message,
)

# A synthetic, obviously-fake credential -- never a real key of any kind, for any provider. Used
# only to prove the sanitizer redacts a credential-shaped value; the test suite must never
# contain, print, or produce output that could be mistaken for a real credential (V2-D043).
_SYNTHETIC_CREDENTIAL = "FAKE-SYNTHETIC-CREDENTIAL-DO-NOT-USE-1234567890"


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


class URLAndQueryStringSanitizationTests(SimpleTestCase):
    """V2-D043 defence-in-depth: sanitize_error_message must redact sensitive URL/query-string
    values wherever they appear, even without a body-shaped `{...}`/`[...]` wrapper -- this is
    exactly the shape a `requests`/`urllib3` connection-error message takes (the real M2 audit
    BLOCKER: a credential embedded in the Gemini adapter's request URL). Every test here asserts
    on *behavior* (the synthetic credential is provably absent from the output) rather than
    merely grepping the sanitizer's source for a redaction call.
    """

    def test_urllib3_style_connection_error_with_key_query_param_is_redacted(self):
        # The exact shape `str(requests.exceptions.ConnectionError)` produces -- no scheme, just
        # a bare "url: /path?key=..." fragment, which is why the URL pattern must not require a
        # scheme to match.
        raw = (
            "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Max "
            f"retries exceeded with url: /v1beta/models/x:generateContent?key={_SYNTHETIC_CREDENTIAL} "
            '(Caused by NewConnectionError("Failed to establish a new connection"))'
        )
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)
        self.assertIn("key=REDACTED", result)

    def test_full_url_with_api_key_query_param_is_redacted(self):
        raw = f"https://example.com/v1/chat?api_key={_SYNTHETIC_CREDENTIAL}&stream=true failed"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)

    def test_multiple_query_parameters_only_sensitive_one_is_redacted(self):
        raw = f"https://example.com/v1/x?model=gpt&token={_SYNTHETIC_CREDENTIAL}&stream=true"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)
        self.assertIn("model=gpt", result)
        self.assertIn("stream=true", result)

    def test_credential_at_start_of_query_string_is_redacted(self):
        raw = f"https://example.com/v1/x?secret={_SYNTHETIC_CREDENTIAL}&a=1&b=2"
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, sanitize_error_message(raw))

    def test_credential_in_middle_of_query_string_is_redacted(self):
        raw = f"https://example.com/v1/x?a=1&signature={_SYNTHETIC_CREDENTIAL}&b=2"
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, sanitize_error_message(raw))

    def test_credential_at_end_of_query_string_is_redacted(self):
        raw = f"https://example.com/v1/x?a=1&b=2&access_token={_SYNTHETIC_CREDENTIAL}"
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, sanitize_error_message(raw))

    def test_mixed_case_parameter_name_is_redacted(self):
        for param_name in ("Key", "API_KEY", "ApiKey", "Authorization", "PASSWORD"):
            with self.subTest(param_name=param_name):
                raw = f"https://example.com/v1/x?{param_name}={_SYNTHETIC_CREDENTIAL}"
                self.assertNotIn(_SYNTHETIC_CREDENTIAL, sanitize_error_message(raw))

    def test_url_encoded_credential_value_is_redacted(self):
        from urllib.parse import quote

        encoded = quote("weird/value+with=chars" + _SYNTHETIC_CREDENTIAL, safe="")
        raw = f"https://example.com/v1/x?secret={encoded}&ok=1"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)
        self.assertNotIn(encoded, result)

    def test_url_userinfo_credential_is_redacted(self):
        raw = f"https://user:{_SYNTHETIC_CREDENTIAL}@example.com/v1/x failed"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)

    def test_bearer_token_is_redacted(self):
        raw = f"Authorization: Bearer {_SYNTHETIC_CREDENTIAL} rejected by provider"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)

    def test_bare_bearer_token_without_header_name_is_redacted(self):
        raw = f"sent Bearer {_SYNTHETIC_CREDENTIAL} in the request"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)

    def test_dns_style_error_message_is_sanitized_and_stays_useful(self):
        raw = "Failed to resolve 'api.example.com' ([Errno 8] nodename nor servname provided)"
        result = sanitize_error_message(raw)
        self.assertIn("resolve", result)
        self.assertIn("api.example.com", result)

    def test_connection_refused_message_is_sanitized_and_stays_useful(self):
        raw = "Connection refused: could not connect to host 127.0.0.1 port 443"
        result = sanitize_error_message(raw)
        self.assertIn("Connection refused", result)

    def test_timeout_message_with_credential_query_param_is_redacted(self):
        raw = f"Read timed out. (read timeout=60) url=/v1/x?key={_SYNTHETIC_CREDENTIAL}"
        result = sanitize_error_message(raw)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, result)
        self.assertIn("timed out", result)

    def test_safe_diagnostic_text_with_no_secret_survives_unchanged_content(self):
        raw = "Provider returned malformed JSON in the response body at offset 42"
        result = sanitize_error_message(raw)
        self.assertIn("malformed JSON", result)
        self.assertIn("offset 42", result)


class NetworkExceptionClassificationTests(SimpleTestCase):
    """classify_network_exception is used at the adapter HTTP boundary specifically so a
    credential embedded in a request URL is never even a candidate for the stored/raised message
    (V2-D043) -- it must never echo the exception's own text."""

    def _make_exception(self, cls_name: str, bases: tuple[type, ...] = (Exception,)) -> Exception:
        exc_cls = type(cls_name, bases, {})
        return exc_cls(f"...url: /v1/x?key={_SYNTHETIC_CREDENTIAL} ...")

    def test_connection_error_classified_and_credential_absent(self):
        exc = self._make_exception("ConnectionError")
        message = classify_network_exception(exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, message)
        self.assertIn("Connection error", message)

    def test_timeout_classified_and_credential_absent(self):
        exc = self._make_exception("Timeout")
        message = classify_network_exception(exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, message)
        self.assertIn("Timed out", message)

    def test_connect_timeout_subclass_classified_and_credential_absent(self):
        exc = self._make_exception("ConnectTimeout")
        message = classify_network_exception(exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, message)
        self.assertIn("connection", message.lower())

    def test_ssl_error_classified_and_credential_absent(self):
        exc = self._make_exception("SSLError")
        message = classify_network_exception(exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, message)
        self.assertIn("TLS/SSL", message)

    def test_unknown_exception_type_still_produces_safe_generic_message(self):
        exc = self._make_exception("SomeFutureRequestsExceptionType")
        message = classify_network_exception(exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, message)
        self.assertIn("Network error", message)


class NormalizedLLMErrorTests(SimpleTestCase):
    def test_from_exception_sanitizes_message(self):
        exc = ValueError('provider said: {"secret": "leaked-value-should-not-appear"}')
        error = NormalizedLLMError.from_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
        self.assertNotIn("leaked-value-should-not-appear", error.message)
        self.assertEqual(error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    def test_partial_output_received_defaults_false(self):
        error = NormalizedLLMError(category=LLMErrorCategory.TIMEOUT, message="timed out")
        self.assertFalse(error.partial_output_received)

    def test_from_network_exception_never_echoes_raw_exception_text(self):
        exc = ConnectionError(f"...url: /v1/x?key={_SYNTHETIC_CREDENTIAL} ...")
        error = NormalizedLLMError.from_network_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, error.message)
        self.assertEqual(error.category, LLMErrorCategory.PROVIDER_INTERNAL)


class TransientCategoryTests(SimpleTestCase):
    def test_configuration_and_auth_are_never_transient(self):
        self.assertNotIn(LLMErrorCategory.CONFIGURATION, TRANSIENT_ERROR_CATEGORIES)
        self.assertNotIn(LLMErrorCategory.AUTH, TRANSIENT_ERROR_CATEGORIES)
        self.assertNotIn(LLMErrorCategory.SCHEMA_VALIDATION, TRANSIENT_ERROR_CATEGORIES)

    def test_rate_limit_timeout_and_provider_internal_are_transient(self):
        self.assertIn(LLMErrorCategory.RATE_LIMIT, TRANSIENT_ERROR_CATEGORIES)
        self.assertIn(LLMErrorCategory.TIMEOUT, TRANSIENT_ERROR_CATEGORIES)
        self.assertIn(LLMErrorCategory.PROVIDER_INTERNAL, TRANSIENT_ERROR_CATEGORIES)
