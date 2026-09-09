"""OpenRouter adapter (requirements.md LLM-008): an OpenAI-compatible `/chat/completions`
endpoint that routes to many different underlying models. LLM-008: "OpenRouter is a normal
provider implementation, not special pipeline logic" -- this adapter is exactly one more
`BaseLLMAdapter` subclass, routed and validated identically to every other provider.

Reuses `openai.py`'s `build_chat_completion_body`/`parse_openai_style_chat_completion` wherever
their OpenAI-compatible semantics genuinely match; adds only what is genuinely OpenRouter-
specific: the `stream: false` marker and `require_parameters: true` (OpenRouter's own directive
to fail a request rather than silently route it to an endpoint that cannot honor it) that
OpenRouter's docs show explicitly.
"""

from __future__ import annotations

import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..schema_translation import to_openai_strict_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult
from .base import BaseLLMAdapter
from .openai import build_chat_completion_body, parse_openai_style_chat_completion

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
        # OpenRouter's structured-output contract is the same OpenAI-strict dialect NIM uses.
        return to_openai_strict_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        provider = self.llm_model.provider
        api_key = os.environ.get(provider.credential_env_variable, "")

        schema = self.translate_schema(request.output_schema)
        body = build_chat_completion_body(request, self.llm_model.model_identifier, schema)
        body["stream"] = False
        # Fail the request rather than silently routing to an endpoint that cannot honor the
        # requested parameters (structured output, reasoning) -- OpenRouter's own directive for
        # this, never an application-level fallback.
        body["provider"] = {"require_parameters": True}

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
                error=NormalizedLLMError.from_network_exception(LLMErrorCategory.TIMEOUT, exc)
            )
        except requests.RequestException as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_network_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
            )

        return parse_openai_style_chat_completion(response)
