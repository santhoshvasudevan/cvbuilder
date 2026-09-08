"""Gemini adapter (requirements.md LLM-007).

Pinned to REST transport, never gRPC. Uses the reduced schema dialect (no `$ref`/`$defs`, no
`enum` constraints -- see `schema_translation.to_gemini_schema`); enum-typed fields are
re-validated in Python by `BaseLLMAdapter.generate()` after the response is parsed.
"""

from __future__ import annotations

import json
import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..models import ReasoningLevel
from ..schema_translation import to_gemini_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Gemini has no direct equivalent of the canonical NONE/LOW/MEDIUM/HIGH/XHIGH scale (V2-D034) --
# it accepts a numeric "thinking budget". This adapter-local mapping is the one place that
# translation happens; it is deliberately conservative and never sends a value for NONE.
_THINKING_BUDGET_BY_REASONING_LEVEL = {
    ReasoningLevel.LOW: 1024,
    ReasoningLevel.MEDIUM: 8192,
    ReasoningLevel.HIGH: 24576,
    ReasoningLevel.XHIGH: 32768,
}


def _to_gemini_contents(messages: list[dict[str, str]]) -> list[dict]:
    return [
        {
            "role": "model" if message.get("role") == "assistant" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in messages
    ]


class GeminiAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
        return to_gemini_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        provider = self.llm_model.provider
        api_key = os.environ.get(provider.credential_env_variable, "")

        schema = self.translate_schema(request.output_schema)
        generation_config: dict = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_output_tokens:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        thinking_budget = _THINKING_BUDGET_BY_REASONING_LEVEL.get(request.reasoning_level)
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

        base_url = provider.base_url or DEFAULT_BASE_URL
        url = f"{base_url}/models/{self.llm_model.model_identifier}:generateContent?key={api_key}"

        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "contents": _to_gemini_contents(request.messages),
                    "generationConfig": generation_config,
                },
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

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: "requests.Response") -> NormalizedLLMResult:
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
        usage_raw = payload.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=usage_raw.get("promptTokenCount"),
            cached_input_tokens=usage_raw.get("cachedContentTokenCount"),
            output_tokens=usage_raw.get("candidatesTokenCount"),
            total_tokens=usage_raw.get("totalTokenCount"),
        )
        candidate = (payload.get("candidates") or [{}])[0]
        finish_reason = candidate.get("finishReason") or ""
        try:
            text = candidate["content"]["parts"][0]["text"]
            content_dict = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_exception(LLMErrorCategory.SCHEMA_VALIDATION, exc),
                usage=usage,
                finish_reason=finish_reason,
            )
        return NormalizedLLMResult(content=content_dict, usage=usage, finish_reason=finish_reason)
