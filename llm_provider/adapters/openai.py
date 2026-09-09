"""OpenAI adapter (requirements.md LLM-007): supports strict JSON-schema-constrained output
directly, over REST. No provider SDK dependency -- calls the REST endpoint via `requests`.
"""

from __future__ import annotations

import json
import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..models import ReasoningLevel
from ..schema_translation import to_openai_strict_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def parse_openai_style_chat_completion(response: "requests.Response") -> NormalizedLLMResult:
    """Shared response parsing for OpenAI-compatible `/chat/completions` endpoints (OpenAI,
    NVIDIA NIM, and OpenRouter all use this shape)."""
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
    choice = (payload.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason") or ""
    resolved_model_identifier = payload.get("model") or ""
    try:
        content_str = choice["message"]["content"]
        content_dict = json.loads(content_str)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        return NormalizedLLMResult(
            error=NormalizedLLMError.from_exception(LLMErrorCategory.SCHEMA_VALIDATION, exc),
            usage=usage,
            finish_reason=finish_reason,
        )
    return NormalizedLLMResult(
        content=content_dict,
        usage=usage,
        finish_reason=finish_reason,
        resolved_model_identifier=resolved_model_identifier,
    )


def build_chat_completion_body(request: NormalizedLLMRequest, model_identifier: str, schema: dict) -> dict:
    body: dict = {
        "model": model_identifier,
        "messages": request.messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": request.output_schema.__name__,
                "schema": schema,
                "strict": True,
            },
        },
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens:
        body["max_tokens"] = request.max_output_tokens
    if request.reasoning_level and request.reasoning_level != ReasoningLevel.NONE:
        # OpenAI's own reasoning-effort request parameter; adapter-specific translation of the
        # canonical ReasoningLevel scale (V2-D034) lives only here.
        body["reasoning_effort"] = request.reasoning_level.lower()
    return body


class OpenAIAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
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
                error=NormalizedLLMError.from_network_exception(LLMErrorCategory.TIMEOUT, exc)
            )
        except requests.RequestException as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_network_exception(LLMErrorCategory.PROVIDER_INTERNAL, exc)
            )

        return parse_openai_style_chat_completion(response)
