from __future__ import annotations

from django.contrib import admin

from .models import JobApplication


@admin.register(JobApplication)
class JobApplicationAdmin(admin.ModelAdmin):
    """`pipeline_phase` is workflow-controlled -- it only ever changes via a service-level
    transition (`JobApplication.advance_to_analysis()` today; later milestones add their own), so
    it is read-only here rather than editable through the admin change form. `application_outcome`
    is intentionally the opposite: an operator-set field with no automated writer anywhere in the
    codebase (requirements.md Sec 17), so it stays admin-editable."""

    list_display = ("id", "pipeline_phase", "application_outcome", "current_jra", "created_at", "updated_at")
    list_filter = ("pipeline_phase", "application_outcome")
    readonly_fields = ("pipeline_phase", "current_jra", "created_at", "updated_at")
