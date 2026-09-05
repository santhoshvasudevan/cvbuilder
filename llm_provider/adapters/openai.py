"""OpenAI adapter (requirements.md Sec 9.3): supports strict JSON-schema-constrained output
directly, over REST.
"""

from __future__ import annotations

import json
import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..schema_translation import OpenAIStrictSchemaContractError, to_openai_strict_schema
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

    if finish_reason == "length":
        # A `length` finish reason unconditionally means the provider stopped generating because
        # it hit the configured output-token limit -- reported the same way regardless of what
        # `message.content` happens to contain (missing/None/empty/non-string/malformed JSON, or
        # even a string that happens to parse as valid JSON): truncated output may be an
        # incomplete answer, so it must never be accepted as a genuine final answer merely because
        # its partial text parses (D-026 correction, 2026-09-04 -- a first pass at this fix let a
        # `length` response with parseable JSON content through as a success, which is exactly the
        # "accept truncated content because it happens to parse" bug this invariant forbids).
        # Always a configuration problem (the token limit for this model/prompt), never a
        # malformed request and never transient -- CONFIGURATION is deliberately excluded from
        # TRANSIENT_ERROR_CATEGORIES, so callers must never retry it blindly.
        return NormalizedLLMResult(
            error=NormalizedLLMError(
                category=LLMErrorCategory.CONFIGURATION,
                message=(
                    "Provider truncated output at the configured token limit (finish_reason="
                    "length) -- any content present may be incomplete and is never accepted as a "
                    "final answer. Raise max_output_tokens or reduce reasoning for this model -- "
                    "not a transient failure, do not retry with identical settings."
                ),
            ),
            usage=usage,
        )

    if not isinstance(content_str, str) or not content_str.strip():
        # Absent/None/non-string/empty/whitespace-only content with a non-`length` finish reason
        # is a malformed/invalid provider response, not a token-budget problem -- the same
        # non-transient category used for unparseable content below, never retried
        # (SCHEMA_VALIDATION is not in TRANSIENT_ERROR_CATEGORIES).
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
    request: NormalizedLLMRequest,
    model_id: str,
    schema: dict,
    *,
    token_limit_key: str = "max_tokens",
    include_temperature: bool = True,
) -> dict:
    """Shared by OpenAI/NVIDIA NIM/OpenRouter (all OpenAI-compatible `/chat/completions`). The two
    keyword-only overrides (2026-09-05, GPT-5 compatibility) exist for exactly one caller --
    `OpenAIAdapter._call_once` on a `supports_reasoning` model -- and default to the original,
    unconditional behavior every existing caller (NVIDIA NIM, OpenRouter, and OpenAI on a
    non-reasoning model) still relies on unchanged: always `max_tokens`, always `temperature`.
    OpenAI's reasoning-model family (o1/o3/gpt-5, ...) rejects both of those on the Chat
    Completions API -- `max_tokens` must be `max_completion_tokens`, and `temperature` must be
    omitted entirely (only the provider's own default is accepted; sending any explicit value,
    including this codebase's own `NormalizedLLMRequest.temperature=0.0` default, is a 400).
    """
    body: dict = {
        "model": model_id,
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
    if include_temperature:
        body["temperature"] = request.temperature
    if request.max_output_tokens:
        body[token_limit_key] = request.max_output_tokens
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

        is_reasoning_model = self.llm_model.supports_reasoning
        if request.reasoning_effort is not None and not is_reasoning_model:
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"reasoning_effort was requested but LLMModel '{self.llm_model.model_id}' "
                        "is not registered as supporting reasoning."
                    ),
                )
            )

        try:
            schema = self.translate_schema(request.output_schema)
        except OpenAIStrictSchemaContractError as exc:
            # D-032: a locally-detected incomplete strict-mode schema is a request-contract
            # configuration problem, caught before any HTTP call -- never sent and never retried.
            return NormalizedLLMResult(
                error=NormalizedLLMError(category=LLMErrorCategory.CONFIGURATION, message=str(exc))
            )
        # GPT-5-family compatibility (2026-09-05, D-029/Phase F): a reasoning-capable OpenAI model
        # (o1/o3/gpt-5, ...) requires `max_completion_tokens` in place of `max_tokens` and rejects
        # any explicit `temperature` value -- see `build_chat_completion_body`'s own docstring.
        # Every non-reasoning model's request body is byte-for-byte unchanged by this branch.
        body = build_chat_completion_body(
            request,
            self.llm_model.model_id,
            schema,
            token_limit_key="max_completion_tokens" if is_reasoning_model else "max_tokens",
            include_temperature=not is_reasoning_model,
        )
        if is_reasoning_model and request.reasoning_effort is not None:
            # OpenAI's own top-level Chat Completions field name -- never nested, unlike NVIDIA's
            # `chat_template_kwargs.enable_thinking` or Gemini's `thinkingConfig`. Sent only when
            # explicitly requested by the caller (never a blanket per-adapter default), matching
            # the same opt-in translation pattern `nvidia.py`/`openrouter.py` already use for their
            # own reasoning parameters.
            body["reasoning_effort"] = request.reasoning_effort
        base_url = provider.base_url or DEFAULT_BASE_URL

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=self.request_timeout,
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
