"""Idempotent registry configuration for the paid GPT-5.4 model defaults (2026-09-07, D-039,
corrected same day to route through the direct OpenAI API rather than OpenRouter):
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
    AC_NORMALIZE_OUTPUT_BUDGET,
    AJ_ANALYZE_OUTPUT_BUDGET,
    EXPANDED_STAGE_OUTPUT_BUDGET,
    GPT54_MAX_OUTPUT_TOKENS,
    GPT54_MINI_MAX_OUTPUT_TOKENS,
    GPT54_MINI_MODEL_ID,
    GPT54_MODEL_ID,
    MEMORY_BUILD_OUTPUT_BUDGET,
    OPENROUTER_GPT54_MINI_MODEL_ID,
    OPENROUTER_GPT54_MODEL_ID,
    STAGE_DEFAULT_MATRIX,
    configure_gpt54_defaults,
)
from ..services.openrouter_free_router import ZAI_MODEL_ID, configure_openrouter_free_router
from .factories import make_model, make_provider, make_stage_assignment

ALL_STAGES = list(StageModelAssignment.Stage.values)

EXPECTED_MATRIX = {
    StageModelAssignment.Stage.MEMORY_BUILD: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, MEMORY_BUILD_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AJ_ANALYZE: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, AJ_ANALYZE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_NORMALIZE: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, AC_NORMALIZE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_MATCH: (
        GPT54_MODEL_ID, ReasoningEffort.MEDIUM, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_RANK: (
        GPT54_MODEL_ID, ReasoningEffort.MEDIUM, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AB_BUILD: (
        GPT54_MODEL_ID, ReasoningEffort.MEDIUM, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
}


class ExactModelIdentifierTests(TestCase):
    def test_mini_model_id_is_exact_and_direct(self):
        self.assertEqual(GPT54_MINI_MODEL_ID, "gpt-5.4-mini")

    def test_full_model_id_is_exact_and_direct(self):
        self.assertEqual(GPT54_MODEL_ID, "gpt-5.4")

    def test_direct_ids_never_carry_the_openrouter_routing_prefix(self):
        """Requirements Sec 2 of the correction: the direct-OpenAI ids must never contain
        'openai/', 'openrouter/', or any OpenRouter provider reference -- that prefix belongs only
        to the separate OpenRouter-hosted alternative records."""
        for model_id in (GPT54_MINI_MODEL_ID, GPT54_MODEL_ID):
            self.assertNotIn("openai/", model_id)
            self.assertNotIn("openrouter/", model_id)

    def test_openrouter_equivalents_are_kept_as_distinct_ids(self):
        self.assertEqual(OPENROUTER_GPT54_MINI_MODEL_ID, "openai/gpt-5.4-mini")
        self.assertEqual(OPENROUTER_GPT54_MODEL_ID, "openai/gpt-5.4")


class CompleteDefaultMatrixTests(TestCase):
    def test_matrix_covers_every_implemented_stage(self):
        self.assertEqual(set(STAGE_DEFAULT_MATRIX), set(ALL_STAGES))

    def test_matrix_matches_the_operator_approved_assignment_exactly(self):
        self.assertEqual(STAGE_DEFAULT_MATRIX, EXPECTED_MATRIX)

    def test_matrix_uses_only_direct_openai_ids_never_openrouter_ids(self):
        matrix_model_ids = {model_id for model_id, _reasoning, _budget in STAGE_DEFAULT_MATRIX.values()}
        self.assertEqual(matrix_model_ids, {GPT54_MINI_MODEL_ID, GPT54_MODEL_ID})
        self.assertNotIn(OPENROUTER_GPT54_MINI_MODEL_ID, matrix_model_ids)
        self.assertNotIn(OPENROUTER_GPT54_MODEL_ID, matrix_model_ids)

    def test_real_run_assigns_every_stage_to_the_exact_direct_matrix(self):
        configure_gpt54_defaults(dry_run=False)
        for stage, (expected_model_id, expected_reasoning, expected_budget) in EXPECTED_MATRIX.items():
            assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
            self.assertEqual(assignment.model.model_id, expected_model_id)
            self.assertEqual(assignment.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)
            self.assertEqual(assignment.default_reasoning_effort, expected_reasoning)
            self.assertEqual(assignment.max_output_tokens, expected_budget)


class DirectOpenAIProviderTests(TestCase):
    def test_provider_row_uses_the_direct_openai_endpoint_and_credential(self):
        configure_gpt54_defaults(dry_run=False)
        mini = LLMModel.objects.select_related("provider").get(model_id=GPT54_MINI_MODEL_ID)
        self.assertEqual(mini.provider.provider_type, LLMProvider.ProviderType.OPENAI)
        self.assertEqual(mini.provider.base_url, "https://api.openai.com/v1")
        self.assertEqual(mini.provider.credential_env_var, "OPENAI_API_KEY")

    def test_reuses_a_pre_existing_direct_openai_provider_row_never_duplicates_it(self):
        existing_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI",
            credential_env_var="OPENAI_API_KEY",
            base_url="https://api.openai.com/v1",
        )
        make_model(provider=existing_provider, model_id="gpt-5", supports_structured_output=True)

        report = configure_gpt54_defaults(dry_run=False)

        self.assertFalse(report.openai_provider_created)
        self.assertEqual(report.openai_provider_id, existing_provider.pk)
        self.assertEqual(
            LLMProvider.objects.filter(provider_type=LLMProvider.ProviderType.OPENAI).count(), 1
        )
        # The pre-existing gpt-5 row is left completely untouched.
        self.assertTrue(LLMModel.objects.get(provider=existing_provider, model_id="gpt-5").is_active)

    def test_never_overwrites_an_operators_custom_base_url_or_credential_var(self):
        existing_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI,
            name="OpenAI",
            credential_env_var="MY_CUSTOM_OPENAI_KEY",
            base_url="https://my-custom-proxy.example.com/v1",
        )
        configure_gpt54_defaults(dry_run=False)
        existing_provider.refresh_from_db()
        self.assertEqual(existing_provider.base_url, "https://my-custom-proxy.example.com/v1")
        self.assertEqual(existing_provider.credential_env_var, "MY_CUSTOM_OPENAI_KEY")


def _direct_model(model_id: str) -> LLMModel:
    return LLMModel.objects.get(model_id=model_id, provider__provider_type=LLMProvider.ProviderType.OPENAI)


class RegistryCapabilityTests(TestCase):
    def test_both_direct_models_are_active_structured_output_reasoning_capable(self):
        configure_gpt54_defaults(dry_run=False)
        for model_id in (GPT54_MINI_MODEL_ID, GPT54_MODEL_ID):
            model = _direct_model(model_id)
            self.assertTrue(model.is_active)
            self.assertTrue(model.supports_structured_output)
            self.assertTrue(model.supports_reasoning)

    def test_model_capability_matches_the_product_owner_approved_split_ceiling(self):
        """Product Owner capacity correction (2026-09-08): gpt-5.4-mini keeps the approved
        conservative 16384-token capability; gpt-5.4 is raised to 32768 after AC_RANK truncated a
        real run at the prior shared 16384 ceiling -- neither is a larger, undocumented number."""
        configure_gpt54_defaults(dry_run=False)
        mini = _direct_model(GPT54_MINI_MODEL_ID)
        full = _direct_model(GPT54_MODEL_ID)
        self.assertEqual(mini.max_output_tokens, 16384)
        self.assertEqual(mini.max_output_tokens, GPT54_MINI_MAX_OUTPUT_TOKENS)
        self.assertEqual(full.max_output_tokens, 32768)
        self.assertEqual(full.max_output_tokens, GPT54_MAX_OUTPUT_TOKENS)

    def test_display_names_are_exact(self):
        configure_gpt54_defaults(dry_run=False)
        mini = _direct_model(GPT54_MINI_MODEL_ID)
        full = _direct_model(GPT54_MODEL_ID)
        self.assertEqual(mini.display_name, "GPT-5.4 Mini")
        self.assertEqual(full.display_name, "GPT-5.4")


class OpenRouterEquivalentsPreservedTests(TestCase):
    """Requirements Sec 2 of the correction: the OpenRouter-hosted records must still be created/
    kept active as explicit optional alternatives -- never a stage default, never removed."""

    def test_openrouter_equivalents_are_created_active_but_never_a_default(self):
        configure_gpt54_defaults(dry_run=False)
        for model_id in (OPENROUTER_GPT54_MINI_MODEL_ID, OPENROUTER_GPT54_MODEL_ID):
            model = LLMModel.objects.get(
                model_id=model_id, provider__provider_type=LLMProvider.ProviderType.OPENROUTER
            )
            self.assertTrue(model.is_active)
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertNotIn(
                assignment.model.model_id, (OPENROUTER_GPT54_MINI_MODEL_ID, OPENROUTER_GPT54_MODEL_ID)
            )

    def test_pre_existing_openrouter_gpt54_records_from_the_original_pass_are_preserved(self):
        """Simulates the real state left behind by the original (corrected) implementation: the
        OpenRouter provider already has openai/gpt-5.4-mini and openai/gpt-5.4 rows, previously the
        stage defaults. This correction must never delete them, only stop defaulting to them."""
        openrouter_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        openrouter_mini = make_model(
            provider=openrouter_provider,
            model_id=OPENROUTER_GPT54_MINI_MODEL_ID,
            supports_structured_output=True,
            supports_reasoning=True,
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AJ_ANALYZE, model=openrouter_mini)

        configure_gpt54_defaults(dry_run=False)

        openrouter_mini.refresh_from_db()
        self.assertTrue(openrouter_mini.is_active)  # preserved, never deleted/deactivated
        assignment = StageModelAssignment.objects.select_related("model__provider").get(
            stage=StageModelAssignment.Stage.AJ_ANALYZE
        )
        self.assertEqual(assignment.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)
        self.assertEqual(assignment.model.model_id, GPT54_MINI_MODEL_ID)


class DryRunTests(TestCase):
    def test_dry_run_writes_nothing(self):
        report = configure_gpt54_defaults(dry_run=True)
        self.assertTrue(report.openai_provider_created)
        self.assertTrue(report.openrouter_provider_created)
        self.assertTrue(report.mini_model_created)
        self.assertTrue(report.full_model_created)
        self.assertTrue(report.openrouter_mini_model_created)
        self.assertTrue(report.openrouter_full_model_created)
        self.assertEqual(len(report.reassigned_stages), len(ALL_STAGES))
        self.assertEqual(LLMProvider.objects.count(), 0)
        self.assertEqual(LLMModel.objects.count(), 0)
        self.assertEqual(StageModelAssignment.objects.count(), 0)


class IdempotencyTests(TestCase):
    def test_repeated_invocation_reports_no_further_changes(self):
        configure_gpt54_defaults(dry_run=False)
        second = configure_gpt54_defaults(dry_run=False)
        self.assertEqual(second.reassigned_stages, [])
        self.assertFalse(second.openai_provider_created)
        self.assertFalse(second.mini_model_created)
        self.assertFalse(second.full_model_created)

    def test_repeated_invocation_creates_no_duplicate_rows(self):
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        self.assertEqual(LLMModel.objects.filter(model_id=GPT54_MINI_MODEL_ID).count(), 1)
        self.assertEqual(LLMModel.objects.filter(model_id=GPT54_MODEL_ID).count(), 1)
        self.assertEqual(
            LLMProvider.objects.filter(provider_type=LLMProvider.ProviderType.OPENAI).count(), 1
        )
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
    Sec 11) -- proves they never fight over the same OpenRouter provider row, and that direct
    GPT-5.4 genuinely supersedes the free-router default rather than merely coexisting with it."""

    def test_free_router_then_gpt54_leaves_free_router_model_active_but_no_longer_any_default(self):
        configure_openrouter_free_router(dry_run=False)
        configure_gpt54_defaults(dry_run=False)

        free_model = LLMModel.objects.get(model_id="openrouter/free")
        self.assertTrue(free_model.is_active)  # still selectable per-run
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertNotEqual(assignment.model.model_id, "openrouter/free")

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

    def test_no_stage_defaults_to_a_bare_legacy_gpt5_model_id(self):
        configure_gpt54_defaults(dry_run=False)
        legacy_ids = {"gpt-5", "openai/gpt-5", "gpt-5-mini", "openai/gpt-5-mini"}
        for assignment in StageModelAssignment.objects.select_related("model"):
            self.assertNotIn(assignment.model.model_id, legacy_ids)

    def test_no_stage_defaults_to_an_openrouter_hosted_gpt54_record(self):
        configure_gpt54_defaults(dry_run=False)
        for assignment in StageModelAssignment.objects.select_related("model"):
            self.assertNotIn(
                assignment.model.model_id, (OPENROUTER_GPT54_MINI_MODEL_ID, OPENROUTER_GPT54_MODEL_ID)
            )

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

        assignment = StageModelAssignment.objects.select_related("model__provider").get(
            stage=StageModelAssignment.Stage.AC_MATCH
        )
        self.assertEqual(assignment.model.model_id, GPT54_MODEL_ID)
        self.assertEqual(assignment.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)
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
        self.assertIn("direct OpenAI", out.getvalue())

        second_out = StringIO()
        call_command("configure_gpt54_defaults", stdout=second_out)
        self.assertIn("no reassignment needed", second_out.getvalue())
