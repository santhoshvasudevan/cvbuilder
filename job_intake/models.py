"""Agent Jobber's persisted output (requirements.md Sec 5/16, docs/ARCHITECTURE.md Sec 4,
D-014). `JobRequirementAnalysis` is append-only/versioned per D-010: a completed version's own
fields and its `JobRequirement` children are never edited in place -- a re-analysis (M5's Gate-1
feedback flow) creates a new version instead. M4 itself only ever creates version 1 for a brand
new `JobApplication`; the version-uniqueness constraint below is forward-looking for that later
re-run path, not exercised by M4's own code paths.
"""

from __future__ import annotations

from django.db import models


class JobRequirementAnalysis(models.Model):
    class SourceType(models.TextChoices):
        URL = "URL", "URL"
        PASTED = "PASTED", "Pasted text"

    job_application = models.ForeignKey(
        "job_applications.JobApplication",
        on_delete=models.CASCADE,
        related_name="job_requirement_analyses",
    )
    version = models.PositiveIntegerField()

    # Source traceability (AJ-005): always stored regardless of intake path.
    source_type = models.CharField(max_length=10, choices=SourceType.choices)
    source_url = models.URLField(max_length=2000, blank=True, default="")
    original_input = models.TextField(
        help_text="The operator-supplied original input verbatim -- the pasted text, or the URL."
    )
    extracted_text = models.TextField(
        help_text="The posting text actually sent for analysis (for PASTED, equals "
        "original_input; for URL, the fetched+extracted main content)."
    )
    extracted_text_sha256 = models.CharField(max_length=64, editable=False)

    # Agent Jobber's structured output (requirements.md Sec 5).
    posting_language = models.CharField(max_length=16)
    employer = models.CharField(max_length=300, blank=True, default="")
    role_title = models.CharField(max_length=300, blank=True, default="")
    location = models.CharField(max_length=300, blank=True, default="")
    work_arrangement = models.CharField(max_length=100, blank=True, default="")
    screening_risks = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "version"], name="unique_jra_version_per_application"
            )
        ]
        ordering = ["job_application_id", "version"]

    def __str__(self) -> str:
        return f"JRA(app={self.job_application_id}, v{self.version})"

    def save(self, *args, **kwargs):
        # Append-only (D-010): a completed version is never mutated in place through the normal
        # ORM instance API. This mirrors candidate_memory's `_RevisionScopedModel` guard -- an
        # honest, application-layer-only guarantee, not a database-level constraint. Known,
        # accepted bypasses (same as candidate_memory's equivalent, pre-existing limitation):
        # `QuerySet.update()`/`bulk_update()`/raw SQL all skip instance `save()` entirely; and
        # deleting the owning `JobApplication` cascades (`on_delete=CASCADE`) via Django's bulk
        # delete collector, which does not call this model's overridden `delete()` below -- so a
        # `JobApplication` deletion *does* remove its `JobRequirementAnalysis`/`JobRequirement`
        # rows despite the "never deleted" wording there. This module does not claim database-
        # enforced immutability; only single-instance ORM calls are guarded.
        if self.pk is not None:
            raise ImmutableJobRequirementAnalysisError(
                "JobRequirementAnalysis is append-only once created -- create a new version "
                "instead of editing this one."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableJobRequirementAnalysisError(
            "JobRequirementAnalysis is append-only -- it is never deleted through the normal ORM "
            "instance API. (This guard does not cover cascade deletion triggered by deleting the "
            "owning JobApplication, nor QuerySet-level/raw-SQL bulk deletes -- see save() above.)"
        )


class ImmutableJobRequirementAnalysisError(Exception):
    pass


class JobRequirement(models.Model):
    class Category(models.TextChoices):
        MANDATORY = "MANDATORY", "Mandatory"
        PREFERRED = "PREFERRED", "Preferred"
        RESPONSIBILITY = "RESPONSIBILITY", "Responsibility"
        ATS_SIGNAL = "ATS_SIGNAL", "ATS signal"
        IMPLIED_EXPECTATION = "IMPLIED_EXPECTATION", "Implied expectation"

    job_requirement_analysis = models.ForeignKey(
        JobRequirementAnalysis, on_delete=models.CASCADE, related_name="requirements"
    )
    # Assigned by application code after schema validation (point 25) -- e.g. "JR-001". Never
    # trusted directly from arbitrary model-supplied numbering; the Pydantic schema (schemas.py)
    # doesn't even give the model a numbering field to populate.
    requirement_id = models.CharField(max_length=16)
    order = models.PositiveIntegerField(help_text="Deterministic 1-based ordering within this JRA version.")
    category = models.CharField(max_length=24, choices=Category.choices)
    text = models.TextField()
    source_context = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_requirement_analysis", "requirement_id"],
                name="unique_requirement_id_per_jra",
            ),
            models.UniqueConstraint(
                fields=["job_requirement_analysis", "order"], name="unique_requirement_order_per_jra"
            ),
        ]
        ordering = ["job_requirement_analysis_id", "order"]

    def __str__(self) -> str:
        return f"{self.requirement_id} ({self.category})"

    def save(self, *args, **kwargs):
        # Same honest, application-layer-only guarantee as JobRequirementAnalysis above: bulk
        # QuerySet operations, raw SQL, and cascade deletion from either the owning
        # JobRequirementAnalysis or (transitively) its JobApplication all bypass this guard.
        if self.pk is not None:
            raise ImmutableJobRequirementAnalysisError(
                "JobRequirement rows belong to an append-only JobRequirementAnalysis version and "
                "are never edited in place."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableJobRequirementAnalysisError(
            "JobRequirement rows are never deleted through the normal ORM instance API. (Cascade "
            "deletion from JobRequirementAnalysis/JobApplication and QuerySet-level/raw-SQL bulk "
            "deletes bypass this guard -- see JobRequirementAnalysis.save() above.)"
        )
