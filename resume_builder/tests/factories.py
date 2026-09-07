"""Test-only helpers, mirroring candidate_matching's own test factories pattern: every LLM call in
this suite is routed to the M2 `FakeAdapter` with a scripted response -- zero network, zero live
credentials."""

from __future__ import annotations

import contextlib
from unittest import mock

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import build_manifest_for_job_relevant_claim_ids
from candidate_matching.tests.factories import (
    freeze_revision,
    make_engagement,
    make_narrative_claim,
    make_revision,
)
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from job_applications.models import JobApplication
from job_intake.models import JobRequirement, JobRequirementAnalysis
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMModel, LLMProvider, StageModelAssignment

__all__ = [
    "freeze_revision",
    "make_engagement",
    "make_narrative_claim",
    "make_revision",
    "make_ready_for_gate2_application",
    "make_fake_stage_assignment",
    "scripted_generation",
    "valid_generation_response",
]


def make_fake_stage_assignment(stage=StageModelAssignment.Stage.AB_BUILD) -> LLMModel:
    provider, _ = LLMProvider.objects.get_or_create(
        name="Fake Provider",
        defaults={"provider_type": LLMProvider.ProviderType.FAKE, "credential_env_var": "FAKE_KEY"},
    )
    model, _ = LLMModel.objects.get_or_create(
        provider=provider, model_id="fake-model", defaults={"supports_structured_output": True}
    )
    StageModelAssignment.objects.update_or_create(stage=stage, defaults={"model": model})
    return model


@contextlib.contextmanager
def scripted_generation(fixed_response: dict):
    """Patch `resume_builder.services.generate.get_adapter_for_stage` so the AB generation call in
    the wrapped block returns `fixed_response`, via the real `FakeAdapter`."""
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage, *, requested_model_id=None, requested_reasoning_effort=None):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("resume_builder.services.generate.get_adapter_for_stage", _get_adapter_for_stage):
        yield


def valid_generation_response(engagement_id: str, claim_id: str, **overrides) -> dict:
    response = {
        "target_positioning": {
            "title_options": ["Senior Backend Engineer", "Backend Team Lead"],
            "recommended_title": "Senior Backend Engineer",
        },
        "summary_elements": [
            {
                "text": "Backend engineer with a track record of owning services end to end.",
                "supporting_memory_claim_ids": [claim_id],
                "matched_job_requirement_ids": [],
            }
        ],
        "experience_sections": [
            {
                "engagement_id": engagement_id,
                "bullets": [
                    {
                        "text": "Owned the payments service end to end.",
                        "supporting_memory_claim_ids": [claim_id],
                        "matched_job_requirement_ids": ["JR-001"],
                    }
                ],
            }
        ],
        "positioning_themes": [],
        "achievements": [],
        "skill_categories": [],
        "selected_skills": [
            {
                "text": "Python",
                "supporting_memory_claim_ids": [claim_id],
                "matched_job_requirement_ids": [],
            }
        ],
        "certifications": [],
        "languages": [],
        "positioning_guidance": {
            "preferred_role_positioning": "",
            "do_not_overstate": [],
            "terminology_preferences": [],
            "context_only_technologies": [],
            "naming_privacy_preferences": "",
            "resume_language": "en",
        },
    }
    response.update(overrides)
    return response


def make_ready_for_gate2_application(
    *, requirements: list[dict] | None = None
) -> tuple[JobApplication, str, str]:
    """Builds a JobApplication all the way through an approved Gate 1, with one narrative claim
    mapped to one APPROVED engagement -- everything M6 needs as a starting point. Returns
    (application, claim_id, engagement_id)."""
    rev = make_revision(status=CandidateMemory.Status.BUILDING)
    engagement = make_engagement()
    claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
    ClaimEngagementMapping.objects.create(
        memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
    )
    freeze_revision(rev, CandidateMemory.Status.ACTIVE)

    application = JobApplication.objects.create(pipeline_phase=JobApplication.PipelinePhase.NEW)
    jra = JobRequirementAnalysis.objects.create(
        job_application=application,
        version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="A pasted job posting about a role.",
        extracted_text="A pasted job posting about a role.",
        extracted_text_sha256="0" * 64,
        posting_language="en",
        employer="Globex Corporation",
        role_title="Senior Backend Engineer",
    )
    requirements = requirements or [
        {"category": "MANDATORY", "text": "Own the payments service end to end."},
    ]
    for order, requirement in enumerate(requirements, start=1):
        JobRequirement.objects.create(
            job_requirement_analysis=jra,
            requirement_id=f"JR-{order:03d}",
            order=order,
            category=requirement["category"],
            text=requirement["text"],
            source_context=requirement.get("source_context", ""),
        )
    application.advance_to_analysis(jra=jra)

    manifest = build_manifest_for_job_relevant_claim_ids(rev, [engagement], [claim.claim_id])
    fit_assessment = FitAssessment.objects.create(
        job_application=application, version=1, based_on_jra=jra,
        based_on_candidate_memory=rev,
        retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id],
        baseline_chronology_manifest=manifest,
    )
    for order, _requirement in enumerate(requirements, start=1):
        from candidate_matching.models import RequirementAssessment

        RequirementAssessment.objects.create(
            fit_assessment=fit_assessment,
            requirement_id=f"JR-{order:03d}",
            disposition=RequirementAssessment.Disposition.MATCH,
            supporting_memory_claim_ids=[claim.claim_id],
            explanation="Directly owned an equivalent service end to end.",
        )
    application.record_fit_assessment(fit_assessment)
    application.approve_gate1()

    return application, claim.claim_id, engagement.engagement_id
