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
    "scripted_ranking",
    "scripted_ranking_selecting_all",
    "scripted_agent_candidate",
    "scripted_normalization",
    "stub_identity_normalization",
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
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH)

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("candidate_matching.services.assess.get_adapter_for_stage", _get_adapter_for_stage):
        yield


@contextlib.contextmanager
def stub_identity_normalization():
    """Patch `candidate_matching.services.bounded_retrieval.expand_requirements_for_search` so
    every requirement's search text expands to just itself (canonical_english_text=text, every
    bounded term list empty) -- the shared default for every existing ranking-focused fixture/test
    that isn't specifically exercising the AC_NORMALIZE stage itself, so those tests don't also
    need a real `StageModelAssignment(AC_NORMALIZE)`. Tests that care about normalization behavior
    use `scripted_normalization` instead, which routes through the real adapter/schema."""
    from candidate_matching.schemas import RequirementNormalizationItem

    def _identity(requirements, *, posting_language):
        return {
            requirement["requirement_id"]: RequirementNormalizationItem(
                requirement_id=requirement["requirement_id"],
                canonical_english_text=requirement["text"],
                diagnostic_terms=[],
                equivalents=[],
                preserved_technical_terms=[],
                source_language=posting_language,
            )
            for requirement in requirements
        }

    with mock.patch(
        "candidate_matching.services.bounded_retrieval.expand_requirements_for_search", _identity
    ):
        yield


@contextlib.contextmanager
def scripted_normalization(fixed_response: dict):
    """Patch `candidate_matching.services.normalize.get_adapter_for_stage` so the AC_NORMALIZE
    call in the wrapped block returns `fixed_response`, via the real `FakeAdapter` and the real
    `RequirementNormalizationOutput` schema validation -- use this to test the normalization
    stage's actual wiring (including its schema/id-matching failure modes), as opposed to
    `stub_identity_normalization`'s bypass."""
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("candidate_matching.services.normalize.get_adapter_for_stage", _get_adapter_for_stage):
        yield


@contextlib.contextmanager
def scripted_ranking(fixed_response: dict):
    """Patch `candidate_matching.services.rank.get_adapter_for_stage` so the D-015 relevance-
    ranking call in the wrapped block returns `fixed_response`, via the real `FakeAdapter`. Use
    this when a test needs precise control over which claim_ids the ranking step "selects" (e.g.
    to prove a fabricated or missing-requirement ranking result is handled correctly)."""
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK)

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with stub_identity_normalization():
        with mock.patch(
            "candidate_matching.services.rank.get_adapter_for_stage", _get_adapter_for_stage
        ):
            yield


@contextlib.contextmanager
def scripted_ranking_selecting_all():
    """Convenience for tests that don't care about ranking behavior itself: patches
    `candidate_matching.services.bounded_retrieval.rank_relevance` so every claim actually present
    in the real candidate pool for a given call is returned as relevant for every requirement --
    still routed through a real `FakeAdapter`/`StageModelAssignment(AC_RANK)` and the real
    `RelevanceRankingOutput` schema validation, just without hand-authoring the exact candidate
    pool contents (which are the deterministic, data-dependent output of lexical scoring) in every
    test."""
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK)

    def _fake_rank_relevance(candidate_pool, requirements):
        from candidate_matching.services.rank import build_request

        fixed_response = {
            "rankings": [
                {
                    "requirement_id": requirement["requirement_id"],
                    "relevant_claim_ids": [claim.claim_id for claim in candidate_pool],
                }
                for requirement in requirements
            ]
        }
        adapter = FakeAdapter(model, fixed_response=fixed_response)
        request = build_request(candidate_pool, requirements)
        return adapter.generate(request)

    with stub_identity_normalization():
        with mock.patch(
            "candidate_matching.services.bounded_retrieval.rank_relevance", _fake_rank_relevance
        ):
            yield


@contextlib.contextmanager
def scripted_agent_candidate(assessment_response: dict):
    """The common case: script the D-015 ranking step to pass through every real candidate as
    relevant, and script the AC_MATCH assessment call with `assessment_response`. Equivalent to
    nesting `scripted_ranking_selecting_all()` and `scripted_assessment(assessment_response)`."""
    with scripted_ranking_selecting_all():
        with scripted_assessment(assessment_response):
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
