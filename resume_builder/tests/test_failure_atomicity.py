"""Provider-failure and persistence-atomicity tests for build_resume_draft (audit hardening,
2026-09-03) -- mirrors candidate_matching.tests.test_failure_atomicity for the AB_BUILD stage.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from llm_provider.adapters.base import BaseLLMAdapter
from llm_provider.errors import LLMErrorCategory, NormalizedLLMError
from llm_provider.models import LLMCallLog
from llm_provider.types import NormalizedLLMResult, TokenUsage

from ..models import ResumeDraft
from ..services.build import ResumeBuilderError, build_resume_draft
from .factories import make_fake_stage_assignment, make_ready_for_gate2_application, valid_generation_response


class _ScriptedResultsAdapter(BaseLLMAdapter):
    def __init__(self, llm_model, results: list[NormalizedLLMResult]):
        super().__init__(llm_model)
        self._results = list(results)
        self.call_count = 0

    def _call_once(self, request) -> NormalizedLLMResult:
        self.call_count += 1
        index = min(self.call_count - 1, len(self._results) - 1)
        return self._results[index]


def _patch_generate_adapter(results: list[NormalizedLLMResult]):
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage):
        return _ScriptedResultsAdapter(model, results)

    return mock.patch("resume_builder.services.generate.get_adapter_for_stage", _get_adapter_for_stage)


def _error_result(category, *, partial=False) -> NormalizedLLMResult:
    return NormalizedLLMResult(
        error=NormalizedLLMError(category=category, message="simulated", partial_output_received=partial)
    )


def _success_result(content: dict) -> NormalizedLLMResult:
    return NormalizedLLMResult(
        content=dict(content), usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    )


class TimeoutAndTransientRetryTests(TestCase):
    def test_timeout_is_retried_and_recovers(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter([_error_result(LLMErrorCategory.TIMEOUT), _success_result(response)]):
            draft = build_resume_draft(application)
        self.assertEqual(draft.version, 1)

    def test_persistent_timeout_is_bounded_and_eventually_fails(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with _patch_generate_adapter([_error_result(LLMErrorCategory.TIMEOUT)] * 5):
            with self.assertRaises(ResumeBuilderError):
                build_resume_draft(application)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 0)
        self.assertEqual(LLMCallLog.objects.filter(stage="AB_BUILD").count(), 1)

    def test_429_rate_limit_is_retried_and_recovers(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter([_error_result(LLMErrorCategory.RATE_LIMIT), _success_result(response)]):
            draft = build_resume_draft(application)
        self.assertEqual(draft.version, 1)

    def test_retryable_5xx_provider_internal_is_retried_and_recovers(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter(
            [_error_result(LLMErrorCategory.PROVIDER_INTERNAL), _success_result(response)]
        ):
            draft = build_resume_draft(application)
        self.assertEqual(draft.version, 1)


class NonRetryableFailureTests(TestCase):
    def test_auth_failure_is_never_retried(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter([_error_result(LLMErrorCategory.AUTH), _success_result(response)]):
            with self.assertRaises(ResumeBuilderError):
                build_resume_draft(application)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 0)

    def test_truncated_partial_output_is_never_retried_even_if_category_is_transient(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter(
            [_error_result(LLMErrorCategory.TIMEOUT, partial=True), _success_result(response)]
        ):
            with self.assertRaises(ResumeBuilderError):
                build_resume_draft(application)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 0)

    def test_malformed_schema_invalid_output_fails_without_persisting(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        model = make_fake_stage_assignment()

        def _get_adapter_for_stage(stage):
            class _BadSchemaAdapter(BaseLLMAdapter):
                def _call_once(self, request):
                    return NormalizedLLMResult(content={"totally": "wrong shape"})

            return _BadSchemaAdapter(model)

        with mock.patch("resume_builder.services.generate.get_adapter_for_stage", _get_adapter_for_stage):
            with self.assertRaises(ResumeBuilderError):
                build_resume_draft(application)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 0)
        log = LLMCallLog.objects.filter(stage="AB_BUILD").latest("id")
        self.assertEqual(log.error_category, LLMErrorCategory.SCHEMA_VALIDATION.value)


class DatabaseFailureDuringPersistenceTests(TestCase):
    def test_db_failure_creating_a_resume_element_rolls_back_the_whole_build(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        before = ResumeDraft.objects.filter(job_application=application).count()
        with _patch_generate_adapter([_success_result(response)]):
            with mock.patch(
                "resume_builder.services.build.ResumeElement.objects.create",
                side_effect=RuntimeError("simulated database failure"),
            ):
                with self.assertRaises(RuntimeError):
                    build_resume_draft(application)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), before)
        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft)

    def test_retry_after_a_db_failure_succeeds_without_duplicating_versions(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        response = valid_generation_response(engagement_id, claim_id)
        with _patch_generate_adapter([_success_result(response)]):
            with mock.patch(
                "resume_builder.services.build.ResumeElement.objects.create",
                side_effect=RuntimeError("simulated database failure"),
            ):
                with self.assertRaises(RuntimeError):
                    build_resume_draft(application)
        with _patch_generate_adapter([_success_result(response)]):
            draft = build_resume_draft(application)
        self.assertEqual(draft.version, 1)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 1)
