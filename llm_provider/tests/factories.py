"""Small helpers shared across llm_provider's test modules -- not a full factory library,
just enough to avoid repeating the same registry setup in every test."""

from __future__ import annotations

from ..models import LLMModel, LLMProvider, StageModelAssignment


def make_provider(provider_type=LLMProvider.ProviderType.FAKE, name="Test Provider", **kwargs):
    defaults = {"provider_type": provider_type, "credential_env_var": "TEST_API_KEY"}
    defaults.update(kwargs)
    return LLMProvider.objects.create(name=name, **defaults)


def make_model(provider=None, model_id="test-model", **kwargs):
    provider = provider or make_provider()
    defaults = {"supports_structured_output": True}
    defaults.update(kwargs)
    return LLMModel.objects.create(provider=provider, model_id=model_id, **defaults)


def make_stage_assignment(
    stage=StageModelAssignment.Stage.MEMORY_BUILD,
    model=None,
    max_output_tokens=None,
    read_timeout_seconds=None,
):
    model = model or make_model()
    return StageModelAssignment.objects.create(
        stage=stage,
        model=model,
        max_output_tokens=max_output_tokens,
        read_timeout_seconds=read_timeout_seconds,
    )
