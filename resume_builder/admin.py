from django.contrib import admin

from .models import ResumeDraft, ResumeElement


class ResumeElementInline(admin.TabularInline):
    model = ResumeElement
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in ResumeElement._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ResumeDraft)
class ResumeDraftAdmin(admin.ModelAdmin):
    list_display = ["id", "job_application", "version", "recommended_title", "confirmed_at", "created_at"]
    list_filter = ["created_at", "confirmed_at"]
    readonly_fields = [field.name for field in ResumeDraft._meta.fields]
    inlines = [ResumeElementInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
