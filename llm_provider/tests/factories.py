"""Small, explicit test-data builders -- no factory_boy dependency, just plain functions, so
every test can see exactly what data it is creating.
"""

from __future__ import annotations

from llm_provider.models import LLMModel, LLMProvider, ReasoningLevel, StageModelAssignment


def make_provider(
    *,
    name: str = "Test OpenAI",
    adapter_type: str = LLMProvider.AdapterType.OPENAI,
    credential_env_variable: str = "TEST_OPENAI_API_KEY",
    enabled: bool = True,
    base_url: str = "",
) -> LLMProvider:
    return LLMProvider.objects.create(
        name=name,
        adapter_type=adapter_type,
        credential_env_variable=credential_env_variable,
        enabled=enabled,
        base_url=base_url,
    )


def make_fake_provider(*, name: str = "Test Fake", enabled: bool = True) -> LLMProvider:
    return LLMProvider.objects.create(
        name=name,
        adapter_type=LLMProvider.AdapterType.FAKE,
        credential_env_variable="",
        enabled=enabled,
    )


def make_model(
    provider: LLMProvider,
    *,
    model_identifier: str = "test-model-1",
    supports_structured_output: bool = True,
    supported_reasoning_levels: list[str] | None = None,
    max_output_tokens: int | None = 4096,
    supports_temperature: bool = True,
    supports_temperature_with_reasoning: bool = True,
    enabled: bool = True,
) -> LLMModel:
    return LLMModel.objects.create(
        provider=provider,
        model_identifier=model_identifier,
        supports_structured_output=supports_structured_output,
        supported_reasoning_levels=supported_reasoning_levels or [ReasoningLevel.NONE],
        max_output_tokens=max_output_tokens,
        supports_temperature=supports_temperature,
        supports_temperature_with_reasoning=supports_temperature_with_reasoning,
        enabled=enabled,
    )


def make_stage_assignment(
    model: LLMModel,
    *,
    stage: str = "AJ_ANALYZE",
    default_reasoning_level: str = ReasoningLevel.NONE,
    default_max_output_tokens: int | None = None,
    default_temperature: float | None = None,
) -> StageModelAssignment:
    return StageModelAssignment.objects.create(
        stage=stage,
        model=model,
        default_reasoning_level=default_reasoning_level,
        default_max_output_tokens=default_max_output_tokens,
        default_temperature=default_temperature,
    )
