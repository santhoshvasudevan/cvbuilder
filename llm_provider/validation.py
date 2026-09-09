"""Pre-flight configuration validation (docs/IMPLEMENTATION_PLAN.md M2 acceptance): the single
place every "fails before HTTP" check lives, so no call path can skip a check by constructing an
adapter directly instead of going through `llm_provider.adapters.get_adapter_for_stage()` or
`llm_provider.services.console`.

Every function here is pure and synchronous -- no I/O except reading (never requiring) an
environment variable's *presence*, never its value, and never a network call. Every function
raises a typed `llm_provider.errors.ConfigurationError` subclass; nothing here ever performs
automatic provider/model fallback -- a failed validation always stops the call, never silently
substitutes a different provider or model.
"""

from __future__ import annotations

import math
import os

from .errors import (
    InactiveModelError,
    InactiveProviderError,
    MissingCredentialConfigurationError,
    MissingCredentialValueError,
    StageBudgetExceededError,
    TemperatureOutOfRangeError,
    TemperatureReasoningConflictError,
    UnsupportedReasoningLevelError,
    UnsupportedStructuredOutputError,
    UnsupportedTemperatureError,
)
from .models import MAX_TEMPERATURE, MIN_TEMPERATURE, LLMModel, LLMProvider, ReasoningLevel


def validate_provider_active(provider: LLMProvider) -> None:
    if not provider.enabled:
        raise InactiveProviderError(provider.name)


def validate_model_active(model: LLMModel) -> None:
    if not model.enabled:
        raise InactiveModelError(str(model))


def validate_credential_configured(provider: LLMProvider) -> None:
    """The DB row must *reference* a credential environment variable -- never the value itself
    (LLM-012). FAKE providers need no credential at all."""
    if provider.adapter_type == LLMProvider.AdapterType.FAKE:
        return
    if not provider.credential_env_variable:
        raise MissingCredentialConfigurationError(provider.name)


def validate_credential_value_present(provider: LLMProvider) -> None:
    """The named environment variable must actually be set in the current process environment.
    Reads only whether the variable is present -- never logs, returns, or stores its value."""
    if provider.adapter_type == LLMProvider.AdapterType.FAKE:
        return
    env_var = provider.credential_env_variable
    if not env_var:
        return  # validate_credential_configured already covers the blank-reference case
    if os.environ.get(env_var) is None:
        raise MissingCredentialValueError(provider.name, env_var)


def validate_structured_output_supported(model: LLMModel, *, required: bool = True) -> None:
    if required and not model.supports_structured_output:
        raise UnsupportedStructuredOutputError(str(model))


def validate_reasoning_level(model: LLMModel, reasoning_level: str) -> None:
    if reasoning_level not in (model.supported_reasoning_levels or []):
        raise UnsupportedReasoningLevelError(str(model), reasoning_level)


def validate_output_budget(model: LLMModel, requested_max_output_tokens: int | None) -> None:
    if requested_max_output_tokens is None:
        return
    if model.max_output_tokens is not None and requested_max_output_tokens > model.max_output_tokens:
        raise StageBudgetExceededError(str(model), requested_max_output_tokens, model.max_output_tokens)


def validate_temperature_supported(
    model: LLMModel, temperature: float | None, reasoning_level: str = ReasoningLevel.NONE
) -> None:
    """V2-D042: reject before HTTP when `temperature` is set but the model doesn't support it,
    is combined with a reasoning level the model forbids it with, or falls outside the canonical
    permitted numeric range. `None` (unset) always passes -- a model that doesn't support
    temperature is still callable as long as the caller doesn't request one.
    """
    if temperature is None:
        return
    if not math.isfinite(temperature) or not (MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE):
        raise TemperatureOutOfRangeError(temperature, MIN_TEMPERATURE, MAX_TEMPERATURE)
    if not model.supports_temperature:
        raise UnsupportedTemperatureError(str(model))
    reasoning_active = reasoning_level and reasoning_level != ReasoningLevel.NONE
    if reasoning_active and not model.supports_temperature_with_reasoning:
        raise TemperatureReasoningConflictError(str(model), reasoning_level)


def validate_call_configuration(
    *,
    provider: LLMProvider,
    model: LLMModel,
    reasoning_level: str = ReasoningLevel.NONE,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    require_structured_output: bool = True,
) -> None:
    """Run every applicable pre-flight check, in a fixed order, before any HTTP call is
    attempted. Raises on the first failing check; callers never need to catch individual
    exception types unless they want a specific message -- all subclass `ConfigurationError`.
    """
    validate_provider_active(provider)
    validate_model_active(model)
    validate_credential_configured(provider)
    validate_credential_value_present(provider)
    validate_structured_output_supported(model, required=require_structured_output)
    validate_reasoning_level(model, reasoning_level)
    validate_output_budget(model, max_output_tokens)
    validate_temperature_supported(model, temperature, reasoning_level)
