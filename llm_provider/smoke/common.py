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


# Reasoning-enabled smoke-test output budgets (2026-09-04, D-026): reasoning tokens consume the
# same completion-token allowance as the final answer, so the plain non-reasoning smoke budget
# (suitable only for a minimal, non-reasoning acknowledgement) is not a valid qualification budget
# for a reasoning-enabled request -- a reasoning model can spend the whole allowance "thinking"
# and never emit a final answer, which is exactly the shape of the real OpenRouter incident this
# addresses (see D-026). None of these change `LLMModel.max_output_tokens`,
# `StageModelAssignment.max_output_tokens`, or any application-stage request budget -- this is a
# smoke-harness-only concern.
DEFAULT_SMOKE_MAX_OUTPUT_TOKENS = 64
DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS = 4096
# A hard ceiling on what any smoke test -- reasoning or not, explicit or default -- may ever
# request, independent of how large the underlying model's own capability is; a smoke test is a
# minimal connectivity/configuration check, never a real workload.
SMOKE_MAX_OUTPUT_TOKENS_CEILING = 8192


class InvalidSmokeOutputBudgetError(Exception):
    """Raised (and caught locally, before any adapter is constructed or HTTP call is made) when
    the resolved smoke-test output-token budget is invalid -- not a positive integer, above the
    smoke-specific safety ceiling, or above the selected model's own capability."""


def resolve_smoke_max_output_tokens(
    explicit: int | None,
    *,
    reasoning_enabled: bool | None,
    model_capability: int | None,
) -> int:
    """Resolve the exact output-token budget a smoke-test request body will carry.

    An explicit `--max-output-tokens` value always overrides the reasoning/non-reasoning
    default. Every resolved value (explicit or default) must be a positive integer, must not
    exceed `SMOKE_MAX_OUTPUT_TOKENS_CEILING`, and must not exceed the selected model's own
    `max_output_tokens` capability when that capability is known -- raising
    `InvalidSmokeOutputBudgetError` rather than silently clamping, so a misconfigured value is
    caught here, before any provider call, never sent and hoped for.
    """
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int) or explicit <= 0:
            raise InvalidSmokeOutputBudgetError(
                f"--max-output-tokens must be a positive integer, got {explicit!r}."
            )
        resolved = explicit
    else:
        resolved = (
            DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS
            if reasoning_enabled
            else DEFAULT_SMOKE_MAX_OUTPUT_TOKENS
        )

    if resolved > SMOKE_MAX_OUTPUT_TOKENS_CEILING:
        raise InvalidSmokeOutputBudgetError(
            f"Resolved smoke-test output-token budget ({resolved}) exceeds the smoke-test safety "
            f"ceiling ({SMOKE_MAX_OUTPUT_TOKENS_CEILING})."
        )
    if model_capability is not None and resolved > model_capability:
        raise InvalidSmokeOutputBudgetError(
            f"Resolved smoke-test output-token budget ({resolved}) exceeds the selected model's "
            f"own max_output_tokens capability ({model_capability})."
        )
    return resolved


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
    max_output_tokens: int | None = None,
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

    try:
        resolved_max_output_tokens = resolve_smoke_max_output_tokens(
            max_output_tokens,
            reasoning_enabled=reasoning_enabled,
            model_capability=llm_model.max_output_tokens,
        )
    except InvalidSmokeOutputBudgetError as exc:
        print(f"{provider_type}: NOT RUN -- {exc} No provider call was made.")
        return

    print(
        f"{provider_type}: using provider={llm_model.provider.name!r} model={llm_model.model_id!r} "
        f"max_output_tokens={resolved_max_output_tokens}"
    )

    adapter = ADAPTER_CLASSES[provider_type](llm_model)
    request = NormalizedLLMRequest(
        stage="MEMORY_BUILD",
        messages=[
            {"role": "user", "content": "Reply with a JSON object acknowledging this smoke test."}
        ],
        output_schema=SmokeTestOutput,
        max_output_tokens=resolved_max_output_tokens,
        temperature=temperature,
        reasoning_enabled=reasoning_enabled,
    )
    result = adapter.generate(request)
    if result.is_error:
        print(f"{provider_type}: FAILED -- {result.error.category.value}: {result.error.message}")
        sys.exit(1)

    latency = f"{result.latency_ms:.0f}" if result.latency_ms is not None else "?"
    print(f"{provider_type}: OK -- content={result.content!r} usage={result.usage} latency_ms={latency}")
