from django.test import SimpleTestCase

from llm_provider.errors import LLMErrorCategory, NormalizedLLMError
from llm_provider.retry import RetryPolicy, execute_with_retry, is_retryable
from llm_provider.types import NormalizedLLMResult


def _error_result(category, partial_output_received=False):
    return NormalizedLLMResult(
        error=NormalizedLLMError(
            category=category, message="x", partial_output_received=partial_output_received
        )
    )


class IsRetryableTests(SimpleTestCase):
    def test_success_is_never_retryable(self):
        self.assertFalse(is_retryable(NormalizedLLMResult(content={})))

    def test_transient_error_is_retryable(self):
        self.assertTrue(is_retryable(_error_result(LLMErrorCategory.TIMEOUT)))

    def test_configuration_error_is_not_retryable(self):
        self.assertFalse(is_retryable(_error_result(LLMErrorCategory.CONFIGURATION)))

    def test_auth_error_is_not_retryable(self):
        self.assertFalse(is_retryable(_error_result(LLMErrorCategory.AUTH)))

    def test_schema_validation_error_is_not_retryable(self):
        self.assertFalse(is_retryable(_error_result(LLMErrorCategory.SCHEMA_VALIDATION)))

    def test_partial_output_received_is_never_retried_even_if_transient(self):
        result = _error_result(LLMErrorCategory.TIMEOUT, partial_output_received=True)
        self.assertFalse(is_retryable(result))


class ExecuteWithRetryTests(SimpleTestCase):
    def test_succeeds_on_first_attempt_without_retry(self):
        calls = []

        def call_once():
            calls.append(1)
            return NormalizedLLMResult(content={"ok": True})

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=3), sleep_fn=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.retry_count, 0)

    def test_retries_transient_error_up_to_max_attempts(self):
        calls = []

        def call_once():
            calls.append(1)
            return _error_result(LLMErrorCategory.TIMEOUT)

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=3), sleep_fn=lambda s: None)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.retry_count, 2)
        self.assertTrue(result.is_error)

    def test_stops_retrying_once_a_later_attempt_succeeds(self):
        attempts = {"n": 0}

        def call_once():
            attempts["n"] += 1
            if attempts["n"] < 2:
                return _error_result(LLMErrorCategory.RATE_LIMIT)
            return NormalizedLLMResult(content={"ok": True})

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=5), sleep_fn=lambda s: None)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(result.retry_count, 1)
        self.assertFalse(result.is_error)

    def test_never_retries_a_non_transient_error(self):
        calls = []

        def call_once():
            calls.append(1)
            return _error_result(LLMErrorCategory.CONFIGURATION)

        execute_with_retry(call_once, policy=RetryPolicy(max_attempts=5), sleep_fn=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_retry_never_changes_which_call_is_made(self):
        # No invisible fallback: `call_once` is the *same* closure every attempt -- retry.py has
        # no parameter or code path that could select a different provider/model mid-retry.
        seen_call_ids = set()

        def call_once():
            seen_call_ids.add(id(call_once))
            return _error_result(LLMErrorCategory.TIMEOUT)

        execute_with_retry(call_once, policy=RetryPolicy(max_attempts=3), sleep_fn=lambda s: None)
        self.assertEqual(len(seen_call_ids), 1)
