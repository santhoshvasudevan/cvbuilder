from django.db import IntegrityError, transaction
from django.test import TestCase

from job_applications.models import (
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

    def test_stage_run_has_no_provider_model_fields_yet(self):
        # V2-D036: provider/model/reasoning_level/max_output_tokens/temperature are added in M2
        # once llm_provider.LLMProvider/LLMModel exist -- M1 must not implement M2 scope.
        field_names = {f.name for f in StageRun._meta.get_fields()}
        for name in ("provider", "model", "reasoning_level", "max_output_tokens", "temperature"):
            self.assertNotIn(name, field_names)

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
