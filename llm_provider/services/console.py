"""Operator-facing stage-execution summary for the M5/M6 stage console (2026-09-07, D-039 paid
GPT-5.4 model defaults). One place computes the read-only "stage card" facts every AJ/AC/AB
inspection view needs (default vs. effective model/reasoning, paid/free, latest attempt, token
usage, sanitized error guidance) so the templates never re-derive them independently and can never
disagree about what "the latest attempt" means.

Never displays a raw prompt, a raw provider response body, or an unsanitized exception -- only the
already-sanitized `LLMCallLog` fields every adapter already writes (`llm_provider/adapters/base.py`,
`llm_provider/errors.py`).
"""

from __future__ import annotations

import dataclasses
import os

from ..errors import LLMErrorCategory
from ..models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment
from .eligibility import eligible_models_for_stage, model_display_label, provider_group_label

# Provider types this codebase routes to through a virtual/multi-model router rather than a fixed
# vendor endpoint (2026-09-07, D-039 correction: provider-aware selection) -- used only for the
# stage card's "endpoint type" display; never for eligibility/routing, which is always keyed off
# the model's own provider FK.
_ROUTED_PROVIDER_TYPES = frozenset({LLMProvider.ProviderType.OPENROUTER})


def endpoint_type_label(provider: LLMProvider | None) -> str:
    if provider is None:
        return ""
    return "Routed (OpenRouter)" if provider.provider_type in _ROUTED_PROVIDER_TYPES else "Direct API"


def credential_configured(provider: LLMProvider | None) -> bool | None:
    """`True`/`False` when the provider's referenced credential environment variable is
    set/missing in this process's own environment right now; `None` when there is no provider (or
    no configured reference) to check. Never reads, logs, or returns the credential *value* --
    only whether the named environment variable is non-empty."""
    if provider is None or not provider.credential_env_var:
        return None
    return bool(os.environ.get(provider.credential_env_var))

# Exact known free-tier OpenRouter model ids/suffixes actually used by this registry (D-038's
# `openrouter/free`, and the retired Z.ai/GLM `:free`-suffixed slug) -- the only "free" signal this
# codebase can state truthfully without a dedicated registry field. Never a guess about a real
# provider's pricing beyond what this codebase's own naming convention for its free-tier rows
# already encodes -- see requirements Sec 4 ("whether the selected model is paid or free when
# truthfully known").
_KNOWN_FREE_MODEL_IDS = frozenset({"openrouter/free"})
_FREE_MODEL_ID_SUFFIX = ":free"


def is_free_model(model: LLMModel | None) -> bool | None:
    """`True`/`False` when truthfully known from this codebase's own free-tier naming convention,
    `None` when not knowable from the model id alone (never guessed)."""
    if model is None:
        return None
    return model.model_id in _KNOWN_FREE_MODEL_IDS or model.model_id.endswith(_FREE_MODEL_ID_SUFFIX)


# Short, actionable operator guidance per sanitized error category (never provider-specific raw
# text) -- shown alongside `LLMCallLog.error_category`/`error_message` on a failed attempt's stage
# card.
ERROR_GUIDANCE: dict[str, str] = {
    LLMErrorCategory.CONFIGURATION.value: (
        "A registry/request configuration problem, not a transient failure -- check the stage's "
        "assigned model, reasoning-effort compatibility, and output-token budget before retrying."
    ),
    LLMErrorCategory.AUTH.value: (
        "The provider rejected the credential -- verify the environment variable named by this "
        "provider's credential_env_var is set and current."
    ),
    LLMErrorCategory.RATE_LIMIT.value: (
        "The provider is rate-limiting this key -- wait before retrying, or check "
        "Admin -> OpenRouter diagnostics for quota detail."
    ),
    LLMErrorCategory.TIMEOUT.value: (
        "The provider did not respond in time -- retry, or raise this stage's read-timeout "
        "override in the registry if this happens consistently."
    ),
    LLMErrorCategory.SCHEMA_VALIDATION.value: (
        "The provider's response did not validate against the required structured-output schema "
        "-- retry, or try a different model/reasoning effort for this stage."
    ),
    LLMErrorCategory.PROVIDER_INTERNAL.value: (
        "The provider reported an internal/upstream error -- usually transient; retry, or check "
        "the provider's own status page if it persists."
    ),
}


@dataclasses.dataclass(frozen=True)
class StageAttempt:
    attempt_number: int
    created_at: object
    latency_ms: int | None
    requested_model_label: str
    resolved_model_id: str
    finish_reason: str
    selection_source: str
    reasoning_effort: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    retry_count: int
    is_error: bool
    error_category: str
    error_message: str
    error_guidance: str


@dataclasses.dataclass(frozen=True)
class StageCard:
    stage: str
    stage_label: str
    default_assignment: StageModelAssignment | None
    default_model: LLMModel | None
    default_reasoning_effort: str
    default_is_free: bool | None
    default_provider_label: str
    default_endpoint_type: str
    default_credential_configured: bool | None
    effective_model: LLMModel | None
    effective_reasoning_effort: str
    effective_is_free: bool | None
    effective_provider_label: str
    effective_endpoint_type: str
    effective_credential_configured: bool | None
    max_output_tokens: int | None
    model_choices: list[tuple[int, str]]
    reasoning_choices: list[tuple[str, str]]
    latest_attempt: StageAttempt | None
    attempt_count: int


def _to_attempt(log: LLMCallLog, attempt_number: int) -> StageAttempt:
    requested_label = model_display_label(log.model) if log.model_id else ""
    return StageAttempt(
        attempt_number=attempt_number,
        created_at=log.created_at,
        latency_ms=log.latency_ms,
        requested_model_label=requested_label,
        resolved_model_id=log.resolved_model_id,
        finish_reason=log.finish_reason,
        selection_source=log.selection_source,
        reasoning_effort=log.reasoning_effort,
        input_tokens=log.input_tokens,
        cached_input_tokens=log.cached_input_tokens,
        output_tokens=log.output_tokens,
        total_tokens=log.total_tokens,
        retry_count=log.retry_count,
        is_error=bool(log.error_category),
        error_category=log.error_category,
        error_message=log.error_message,
        error_guidance=ERROR_GUIDANCE.get(log.error_category, ""),
    )


def build_stage_card(stage: str, *, correlation_id: str | None = None) -> StageCard:
    """The one function every AJ/AC/AB inspection view calls to render a stage's card. Read-only --
    never executes a provider call, never mutates the registry.

    `correlation_id`, when given (the owning `JobApplication` id as a string -- see
    `llm_provider.types.NormalizedLLMRequest.correlation_id`), scopes `latest_attempt`/
    `attempt_count` to that one application's calls for this stage; without it (e.g. before any
    application exists yet), attempt history is empty by construction rather than guessing at a
    global "latest call for this stage," which could belong to a different application entirely."""
    assignment = (
        StageModelAssignment.objects.select_related("model__provider").filter(stage=stage).first()
    )
    default_model = assignment.model if assignment else None
    default_reasoning_effort = assignment.default_reasoning_effort if assignment else ""

    logs_qs = LLMCallLog.objects.select_related("model", "model__provider").filter(stage=stage)
    if correlation_id:
        logs_qs = logs_qs.filter(correlation_id=correlation_id)
    else:
        logs_qs = logs_qs.none()
    attempt_count = logs_qs.count()
    latest_log = logs_qs.order_by("-created_at", "-id").first()
    latest_attempt = _to_attempt(latest_log, attempt_count) if latest_log is not None else None

    # "Effective" model/reasoning: whatever the most recent attempt for *this application* actually
    # used, when one exists -- otherwise the stage's own configured default (nothing has run yet,
    # so the default is exactly what the next run would use absent any per-run override, which the
    # view itself is responsible for re-resolving server-side at execution time, never trusted from
    # this display-only card).
    if latest_log is not None:
        effective_model = latest_log.model
        effective_reasoning_effort = latest_log.reasoning_effort
    else:
        effective_model = default_model
        effective_reasoning_effort = default_reasoning_effort

    model_choices = [
        (model.pk, model_display_label(model)) for model in eligible_models_for_stage(stage)
    ]
    from ..models import ReasoningEffort

    reasoning_choices = list(ReasoningEffort.choices)

    return StageCard(
        stage=stage,
        stage_label=StageModelAssignment.Stage(stage).label,
        default_assignment=assignment,
        default_model=default_model,
        default_reasoning_effort=default_reasoning_effort,
        default_is_free=is_free_model(default_model),
        default_provider_label=provider_group_label(default_model.provider) if default_model else "",
        default_endpoint_type=endpoint_type_label(default_model.provider if default_model else None),
        default_credential_configured=credential_configured(
            default_model.provider if default_model else None
        ),
        effective_model=effective_model,
        effective_reasoning_effort=effective_reasoning_effort,
        effective_is_free=is_free_model(effective_model),
        effective_provider_label=(
            provider_group_label(effective_model.provider) if effective_model else ""
        ),
        effective_endpoint_type=endpoint_type_label(
            effective_model.provider if effective_model else None
        ),
        effective_credential_configured=credential_configured(
            effective_model.provider if effective_model else None
        ),
        max_output_tokens=(assignment.max_output_tokens if assignment else None)
        or (default_model.max_output_tokens if default_model else None),
        model_choices=model_choices,
        reasoning_choices=reasoning_choices,
        latest_attempt=latest_attempt,
        attempt_count=attempt_count,
    )
