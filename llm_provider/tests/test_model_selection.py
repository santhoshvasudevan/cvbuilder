"""Deterministic coverage for `llm_provider.services.model_selection` and its wiring into
`llm_provider.adapters.get_adapter_for_stage` (2026-09-07, per-run model selection): the
precedence (explicit override -> StageModelAssignment default -> typed failure), override
validation, and requested/default audit capture on `LLMCallLog`.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from ..adapters import ModelNotEligibleForStageError, NoStageDefaultConfiguredError, get_adapter_for_stage
from ..models import LLMCallLog, LLMProvider, StageModelAssignment
from ..services.model_selection import resolve_stage_model
from .factories import make_model, make_provider, make_stage_assignment

STAGE = StageModelAssignment.Stage.AB_BUILD


class ResolutionPrecedenceTests(TestCase):
    def setUp(self):
        self.default_provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.default_model = make_model(
            provider=self.default_provider, model_id="openrouter/free", supports_structured_output=True
        )
        self.assignment = make_stage_assignment(stage=STAGE, model=self.default_model)

        self.alt_provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        self.alt_model = make_model(
            provider=self.alt_provider, model_id="nvidia/nemotron", supports_structured_output=True
        )

    def test_no_override_uses_stage_default(self):
        selection = resolve_stage_model(STAGE)
        self.assertEqual(selection.model.pk, self.default_model.pk)
        self.assertEqual(selection.source, LLMCallLog.SelectionSource.DEFAULT)

    def test_explicit_override_takes_precedence(self):
        selection = resolve_stage_model(STAGE, requested_model_id=self.alt_model.pk)
        self.assertEqual(selection.model.pk, self.alt_model.pk)
        self.assertEqual(selection.source, LLMCallLog.SelectionSource.OVERRIDE)

    def test_override_does_not_change_global_assignment(self):
        resolve_stage_model(STAGE, requested_model_id=self.alt_model.pk)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.model_id, self.default_model.pk)

    def test_ineligible_override_is_rejected(self):
        inactive_model = make_model(
            provider=self.alt_provider,
            model_id="nvidia/retired",
            supports_structured_output=True,
            is_active=False,
        )
        with self.assertRaises(ModelNotEligibleForStageError):
            resolve_stage_model(STAGE, requested_model_id=inactive_model.pk)

    def test_nonexistent_override_is_rejected(self):
        with self.assertRaises(ModelNotEligibleForStageError):
            resolve_stage_model(STAGE, requested_model_id=999_999)

    def test_no_default_configured_and_no_override_fails_closed(self):
        StageModelAssignment.objects.filter(stage=StageModelAssignment.Stage.AC_RANK).delete()
        with self.assertRaises(NoStageDefaultConfiguredError):
            resolve_stage_model(StageModelAssignment.Stage.AC_RANK)


class GetAdapterForStageOverrideTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var="OPENROUTER_API_KEY",
        )
        self.default_model = make_model(
            provider=self.provider,
            model_id="openrouter/free",
            supports_structured_output=True,
            max_output_tokens=8192,
        )
        make_stage_assignment(stage=STAGE, model=self.default_model, max_output_tokens=4096)

        self.nvidia_provider = make_provider(
            provider_type=LLMProvider.ProviderType.NVIDIA_NIM,
            name="NVIDIA NIM",
            credential_env_var="NVIDIA_NIM_API_KEY",
        )
        self.nvidia_model = make_model(
            provider=self.nvidia_provider,
            model_id="nvidia/nemotron",
            supports_structured_output=True,
            max_output_tokens=32768,
        )

    def test_default_path_uses_stage_budget_and_marks_default_source(self):
        adapter = get_adapter_for_stage(STAGE)
        self.assertEqual(adapter.llm_model.pk, self.default_model.pk)
        self.assertEqual(adapter.effective_max_output_tokens, 4096)
        self.assertEqual(adapter.selection_source, LLMCallLog.SelectionSource.DEFAULT)

    def test_override_path_uses_override_models_own_capability_and_marks_override_source(self):
        adapter = get_adapter_for_stage(STAGE, requested_model_id=self.nvidia_model.pk)
        self.assertEqual(adapter.llm_model.pk, self.nvidia_model.pk)
        # Never the default model's stage-tuned budget (4096) -- the override model's own
        # capability instead.
        self.assertEqual(adapter.effective_max_output_tokens, 32768)
        self.assertEqual(adapter.selection_source, LLMCallLog.SelectionSource.OVERRIDE)

    def test_unavailable_selection_fails_before_any_provider_call(self):
        with mock.patch("requests.post") as post_mock:
            with self.assertRaises(ModelNotEligibleForStageError):
                get_adapter_for_stage(STAGE, requested_model_id=999_999)
        post_mock.assert_not_called()

    def test_call_log_captures_requested_and_selection_source_for_override(self):
        from pydantic import BaseModel

        from ..types import NormalizedLLMRequest

        class _Tiny(BaseModel):
            ok: bool = True

        adapter = get_adapter_for_stage(STAGE, requested_model_id=self.nvidia_model.pk)
        request = NormalizedLLMRequest(
            stage=STAGE,
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
        self.assertEqual(log.model_id, self.nvidia_model.pk)
        self.assertEqual(log.selection_source, LLMCallLog.SelectionSource.OVERRIDE)

    def test_different_stages_can_select_different_models_independently(self):
        other_stage = StageModelAssignment.Stage.AJ_ANALYZE
        make_stage_assignment(stage=other_stage, model=self.default_model)
        ab_adapter = get_adapter_for_stage(STAGE, requested_model_id=self.nvidia_model.pk)
        aj_adapter = get_adapter_for_stage(other_stage)
        self.assertEqual(ab_adapter.llm_model.pk, self.nvidia_model.pk)
        self.assertEqual(aj_adapter.llm_model.pk, self.default_model.pk)
