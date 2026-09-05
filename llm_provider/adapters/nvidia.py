"""NVIDIA NIM adapter (requirements.md Sec 9.3): OpenAI-compatible chat-completions endpoint,
supporting response_format-style strict JSON schema -- but as a self-hosted/managed endpoint,
per-model support is not uniform, so `LLMModel.supports_structured_output` is checked before
assuming it.

Reasoning/sampling translation (operator-decision repair): `request.reasoning_enabled` and
`request.top_p` are generic, typed `NormalizedLLMRequest` options (see `llm_provider/types.py`) --
this adapter is the only place that knows NVIDIA NIM's own provider-specific representation of
them (`chat_template_kwargs.enable_thinking`, `top_p`). Both are opt-in: a request that leaves
either field `None` gets no such key in its body at all, so this never changes behavior for a
call that doesn't ask for it, and it never hardcodes "MEMORY_BUILD" or any other stage name here
-- the *caller* (e.g. `candidate_memory.services.extraction`) decides, per stage and per resolved
model, whether to set these fields; this adapter only ever faithfully translates what it's given.
Never touches the shared `build_chat_completion_body()` in `openai.py`, so OpenAI's request body
is completely unaffected by either field.
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
        # NIM is OpenAI-compatible for structured output (requirements.md Sec 9.3).
        return to_openai_strict_schema(output_schema)

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        if not self.llm_model.supports_structured_output:
            return NormalizedLLMResult(
                error=NormalizedLLMError(
                    category=LLMErrorCategory.CONFIGURATION,
                    message=(
                        f"LLMModel '{self.llm_model.model_id}' is not marked as supporting "
                        "structured output; NIM model support is not uniform across models."
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

        schema = self.translate_schema(request.output_schema)
        body = build_chat_completion_body(request, self.llm_model.model_id, schema)
        # NVIDIA-specific translation of generic, opt-in normalized options -- applied only when
        # the caller explicitly set them (never a blanket per-adapter default); see module
        # docstring. `build_chat_completion_body` itself (shared with OpenAI) is never touched.
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.reasoning_enabled is not None:
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = request.reasoning_enabled
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
