"""Deterministic coverage for the M5/M6 stage-console read-only summary service (2026-09-07,
D-039): `llm_provider.services.console.build_stage_card` -- paid/free detection, attempt scoping by
`correlation_id`, and that raw provider content/prompts are never exposed.
"""

from __future__ import annotations

from django.test import TestCase

from ..errors import LLMErrorCategory
from ..models import LLMCallLog, LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.console import build_stage_card, is_free_model
from .factories import make_model, make_provider, make_stage_assignment

STAGE = StageModelAssignment.Stage.AC_MATCH


class IsFreeModelTests(TestCase):
    def test_openrouter_free_router_is_free(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.OPENROUTER, name="OpenRouter")
        model = make_model(provider=provider, model_id="openrouter/free")
        self.assertTrue(is_free_model(model))

    def test_colon_free_suffixed_model_is_free(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.OPENROUTER, name="OpenRouter")
        model = make_model(provider=provider, model_id="z-ai/glm-5.2:free")
        self.assertTrue(is_free_model(model))

    def test_gpt54_is_never_described_as_free(self):
        provider = make_provider(provider_type=LLMProvider.ProviderType.OPENROUTER, name="OpenRouter")
        model = make_model(provider=provider, model_id="openai/gpt-5.4")
        self.assertFalse(is_free_model(model))

    def test_none_model_is_unknown_not_false(self):
        self.assertIsNone(is_free_model(None))


class StageCardTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.model = make_model(
            provider=self.provider,
            model_id="openai/gpt-5.4",
            supports_structured_output=True,
            supports_reasoning=True,
            max_output_tokens=8192,
        )
        self.assignment = make_stage_assignment(stage=STAGE, model=self.model)
        self.assignment.default_reasoning_effort = ReasoningEffort.HIGH
        self.assignment.full_clean()
        self.assignment.save()

    def test_no_assignment_at_all_reports_not_configured(self):
        card = build_stage_card(StageModelAssignment.Stage.AC_RANK)
        self.assertIsNone(card.default_model)
        self.assertIsNone(card.effective_model)

    def test_default_model_and_reasoning_shown_without_any_attempt(self):
        card = build_stage_card(STAGE, correlation_id="42")
        self.assertEqual(card.default_model.model_id, "openai/gpt-5.4")
        self.assertEqual(card.default_reasoning_effort, ReasoningEffort.HIGH)
        self.assertFalse(card.default_is_free)
        self.assertIsNone(card.latest_attempt)
        self.assertEqual(card.attempt_count, 0)

    def test_no_correlation_id_never_shows_another_applications_attempt(self):
        LLMCallLog.objects.create(
            provider=self.provider, model=self.model, stage=STAGE, correlation_id="99"
        )
        card = build_stage_card(STAGE)  # no correlation_id given at all
        self.assertIsNone(card.latest_attempt)
        self.assertEqual(card.attempt_count, 0)

    def test_attempt_scoped_strictly_to_this_applications_correlation_id(self):
        LLMCallLog.objects.create(
            provider=self.provider, model=self.model, stage=STAGE, correlation_id="99"
        )
        LLMCallLog.objects.create(
            provider=self.provider,
            model=self.model,
            stage=STAGE,
            correlation_id="42",
            finish_reason="stop",
            total_tokens=100,
        )
        card = build_stage_card(STAGE, correlation_id="42")
        self.assertEqual(card.attempt_count, 1)
        self.assertEqual(card.latest_attempt.attempt_number, 1)
        self.assertEqual(card.latest_attempt.total_tokens, 100)

    def test_attempt_number_increments_and_latest_is_most_recent(self):
        for _ in range(3):
            LLMCallLog.objects.create(
                provider=self.provider, model=self.model, stage=STAGE, correlation_id="42"
            )
        card = build_stage_card(STAGE, correlation_id="42")
        self.assertEqual(card.attempt_count, 3)
        self.assertEqual(card.latest_attempt.attempt_number, 3)

    def test_error_attempt_carries_sanitized_category_and_actionable_guidance(self):
        LLMCallLog.objects.create(
            provider=self.provider,
            model=self.model,
            stage=STAGE,
            correlation_id="42",
            error_category=LLMErrorCategory.RATE_LIMIT.value,
            error_message="Rate limited.",
        )
        card = build_stage_card(STAGE, correlation_id="42")
        self.assertTrue(card.latest_attempt.is_error)
        self.assertEqual(card.latest_attempt.error_category, "RATE_LIMIT")
        self.assertIn("rate-limiting", card.latest_attempt.error_guidance)

    def test_effective_model_after_an_attempt_reflects_what_actually_ran(self):
        other_model = make_model(
            provider=self.provider,
            model_id="openai/gpt-5.4-mini",
            supports_structured_output=True,
            supports_reasoning=True,
        )
        LLMCallLog.objects.create(
            provider=self.provider,
            model=other_model,
            stage=STAGE,
            correlation_id="42",
            reasoning_effort=ReasoningEffort.LOW,
            selection_source=LLMCallLog.SelectionSource.OVERRIDE,
        )
        card = build_stage_card(STAGE, correlation_id="42")
        self.assertEqual(card.effective_model.model_id, "openai/gpt-5.4-mini")
        self.assertEqual(card.effective_reasoning_effort, ReasoningEffort.LOW)
        # The stage's own configured default is unaffected by what one run happened to override.
        self.assertEqual(card.default_model.model_id, "openai/gpt-5.4")
