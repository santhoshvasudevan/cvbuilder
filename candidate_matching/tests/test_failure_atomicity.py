"""Provider-failure and persistence-atomicity tests for build_fit_assessment (audit hardening,
2026-09-03): timeout, 429/retryable 5xx, non-retryable failure, malformed/schema-invalid output,
truncation, a simulated database failure during child-row persistence, and retry-after-failure
idempotency. These exercise the real `llm_provider.retry.execute_with_retry` bounded-retry policy
through the real orchestrator, not just llm_provider's own generic unit tests.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from candidate_memory.models import CandidateMemory
from llm_provider.adapters.base import BaseLLMAdapter
from llm_provider.errors import LLMErrorCategory, NormalizedLLMError
from llm_provider.models import LLMCallLog
from llm_provider.types import NormalizedLLMResult, TokenUsage

from ..models import FitAssessment
from ..services.fit_assessment import AgentCandidateError, build_fit_assessment
from .factories import (
    make_engagement,
    make_fake_stage_assignment,
    make_job_application_with_jra,
    make_narrative_claim,
    make_revision,
    scripted_ranking_selecting_all,
)


class _ScriptedResultsAdapter(BaseLLMAdapter):
    """Returns each `NormalizedLLMResult` in `results` in sequence for successive `_call_once`
    invocations (holding the last one once exhausted) -- lets a test drive
    `execute_with_retry`'s real bounded-retry behavior deterministically."""

    def __init__(self, llm_model, results: list[NormalizedLLMResult]):
        super().__init__(llm_model)
        self._results = list(results)
        self.call_count = 0

    def _call_once(self, request) -> NormalizedLLMResult:
        self.call_count += 1
        index = min(self.call_count - 1, len(self._results) - 1)
        return self._results[index]


def _ready_application():
    rev = make_revision(status=CandidateMemory.Status.BUILDING)
    from candidate_memory.tests.factories import freeze_revision

    engagement = make_engagement()
    claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
    from candidate_memory.models import ClaimEngagementMapping

    ClaimEngagementMapping.objects.create(
        memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
    )
    freeze_revision(rev, CandidateMemory.Status.ACTIVE)
    application = make_job_application_with_jra(
        requirements=[{"category": "MANDATORY", "text": "Own the payments service end to end."}]
    )
    return application, claim


def _patch_assess_adapter(results: list[NormalizedLLMResult]):
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage, *, requested_model_id=None):
        return _ScriptedResultsAdapter(model, results)

    return mock.patch("candidate_matching.services.assess.get_adapter_for_stage", _get_adapter_for_stage)


_VALID_CONTENT = {
    "requirement_assessments": [
        {
            "requirement_id": "JR-001",
            "disposition": "MATCH",
            "explanation": "Directly owned an equivalent service.",
            "gap_or_limitation": "",
            "supporting_memory_claim_ids": [],
            "supporting_engagement_ids": [],
        }
    ]
}


def _error_result(category, *, partial=False, retry_count=0) -> NormalizedLLMResult:
    return NormalizedLLMResult(
        error=NormalizedLLMError(category=category, message="simulated", partial_output_received=partial),
        retry_count=retry_count,
    )


def _success_result() -> NormalizedLLMResult:
    return NormalizedLLMResult(
        content=dict(_VALID_CONTENT), usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    )


class TimeoutAndTransientRetryTests(TestCase):
    def test_timeout_is_retried_and_recovers(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_error_result(LLMErrorCategory.TIMEOUT), _success_result()]):
                fit_assessment = build_fit_assessment(application)
        self.assertEqual(fit_assessment.version, 1)

    def test_persistent_timeout_is_bounded_and_eventually_fails(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_error_result(LLMErrorCategory.TIMEOUT)] * 5):
                with self.assertRaises(AgentCandidateError):
                    build_fit_assessment(application)
        # No FitAssessment persisted; exactly one LLMCallLog row (default RetryPolicy max_attempts=3).
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 0)
        self.assertEqual(LLMCallLog.objects.filter(stage="AC_MATCH").count(), 1)

    def test_429_rate_limit_is_retried_and_recovers(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_error_result(LLMErrorCategory.RATE_LIMIT), _success_result()]):
                fit_assessment = build_fit_assessment(application)
        self.assertEqual(fit_assessment.version, 1)

    def test_retryable_5xx_provider_internal_is_retried_and_recovers(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter(
                [_error_result(LLMErrorCategory.PROVIDER_INTERNAL), _success_result()]
            ):
                fit_assessment = build_fit_assessment(application)
        self.assertEqual(fit_assessment.version, 1)


class NonRetryableFailureTests(TestCase):
    def test_auth_failure_is_never_retried(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_error_result(LLMErrorCategory.AUTH), _success_result()]):
                with self.assertRaises(AgentCandidateError):
                    build_fit_assessment(application)
        # If it had retried, the second (success) result would have been consumed and a
        # FitAssessment would exist -- it must not.
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 0)

    def test_truncated_partial_output_is_never_retried_even_if_category_is_transient(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter(
                [_error_result(LLMErrorCategory.TIMEOUT, partial=True), _success_result()]
            ):
                with self.assertRaises(AgentCandidateError):
                    build_fit_assessment(application)
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 0)

    def test_malformed_schema_invalid_output_fails_without_persisting(self):
        application, _claim = _ready_application()
        model = make_fake_stage_assignment()

        def _get_adapter_for_stage(stage, *, requested_model_id=None):
            class _BadSchemaAdapter(BaseLLMAdapter):
                def _call_once(self, request):
                    return NormalizedLLMResult(content={"totally": "wrong shape"})

            return _BadSchemaAdapter(model)

        with scripted_ranking_selecting_all():
            with mock.patch(
                "candidate_matching.services.assess.get_adapter_for_stage", _get_adapter_for_stage
            ):
                with self.assertRaises(AgentCandidateError):
                    build_fit_assessment(application)
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 0)
        log = LLMCallLog.objects.filter(stage="AC_MATCH").latest("id")
        self.assertEqual(log.error_category, LLMErrorCategory.SCHEMA_VALIDATION.value)


class DatabaseFailureDuringPersistenceTests(TestCase):
    def test_db_failure_creating_a_requirement_assessment_rolls_back_the_whole_build(self):
        application, _claim = _ready_application()
        before = FitAssessment.objects.filter(job_application=application).count()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_success_result()]):
                with mock.patch(
                    "candidate_matching.services.fit_assessment.RequirementAssessment.objects.create",
                    side_effect=RuntimeError("simulated database failure"),
                ):
                    with self.assertRaises(RuntimeError):
                        build_fit_assessment(application)
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), before)
        application.refresh_from_db()
        self.assertIsNone(application.current_fit_assessment)

    def test_retry_after_a_db_failure_succeeds_without_duplicating_versions(self):
        application, _claim = _ready_application()
        with scripted_ranking_selecting_all():
            with _patch_assess_adapter([_success_result()]):
                with mock.patch(
                    "candidate_matching.services.fit_assessment.RequirementAssessment.objects.create",
                    side_effect=RuntimeError("simulated database failure"),
                ):
                    with self.assertRaises(RuntimeError):
                        build_fit_assessment(application)
            with _patch_assess_adapter([_success_result()]):
                fit_assessment = build_fit_assessment(application)
        self.assertEqual(fit_assessment.version, 1)
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 1)
