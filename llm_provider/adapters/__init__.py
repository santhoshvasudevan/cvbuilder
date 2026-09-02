"""Adapter registry and stage routing (LLM-001/LLM-006).

Adding a new provider means: implement the adapter interface (a new module here) and add one
line to ADAPTER_CLASSES -- zero changes to pipeline logic (NFR-005).
"""

from __future__ import annotations

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


def get_adapter_for_stage(stage: str) -> BaseLLMAdapter:
    """Look up which LLMModel is currently assigned to `stage` and return an adapter instance
    for it. This is the *only* place pipeline code needs to call to route a stage to whichever
    provider/model the registry currently assigns -- changing the assignment in the admin UI
    changes the routing with zero code change.
    """
    assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
    llm_model = assignment.model
    adapter_cls = ADAPTER_CLASSES[llm_model.provider.provider_type]
    return adapter_cls(llm_model)


__all__ = [
    "ADAPTER_CLASSES",
    "BaseLLMAdapter",
    "FakeAdapter",
    "GeminiAdapter",
    "NvidiaNimAdapter",
    "OpenAIAdapter",
    "get_adapter_for_stage",
]
