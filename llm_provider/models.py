"""Registry and audit models for the LLM provider abstraction (requirements.md Sec 9).

Pipeline code never talks to a provider SDK directly -- it goes through the adapter interface
in llm_provider.adapters, routed via StageModelAssignment. This module owns only the durable
state: which providers/models exist, which model handles which stage, and the audit ledger of
every call made (docs/ARCHITECTURE.md Sec 3/Sec 4).
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# Bounds for `StageModelAssignment.read_timeout_seconds` (2026-09-05, configurable per-stage
# timeout). `MIN_READ_TIMEOUT_SECONDS` rejects a zero/negative value that would make every call
# fail instantly; `MAX_READ_TIMEOUT_SECONDS` rejects an excessive value that would make a single
# stuck call block far longer than any interactive/local-operator workflow should tolerate --
# with `RetryPolicy.max_attempts=3` (`llm_provider/retry.py`), a stage configured at this ceiling
# has a worst-case wall-clock wait (all three attempts time out) of
# `3 * MAX_READ_TIMEOUT_SECONDS + backoff (~3s)` -- about 15 minutes. Both bounds are enforced by
# `full_clean()` here (admin/service-layer saves) and re-checked defensively in
# `llm_provider.adapters.get_adapter_for_stage` for a row that reached the database through a path
# that bypassed `full_clean()` (e.g. a fixture or script), mirroring the existing
# `max_output_tokens`/`InvalidStageBudgetError` defense-in-depth pattern.
MIN_READ_TIMEOUT_SECONDS = 1
MAX_READ_TIMEOUT_SECONDS = 300


class LLMProvider(models.Model):
    """One configured LLM backend (LLM-004). The credential *value* is never stored here --
    only the name of the environment variable that holds it (NFR-003)."""

    class ProviderType(models.TextChoices):
        OPENAI = "OPENAI", "OpenAI"
        NVIDIA_NIM = "NVIDIA_NIM", "NVIDIA NIM"
        GEMINI = "GEMINI", "Gemini"
        OPENROUTER = "OPENROUTER", "OpenRouter"
        FAKE = "FAKE", "Fake (test-only)"

    class DataCollectionPolicy(models.TextChoices):
        """Constrained values for OpenRouter's `provider.data_collection` routing directive
        (2026-09-04, OpenRouter provider integration) -- never arbitrary JSON. Read only by
        `OpenRouterAdapter`; harmless/unused for every other provider type. Defaults to `DENY`
        so personal resume/Candidate Memory content is never routed to an endpoint that logs or
        trains on request data unless an operator deliberately opts a provider row in."""

        DENY = "DENY", "Deny (no endpoint that collects/trains on request data)"
        ALLOW = "ALLOW", "Allow"

    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices)
    base_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Overrides the adapter's default base URL. Leave blank to use the default.",
    )
    credential_env_var = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Name of the environment variable holding this provider's API credential. "
            "The credential value itself is never stored in the database (NFR-003)."
        ),
    )
    data_collection_policy = models.CharField(
        max_length=10,
        choices=DataCollectionPolicy.choices,
        default=DataCollectionPolicy.DENY,
        help_text=(
            "OpenRouter-specific: the privacy value sent as this provider's request-level "
            "`provider.data_collection` routing directive. Ignored by every other provider type."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Whether any model under this provider may be selected for a new "
            "StageModelAssignment or offered as a per-run override in the AJ/AC/AB model-selection "
            "UI (2026-09-07, per-run model selection / D-038 default-configuration broadening). "
            "Mirrors LLMModel.is_active at the provider level -- never deletes a provider row or "
            "its LLMModel/LLMCallLog history; a retired provider is deactivated instead, which "
            "removes every one of its models from eligibility (llm_provider.services.eligibility) "
            "without needing to flip each model individually. Existing rows default to True, so "
            "this migration changes no provider's current behavior on its own."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class LLMModel(models.Model):
    """One usable model under a provider (LLM-005), with capability flags the adapter layer
    consults before assuming a feature is supported."""

    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name="models")
    model_id = models.CharField(
        max_length=200, help_text="Provider-specific model identifier, e.g. 'gpt-4o-mini'."
    )
    display_name = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Optional operator-friendly label shown in the AJ/AC/AB model-selection UI in place "
            "of the raw model_id (2026-09-07, per-run model selection). Blank means the UI falls "
            "back to showing '<provider name> -- <model_id>' -- this field exists only to improve "
            "a selector's readability (e.g. 'Free Models Router' for 'openrouter/free'), never to "
            "change routing or eligibility, which are always keyed off model_id/provider."
        ),
    )
    supports_structured_output = models.BooleanField(default=False)
    supports_streaming = models.BooleanField(default=False)
    supports_reasoning = models.BooleanField(default=False)
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Whether this model may still be selected for a new StageModelAssignment or the "
            "smoke-test harness's default-model selection (2026-09-07, OpenRouter Free Router "
            "migration). Never deletes a model row or its LLMCallLog history -- a retired model "
            "(e.g. a superseded OpenRouter slug) is set to False instead, preserving referential "
            "integrity and audit history while making rollback (flip this back to True, or "
            "reassign a stage to it) a plain registry edit rather than a re-creation. "
            "`get_adapter_for_stage` refuses to route a live call to an inactive model "
            "(`InactiveModelAssignedError`) so nothing can silently keep calling a deactivated "
            "model just because an old StageModelAssignment row still points at it."
        ),
    )
    max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            "Provider/model capability ceiling -- the most output tokens this model can ever "
            "return, regardless of which stage is calling it. Never a per-stage request budget "
            "(see StageModelAssignment.max_output_tokens for that); a stage assignment's own "
            "budget may never exceed this value."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "model_id"], name="unique_provider_model_id"),
        ]

    def __str__(self) -> str:
        return f"{self.provider.name}/{self.model_id}"


class StageModelAssignment(models.Model):
    """Maps a pipeline stage to the LLMModel currently handling it (LLM-001/LLM-006).
    Changing which row a stage points at is the *only* action needed to move a stage to a
    different provider/model -- no pipeline code reads provider identity any other way."""

    class Stage(models.TextChoices):
        MEMORY_BUILD = "MEMORY_BUILD", "Memory Build"
        AJ_ANALYZE = "AJ_ANALYZE", "Agent Jobber - Analyze"
        AC_NORMALIZE = (
            "AC_NORMALIZE",
            "Agent Candidate - Requirement Normalization (bounded query expansion, D-015/D-020)",
        )
        AC_RANK = "AC_RANK", "Agent Candidate - Relevance Ranking (D-015 bounded step)"
        AC_MATCH = "AC_MATCH", "Agent Candidate - Match"
        AB_BUILD = "AB_BUILD", "Agent Builder - Build"

    stage = models.CharField(max_length=20, choices=Stage.choices, unique=True)
    model = models.ForeignKey(LLMModel, on_delete=models.PROTECT, related_name="stage_assignments")
    max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            "Optional per-stage request budget (2026-09-04, stage-specific token budgets). When "
            "set, this stage's requests use this value instead of the assigned model's own "
            "max_output_tokens; it must never exceed that model capability. Leave blank to use "
            "the model's capability (or a conservative built-in default if the model doesn't "
            "declare one) -- the previous, single-budget-per-model behavior."
        ),
    )
    read_timeout_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(MIN_READ_TIMEOUT_SECONDS),
            MaxValueValidator(MAX_READ_TIMEOUT_SECONDS),
        ],
        help_text=(
            "Optional per-stage HTTP read-timeout override, in seconds (2026-09-05, configurable "
            "per-stage timeout). When set, this stage's provider requests use this value instead "
            f"of the built-in default (see `llm_provider.adapters.base."
            f"DEFAULT_READ_TIMEOUT_SECONDS`). Must be between {MIN_READ_TIMEOUT_SECONDS} and "
            f"{MAX_READ_TIMEOUT_SECONDS} seconds inclusive. This is the read timeout only -- the "
            "connect timeout is a separate, fixed, non-configurable value shared by every stage "
            "(`llm_provider.adapters.base.DEFAULT_CONNECT_TIMEOUT_SECONDS`), never the same "
            "'one combined number' the pre-2026-09-05 code passed to every adapter's HTTP call. "
            "Leave blank to use the built-in default."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.max_output_tokens is not None:
            try:
                model_capability = self.model.max_output_tokens
            except LLMModel.DoesNotExist:
                model_capability = None
            if model_capability is not None and self.max_output_tokens > model_capability:
                raise ValidationError(
                    {
                        "max_output_tokens": (
                            f"Stage budget ({self.max_output_tokens}) exceeds the assigned model "
                            f"{self.model}'s capability ({model_capability}) -- lower the stage "
                            "budget or raise the model's own max_output_tokens first."
                        )
                    }
                )
        if self.read_timeout_seconds is not None and not (
            MIN_READ_TIMEOUT_SECONDS <= self.read_timeout_seconds <= MAX_READ_TIMEOUT_SECONDS
        ):
            # PositiveIntegerField's own MinValueValidator/MaxValueValidator (declared above)
            # already enforce this during a normal `full_clean()` -- this branch only guards
            # against a value that reached this method by some other write path;  ValidationError
            # is still the right way to fail closed here rather than silently clamping.
            raise ValidationError(
                {
                    "read_timeout_seconds": (
                        f"read_timeout_seconds ({self.read_timeout_seconds}) must be between "
                        f"{MIN_READ_TIMEOUT_SECONDS} and {MAX_READ_TIMEOUT_SECONDS} seconds "
                        "inclusive."
                    )
                }
            )

    def __str__(self) -> str:
        return f"{self.stage} -> {self.model}"


class LLMCallLog(models.Model):
    """One row per LLM call -- the audit ledger (LLM-010). Never a cache, never read from to
    skip a call. Token-first per D-008: no dollar-cost field at this milestone."""

    provider = models.ForeignKey(LLMProvider, on_delete=models.PROTECT, related_name="call_logs")
    model = models.ForeignKey(LLMModel, on_delete=models.PROTECT, related_name="call_logs")
    stage = models.CharField(max_length=20, choices=StageModelAssignment.Stage.choices)

    resolved_model_id = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "The actual upstream model id the provider reports it used to serve this call "
            "(2026-09-07, requested-vs-resolved audit -- relevant for a virtual router such as "
            "OpenRouter's Free Models Router, whose requested slug, e.g. 'openrouter/free', is "
            "not the model that actually generated the response). Parsed only from the "
            "response's own documented top-level 'model' field when present -- never guessed, "
            "never overwriting `model` (the requested LLMModel FK, which always stays exact). "
            "Blank when the provider did not report one (e.g. an error response, or a provider "
            "whose response shape does not include it)."
        ),
    )
    finish_reason = models.CharField(
        max_length=50,
        blank=True,
        help_text=(
            "The provider's reported finish_reason for a successful call (e.g. 'stop', "
            "'length'). Blank for an error row or when the provider did not report one. A "
            "truncated response (finish_reason=length) is never stored as a success row in the "
            "first place (llm_provider/adapters/openai.py's parser fails it closed as "
            "CONFIGURATION before reaching here) -- this field records the reason for the rows "
            "that do succeed, for diagnostic grouping."
        ),
    )
    correlation_id = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Optional caller-supplied correlation/workflow identifier (e.g. a JobApplication "
            "id), carried through from NormalizedLLMRequest.correlation_id when the caller sets "
            "one. Blank when not supplied -- no pipeline call site is required to set this."
        ),
    )

    class SelectionSource(models.TextChoices):
        """Distinguishes a call routed through the stage's configured global
        StageModelAssignment default from one routed through an explicit per-run operator
        override (2026-09-07, per-run model selection). Blank for any row written before this
        field existed -- never backfilled/guessed for historical rows."""

        DEFAULT = "DEFAULT", "Stage default (StageModelAssignment)"
        OVERRIDE = "OVERRIDE", "Explicit per-run override"

    selection_source = models.CharField(
        max_length=10,
        choices=SelectionSource.choices,
        blank=True,
        help_text=(
            "Whether `model` (the requested LLMModel FK above) was resolved from the stage's "
            "configured global default or from an explicit per-run operator override. Set by "
            "`llm_provider.adapters.get_adapter_for_stage`/`BaseLLMAdapter`; blank for any "
            "LLMCallLog row written before this field existed."
        ),
    )

    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cached_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)

    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    error_category = models.CharField(max_length=20, blank=True)
    error_message = models.TextField(
        blank=True, help_text="Sanitized message only -- never raw provider request/response content."
    )
    rate_limit_diagnostics = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Sanitized rate-limit diagnostic metadata (2026-09-05, OpenRouter 429 diagnostics). "
            "Populated only for RATE_LIMIT errors where the adapter could extract it from "
            "documented, non-secret response headers/metadata -- a small fixed set of keys "
            "(retry_after_seconds, limit, remaining, reset, source, upstream_provider), never a "
            "raw response body or headers wholesale. Null for every other error/success row and "
            "for a RATE_LIMIT row where no such metadata was present."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        timestamp = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
        return f"{self.stage} via {self.provider.name}/{self.model.model_id} @ {timestamp}"


class OpenRouterKeyStatus(models.Model):
    """The last known result of an explicit, operator-triggered OpenRouter key-status check
    (`GET /api/v1/key`, 2026-09-05) -- never queried automatically (not during inference, not on
    every page load, not from a retry loop). One row per `LLMProvider` (a provider could
    theoretically have more than one OpenRouter row, e.g. two different keys/accounts, each
    tracked independently). Every field here is a documented, non-secret quota/limit field from
    OpenRouter's own response -- the credential value itself is never stored, displayed, hashed,
    or logged anywhere in this model or the service that populates it."""

    provider = models.OneToOneField(
        LLMProvider, on_delete=models.CASCADE, related_name="openrouter_key_status"
    )
    fetched_at = models.DateTimeField(auto_now=True)
    success = models.BooleanField(default=False)
    label = models.CharField(max_length=200, blank=True)
    is_free_tier = models.BooleanField(null=True, blank=True)
    limit = models.FloatField(null=True, blank=True)
    limit_remaining = models.FloatField(null=True, blank=True)
    limit_reset = models.CharField(max_length=50, blank=True)
    usage = models.FloatField(null=True, blank=True)
    usage_daily = models.FloatField(null=True, blank=True)
    usage_weekly = models.FloatField(null=True, blank=True)
    usage_monthly = models.FloatField(null=True, blank=True)
    error_message = models.CharField(
        max_length=300,
        blank=True,
        help_text="Sanitized failure reason from the last refresh attempt, if it failed. Blank on success.",
    )

    def __str__(self) -> str:
        return f"OpenRouter key status for {self.provider.name} @ {self.fetched_at}"
