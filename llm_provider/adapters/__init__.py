"""Adapter registry and stage routing (LLM-001/LLM-006).

Adding a new provider means: implement the adapter interface (a new module here) and add one
line to ADAPTER_CLASSES -- zero changes to pipeline logic (NFR-005).
"""

from __future__ import annotations

from django.conf import settings

from ..models import LLMProvider, StageModelAssignment
from .base import BaseLLMAdapter
from .fake import FakeAdapter
from .gemini import GeminiAdapter
from .nvidia import NvidiaNimAdapter
from .openai import OpenAIAdapter

ADAPTER_CLASSES: dict[str, type[BaseLLMAdapter]] = {
    LLMProvider.ProviderType.OPENAI: OpenAIAdapter,
    LLMProvider.ProviderType.NVIDIA_NIM: NvidiaNimAdapter,
    LLMProvider.ProviderType.GEMINI: GeminiAdapter,
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


def get_adapter_for_stage(stage: str) -> BaseLLMAdapter:
    """Look up which LLMModel is currently assigned to `stage` and return an adapter instance
    for it. This is the *only* place pipeline code needs to call to route a stage to whichever
    provider/model the registry currently assigns -- changing the assignment in the admin UI
    changes the routing with zero code change.
    """
    assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
    llm_model = assignment.model
    provider_type = llm_model.provider.provider_type
    if provider_type == LLMProvider.ProviderType.FAKE and not settings.TESTING:
        raise FakeProviderNotAllowedError(
            f"Stage {stage!r} is currently assigned to a FAKE provider/model "
            f"({llm_model.provider.name}/{llm_model.model_id}) outside of an automated test run. "
            "Reassign this stage to a real provider/model via the registry admin before using it."
        )
    adapter_cls = ADAPTER_CLASSES[provider_type]
    return adapter_cls(llm_model)


__all__ = [
    "ADAPTER_CLASSES",
    "BaseLLMAdapter",
    "FakeAdapter",
    "FakeProviderNotAllowedError",
    "GeminiAdapter",
    "NvidiaNimAdapter",
    "OpenAIAdapter",
    "get_adapter_for_stage",
]
