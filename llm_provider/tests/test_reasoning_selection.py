"""Deterministic coverage for reasoning-effort selection (2026-09-07, D-039 paid GPT-5.4 model
defaults): the same three-step precedence `test_model_selection.py` already proves for the model
itself (explicit override -> StageModelAssignment default -> typed failure/silent-safe fallback),
now for `StageModelAssignment.default_reasoning_effort`/a per-run reasoning override, plus its own
distinct validation rules (compatibility with whichever model actually resolves, persisted audit
trail, no mutation of the global default).
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from ..adapters import get_adapter_for_stage
from ..models import LLMCallLog, LLMProvider, ReasoningEffort, StageModelAssignment
from ..services.model_selection import (
    ReasoningNotEligibleForStageError,
    parse_requested_reasoning_effort,
    resolve_stage_model,
)
from .factories import make_model, make_provider, make_stage_assignment

STAGE = StageModelAssignment.Stage.AC_MATCH


class ParseRequestedReasoningEffortTests(TestCase):
    def test_blank_or_missing_means_system_default(self):
        self.assertIsNone(parse_requested_reasoning_effort(""))
        self.assertIsNone(parse_requested_reasoning_effort(None))
        self.assertIsNone(parse_requested_reasoning_effort("   "))

    def test_every_valid_reasoning_effort_value_round_trips(self):
        for value in ReasoningEffort.values:
            self.assertEqual(parse_requested_reasoning_effort(value), value)

    def test_garbage_value_is_rejected(self):
        with self.assertRaises(ReasoningNotEligibleForStageError):
            parse_requested_reasoning_effort("ultra-mega-high")


class ReasoningResolutionPrecedenceTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.reasoning_model = make_model(
            provider=self.provider,
            model_id="openai/gpt-5.4",
            supports_structured_output=True,
            supports_reasoning=True,
        )
        self.non_reasoning_model = make_model(
            provider=self.provider,
            model_id="openrouter/free",
            supports_structured_output=True,
            supports_reasoning=False,
        )
        self.assignment = make_stage_assignment(stage=STAGE, model=self.reasoning_model)
        self.assignment.default_reasoning_effort = ReasoningEffort.HIGH
        self.assignment.full_clean()
        self.assignment.save()

    def test_no_override_uses_stage_default_reasoning(self):
        selection = resolve_stage_model(STAGE)
        self.assertEqual(selection.reasoning_effort, ReasoningEffort.HIGH)

    def test_explicit_override_takes_precedence_over_stage_default(self):
        selection = resolve_stage_model(STAGE, requested_reasoning_effort=ReasoningEffort.LOW)
        self.assertEqual(selection.reasoning_effort, ReasoningEffort.LOW)

    def test_explicit_none_overrides_a_configured_default(self):
        """`ReasoningEffort.NONE` is itself a real, explicit selection -- distinct from omitting
        the override entirely (which falls through to the stage default)."""
        selection = resolve_stage_model(STAGE, requested_reasoning_effort=ReasoningEffort.NONE)
        self.assertEqual(selection.reasoning_effort, ReasoningEffort.NONE)

    def test_override_does_not_mutate_the_stored_stage_default(self):
        resolve_stage_model(STAGE, requested_reasoning_effort=ReasoningEffort.LOW)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.default_reasoning_effort, ReasoningEffort.HIGH)

    def test_no_default_reasoning_configured_and_no_override_resolves_to_none(self):
        """Absence of a configured `default_reasoning_effort` is a silent-safe 'say nothing about
        reasoning' -- never a typed failure the way a missing *model* default is; a stage without
        graded reasoning support today (e.g. still on `openrouter/free`) must keep working exactly
        as before this feature existed."""
        other_stage = StageModelAssignment.Stage.AJ_ANALYZE
        make_stage_assignment(stage=other_stage, model=self.non_reasoning_model)
        selection = resolve_stage_model(other_stage)
        self.assertIsNone(selection.reasoning_effort)

    def test_explicit_reasoning_incompatible_with_the_resolved_model_is_rejected(self):
        with self.assertRaises(ReasoningNotEligibleForStageError):
            resolve_stage_model(
                STAGE,
                requested_model_id=self.non_reasoning_model.pk,
                requested_reasoning_effort=ReasoningEffort.HIGH,
            )

    def test_default_reasoning_incompatible_with_an_overridden_model_is_rejected(self):
        """Overriding only the *model* to a non-reasoning one while the stage's own configured
        default reasoning is still a real effort level must fail closed with an actionable typed
        error -- never silently drop the reasoning setting and call the non-reasoning model anyway."""
        with self.assertRaises(ReasoningNotEligibleForStageError):
            resolve_stage_model(STAGE, requested_model_id=self.non_reasoning_model.pk)

    def test_explicit_none_still_requires_a_reasoning_capable_model(self):
        """`ReasoningEffort.NONE` is a real, explicit reasoning request (matching
        `eligibility.eligible_models_for_stage`'s own "any truthy value, including NONE" rule) --
        it is rejected against a model never registered `supports_reasoning=True`, exactly like
        every other graded effort level, rather than being special-cased as a universal no-op."""
        with self.assertRaises(ReasoningNotEligibleForStageError):
            resolve_stage_model(
                STAGE,
                requested_model_id=self.non_reasoning_model.pk,
                requested_reasoning_effort=ReasoningEffort.NONE,
            )


class AdapterExposesEffectiveReasoningEffortTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.model = make_model(
            provider=self.provider,
            model_id="openai/gpt-5.4-mini",
            supports_structured_output=True,
            supports_reasoning=True,
        )
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE,
            model=self.model,
        )
        assignment = StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AJ_ANALYZE)
        assignment.default_reasoning_effort = ReasoningEffort.MEDIUM
        assignment.full_clean()
        assignment.save()

    def test_default_path_exposes_the_stage_configured_reasoning_effort(self):
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)
        self.assertEqual(adapter.effective_reasoning_effort, ReasoningEffort.MEDIUM)

    def test_override_path_exposes_the_requested_reasoning_effort(self):
        adapter = get_adapter_for_stage(
            StageModelAssignment.Stage.AJ_ANALYZE,
            requested_reasoning_effort=ReasoningEffort.XHIGH,
        )
        self.assertEqual(adapter.effective_reasoning_effort, ReasoningEffort.XHIGH)


class ReasoningEffortPersistedOnCallLogTests(TestCase):
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
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=self.model)

    def test_resolved_reasoning_effort_is_recorded_on_the_call_log(self):
        from pydantic import BaseModel

        from ..types import NormalizedLLMRequest

        class _Tiny(BaseModel):
            ok: bool = True

        adapter = get_adapter_for_stage(
            StageModelAssignment.Stage.AC_MATCH, requested_reasoning_effort=ReasoningEffort.HIGH
        )
        request = NormalizedLLMRequest(
            stage=StageModelAssignment.Stage.AC_MATCH,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_Tiny,
            max_output_tokens=16,
            reasoning_effort=adapter.effective_reasoning_effort,
        )
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = mock.Mock(
                status_code=200,
                headers={},
                json=lambda: {
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(request)
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.reasoning_effort, ReasoningEffort.HIGH)

    def test_no_reasoning_requested_leaves_the_call_log_field_blank(self):
        from pydantic import BaseModel

        from ..types import NormalizedLLMRequest

        class _Tiny(BaseModel):
            ok: bool = True

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        request = NormalizedLLMRequest(
            stage=StageModelAssignment.Stage.AC_MATCH,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_Tiny,
            max_output_tokens=16,
        )
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = mock.Mock(
                status_code=200,
                headers={},
                json=lambda: {
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(request)
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.reasoning_effort, "")
