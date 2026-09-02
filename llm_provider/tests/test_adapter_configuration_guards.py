"""Configuration-guard paths that must short-circuit before any network call is attempted --
so these are safe to run in the deterministic suite with no credentials and no mocking."""

from typing import Literal

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.gemini import GeminiAdapter
from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..models import LLMProvider, StageModelAssignment
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"]


def _request():
    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.MEMORY_BUILD,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
    )


class MissingCredentialTests(TestCase):
    def test_openai_adapter_reports_configuration_error_when_credential_missing(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI, credential_env_var="DOES_NOT_EXIST_ENV_VAR"
        )
        model = make_model(provider=provider)
        result = OpenAIAdapter(model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")

    def test_gemini_adapter_reports_configuration_error_when_credential_missing(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.GEMINI, credential_env_var="DOES_NOT_EXIST_ENV_VAR"
        )
        model = make_model(provider=provider)
        result = GeminiAdapter(model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")

    def test_nvidia_adapter_reports_configuration_error_when_credential_missing(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM, credential_env_var="DOES_NOT_EXIST_ENV_VAR"
        )
        model = make_model(provider=provider, supports_structured_output=True)
        result = NvidiaNimAdapter(model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")


class NvidiaCapabilityGuardTests(TestCase):
    def test_reports_configuration_error_when_model_not_marked_structured_output_capable(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM, credential_env_var="NVIDIA_NIM_API_KEY"
        )
        model = make_model(provider=provider, supports_structured_output=False)

        result = NvidiaNimAdapter(model).generate(_request())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")
        self.assertIn("not marked as supporting structured output", result.error.message)
