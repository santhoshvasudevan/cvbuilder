"""Shared logic for opt-in manual provider smoke verification (docs/IMPLEMENTATION_PLAN.md M2).

These scripts are never invoked by `manage.py test` or any automated/CI path -- they are run
only when the operator explicitly initiates them, and require a real credential in the
environment. A provider with no configured credential is reported as NOT LIVE-VERIFIED rather
than failing. Only sanitized `LLMCallLog` metadata is ever recorded; no raw provider
request/response body is persisted anywhere, including here (this prints to the operator's own
terminal only).

Model selection (audit repair, 2026-09-02): the harness must never depend on a stale hardcoded
model id. When `--model`/`model_id` is not given, it prefers the model actually assigned to
`MEMORY_BUILD` for this provider type -- the model genuinely intended for Candidate Memory -- and
otherwise falls back to a registered, structured-output-capable model only if there is exactly
one unambiguous candidate. If neither resolves, it fails clearly, printing why, and makes no
provider call at all. It never silently creates a different hardcoded model row.
"""

from __future__ import annotations

import os
import sys

from pydantic import BaseModel

from ..adapters import ADAPTER_CLASSES
from ..models import LLMModel, LLMProvider, StageModelAssignment
from ..types import NormalizedLLMRequest


class SmokeTestOutput(BaseModel):
    acknowledged: bool


CREDENTIAL_ENV_VARS = {
    LLMProvider.ProviderType.OPENAI: "OPENAI_API_KEY",
    LLMProvider.ProviderType.NVIDIA_NIM: "NVIDIA_NIM_API_KEY",
    LLMProvider.ProviderType.GEMINI: "GEMINI_API_KEY",
    LLMProvider.ProviderType.OPENROUTER: "OPENROUTER_API_KEY",
}


class AmbiguousModelSelectionError(Exception):
    """Raised (and caught locally) when more than one candidate model exists and none is
    assigned to MEMORY_BUILD -- selection must fail clearly, never guess."""

    def __init__(self, candidate_model_ids: list[str]):
        self.candidate_model_ids = candidate_model_ids
        super().__init__(f"Ambiguous: {candidate_model_ids}")


class NoModelRegisteredError(Exception):
    """Raised (and caught locally) when no structured-output-capable model is registered for
    this provider type and none was given explicitly."""


def select_default_model(provider_type: str) -> LLMModel:
    """Resolve which `LLMModel` a smoke test should use when `--model` is not given.

    1. Prefer whatever model is currently assigned to the `MEMORY_BUILD` stage for this provider
       type -- that is, by definition, the model actually intended for Candidate Memory.
    2. Otherwise, if exactly one structured-output-capable model is registered for this provider
       type, use it.
    3. Otherwise raise `NoModelRegisteredError` (zero candidates) or
       `AmbiguousModelSelectionError` (more than one candidate) -- callers must fail clearly
       rather than pick one arbitrarily.
    """
    assigned = (
        StageModelAssignment.objects.filter(
            stage=StageModelAssignment.Stage.MEMORY_BUILD,
            model__provider__provider_type=provider_type,
        )
        .select_related("model__provider")
        .first()
    )
    if assigned is not None:
        return assigned.model

    candidates = list(
        LLMModel.objects.filter(
            provider__provider_type=provider_type, supports_structured_output=True
        ).select_related("provider")
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) == 0:
        raise NoModelRegisteredError(provider_type)
    raise AmbiguousModelSelectionError([m.model_id for m in candidates])


def _resolve_explicit_model(provider_type: str, env_var: str, model_id: str) -> LLMModel:
    """`--model` was given explicitly: get-or-create exactly that registry row (same behavior as
    before this repair) rather than consulting registry-driven default selection."""
    provider, _ = LLMProvider.objects.get_or_create(
        name=f"{provider_type} (smoke test)",
        defaults={"provider_type": provider_type, "credential_env_var": env_var},
    )
    if provider.credential_env_var != env_var:
        provider.credential_env_var = env_var
        provider.save(update_fields=["credential_env_var"])

    llm_model, created = LLMModel.objects.get_or_create(
        provider=provider, model_id=model_id, defaults={"supports_structured_output": True}
    )
    if not created and not llm_model.supports_structured_output:
        llm_model.supports_structured_output = True
        llm_model.save(update_fields=["supports_structured_output"])
    return llm_model


def run_smoke_test(
    provider_type: str,
    model_id: str | None = None,
    *,
    temperature: float = 0.0,
    reasoning_enabled: bool | None = None,
) -> None:
    env_var = CREDENTIAL_ENV_VARS[provider_type]
    if not os.environ.get(env_var):
        print(f"{provider_type}: NOT LIVE-VERIFIED (no credential in ${env_var})")
        return

    if model_id:
        llm_model = _resolve_explicit_model(provider_type, env_var, model_id)
    else:
        try:
            llm_model = select_default_model(provider_type)
        except NoModelRegisteredError:
            print(
                f"{provider_type}: NOT RUN -- no structured-output-capable LLMModel is "
                f"registered for this provider and no --model was given. Register a provider/"
                f"model/StageModelAssignment row first (see docs/CURRENT_STATE.md), or pass "
                f"--model explicitly. No provider call was made."
            )
            return
        except AmbiguousModelSelectionError as exc:
            ids = ", ".join(exc.candidate_model_ids)
            print(
                f"{provider_type}: NOT RUN -- model selection is ambiguous "
                f"({len(exc.candidate_model_ids)} candidates: {ids}) and no MEMORY_BUILD "
                f"StageModelAssignment exists for this provider to disambiguate. Pass --model "
                f"explicitly. No provider call was made."
            )
            return

    print(f"{provider_type}: using provider={llm_model.provider.name!r} model={llm_model.model_id!r}")

    adapter = ADAPTER_CLASSES[provider_type](llm_model)
    request = NormalizedLLMRequest(
        stage="MEMORY_BUILD",
        messages=[
            {"role": "user", "content": "Reply with a JSON object acknowledging this smoke test."}
        ],
        output_schema=SmokeTestOutput,
        max_output_tokens=64,
        temperature=temperature,
        reasoning_enabled=reasoning_enabled,
    )
    result = adapter.generate(request)
    if result.is_error:
        print(f"{provider_type}: FAILED -- {result.error.category.value}: {result.error.message}")
        sys.exit(1)

    latency = f"{result.latency_ms:.0f}" if result.latency_ms is not None else "?"
    print(f"{provider_type}: OK -- content={result.content!r} usage={result.usage} latency_ms={latency}")
