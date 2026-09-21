"""Durable candidate factual knowledge and StaticResumeProfile (M3A, V2-D030/D024/D031)."""

from django.db import models


class CandidateMemory(models.Model):
    """Root revision of candidate knowledge. PostgreSQL is the operational source of truth."""

    label = models.CharField(max_length=255, default="default")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name_plural = "candidate memories"

    def __str__(self) -> str:
        state = "active" if self.is_active else "inactive"
        return f"CandidateMemory #{self.pk} ({self.label}, {state})"


class MemorySourceDocument(models.Model):
    """Ingested candidate source document with immutable provenance metadata."""

    class SourceKind(models.TextChoices):
        MEMORY_PROFILE = "MEMORY_PROFILE", "AC memory profile"
        PROFILE_ENGLISH = "PROFILE_ENGLISH", "English corpus"
        PROFILE_GERMAN = "PROFILE_GERMAN", "German corpus"
        OTHER = "OTHER", "Other operator source"

    memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="source_documents"
    )
    source_path = models.CharField(max_length=512)
    source_kind = models.CharField(max_length=32, choices=SourceKind.choices)
    precedence_rank = models.PositiveSmallIntegerField(
        help_text="Lower number wins when sources disagree (1 = highest precedence).",
    )
    content_sha256 = models.CharField(max_length=64)
    content_text = models.TextField()
    byte_size = models.PositiveIntegerField()
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["precedence_rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["memory", "content_sha256"],
                name="unique_memory_source_content_hash",
            ),
            models.UniqueConstraint(
                fields=["memory", "source_path"],
                name="unique_memory_source_path",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_path} ({self.content_sha256[:12]})"


class MemoryClaim(models.Model):
    """Atomic provenance-bearing claim retained where useful (V2-D003, FACT-002)."""

    class Category(models.TextChoices):
        CAREER_HISTORY = "CAREER_HISTORY", "Career history"
        SKILL = "SKILL", "Skill"
        PROJECT = "PROJECT", "Project"
        ACHIEVEMENT = "ACHIEVEMENT", "Achievement"
        EDUCATION = "EDUCATION", "Education"
        CERTIFICATION = "CERTIFICATION", "Certification"
        LANGUAGE = "LANGUAGE", "Language"
        PREFERENCE = "PREFERENCE", "Preference"
        GAP_OR_CONSTRAINT = "GAP_OR_CONSTRAINT", "Gap or constraint"
        OTHER = "OTHER", "Other"

    class ConfirmationStatus(models.TextChoices):
        UNREVIEWED = "UNREVIEWED", "Unreviewed"
        CONFIRMED = "CONFIRMED", "Confirmed"
        REJECTED = "REJECTED", "Rejected"

    memory = models.ForeignKey(CandidateMemory, on_delete=models.CASCADE, related_name="claims")
    claim_key = models.CharField(max_length=255)
    text = models.TextField()
    category = models.CharField(max_length=32, choices=Category.choices)
    confirmation_status = models.CharField(
        max_length=16,
        choices=ConfirmationStatus.choices,
        default=ConfirmationStatus.UNREVIEWED,
    )
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["memory", "claim_key"],
                name="unique_memory_claim_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.claim_key}: {self.text[:60]}"


class MemoryClaimSupport(models.Model):
    """Immutable support/provenance link from a claim to a source excerpt."""

    claim = models.ForeignKey(MemoryClaim, on_delete=models.CASCADE, related_name="supports")
    source_document = models.ForeignKey(
        MemorySourceDocument, on_delete=models.CASCADE, related_name="claim_supports"
    )
    excerpt = models.TextField()
    location_hint = models.CharField(max_length=255, blank=True, default="")
    char_start = models.PositiveIntegerField(null=True, blank=True)
    char_end = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Support for {self.claim.claim_key} from {self.source_document.source_path}"


class MemoryConflict(models.Model):
    """Unresolved source contradiction preserved for operator review (never silently resolved)."""

    class Status(models.TextChoices):
        UNRESOLVED = "UNRESOLVED", "Unresolved"
        RESOLVED = "RESOLVED", "Resolved"

    memory = models.ForeignKey(CandidateMemory, on_delete=models.CASCADE, related_name="conflicts")
    topic = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNRESOLVED)
    related_claims = models.ManyToManyField(MemoryClaim, blank=True, related_name="conflicts")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["memory", "topic"],
                name="unique_memory_conflict_topic",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.topic} [{self.status}]"


class CandidateProfile(models.Model):
    """Structured candidate profile fields available to downstream stages (§6.1, CTX-004)."""

    memory = models.OneToOneField(
        CandidateMemory, on_delete=models.CASCADE, related_name="profile"
    )
    career_direction = models.TextField(blank=True, default="")
    target_role_families = models.JSONField(default=list, blank=True)
    preferred_positioning = models.TextField(blank=True, default="")
    technical_strengths = models.JSONField(default=list, blank=True)
    domain_strengths = models.JSONField(default=list, blank=True)
    known_gaps = models.JSONField(default=list, blank=True)
    development_areas = models.JSONField(default=list, blank=True)
    technology_depth = models.JSONField(default=dict, blank=True)
    wording_preferences = models.TextField(blank=True, default="")
    privacy_preferences = models.TextField(blank=True, default="")
    employer_naming_preferences = models.TextField(blank=True, default="")
    skills = models.JSONField(default=list, blank=True)
    projects = models.JSONField(default=list, blank=True)
    achievements = models.JSONField(default=list, blank=True)
    education = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile for CandidateMemory #{self.memory_id}"


class OperatorCorrection(models.Model):
    """Operator correction or preference retained for AC/APS/AB (CTX-004)."""

    memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="operator_corrections"
    )
    field_path = models.CharField(max_length=255)
    correction_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]

    def __str__(self) -> str:
        return f"{self.field_path}: {self.correction_text[:60]}"


class PositioningHistoryEntry(models.Model):
    """Prior positioning history — informs strategy, never silently becomes factual truth."""

    memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="positioning_history"
    )
    employer_or_context = models.CharField(max_length=255)
    summary = models.TextField()
    was_successful = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name_plural = "positioning history entries"

    def __str__(self) -> str:
        return f"{self.employer_or_context}: {self.summary[:60]}"


class CareerEngagement(models.Model):
    """Operator-owned career engagement source record (CareerEngagement-equivalent, V2-D031)."""

    memory = models.ForeignKey(
        CandidateMemory, on_delete=models.CASCADE, related_name="career_engagements"
    )
    company_name = models.CharField(max_length=255)
    role_title = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, default="")
    start_date = models.CharField(max_length=64, blank=True, default="")
    end_date_or_present = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    source_document = models.ForeignKey(
        MemorySourceDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="career_engagements",
    )
    sort_hint = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_hint", "id"]

    def __str__(self) -> str:
        return f"{self.company_name} — {self.role_title}"


class StaticResumeProfile(models.Model):
    """Deterministic static resume content; certifications/languages are never LLM-generated."""

    memory = models.OneToOneField(
        CandidateMemory, on_delete=models.CASCADE, related_name="static_resume_profile"
    )
    candidate_name = models.CharField(max_length=255, blank=True, default="")
    contact_metadata = models.JSONField(default=dict, blank=True)
    static_certifications = models.JSONField(default=list, blank=True)
    static_languages = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"StaticResumeProfile for CandidateMemory #{self.memory_id}"


class ExperienceSlot(models.Model):
    """Sequenced related collection — not fixed columns (V2-D024). Metadata is copied + linked."""

    static_resume_profile = models.ForeignKey(
        StaticResumeProfile, on_delete=models.CASCADE, related_name="experience_slots"
    )
    career_engagement = models.ForeignKey(
        CareerEngagement,
        on_delete=models.PROTECT,
        related_name="experience_slots",
        help_text="Source engagement retained for provenance; metadata is copied at creation.",
    )
    sequence = models.PositiveSmallIntegerField()
    is_primary = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    # Operator-owned static metadata copied from CareerEngagement (V2-D031 copy approach).
    company_name = models.CharField(max_length=255)
    role_title = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, default="")
    start_date = models.CharField(max_length=64, blank=True, default="")
    end_date_or_present = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["static_resume_profile", "sequence"],
                condition=models.Q(is_active=True, is_primary=True),
                name="unique_active_primary_slot_sequence",
            ),
            models.UniqueConstraint(
                fields=["static_resume_profile", "career_engagement"],
                condition=models.Q(is_active=True, is_primary=True),
                name="unique_active_primary_slot_engagement",
            ),
        ]

    def __str__(self) -> str:
        flags = []
        if self.is_primary:
            flags.append("primary")
        if self.is_active:
            flags.append("active")
        flag_text = ",".join(flags) or "inactive"
        return f"Slot {self.sequence} ({flag_text}): {self.company_name}"
