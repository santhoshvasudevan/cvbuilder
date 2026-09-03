"""Agent Builder's persisted output (requirements.md Sec 7/16, D-007/D-014/D-019, M6).

`ResumeDraft` is append-only/versioned per D-010 for its content fields (mirroring
`candidate_matching.FitAssessment`), with exactly one further permitted mutation: `confirm()`,
which sets `confirmed_at` from `None` to a real timestamp on Gate-2 approval -- the same
"one specific allowed transition, everything else frozen" pattern `CandidateMemory` already uses
for ACTIVE -> SUPERSEDED.

`ResumeElement` rows are the queryable, evidence-bearing structured content the no-fabrication
validator (`validators/no_fabrication.py`) already checked before this draft was ever created --
they exist so Gate 2 can preserve full evidence inspection (which MemoryClaim/JobRequirement IDs
back which line of the rendered resume) without re-parsing `rendered_markdown`.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class ResumeDraft(models.Model):
    job_application = models.ForeignKey(
        "job_applications.JobApplication", on_delete=models.CASCADE, related_name="resume_drafts"
    )
    version = models.PositiveIntegerField()
    based_on_fit_assessment = models.ForeignKey(
        "candidate_matching.FitAssessment",
        on_delete=models.PROTECT,
        related_name="resume_drafts",
        help_text="The exact FitAssessment version this draft was built from (D-006 freshness "
        "identity -- compared against JobApplication.current_fit_assessment_id, never a timestamp).",
    )
    retrieved_claim_ids = models.JSONField(default=list, blank=True)
    retrieved_engagement_ids = models.JSONField(default=list, blank=True)

    recommended_title = models.CharField(max_length=300)
    title_options = models.JSONField(
        default=list, blank=True,
        help_text="TargetPositioning.title_options -- internal planning content, never rendered "
        "(docs/RESUME_OUTPUT_STRUCTURE.md Sec 5).",
    )
    skill_categories = models.JSONField(
        default=list, blank=True,
        help_text="Agent Builder's own broader SkillCategory reasoning workspace -- not "
        "independently evidence-checked and never rendered directly; only the SelectedResumeSkills "
        "entries (stored as ResumeElement rows, section=SKILL) are validated and rendered.",
    )
    positioning_guidance = models.JSONField(
        default=dict, blank=True,
        help_text="PositioningGuidance -- a generation constraint, never independent factual "
        "evidence, never rendered.",
    )
    rendered_markdown = models.TextField(
        help_text="Deterministic markdown rendered from validated ResumeElement rows only, after "
        "the no-fabrication validator passed -- never anything Agent Builder emitted directly.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "version"], name="unique_resume_draft_version_per_application"
            )
        ]
        ordering = ["job_application_id", "version"]

    def __str__(self) -> str:
        return f"ResumeDraft(app={self.job_application_id}, v{self.version})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old_confirmed_at = (
                ResumeDraft.objects.filter(pk=self.pk).values_list("confirmed_at", flat=True).first()
            )
            allowed = old_confirmed_at is None and self.confirmed_at is not None
            if not allowed:
                raise ImmutableResumeDraftError(
                    "ResumeDraft is append-only once created -- the only permitted change is "
                    "confirm() (setting confirmed_at once). Create a new version for anything else."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableResumeDraftError(
            "ResumeDraft is append-only -- it is never deleted through the normal ORM instance API."
        )

    def confirm(self) -> None:
        if self.confirmed_at is not None:
            raise ImmutableResumeDraftError(f"ResumeDraft v{self.version} is already confirmed.")
        self.confirmed_at = timezone.now()
        self.save(update_fields=["confirmed_at"])

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class ImmutableResumeDraftError(Exception):
    pass


class ResumeElement(models.Model):
    class Section(models.TextChoices):
        SUMMARY = "SUMMARY", "Professional summary"
        EXPERIENCE_BULLET = "EXPERIENCE_BULLET", "Experience bullet"
        POSITIONING_THEME = "POSITIONING_THEME", "Positioning theme"
        ACHIEVEMENT = "ACHIEVEMENT", "Achievement"
        SKILL = "SKILL", "Skill"
        CERTIFICATION = "CERTIFICATION", "Certification"
        LANGUAGE = "LANGUAGE", "Language"

    resume_draft = models.ForeignKey(ResumeDraft, on_delete=models.CASCADE, related_name="elements")
    section = models.CharField(max_length=20, choices=Section.choices)
    # Set only for section=EXPERIENCE_BULLET -- which APPROVED CareerEngagement this bullet is
    # placed under. A plain CharField (not a FK) mirrors candidate_matching.RequirementAssessment's
    # choice to copy an external stable ID rather than FK across an app boundary; the ID is
    # re-resolved fresh via services.static_profile_boundary.resolve_approved_engagement at render
    # time, never trusted as a cached identity.
    engagement_id = models.CharField(max_length=32, blank=True, default="")
    order = models.PositiveIntegerField(help_text="Deterministic ordering within (section, engagement_id).")
    text = models.TextField()
    supporting_memory_claim_ids = models.JSONField(default=list, blank=True)
    matched_job_requirement_ids = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["resume_draft", "section", "engagement_id", "order"],
                name="unique_resume_element_order",
            )
        ]
        ordering = ["resume_draft_id", "section", "engagement_id", "order"]

    def __str__(self) -> str:
        return f"{self.section}: {self.text[:50]}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableResumeDraftError(
                "ResumeElement rows belong to an append-only ResumeDraft version and are never "
                "edited in place."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableResumeDraftError(
            "ResumeElement rows are never deleted through the normal ORM instance API."
        )
