from django.contrib import admin

from .models import (
    CandidateMemory,
    CandidateRule,
    MemoryClaim,
    MemoryClaimSupport,
    MemoryConflict,
    MemorySourceDocument,
)


class _RevisionScopedAdminMixin:
    """Admin add/change/delete permissions follow the same rule the model layer enforces:
    read-only once the owning revision leaves BUILDING/NEEDS_REVIEW."""

    def _revision_for(self, obj):
        raise NotImplementedError

    def has_change_permission(self, request, obj=None):
        if obj is not None and not self._revision_for(obj).is_mutable:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not self._revision_for(obj).is_mutable:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CandidateMemory)
class CandidateMemoryAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "base_revision", "created_at", "activated_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "activated_at", "build_summary")

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.is_mutable:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not obj.is_mutable:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(MemorySourceDocument)
class MemorySourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("filename", "candidate_memory", "source_role", "language", "trust_status", "precedence")
    list_filter = ("source_role", "trust_status", "candidate_memory")
    readonly_fields = ("content_sha256", "imported_at")

    def has_change_permission(self, request, obj=None):
        return False  # immutable once created, always

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MemoryClaim)
class MemoryClaimAdmin(_RevisionScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "claim_id",
        "candidate_memory",
        "claim_type",
        "subject_scope",
        "experience_level",
        "resume_eligible",
        "confirmation_status",
    )
    list_filter = ("candidate_memory", "confirmation_status", "resume_eligible", "claim_type")
    search_fields = ("claim_id", "canonical_text_en", "subject_scope")

    def _revision_for(self, obj):
        return obj.candidate_memory


@admin.register(MemoryClaimSupport)
class MemoryClaimSupportAdmin(_RevisionScopedAdminMixin, admin.ModelAdmin):
    list_display = ("memory_claim", "memory_source_document", "start_line", "end_line", "support_role")
    list_filter = ("support_role", "source_language")

    def _revision_for(self, obj):
        return obj.memory_claim.candidate_memory


@admin.register(CandidateRule)
class CandidateRuleAdmin(_RevisionScopedAdminMixin, admin.ModelAdmin):
    list_display = ("rule_type", "candidate_memory", "scope", "text")
    list_filter = ("candidate_memory", "rule_type")

    def _revision_for(self, obj):
        return obj.candidate_memory


@admin.register(MemoryConflict)
class MemoryConflictAdmin(_RevisionScopedAdminMixin, admin.ModelAdmin):
    list_display = ("conflict_key", "candidate_memory", "status", "resolved_claim", "resolved_at")
    list_filter = ("candidate_memory", "status")
    filter_horizontal = ("involved_claims", "involved_supports")

    def _revision_for(self, obj):
        return obj.candidate_memory
