"""OpenRouter adapter (2026-09-04, OpenRouter provider integration): an OpenAI-compatible
`/chat/completions` endpoint that routes to many different underlying models, each with uneven
feature support -- so, like NVIDIA NIM, this adapter checks `LLMModel.supports_structured_output`/
`supports_reasoning` before assuming either, and additionally asks OpenRouter itself to enforce
that the routed endpoint actually supports whatever was requested via `provider.require_parameters`
(see below).

Reasoning is OpenRouter's own unified `reasoning` request parameter (`{"enabled": true}`), never
NVIDIA's `chat_template_kwargs.enable_thinking` or Gemini's `thinkingConfig.thinkingBudget` --
this adapter is the only place that knows OpenRouter's own representation, mirroring the same
opt-in, per-request translation pattern `nvidia.py` already established: a request that leaves
`reasoning_enabled` unset (`None`) or `False` gets no `reasoning` key in its body at all (never a
blanket per-adapter default), and an explicitly *enabled* request against a model not registered
as `supports_reasoning` is rejected before any HTTP call, rather than sent and hoped for.

Privacy routing (`provider.data_collection`, `provider.require_parameters=true`) is this adapter's
own responsibility, never a pipeline-app concern -- `LLMProvider.data_collection_policy` (a
constrained choices field, never arbitrary JSON) is validated here and the call fails closed
(CONFIGURATION, no HTTP call) if it is ever anything other than one of the two allowed values, e.g.
a row that reached the database bypassing `full_clean()`.

Reuses `openai.py`'s `build_chat_completion_body`/`parse_openai_style_chat_completion` wherever
their OpenAI-compatible semantics genuinely match (message/schema/token-budget shape, response
envelope, usage extraction, status-code classification) -- this file adds only what is genuinely
OpenRouter-specific: attribution headers, the `provider` routing object, `reasoning`, and the
`stream: false` marker OpenRouter's own docs show explicitly.
"""

from __future__ import annotations

import os

import requests

from ..errors import LLMErrorCategory, NormalizedLLMError
from ..models import LLMProvider
from ..schema_translation import to_openai_strict_schema
from ..types import NormalizedLLMRequest, NormalizedLLMResult
from .base import BaseLLMAdapter
from .openai import build_chat_completion_body, parse_openai_style_chat_completion

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Fixed, documented environment-variable names for OpenRouter's optional attribution headers
# (openrouter.ai/docs -- these identify the calling app to OpenRouter, not a credential). Unlike
# `credential_env_var`, these names are not operator-configurable per provider row: OpenRouter
# defines exactly these two headers, so there is nothing to make configurable.
_HTTP_REFERER_ENV_VAR = "OPENROUTER_HTTP_REFERER"
_APP_TITLE_ENV_VAR = "OPENROUTER_APP_TITLE"

_VALID_DATA_COLLECTION_VALUES = {
    LLMProvider.DataCollectionPolicy.DENY,
    LLMProvider.DataCollectionPolicy.ALLOW,
}


class OpenRouterAdapter(BaseLLMAdapter):
    @staticmethod
    def translate_schema(output_schema: type) -> dict:
        # OpenRouter's structured-output contract is the same OpenAI-strict dialect NIM uses.
        return to_openai_strict_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        if not self.llm_model.supports_structured_output:
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"LLMModel '{self.llm_model.model_id}' is not marked as supporting "
                        "structured output; OpenRouter model support is not uniform across the "
                        "endpoints it routes to."
                    ),
                )
            )

        if request.reasoning_enabled and not self.llm_model.supports_reasoning:
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"Reasoning was requested but LLMModel '{self.llm_model.model_id}' is not "
                        "registered as supporting reasoning."
                    ),
                )
            )

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

        data_collection = provider.data_collection_policy
        if data_collection not in _VALID_DATA_COLLECTION_VALUES:
            # Fail closed rather than ever silently weakening the privacy policy or sending an
            # unvalidated value -- this can only happen for a row that bypassed full_clean()
            # (Django's choices constraint is enforced at validation time, not by the DB column).
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"LLMProvider '{provider.name}' has an invalid data_collection_policy "
                        f"({data_collection!r}); must be one of "
                        f"{sorted(_VALID_DATA_COLLECTION_VALUES)}. Refusing to call OpenRouter "
                        "rather than silently weakening the privacy routing policy."
                    ),
                )
            )

        schema = self.translate_schema(request.output_schema)
        body = build_chat_completion_body(request, self.llm_model.model_id, schema)
        body["stream"] = False
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.reasoning_enabled:
            # Only ever sent when explicitly enabled (module docstring) -- never a blanket
            # per-adapter default, and never for a `False`/`None` request.
            body["reasoning"] = {"enabled": True}
        body["provider"] = {
            "require_parameters": True,
            "data_collection": data_collection.lower(),
        }

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        http_referer = os.environ.get(_HTTP_REFERER_ENV_VAR, "")
        if http_referer:
            headers["HTTP-Referer"] = http_referer
        app_title = os.environ.get(_APP_TITLE_ENV_VAR, "")
        if app_title:
            headers["X-OpenRouter-Title"] = app_title

        base_url = provider.base_url or DEFAULT_BASE_URL

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
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
