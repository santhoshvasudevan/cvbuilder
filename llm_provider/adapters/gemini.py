"""Gemini adapter (requirements.md Sec 9.3).

Pinned to REST transport, never gRPC -- gRPC has been observed elsewhere to reject payloads
that succeed over REST with identical content. Uses the reduced schema dialect (no
`$ref`/`$defs`, no `enum` constraints -- see schema_translation.to_gemini_schema); enum-typed
fields are re-validated in Python by BaseLLMAdapter.generate() after the response is parsed.
Thinking-budget/reasoning-effort parameters are passed defensively since they are still
evolving across Gemini model generations.
"""

from __future__ import annotations

import json
import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..schema_translation import to_gemini_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


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
        generation_config: dict = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": request.temperature,
        }
        if request.max_output_tokens:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        if request.reasoning_effort is not None:
            # Never assume a literal zero disables thinking -- pass through whatever the
            # caller explicitly requested rather than guessing a "disabled" sentinel.
            generation_config["thinkingConfig"] = {"thinkingBudget": request.reasoning_effort}

        base_url = provider.base_url or DEFAULT_BASE_URL
        url = f"{base_url}/models/{self.llm_model.model_id}:generateContent?key={api_key}"

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
        if response.status_code in (404, 410):
            # The requested model id is missing/no longer available -- a registry/configuration
            # problem (wrong or stale model_id), not a malformed request body.
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"Provider reports the requested model is not found/no longer available "
                        f"({response.status_code}). Check the registered LLMModel.model_id."
                    ),
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
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            content_dict = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            return NormalizedLLMResult(
                error=NormalizedLLMError.from_exception(LLMErrorCategory.SCHEMA_VALIDATION, exc),
                usage=usage,
            )
        return NormalizedLLMResult(content=content_dict, usage=usage)
