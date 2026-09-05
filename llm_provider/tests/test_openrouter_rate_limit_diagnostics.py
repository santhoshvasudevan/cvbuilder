"""OpenRouter 429 diagnostic-metadata retention (2026-09-05, D-029): `Retry-After`, the
`X-RateLimit-*` headers when present, and upstream-vs-OpenRouter attribution when the error body's
own `error.metadata` names an upstream provider. Scoped to `OpenRouterAdapter` only -- the shared
OpenAI-compatible 429 branch used by OpenAI/NVIDIA NIM (`parse_openai_style_chat_completion`) is
untouched by this feature.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.openai import parse_openai_style_chat_completion
from ..adapters.openrouter import OpenRouterAdapter, parse_openrouter_rate_limit
from ..errors import LLMErrorCategory
from ..models import LLMCallLog, LLMProvider, StageModelAssignment
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_429_KEY"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


class _FakeResponse:
    def __init__(self, status_code=429, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _request() -> NormalizedLLMRequest:
    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=64,
    )


class HeaderParsingTests(TestCase):
    def test_retry_after_and_rate_limit_headers_are_retained(self):
        response = _FakeResponse(
            headers={
                "Retry-After": "42",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1735689600000",
            }
        )
        result = parse_openrouter_rate_limit(response)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)
        diagnostics = result.error.rate_limit_diagnostics
        self.assertEqual(diagnostics["retry_after_seconds"], 42)
        self.assertEqual(diagnostics["limit"], 100)
        self.assertEqual(diagnostics["remaining"], 0)
        self.assertEqual(diagnostics["reset"], "1735689600000")
        self.assertEqual(diagnostics["source"], "unknown")
        self.assertNotIn("upstream_provider", diagnostics)

    def test_missing_headers_produce_no_corresponding_keys(self):
        response = _FakeResponse(headers={})
        result = parse_openrouter_rate_limit(response)
        diagnostics = result.error.rate_limit_diagnostics
        self.assertNotIn("retry_after_seconds", diagnostics)
        self.assertNotIn("limit", diagnostics)
        self.assertNotIn("remaining", diagnostics)
        self.assertNotIn("reset", diagnostics)
        self.assertEqual(diagnostics["source"], "unknown")

    def test_malformed_retry_after_is_dropped_not_guessed(self):
        response = _FakeResponse(headers={"Retry-After": "not-a-number"})
        result = parse_openrouter_rate_limit(response)
        self.assertNotIn("retry_after_seconds", result.error.rate_limit_diagnostics)

    def test_negative_retry_after_is_dropped(self):
        response = _FakeResponse(headers={"Retry-After": "-5"})
        result = parse_openrouter_rate_limit(response)
        self.assertNotIn("retry_after_seconds", result.error.rate_limit_diagnostics)

    def test_reset_header_is_reduced_to_digits_only_and_bounded(self):
        response = _FakeResponse(headers={"X-RateLimit-Reset": "abc123<script>xyz" + "9" * 40})
        result = parse_openrouter_rate_limit(response)
        reset = result.error.rate_limit_diagnostics["reset"]
        self.assertTrue(reset.isdigit())
        self.assertLessEqual(len(reset), 20)


class UpstreamAttributionTests(TestCase):
    def test_upstream_provider_name_in_metadata_sets_source_upstream(self):
        response = _FakeResponse(
            payload={
                "error": {
                    "code": 429,
                    "message": "rate limited",
                    "metadata": {"provider_name": "SomeUpstreamProvider"},
                }
            }
        )
        result = parse_openrouter_rate_limit(response)
        diagnostics = result.error.rate_limit_diagnostics
        self.assertEqual(diagnostics["source"], "upstream")
        self.assertEqual(diagnostics["upstream_provider"], "SomeUpstreamProvider")

    def test_no_metadata_leaves_source_unknown_never_guessed(self):
        response = _FakeResponse(payload={"error": {"code": 429, "message": "rate limited"}})
        result = parse_openrouter_rate_limit(response)
        self.assertEqual(result.error.rate_limit_diagnostics["source"], "unknown")
        self.assertNotIn("upstream_provider", result.error.rate_limit_diagnostics)

    def test_non_json_body_still_yields_header_diagnostics_with_unknown_source(self):
        response = _FakeResponse(headers={"Retry-After": "10"})
        response.json = mock.Mock(side_effect=ValueError("no body"))
        result = parse_openrouter_rate_limit(response)
        self.assertEqual(result.error.rate_limit_diagnostics["retry_after_seconds"], 10)
        self.assertEqual(result.error.rate_limit_diagnostics["source"], "unknown")

    def test_upstream_provider_label_is_sanitized_and_bounded(self):
        malicious = "x" * 500
        response = _FakeResponse(
            payload={"error": {"metadata": {"provider_name": malicious}}},
        )
        result = parse_openrouter_rate_limit(response)
        self.assertLessEqual(len(result.error.rate_limit_diagnostics["upstream_provider"]), 100)


class SharedParserUntouchedTests(TestCase):
    """The generic OpenAI-compatible 429 branch (shared with OpenAI/NVIDIA NIM) must remain a
    plain, undecorated RATE_LIMIT error -- this feature is scoped to OpenRouter only."""

    def test_generic_parser_still_returns_no_diagnostics(self):
        response = mock.Mock(status_code=429)
        result = parse_openai_style_chat_completion(response)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)
        self.assertIsNone(result.error.rate_limit_diagnostics)


class EndToEndAdapterAndCallLogTests(TestCase):
    """The diagnostics actually reach the adapter's real 429 code path and get persisted onto the
    `LLMCallLog` row this call writes -- not just the standalone parser function."""

    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter 429 e2e test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(
            provider=self.provider,
            model_id="z-ai/glm-5.2:free",
            supports_structured_output=True,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_429_response_persists_sanitized_diagnostics_on_the_call_log_row(self):
        response = _FakeResponse(
            headers={"Retry-After": "17"},
            payload={"error": {"metadata": {"provider_name": "UpstreamX"}}},
        )
        with mock.patch("requests.post", return_value=response):
            result = OpenRouterAdapter(self.model).generate(_request())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)

        log = LLMCallLog.objects.latest("created_at")
        self.assertEqual(log.error_category, LLMErrorCategory.RATE_LIMIT.value)
        self.assertEqual(log.rate_limit_diagnostics["retry_after_seconds"], 17)
        self.assertEqual(log.rate_limit_diagnostics["source"], "upstream")
        self.assertEqual(log.rate_limit_diagnostics["upstream_provider"], "UpstreamX")

    def test_success_response_leaves_rate_limit_diagnostics_null(self):
        response = _FakeResponse(
            status_code=200,
            payload={
                "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
        with mock.patch("requests.post", return_value=response):
            OpenRouterAdapter(self.model).generate(_request())

        log = LLMCallLog.objects.latest("created_at")
        self.assertIsNone(log.rate_limit_diagnostics)

    def test_no_raw_response_body_or_headers_leak_into_the_call_log(self):
        secret_looking_header_value = "should-never-be-stored-verbatim"
        response = _FakeResponse(
            headers={"Retry-After": "5", "X-Some-Other-Header": secret_looking_header_value},
            payload={"error": {"message": "rate limited", "internal_debug": "leak-candidate"}},
        )
        with mock.patch("requests.post", return_value=response):
            OpenRouterAdapter(self.model).generate(_request())

        log = LLMCallLog.objects.latest("created_at")
        stored = str(log.rate_limit_diagnostics)
        self.assertNotIn(secret_looking_header_value, stored)
        self.assertNotIn("leak-candidate", stored)
