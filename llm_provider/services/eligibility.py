"""Shared model-eligibility service (2026-09-07, per-run model selection / D-038 default-
configuration broadening).

The AJ/AC/AB model-selection UI and the execution path that resolves a per-run override both call
`eligible_models_for_stage()` -- there is exactly one place that decides "which models may this
stage use," so a model can never appear selectable in a form but be rejected at execution time (or
vice versa) because two different pieces of code drifted out of sync.

A selectable model must, in order:

1. belong to an active provider (`LLMProvider.is_active`);
2. be active itself (`LLMModel.is_active`);
3. support the capability the stage requires -- every stage currently implemented in this codebase
   (MEMORY_BUILD/AJ_ANALYZE/AC_NORMALIZE/AC_RANK/AC_MATCH/AB_BUILD) requires schema-validated
   structured output, so `supports_structured_output` is required for all of them today;
4. have a configured credential *reference* where the project requires one -- a non-blank
   `LLMProvider.credential_env_var` (never the credential's actual value, which this application
   never stores at all, NFR-003) -- the FAKE provider type is test-only and never requires one;
5. never be the FAKE provider type outside an automated test run, mirroring
   `llm_provider.adapters.get_adapter_for_stage`'s own `FakeProviderNotAllowedError` guard --
   production code must never offer a test-only model in a real operator-facing selector.

Retirement (e.g. the retired Z.ai/GLM OpenRouter model, D-038) is handled entirely through
`LLMModel.is_active=False` -- there is no separate "retired model id" blocklist here, so
reactivating a model via the existing admin (the documented rollback path) is exactly what makes
it eligible again, with zero change to this module. `manage.py configure_openrouter_free_router`
deactivates *every* `LLMModel` row matching the retired model id, across every OpenRouter provider
row that happens to have one -- never only "the" canonical one -- so a stray duplicate provider
row (e.g. a leftover debug/smoke-test artifact) can never leave an active copy of a retired model
eligible just because that particular row was never touched.

Adding a future paid model (OpenRouter or a direct provider) therefore requires only a registry
row with truthful capability flags, activated -- no change to this module, to AJ/AC/AB forms, to
templates, or to any pipeline service.
"""

from __future__ import annotations

from django.db.models import QuerySet

from ..models import LLMModel, LLMProvider, StageModelAssignment

# Every stage currently implemented in this codebase produces schema-validated structured output
# (AgentJobberAnalysis, RequirementNormalizationOutput, RelevanceRankingOutput,
# AgentCandidateAssessment, AgentBuilderOutput) -- so today every stage requires
# `supports_structured_output=True`. Kept as an explicit per-stage mapping (rather than a single
# blanket filter) so a future stage with a genuinely different capability requirement is a
# one-line addition here, not a rethink of this function's shape.
STRUCTURED_OUTPUT_STAGES: frozenset[str] = frozenset(StageModelAssignment.Stage.values)


def eligible_models_for_stage(stage: str) -> QuerySet[LLMModel]:
    """Returns the queryset of `LLMModel` rows selectable for `stage` -- by an operator in the
    AJ/AC/AB UI, and by `llm_provider.services.model_selection.resolve_stage_model` when
    validating an explicit per-run override. Ordered deterministically (provider name, then model
    id) so the UI and any test asserting on order stay stable."""
    queryset = (
        LLMModel.objects.select_related("provider")
        .filter(is_active=True, provider__is_active=True)
        .exclude(provider__provider_type=LLMProvider.ProviderType.FAKE)
    )
    if stage in STRUCTURED_OUTPUT_STAGES:
        queryset = queryset.filter(supports_structured_output=True)
    queryset = queryset.exclude(provider__credential_env_var="")
    return queryset.order_by("provider__name", "model_id")


def is_model_eligible_for_stage(model: LLMModel, stage: str) -> bool:
    """Convenience re-check for a single already-fetched model -- used where re-querying the full
    eligible set would be wasteful (e.g. confirming a stage's own current default is still
    eligible for display purposes). Always consistent with `eligible_models_for_stage` because it
    delegates to the exact same queryset rather than duplicating the filter logic."""
    return eligible_models_for_stage(stage).filter(pk=model.pk).exists()


def model_display_label(model: LLMModel) -> str:
    """The operator-facing label for one model, used by every AJ/AC/AB selector and by
    `llm_provider.services.model_selection` error messages -- one shared formatting rule so the
    UI and diagnostics never disagree on how a model is described.

    Format: '<provider name> -- <display_name or model_id>', with the raw model_id appended in
    parentheses whenever a curated `display_name` is set and differs from it (so the operator
    always has the exact wire value available, never only a friendly label that could obscure
    which concrete model_id is actually selected)."""
    provider_name = model.provider.name
    if model.display_name and model.display_name != model.model_id:
        return f"{provider_name} -- {model.display_name} ({model.model_id})"
    return f"{provider_name} -- {model.model_id}"
