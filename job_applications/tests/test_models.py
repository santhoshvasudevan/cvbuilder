from django.db import IntegrityError, transaction
from django.test import TestCase

from job_applications.models import (
    _REASONING_LEVEL_VALUES,
    LLM_CAPABLE_STAGES,
    JobApplication,
    JobApplicationStageState,
    StageIdentifier,
    StageRun,
)

# V1 stage identifiers that must never reappear (docs/V2_REUSE_AUDIT.md; requirements.md
# Section 8; docs/IMPLEMENTATION_PLAN.md M1 acceptance).
LEGACY_V1_STAGE_IDENTIFIERS = {"AC_NORMALIZE", "AC_RANK", "AC_MATCH", "AC_BUILD", "AB_BUILD", "MEMORY_BUILD"}


class StageIdentifierTests(TestCase):
    def test_no_legacy_v1_stage_identifiers(self):
        self.assertEqual(set(StageIdentifier.values) & LEGACY_V1_STAGE_IDENTIFIERS, set())

    def test_llm_capable_stages_are_a_subset_of_all_stages(self):
        self.assertTrue(LLM_CAPABLE_STAGES.issubset(set(StageIdentifier.values)))

    def test_deterministic_stages_are_not_llm_capable(self):
        deterministic = {
            StageIdentifier.CANDIDATE_CONTEXT_BUILD,
            StageIdentifier.VALIDATE_DRAFT,
            StageIdentifier.VALIDATE_REFINED,
            StageIdentifier.GATE_1,
            StageIdentifier.GATE_2,
            StageIdentifier.RENDER,
        }
        self.assertEqual(deterministic & LLM_CAPABLE_STAGES, set())

    def test_canonical_vocabulary_matches_architecture_doc(self):
        expected = {
            "AJ_ANALYZE",
            "AC_ASSESS",
            "APS_POSITION",
            "AB_PLAN",
            "AB_DRAFT",
            "AB_CRITIQUE",
            "AB_REFINE",
            "QUALITY_EVAL",
            "CANDIDATE_CONTEXT_BUILD",
            "VALIDATE_DRAFT",
            "VALIDATE_REFINED",
            "GATE_1",
            "GATE_2",
            "RENDER",
        }
        self.assertEqual(set(StageIdentifier.values), expected)


class JobApplicationTests(TestCase):
    def test_create_with_defaults(self):
        job = JobApplication.objects.create(
            employer="Acme Corp",
            job_title="Solutions Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/1",
        )
        self.assertEqual(job.pipeline_phase, JobApplication.PipelinePhase.NEW)
        self.assertEqual(job.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)
        self.assertIsNotNone(job.created_at)
        self.assertIsNotNone(job.updated_at)

    def test_pipeline_phase_and_application_outcome_are_independent_fields(self):
        # V2-D021: these are two distinct enums, never merged into one status list.
        job = JobApplication.objects.create(
            employer="Acme Corp",
            job_title="Solutions Architect",
            source_type=JobApplication.SourceType.PASTED_TEXT,
            pipeline_phase=JobApplication.PipelinePhase.READY,
            application_outcome=JobApplication.ApplicationOutcome.APPLIED,
        )
        job.refresh_from_db()
        self.assertEqual(job.pipeline_phase, JobApplication.PipelinePhase.READY)
        self.assertEqual(job.application_outcome, JobApplication.ApplicationOutcome.APPLIED)

    def test_job_application_has_no_current_artifact_pointer_fields(self):
        # V2-D022: JobApplication must not grow a current_* FK per pipeline artifact.
        field_names = {f.name for f in JobApplication._meta.get_fields()}
        forbidden_prefixes = ("current_jra", "current_candidate", "current_ac", "current_positioning",
                               "current_resume", "current_refined")
        for name in field_names:
            for prefix in forbidden_prefixes:
                self.assertFalse(
                    name.startswith(prefix), f"unexpected current_* pointer field: {name}"
                )


class StageRunTests(TestCase):
    def setUp(self):
        self.job = JobApplication.objects.create(
            employer="Acme Corp",
            job_title="Solutions Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/1",
        )

    def test_create_stage_run(self):
        run = StageRun.objects.create(
            job_application=self.job,
            stage=StageIdentifier.AJ_ANALYZE,
            input_snapshot={"posting_text": "..."},
        )
        self.assertEqual(run.status, StageRun.Status.PENDING)
        self.assertIsNone(run.raw_structured_output)
        self.assertIsNone(run.approved_at)
        self.assertEqual(run.lock_version, 0)

    def test_stage_run_now_has_provider_model_fields(self):
        # V2-D036 predicted this exact, deliberate change: "M2 adds the five remaining fields
        # via a new migration once llm_provider.LLMProvider/LLMModel exist ... so its removal
        # [of the M1 test asserting their absence] in M2 is a deliberate, visible change rather
        # than a silent one." This test replaces
        # test_stage_run_has_no_provider_model_fields_yet now that M2 has landed.
        field_names = {f.name for f in StageRun._meta.get_fields()}
        for name in ("provider", "model", "reasoning_level", "max_output_tokens", "temperature"):
            self.assertIn(name, field_names)

    def test_stage_run_provider_model_fields_are_optional(self):
        # Null for deterministic stages (docs/ARCHITECTURE.md Section 5) -- creating a
        # deterministic StageRun with no LLM configuration must still succeed.
        run = StageRun.objects.create(
            job_application=self.job,
            stage=StageIdentifier.CANDIDATE_CONTEXT_BUILD,
            input_snapshot={},
        )
        self.assertIsNone(run.provider)
        self.assertIsNone(run.model)
        self.assertEqual(run.reasoning_level, "")
        self.assertIsNone(run.max_output_tokens)
        self.assertIsNone(run.temperature)

    def test_deterministic_stage_run_allowed(self):
        run = StageRun.objects.create(
            job_application=self.job,
            stage=StageIdentifier.CANDIDATE_CONTEXT_BUILD,
            input_snapshot={},
        )
        self.assertEqual(run.stage, StageIdentifier.CANDIDATE_CONTEXT_BUILD)


class JobApplicationStageStateTests(TestCase):
    def setUp(self):
        self.job = JobApplication.objects.create(
            employer="Acme Corp",
            job_title="Solutions Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/1",
        )
        self.run = StageRun.objects.create(
            job_application=self.job,
            stage=StageIdentifier.AJ_ANALYZE,
            input_snapshot={},
        )

    def test_create_and_link_current_stage_run(self):
        state = JobApplicationStageState.objects.create(
            job_application=self.job,
            stage=StageIdentifier.AJ_ANALYZE,
            current_stage_run=self.run,
        )
        self.assertIsNone(state.approved_stage_run)
        self.assertEqual(state.current_stage_run_id, self.run.id)

    def test_unique_together_job_application_and_stage(self):
        JobApplicationStageState.objects.create(
            job_application=self.job, stage=StageIdentifier.AJ_ANALYZE
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JobApplicationStageState.objects.create(
                    job_application=self.job, stage=StageIdentifier.AJ_ANALYZE
                )

    def test_same_stage_allowed_for_different_job_applications(self):
        other_job = JobApplication.objects.create(
            employer="Other Corp",
            job_title="Cloud Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/2",
        )
        JobApplicationStageState.objects.create(
            job_application=self.job, stage=StageIdentifier.AJ_ANALYZE
        )
        # must not raise
        JobApplicationStageState.objects.create(
            job_application=other_job, stage=StageIdentifier.AJ_ANALYZE
        )


class DeletionBehaviorTests(TestCase):
    """docs/ARCHITECTURE.md Section 5's on_delete choices (CASCADE for job_application FKs,
    SET_NULL for JobApplicationStageState's current/approved StageRun pointers) are Django-ORM
    -level behavior, not database-level ON DELETE rules (Django's default for ForeignKey), so
    they only take effect through the ORM's delete collector -- exercised here rather than
    inferred from the migration alone.
    """

    def setUp(self):
        self.job = JobApplication.objects.create(
            employer="Acme Corp",
            job_title="Solutions Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/1",
        )
        self.other_job = JobApplication.objects.create(
            employer="Other Corp",
            job_title="Cloud Architect",
            source_type=JobApplication.SourceType.URL,
            source_url="https://example.com/job/2",
        )
        self.run = StageRun.objects.create(
            job_application=self.job, stage=StageIdentifier.AJ_ANALYZE, input_snapshot={}
        )
        self.other_run = StageRun.objects.create(
            job_application=self.other_job, stage=StageIdentifier.AJ_ANALYZE, input_snapshot={}
        )
        self.state = JobApplicationStageState.objects.create(
            job_application=self.job,
            stage=StageIdentifier.AJ_ANALYZE,
            current_stage_run=self.run,
            approved_stage_run=self.run,
        )
        self.other_state = JobApplicationStageState.objects.create(
            job_application=self.other_job,
            stage=StageIdentifier.AJ_ANALYZE,
            current_stage_run=self.other_run,
        )

    def test_deleting_job_application_cascades_to_its_stage_runs(self):
        self.job.delete()
        self.assertFalse(StageRun.objects.filter(id=self.run.id).exists())

    def test_deleting_job_application_cascades_to_its_stage_state(self):
        self.job.delete()
        self.assertFalse(JobApplicationStageState.objects.filter(id=self.state.id).exists())

    def test_deleting_referenced_stage_run_sets_current_stage_run_null(self):
        self.run.delete()
        self.state.refresh_from_db()
        self.assertIsNone(self.state.current_stage_run)

    def test_deleting_referenced_stage_run_sets_approved_stage_run_null(self):
        self.run.delete()
        self.state.refresh_from_db()
        self.assertIsNone(self.state.approved_stage_run)

    def test_unrelated_records_remain_intact_after_job_application_deletion(self):
        self.job.delete()
        self.assertTrue(JobApplication.objects.filter(id=self.other_job.id).exists())
        self.assertTrue(StageRun.objects.filter(id=self.other_run.id).exists())
        self.assertTrue(JobApplicationStageState.objects.filter(id=self.other_state.id).exists())

    def test_unrelated_records_remain_intact_after_stage_run_deletion(self):
        self.run.delete()
        self.other_state.refresh_from_db()
        self.assertEqual(self.other_state.current_stage_run_id, self.other_run.id)


class ReasoningLevelVocabularyDriftTests(TestCase):
    """V2-D041 documents `job_applications._REASONING_LEVEL_VALUES` as a deliberate,
    dependency-cycle-avoiding echo of `llm_provider.models.ReasoningLevel`'s value set -- never a
    second, independently-evolving definition. This regression test is the guard that claim
    actually needs: without it, the two could silently drift (e.g. a new reasoning level added to
    one but not the other) with no test catching it, since `StageRun.reasoning_level`'s `choices`
    constraint and `llm_provider.validation.validate_reasoning_level`'s membership check are
    otherwise never compared against each other.
    """

    def test_stage_run_reasoning_level_values_match_llm_provider_reasoning_level(self):
        # Imported here, not at module level, so this file's own import order can never be the
        # thing that (re)introduces the job_applications <-> llm_provider circular import
        # _REASONING_LEVEL_VALUES exists specifically to avoid (V2-D041) -- only this test needs
        # llm_provider at all, and only at call time, once both apps are fully loaded.
        from llm_provider.models import ReasoningLevel

        self.assertEqual(set(_REASONING_LEVEL_VALUES), set(ReasoningLevel.values))
