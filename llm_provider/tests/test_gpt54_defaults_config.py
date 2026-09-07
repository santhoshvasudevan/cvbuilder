"""Idempotent registry configuration for the paid GPT-5.4 model defaults (2026-09-07, D-039):
`llm_provider/services/gpt54_defaults.py` and its thin management-command wrapper.

Deterministic only -- no network, no live credential. Exercises the ORM directly against the test
database, mirroring `test_openrouter_free_router_config.py`'s own pattern.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from ..models import LLMCallLog, LLMModel, LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.gpt54_defaults import (
    GPT54_MAX_OUTPUT_TOKENS,
    GPT54_MINI_MODEL_ID,
    GPT54_MODEL_ID,
    STAGE_DEFAULT_MATRIX,
    configure_gpt54_defaults,
)
from ..services.openrouter_free_router import ZAI_MODEL_ID, configure_openrouter_free_router
from .factories import make_model, make_provider, make_stage_assignment

ALL_STAGES = list(StageModelAssignment.Stage.values)

EXPECTED_MATRIX = {
    StageModelAssignment.Stage.MEMORY_BUILD: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AJ_ANALYZE: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AC_NORMALIZE: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AC_MATCH: (GPT54_MODEL_ID, ReasoningEffort.HIGH),
    StageModelAssignment.Stage.AC_RANK: (GPT54_MODEL_ID, ReasoningEffort.HIGH),
    StageModelAssignment.Stage.AB_BUILD: (GPT54_MODEL_ID, ReasoningEffort.MEDIUM),
}


class ExactModelIdentifierTests(TestCase):
    def test_mini_model_id_is_exact(self):
        self.assertEqual(GPT54_MINI_MODEL_ID, "openai/gpt-5.4-mini")

    def test_full_model_id_is_exact(self):
        self.assertEqual(GPT54_MODEL_ID, "openai/gpt-5.4")

    def test_direct_openai_identifiers_are_never_used(self):
        """Requirements Sec 1: never send the direct-OpenAI identifiers through the OpenRouter
        adapter -- both configured ids must carry the 'openai/' OpenRouter routing prefix."""
        self.assertNotEqual(GPT54_MINI_MODEL_ID, "gpt-5.4-mini")
        self.assertNotEqual(GPT54_MODEL_ID, "gpt-5.4")


class CompleteDefaultMatrixTests(TestCase):
    def test_matrix_covers_every_implemented_stage(self):
        self.assertEqual(set(STAGE_DEFAULT_MATRIX), set(ALL_STAGES))

    def test_matrix_matches_the_operator_approved_assignment_exactly(self):
        self.assertEqual(STAGE_DEFAULT_MATRIX, EXPECTED_MATRIX)

    def test_real_run_assigns_every_stage_to_the_exact_matrix(self):
        configure_gpt54_defaults(dry_run=False)
        for stage, (expected_model_id, expected_reasoning) in EXPECTED_MATRIX.items():
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertEqual(assignment.model.model_id, expected_model_id)
            self.assertEqual(assignment.default_reasoning_effort, expected_reasoning)


class RegistryCapabilityTests(TestCase):
    def test_both_models_are_active_structured_output_reasoning_capable(self):
        configure_gpt54_defaults(dry_run=False)
        for model_id in (GPT54_MINI_MODEL_ID, GPT54_MODEL_ID):
            model = LLMModel.objects.get(model_id=model_id)
            self.assertTrue(model.is_active)
            self.assertTrue(model.supports_structured_output)
            self.assertTrue(model.supports_reasoning)

    def test_output_budget_never_exceeds_the_pre_existing_conservative_ceiling(self):
        """Requirements Sec 4: 'without unnecessarily increasing the application's current
        per-call output budget' -- matches the exact value the openrouter/free migration (D-038)
        already established as this codebase's known-good ceiling."""
        configure_gpt54_defaults(dry_run=False)
        for model_id in (GPT54_MINI_MODEL_ID, GPT54_MODEL_ID):
            model = LLMModel.objects.get(model_id=model_id)
            self.assertEqual(model.max_output_tokens, 8192)
            self.assertEqual(model.max_output_tokens, GPT54_MAX_OUTPUT_TOKENS)

    def test_display_names_are_exact(self):
        configure_gpt54_defaults(dry_run=False)
        self.assertEqual(LLMModel.objects.get(model_id=GPT54_MINI_MODEL_ID).display_name, "GPT-5.4 Mini")
        self.assertEqual(LLMModel.objects.get(model_id=GPT54_MODEL_ID).display_name, "GPT-5.4")


class DryRunTests(TestCase):
    def test_dry_run_writes_nothing(self):
        report = configure_gpt54_defaults(dry_run=True)
        self.assertTrue(report.provider_created)
        self.assertTrue(report.mini_model_created)
        self.assertTrue(report.full_model_created)
        self.assertEqual(len(report.reassigned_stages), len(ALL_STAGES))
        self.assertEqual(LLMProvider.objects.count(), 0)
        self.assertEqual(LLMModel.objects.count(), 0)
        self.assertEqual(StageModelAssignment.objects.count(), 0)


class IdempotencyTests(TestCase):
    def test_repeated_invocation_reports_no_further_changes(self):
        configure_gpt54_defaults(dry_run=False)
        second = configure_gpt54_defaults(dry_run=False)
        self.assertEqual(second.reassigned_stages, [])
        self.assertFalse(second.provider_created)
        self.assertFalse(second.mini_model_created)
        self.assertFalse(second.full_model_created)

    def test_repeated_invocation_creates_no_duplicate_rows(self):
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        self.assertEqual(LLMModel.objects.filter(model_id=GPT54_MINI_MODEL_ID).count(), 1)
        self.assertEqual(LLMModel.objects.filter(model_id=GPT54_MODEL_ID).count(), 1)
        self.assertEqual(StageModelAssignment.objects.count(), len(ALL_STAGES))

    def test_dry_run_after_real_run_reports_converged_with_no_writes(self):
        configure_gpt54_defaults(dry_run=False)
        before_provider_count = LLMProvider.objects.count()
        before_model_count = LLMModel.objects.count()
        report = configure_gpt54_defaults(dry_run=True)
        self.assertEqual(report.reassigned_stages, [])
        self.assertEqual(LLMProvider.objects.count(), before_provider_count)
        self.assertEqual(LLMModel.objects.count(), before_model_count)


class InteractionWithFreeRouterMigrationTests(TestCase):
    """Running both idempotent commands together (the documented operator playbook, requirements
    Sec 11) -- proves they never fight over the same OpenRouter provider row, and that GPT-5.4
    genuinely supersedes the free-router default rather than merely coexisting with it."""

    def test_free_router_then_gpt54_leaves_free_router_model_active_but_no_longer_any_default(self):
        configure_openrouter_free_router(dry_run=False)
        configure_gpt54_defaults(dry_run=False)

        free_model = LLMModel.objects.get(model_id="openrouter/free")
        self.assertTrue(free_model.is_active)  # still selectable per-run
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertNotEqual(assignment.model.model_id, "openrouter/free")

    def test_both_commands_share_the_same_openrouter_provider_row(self):
        configure_openrouter_free_router(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        openrouter_providers = LLMProvider.objects.filter(
            provider_type=LLMProvider.ProviderType.OPENROUTER
        )
        self.assertEqual(openrouter_providers.count(), 1)

    def test_zai_remains_inactive_and_unassigned_after_both_commands(self):
        configure_openrouter_free_router(dry_run=False)  # deactivates Z.ai
        configure_gpt54_defaults(dry_run=False)
        zai_models = LLMModel.objects.filter(model_id=ZAI_MODEL_ID)
        if zai_models.exists():
            self.assertTrue(all(not m.is_active for m in zai_models))
        self.assertEqual(
            StageModelAssignment.objects.filter(model__model_id=ZAI_MODEL_ID).count(), 0
        )

    def test_reverse_order_last_command_wins_cleanly_with_no_stale_reasoning_default(self):
        """Each command unconditionally converges every stage to its own target model -- running
        `configure_gpt54_defaults` and then `configure_openrouter_free_router` afterward (e.g. an
        operator temporarily reverting to the free tier for cost reasons) must cleanly move every
        stage back to `openrouter/free`, including clearing the now-incompatible
        `default_reasoning_effort` a prior `configure_gpt54_defaults` run left set -- never a
        validation error, never a stage stuck half-converted."""
        configure_gpt54_defaults(dry_run=False)
        report = configure_openrouter_free_router(dry_run=False)
        self.assertEqual(len(report.reassigned_stages), len(ALL_STAGES))
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertEqual(assignment.model.model_id, "openrouter/free")
            self.assertEqual(assignment.default_reasoning_effort, "")


class NoStaleOrLegacyDefaultTests(TestCase):
    def test_no_stage_defaults_to_zai(self):
        configure_gpt54_defaults(dry_run=False)
        self.assertEqual(StageModelAssignment.objects.filter(model__model_id=ZAI_MODEL_ID).count(), 0)

    def test_no_stage_defaults_to_a_bare_gpt5_legacy_model_id(self):
        configure_gpt54_defaults(dry_run=False)
        legacy_ids = {"gpt-5", "openai/gpt-5", "gpt-5-mini", "openai/gpt-5-mini"}
        for assignment in StageModelAssignment.objects.select_related("model"):
            self.assertNotIn(assignment.model.model_id, legacy_ids)

    def test_stale_prior_assignment_is_moved_not_left_behind(self):
        """A stage previously pointed at some other (e.g. NVIDIA) model must be moved onto the
        matrix, not left stale -- the old model row itself is never touched/deleted."""
        nvidia_provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        nvidia_model = make_model(
            provider=nvidia_provider, model_id="nvidia/nemotron", supports_structured_output=True
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=nvidia_model)

        configure_gpt54_defaults(dry_run=False)

        assignment = StageModelAssignment.objects.select_related("model").get(
            stage=StageModelAssignment.Stage.AC_MATCH
        )
        self.assertEqual(assignment.model.model_id, GPT54_MODEL_ID)
        nvidia_model.refresh_from_db()
        self.assertTrue(nvidia_model.is_active)  # never touched, still a selectable alternative


class NoAutomaticFallbackTests(TestCase):
    def test_configuring_gpt54_never_creates_a_second_default_for_any_stage(self):
        """Every stage has exactly one StageModelAssignment row (a unique constraint already
        enforces this at the DB level) -- there is no parallel/fallback assignment mechanism."""
        configure_gpt54_defaults(dry_run=False)
        for stage in ALL_STAGES:
            self.assertEqual(StageModelAssignment.objects.filter(stage=stage).count(), 1)

    def test_no_provider_row_is_marked_as_a_fallback_or_secondary(self):
        configure_gpt54_defaults(dry_run=False)
        # No such concept exists on LLMProvider at all -- proven by its absence rather than a
        # field assertion, matching the "no automatic fallback chain" requirement literally.
        self.assertFalse(hasattr(LLMProvider, "is_fallback"))
        self.assertFalse(hasattr(LLMProvider, "fallback_provider"))


class HistoricalAuditPreservationTests(TestCase):
    def test_pre_existing_call_log_survives_reassignment_unchanged(self):
        old_provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        old_model = make_model(
            provider=old_provider, model_id="nvidia/nemotron", supports_structured_output=True
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=old_model)
        log = LLMCallLog.objects.create(
            provider=old_provider, model=old_model, stage=StageModelAssignment.Stage.AC_MATCH
        )

        configure_gpt54_defaults(dry_run=False)

        log.refresh_from_db()
        self.assertEqual(log.model_id, old_model.pk)
        self.assertEqual(log.provider_id, old_provider.pk)


class ManagementCommandTests(TestCase):
    def test_command_dry_run_writes_nothing_and_prints_report(self):
        out = StringIO()
        call_command("configure_gpt54_defaults", "--dry-run", stdout=out)
        self.assertIn("dry-run", out.getvalue())
        self.assertEqual(LLMProvider.objects.count(), 0)

    def test_command_real_run_is_visible_in_output_and_idempotent_on_rerun(self):
        out = StringIO()
        call_command("configure_gpt54_defaults", stdout=out)
        self.assertIn(GPT54_MINI_MODEL_ID, out.getvalue())
        self.assertIn(GPT54_MODEL_ID, out.getvalue())

        second_out = StringIO()
        call_command("configure_gpt54_defaults", stdout=second_out)
        self.assertIn("no reassignment needed", second_out.getvalue())
