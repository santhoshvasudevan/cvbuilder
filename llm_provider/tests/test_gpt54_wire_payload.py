"""Wire-payload coverage for the paid GPT-5.4 model defaults (2026-09-07, D-039): proves the exact
OpenRouter model id and every configured reasoning-effort level in the default matrix reach the
request body unchanged, alongside the pre-existing structured-output/`provider.require_parameters`
contract -- CLAUDE.md's "no unnecessary infrastructure" and "provider-specific request translation
stays inside llm_provider" invariants, exercised against the two real paid model ids this decision
introduces rather than only the generic/free-tier fixtures other test files already cover.

All network calls are mocked (`requests.post`) -- deterministic, no live credential/network
required, matching `test_openrouter_adapter.py`'s own pattern.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.openrouter import OpenRouterAdapter
from ..models import LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.gpt54_defaults import GPT54_MINI_MODEL_ID, GPT54_MODEL_ID
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_KEY"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


class _FakeHttpResponse:
    status_code = 200
    headers: dict = {}

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


class _Gpt54TestCase(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _model(self, model_id: str) -> "LLMModel":  # noqa: F821 - forward ref for readability only
        return make_model(
            provider=self.provider,
            model_id=model_id,
            supports_structured_output=True,
            supports_reasoning=True,
        )


# 1. Exact model id reaches the wire unchanged, for both GPT-5.4 Mini and GPT-5.4.
class ExactModelIdOnTheWireTests(_Gpt54TestCase):
    def test_gpt54_mini_model_id_is_sent_verbatim(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["model"], "openai/gpt-5.4-mini")

    def test_gpt54_model_id_is_sent_verbatim(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["model"], "openai/gpt-5.4")

    def test_model_id_is_never_split_or_double_prefixed(self):
        """Opaque-string requirement (requirements Sec 3): the exact configured id reaches the
        wire with no vendor-segment manipulation -- never 'openai/openai/gpt-5.4', never a bare
        'gpt-5.4' with the 'openai/' OpenRouter routing prefix silently stripped."""
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotEqual(body["model"], "gpt-5.4")
        self.assertNotIn("openai/openai", body["model"])


# 2. Every default-matrix reasoning level (medium, high) round-trips through the unified
#    `reasoning.effort` OpenRouter parameter -- plus the two structurally-supported values the
#    matrix doesn't use today (low, xhigh) and the explicit-suppression value (none).
class ReasoningEffortWirePayloadTests(_Gpt54TestCase):
    def test_medium_effort_is_sent_as_unified_reasoning_effort(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.MEDIUM))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "medium"})

    def test_high_effort_is_sent_as_unified_reasoning_effort(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.HIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "high"})

    def test_low_effort_is_sent_as_unified_reasoning_effort(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.LOW))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "low"})

    def test_xhigh_effort_is_sent_as_unified_reasoning_effort(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.XHIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "xhigh"})

    def test_none_effort_explicitly_suppresses_reasoning_rather_than_omitting_the_key(self):
        """`ReasoningEffort.NONE` ("none") is a deliberate, explicit request -- distinct from
        never setting `reasoning_effort` at all (which omits the key entirely, letting the model
        use its own default reasoning behavior)."""
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.NONE))
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "none"})

    def test_no_reasoning_key_at_all_when_not_set(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("reasoning", body)

    def test_effort_and_boolean_enabled_are_never_both_sent(self):
        """OpenRouter's two reasoning knobs (`reasoning.effort` and `reasoning.enabled`) are
        mutually exclusive on the wire -- a request carrying both (a caller-authoring error no
        production call site actually makes) must still never produce a body with both keys."""
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(
                _request(reasoning_effort=ReasoningEffort.HIGH, reasoning_enabled=True)
            )
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertNotIn("enabled", body["reasoning"])


# 3. Structured-output/require_parameters/no-tools contract, proven against the real GPT-5.4 ids.
class StructuredOutputAndRoutingContractTests(_Gpt54TestCase):
    def test_strict_json_schema_structured_output_is_sent(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_provider_require_parameters_is_true(self):
        model = self._model(GPT54_MINI_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertTrue(body["provider"]["require_parameters"])

    def test_no_tools_or_tool_choice_sent_for_a_non_tool_using_stage(self):
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)

    def test_no_nvidia_or_gemini_specific_parameters_leak_into_the_body(self):
        """Cross-provider parameter leakage would violate CLAUDE.md's "provider-specific request
        translation stays inside llm_provider, in exactly one adapter" invariant -- NVIDIA's
        `chat_template_kwargs` and Gemini's `thinkingConfig`/`generationConfig` must never appear
        on an OpenRouter request body, even when `reasoning_effort` is set (the field both this
        adapter and `nvidia.py`/`gemini.py` read from the same normalized request)."""
        model = self._model(GPT54_MODEL_ID)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(model).generate(_request(reasoning_effort=ReasoningEffort.HIGH))
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("chat_template_kwargs", body)
        self.assertNotIn("thinkingConfig", body)
        self.assertNotIn("generationConfig", body)
