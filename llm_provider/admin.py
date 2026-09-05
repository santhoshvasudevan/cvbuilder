from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone

from .adapters.base import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from .models import LLMCallLog, LLMModel, LLMProvider, OpenRouterKeyStatus, StageModelAssignment
from .openrouter_key_status import fetch_openrouter_key_status

_RECENT_RATE_LIMIT_LOG_LIMIT = 25


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider_type",
        "credential_env_var",
        "data_collection_policy",
        "created_at",
    )
    list_filter = ("provider_type",)
    change_list_template = "admin/llm_provider/llmprovider/change_list.html"

    def get_urls(self):
        custom_urls = [
            path(
                "openrouter-diagnostics/",
                self.admin_site.admin_view(self.openrouter_diagnostics_view),
                name="llm_provider_openrouter_diagnostics",
            ),
            path(
                "openrouter-diagnostics/refresh/<int:provider_id>/",
                self.admin_site.admin_view(self.openrouter_diagnostics_refresh_view),
                name="llm_provider_openrouter_diagnostics_refresh",
            ),
        ]
        return custom_urls + super().get_urls()

    def openrouter_diagnostics_view(self, request):
        """Operator-facing OpenRouter diagnostics (2026-09-05, D-029): the smallest UI that
        satisfies the task's requirements, integrated directly into the existing admin rather than
        a new app/route namespace. Shows only `llm_provider`-owned data (providers, models,
        stage assignments, call-log rows, key-status rows) -- never any Candidate Memory,
        job-description, resume, employer, or application content, none of which this app's models
        reference at all."""
        openrouter_providers = list(
            LLMProvider.objects.filter(provider_type=LLMProvider.ProviderType.OPENROUTER).order_by("name")
        )
        key_statuses = {
            status.provider_id: status
            for status in OpenRouterKeyStatus.objects.filter(
                provider__in=openrouter_providers
            ).select_related("provider")
        }
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        provider_rows = []
        for provider in openrouter_providers:
            locally_observed_today = LLMCallLog.objects.filter(
                provider=provider, created_at__gte=today_start
            ).count()
            provider_rows.append(
                {
                    "provider": provider,
                    "status": key_statuses.get(provider.id),
                    "locally_observed_today": locally_observed_today,
                    "refresh_url": reverse(
                        "admin:llm_provider_openrouter_diagnostics_refresh", args=[provider.id]
                    ),
                }
            )
        recent_rate_limits = (
            LLMCallLog.objects.filter(
                provider__provider_type=LLMProvider.ProviderType.OPENROUTER,
                error_category="RATE_LIMIT",
            )
            .select_related("provider", "model")
            .order_by("-created_at")[:_RECENT_RATE_LIMIT_LOG_LIMIT]
        )
        context = {
            **self.admin_site.each_context(request),
            "title": "OpenRouter diagnostics",
            "provider_rows": provider_rows,
            "recent_rate_limits": recent_rate_limits,
            "recent_rate_limit_limit": _RECENT_RATE_LIMIT_LOG_LIMIT,
        }
        return render(request, "admin/llm_provider/openrouter_diagnostics.html", context)

    def openrouter_diagnostics_refresh_view(self, request, provider_id):
        """The one explicit operator action that ever calls the OpenRouter key-status endpoint --
        never triggered by a page load, an inference call, or a retry. POST-only, admin-CSRF
        protected (the same protection every other admin form on this site uses)."""
        provider = self.get_object(request, provider_id)
        if provider is None or provider.provider_type != LLMProvider.ProviderType.OPENROUTER:
            messages.error(request, "Unknown or non-OpenRouter provider.")
            return redirect("admin:llm_provider_openrouter_diagnostics")
        if request.method != "POST":
            messages.error(request, "Refresh must be requested via POST.")
            return redirect("admin:llm_provider_openrouter_diagnostics")

        result = fetch_openrouter_key_status(provider)
        OpenRouterKeyStatus.objects.update_or_create(
            provider=provider,
            defaults={
                "success": result.success,
                "label": result.label or "",
                "is_free_tier": result.is_free_tier,
                "limit": result.limit,
                "limit_remaining": result.limit_remaining,
                "limit_reset": result.limit_reset or "",
                "usage": result.usage,
                "usage_daily": result.usage_daily,
                "usage_weekly": result.usage_weekly,
                "usage_monthly": result.usage_monthly,
                "error_message": result.error_message,
            },
        )
        if result.success:
            messages.success(request, f"Refreshed OpenRouter status for '{provider.name}'.")
        else:
            messages.warning(
                request, f"Could not refresh OpenRouter status for '{provider.name}': {result.error_message}"
            )
        return redirect("admin:llm_provider_openrouter_diagnostics")


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
    list_display = (
        "stage",
        "model",
        "max_output_tokens",
        "read_timeout_seconds",
        "effective_timeout_display",
        "updated_at",
    )
    readonly_fields = ("effective_timeout_display",)

    @admin.display(description="Effective (connect, read) timeout")
    def effective_timeout_display(self, obj: StageModelAssignment) -> str:
        read_timeout = obj.read_timeout_seconds or DEFAULT_READ_TIMEOUT_SECONDS
        configured = (
            " (configured override)" if obj.read_timeout_seconds is not None else " (built-in default)"
        )
        return f"({DEFAULT_CONNECT_TIMEOUT_SECONDS}s connect, {read_timeout}s read{configured})"


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
        "rate_limit_diagnostics",
    )
    list_filter = ("stage", "provider", "error_category")
    readonly_fields = [f.name for f in LLMCallLog._meta.fields]

    def has_add_permission(self, request):
        # Audit ledger: only ever written by the adapter call path, never created by hand.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OpenRouterKeyStatus)
class OpenRouterKeyStatusAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "fetched_at",
        "success",
        "is_free_tier",
        "limit",
        "limit_remaining",
        "limit_reset",
        "usage",
    )
    readonly_fields = [f.name for f in OpenRouterKeyStatus._meta.fields]

    def has_add_permission(self, request):
        # Only ever written by the explicit "Refresh OpenRouter status" admin action.
        return False

    def has_change_permission(self, request, obj=None):
        return False
