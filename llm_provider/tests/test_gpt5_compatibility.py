"""Exact `gpt-5` Chat Completions request-contract compatibility (2026-09-05, Phase F -- read-only
audit against current official OpenAI documentation, no live provider call in this task).

Confirmed against OpenAI's own docs (`developers.openai.com/api/docs/models/gpt-5` and the
reasoning-models guide): `gpt-5` supports `v1/chat/completions`, `structured_outputs`, and
`reasoning.effort` in {minimal, low, medium, high}; it has a 400,000-token context window and a
128,000 max-output-tokens ceiling. As a reasoning-family model, it rejects the pre-existing shared
request-body shape on two counts: `max_tokens` is unsupported (`max_completion_tokens` is
required), and `temperature` is unsupported at any value other than the provider's own default
(this codebase's `NormalizedLLMRequest.temperature` defaults to `0.0`, which would 400 unmodified).
`top_p` was never sent by this adapter at all, so no fix was needed there. These tests exercise the
exact fix in `OpenAIAdapter`/`build_chat_completion_body` -- all HTTP calls are mocked; no network
access, no credential, no live call.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.openai import OpenAIAdapter, build_chat_completion_body
from ..errors import LLMErrorCategory
from ..models import LLMProvider, StageModelAssignment
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_GPT5_KEY"
_GPT5_MODEL_ID = "gpt-5"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


def _request(**overrides) -> NormalizedLLMRequest:
    defaults = dict(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=2048,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = (
            payload
            if payload is not None
            else {
                "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
            }
        )

    def json(self):
        return self._payload


class _GPT5TestCase(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI GPT-5 readiness test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        # Exact intended future registry metadata (Phase F): structured output required, 128,000
        # max output tokens (the documented ceiling), reasoning supported.
        self.model = make_model(
            provider=self.provider,
            model_id=_GPT5_MODEL_ID,
            supports_structured_output=True,
            supports_streaming=False,
            supports_reasoning=True,
            max_output_tokens=128_000,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)


class RequestBodyShapeTests(_GPT5TestCase):
    def test_gpt5_request_uses_max_completion_tokens_never_max_tokens(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request(max_output_tokens=8192))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["max_completion_tokens"], 8192)
        self.assertNotIn("max_tokens", body)

    def test_gpt5_request_omits_temperature_entirely(self):
        """The codebase's own `NormalizedLLMRequest.temperature` default is 0.0 -- gpt-5 rejects
        any explicit temperature other than its own default (1), so the key must be absent, never
        sent as 0.0 and never silently rewritten to 1."""
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request(temperature=0.0))
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("temperature", body)

    def test_gpt5_request_still_uses_strict_json_schema_structured_output(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_gpt5_model_slug_sent_exactly_never_substituted(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gpt-5")

    def test_reasoning_effort_is_sent_only_when_explicitly_requested(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request(reasoning_effort="low"))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning_effort"], "low")

    def test_reasoning_effort_absent_by_default_matching_unset_stage_setting(self):
        """Phase F's intended registry row explicitly leaves the stage reasoning setting unset --
        confirm that produces no `reasoning_effort` key at all, not a default value."""
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("reasoning_effort", body)

    def test_reasoning_effort_on_a_non_reasoning_model_fails_closed_before_any_http_call(self):
        non_reasoning_model = make_model(
            provider=self.provider,
            model_id="gpt-4o-mini-test",
            supports_structured_output=True,
            supports_reasoning=False,
        )
        with mock.patch("requests.post") as post_mock:
            result = OpenAIAdapter(non_reasoning_model).generate(_request(reasoning_effort="low"))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        post_mock.assert_not_called()


class NonReasoningModelUnaffectedTests(TestCase):
    """Regression guard: a non-reasoning OpenAI model (e.g. gpt-4o-class) must keep the exact
    pre-existing request shape -- `max_tokens` present, `temperature` present -- since this is the
    contract NVIDIA NIM and OpenRouter (which reuse the same shared `build_chat_completion_body`)
    still depend on unconditionally."""

    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI non-reasoning test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(
            provider=self.provider,
            model_id="gpt-4o-mini",
            supports_structured_output=True,
            supports_reasoning=False,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_non_reasoning_model_still_sends_max_tokens_and_temperature(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(_request(max_output_tokens=4096, temperature=0.0))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("max_completion_tokens", body)

    def test_shared_builder_default_arguments_are_unchanged_for_direct_callers(self):
        """NVIDIA NIM's and OpenRouter's adapters call `build_chat_completion_body` with no
        keyword overrides at all -- confirm the defaults still produce the original shape."""
        request = _request(max_output_tokens=1234, temperature=0.5)
        body = build_chat_completion_body(request, "some-model", schema={})
        self.assertEqual(body["max_tokens"], 1234)
        self.assertEqual(body["temperature"], 0.5)
        self.assertNotIn("max_completion_tokens", body)


class TruncationAndUsageParsingUnaffectedTests(_GPT5TestCase):
    """`finish_reason=length`/null-content handling (D-026) and usage parsing are provider- and
    model-agnostic in the shared parser -- confirm they still behave correctly for a `gpt-5`-slugged
    model without any adapter-specific change being required."""

    def test_finish_reason_length_is_configuration_not_retried_for_gpt5(self):
        response = _FakeHttpResponse(
            payload={
                "choices": [{"finish_reason": "length", "message": {"content": None}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 128_000, "total_tokens": 128_100},
            }
        )
        with mock.patch("requests.post", return_value=response):
            result = OpenAIAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_usage_including_reasoning_tokens_is_parsed_into_total_output_tokens(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()):
            result = OpenAIAdapter(self.model).generate(_request())
        self.assertFalse(result.is_error)
        self.assertEqual(result.usage.output_tokens, 10)
        self.assertEqual(result.usage.total_tokens, 30)
