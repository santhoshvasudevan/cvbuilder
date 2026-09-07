"""Agent Candidate's persisted output (requirements.md Sec 6/16, D-014, D-019, M5).

`FitAssessment` is append-only/versioned per D-010, mirroring `job_intake.JobRequirementAnalysis`
exactly: a completed version's own fields and its `RequirementAssessment` children are never
edited in place -- a re-run (Gate-1 feedback targeting Agent Candidate, or a re-run triggered by a
new JobRequirementAnalysis version) creates a new version instead.
"""

from __future__ import annotations

from django.db import models


class FitAssessment(models.Model):
    job_application = models.ForeignKey(
        "job_applications.JobApplication", on_delete=models.CASCADE, related_name="fit_assessments"
    )
    version = models.PositiveIntegerField()
    based_on_jra = models.ForeignKey(
        "job_intake.JobRequirementAnalysis",
        on_delete=models.PROTECT,
        related_name="fit_assessments",
        help_text="The exact JobRequirementAnalysis version this assessment was built against "
        "(D-006 freshness identity -- compared against JobApplication.current_jra_id, never a "
        "timestamp).",
    )
    based_on_candidate_memory = models.ForeignKey(
        "candidate_memory.CandidateMemory",
        on_delete=models.PROTECT,
        related_name="fit_assessments",
        null=True,
        blank=True,
        help_text="D-037 pinned-evidence-identity correction: the exact CandidateMemory revision "
        "that was ACTIVE when this FitAssessment was created -- the frozen identity Agent Builder "
        "(M6) must use for this assessment forever after, regardless of which CandidateMemory "
        "revision is ACTIVE at M6 execution time. Nullable only for pre-D-037 legacy rows (e.g. "
        "FitAssessment id 9), which never recorded this identity and therefore have no valid "
        "baseline_chronology_manifest either -- every FitAssessment created through "
        "candidate_matching.services.fit_assessment.build_fit_assessment from this correction "
        "onward always sets this field; the production M6 path "
        "(resume_builder.services.context.build_builder_context) fails closed on a legacy row "
        "with this field null rather than treating null as 'use whatever is ACTIVE now'.",
    )
    baseline_chronology_manifest = models.JSONField(
        default=dict,
        blank=True,
        help_text="D-037 pinned-evidence-identity correction: the deterministic, zero-LLM "
        "baseline-chronology manifest computed once, at this FitAssessment's own creation time, "
        "against based_on_candidate_memory and the CareerEngagement/ClaimEngagementMapping state "
        "as it stood at that exact moment -- see candidate_matching.services.baseline_chronology."
        "build_baseline_manifest for the schema and candidate_matching.services.fit_assessment for "
        "where it is computed and persisted atomically with this row. Never recomputed at M6 time: "
        "resume_builder.services.context.build_builder_context reads this exact persisted value, "
        "never live CareerEngagement.approval_status/ClaimEngagementMapping.status/the currently "
        "ACTIVE CandidateMemory. An empty dict (the default) means 'no manifest recorded' -- true "
        "only for pre-D-037 legacy rows; a row created after this correction always has a non-"
        "empty, schema-validated manifest.",
    )
    retrieved_claim_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Exactly which confirmed, resume-eligible MemoryClaim IDs were retrieved and "
        "made available to Agent Candidate for this run -- an explicit, inspectable audit record "
        "of the bounded context actually provided, never the full CandidateMemory.",
    )
    retrieved_engagement_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Exactly which APPROVED CareerEngagement IDs were made available for this run.",
    )
    retrieval_manifest = models.JSONField(
        default=dict,
        blank=True,
        help_text="Inspectable record of the bounded retrieval pipeline for this run (audit "
        "hardening, 2026-09-03): eligible/duplicate/candidate/selected counts, excluded-with-"
        "reason counts, per-requirement selected claim IDs, rule counts, the estimated request "
        "token size, and the cap configuration in effect -- see "
        "candidate_matching.services.bounded_retrieval.RetrievalManifest. Never raw provider "
        "prompts or responses.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_application", "version"], name="unique_fit_assessment_version_per_application"
            )
        ]
        ordering = ["job_application_id", "version"]

    def __str__(self) -> str:
        return f"FitAssessment(app={self.job_application_id}, v{self.version})"

    def save(self, *args, **kwargs):
        # Append-only (D-010), the same honest, application-layer-only guarantee as
        # JobRequirementAnalysis -- see that model's save()/delete() docstring for the known,
        # accepted bypasses (QuerySet.update()/bulk_update()/raw SQL, cascade deletion).
        if self.pk is not None:
            raise ImmutableFitAssessmentError(
                "FitAssessment is append-only once created -- create a new version instead of "
                "editing this one."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableFitAssessmentError(
            "FitAssessment is append-only -- it is never deleted through the normal ORM instance "
            "API. (This guard does not cover cascade deletion triggered by deleting the owning "
            "JobApplication, nor QuerySet-level/raw-SQL bulk deletes -- see save() above.)"
        )


class ImmutableFitAssessmentError(Exception):
    pass


class RequirementAssessment(models.Model):
    class Disposition(models.TextChoices):
        MATCH = "MATCH", "Match"
        PARTIAL = "PARTIAL", "Partial"
        GAP = "GAP", "Gap"
        UNKNOWN = "UNKNOWN", "Unknown"

    fit_assessment = models.ForeignKey(
        FitAssessment, on_delete=models.CASCADE, related_name="requirement_assessments"
    )
    # Copied from job_intake.JobRequirement.requirement_id (e.g. "JR-001") rather than FK'd --
    # this row must remain fully readable/auditable even if a future data-retention policy ever
    # needs to prune old JobRequirement rows independently, and it keeps this app from taking a
    # hard schema dependency on job_intake's internal PK shape.
    requirement_id = models.CharField(max_length=16)
    disposition = models.CharField(max_length=10, choices=Disposition.choices)
    supporting_memory_claim_ids = models.JSONField(default=list, blank=True)
    supporting_engagement_ids = models.JSONField(default=list, blank=True)
    explanation = models.TextField(blank=True, default="")
    gap_or_limitation = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fit_assessment", "requirement_id"], name="unique_requirement_assessment_per_fit"
            )
        ]
        ordering = ["fit_assessment_id", "requirement_id"]

    def __str__(self) -> str:
        return f"{self.requirement_id}: {self.disposition}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableFitAssessmentError(
                "RequirementAssessment rows belong to an append-only FitAssessment version and "
                "are never edited in place."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableFitAssessmentError(
            "RequirementAssessment rows are never deleted through the normal ORM instance API."
        )

    @property
    def has_evidence(self) -> bool:
        return bool(self.supporting_memory_claim_ids) or bool(self.supporting_engagement_ids)
