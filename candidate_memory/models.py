"""Candidate Memory domain models (docs/ARCHITECTURE.md Sec 4/8/9, D-002/D-003/D-015, D-016).

Lifecycle summary (M0.1 audit fix -- see docs/ARCHITECTURE.md Sec 4 `CandidateMemory` for the
full rationale):

    BUILDING -> NEEDS_REVIEW -> ACTIVE -> SUPERSEDED
    BUILDING -> FAILED
    NEEDS_REVIEW -> FAILED

Claims/supports/rules/conflicts are freely mutable while their owning revision is BUILDING or
NEEDS_REVIEW -- that is the normal extraction/review workflow, not an exception to immutability.
The moment a revision becomes ACTIVE, all of that content freezes permanently; the only further
transition the revision itself may undergo is ACTIVE -> SUPERSEDED, applied atomically when a
newer revision is activated. These invariants are enforced here, at the model layer, so no
service, view, form, or admin page can bypass them.

`FAILED` (D-016, audit repair) is a second terminal state alongside `SUPERSEDED`: a working
revision that crashed mid-build or that the operator explicitly abandoned (e.g. to retry a
bootstrap without a silently-orphaned duplicate working revision sitting in `NEEDS_REVIEW`). Like
`SUPERSEDED`, it is frozen -- no further status transition is permitted out of it; recovery means
starting a genuinely new revision, never resurrecting a `FAILED` one.
"""

from __future__ import annotations

import hashlib

from django.db import models
from django.db.models import Q

from .exceptions import RevisionNotEditableError, SourceDocumentImmutableError

_MUTABLE_STATUSES = {"BUILDING", "NEEDS_REVIEW"}


class CandidateMemory(models.Model):
    """One complete logical revision of the candidate's profile (D-002/D-015)."""

    class Status(models.TextChoices):
        BUILDING = "BUILDING", "Building"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs review"
        ACTIVE = "ACTIVE", "Active"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        FAILED = "FAILED", "Failed"

    version = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BUILDING)
    base_revision = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived_revisions"
    )
    build_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="ACTIVE"),
                name="unique_active_candidate_memory",
            ),
        ]

    def __str__(self) -> str:
        return f"CandidateMemory v{self.version} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            old_status = (
                CandidateMemory.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
            if old_status is not None and old_status not in _MUTABLE_STATUSES:
                allowed_transition = (
                    old_status == self.Status.ACTIVE and self.status == self.Status.SUPERSEDED
                )
                if not allowed_transition:
                    raise RevisionNotEditableError(
                        f"CandidateMemory {self.pk} is {old_status} and cannot be modified "
                        "(the only permitted transition on a frozen revision is ACTIVE -> SUPERSEDED)."
                    )
        super().save(*args, **kwargs)

    @property
    def is_mutable(self) -> bool:
        return self.status in _MUTABLE_STATUSES


class _RevisionScopedModel(models.Model):
    """Shared save()/delete() guard for anything owned by one CandidateMemory revision."""

    class Meta:
        abstract = True

    def _owning_revision_status(self) -> str | None:
        return (
            CandidateMemory.objects.filter(pk=self.candidate_memory_id)
            .values_list("status", flat=True)
            .first()
        )

    def save(self, *args, **kwargs):
        status = self._owning_revision_status()
        if status is not None and status not in _MUTABLE_STATUSES:
            raise RevisionNotEditableError(
                f"{type(self).__name__} belongs to a CandidateMemory revision that is {status}; "
                "content on ACTIVE/SUPERSEDED revisions is read-only. Corrections must go through "
                "the ongoing-update workflow, which creates a new revision."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        status = self._owning_revision_status()
        if status is not None and status not in _MUTABLE_STATUSES:
            raise RevisionNotEditableError(
                f"{type(self).__name__} belongs to a CandidateMemory revision that is {status}; "
                "content on ACTIVE/SUPERSEDED revisions cannot be deleted."
            )
        super().delete(*args, **kwargs)


class MemorySourceDocument(models.Model):
    """One immutable source occurrence within one CandidateMemory revision (D-015)."""

    class SourceRole(models.TextChoices):
        PRIMARY_PROFILE = "PRIMARY_PROFILE", "Primary profile"
        ENGLISH_CORPUS = "ENGLISH_CORPUS", "English corpus"
        GERMAN_CORPUS = "GERMAN_CORPUS", "German corpus"
        OPERATOR_UPDATE = "OPERATOR_UPDATE", "Operator update"

    class TrustStatus(models.TextChoices):
        OPERATOR_APPROVED = "OPERATOR_APPROVED", "Operator approved"
        UNVERIFIED = "UNVERIFIED", "Unverified"

    candidate_memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="source_documents"
    )
    logical_source_key = models.CharField(
        max_length=100,
        help_text="Stable identity for 'this conceptual document' across revisions, independent "
        "of filename -- what D-002's unchanged/changed comparison keys off.",
    )
    filename = models.CharField(max_length=255)
    source_role = models.CharField(max_length=20, choices=SourceRole.choices)
    language = models.CharField(max_length=10, default="en")
    trust_status = models.CharField(max_length=20, choices=TrustStatus.choices)
    precedence = models.PositiveIntegerField(help_text="Lower number = higher precedence.")
    raw_content = models.TextField()
    content_sha256 = models.CharField(max_length=64, editable=False)
    imported_at = models.DateTimeField(auto_now_add=True)
    unchanged_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="carried_forward_to"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["candidate_memory", "logical_source_key"],
                name="unique_logical_source_per_revision",
            ),
        ]
        ordering = ["precedence", "logical_source_key"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.source_role}) @ rev {self.candidate_memory_id}"

    def save(self, *args, **kwargs):
        if not self.content_sha256:
            self.content_sha256 = hashlib.sha256(self.raw_content.encode("utf-8")).hexdigest()
        if self.pk:
            raise SourceDocumentImmutableError(
                "MemorySourceDocument is immutable once created; a changed document must be "
                "stored as a new row (with unchanged_from left unset), never edited in place."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise SourceDocumentImmutableError("MemorySourceDocument rows are never deleted.")

    def verify_content_hash(self) -> bool:
        return hashlib.sha256(self.raw_content.encode("utf-8")).hexdigest() == self.content_sha256


class MemoryClaim(_RevisionScopedModel):
    """One atomic canonical fact (evidence plane only) -- the atomic unit of retrieval, review,
    and evidence citation (D-014/D-015)."""

    class ExperienceLevel(models.TextChoices):
        AWARENESS = "AWARENESS", "Awareness"
        LEARNING = "LEARNING", "Learning"
        PROTOTYPE = "PROTOTYPE", "Prototype"
        PROFESSIONAL_DELIVERY = "PROFESSIONAL_DELIVERY", "Professional delivery"
        PRODUCTION_OPERATION = "PRODUCTION_OPERATION", "Production operation"
        ARCHITECTURE_OWNERSHIP = "ARCHITECTURE_OWNERSHIP", "Architecture ownership"
        LEADERSHIP = "LEADERSHIP", "Leadership"

    class ConfirmationStatus(models.TextChoices):
        UNCONFIRMED = "UNCONFIRMED", "Unconfirmed"
        CONFIRMED = "CONFIRMED", "Confirmed"
        RETIRED = "RETIRED", "Retired"
        BLOCKED_CONFLICT = "BLOCKED_CONFLICT", "Blocked (conflict)"

    class PresentationMode(models.TextChoices):
        CLIENT_CENTRIC = "CLIENT_CENTRIC", "Client-centric (default)"
        LEGAL_EMPLOYER_EXPLICIT = "LEGAL_EMPLOYER_EXPLICIT", "Legal employer explicit"
        COMBINED = "COMBINED", "Combined"

    candidate_memory = models.ForeignKey(CandidateMemory, on_delete=models.CASCADE, related_name="claims")
    claim_id = models.CharField(max_length=32, blank=True)
    stable_key = models.CharField(
        max_length=100,
        help_text="Cross-revision identity key -- lets carry-forward say 'this is the same claim "
        "as one in base_revision' even if claim_id renumbers.",
    )
    canonical_text_en = models.TextField(help_text="Canonical claim text -- English only (D-015).")
    claim_type = models.CharField(max_length=100)
    subject_scope = models.CharField(max_length=200, help_text="Employer/client/project/context.")
    experience_level = models.CharField(
        max_length=30, choices=ExperienceLevel.choices, null=True, blank=True
    )
    resume_eligible = models.BooleanField(default=False)
    confirmation_status = models.CharField(
        max_length=20, choices=ConfirmationStatus.choices, default=ConfirmationStatus.UNCONFIRMED
    )
    duplicate_group_key = models.CharField(max_length=200, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    structured_value = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional structured payload for deterministic conflict comparison, e.g. "
        "{'location': 'Nuremberg, Germany'} or {'language': 'German', 'level': 'B1'}. Prose "
        "(canonical_text_en) remains the resume-facing text; this is comparison data only.",
    )

    # Legal-employer-vs-client separation (operator resolution 2026-09-02, item 1/2).
    legal_employer = models.CharField(max_length=200, blank=True)
    client_organization = models.CharField(max_length=200, blank=True)
    presentation_mode = models.CharField(
        max_length=30, choices=PresentationMode.choices, default=PresentationMode.CLIENT_CENTRIC
    )

    class Meta:
        ordering = ["claim_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["candidate_memory", "claim_id"], name="unique_claim_id_per_revision"
            ),
        ]

    def __str__(self) -> str:
        return self.claim_id or f"(unassigned claim on rev {self.candidate_memory_id})"

    def save(self, *args, **kwargs):
        if not self.claim_id and self.candidate_memory_id:
            existing = MemoryClaim.objects.filter(candidate_memory_id=self.candidate_memory_id).count()
            self.claim_id = f"MC-{self.candidate_memory_id}-{existing + 1:04d}"
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Audit repair: deleting a claim that a MemoryConflict references (at any status, not
        # just OPEN) would silently desync that conflict's `involved_claims` set from its frozen
        # `description` text -- an audit-trail integrity issue, not just a missing feature.
        # Retirement (`services.lifecycle.retire_claim`) is the supported way to remove a claim
        # from consideration; it preserves the claim row and the conflict's history intact.
        if self.pk and self.conflicts.exists():
            raise RevisionNotEditableError(
                f"{self.claim_id or self.pk} is referenced by a MemoryConflict; deleting it "
                "would desync that conflict's audit trail. Use retire_claim() instead."
            )
        super().delete(*args, **kwargs)

    @property
    def is_usable_as_evidence(self) -> bool:
        return (
            self.resume_eligible
            and self.confirmation_status == self.ConfirmationStatus.CONFIRMED
        )


class MemoryClaimSupport(_RevisionScopedModel):
    """One exact supporting passage for one canonical claim -- a claim may have several
    (D-003/D-015): e.g. an English primary passage plus a German corroborating passage."""

    class SupportRole(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary"
        CORROBORATING = "CORROBORATING", "Corroborating"
        GERMAN_EXPRESSION = "GERMAN_EXPRESSION", "German expression"

    memory_claim = models.ForeignKey(MemoryClaim, on_delete=models.CASCADE, related_name="supports")
    memory_source_document = models.ForeignKey(
        MemorySourceDocument, on_delete=models.PROTECT, related_name="claim_supports"
    )
    quotation = models.TextField()
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    quotation_hash = models.CharField(max_length=64, editable=False)
    source_language = models.CharField(max_length=10)
    support_role = models.CharField(max_length=20, choices=SupportRole.choices)

    @property
    def candidate_memory_id(self):  # noqa: D401 -- used by _RevisionScopedModel's guard
        return self.memory_claim.candidate_memory_id

    def save(self, *args, **kwargs):
        self.quotation_hash = hashlib.sha256(self.quotation.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    def verify_against_source(self) -> bool:
        """Exact provenance validation (D-003): the quotation must be a real substring of the
        source document's immutable content at the stated line range."""
        lines = self.memory_source_document.raw_content.splitlines()
        excerpt = "\n".join(lines[self.start_line - 1 : self.end_line])
        return self.quotation.strip() in excerpt

    def __str__(self) -> str:
        return (
            f"{self.support_role} support for {self.memory_claim.claim_id} "
            f"@ L{self.start_line}-{self.end_line}"
        )


class CandidateRule(_RevisionScopedModel):
    """Constraint-plane and positioning-plane content (D-015) -- never resume evidence, never
    independently sufficient to satisfy a MATCH or PARTIAL disposition."""

    class RuleType(models.TextChoices):
        CAUTION = "CAUTION", "Caution"
        PROHIBITION = "PROHIBITION", "Prohibition"
        PREFERENCE = "PREFERENCE", "Preference"
        POSITIONING = "POSITIONING", "Positioning"
        LEARNING_STATUS = "LEARNING_STATUS", "Learning status"

    candidate_memory = models.ForeignKey(CandidateMemory, on_delete=models.CASCADE, related_name="rules")
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    text = models.TextField()
    scope = models.CharField(max_length=200, blank=True)

    source_document = models.ForeignKey(
        MemorySourceDocument, on_delete=models.PROTECT, related_name="rules", null=True, blank=True
    )
    source_quote = models.TextField(blank=True)
    start_line = models.PositiveIntegerField(null=True, blank=True)
    end_line = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.rule_type}: {self.text[:60]}"


class MemoryConflict(_RevisionScopedModel):
    """An explicit, visible contradiction record (D-015) -- while OPEN, every involved claim is
    ineligible (BLOCKED_CONFLICT) regardless of any prior confirmation."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    candidate_memory = models.ForeignKey(CandidateMemory, on_delete=models.CASCADE, related_name="conflicts")
    conflict_key = models.CharField(max_length=200)
    description = models.TextField()
    involved_claims = models.ManyToManyField(MemoryClaim, related_name="conflicts")
    involved_supports = models.ManyToManyField(MemoryClaimSupport, related_name="conflicts", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    operator_resolution = models.TextField(blank=True)
    resolved_claim = models.ForeignKey(
        MemoryClaim, null=True, blank=True, on_delete=models.SET_NULL, related_name="resolved_conflicts"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.conflict_key} ({self.status})"
