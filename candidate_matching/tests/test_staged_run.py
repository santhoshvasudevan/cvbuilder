"""Phase J workflow/editing/configuration/finalization tests for the persistent, resumable M5
staged workflow (`candidate_matching.services.staged_run`, 2026-09-08, D-041). Every LLM call in
this suite is routed to the M2 `FakeAdapter` with a scripted response -- zero network, zero live
credentials, mirroring `test_fit_assessment.py`'s own convention.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMCallLog, LLMModel, LLMProvider, ReasoningEffort, StageModelAssignment

from ..models import AgentCandidateStage, AgentCandidateStageRevision, FitAssessment
from ..services import staged_run
from .factories import (
    freeze_revision,
    make_engagement,
    make_fake_stage_assignment,
    make_job_application_with_jra,
    make_narrative_claim,
    make_revision,
)

Stage = AgentCandidateStage.Stage
Status = AgentCandidateStage.Status


def _valid_normalization_response(requirement_ids=("JR-001",)):
    return {
        "items": [
            {
                "requirement_id": rid, "canonical_english_text": "Own the payments service end to end.",
                "diagnostic_terms": [], "equivalents": [], "preserved_technical_terms": [],
                "source_language": "en",
            }
            for rid in requirement_ids
        ]
    }


def _patch_adapter(module_path: str, model, fixed_response: dict):
    def _get_adapter_for_stage(stage, *, requested_model_id=None, requested_reasoning_effort=None):
        return FakeAdapter(model, fixed_response=fixed_response)

    return mock.patch(module_path, _get_adapter_for_stage)


class _Fixture:
    """Shared setup: one ACTIVE CandidateMemory revision with one narrative claim mapped to one
    APPROVED engagement, and one JobApplication with a single-requirement JRA."""

    def build(self, *, requirements=None):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=requirements
            or [{"category": "MANDATORY", "text": "Own the payments service end to end."}]
        )
        return application, rev, engagement, claim


def _run_normalize(stage, requirement_ids=("JR-001",)):
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
    with _patch_adapter(
        "candidate_matching.services.staged_run.get_adapter_for_stage", model,
        _valid_normalization_response(requirement_ids),
    ):
        return staged_run.execute_stage(stage, lock_version=stage.lock_version)


def _run_rank(stage, claim_ids, requirement_ids=("JR-001",)):
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK)
    response = {
        "rankings": [
            {"requirement_id": rid, "relevant_claim_ids": list(claim_ids)} for rid in requirement_ids
        ]
    }
    with _patch_adapter("candidate_matching.services.staged_run.get_adapter_for_stage", model, response):
        return staged_run.execute_stage(stage, lock_version=stage.lock_version)


def _run_match(stage, claim_id, requirement_id="JR-001"):
    model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH)
    response = {
        "requirement_assessments": [
            {
                "requirement_id": requirement_id, "disposition": "MATCH", "explanation": "Direct match.",
                "gap_or_limitation": "", "supporting_memory_claim_ids": [claim_id],
                "supporting_engagement_ids": [],
            }
        ]
    }
    with _patch_adapter("candidate_matching.services.staged_run.get_adapter_for_stage", model, response):
        return staged_run.execute_stage(stage, lock_version=stage.lock_version)


def _advance_to_ready_match(fixture_result):
    """Runs+approves AC_NORMALIZE and AC_RANK, leaving AC_MATCH READY. Returns (run, claim, match_stage)."""
    application, rev, engagement, claim = fixture_result
    run = staged_run.start_run(application)
    normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
    normalize_stage = _run_normalize(normalize_stage)
    normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)

    rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
    rank_stage.refresh_from_db()
    rank_stage = _run_rank(rank_stage, [claim.claim_id])
    rank_stage = staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)

    match_stage = staged_run.get_stage(run, Stage.AC_MATCH)
    match_stage.refresh_from_db()
    return run, claim, match_stage


class WorkflowTests(TestCase):
    def test_starting_a_run_makes_zero_provider_calls(self):
        application, *_ = _Fixture().build()
        baseline = LLMCallLog.objects.count()
        staged_run.start_run(application)
        self.assertEqual(LLMCallLog.objects.count(), baseline)

    def test_three_stages_are_created_in_order(self):
        application, *_ = _Fixture().build()
        run = staged_run.start_run(application)
        stages = list(run.stages.order_by("stage_order"))
        self.assertEqual([s.stage for s in stages], [Stage.AC_NORMALIZE, Stage.AC_RANK, Stage.AC_MATCH])
        self.assertEqual([s.stage_order for s in stages], [1, 2, 3])

    def test_later_stages_are_initially_blocked(self):
        application, *_ = _Fixture().build()
        run = staged_run.start_run(application)
        self.assertEqual(staged_run.get_stage(run, Stage.AC_NORMALIZE).status, Status.READY)
        self.assertEqual(staged_run.get_stage(run, Stage.AC_RANK).status, Status.DRAFT)
        self.assertEqual(staged_run.get_stage(run, Stage.AC_MATCH).status, Status.DRAFT)

    def test_executing_normalize_makes_exactly_one_logical_call_and_not_rank(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        baseline = LLMCallLog.objects.count()
        normalize_stage = _run_normalize(normalize_stage)
        self.assertEqual(LLMCallLog.objects.count(), baseline + 1)
        self.assertEqual(normalize_stage.status, Status.SUCCEEDED)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        self.assertEqual(rank_stage.status, Status.DRAFT)

    def test_approving_normalization_prepares_rank_without_calling_it(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        normalize_stage = _run_normalize(staged_run.get_stage(run, Stage.AC_NORMALIZE))
        baseline = LLMCallLog.objects.count()
        staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        self.assertEqual(LLMCallLog.objects.count(), baseline)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        self.assertEqual(rank_stage.status, Status.READY)
        self.assertIsNotNone(rank_stage.prepared_input)
        self.assertIn(claim.claim_id, [c["claim_id"] for c in rank_stage.prepared_input["candidate_pool"]])

    def test_executing_rank_does_not_execute_match(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        normalize_stage = _run_normalize(staged_run.get_stage(run, Stage.AC_NORMALIZE))
        staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_stage = _run_rank(rank_stage, [claim.claim_id])
        self.assertEqual(staged_run.get_stage(run, Stage.AC_MATCH).status, Status.DRAFT)

    def test_approving_rank_prepares_match_without_calling_it(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        self.assertEqual(match_stage.status, Status.READY)
        self.assertIn(claim.claim_id, [c["claim_id"] for c in match_stage.prepared_input["claims"]])

    def test_executing_match_does_not_create_a_fit_assessment_until_explicit_approval(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        baseline = FitAssessment.objects.count()
        match_stage = _run_match(match_stage, claim.claim_id)
        self.assertEqual(match_stage.status, Status.SUCCEEDED)
        self.assertEqual(FitAssessment.objects.count(), baseline)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        self.assertEqual(FitAssessment.objects.count(), baseline)  # approval alone still doesn't finalize

    def test_approving_valid_match_creates_exactly_one_fit_assessment(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        baseline = FitAssessment.objects.count()
        fit_assessment = staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        self.assertEqual(FitAssessment.objects.count(), baseline + 1)
        run.refresh_from_db()
        self.assertEqual(run.resulting_fit_assessment_id, fit_assessment.pk)

    def test_exactly_30_requirement_assessment_rows_for_a_30_requirement_jra(self):
        requirements = [{"category": "MANDATORY", "text": f"Requirement number {i}."} for i in range(30)]
        application, rev, engagement, claim = _Fixture().build(requirements=requirements)
        run = staged_run.start_run(application)
        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        req_ids = [f"JR-{i:03d}" for i in range(1, 31)]
        normalize_stage = _run_normalize(normalize_stage, requirement_ids=req_ids)
        normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_stage = _run_rank(rank_stage, [claim.claim_id], requirement_ids=req_ids)
        rank_stage = staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)
        match_stage = staged_run.get_stage(run, Stage.AC_MATCH)
        match_stage.refresh_from_db()
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH)
        response = {
            "requirement_assessments": [
                {
                    "requirement_id": rid, "disposition": "MATCH", "explanation": "ok",
                    "gap_or_limitation": "", "supporting_memory_claim_ids": [claim.claim_id],
                    "supporting_engagement_ids": [],
                }
                for rid in req_ids
            ]
        }
        with _patch_adapter("candidate_matching.services.staged_run.get_adapter_for_stage", model, response):
            match_stage = staged_run.execute_stage(match_stage, lock_version=match_stage.lock_version)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        fit_assessment = staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        self.assertEqual(fit_assessment.requirement_assessments.count(), 30)

    def test_gate_1_remains_unapproved_after_finalization(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        run.refresh_from_db()
        application = run.job_application
        application.refresh_from_db()
        self.assertNotEqual(application.pipeline_phase, application.PipelinePhase.PREPARATION)


class EditingTests(TestCase):
    def test_input_edits_are_run_local_and_never_mutate_canonical_records(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        original_jra_text = application.current_jra.requirements.get(requirement_id="JR-001").text
        edited = dict(normalize_stage.prepared_input)
        edited["requirements"] = [{"requirement_id": "JR-001", "text": "A hand-edited requirement text."}]
        staged_run.edit_stage_input(
            normalize_stage, edited_input=edited, lock_version=normalize_stage.lock_version
        )
        application.current_jra.refresh_from_db()
        self.assertEqual(
            application.current_jra.requirements.get(requirement_id="JR-001").text, original_jra_text
        )

    def test_invalid_claim_id_edit_is_rejected_for_ac_rank(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        normalize_stage = _run_normalize(staged_run.get_stage(run, Stage.AC_NORMALIZE))
        staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        prepared = rank_stage.prepared_input
        bad = dict(prepared)
        bad["candidate_pool"] = prepared["candidate_pool"] + [
            {"claim_id": "MC-9999-9999", "text": "fabricated", "claim_type": "responsibility",
             "subject_scope": "", "approved_engagement_ids": [], "grouped_claim_ids": ["MC-9999-9999"]}
        ]
        with self.assertRaises(staged_run.StageValidationError):
            staged_run.edit_stage_input(rank_stage, edited_input=bad, lock_version=rank_stage.lock_version)

    def test_cross_memory_claim_id_is_rejected_for_ac_match(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        bad = dict(match_stage.prepared_input)
        bad["claims"] = match_stage.prepared_input["claims"] + [
            {"claim_id": "MC-9999-9999", "text": "fabricated", "claim_type": "responsibility",
             "subject_scope": "", "approved_engagement_ids": []}
        ]
        with self.assertRaises(staged_run.StageValidationError):
            staged_run.edit_stage_input(match_stage, edited_input=bad, lock_version=match_stage.lock_version)

    def test_provider_output_is_immutable_after_an_output_edit(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        original_provider_output = dict(match_stage.provider_output)
        edited = dict(match_stage.operator_output)
        edited["requirement_assessments"][0]["explanation"] = "Operator-rewritten explanation."
        match_stage = staged_run.edit_stage_output(
            match_stage, edited_output=edited, lock_version=match_stage.lock_version
        )
        self.assertEqual(match_stage.provider_output, original_provider_output)
        self.assertNotEqual(match_stage.operator_output, original_provider_output)

    def test_edited_output_is_stored_as_a_separate_revision(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        baseline_revisions = match_stage.revisions.filter(
            kind=AgentCandidateStageRevision.Kind.OUTPUT
        ).count()
        edited = dict(match_stage.operator_output)
        edited["requirement_assessments"][0]["explanation"] = "Edited."
        staged_run.edit_stage_output(match_stage, edited_output=edited, lock_version=match_stage.lock_version)
        self.assertEqual(
            match_stage.revisions.filter(kind=AgentCandidateStageRevision.Kind.OUTPUT).count(),
            baseline_revisions + 1,
        )

    def test_malformed_edit_cannot_be_approved(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        malformed = {"requirement_assessments": [{"requirement_id": "JR-001"}]}  # missing required fields
        match_stage = staged_run.edit_stage_output(
            match_stage, edited_output=malformed, lock_version=match_stage.lock_version
        )
        self.assertEqual(match_stage.validation_state, "INVALID")
        with self.assertRaises(staged_run.StageValidationError):
            staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)

    def test_no_fabrication_violation_cannot_be_approved(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        fabricated = {
            "requirement_assessments": [
                {
                    "requirement_id": "JR-001", "disposition": "MATCH", "explanation": "x",
                    "gap_or_limitation": "", "supporting_memory_claim_ids": ["MC-9999-9999"],
                    "supporting_engagement_ids": [],
                }
            ]
        }
        match_stage = staged_run.edit_stage_output(
            match_stage, edited_output=fabricated, lock_version=match_stage.lock_version
        )
        self.assertEqual(match_stage.validation_state, "INVALID")
        with self.assertRaises(staged_run.StageValidationError):
            staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)

    def test_editing_an_upstream_stage_invalidates_every_downstream_stage(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        self.assertEqual(match_stage.status, Status.APPROVED)

        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        edited = dict(normalize_stage.operator_output)
        edited["items"][0]["canonical_english_text"] = "Something different now."
        staged_run.edit_stage_output(
            normalize_stage, edited_output=edited, lock_version=normalize_stage.lock_version
        )

        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        match_stage.refresh_from_db()
        self.assertEqual(rank_stage.status, Status.INVALIDATED)
        self.assertEqual(match_stage.status, Status.INVALIDATED)
        self.assertIsNone(rank_stage.prepared_input)
        self.assertIsNone(match_stage.approved_output)

    def test_historical_revisions_remain_available_after_invalidation(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        original_output_revision_count = match_stage.revisions.filter(
            kind=AgentCandidateStageRevision.Kind.OUTPUT
        ).count()

        normalize_stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        edited = dict(normalize_stage.operator_output)
        edited["items"][0]["canonical_english_text"] = "Different."
        staged_run.edit_stage_output(
            normalize_stage, edited_output=edited, lock_version=normalize_stage.lock_version
        )

        match_stage.refresh_from_db()
        self.assertEqual(
            match_stage.revisions.filter(kind=AgentCandidateStageRevision.Kind.OUTPUT).count(),
            original_output_revision_count,
        )
        self.assertGreaterEqual(original_output_revision_count, 1)


class ConfigurationTests(TestCase):
    def test_ac_normalize_default_matrix_is_mini_medium_16384(self):
        from llm_provider.services.gpt54_defaults import configure_gpt54_defaults

        configure_gpt54_defaults(dry_run=False)
        assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        self.assertEqual(assignment.model.model_id, "gpt-5.4-mini")
        self.assertEqual(assignment.default_reasoning_effort, ReasoningEffort.MEDIUM)
        self.assertEqual(assignment.max_output_tokens, 16384)

    def test_ac_rank_and_ac_match_default_matrix_is_full_medium_32768(self):
        from llm_provider.services.gpt54_defaults import configure_gpt54_defaults

        configure_gpt54_defaults(dry_run=False)
        for stage in (StageModelAssignment.Stage.AC_RANK, StageModelAssignment.Stage.AC_MATCH):
            assignment = StageModelAssignment.objects.get(stage=stage)
            self.assertEqual(assignment.model.model_id, "gpt-5.4")
            self.assertEqual(assignment.default_reasoning_effort, ReasoningEffort.MEDIUM)
            self.assertEqual(assignment.max_output_tokens, 32768)

    def test_ab_build_default_matrix_is_full_medium_32768(self):
        from llm_provider.services.gpt54_defaults import configure_gpt54_defaults

        configure_gpt54_defaults(dry_run=False)
        assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AB_BUILD)
        self.assertEqual(assignment.model.model_id, "gpt-5.4")
        self.assertEqual(assignment.default_reasoning_effort, ReasoningEffort.MEDIUM)
        self.assertEqual(assignment.max_output_tokens, 32768)

    def test_configure_gpt54_defaults_never_touches_an_already_configured_300s_timeout(self):
        """`configure_gpt54_defaults` never sets `read_timeout_seconds` at all (it is configured
        separately, e.g. via the registry admin) -- this proves it is preserved by omission, not
        cleared or overwritten, across a (re)run of the GPT-5.4 default-configuration pass."""
        from llm_provider.services.gpt54_defaults import configure_gpt54_defaults

        configure_gpt54_defaults(dry_run=False)
        for stage in (
            StageModelAssignment.Stage.AC_NORMALIZE, StageModelAssignment.Stage.AC_RANK,
            StageModelAssignment.Stage.AC_MATCH, StageModelAssignment.Stage.AB_BUILD,
        ):
            StageModelAssignment.objects.filter(stage=stage).update(read_timeout_seconds=300)

        configure_gpt54_defaults(dry_run=False)

        for stage in (
            StageModelAssignment.Stage.AC_NORMALIZE, StageModelAssignment.Stage.AC_RANK,
            StageModelAssignment.Stage.AC_MATCH, StageModelAssignment.Stage.AB_BUILD,
        ):
            assignment = StageModelAssignment.objects.get(stage=stage)
            self.assertEqual(assignment.read_timeout_seconds, 300)

    def test_model_capability_validation_rejects_a_budget_above_capability(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        model.max_output_tokens = 100
        model.full_clean()
        model.save()
        with self.assertRaises(staged_run.StagedRunError):
            staged_run.configure_stage(stage, requested_output_budget=200, lock_version=stage.lock_version)

    def test_per_run_override_does_not_mutate_the_default_assignment(self):
        # FAKE-type providers are unconditionally excluded from eligibility
        # (llm_provider.services.eligibility.eligible_models_for_stage), even under a test run --
        # a real-looking provider type is required for an override to resolve as eligible.
        provider, _ = LLMProvider.objects.get_or_create(
            name="Alt Provider",
            defaults={
                "provider_type": LLMProvider.ProviderType.NVIDIA_NIM,
                "credential_env_var": "X",
                "is_active": True,
            },
        )
        alt_model, _ = LLMModel.objects.get_or_create(
            provider=provider, model_id="alt-model",
            defaults={"supports_structured_output": True, "max_output_tokens": 5000, "is_active": True},
        )
        make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        default_assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        default_model_id = default_assignment.model_id

        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        staged_run.configure_stage(
            stage,
            requested_model_id=alt_model.pk,
            requested_output_budget=4000,
            lock_version=stage.lock_version,
        )
        default_assignment.refresh_from_db()
        self.assertEqual(default_assignment.model_id, default_model_id)

    def test_unavailable_model_cannot_be_selected(self):
        provider, _ = LLMProvider.objects.get_or_create(
            name="Inactive Provider",
            defaults={"provider_type": LLMProvider.ProviderType.FAKE, "credential_env_var": "X"},
        )
        inactive_model, _ = LLMModel.objects.get_or_create(
            provider=provider, model_id="inactive-model",
            defaults={"supports_structured_output": True, "is_active": False},
        )
        make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        with self.assertRaises(Exception):
            staged_run.configure_stage(
                stage, requested_model_id=inactive_model.pk, lock_version=stage.lock_version
            )

    def test_unsupported_reasoning_choice_is_rejected(self):
        provider, _ = LLMProvider.objects.get_or_create(
            name="No Reasoning Provider",
            defaults={"provider_type": LLMProvider.ProviderType.FAKE, "credential_env_var": "X"},
        )
        non_reasoning_model, _ = LLMModel.objects.get_or_create(
            provider=provider, model_id="no-reasoning-model",
            defaults={"supports_structured_output": True, "supports_reasoning": False},
        )
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        with self.assertRaises(Exception):
            staged_run.configure_stage(
                stage,
                requested_model_id=non_reasoning_model.pk,
                requested_reasoning_effort=ReasoningEffort.HIGH,
                lock_version=stage.lock_version,
            )

    def test_excessive_token_budget_is_rejected(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        model.max_output_tokens = 4096
        model.full_clean()
        model.save()
        with self.assertRaises(staged_run.StagedRunError):
            staged_run.configure_stage(stage, requested_output_budget=5096, lock_version=stage.lock_version)


class FinalizationIntegrityTests(TestCase):
    def test_candidate_memory_is_pinned_on_the_run(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        self.assertEqual(run.based_on_candidate_memory_id, rev.pk)

    def test_baseline_chronology_includes_the_approved_engagement_anchor_claim(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        run.refresh_from_db()
        manifest = run.baseline_chronology_manifest
        self.assertIn(claim.claim_id, manifest["claim_inclusion_reasons"])

    def test_language_evidence_is_included_in_the_baseline_manifest(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_engagement()  # an APPROVED engagement must exist for baseline chronology to compute
        language_claim = make_narrative_claim(
            rev, claim_type="language_proficiency", canonical_text_en="Fluent in German (C1)."
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Fluency in an additional language a plus."}]
        )
        run = staged_run.start_run(application)
        normalize_stage = _run_normalize(staged_run.get_stage(run, Stage.AC_NORMALIZE))
        normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_stage = _run_rank(rank_stage, [])  # AC_RANK selects nothing
        staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)
        run.refresh_from_db()
        self.assertIn(language_claim.claim_id, run.baseline_chronology_manifest["language_claim_ids"])

    def test_continental_and_maruti_style_engagements_do_not_depend_on_ac_rank_selection(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        continental = make_engagement(legal_employer="Continental AG", client_organization="")
        anchor_claim = make_narrative_claim(
            rev, canonical_text_en="Delivered ADAS software at Continental."
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=anchor_claim, career_engagement=continental,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Experience with cloud infrastructure."}]
        )
        run = staged_run.start_run(application)
        normalize_stage = _run_normalize(staged_run.get_stage(run, Stage.AC_NORMALIZE))
        normalize_stage = staged_run.approve_stage(normalize_stage, lock_version=normalize_stage.lock_version)
        rank_stage = staged_run.get_stage(run, Stage.AC_RANK)
        rank_stage.refresh_from_db()
        rank_stage = _run_rank(rank_stage, [])  # AC_RANK selects nothing relevant to Continental
        staged_run.approve_stage(rank_stage, lock_version=rank_stage.lock_version)
        run.refresh_from_db()
        self.assertIn(
            continental.engagement_id, run.baseline_chronology_manifest["approved_engagement_ids"]
        )
        self.assertIn(
            anchor_claim.claim_id,
            run.baseline_chronology_manifest["anchor_claim_ids_by_engagement"][continental.engagement_id],
        )

    def test_partial_stage_success_creates_no_partial_fit_assessment(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)  # SUCCEEDED but not approved
        baseline = FitAssessment.objects.count()
        with self.assertRaises(staged_run.StageSequenceError):
            staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        self.assertEqual(FitAssessment.objects.count(), baseline)

    def test_fit_assessment_creation_is_atomic(self):
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        baseline = FitAssessment.objects.count()
        with mock.patch(
            "candidate_matching.services.staged_run.RequirementAssessment.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        self.assertEqual(FitAssessment.objects.count(), baseline)

    def test_historical_fit_assessment_and_resume_draft_remain_immutable(self):
        """Guards the real repository invariant this task must never touch: JobApplication 9's
        historical FitAssessment 9 / ResumeDraft 4 stay append-only regardless of any staged-run
        activity elsewhere -- exercised here against locally-created equivalents rather than the
        real production rows, since this suite uses isolated test data."""
        run, claim, match_stage = _advance_to_ready_match(_Fixture().build())
        match_stage = _run_match(match_stage, claim.claim_id)
        match_stage = staged_run.approve_stage(match_stage, lock_version=match_stage.lock_version)
        fit_assessment = staged_run.finalize_run(run, lock_version=match_stage.lock_version)
        with self.assertRaises(Exception):
            fit_assessment.retrieval_manifest = {"tampered": True}
            fit_assessment.save()


class ConcurrencyTests(TestCase):
    def test_stale_lock_version_is_rejected_on_execute(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        model = make_fake_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE)
        with _patch_adapter(
            "candidate_matching.services.staged_run.get_adapter_for_stage",
            model,
            _valid_normalization_response(),
        ):
            with self.assertRaises(staged_run.ConcurrentStageModificationError):
                staged_run.execute_stage(stage, lock_version=stage.lock_version + 1)

    def test_a_running_stage_cannot_be_executed_again(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        AgentCandidateStage.objects.filter(pk=stage.pk).update(status=Status.RUNNING)
        stage.refresh_from_db()
        with self.assertRaises(staged_run.StageSequenceError):
            staged_run.execute_stage(stage, lock_version=stage.lock_version)

    def test_reconcile_stale_running_stage_marks_it_failed(self):
        application, rev, engagement, claim = _Fixture().build()
        run = staged_run.start_run(application)
        stage = staged_run.get_stage(run, Stage.AC_NORMALIZE)
        AgentCandidateStage.objects.filter(pk=stage.pk).update(status=Status.RUNNING)
        stage.refresh_from_db()
        stage = staged_run.reconcile_stale_running_stage(stage)
        self.assertEqual(stage.status, Status.FAILED)
        self.assertEqual(stage.failure_category, "INTERRUPTED")
