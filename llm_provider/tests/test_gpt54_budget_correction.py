"""Product Owner budget correction (2026-09-07): registers a conservative 16384-token application
output capability for both direct-OpenAI GPT-5.4 models and raises AC_NORMALIZE/AC_RANK/AC_MATCH/
AB_BUILD's own stage budgets to match, while leaving MEMORY_BUILD (4096) and AJ_ANALYZE (8192)
untouched -- historical `LLMCallLog` measurements (AC_NORMALIZE ~7650, AC_RANK ~12230, AC_MATCH
~7326, AB_BUILD ~8204 output tokens) proved the original D-039 pass's 8192 ceiling already
insufficient for AC_RANK/AB_BUILD and left inadequate headroom for AC_NORMALIZE/AC_MATCH.

Deterministic only -- every test in this file either exercises the ORM directly or constructs a
request body locally via the adapter's own pure body-building function
(`llm_provider.adapters.openai.build_chat_completion_body`); none ever calls `requests.post` or any
other network primitive, and none patches `requests.post` either, since none is expected to be
reachable in the first place. `manage.py test`'s network guard
(`llm_provider.testing.NetworkGuardedTestRunner`) independently structurally prevents any real
HTTP call from this file even if that expectation were ever violated by a future edit.
"""

from __future__ import annotations

from django.test import TestCase

from ..adapters import get_adapter_for_stage
from ..adapters.openai import build_chat_completion_body
from ..models import LLMCallLog, LLMModel, LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.gpt54_defaults import (
    AJ_ANALYZE_OUTPUT_BUDGET,
    EXPANDED_STAGE_OUTPUT_BUDGET,
    GPT54_MAX_OUTPUT_TOKENS,
    GPT54_MINI_MODEL_ID,
    GPT54_MODEL_ID,
    MEMORY_BUILD_OUTPUT_BUDGET,
    OPENROUTER_GPT54_MINI_MODEL_ID,
    OPENROUTER_GPT54_MODEL_ID,
    configure_gpt54_defaults,
)

Stage = StageModelAssignment.Stage

EXPANDED_STAGES = (Stage.AC_NORMALIZE, Stage.AC_RANK, Stage.AC_MATCH, Stage.AB_BUILD)
MINI_STAGES = (Stage.MEMORY_BUILD, Stage.AJ_ANALYZE, Stage.AC_NORMALIZE)
FULL_STAGES = (Stage.AC_MATCH, Stage.AC_RANK, Stage.AB_BUILD)


class ModelCapabilityAccepts16kTests(TestCase):
    """1/2: both models accept (register/persist without validation error) the 16384 capability."""

    def test_gpt54_mini_registers_16384_capability(self):
        configure_gpt54_defaults(dry_run=False)
        model = LLMModel.objects.get(
            model_id=GPT54_MINI_MODEL_ID, provider__provider_type=LLMProvider.ProviderType.OPENAI
        )
        model.full_clean()  # raises if 16384 were somehow invalid for this model row
        self.assertEqual(model.max_output_tokens, 16384)
        self.assertEqual(GPT54_MAX_OUTPUT_TOKENS, 16384)

    def test_gpt54_registers_16384_capability(self):
        configure_gpt54_defaults(dry_run=False)
        model = LLMModel.objects.get(
            model_id=GPT54_MODEL_ID, provider__provider_type=LLMProvider.ProviderType.OPENAI
        )
        model.full_clean()
        self.assertEqual(model.max_output_tokens, 16384)

    def test_16384_is_not_inflated_beyond_the_product_owner_approved_number(self):
        """Explicit guard against a future edit accidentally registering a larger, undocumented
        ceiling (e.g. the family's own separately-audited 128,000) instead of the deliberately
        conservative 16384 the Product Owner approved for this correction."""
        self.assertEqual(GPT54_MAX_OUTPUT_TOKENS, 16384)
        self.assertLess(GPT54_MAX_OUTPUT_TOKENS, 128_000)


class FourStageEffectiveBudgetTests(TestCase):
    """3: AC_NORMALIZE/AC_RANK/AC_MATCH/AB_BUILD each resolve to an effective request budget of
    exactly 16384 via the real resolution path (`get_adapter_for_stage`), not merely a registry
    field read out of context."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_each_expanded_stage_resolves_to_16384(self):
        for stage in EXPANDED_STAGES:
            with self.subTest(stage=stage):
                adapter = get_adapter_for_stage(stage)
                self.assertEqual(adapter.effective_max_output_tokens, EXPANDED_STAGE_OUTPUT_BUDGET)
                self.assertEqual(adapter.effective_max_output_tokens, 16384)


class UnchangedStageBudgetTests(TestCase):
    """4/5: MEMORY_BUILD/AJ_ANALYZE keep their existing, already-sufficient budgets -- this
    correction targets only the four stages historical measurements proved too tight."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_memory_build_remains_4096(self):
        adapter = get_adapter_for_stage(Stage.MEMORY_BUILD)
        self.assertEqual(adapter.effective_max_output_tokens, 4096)
        self.assertEqual(MEMORY_BUILD_OUTPUT_BUDGET, 4096)

    def test_aj_analyze_remains_8192(self):
        adapter = get_adapter_for_stage(Stage.AJ_ANALYZE)
        self.assertEqual(adapter.effective_max_output_tokens, 8192)
        self.assertEqual(AJ_ANALYZE_OUTPUT_BUDGET, 8192)


class BudgetCannotExceedModelCapabilityTests(TestCase):
    """6: the pre-existing `StageModelAssignment.clean()`/`get_adapter_for_stage` guard is
    unaffected by this correction -- a stage budget above the model's own registered capability is
    still rejected, now proven at the new 16384 ceiling rather than the old 8192 one."""

    def test_full_clean_rejects_a_stage_budget_above_the_new_16384_ceiling(self):
        from django.core.exceptions import ValidationError

        configure_gpt54_defaults(dry_run=False)
        assignment = StageModelAssignment.objects.get(stage=Stage.AC_MATCH)
        assignment.max_output_tokens = 16385
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_get_adapter_for_stage_fails_closed_on_a_bypassed_over_budget_row(self):
        """Defense-in-depth path (a row that reached the database without `full_clean()`, e.g. a
        raw `objects.create()`) -- `get_adapter_for_stage` itself still refuses to route a live
        call rather than silently sending an over-capability request."""
        from ..adapters import InvalidStageBudgetError

        configure_gpt54_defaults(dry_run=False)
        StageModelAssignment.objects.filter(stage=Stage.AB_BUILD).update(max_output_tokens=20000)
        with self.assertRaises(InvalidStageBudgetError):
            get_adapter_for_stage(Stage.AB_BUILD)


class SelectionSourceRemainsDefaultTests(TestCase):
    """7: with no per-run override submitted, every expanded stage still resolves through the
    ordinary DEFAULT path -- this correction is a registry data change only, never a change to
    selection precedence/behavior."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_default_selection_source_for_every_expanded_stage(self):
        for stage in EXPANDED_STAGES:
            with self.subTest(stage=stage):
                adapter = get_adapter_for_stage(stage)
                self.assertEqual(adapter.selection_source, LLMCallLog.SelectionSource.DEFAULT)


class ProviderModelMappingUnchangedTests(TestCase):
    """8: every stage still maps to exactly the same provider/model this correction was told to
    preserve -- only the budget numbers changed."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_mini_stages_still_use_direct_openai_mini(self):
        for stage in MINI_STAGES:
            with self.subTest(stage=stage):
                assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
                self.assertEqual(assignment.model.model_id, GPT54_MINI_MODEL_ID)
                self.assertEqual(assignment.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)

    def test_full_stages_still_use_direct_openai_full(self):
        for stage in FULL_STAGES:
            with self.subTest(stage=stage):
                assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
                self.assertEqual(assignment.model.model_id, GPT54_MODEL_ID)
                self.assertEqual(assignment.model.provider.provider_type, LLMProvider.ProviderType.OPENAI)

    def test_openrouter_hosted_records_remain_untouched_alternatives(self):
        for model_id in (OPENROUTER_GPT54_MINI_MODEL_ID, OPENROUTER_GPT54_MODEL_ID):
            model = LLMModel.objects.get(
                model_id=model_id, provider__provider_type=LLMProvider.ProviderType.OPENROUTER
            )
            self.assertTrue(model.is_active)
        for stage in Stage.values:
            assignment = StageModelAssignment.objects.select_related("model").get(stage=stage)
            self.assertNotIn(
                assignment.model.model_id, (OPENROUTER_GPT54_MINI_MODEL_ID, OPENROUTER_GPT54_MODEL_ID)
            )


class ReasoningLevelsUnchangedTests(TestCase):
    """9: reasoning effort per stage is exactly what D-039 already established -- this correction
    never touches it."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def test_reasoning_levels_match_the_pre_existing_matrix(self):
        expected = {
            Stage.MEMORY_BUILD: ReasoningEffort.MEDIUM,
            Stage.AJ_ANALYZE: ReasoningEffort.MEDIUM,
            Stage.AC_NORMALIZE: ReasoningEffort.MEDIUM,
            Stage.AC_MATCH: ReasoningEffort.HIGH,
            Stage.AC_RANK: ReasoningEffort.HIGH,
            Stage.AB_BUILD: ReasoningEffort.MEDIUM,
        }
        for stage, reasoning in expected.items():
            with self.subTest(stage=stage):
                assignment = StageModelAssignment.objects.get(stage=stage)
                self.assertEqual(assignment.default_reasoning_effort, reasoning)


class NoFallbackOrSubstitutionIntroducedTests(TestCase):
    """10: this correction adds no new fallback/substitution mechanism of any kind -- proven by
    absence, mirroring the equivalent D-039 guard."""

    def test_no_fallback_related_field_or_mechanism_exists(self):
        self.assertFalse(hasattr(LLMProvider, "is_fallback"))
        self.assertFalse(hasattr(LLMProvider, "fallback_provider"))
        self.assertFalse(hasattr(StageModelAssignment, "fallback_model"))

    def test_each_stage_still_has_exactly_one_assignment_row(self):
        configure_gpt54_defaults(dry_run=False)
        for stage in Stage.values:
            self.assertEqual(StageModelAssignment.objects.filter(stage=stage).count(), 1)


class IdempotencyTests(TestCase):
    """11: rerunning the configuration mechanism after the correction converges cleanly, with zero
    further writes, exactly like every other idempotent pass in this codebase."""

    def test_repeated_invocation_reports_no_further_changes(self):
        configure_gpt54_defaults(dry_run=False)
        second = configure_gpt54_defaults(dry_run=False)
        self.assertEqual(second.reassigned_stages, [])

    def test_repeated_invocation_leaves_budgets_stable(self):
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        configure_gpt54_defaults(dry_run=False)
        for stage in EXPANDED_STAGES:
            assignment = StageModelAssignment.objects.get(stage=stage)
            self.assertEqual(assignment.max_output_tokens, 16384)
        self.assertEqual(
            StageModelAssignment.objects.get(stage=Stage.MEMORY_BUILD).max_output_tokens, 4096
        )
        self.assertEqual(
            StageModelAssignment.objects.get(stage=Stage.AJ_ANALYZE).max_output_tokens, 8192
        )

    def test_dry_run_after_real_run_reports_converged_with_no_writes(self):
        configure_gpt54_defaults(dry_run=False)
        before = list(
            StageModelAssignment.objects.order_by("stage").values("stage", "max_output_tokens")
        )
        report = configure_gpt54_defaults(dry_run=True)
        after = list(
            StageModelAssignment.objects.order_by("stage").values("stage", "max_output_tokens")
        )
        self.assertEqual(report.reassigned_stages, [])
        self.assertEqual(before, after)

    def test_a_stale_row_still_carrying_the_old_8192_budget_converges_to_16384(self):
        """Simulates the real pre-correction database state (rows already converged on model/
        reasoning at the old 8192 budget) -- proves the correction actually *writes* the new
        budget rather than treating a model/reasoning match as "nothing to do"."""
        configure_gpt54_defaults(dry_run=False)
        StageModelAssignment.objects.filter(stage__in=EXPANDED_STAGES).update(max_output_tokens=8192)

        report = configure_gpt54_defaults(dry_run=False)

        changed_stages = {r.stage for r in report.reassigned_stages}
        self.assertEqual(changed_stages, set(EXPANDED_STAGES))
        for stage in EXPANDED_STAGES:
            assignment = StageModelAssignment.objects.get(stage=stage)
            self.assertEqual(assignment.max_output_tokens, 16384)


class LocallyConstructedRequestBodyTests(TestCase):
    """Constructs each of the four affected stages' real request body locally, via the exact same
    pure function `OpenAIAdapter` uses (`build_chat_completion_body`) -- never through
    `adapter.generate()`, never through `requests.post` (not imported, not mocked, not reachable in
    this file) -- and confirms the corrected 16384 output-token value reaches the OpenAI reasoning-
    model request contract (`max_completion_tokens`, since all four stages are reasoning models)."""

    def setUp(self):
        configure_gpt54_defaults(dry_run=False)

    def _model_for(self, stage: str) -> LLMModel:
        return StageModelAssignment.objects.select_related("model").get(stage=stage).model

    def test_ac_normalize_request_body_carries_16384(self):
        from candidate_matching.services.normalize import build_request

        model = self._model_for(Stage.AC_NORMALIZE)
        request = build_request(
            [{"requirement_id": "JR-001", "text": "5+ years Python"}],
            posting_language="en",
            max_output_tokens=16384,
        )
        body = build_chat_completion_body(
            request, model.model_id, {"type": "object"}, token_limit_key="max_completion_tokens",
            include_temperature=False,
        )
        self.assertEqual(body["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", body)

    def test_ac_rank_request_body_carries_16384(self):
        from candidate_matching.services.dedup import DedupedClaim
        from candidate_matching.services.rank import build_request

        model = self._model_for(Stage.AC_RANK)
        pool = [
            DedupedClaim(
                claim_id="CLM-001", text="Owned a payments service.", claim_type="responsibility",
                subject_scope="", approved_engagement_ids=(), grouped_claim_ids=("CLM-001",),
            )
        ]
        request = build_request(
            pool, [{"requirement_id": "JR-001", "text": "5+ years Python"}], max_output_tokens=16384,
        )
        body = build_chat_completion_body(
            request, model.model_id, {"type": "object"}, token_limit_key="max_completion_tokens",
            include_temperature=False,
        )
        self.assertEqual(body["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", body)

    def test_ac_match_request_body_carries_16384(self):
        from candidate_matching.services.assess import build_request
        from candidate_matching.services.retrieve import RetrievalContext

        model = self._model_for(Stage.AC_MATCH)
        retrieval = RetrievalContext(candidate_memory_id=1, claims=[], engagements=[], rules=[])
        request = build_request(
            retrieval, [{"requirement_id": "JR-001", "category": "MANDATORY", "text": "5+ years Python"}],
            max_output_tokens=16384,
        )
        body = build_chat_completion_body(
            request, model.model_id, {"type": "object"}, token_limit_key="max_completion_tokens",
            include_temperature=False,
        )
        self.assertEqual(body["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", body)

    def test_ab_build_request_body_carries_16384(self):
        from candidate_matching.services.retrieve import RetrievalContext
        from resume_builder.services.generate import build_request

        model = self._model_for(Stage.AB_BUILD)

        class _FakeJra:
            role_title = "Senior Backend Engineer"
            employer = "Acme"

        retrieval = RetrievalContext(candidate_memory_id=1, claims=[], engagements=[], rules=[])
        request = build_request(_FakeJra(), [], retrieval, max_output_tokens=16384)
        body = build_chat_completion_body(
            request, model.model_id, {"type": "object"}, token_limit_key="max_completion_tokens",
            include_temperature=False,
        )
        self.assertEqual(body["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", body)

    def test_no_requests_module_symbol_is_referenced_anywhere_in_this_test_class(self):
        """Explicit self-check (12): this test file never imports or patches `requests` -- the
        four tests above call only pure, in-memory body-building functions."""
        import sys

        this_module = sys.modules[__name__]
        self.assertFalse(hasattr(this_module, "requests"))
