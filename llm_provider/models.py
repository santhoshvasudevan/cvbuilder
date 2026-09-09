"""Registry and audit models for the LLM provider abstraction.

docs/ARCHITECTURE.md Section 12 (LLM Control Plane) and Section 13 (LLMCallLog) define the
canonical schema this module implements; requirements.md Section 16 (LLM-001..012) is the
requirement source. `llm_provider` owns `LLMProvider`, `LLMModel`, `StageModelAssignment`, and
`LLMCallLog` only -- it never owns `StageRun`/`JobApplicationStageState` (V2-D022) and never
imports a provider SDK directly (only `llm_provider.adapters` does, from M2.x/M4 onward).

Pipeline/domain apps never talk to a provider SDK directly and never read a real credential --
they go through `StageModelAssignment` (registry-driven routing, LLM-001/LLM-006) and
`llm_provider.adapters.get_adapter_for_stage()`.
"""

from __future__ import annotations

import math

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from job_applications.models import LLM_CAPABLE_STAGES, StageIdentifier

from .errors import LLMErrorCategory

# Canonical permitted temperature range (V2-D042) -- matches the range OpenAI/Gemini/OpenRouter
# all document for their `temperature` request parameter. A per-model ceiling narrower than this
# (e.g. a model that only supports up to 1.0) is not currently represented -- only whether
# temperature is supported at all (`LLMModel.supports_temperature`) and, independently, whether
# it may be combined with a non-NONE reasoning level (`LLMModel.supports_temperature_with_reasoning`).
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0

# The LLM-capable subset of the canonical stage vocabulary (job_applications.StageIdentifier,
# V2-D022) is the only set of stage values StageModelAssignment/LLMCallLog may ever store --
# deterministic/workflow stages (CANDIDATE_CONTEXT_BUILD, GATE_1, ...) never have a
# StageModelAssignment row and never appear in LLMCallLog.
LLM_CAPABLE_STAGE_CHOICES = [
    (stage.value, stage.label) for stage in StageIdentifier if stage in LLM_CAPABLE_STAGES
]


class ReasoningLevel(models.TextChoices):
    """Canonical reasoning-capability/request scale (V2-D034) -- the single source of truth for
    what a model supports (`LLMModel.supported_reasoning_levels`) and what a stage/call requests
    (`StageModelAssignment.default_reasoning_level`, `StageRun.reasoning_level`). There is no
    independently-stored `supports_reasoning` boolean anywhere in this codebase that could drift
    from this set -- see `LLMModel.supports_reasoning` below, which is a derived property only.
    """

    NONE = "NONE", "None (reasoning disabled)"
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    XHIGH = "XHIGH", "Extra high"


def _default_supported_reasoning_levels() -> list[str]:
    # V2-D034: "A model with no reasoning capability stores {NONE} ... there is no
    # independently-stored supports_reasoning boolean." NONE is always a member of this set --
    # every model can be called with reasoning explicitly disabled.
    return [ReasoningLevel.NONE]


class LLMProvider(models.Model):
    """One configured LLM backend (LLM-002/LLM-003). The credential *value* is never stored
    here -- only the name of the environment variable that holds it (LLM-012)."""

    class AdapterType(models.TextChoices):
        OPENAI = "OPENAI", "OpenAI"
        NVIDIA_NIM = "NVIDIA_NIM", "NVIDIA NIM"
        GEMINI = "GEMINI", "Gemini"
        OPENROUTER = "OPENROUTER", "OpenRouter"
        FAKE = "FAKE", "Fake (test-only, no network)"

    name = models.CharField(max_length=100, unique=True)
    adapter_type = models.CharField(max_length=20, choices=AdapterType.choices)
    base_url = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Overrides the adapter's default base URL (LLM-009). Blank uses the adapter default.",
    )
    credential_env_variable = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Name of the environment variable holding this provider's API credential. The "
            "credential value itself is never stored in the database (LLM-012). Required for "
            "every adapter_type except FAKE."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=(
            "Whether any model under this provider may be selected for a new "
            "StageModelAssignment or a manual/console call. Never deletes a provider row or its "
            "LLMModel/LLMCallLog history -- a retired provider is disabled instead."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class LLMModel(models.Model):
    """One usable model under a provider (LLM-004), with capability flags the adapter/validation
    layer consults before assuming a feature is supported."""

    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name="models")
    model_identifier = models.CharField(
        max_length=200, help_text="Provider-specific model identifier, e.g. 'gpt-4o-mini'."
    )
    supports_structured_output = models.BooleanField(default=False)
    supported_reasoning_levels = ArrayField(
        models.CharField(max_length=10, choices=ReasoningLevel.choices),
        default=_default_supported_reasoning_levels,
        help_text=(
            "Canonical source of truth for this model's reasoning capability (V2-D034). Always "
            "includes NONE. A model with no real reasoning capability stores only [NONE]."
        ),
    )
    max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Provider/model capability ceiling -- the most output tokens this model can ever "
            "return. A StageModelAssignment's default_max_output_tokens may never exceed this."
        ),
    )
    supports_temperature = models.BooleanField(
        default=True,
        help_text=(
            "Whether this model accepts a `temperature` request parameter at all (V2-D042). "
            "Explicit model capability metadata, not inferred from model_identifier/provider."
        ),
    )
    supports_temperature_with_reasoning = models.BooleanField(
        default=True,
        help_text=(
            "Whether `temperature` may be combined with a non-NONE reasoning_level for this "
            "model (V2-D042) -- some reasoning-tier models reject temperature entirely once "
            "reasoning is enabled. Only consulted when supports_temperature is True and the "
            "requested reasoning_level != NONE; irrelevant otherwise."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=(
            "Whether this model may still be selected for a new StageModelAssignment or a "
            "manual/console call. Never deletes a model row or its LLMCallLog history -- a "
            "retired model is disabled instead, preserving referential/audit integrity."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "model_identifier"], name="unique_provider_model_identifier"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider.name}/{self.model_identifier}"

    @property
    def supports_reasoning(self) -> bool:
        """Derived helper only (V2-D034) -- never independently stored, never authoritative.
        True iff this model supports any reasoning level beyond NONE."""
        levels = set(self.supported_reasoning_levels or [])
        return bool(levels - {ReasoningLevel.NONE})

    def clean(self):
        super().clean()
        levels = self.supported_reasoning_levels or []
        valid_values = {choice for choice, _ in ReasoningLevel.choices}
        invalid = set(levels) - valid_values
        if invalid:
            raise ValidationError(
                {"supported_reasoning_levels": f"Invalid reasoning level(s): {sorted(invalid)}."}
            )
        if ReasoningLevel.NONE not in levels:
            raise ValidationError(
                {
                    "supported_reasoning_levels": (
                        "Must always include NONE -- every model can be called with reasoning "
                        "explicitly disabled (V2-D034)."
                    )
                }
            )


class StageModelAssignment(models.Model):
    """Maps an LLM-capable pipeline stage to the LLMModel currently handling it (LLM-005/006).
    Changing which row a stage points at is the *only* action needed to move a stage to a
    different provider/model -- no pipeline code reads provider identity any other way.

    `stage` is restricted to the LLM-capable subset of the canonical stage vocabulary owned by
    `job_applications` (V2-D022) -- deterministic/workflow stages never get a row here.
    """

    stage = models.CharField(max_length=40, choices=LLM_CAPABLE_STAGE_CHOICES, unique=True)
    model = models.ForeignKey(LLMModel, on_delete=models.PROTECT, related_name="stage_assignments")
    default_reasoning_level = models.CharField(
        max_length=10, choices=ReasoningLevel.choices, default=ReasoningLevel.NONE
    )
    default_max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank to use the assigned model's own max_output_tokens capability.",
    )
    default_temperature = models.FloatField(
        null=True,
        blank=True,
        help_text="Leave blank where the model/reasoning combination does not accept temperature.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.stage} -> {self.model}"

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        try:
            model = self.model
        except LLMModel.DoesNotExist:
            model = None
        if model is not None:
            if self.default_reasoning_level not in (model.supported_reasoning_levels or []):
                errors["default_reasoning_level"] = (
                    f"'{self.default_reasoning_level}' is not in {model} "
                    f"supported_reasoning_levels={model.supported_reasoning_levels!r} (V2-D034)."
                )
            if (
                self.default_max_output_tokens is not None
                and model.max_output_tokens is not None
                and self.default_max_output_tokens > model.max_output_tokens
            ):
                errors["default_max_output_tokens"] = (
                    f"{self.default_max_output_tokens} exceeds {model} capability of "
                    f"{model.max_output_tokens}."
                )
            if self.default_temperature is not None:
                if not math.isfinite(self.default_temperature) or not (
                    MIN_TEMPERATURE <= self.default_temperature <= MAX_TEMPERATURE
                ):
                    errors["default_temperature"] = (
                        f"{self.default_temperature} is outside the permitted range "
                        f"[{MIN_TEMPERATURE}, {MAX_TEMPERATURE}]."
                    )
                elif not model.supports_temperature:
                    errors["default_temperature"] = f"{model} does not support a temperature parameter."
                elif (
                    self.default_reasoning_level != ReasoningLevel.NONE
                    and not model.supports_temperature_with_reasoning
                ):
                    errors["default_temperature"] = (
                        f"{model} does not accept temperature combined with reasoning_level="
                        f"'{self.default_reasoning_level}'."
                    )
        if errors:
            raise ValidationError(errors)


class LLMCallLog(models.Model):
    """One row per LLM call attempt -- the audit ledger (LLM-010/LLM-011, docs/ARCHITECTURE.md
    Section 13). Never a cache, never read from to skip a call. No prompt/response body field
    exists anywhere on this model -- only token counts, timing, and a sanitized error category
    (requirements.md LLM-012 / docs/ENGINEERING_RULES.md Section G).

    `stage_run` is a nullable FK to `job_applications.StageRun` (V2-D022) -- nullable to support
    standalone/manual smoke-test calls that are not tied to any StageRun. This is the one
    deliberate, accepted exception to "llm_provider has no dependency on pipeline apps".
    """

    stage_run = models.ForeignKey(
        "job_applications.StageRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="llm_call_logs",
    )
    stage = models.CharField(max_length=40, choices=LLM_CAPABLE_STAGE_CHOICES)

    requested_provider = models.ForeignKey(
        LLMProvider, on_delete=models.PROTECT, related_name="call_logs"
    )
    requested_model = models.ForeignKey(LLMModel, on_delete=models.PROTECT, related_name="call_logs")
    resolved_model_identifier = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text=(
            "Only set when a provider reports a concrete model identifier different from "
            "requested_model.model_identifier (e.g. an alias/router resolution). Blank when no "
            "call has been made or the provider echoed the same identifier -- this app never "
            "performs automatic provider/model fallback, so this is provenance, not routing."
        ),
    )

    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cached_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)

    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    finish_reason = models.CharField(max_length=40, blank=True, default="")

    error_category = models.CharField(
        max_length=20, blank=True, default="", choices=[(c.value, c.value) for c in LLMErrorCategory]
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized message only -- never raw provider request/response content.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        timestamp = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
        model_label = f"{self.requested_provider.name}/{self.requested_model.model_identifier}"
        return f"{self.stage} via {model_label} @ {timestamp}"
