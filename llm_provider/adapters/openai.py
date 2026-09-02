"""OpenAI adapter (requirements.md Sec 9.3): supports strict JSON-schema-constrained output
directly, over REST.
"""

from __future__ import annotations

import json
import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..schema_translation import to_openai_strict_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def parse_openai_style_chat_completion(response: "requests.Response") -> NormalizedLLMResult:
    """Shared response parsing for OpenAI-compatible `/chat/completions` endpoints (OpenAI and
    NVIDIA NIM both use this shape)."""
    if response.status_code in (401, 403):
        return NormalizedLLMResult(
            error=NormalizedLLMError(category=LLMErrorCategory.AUTH, message="Authentication failed.")
        )
    if response.status_code == 429:
        return NormalizedLLMResult(
            error=NormalizedLLMError(category=LLMErrorCategory.RATE_LIMIT, message="Rate limited.")
        )
    if response.status_code >= 500:
        return NormalizedLLMResult(
            error=NormalizedLLMError(
                category=LLMErrorCategory.PROVIDER_INTERNAL,
                message=f"Provider returned {response.status_code}.",
            )
        )
    if response.status_code >= 400:
        return NormalizedLLMResult(
            error=NormalizedLLMError(
                category=LLMErrorCategory.SCHEMA_VALIDATION,
                message=f"Provider rejected the request ({response.status_code}).",
            )
        )

    payload = response.json()
    usage_raw = payload.get("usage") or {}
    usage = TokenUsage(
        input_tokens=usage_raw.get("prompt_tokens"),
        cached_input_tokens=(usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens"),
        output_tokens=usage_raw.get("completion_tokens"),
        total_tokens=usage_raw.get("total_tokens"),
    )
    try:
        content_str = payload["choices"][0]["message"]["content"]
        content_dict = json.loads(content_str)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        return NormalizedLLMResult(
            error=NormalizedLLMError.from_exception(LLMErrorCategory.SCHEMA_VALIDATION, exc),
            usage=usage,
        )
    return NormalizedLLMResult(content=content_dict, usage=usage)


def build_chat_completion_body(
    request: NormalizedLLMRequest, model_id: str, schema: dict
) -> dict:
    body: dict = {
        "model": model_id,
        "messages": request.messages,
        "temperature": request.temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": request.output_schema.__name__,
                "schema": schema,
                "strict": True,
            },
        },
    }
    if request.max_output_tokens:
        body["max_tokens"] = request.max_output_tokens
    return body


class OpenAIAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
        return to_openai_strict_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        provider = self.llm_model.provider
        api_key = os.environ.get(provider.credential_env_var, "") if provider.credential_env_var else ""
        if not api_key:
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"No credential found in environment variable "
                        f"'{provider.credential_env_var or '(not configured)'}'."
                    ),
                )
            )

        schema = self.translate_schema(request.output_schema)
        body = build_chat_completion_body(request, self.llm_model.model_id, schema)
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
