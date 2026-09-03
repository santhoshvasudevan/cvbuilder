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

import datetime
import hashlib

from django.conf import settings
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


class ChunkExtractionAttempt(_RevisionScopedModel):
    """Durable per-chunk extraction-attempt audit trail (Candidate Memory recovery, 2026-09-03).

    Every attempt to extract one bounded chunk -- or sub-chunk, after a `finish_reason=length`
    truncation split -- through the `MEMORY_BUILD` stage gets exactly one row here, regardless of
    outcome. This is what lets activation validation see "was every part of every source document
    actually, successfully covered" without trying to re-derive it from `LLMCallLog` (which has no
    source/line reference at all) or from stored claims (which say nothing about a chunk that
    produced zero claims). `FAILED` rows are unresolved and block activation; `SUCCESS` and
    `SUPERSEDED` rows are historical/covered and never block (see `services/lifecycle.py`).
    """

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    candidate_memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="chunk_attempts"
    )
    source_document = models.ForeignKey(
        MemorySourceDocument, on_delete=models.CASCADE, related_name="chunk_attempts"
    )
    source_content_sha256 = models.CharField(
        max_length=64, editable=False,
        help_text="Snapshot of the source document's content hash at attempt time.",
    )
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    start_char = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "Character offset (inclusive, 0-based) into the single line at start_line==end_line "
            "for a sentence-level sub-line fragment (Candidate Memory recovery, 2026-09-03). Null "
            "for every whole-line-or-wider attempt -- the overwhelming majority, and every row "
            "that predates this field -- meaning start_char/end_char simply do not apply."
        ),
    )
    end_char = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Character offset (exclusive) paired with start_char; see start_char.",
    )
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices)
    error_category = models.CharField(max_length=40, blank=True)
    llm_call_log = models.ForeignKey(
        "llm_provider.LLMCallLog", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="chunk_attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_document_id", "start_line", "-created_at"]

    def __str__(self) -> str:
        filename = self.source_document.filename if self.source_document_id else "?"
        char_suffix = f" C{self.start_char}-{self.end_char}" if self.start_char is not None else ""
        return (
            f"ChunkExtractionAttempt(rev={self.candidate_memory_id}, {filename} "
            f"L{self.start_line}-{self.end_line}{char_suffix}, {self.status})"
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


class CareerEngagement(models.Model):
    """Operator-owned, deterministic structured record of one real employment/client engagement
    (D-019, the deterministic static-profile boundary). Employment identity, organisation, title,
    location, and dates are facts the operator approves directly here -- never generated,
    rewritten, or inferred by an LLM, and never part of any future Agent Candidate/Agent Builder
    input or output schema (see `services/static_profile_boundary.py`).

    Deliberately **not** a `_RevisionScopedModel`: a `CareerEngagement` is not re-extracted per
    `CandidateMemory` revision and is not frozen by that revision's own BUILDING/NEEDS_REVIEW/
    ACTIVE/SUPERSEDED lifecycle -- it has its own independent `approval_status` instead, following
    the same "admin-editable registry, not per-build state" pattern as `llm_provider`'s
    `LLMProvider`/`LLMModel` registry.
    """

    class EndStatus(models.TextChoices):
        KNOWN = "KNOWN", "Known end date"
        PRESENT = "PRESENT", "Present (ongoing)"
        UNKNOWN = "UNKNOWN", "Unknown"

    class PresentationMode(models.TextChoices):
        CLIENT_CENTRIC = "CLIENT_CENTRIC", "Client-centric (default)"
        LEGAL_EMPLOYER_EXPLICIT = "LEGAL_EMPLOYER_EXPLICIT", "Legal employer explicit"
        COMBINED = "COMBINED", "Combined"

    class ApprovalStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    engagement_id = models.CharField(max_length=32, unique=True, blank=True)
    legal_employer = models.CharField(max_length=200)
    client_organization = models.CharField(
        max_length=200, blank=True,
        help_text="Blank when there is no separate client -- the legal employer is also the "
        "displayed employer.",
    )
    default_displayed_organization = models.CharField(
        max_length=200, blank=True,
        help_text="The organisation name a rendered resume header shows by default. Auto-filled "
        "from client_organization (or legal_employer if there is no client) on first save if left "
        "blank -- never a third, independently invented value.",
    )
    approved_role_title = models.CharField(max_length=200)
    localized_titles = models.JSONField(
        default=dict, blank=True,
        help_text="Optional operator-approved title translations keyed by language code, e.g. "
        "{'de': 'Senior Cloud-Ingenieur'}. Never machine-translated at render time -- see "
        "title_for_language().",
    )
    organization_aliases = models.JSONField(
        default=list, blank=True,
        help_text="Operator-approved alternate exact spellings/casings of legal_employer or "
        "client_organization (e.g. 'ford motors' for 'Ford Motor Werk GmbH') -- used only for "
        "deterministic claim-mapping matching (services/engagement_mapping.py). Never the "
        "canonical identity itself, which always stays legal_employer/client_organization.",
    )
    programme_scopes = models.JSONField(
        default=list, blank=True,
        help_text="Operator-approved project/programme subject_scope values known to have "
        "occurred during this engagement (e.g. 'ford connectivity', a named initiative) -- "
        "matches a claim to this engagement without implying it is an employer/client name.",
    )
    location = models.CharField(max_length=200, blank=True)
    start_year = models.PositiveIntegerField()
    start_month = models.PositiveIntegerField(null=True, blank=True)
    end_status = models.CharField(max_length=10, choices=EndStatus.choices, default=EndStatus.UNKNOWN)
    end_year = models.PositiveIntegerField(null=True, blank=True)
    end_month = models.PositiveIntegerField(null=True, blank=True)
    presentation_mode = models.CharField(
        max_length=30, choices=PresentationMode.choices, default=PresentationMode.CLIENT_CENTRIC
    )
    approval_status = models.CharField(
        max_length=10, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_year", "start_month"]
        constraints = [
            models.CheckConstraint(
                check=Q(start_month__gte=1, start_month__lte=12) | Q(start_month__isnull=True),
                name="career_engagement_start_month_range",
            ),
            models.CheckConstraint(
                check=Q(end_month__gte=1, end_month__lte=12) | Q(end_month__isnull=True),
                name="career_engagement_end_month_range",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.engagement_id:
            existing = CareerEngagement.objects.count()
            self.engagement_id = f"CE-{existing + 1:04d}"
        if not self.default_displayed_organization:
            self.default_displayed_organization = self.client_organization or self.legal_employer
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.engagement_id}: {self.approved_role_title} @ {self.default_displayed_organization}"

    @property
    def displayed_organization(self) -> str:
        """Deterministic organisation-name presentation -- never translated or rewritten, only
        selected among the operator's own stored alternatives. `default_displayed_organization` is
        the default; `presentation_mode` can select the legal employer explicitly, or a combined
        form, but never introduces a name not already stored on this record."""
        if self.presentation_mode == self.PresentationMode.LEGAL_EMPLOYER_EXPLICIT:
            return self.legal_employer
        if self.presentation_mode == self.PresentationMode.COMBINED and self.client_organization:
            return f"{self.client_organization} (via {self.legal_employer})"
        return self.default_displayed_organization

    def title_for_language(self, language_code: str) -> str:
        """An operator-approved localized title for `language_code` if one is stored; otherwise
        the single `approved_role_title`. Never machine-translated on the fly -- a title in a
        language with no stored entry falls back to English rather than guessing a translation."""
        return self.localized_titles.get(language_code) or self.approved_role_title

    @property
    def is_current(self) -> bool:
        return self.end_status == self.EndStatus.PRESENT

    def duration_months(self, *, as_of: datetime.date | None = None) -> int | None:
        """Deterministic whole-month duration. Returns `None` when `end_status` is `UNKNOWN` --
        there is no basis to compute an end point at all (a genuinely open question, never
        defaulted to zero or to "ongoing"). A missing month component is treated as January for a
        start date and December for an end date -- a defined, disclosed rounding convention
        (never a hidden guess) that resolves a year-only precision date to its outer bound."""
        if self.end_status == self.EndStatus.UNKNOWN:
            return None
        start_index = self.start_year * 12 + (self.start_month or 1)
        if self.end_status == self.EndStatus.PRESENT:
            reference = as_of or datetime.date.today()
            end_index = reference.year * 12 + reference.month
        else:
            end_index = self.end_year * 12 + (self.end_month or 12)
        return end_index - start_index


class ClaimEngagementMapping(models.Model):
    """A reviewable link between one `MemoryClaim` and one `CareerEngagement` (D-019).

    Deliberately a **separate table**, never a field on `MemoryClaim` itself: proposing,
    approving, or rejecting a mapping never mutates a `MemoryClaim` row, so it never conflicts
    with `_RevisionScopedModel`'s ACTIVE-revision-content-freeze invariant -- a claim that belongs
    to an already-`ACTIVE` `CandidateMemory` revision can still be mapped to an engagement after
    the fact, exactly like `MemoryConflict.involved_claims` already references frozen claims
    without editing them.
    """

    class Status(models.TextChoices):
        PROPOSED = "PROPOSED", "Proposed"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    memory_claim = models.ForeignKey(
        MemoryClaim, on_delete=models.CASCADE, related_name="engagement_mappings"
    )
    career_engagement = models.ForeignKey(
        CareerEngagement, on_delete=models.CASCADE, related_name="claim_mappings"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROPOSED)
    proposed_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set whenever status changes away from PROPOSED -- doubles as the approval "
        "timestamp when status=APPROVED.",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_claim_engagement_mappings",
        help_text="The operator who approved this mapping (set only on approval, never on "
        "rejection or proposal) -- part of the audit trail alongside reviewed_at.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["memory_claim", "career_engagement"], name="unique_claim_engagement_pair"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.memory_claim_id} -> {self.career_engagement_id} ({self.status})"
