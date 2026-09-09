"""Adapter registry and stage routing (LLM-001/LLM-006).

Adding a new provider means: implement the adapter interface (a new module here) and add one
line to ADAPTER_CLASSES -- zero changes to pipeline logic.
"""

from __future__ import annotations

from ..errors import ConfigurationError
from ..models import LLMProvider, StageModelAssignment
from ..validation import validate_call_configuration
from .base import BaseLLMAdapter
from .fake import FakeAdapter
from .gemini import GeminiAdapter
from .nvidia import NvidiaNimAdapter
from .openai import OpenAIAdapter
from .openrouter import OpenRouterAdapter

ADAPTER_CLASSES: dict[str, type[BaseLLMAdapter]] = {
    LLMProvider.AdapterType.OPENAI: OpenAIAdapter,
    LLMProvider.AdapterType.NVIDIA_NIM: NvidiaNimAdapter,
    LLMProvider.AdapterType.GEMINI: GeminiAdapter,
    LLMProvider.AdapterType.OPENROUTER: OpenRouterAdapter,
    LLMProvider.AdapterType.FAKE: FakeAdapter,
}


def get_adapter_for_stage(stage: str, *, stage_run=None) -> BaseLLMAdapter:
    """Look up which LLMModel is currently assigned to `stage` and return an adapter instance
    for it. This is the *only* place pipeline code needs to call to route a stage to whichever
    provider/model the registry currently assigns -- changing the assignment in the admin
    changes routing with zero code change (LLM-001/LLM-006). Never selects a fallback provider
    or model: if the assignment or its configuration is invalid, this raises rather than
    silently trying something else.

    Raises `StageModelAssignment.DoesNotExist` if no assignment exists for `stage`, or a
    `ConfigurationError` subclass if the assigned provider/model fails pre-flight validation --
    both fail before any HTTP call is attempted.
    """
    assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
    llm_model = assignment.model
    validate_call_configuration(
        provider=llm_model.provider,
        model=llm_model,
        reasoning_level=assignment.default_reasoning_level,
        max_output_tokens=assignment.default_max_output_tokens,
        temperature=assignment.default_temperature,
    )
    adapter_cls = ADAPTER_CLASSES[llm_model.provider.adapter_type]
    return adapter_cls(llm_model, stage_run=stage_run)


def get_adapter_for_model(model, *, stage_run=None) -> BaseLLMAdapter:
    """Construct an adapter for an explicit `LLMModel` override (LLM-006 runtime override, and
    model-comparison reruns of the same stored input against a different model, LLM-010/011) --
    without touching or requiring a `StageModelAssignment` row. Configuration is validated with
    the requested reasoning level defaulting to NONE unless the caller overrides it via the
    request passed to the returned adapter's `generate()`.
    """
    adapter_cls = ADAPTER_CLASSES[model.provider.adapter_type]
    return adapter_cls(model, stage_run=stage_run)


__all__ = [
    "ADAPTER_CLASSES",
    "BaseLLMAdapter",
    "ConfigurationError",
    "FakeAdapter",
    "GeminiAdapter",
    "NvidiaNimAdapter",
    "OpenAIAdapter",
    "OpenRouterAdapter",
    "get_adapter_for_model",
    "get_adapter_for_stage",
]
