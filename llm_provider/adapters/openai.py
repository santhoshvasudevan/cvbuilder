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
    if response.status_code == 402:
        # Quota/billing/account restriction -- non-transient (an operator action is required,
        # e.g. adding credit or fixing the account), so this must never be retried.
        return NormalizedLLMResult(
            error=NormalizedLLMError(
                category=LLMErrorCategory.CONFIGURATION,
                message="Provider reports a quota/billing/account restriction (402).",
            )
        )
    if response.status_code == 429:
        return NormalizedLLMResult(
            error=NormalizedLLMError(category=LLMErrorCategory.RATE_LIMIT, message="Rate limited.")
        )
    if response.status_code == 408:
        # Request timeout -- transient, eligible for the existing bounded retry policy (same
        # category the client-side `requests.Timeout` exception already uses below).
        return NormalizedLLMResult(
            error=NormalizedLLMError(category=LLMErrorCategory.TIMEOUT, message="Request timed out (408).")
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
        # problem (wrong or stale model_id), not a malformed request body. Misfiling this as
        # SCHEMA_VALIDATION hides the real, actionable cause.
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
    usage_raw = payload.get("usage") or {}
    usage = TokenUsage(
        input_tokens=usage_raw.get("prompt_tokens"),
        cached_input_tokens=(usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens"),
        output_tokens=usage_raw.get("completion_tokens"),
        total_tokens=usage_raw.get("total_tokens"),
    )

    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        return NormalizedLLMResult(
            error=NormalizedLLMError.from_exception(LLMErrorCategory.SCHEMA_VALIDATION, exc),
            usage=usage,
        )

    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    message = choice.get("message") if isinstance(choice, dict) else None
    # The final answer is read only from `message.content` -- `reasoning`/`reasoning_content`/
    # `reasoning_details` (whatever shape a given provider uses for its chain-of-thought) are
    # never read as a substitute, transport behavior never infers a missing final answer from
    # reasoning content (D-026).
    content_str = message.get("content") if isinstance(message, dict) else None

    if not isinstance(content_str, str) or not content_str.strip():
        if finish_reason == "length":
            # The provider truncated the response at max_output_tokens before any (or enough)
            # visible content was emitted -- observed with reasoning models that can spend the
            # entire budget "thinking" before writing a final answer. This is a configuration
            # problem (the token limit for this model/prompt), not a malformed request and not a
            # transient failure -- CONFIGURATION is deliberately excluded from
            # TRANSIENT_ERROR_CATEGORIES, so callers must never retry it blindly.
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        "Provider truncated output at the configured token limit before "
                        "producing usable final content (finish_reason=length). Raise "
                        "max_output_tokens or reduce reasoning for this model -- not a transient "
                        "failure, do not retry with identical settings."
                    ),
                ),
                usage=usage,
            )
        # Absent/None/non-string/empty/whitespace-only content with any other (or missing)
        # finish_reason is a malformed/invalid provider response, not a token-budget problem --
        # the same non-transient category already used for unparseable content below, never
        # retried (SCHEMA_VALIDATION is not in TRANSIENT_ERROR_CATEGORIES).
        return NormalizedLLMResult(
            error=NormalizedLLMError(
                category=LLMErrorCategory.SCHEMA_VALIDATION,
                message=f"Provider response has no usable final content (finish_reason={finish_reason!r}).",
            ),
            usage=usage,
        )

    try:
        content_dict = json.loads(content_str)
    except json.JSONDecodeError as exc:
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
