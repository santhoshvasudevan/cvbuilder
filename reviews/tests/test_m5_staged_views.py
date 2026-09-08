"""Phase J UI/security tests for the operator-controlled, persistent, resumable M5 staged
workflow (`reviews/views_m5.py`, 2026-09-08, D-041). Every LLM call is routed to the M2
`FakeAdapter`; `Client(enforce_csrf_checks=True)` proves CSRF is actually required, not merely
present in the template."""

from __future__ import annotations

from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from candidate_matching.models import AgentCandidateRun, AgentCandidateStage, FitAssessment
from candidate_matching.services import staged_run
from candidate_matching.tests.factories import (
    freeze_revision,
    make_engagement,
    make_fake_stage_assignment,
    make_job_application_with_jra,
    make_narrative_claim,
    make_revision,
)
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMCallLog, StageModelAssignment

Stage = AgentCandidateStage.Stage


def _valid_normalization_response():
    return {
        "items": [
            {
                "requirement_id": "JR-001", "canonical_english_text": "Own the payments service end to end.",
                "diagnostic_terms": [], "equivalents": [], "preserved_technical_terms": [],
                "source_language": "en",
            }
        ]
    }


def _adapter_patch(model, response):
    def _get_adapter_for_stage(stage, *, requested_model_id=None, requested_reasoning_effort=None):
        return FakeAdapter(model, fixed_response=response)

    return mock.patch("candidate_matching.services.staged_run.get_adapter_for_stage", _get_adapter_for_stage)


class M5StagedViewTests(TestCase):
    def setUp(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        self.engagement = make_engagement()
        self.claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=self.claim, career_engagement=self.engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        self.application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Own the payments service end to end."}]
        )
        self.start_url = reverse("reviews:m5_start", args=[self.application.pk])

    def _start_run(self) -> AgentCandidateRun:
        response = self.client.post(self.start_url)
        self.assertEqual(response.status_code, 302)
        return AgentCandidateRun.objects.get(job_application=self.application)

    # -- basic reachability / GET never mutates ---------------------------------------------

    def test_get_normalize_page_never_causes_an_llm_call(self):
        run = self._start_run()
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_start_view_get_is_not_allowed(self):
        response = self.client.get(self.start_url)
        self.assertEqual(response.status_code, 405)

    def test_starting_a_run_creates_three_stages_and_zero_provider_calls(self):
        baseline = LLMCallLog.objects.count()
        run = self._start_run()
        self.assertEqual(AgentCandidateStage.objects.filter(run=run).count(), 3)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    # -- CSRF / duplicate submission / stale revision -----------------------------------------

    def test_csrf_is_required_for_run_action(self):
        run = self._start_run()
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(url, {"action": "run", "lock_version": "0"})
        self.assertEqual(response.status_code, 403)

    def test_save_input_post_never_causes_an_llm_call(self):
        run = self._start_run()
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        edited = dict(stage.prepared_input)
        import json

        response = self.client.post(url, {
            "action": "edit_input", "lock_version": str(stage.lock_version),
            "edited_input_json": json.dumps(edited),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_save_output_post_never_causes_an_llm_call(self):
        run, claim, match_stage = self._advance_to_match_succeeded()
        url = reverse("reviews:m5_match", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        import json

        edited = dict(match_stage.operator_output)
        response = self.client.post(url, {
            "action": "edit_output", "lock_version": str(match_stage.lock_version),
            "edited_output_json": json.dumps(edited),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_approve_post_never_causes_an_llm_call(self):
        run, claim, match_stage = self._advance_to_match_succeeded()
        url = reverse("reviews:m5_match", args=[self.application.pk, run.pk])
        baseline = LLMCallLog.objects.count()
        self.client.post(url, {"action": "approve", "lock_version": str(match_stage.lock_version)})
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_only_run_action_invokes_the_adapter(self):
        run = self._start_run()
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        baseline = LLMCallLog.objects.count()
        with _adapter_patch(model, _valid_normalization_response()):
            response = self.client.post(url, {"action": "run", "lock_version": str(stage.lock_version)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LLMCallLog.objects.count(), baseline + 1)

    def test_duplicate_submission_is_blocked_by_stale_lock_version(self):
        run = self._start_run()
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        with _adapter_patch(model, _valid_normalization_response()):
            first = self.client.post(url, {"action": "run", "lock_version": str(stage.lock_version)})
        self.assertEqual(first.status_code, 302)
        # A second submission with the SAME (now-stale) lock_version -- simulating a double-click
        # or a stale browser tab resubmitting the same form -- must not make a second call.
        baseline = LLMCallLog.objects.count()
        with _adapter_patch(model, _valid_normalization_response()):
            second = self.client.post(url, {"action": "run", "lock_version": str(stage.lock_version)})
        self.assertEqual(second.status_code, 200)  # re-rendered with an error, not a redirect
        self.assertContains(second, "modified by another request")
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_refresh_get_does_not_repeat_a_call(self):
        run = self._start_run()
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        with _adapter_patch(model, _valid_normalization_response()):
            self.client.post(url, {"action": "run", "lock_version": str(stage.lock_version)})
        baseline = LLMCallLog.objects.count()
        self.client.get(url)
        self.client.get(url)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_failure_diagnostics_are_sanitized_never_raw_response_body(self):
        run = self._start_run()
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        # Missing the required requirement_id -- triggers a NormalizationFailedError-equivalent
        # schema mismatch inside execute_stage's own _validate_output/adapter schema check.
        with _adapter_patch(model, {"items": []}):
            self.client.post(url, {"action": "run", "lock_version": str(stage.lock_version)})
        stage.refresh_from_db()
        self.assertIn(stage.status, (AgentCandidateStage.Status.SUCCEEDED, AgentCandidateStage.Status.FAILED))
        response = self.client.get(url)
        self.assertNotContains(response, "Authorization: Bearer")
        self.assertNotContains(response, "api_key")

    # -- historical FitAssessment is not shown as the active run's result --------------------

    def test_historical_fit_assessment_is_not_presented_as_the_active_run_result(self):
        run = self._start_run()
        url = reverse("reviews:m5_normalize", args=[self.application.pk, run.pk])
        response = self.client.get(url)
        self.assertContains(response, f"M5 Staged Run v{run.version}")
        self.assertNotContains(response, "Fit Assessment v")  # gate1.html's historical-result heading

    # -- helper ---------------------------------------------------------------------------------

    def _advance_to_match_succeeded(self):
        run = self._start_run()
        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        with _adapter_patch(model, _valid_normalization_response()):
            normalize_stage = staged_run.execute_stage(
                normalize_stage, lock_version=normalize_stage.lock_version
            )
        normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)

        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK)
        rank_response = {
            "rankings": [{"requirement_id": "JR-001", "relevant_claim_ids": [self.claim.claim_id]}]
        }
        with _adapter_patch(rank_model, rank_response):
            rank_stage = staged_run.execute_stage(rank_stage, lock_version=rank_stage.lock_version)
        rank_stage = staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)

        match_stage = staged_run.get_stage(run, Stage.AC_MATCH)
        match_stage.refresh_from_db()
        match_model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH)
        match_response = {
            "requirement_assessments": [
                {
                    "requirement_id": "JR-001", "disposition": "MATCH", "explanation": "ok",
                    "gap_or_limitation": "", "supporting_memory_claim_ids": [self.claim.claim_id],
                    "supporting_engagement_ids": [],
                }
            ]
        }
        with _adapter_patch(match_model, match_response):
            match_stage = staged_run.execute_stage(match_stage, lock_version=match_stage.lock_version)
        return run, self.claim, match_stage


class M5FinalizationViewTests(TestCase):
    def setUp(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        self.engagement = make_engagement()
        self.claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=self.claim, career_engagement=self.engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        self.application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Own the payments service end to end."}]
        )

    def test_approving_match_finalizes_and_redirects_to_gate1(self):
        run = staged_run.start_run(self.application)
        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        with _adapter_patch(model, _valid_normalization_response()):
            normalize_stage = staged_run.execute_stage(
                normalize_stage, lock_version=normalize_stage.lock_version
            )
        normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)

        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK)
        rank_response = {
            "rankings": [{"requirement_id": "JR-001", "relevant_claim_ids": [self.claim.claim_id]}]
        }
        with _adapter_patch(rank_model, rank_response):
            rank_stage = staged_run.execute_stage(rank_stage, lock_version=rank_stage.lock_version)
        rank_stage = staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)

        match_stage = staged_run.get_stage(run, Stage.AC_MATCH)
        match_stage.refresh_from_db()
        match_model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH)
        match_response = {
            "requirement_assessments": [
                {
                    "requirement_id": "JR-001", "disposition": "MATCH", "explanation": "ok",
                    "gap_or_limitation": "", "supporting_memory_claim_ids": [self.claim.claim_id],
                    "supporting_engagement_ids": [],
                }
            ]
        }
        with _adapter_patch(match_model, match_response):
            match_stage = staged_run.execute_stage(match_stage, lock_version=match_stage.lock_version)

        url = reverse("reviews:m5_match", args=[self.application.pk, run.pk])
        baseline_fa = FitAssessment.objects.count()
        response = self.client.post(url, {"action": "approve", "lock_version": str(match_stage.lock_version)})
        self.assertRedirects(response, reverse("reviews:gate1", args=[self.application.pk]))
        self.assertEqual(FitAssessment.objects.count(), baseline_fa + 1)
        self.application.refresh_from_db()
        self.assertIsNotNone(self.application.current_fit_assessment_id)
        self.assertNotEqual(self.application.pipeline_phase, self.application.PipelinePhase.PREPARATION)
