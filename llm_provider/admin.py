"""Django admin registration -- the M2 registry/audit UI (docs/IMPLEMENTATION_PLAN.md M2:
"Django admin ... UI capabilities"). Shows provider/model configuration and the LLMCallLog audit
ledger only; interactive per-call run/approve/edit/rerun UI (requirements.md UI-001..006) is
pipeline-stage UI that depends on a real LLM-capable stage existing (M4 AJ_ANALYZE onward) --
out of M2 scope, since M2 intentionally implements no domain pipeline stage.

Never displays a credential value -- LLMProvider only ever stores the environment variable
*name*, never the secret itself, so `credential_env_variable` is safe to show as-is.
"""

from django.contrib import admin

from .models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "adapter_type", "credential_env_variable", "enabled", "created_at")
    list_filter = ("adapter_type", "enabled")
    search_fields = ("name",)


@admin.register(LLMModel)
class LLMModelAdmin(admin.ModelAdmin):
    list_display = (
        "model_identifier",
        "provider",
        "supports_structured_output",
        "supports_reasoning",
        "supported_reasoning_levels",
        "max_output_tokens",
        "enabled",
    )
    list_filter = ("provider", "supports_structured_output", "enabled")
    search_fields = ("model_identifier",)

    @admin.display(boolean=True)
    def supports_reasoning(self, obj: LLMModel) -> bool:
        return obj.supports_reasoning


@admin.register(StageModelAssignment)
class StageModelAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "stage",
        "model",
        "default_reasoning_level",
        "default_max_output_tokens",
        "default_temperature",
        "updated_at",
    )
    list_filter = ("stage",)


@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "stage",
        "stage_run",
        "requested_provider",
        "requested_model",
        "total_tokens",
        "latency_ms",
        "retry_count",
        "finish_reason",
        "error_category",
    )
    list_filter = ("stage", "requested_provider", "error_category")
    readonly_fields = [f.name for f in LLMCallLog._meta.fields]

    def has_add_permission(self, request):
        # Audit ledger: only ever written by the adapter call path, never created by hand.
        return False

    def has_change_permission(self, request, obj=None):
        return False
