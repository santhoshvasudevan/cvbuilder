from django.contrib import admin

from .models import ReviewFeedback


@admin.register(ReviewFeedback)
class ReviewFeedbackAdmin(admin.ModelAdmin):
    list_display = ["id", "job_application", "gate", "target", "created_at"]
    list_filter = ["gate", "target"]
    readonly_fields = [field.name for field in ReviewFeedback._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
