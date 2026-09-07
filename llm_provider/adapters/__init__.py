"""Adapter registry and stage routing (LLM-001/LLM-006).

Adding a new provider means: implement the adapter interface (a new module here) and add one
line to ADAPTER_CLASSES -- zero changes to pipeline logic (NFR-005).
"""

from __future__ import annotations

from django.conf import settings

from ..models import MAX_READ_TIMEOUT_SECONDS, MIN_READ_TIMEOUT_SECONDS, LLMCallLog, LLMProvider
from ..services.model_selection import (
    ModelNotEligibleForStageError,
    NoStageDefaultConfiguredError,
    ReasoningNotEligibleForStageError,
    resolve_stage_model,
)
from .base import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    BaseLLMAdapter,
)
from .fake import FakeAdapter
from .gemini import GeminiAdapter
from .nvidia import NvidiaNimAdapter
from .openai import OpenAIAdapter
from .openrouter import OpenRouterAdapter

ADAPTER_CLASSES: dict[str, type[BaseLLMAdapter]] = {
    LLMProvider.ProviderType.OPENAI: OpenAIAdapter,
    LLMProvider.ProviderType.NVIDIA_NIM: NvidiaNimAdapter,
    LLMProvider.ProviderType.GEMINI: GeminiAdapter,
    LLMProvider.ProviderType.OPENROUTER: OpenRouterAdapter,
    LLMProvider.ProviderType.FAKE: FakeAdapter,
}


class FakeProviderNotAllowedError(Exception):
    """Raised by `get_adapter_for_stage` when a real pipeline stage's registry assignment
    resolves to a FAKE provider outside an automated test run (audit finding, 2026-09-03). The
    FAKE provider type exists solely for deterministic tests -- production code must never
    silently route a real stage through it just because an operator (or a stray script) left a
    FAKE `StageModelAssignment` configured. Tests that need a `FakeAdapter` inject it explicitly
    (`unittest.mock.patch` on the caller's own `get_adapter_for_stage` reference) rather than
    relying on the registry at all, so this guard never fires for the existing test suite."""


class InvalidStageBudgetError(Exception):
    """Raised by `get_adapter_for_stage` when a stage's own `StageModelAssignment.
    max_output_tokens` exceeds its assigned model's `max_output_tokens` capability (2026-09-04,
    stage-specific token budgets). `StageModelAssignment.clean()` already rejects this at
    admin-save time -- this is defense in depth against a row that reached the database without
    going through `full_clean()` (e.g. a fixture, a script, `objects.create()`), so a
    misconfigured budget is caught here, before any provider call, rather than silently sent to
    the provider or silently clamped."""


class InactiveModelAssignedError(Exception):
    """Raised by `get_adapter_for_stage` when a stage's `StageModelAssignment.model` has been
    marked `is_active=False` (2026-09-07, OpenRouter Free Router migration). A model is
    deactivated -- never deleted -- when it is retired (e.g. a superseded OpenRouter slug like
    the old Z.ai/GLM model); this guard is the enforcement half of that retirement, so a stale
    StageModelAssignment left pointing at a deactivated model can never silently keep routing
    live calls to it. Fix by reassigning the stage to an active model, or by reactivating this
    one (`LLMModel.is_active = True`) if that was a mistake -- both are plain registry edits via
    the existing admin, per this decision's rollback requirement."""


class InvalidStageTimeoutError(Exception):
    """Raised by `get_adapter_for_stage` when a stage's own `StageModelAssignment.
    read_timeout_seconds` falls outside `[MIN_READ_TIMEOUT_SECONDS, MAX_READ_TIMEOUT_SECONDS]`
    (2026-09-05, configurable per-stage timeout). `StageModelAssignment.clean()` already rejects
    this at admin-save time -- this is the same defense-in-depth pattern as
    `InvalidStageBudgetError` immediately above, for a row that reached the database without going
    through `full_clean()`, caught here before any provider call rather than silently sent to the
    provider or silently clamped."""


def get_adapter_for_stage(
    stage: str,
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
) -> BaseLLMAdapter:
    """Resolve which `LLMModel` handles this call and return a bound adapter instance for it.
    This is the *only* place pipeline code needs to call to route a stage to whichever
    provider/model should handle one specific invocation.

    Resolution precedence (2026-09-07, per-run model selection; `llm_provider.services.
    model_selection.resolve_stage_model` is the single source of truth for it):

    1. `requested_model_id`, when given, if it is currently eligible for `stage`
       (`llm_provider.services.eligibility.eligible_models_for_stage`) -- an explicit per-run
       override, never persisted as a new global default;
    2. otherwise the stage's configured `StageModelAssignment` (the global default);
    3. otherwise `NoStageDefaultConfiguredError` -- there is no hidden hard-coded fallback.

    `requested_reasoning_effort` (2026-09-07, paid GPT-5.4 model defaults) follows the exact same
    per-field precedence, independent of the model precedence above: an explicit per-run value,
    else the stage's own `StageModelAssignment.default_reasoning_effort`, else `None` ("say
    nothing about reasoning"). The resolved value is re-validated against whichever model was
    actually resolved (`ReasoningNotEligibleForStageError` if incompatible) and exposed on the
    returned adapter as `effective_reasoning_effort` -- every pipeline service must read that
    attribute into its `NormalizedLLMRequest.reasoning_effort`, exactly like
    `effective_max_output_tokens` below.

    The returned adapter's `effective_max_output_tokens` (2026-09-04) is the one value every
    pipeline service must use as its request budget: for the DEFAULT path, the stage's own
    `StageModelAssignment.max_output_tokens` when configured, otherwise the model's own capability
    (or a conservative built-in default if neither is set) -- `BaseLLMAdapter.__init__` already
    computes that fallback, so this function only ever needs to *override* it when a stage-
    specific budget is configured. For the OVERRIDE path, the stage's own tuned budget was never
    validated against an arbitrary other model's capability, so the override model's own
    capability (via that same `BaseLLMAdapter.__init__` fallback) is used unmodified, never a
    budget number tuned for a different model.
    """
    selection = resolve_stage_model(
        stage,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    llm_model = selection.model
    assignment = selection.assignment
    if not llm_model.is_active:
        raise InactiveModelAssignedError(
            f"Stage {stage!r} resolved to {llm_model.provider.name}/{llm_model.model_id}, which "
            "has been deactivated (LLMModel.is_active=False). Reassign this stage to an active "
            "model via the registry admin, or reactivate this one if that was unintended, before "
            "this stage can be used."
        )
    provider_type = llm_model.provider.provider_type
    if provider_type == LLMProvider.ProviderType.FAKE and not settings.TESTING:
        raise FakeProviderNotAllowedError(
            f"Stage {stage!r} resolved to a FAKE provider/model "
            f"({llm_model.provider.name}/{llm_model.model_id}) outside of an automated test run. "
            "Reassign this stage to a real provider/model via the registry admin before using it."
        )
    adapter_cls = ADAPTER_CLASSES[provider_type]
    adapter = adapter_cls(llm_model)
    adapter.selection_source = selection.source
    adapter.effective_reasoning_effort = selection.reasoning_effort
    if selection.source == LLMCallLog.SelectionSource.DEFAULT and assignment is not None:
        if assignment.max_output_tokens is not None:
            model_capability = llm_model.max_output_tokens
            if model_capability is not None and assignment.max_output_tokens > model_capability:
                raise InvalidStageBudgetError(
                    f"Stage {stage!r}'s configured max_output_tokens "
                    f"({assignment.max_output_tokens}) exceeds {llm_model}'s capability "
                    f"({llm_model.max_output_tokens}) -- fix the StageModelAssignment before this "
                    "stage can be used."
                )
            adapter.effective_max_output_tokens = assignment.max_output_tokens
        if assignment.read_timeout_seconds is not None:
            if not (
                MIN_READ_TIMEOUT_SECONDS <= assignment.read_timeout_seconds <= MAX_READ_TIMEOUT_SECONDS
            ):
                raise InvalidStageTimeoutError(
                    f"Stage {stage!r}'s configured read_timeout_seconds "
                    f"({assignment.read_timeout_seconds}) is outside the allowed range "
                    f"[{MIN_READ_TIMEOUT_SECONDS}, {MAX_READ_TIMEOUT_SECONDS}] -- fix the "
                    "StageModelAssignment before this stage can be used."
                )
            adapter.effective_read_timeout_seconds = assignment.read_timeout_seconds
    return adapter


__all__ = [
    "ADAPTER_CLASSES",
    "BaseLLMAdapter",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "FakeAdapter",
    "FakeProviderNotAllowedError",
    "GeminiAdapter",
    "InactiveModelAssignedError",
    "InvalidStageBudgetError",
    "InvalidStageTimeoutError",
    "ModelNotEligibleForStageError",
    "NoStageDefaultConfiguredError",
    "NvidiaNimAdapter",
    "OpenAIAdapter",
    "OpenRouterAdapter",
    "ReasoningNotEligibleForStageError",
    "get_adapter_for_stage",
]
