"""Wire-payload coverage for the paid GPT-5.4 model defaults, corrected to route through the
**direct OpenAI API** (2026-09-07, D-039 correction): the operator's actual requirement was
`https://api.openai.com/v1` with the `OPENAI_API_KEY` credential, not OpenRouter -- the original
pass's OpenRouter binding was an implementation misunderstanding. Proves the exact direct-OpenAI
model ids reach the wire unchanged, the correct endpoint/credential are used, reasoning effort is
serialized via OpenAI's own top-level `reasoning_effort` Chat Completions field (never OpenRouter's
nested `reasoning.effort` object), and no OpenRouter-only wire artifact (the `provider` routing
object, attribution headers) or other-provider parameter ever appears on a direct-OpenAI request.

All network calls are mocked (`requests.post`) -- deterministic, no live credential/network
required, matching `test_gpt5_compatibility.py`'s own pattern for this exact adapter.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.openai import OpenAIAdapter
from ..models import LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.gpt54_defaults import (
    GPT54_MAX_OUTPUT_TOKENS,
    GPT54_MINI_MODEL_ID,
    GPT54_MODEL_ID,
    OPENAI_CREDENTIAL_ENV_VAR,
    OPENAI_DEFAULT_BASE_URL,
)
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENAI_KEY"
_DUMMY_CREDENTIAL_VALUE = "dummy-test-value"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


class _FakeHttpResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }


def _request(**overrides) -> NormalizedLLMRequest:
    defaults = dict(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=2048,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class _DirectOpenAITestCase(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: _DUMMY_CREDENTIAL_VALUE})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _model(self, model_id: str):
        return make_model(
            provider=self.provider,
            model_id=model_id,
            supports_structured_output=True,
            supports_reasoning=True,
            max_output_tokens=GPT54_MAX_OUTPUT_TOKENS,
        )


# 1. Exact direct-OpenAI model id on the wire, never carrying the OpenRouter routing prefix.
class ExactModelIdOnTheWireTests(_DirectOpenAITestCase):
    def test_gpt54_mini_model_id_is_sent_verbatim(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gpt-5.4-mini")

    def test_gpt54_model_id_is_sent_verbatim(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gpt-5.4")

    def test_model_id_never_carries_the_openrouter_routing_prefix(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("openai/", body["model"])
        self.assertNotIn("openrouter/", body["model"])


# 2. Direct endpoint and credential reference.
class DirectEndpointAndCredentialTests(_DirectOpenAITestCase):
    def test_posts_to_the_direct_openai_endpoint(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        args, _kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://api.openai.com/v1/chat/completions")

    def test_default_base_url_constant_matches_the_adapters_own_default(self):
        from ..adapters.openai import DEFAULT_BASE_URL

        self.assertEqual(OPENAI_DEFAULT_BASE_URL, DEFAULT_BASE_URL)
        self.assertEqual(OPENAI_DEFAULT_BASE_URL, "https://api.openai.com/v1")

    def test_credential_env_var_constant_is_exact(self):
        self.assertEqual(OPENAI_CREDENTIAL_ENV_VAR, "OPENAI_API_KEY")

    def test_credential_is_read_from_the_configured_env_var_never_a_hardcoded_one(self):
        """The dummy credential value is read from the provider row's own `credential_env_var`
        (never the literal `OPENAI_API_KEY` name, never logged/printed) and forwarded as a Bearer
        token -- proving the registry-driven credential *reference* mechanism, not the value
        itself, which this test never asserts against directly."""
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        headers = post_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {_DUMMY_CREDENTIAL_VALUE}")

    def test_missing_credential_fails_closed_before_any_http_call(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: ""}):
            with mock.patch("requests.post") as post_mock:
                result = OpenAIAdapter(model).generate(_request())
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")


# 3. Reasoning effort via OpenAI's own top-level Chat Completions field.
class ReasoningEffortWirePayloadTests(_DirectOpenAITestCase):
    def test_medium_effort_is_sent_as_top_level_reasoning_effort(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.MEDIUM))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning_effort"], "medium")

    def test_high_effort_is_sent_as_top_level_reasoning_effort(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.HIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning_effort"], "high")

    def test_reasoning_effort_is_never_nested_under_a_reasoning_object(self):
        """The OpenRouter-only `{"reasoning": {"effort": ...}}` shape must never appear on a direct
        OpenAI request -- OpenAI's own Chat Completions API uses a flat top-level field."""
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.HIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("reasoning", body)

    def test_no_reasoning_effort_key_at_all_when_not_set(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("reasoning_effort", body)

    def test_reasoning_effort_against_a_non_reasoning_model_fails_closed_before_any_http_call(self):
        non_reasoning_model = make_model(
            provider=self.provider,
            model_id="gpt-5.4-nano-hypothetical",
            supports_structured_output=True,
            supports_reasoning=False,
        )
        with mock.patch("requests.post") as post_mock:
            result = OpenAIAdapter(non_reasoning_model).generate(
                _request(reasoning_effort=ReasoningEffort.HIGH)
            )
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")


# 4. Reasoning-model request-shape compatibility (D-029/Phase F, exercised here against the real
#    GPT-5.4 ids rather than only the pre-existing `gpt-5` fixture).
class ReasoningModelRequestShapeTests(_DirectOpenAITestCase):
    def test_uses_max_completion_tokens_never_max_tokens(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request(max_output_tokens=4096))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", body)

    def test_omits_temperature_entirely(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("temperature", body)


# 5. Structured output, and absence of every OpenRouter-only/other-provider wire artifact.
class StructuredOutputAndNoCrossProviderLeakageTests(_DirectOpenAITestCase):
    def test_strict_json_schema_structured_output_is_sent(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_no_openrouter_provider_routing_object(self):
        """`{"provider": {"require_parameters": true, ...}}` is OpenRouter's own routing directive
        (`llm_provider/adapters/openrouter.py`) -- it must never appear on a direct OpenAI body."""
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("provider", body)

    def test_no_openrouter_attribution_headers(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        headers = post_mock.call_args.kwargs["headers"]
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-OpenRouter-Title", headers)

    def test_no_tools_or_tool_choice_sent_for_a_non_tool_using_stage(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)

    def test_no_nvidia_or_gemini_specific_parameters_leak_into_the_body(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.HIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("chat_template_kwargs", body)
        self.assertNotIn("thinkingConfig", body)
        self.assertNotIn("generationConfig", body)
        self.assertNotIn("stream", body)  # OpenRouter-adapter-only explicit marker
