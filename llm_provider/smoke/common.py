"""Shared logic for opt-in manual provider smoke verification (docs/IMPLEMENTATION_PLAN.md M2).

These scripts are never invoked by `manage.py test` or any automated/CI path -- they are run
only when the operator explicitly initiates them, and require a real credential in the
environment. A provider with no configured credential is reported as NOT LIVE-VERIFIED rather
than failing. Only sanitized `LLMCallLog` metadata is ever recorded; no raw provider
request/response body is persisted anywhere, including here (this prints to the operator's own
terminal only).
"""

from __future__ import annotations

import os
import sys

from pydantic import BaseModel

from ..adapters import ADAPTER_CLASSES
from ..models import LLMModel, LLMProvider
from ..types import NormalizedLLMRequest


class SmokeTestOutput(BaseModel):
    acknowledged: bool


CREDENTIAL_ENV_VARS = {
    LLMProvider.ProviderType.OPENAI: "OPENAI_API_KEY",
    LLMProvider.ProviderType.NVIDIA_NIM: "NVIDIA_NIM_API_KEY",
    LLMProvider.ProviderType.GEMINI: "GEMINI_API_KEY",
}

DEFAULT_MODEL_IDS = {
    LLMProvider.ProviderType.OPENAI: "gpt-4o-mini",
    LLMProvider.ProviderType.NVIDIA_NIM: "meta/llama-3.1-8b-instruct",
    LLMProvider.ProviderType.GEMINI: "gemini-1.5-flash",
}


def run_smoke_test(provider_type: str, model_id: str | None = None) -> None:
    env_var = CREDENTIAL_ENV_VARS[provider_type]
    if not os.environ.get(env_var):
        print(f"{provider_type}: NOT LIVE-VERIFIED (no credential in ${env_var})")
        return

    provider, _ = LLMProvider.objects.get_or_create(
        name=f"{provider_type} (smoke test)",
        defaults={"provider_type": provider_type, "credential_env_var": env_var},
    )
    if provider.credential_env_var != env_var:
        provider.credential_env_var = env_var
        provider.save(update_fields=["credential_env_var"])

    resolved_model_id = model_id or DEFAULT_MODEL_IDS[provider_type]
    llm_model, created = LLMModel.objects.get_or_create(
        provider=provider,
        model_id=resolved_model_id,
        defaults={"supports_structured_output": True},
    )
    if not created and not llm_model.supports_structured_output:
        llm_model.supports_structured_output = True
        llm_model.save(update_fields=["supports_structured_output"])

    adapter = ADAPTER_CLASSES[provider_type](llm_model)
    request = NormalizedLLMRequest(
        stage="MEMORY_BUILD",
        messages=[
            {"role": "user", "content": "Reply with a JSON object acknowledging this smoke test."}
        ],
        output_schema=SmokeTestOutput,
        max_output_tokens=64,
    )
    result = adapter.generate(request)
    if result.is_error:
        print(f"{provider_type}: FAILED -- {result.error.category.value}: {result.error.message}")
        sys.exit(1)

    latency = f"{result.latency_ms:.0f}" if result.latency_ms is not None else "?"
    print(f"{provider_type}: OK -- content={result.content!r} usage={result.usage} latency_ms={latency}")
