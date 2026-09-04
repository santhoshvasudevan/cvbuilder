"""Regression coverage for the test-suite-wide network guard (`llm_provider/testing.py`,
Gate-1 preparation 2026-09-04): proves the guard actually protects the real adapter call path --
not just that `requests.get`/`requests.post` are patched in the abstract -- and that it never
interferes with `FakeAdapter`-based testing, which the entire rest of the suite depends on."""

from __future__ import annotations

from unittest import mock

import requests
from django.test import TestCase
from pydantic import BaseModel

from ..adapters.fake import FakeAdapter
from ..adapters.gemini import GeminiAdapter
from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..adapters.openrouter import OpenRouterAdapter
from ..models import LLMProvider
from ..testing import BlockedNetworkCallError
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider


class _Output(BaseModel):
    acknowledged: bool


def _request() -> NormalizedLLMRequest:
    return NormalizedLLMRequest(
        stage="MEMORY_BUILD",
        messages=[{"role": "user", "content": "hello"}],
        output_schema=_Output,
    )


class NetworkGuardIsActiveDuringTestsTests(TestCase):
    """Sanity check on the guard itself, independent of any adapter."""

    def test_requests_get_is_blocked(self):
        with self.assertRaises(BlockedNetworkCallError):
            requests.get("https://example.invalid")

    def test_requests_post_is_blocked(self):
        with self.assertRaises(BlockedNetworkCallError):
            requests.post("https://example.invalid")

    def test_a_local_mock_patch_still_nests_correctly_on_top_of_the_guard(self):
        """`job_intake/tests/test_fetch.py`'s pattern -- a test-level `mock.patch("requests.get")`
        -- must still work exactly as before; the runner-level guard must not shadow it."""
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = "stub-response"
            self.assertEqual(requests.get("https://example.invalid"), "stub-response")
        # Guard is back in place once the local patch's `with` block exits.
        with self.assertRaises(BlockedNetworkCallError):
            requests.get("https://example.invalid")


class RealAdapterCallPathIsBlockedTests(TestCase):
    """Constructs each *real* adapter (never `FakeAdapter`) with a dummy-but-present credential --
    exactly the state a developer's shell could be in if a real API key were exported -- and
    proves that actually calling `.generate()` still never reaches the network: it fails with
    `BlockedNetworkCallError` instead, precisely because there is no other path to a live host
    left un-guarded."""

    def _model_with_dummy_credential(self, provider_type, env_var="DUMMY_TEST_CREDENTIAL"):
        provider = make_provider(provider_type=provider_type, credential_env_var=env_var)
        return make_model(provider=provider, model_id="whatever-model")

    def test_nvidia_nim_adapter_generate_never_reaches_the_network(self):
        model = self._model_with_dummy_credential(LLMProvider.ProviderType.NVIDIA_NIM)
        with mock.patch.dict("os.environ", {"DUMMY_TEST_CREDENTIAL": "not-a-real-key"}):
            with self.assertRaises(BlockedNetworkCallError):
                NvidiaNimAdapter(model).generate(_request())

    def test_openai_adapter_generate_never_reaches_the_network(self):
        model = self._model_with_dummy_credential(LLMProvider.ProviderType.OPENAI)
        with mock.patch.dict("os.environ", {"DUMMY_TEST_CREDENTIAL": "not-a-real-key"}):
            with self.assertRaises(BlockedNetworkCallError):
                OpenAIAdapter(model).generate(_request())

    def test_gemini_adapter_generate_never_reaches_the_network(self):
        model = self._model_with_dummy_credential(LLMProvider.ProviderType.GEMINI)
        with mock.patch.dict("os.environ", {"DUMMY_TEST_CREDENTIAL": "not-a-real-key"}):
            with self.assertRaises(BlockedNetworkCallError):
                GeminiAdapter(model).generate(_request())

    def test_openrouter_adapter_generate_never_reaches_the_network(self):
        model = self._model_with_dummy_credential(LLMProvider.ProviderType.OPENROUTER)
        with mock.patch.dict("os.environ", {"DUMMY_TEST_CREDENTIAL": "not-a-real-key"}):
            with self.assertRaises(BlockedNetworkCallError):
                OpenRouterAdapter(model).generate(_request())


class FakeAdapterIsUnaffectedByTheGuardTests(TestCase):
    def test_fake_adapter_still_works_normally(self):
        model = make_model(model_id="fake-model")
        result = FakeAdapter(model, fixed_response={"acknowledged": True}).generate(_request())
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.acknowledged, True)
