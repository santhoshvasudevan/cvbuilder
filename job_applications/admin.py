from django.contrib import admin

from .models import JobApplication, JobApplicationStageState, StageRun


@admin.register(JobApplication)
class JobApplicationAdmin(admin.ModelAdmin):
    list_display = ("employer", "job_title", "pipeline_phase", "application_outcome", "created_at")
    list_filter = ("pipeline_phase", "application_outcome", "source_type")
    search_fields = ("employer", "job_title")
    readonly_fields = ("pipeline_phase", "created_at", "updated_at")


@admin.register(StageRun)
class StageRunAdmin(admin.ModelAdmin):
    list_display = (
        "job_application",
        "stage",
        "status",
        "provider",
        "model",
        "reasoning_level",
        "created_at",
        "approved_at",
    )
    list_filter = ("stage", "status", "provider")
    readonly_fields = ("created_at",)


@admin.register(JobApplicationStageState)
class JobApplicationStageStateAdmin(admin.ModelAdmin):
    list_display = ("job_application", "stage", "current_stage_run", "approved_stage_run", "updated_at")
    list_filter = ("stage",)
    readonly_fields = ("updated_at",)
