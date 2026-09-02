"""Operator-decision repair: NVIDIA-specific reasoning/sampling translation. All network calls
are mocked (`requests.post`) -- these are deterministic, no live credential/network required.
Credentials used here are dummy values that never leave the test process."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from ..adapters.gemini import GeminiAdapter
from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..models import LLMProvider
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def json(self):
        return self._payload


def _request(**overrides):
    from typing import Literal

    from pydantic import BaseModel

    class _TinyOutput(BaseModel):
        ok: Literal["yes", "no"] = "yes"

    defaults = dict(
        stage="MEMORY_BUILD",
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=4096,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class NvidiaReasoningTranslationTests(TestCase):
    def setUp(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA test",
            credential_env_var="TEST_NVIDIA_KEY",
        )
        self.model = make_model(provider=provider, model_id="nvidia/nemotron-3-super-120b-a12b")
        self.env_patch = mock.patch.dict("os.environ", {"TEST_NVIDIA_KEY": "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_reasoning_enabled_false_translates_to_chat_template_kwargs(self):
        request = _request(reasoning_enabled=False)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_top_p_reaches_the_request_body(self):
        request = _request(top_p=0.95)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["top_p"], 0.95)

    def test_temperature_reaches_the_request_body(self):
        request = _request(temperature=1.0)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["temperature"], 1.0)

    def test_max_output_tokens_still_reaches_body_as_max_tokens(self):
        request = _request(max_output_tokens=4096)
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["max_tokens"], 4096)

    def test_reasoning_enabled_none_omits_chat_template_kwargs_entirely(self):
        """A request that doesn't opt in gets no such key at all -- proves this is never a
        blanket per-adapter default; it's purely request-driven."""
        request = _request()  # reasoning_enabled defaults to None
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("chat_template_kwargs", body)

    def test_top_p_none_omits_key_entirely(self):
        request = _request()  # top_p defaults to None
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("top_p", body)

    def test_another_nvidia_model_without_the_opt_in_keeps_default_temperature(self):
        """A different NVIDIA model that never sets these fields must behave exactly as before --
        proves this is not a blanket-NVIDIA change, only an opt-in per request."""
        other_model = make_model(
            provider=self.model.provider, model_id="some-other-nvidia-model"
        )
        request = _request()  # plain defaults: temperature=0.0, no top_p/reasoning key
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(other_model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("top_p", body)
        self.assertNotIn("chat_template_kwargs", body)


class NoLeakToOtherAdaptersTests(TestCase):
    """Provider-specific parameters set on a request must never leak into OpenAI's or Gemini's
    request bodies -- those adapters simply don't read `reasoning_enabled`/`top_p` at all."""

    def test_openai_adapter_never_receives_top_p_or_chat_template_kwargs(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI, name="OpenAI test",
            credential_env_var="TEST_OPENAI_KEY",
        )
        model = make_model(provider=provider, model_id="gpt-4o-mini")
        request = _request(top_p=0.95, reasoning_enabled=False)
        with mock.patch.dict("os.environ", {"TEST_OPENAI_KEY": "dummy-test-value"}):
            with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
                OpenAIAdapter(model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("top_p", body)
        self.assertNotIn("chat_template_kwargs", body)
        self.assertNotIn("enable_thinking", str(body))

    def test_gemini_adapter_never_receives_top_p_or_chat_template_kwargs(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.GEMINI, name="Gemini test",
            credential_env_var="TEST_GEMINI_KEY",
        )
        model = make_model(provider=provider, model_id="gemini-1.5-flash")
        request = _request(top_p=0.95, reasoning_enabled=False)
        with mock.patch.dict("os.environ", {"TEST_GEMINI_KEY": "dummy-test-value"}):
            with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
                GeminiAdapter(model).generate(request)
        sent_json = post_mock.call_args.kwargs["json"]
        self.assertNotIn("top_p", sent_json.get("generationConfig", {}))
        self.assertNotIn("chat_template_kwargs", str(sent_json))
        self.assertNotIn("enable_thinking", str(sent_json))
