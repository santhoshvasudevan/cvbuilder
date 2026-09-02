from django.test import SimpleTestCase

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..retry import RetryPolicy, execute_with_retry, is_retryable
from ..types import NormalizedLLMResult


def _error_result(category, partial_output_received=False):
    return NormalizedLLMResult(
        error=NormalizedLLMError(
            category=category, message="x", partial_output_received=partial_output_received
        )
    )


class IsRetryableTests(SimpleTestCase):
    def test_success_is_never_retryable(self):
        self.assertFalse(is_retryable(NormalizedLLMResult(content={"ok": True})))

    def test_transient_categories_are_retryable(self):
        for category in (
            LLMErrorCategory.RATE_LIMIT,
            LLMErrorCategory.TIMEOUT,
            LLMErrorCategory.PROVIDER_INTERNAL,
        ):
            with self.subTest(category=category):
                self.assertTrue(is_retryable(_error_result(category)))

    def test_non_transient_categories_are_not_retryable(self):
        for category in (
            LLMErrorCategory.CONFIGURATION,
            LLMErrorCategory.AUTH,
            LLMErrorCategory.SCHEMA_VALIDATION,
        ):
            with self.subTest(category=category):
                self.assertFalse(is_retryable(_error_result(category)))

    def test_partial_output_received_is_never_retryable_even_if_transient(self):
        result = _error_result(LLMErrorCategory.RATE_LIMIT, partial_output_received=True)
        self.assertFalse(is_retryable(result))


class ExecuteWithRetryTests(SimpleTestCase):
    def setUp(self):
        self.sleeps: list[float] = []

    def _sleep_fn(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def test_succeeds_first_try_no_retry(self):
        calls = []

        def call_once():
            calls.append(1)
            return NormalizedLLMResult(content={"ok": True})

        result = execute_with_retry(call_once, sleep_fn=self._sleep_fn)

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(self.sleeps, [])

    def test_retries_transient_error_until_success(self):
        attempts = {"n": 0}

        def call_once():
            attempts["n"] += 1
            if attempts["n"] < 3:
                return _error_result(LLMErrorCategory.RATE_LIMIT)
            return NormalizedLLMResult(content={"ok": True})

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=5), sleep_fn=self._sleep_fn)

        self.assertEqual(attempts["n"], 3)
        self.assertEqual(result.retry_count, 2)
        self.assertFalse(result.is_error)
        self.assertEqual(len(self.sleeps), 2)

    def test_stops_at_max_attempts_and_returns_last_error(self):
        calls = []

        def call_once():
            calls.append(1)
            return _error_result(LLMErrorCategory.PROVIDER_INTERNAL)

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=3), sleep_fn=self._sleep_fn)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result.retry_count, 2)
        self.assertTrue(result.is_error)

    def test_non_retryable_error_stops_immediately(self):
        calls = []

        def call_once():
            calls.append(1)
            return _error_result(LLMErrorCategory.AUTH)

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=5), sleep_fn=self._sleep_fn)

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(self.sleeps, [])

    def test_never_retries_after_partial_stream_even_though_transient(self):
        calls = []

        def call_once():
            calls.append(1)
            return _error_result(LLMErrorCategory.RATE_LIMIT, partial_output_received=True)

        result = execute_with_retry(call_once, policy=RetryPolicy(max_attempts=5), sleep_fn=self._sleep_fn)

        self.assertEqual(len(calls), 1, "must not retry once any output has streamed")
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(self.sleeps, [])
