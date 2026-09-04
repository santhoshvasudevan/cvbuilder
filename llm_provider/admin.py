from django.contrib import admin

from .models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "provider_type", "credential_env_var", "created_at")
    list_filter = ("provider_type",)


@admin.register(LLMModel)
class LLMModelAdmin(admin.ModelAdmin):
    list_display = (
        "model_id",
        "provider",
        "supports_structured_output",
        "supports_streaming",
        "supports_reasoning",
        "max_output_tokens",
    )
    list_filter = ("provider", "supports_structured_output")


@admin.register(StageModelAssignment)
class StageModelAssignmentAdmin(admin.ModelAdmin):
    list_display = ("stage", "model", "max_output_tokens", "updated_at")


@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "stage",
        "provider",
        "model",
        "total_tokens",
        "latency_ms",
        "retry_count",
        "error_category",
    )
    list_filter = ("stage", "provider", "error_category")
    readonly_fields = [f.name for f in LLMCallLog._meta.fields]

    def has_add_permission(self, request):
        # Audit ledger: only ever written by the adapter call path, never created by hand.
        return False

    def has_change_permission(self, request, obj=None):
        return False
