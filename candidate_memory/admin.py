from django.contrib import admin

from candidate_memory.models import (
    CandidateMemory,
    CandidateProfile,
    CareerEngagement,
    ExperienceSlot,
    MemoryClaim,
    MemoryClaimSupport,
    MemoryConflict,
    MemorySourceDocument,
    OperatorCorrection,
    PositioningHistoryEntry,
    StaticResumeProfile,
)
from candidate_memory.services.experience_slots import (
    ExperienceSlotServiceError,
    ensure_static_profile,
    set_slot_order,
)


class MemoryClaimSupportInline(admin.TabularInline):
    model = MemoryClaimSupport
    extra = 0
    readonly_fields = ("source_document", "excerpt", "location_hint", "char_start", "char_end", "created_at")
    can_delete = False


class ExperienceSlotInline(admin.TabularInline):
    model = ExperienceSlot
    extra = 0
    fields = (
        "sequence",
        "is_primary",
        "is_active",
        "career_engagement",
        "company_name",
        "role_title",
        "location",
        "start_date",
        "end_date_or_present",
    )
    readonly_fields = (
        "company_name",
        "role_title",
        "location",
        "start_date",
        "end_date_or_present",
    )


@admin.register(CandidateMemory)
class CandidateMemoryAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "is_active", "updated_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("label", "notes")


@admin.register(MemorySourceDocument)
class MemorySourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("source_path", "source_kind", "precedence_rank", "content_sha256", "ingested_at")
    list_filter = ("source_kind",)
    search_fields = ("source_path", "content_sha256")
    readonly_fields = (
        "memory",
        "source_path",
        "source_kind",
        "precedence_rank",
        "content_sha256",
        "content_text",
        "byte_size",
        "ingested_at",
    )

    def has_delete_permission(self, request, obj=None):
        # AUDIT-002: source-document admin cannot delete provenance rows.
        return False


@admin.register(MemoryClaim)
class MemoryClaimAdmin(admin.ModelAdmin):
    list_display = ("claim_key", "category", "confirmation_status", "memory", "updated_at")
    list_filter = ("category", "confirmation_status")
    search_fields = ("claim_key", "text")
    inlines = [MemoryClaimSupportInline]


@admin.register(MemoryConflict)
class MemoryConflictAdmin(admin.ModelAdmin):
    list_display = ("topic", "status", "memory", "updated_at")
    list_filter = ("status",)
    search_fields = ("topic", "description")
    filter_horizontal = ("related_claims",)


@admin.register(CandidateProfile)
class CandidateProfileAdmin(admin.ModelAdmin):
    list_display = ("memory", "updated_at")


@admin.register(OperatorCorrection)
class OperatorCorrectionAdmin(admin.ModelAdmin):
    list_display = ("memory", "field_path", "created_at")
    search_fields = ("field_path", "correction_text")


@admin.register(PositioningHistoryEntry)
class PositioningHistoryEntryAdmin(admin.ModelAdmin):
    list_display = ("memory", "employer_or_context", "was_successful", "created_at")
    list_filter = ("was_successful",)


@admin.register(CareerEngagement)
class CareerEngagementAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "role_title",
        "location",
        "start_date",
        "end_date_or_present",
        "memory",
        "sort_hint",
    )
    search_fields = ("company_name", "role_title", "location")
    list_filter = ("memory",)
    actions = ("create_primary_slots_from_selection",)

    @admin.action(description="Create/activate primary ExperienceSlots from selected engagements (ordered)")
    def create_primary_slots_from_selection(self, request, queryset):
        """Admin-backed explicit operator path — never automatic selection across all engagements."""
        ordered = list(queryset.order_by("sort_hint", "id"))
        if len(ordered) != 3:
            self.message_user(
                request,
                "Select exactly three CareerEngagement rows to create primary slots.",
                level="error",
            )
            return
        memories = {row.memory_id for row in ordered}
        if len(memories) != 1:
            self.message_user(request, "Selected engagements must share one CandidateMemory.", level="error")
            return
        profile = ensure_static_profile(ordered[0].memory)
        try:
            set_slot_order(profile, [row.pk for row in ordered])
        except ExperienceSlotServiceError as exc:
            self.message_user(request, str(exc), level="error")
            return
        self.message_user(request, "Created/activated three primary ExperienceSlots from selection.")


@admin.register(StaticResumeProfile)
class StaticResumeProfileAdmin(admin.ModelAdmin):
    list_display = ("memory", "candidate_name", "updated_at")
    inlines = [ExperienceSlotInline]


@admin.register(ExperienceSlot)
class ExperienceSlotAdmin(admin.ModelAdmin):
    list_display = (
        "sequence",
        "is_primary",
        "is_active",
        "company_name",
        "role_title",
        "static_resume_profile",
        "career_engagement",
    )
    list_filter = ("is_primary", "is_active")
    readonly_fields = (
        "company_name",
        "role_title",
        "location",
        "start_date",
        "end_date_or_present",
        "created_at",
        "updated_at",
    )
    # Operators change selection/order via career_engagement + flags; static fields stay protected.
    fields = (
        "static_resume_profile",
        "career_engagement",
        "sequence",
        "is_primary",
        "is_active",
        "company_name",
        "role_title",
        "location",
        "start_date",
        "end_date_or_present",
        "created_at",
        "updated_at",
    )

    def save_model(self, request, obj, form, change):
        # Keep copied static metadata aligned with the operator-owned engagement source.
        engagement = obj.career_engagement
        obj.company_name = engagement.company_name
        obj.role_title = engagement.role_title
        obj.location = engagement.location
        obj.start_date = engagement.start_date
        obj.end_date_or_present = engagement.end_date_or_present
        super().save_model(request, obj, form, change)
