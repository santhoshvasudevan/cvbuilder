"""Registry and audit models for the LLM provider abstraction (requirements.md Sec 9).

Pipeline code never talks to a provider SDK directly -- it goes through the adapter interface
in llm_provider.adapters, routed via StageModelAssignment. This module owns only the durable
state: which providers/models exist, which model handles which stage, and the audit ledger of
every call made (docs/ARCHITECTURE.md Sec 3/Sec 4).
"""

from django.db import models


class LLMProvider(models.Model):
    """One configured LLM backend (LLM-004). The credential *value* is never stored here --
    only the name of the environment variable that holds it (NFR-003)."""

    class ProviderType(models.TextChoices):
        OPENAI = "OPENAI", "OpenAI"
        NVIDIA_NIM = "NVIDIA_NIM", "NVIDIA NIM"
        GEMINI = "GEMINI", "Gemini"
        FAKE = "FAKE", "Fake (test-only)"

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
    supports_structured_output = models.BooleanField(default=False)
    supports_streaming = models.BooleanField(default=False)
    supports_reasoning = models.BooleanField(default=False)
    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
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
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.stage} -> {self.model}"


class LLMCallLog(models.Model):
    """One row per LLM call -- the audit ledger (LLM-010). Never a cache, never read from to
    skip a call. Token-first per D-008: no dollar-cost field at this milestone."""

    provider = models.ForeignKey(LLMProvider, on_delete=models.PROTECT, related_name="call_logs")
    model = models.ForeignKey(LLMModel, on_delete=models.PROTECT, related_name="call_logs")
    stage = models.CharField(max_length=20, choices=StageModelAssignment.Stage.choices)

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

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        timestamp = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
        return f"{self.stage} via {self.provider.name}/{self.model.model_id} @ {timestamp}"
