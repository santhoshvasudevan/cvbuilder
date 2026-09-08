"""NVIDIA NIM adapter (requirements.md LLM-007): OpenAI-compatible chat-completions endpoint.
Per-model structured-output support is not uniform across NIM-hosted models, so
`LLMModel.supports_structured_output` is checked by the shared pre-flight validation gate
(`llm_provider.validation`) before any call is attempted -- this adapter does not re-check it.
"""

from __future__ import annotations

import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..schema_translation import to_openai_strict_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult
from .base import BaseLLMAdapter
from .openai import build_chat_completion_body, parse_openai_style_chat_completion

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaNimAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
        # NIM is OpenAI-compatible for structured output.
        return to_openai_strict_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        provider = self.llm_model.provider
        api_key = os.environ.get(provider.credential_env_variable, "")

        schema = self.translate_schema(request.output_schema)
        body = build_chat_completion_body(request, self.llm_model.model_identifier, schema)
        base_url = provider.base_url or DEFAULT_BASE_URL

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
        except requests.Timeout as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_exception(LLMErrorCategory.TIMEOUT, exc)
            )
        except requests.RequestException as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
            )

        return parse_openai_style_chat_completion(response)
