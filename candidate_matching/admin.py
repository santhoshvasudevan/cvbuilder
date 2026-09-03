from django.contrib import admin

from .models import FitAssessment, RequirementAssessment


class RequirementAssessmentInline(admin.TabularInline):
    model = RequirementAssessment
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in RequirementAssessment._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FitAssessment)
class FitAssessmentAdmin(admin.ModelAdmin):
    list_display = ["id", "job_application", "version", "based_on_jra", "created_at"]
    list_filter = ["created_at"]
    readonly_fields = [field.name for field in FitAssessment._meta.fields]
    inlines = [RequirementAssessmentInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
