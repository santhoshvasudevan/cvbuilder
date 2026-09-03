"""Test-only helpers, mirroring job_intake/candidate_memory's own test factories pattern: every
LLM call in this suite is routed to the M2 `FakeAdapter` with a scripted response -- zero network,
zero live credentials."""

from __future__ import annotations

import contextlib
from unittest import mock

from django.contrib.auth.models import User

from candidate_memory.models import CandidateMemory, CareerEngagement, MemoryClaim
from candidate_memory.tests.factories import freeze_revision, make_claim, make_revision
from job_applications.models import JobApplication
from job_intake.models import JobRequirement, JobRequirementAnalysis
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMModel, LLMProvider, StageModelAssignment

__all__ = [
    "User",
    "freeze_revision",
    "make_claim",
    "make_revision",
    "make_active_revision",
    "make_engagement",
    "make_narrative_claim",
    "make_job_application_with_jra",
    "make_fake_stage_assignment",
    "scripted_assessment",
    "valid_assessment_response",
]


def make_engagement(**kwargs) -> CareerEngagement:
    defaults = dict(
        legal_employer="Ambigai Consultancy Services",
        client_organization="Ford Motor Company",
        approved_role_title="Senior Cloud Engineer",
        location="Cologne, Germany",
        start_year=2017,
        start_month=7,
        end_status=CareerEngagement.EndStatus.PRESENT,
        approval_status=CareerEngagement.ApprovalStatus.APPROVED,
    )
    defaults.update(kwargs)
    return CareerEngagement.objects.create(**defaults)


def make_narrative_claim(rev, **kwargs):
    defaults = dict(
        claim_type="responsibility",
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        resume_eligible=True,
    )
    defaults.update(kwargs)
    return make_claim(rev, **defaults)


def make_active_revision() -> CandidateMemory:
    rev = make_revision(status=CandidateMemory.Status.BUILDING)
    freeze_revision(rev, CandidateMemory.Status.ACTIVE)
    return rev


def make_job_application_with_jra(
    *, requirements: list[dict] | None = None, phase=JobApplication.PipelinePhase.ANALYSIS
) -> JobApplication:
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
    if phase != JobApplication.PipelinePhase.ANALYSIS:
        application.pipeline_phase = phase
        application.save(update_fields=["pipeline_phase"])
    return application


def make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH) -> LLMModel:
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
def scripted_assessment(fixed_response: dict):
    """Patch `candidate_matching.services.assess.get_adapter_for_stage` so the AC assessment call
    in the wrapped block returns `fixed_response`, via the real `FakeAdapter`."""
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("candidate_matching.services.assess.get_adapter_for_stage", _get_adapter_for_stage):
        yield


def valid_assessment_response(**overrides) -> dict:
    response = {
        "requirement_assessments": [
            {
                "requirement_id": "JR-001",
                "disposition": "MATCH",
                "explanation": "Directly owned an equivalent service end to end.",
                "gap_or_limitation": "",
                "supporting_memory_claim_ids": [],
                "supporting_engagement_ids": [],
            }
        ]
    }
    response.update(overrides)
    return response
