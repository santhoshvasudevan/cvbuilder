from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from .models import (
    CandidateMemory,
    CandidateRule,
    CareerEngagement,
    ClaimEngagementMapping,
    MemoryClaim,
    MemoryClaimSupport,
    MemoryConflict,
    MemorySourceDocument,
)
from .services import engagement_mapping


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


@admin.register(CareerEngagement)
class CareerEngagementAdmin(admin.ModelAdmin):
    """Operator-owned registry, independent of any CandidateMemory revision's lifecycle (D-019) --
    the normal admin-editable-registry pattern, same as llm_provider's LLMProvider/LLMModel."""

    list_display = (
        "engagement_id",
        "approved_role_title",
        "displayed_organization",
        "start_year",
        "end_status",
        "end_year",
        "approval_status",
    )
    list_filter = ("approval_status", "presentation_mode", "end_status")
    search_fields = ("engagement_id", "legal_employer", "client_organization", "approved_role_title")
    readonly_fields = ("engagement_id", "created_at", "updated_at")
    actions = ["approve_engagements", "reject_engagements"]

    @admin.display(description="Displayed organisation")
    def displayed_organization(self, obj):
        return obj.displayed_organization

    @admin.action(description="Approve selected engagements")
    def approve_engagements(self, request, queryset):
        queryset.update(approval_status=CareerEngagement.ApprovalStatus.APPROVED)

    @admin.action(description="Reject selected engagements")
    def reject_engagements(self, request, queryset):
        queryset.update(approval_status=CareerEngagement.ApprovalStatus.REJECTED)


@admin.register(ClaimEngagementMapping)
class ClaimEngagementMappingAdmin(admin.ModelAdmin):
    """The reviewable claim<->engagement mapping workflow (D-019): PROPOSED rows are created only
    by `services.engagement_mapping.propose_claim_engagement_mappings`, never by hand; the operator
    approves or rejects them here.

    Review-experience fix (2026-09-03): the list and change pages must show enough of the
    underlying `MemoryClaim`/`MemoryClaimSupport` evidence for an operator to approve or reject
    safely, without needing database-shell access -- see `evidence_detail` below for the exact
    fields (verified against the real model definitions, not assumed). All of it is rendered
    read-only via `format_html`, which escapes every interpolated value, so no claim/quotation
    text can inject markup into the page."""

    list_display = (
        "claim_preview",
        "engagement_preview",
        "source_preview",
        "status",
        "proposed_reason",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("status",)
    readonly_fields = ("created_at", "evidence_detail")
    fields = (
        "evidence_detail",
        "memory_claim",
        "career_engagement",
        "status",
        "proposed_reason",
        "created_at",
        "reviewed_at",
    )
    actions = ["approve_mappings", "reject_mappings"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("memory_claim", "career_engagement")
            .prefetch_related("memory_claim__supports__memory_source_document")
        )

    @admin.display(description="Claim")
    def claim_preview(self, obj):
        claim = obj.memory_claim
        text = claim.canonical_text_en
        short_text = text if len(text) <= 80 else text[:77] + "..."
        return format_html("{} ({}): {}", claim.claim_id, claim.claim_type, short_text)

    @admin.display(description="Engagement")
    def engagement_preview(self, obj):
        engagement = obj.career_engagement
        return format_html(
            "{}: {} @ {}",
            engagement.engagement_id, engagement.approved_role_title, engagement.displayed_organization,
        )

    @admin.display(description="Source")
    def source_preview(self, obj):
        supports = list(obj.memory_claim.supports.all())
        if not supports:
            return "(no supporting quotation on record)"
        first = supports[0]
        suffix = f" (+{len(supports) - 1} more)" if len(supports) > 1 else ""
        return format_html(
            "{} L{}-{}{}", first.memory_source_document.filename, first.start_line, first.end_line, suffix
        )

    @admin.display(description="Full evidence for review (read-only)")
    def evidence_detail(self, obj):
        claim = obj.memory_claim
        supports = list(claim.supports.all())

        if supports:
            quotation_rows = format_html_join(
                "",
                "<li><strong>{}</strong> — {}, line{} {}-{} ({}):<br>&ldquo;{}&rdquo;</li>",
                (
                    (
                        support.support_role,
                        support.memory_source_document.filename,
                        "s" if support.end_line != support.start_line else "",
                        support.start_line,
                        support.end_line,
                        support.source_language,
                        support.quotation,
                    )
                    for support in supports
                ),
            )
            quotations_html = format_html("<ul>{}</ul>", quotation_rows)
        else:
            quotations_html = format_html("<em>No supporting quotation on record.</em>")

        engagement = obj.career_engagement

        return format_html(
            "<dl>"
            "<dt><strong>MemoryClaim ID</strong></dt><dd>{claim_id}</dd>"
            "<dt><strong>Canonical claim text</strong></dt><dd>{text}</dd>"
            "<dt><strong>Claim type / subject scope</strong></dt><dd>{claim_type} / {scope}</dd>"
            "<dt><strong>Confirmation status</strong></dt><dd>{confirmation}</dd>"
            "<dt><strong>Resume-eligible</strong></dt><dd>{eligible}</dd>"
            "<dt><strong>Supporting source quotation(s)</strong></dt><dd>{quotations}</dd>"
            "<dt><strong>Proposed CareerEngagement</strong></dt>"
            "<dd>{engagement_id}: {engagement_title} @ {engagement_org}</dd>"
            "<dt><strong>Proposal / matching basis</strong></dt><dd>{reason}</dd>"
            "<dt><strong>Mapping status</strong></dt><dd>{status}</dd>"
            "</dl>",
            claim_id=claim.claim_id,
            text=claim.canonical_text_en,
            claim_type=claim.claim_type,
            scope=claim.subject_scope,
            confirmation=claim.get_confirmation_status_display(),
            eligible="Yes" if claim.resume_eligible else "No",
            quotations=quotations_html,
            engagement_id=engagement.engagement_id,
            engagement_title=engagement.approved_role_title,
            engagement_org=engagement.displayed_organization,
            reason=obj.proposed_reason or "(none recorded)",
            status=obj.get_status_display(),
        )

    @admin.action(description="Approve selected mappings")
    def approve_mappings(self, request, queryset):
        # Routed through the service function (D-019 refinement, 2026-09-03) -- never a bulk
        # status update -- so a static engagement claim's mapping is refused here exactly like
        # everywhere else, not just in code nobody calls.
        approved, refused = 0, []
        for mapping in queryset.select_related("memory_claim", "career_engagement"):
            try:
                engagement_mapping.approve_mapping(mapping)
            except engagement_mapping.StaticClaimMappingError:
                refused.append(mapping.memory_claim.claim_id)
            else:
                approved += 1
        if approved:
            self.message_user(request, f"Approved {approved} mapping(s).")
        if refused:
            self.message_user(
                request,
                "Refused to approve static engagement claim mapping(s), reject them instead: "
                + ", ".join(refused),
                level=messages.WARNING,
            )

    @admin.action(description="Reject selected mappings")
    def reject_mappings(self, request, queryset):
        for mapping in queryset:
            engagement_mapping.reject_mapping(mapping)
