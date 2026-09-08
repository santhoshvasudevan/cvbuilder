"""Phase J M6-preparation tests for the operator-controlled AB_BUILD inspect/edit/approve workflow
(`resume_builder.services.staged_build`, 2026-09-08, D-041). FakeAdapter only -- zero live
provider calls."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from job_applications.models import JobApplication

from ..models import AgentBuilderRun, ResumeDraft
from ..services import staged_build
from .factories import make_fake_stage_assignment, make_ready_for_gate2_application, valid_generation_response

Status = AgentBuilderRun.Status


def _patch(response):
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage, *, requested_model_id=None, requested_reasoning_effort=None):
        from llm_provider.adapters.fake import FakeAdapter

        return FakeAdapter(model, fixed_response=response)

    return mock.patch("resume_builder.services.staged_build.get_adapter_for_stage", _get_adapter_for_stage)


class M6PreparationTests(TestCase):
    def test_starting_a_run_makes_zero_provider_calls(self):
        from llm_provider.models import LLMCallLog

        application, claim_id, engagement_id = make_ready_for_gate2_application()
        baseline = LLMCallLog.objects.count()
        staged_build.start_ab_run(application)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_input_inspection_and_editing_is_zero_call(self):
        from llm_provider.models import LLMCallLog

        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        self.assertIsNotNone(run.prepared_input)
        baseline = LLMCallLog.objects.count()
        edited = dict(run.prepared_input)
        staged_build.edit_ab_input(run, edited_input=edited, lock_version=run.lock_version)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_execution_requires_explicit_call_and_produces_no_draft(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        baseline = ResumeDraft.objects.count()
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        self.assertEqual(run.status, Status.SUCCEEDED)
        self.assertEqual(ResumeDraft.objects.count(), baseline)

    def test_provider_output_does_not_automatically_create_a_resume_draft(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft_id)

    def test_only_approved_validated_output_creates_the_resume_draft(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        baseline = ResumeDraft.objects.count()
        draft = staged_build.approve_ab_run_and_create_draft(run, lock_version=run.lock_version)
        self.assertEqual(ResumeDraft.objects.count(), baseline + 1)
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft_id, draft.pk)

    def test_gate_2_remains_unapproved_after_approval(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        staged_build.approve_ab_run_and_create_draft(run, lock_version=run.lock_version)
        application.refresh_from_db()
        self.assertNotEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_edited_output_is_validated_separately_from_provider_output(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        original_provider_output = dict(run.provider_output)
        edited = dict(run.operator_output)
        edited["target_positioning"]["recommended_title"] = "Staff Backend Engineer"
        run = staged_build.edit_ab_output(run, edited_output=edited, lock_version=run.lock_version)
        self.assertEqual(run.provider_output, original_provider_output)
        self.assertEqual(
            run.operator_output["target_positioning"]["recommended_title"], "Staff Backend Engineer"
        )

    def test_diff_reviewable_before_approval_fabricated_edit_rejected(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        run = staged_build.start_ab_run(application)
        with _patch(valid_generation_response(engagement_id, claim_id)):
            run = staged_build.execute_ab_run(run, lock_version=run.lock_version)
        edited = dict(run.operator_output)
        edited["summary_elements"][0]["supporting_memory_claim_ids"] = ["MC-fabricated"]
        run = staged_build.edit_ab_output(run, edited_output=edited, lock_version=run.lock_version)
        self.assertEqual(run.validation_state, "INVALID")
        with self.assertRaises(staged_build.AgentBuilderValidationError):
            staged_build.approve_ab_run_and_create_draft(run, lock_version=run.lock_version)
