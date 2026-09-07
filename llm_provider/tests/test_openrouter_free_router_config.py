"""Idempotent registry configuration for the OpenRouter Free Models Router migration (D-038):
`llm_provider/services/openrouter_free_router.py` and its thin management-command wrapper.

Deterministic only -- no network, no live credential. Exercises the ORM directly against the
test database.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from ..adapters import InactiveModelAssignedError, get_adapter_for_stage
from ..adapters.openrouter import OpenRouterAdapter
from ..models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment
from ..services.openrouter_free_router import (
    FREE_ROUTER_MAX_OUTPUT_TOKENS,
    FREE_ROUTER_MODEL_ID,
    NO_PRIOR_ASSIGNMENT,
    ZAI_MODEL_ID,
    configure_openrouter_free_router,
)
from .factories import make_model, make_provider, make_stage_assignment

ALL_STAGES = list(StageModelAssignment.Stage.values)


class FreshDatabaseTests(TestCase):
    """No OpenRouter provider/model exists yet at all -- the command must still converge cleanly,
    creating exactly what's needed and reassigning nothing (there is nothing to reassign)."""

    def test_dry_run_creates_nothing(self):
        report = configure_openrouter_free_router(dry_run=True)
        self.assertTrue(report.provider_created)
        self.assertTrue(report.free_router_model_created)
        self.assertFalse(report.zai_model_found)
        # Every implemented stage has no prior assignment on a fresh database, so the dry run
        # reports all of them as newly created (D-038 broadened scope) -- but writes nothing.
        self.assertEqual(len(report.reassigned_stages), len(ALL_STAGES))
        self.assertTrue(
            all(r.previous_model_id == NO_PRIOR_ASSIGNMENT for r in report.reassigned_stages)
        )
        self.assertEqual(LLMProvider.objects.count(), 0)
        self.assertEqual(LLMModel.objects.count(), 0)
        self.assertEqual(StageModelAssignment.objects.count(), 0)

    def test_real_run_creates_provider_and_model(self):
        report = configure_openrouter_free_router(dry_run=False)
        self.assertTrue(report.provider_created)
        self.assertTrue(report.free_router_model_created)

        provider = LLMProvider.objects.get(provider_type=LLMProvider.ProviderType.OPENROUTER)
        self.assertEqual(provider.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(provider.data_collection_policy, LLMProvider.DataCollectionPolicy.DENY)

        model = LLMModel.objects.get(provider=provider, model_id=FREE_ROUTER_MODEL_ID)
        self.assertTrue(model.supports_structured_output)
        self.assertFalse(model.supports_reasoning)
        self.assertEqual(model.max_output_tokens, FREE_ROUTER_MAX_OUTPUT_TOKENS)
        self.assertTrue(model.is_active)

    def test_real_run_on_fresh_database_assigns_every_stage(self):
        configure_openrouter_free_router(dry_run=False)
        self.assertEqual(StageModelAssignment.objects.count(), len(ALL_STAGES))
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertEqual(assignment.model.model_id, FREE_ROUTER_MODEL_ID)

    def test_repeated_invocation_on_fresh_database_is_idempotent(self):
        configure_openrouter_free_router(dry_run=False)
        second = configure_openrouter_free_router(dry_run=False)
        self.assertEqual(second.reassigned_stages, [])
        self.assertEqual(StageModelAssignment.objects.count(), len(ALL_STAGES))


class ExistingZaiAssignmentTests(TestCase):
    """The realistic pre-migration scenario: an OpenRouter provider, a Z.ai model, and a real
    stage (AC_NORMALIZE) assigned to it -- mirrors the actual registry state this migration
    targets (docs/CURRENT_STATE.md)."""

    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.zai_model = make_model(
            provider=self.provider,
            model_id=ZAI_MODEL_ID,
            supports_structured_output=True,
            supports_reasoning=True,
            max_output_tokens=230400,
        )
        self.assignment = make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_NORMALIZE,
            model=self.zai_model,
            max_output_tokens=8192,
        )

    def test_dry_run_reports_but_does_not_write(self):
        report = configure_openrouter_free_router(dry_run=True)
        # Every stage converges to the free router now (D-038 broadened scope): the one
        # pre-existing AC_NORMALIZE assignment moves off Z.ai, and the five stages with no prior
        # assignment at all are reported as newly created.
        self.assertEqual(len(report.reassigned_stages), len(ALL_STAGES))
        by_stage = {r.stage: r for r in report.reassigned_stages}
        self.assertEqual(by_stage[StageModelAssignment.Stage.AC_NORMALIZE].previous_model_id, ZAI_MODEL_ID)
        for stage in ALL_STAGES:
            if stage == StageModelAssignment.Stage.AC_NORMALIZE:
                continue
            self.assertEqual(by_stage[stage].previous_model_id, NO_PRIOR_ASSIGNMENT)
        # Nothing actually changed.
        self.zai_model.refresh_from_db()
        self.assertTrue(self.zai_model.is_active)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.model_id, self.zai_model.id)
        self.assertFalse(LLMModel.objects.filter(model_id=FREE_ROUTER_MODEL_ID).exists())
        self.assertEqual(StageModelAssignment.objects.count(), 1)

    def test_real_run_moves_stage_and_deactivates_zai(self):
        configure_openrouter_free_router(dry_run=False)

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.model.model_id, FREE_ROUTER_MODEL_ID)
        self.assertEqual(self.assignment.max_output_tokens, 8192)

        self.zai_model.refresh_from_db()
        self.assertFalse(self.zai_model.is_active)

    def test_every_implemented_stage_defaults_to_free_router(self):
        configure_openrouter_free_router(dry_run=False)
        for stage in ALL_STAGES:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertEqual(
                assignment.model.model_id, FREE_ROUTER_MODEL_ID, msg=f"stage {stage} not on free router"
            )

    def test_ab_build_no_longer_defaults_to_a_different_model(self):
        openai_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI, name="OpenAI", credential_env_var="OPENAI_API_KEY"
        )
        gpt5 = make_model(
            provider=openai_provider, model_id="gpt-5", supports_structured_output=True, is_active=True
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AB_BUILD, model=gpt5)

        configure_openrouter_free_router(dry_run=False)

        assignment = StageModelAssignment.objects.select_related("model").get(
            stage=StageModelAssignment.Stage.AB_BUILD
        )
        self.assertEqual(assignment.model.model_id, FREE_ROUTER_MODEL_ID)
        # The OpenAI model row itself is untouched -- still active, still selectable as a per-run
        # alternative -- only its role as AB_BUILD's *default* was removed.
        gpt5.refresh_from_db()
        self.assertTrue(gpt5.is_active)

    def test_nvidia_default_stages_move_to_free_router_while_nvidia_model_stays_active(self):
        nvidia_provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        nemotron = make_model(
            provider=nvidia_provider,
            model_id="nvidia/nemotron",
            supports_structured_output=True,
            is_active=True,
        )
        for stage in (
            StageModelAssignment.Stage.MEMORY_BUILD,
            StageModelAssignment.Stage.AJ_ANALYZE,
            StageModelAssignment.Stage.AC_MATCH,
            StageModelAssignment.Stage.AC_RANK,
        ):
            make_stage_assignment(stage=stage, model=nemotron)

        configure_openrouter_free_router(dry_run=False)

        for stage in (
            StageModelAssignment.Stage.MEMORY_BUILD,
            StageModelAssignment.Stage.AJ_ANALYZE,
            StageModelAssignment.Stage.AC_MATCH,
            StageModelAssignment.Stage.AC_RANK,
        ):
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertEqual(assignment.model.model_id, FREE_ROUTER_MODEL_ID)
        nemotron.refresh_from_db()
        self.assertTrue(nemotron.is_active)

    def test_orphaned_duplicate_zai_row_under_a_different_provider_is_also_deactivated(self):
        # Real-database finding (2026-09-07): a second OpenRouter-type provider row can carry its
        # own independent copy of the retired model id. The command must deactivate every such
        # row, not only the one under the canonical provider it otherwise operates on.
        other_openrouter_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="dbg",
            credential_env_var="OPENROUTER_API_KEY",
        )
        duplicate_zai = make_model(
            provider=other_openrouter_provider,
            model_id=ZAI_MODEL_ID,
            supports_structured_output=True,
            is_active=True,
        )
        configure_openrouter_free_router(dry_run=False)
        duplicate_zai.refresh_from_db()
        self.assertFalse(duplicate_zai.is_active)

    def test_no_paid_or_fallback_model_is_introduced(self):
        before_model_ids = set(LLMModel.objects.values_list("model_id", flat=True))
        configure_openrouter_free_router(dry_run=False)
        after_model_ids = set(LLMModel.objects.values_list("model_id", flat=True))
        # The only new model row is the free router itself.
        self.assertEqual(after_model_ids - before_model_ids, {FREE_ROUTER_MODEL_ID})

    def test_zai_row_never_deleted(self):
        configure_openrouter_free_router(dry_run=False)
        self.assertTrue(LLMModel.objects.filter(pk=self.zai_model.pk).exists())

    def test_no_stale_assignment_remains_on_zai_after_configuration(self):
        configure_openrouter_free_router(dry_run=False)
        self.assertFalse(
            StageModelAssignment.objects.filter(model__model_id=ZAI_MODEL_ID).exists()
        )

    def test_repeated_invocation_is_idempotent(self):
        first = configure_openrouter_free_router(dry_run=False)
        provider_count_after_first = LLMProvider.objects.count()
        model_count_after_first = LLMModel.objects.count()

        second = configure_openrouter_free_router(dry_run=False)
        self.assertFalse(second.provider_created)
        self.assertFalse(second.free_router_model_created)
        self.assertEqual(second.reassigned_stages, [])
        self.assertEqual(LLMProvider.objects.count(), provider_count_after_first)
        self.assertEqual(LLMModel.objects.count(), model_count_after_first)

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.model.model_id, FREE_ROUTER_MODEL_ID)
        self.assertNotEqual(first.free_router_model_id, None)
        self.assertEqual(first.free_router_model_id, second.free_router_model_id)

    def test_get_adapter_for_stage_now_routes_ac_normalize_through_free_router(self):
        configure_openrouter_free_router(dry_run=False)
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_NORMALIZE)
        self.assertIsInstance(adapter, OpenRouterAdapter)
        self.assertEqual(adapter.llm_model.model_id, FREE_ROUTER_MODEL_ID)

    def test_reassigning_stage_budget_never_exceeds_new_model_capability(self):
        # A pre-existing stage budget above the free router's conservative ceiling must be
        # clamped down, never silently sent to the provider or left in an invalid row.
        self.assignment.max_output_tokens = 999_999
        self.assignment.save(update_fields=["max_output_tokens"])
        configure_openrouter_free_router(dry_run=False)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.max_output_tokens, FREE_ROUTER_MAX_OUTPUT_TOKENS)


class InactiveModelGuardTests(TestCase):
    """A deactivated model must never be silently routable, and reactivating it must be a plain
    registry edit -- both are the enforcement/rollback halves of this migration's safety story."""

    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )

    def test_stage_assigned_to_inactive_model_is_refused(self):
        model = make_model(
            provider=self.provider,
            model_id=ZAI_MODEL_ID,
            supports_structured_output=True,
            is_active=False,
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE, model=model)
        with self.assertRaises(InactiveModelAssignedError):
            get_adapter_for_stage(StageModelAssignment.Stage.AC_NORMALIZE)

    def test_reactivating_the_model_restores_routing_rollback_path(self):
        model = make_model(
            provider=self.provider,
            model_id=ZAI_MODEL_ID,
            supports_structured_output=True,
            is_active=False,
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE, model=model)
        model.is_active = True
        model.save(update_fields=["is_active"])
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_NORMALIZE)
        self.assertEqual(adapter.llm_model.model_id, ZAI_MODEL_ID)

    def test_active_model_is_unaffected(self):
        model = make_model(
            provider=self.provider, model_id=FREE_ROUTER_MODEL_ID, supports_structured_output=True
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_NORMALIZE, model=model)
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_NORMALIZE)
        self.assertEqual(adapter.llm_model.model_id, FREE_ROUTER_MODEL_ID)


class HistoricalAuditPreservationTests(TestCase):
    """An LLMCallLog row referencing the Z.ai model must survive the migration unchanged --
    deactivation must never touch, and PROTECT must forbid deleting, historically-referenced rows."""

    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.zai_model = make_model(
            provider=self.provider, model_id=ZAI_MODEL_ID, supports_structured_output=True
        )
        self.historical_log = LLMCallLog.objects.create(
            provider=self.provider,
            model=self.zai_model,
            stage=StageModelAssignment.Stage.AC_NORMALIZE,
            total_tokens=42,
        )

    def test_historical_call_log_survives_deactivation_unchanged(self):
        configure_openrouter_free_router(dry_run=False)
        self.historical_log.refresh_from_db()
        self.assertEqual(self.historical_log.model_id, self.zai_model.pk)
        self.assertEqual(self.historical_log.model.model_id, ZAI_MODEL_ID)

    def test_zai_model_cannot_be_deleted_while_referenced(self):
        configure_openrouter_free_router(dry_run=False)
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.zai_model.delete()


class ManagementCommandTests(TestCase):
    def test_command_dry_run_writes_nothing_and_prints_report(self):
        out = StringIO()
        call_command("configure_openrouter_free_router", "--dry-run", stdout=out)
        self.assertIn("[dry-run]", out.getvalue())
        self.assertEqual(LLMProvider.objects.count(), 0)

    def test_command_real_run_is_visible_in_output(self):
        out = StringIO()
        call_command("configure_openrouter_free_router", stdout=out)
        self.assertIn(FREE_ROUTER_MODEL_ID, out.getvalue())
        self.assertTrue(LLMProvider.objects.filter(provider_type=LLMProvider.ProviderType.OPENROUTER).exists())
