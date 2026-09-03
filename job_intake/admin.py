from __future__ import annotations

from django.contrib import admin

from .models import JobRequirement, JobRequirementAnalysis


class JobRequirementInline(admin.TabularInline):
    model = JobRequirement
    extra = 0
    fields = ("requirement_id", "order", "category", "text", "source_context")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JobRequirementAnalysis)
class JobRequirementAnalysisAdmin(admin.ModelAdmin):
    """A completed JRA version is append-only (models.py) -- admin reflects that: no add
    permission (created only through the intake flow) and no change/delete permission, same
    honesty as candidate_memory's frozen-revision admin guards."""

    list_display = ("id", "job_application", "version", "source_type", "employer", "role_title",
                     "posting_language", "created_at")
    list_filter = ("source_type", "posting_language")
    search_fields = ("employer", "role_title", "source_url")
    inlines = [JobRequirementInline]
    readonly_fields = [f.name for f in JobRequirementAnalysis._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
