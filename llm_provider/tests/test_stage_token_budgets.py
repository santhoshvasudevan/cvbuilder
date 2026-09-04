"""Stage-specific LLM output-token budgets (2026-09-04): `LLMModel.max_output_tokens` is a
provider/model *capability* ceiling; `StageModelAssignment.max_output_tokens` is an optional,
per-stage *request budget* that must never exceed that ceiling. Motivated by JobApplication 9's
AJ_ANALYZE rerun hitting the model's own 4,096-token capability (then also used, unmodified, as
every stage's request budget) and truncating (`finish_reason=length`) before producing valid
structured output.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from pydantic import BaseModel

from ..adapters import DEFAULT_MAX_OUTPUT_TOKENS, InvalidStageBudgetError, get_adapter_for_stage
from ..adapters.openai import build_chat_completion_body, parse_openai_style_chat_completion
from ..errors import LLMErrorCategory
from ..models import LLMProvider, StageModelAssignment
from ..retry import is_retryable
from ..types import NormalizedLLMRequest, NormalizedLLMResult
from .factories import make_model, make_provider, make_stage_assignment


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"]


_provider_counter = iter(range(1, 10_000))


def _openai_model(**kwargs):
    provider = make_provider(
        provider_type=LLMProvider.ProviderType.OPENAI, name=f"OpenAI test {next(_provider_counter)}"
    )
    return make_model(provider=provider, **kwargs)


class EffectiveBudgetResolutionTests(TestCase):
    """Item 1/2/3: stage override used; stage without override retains previous behavior; one
    stage's own override never leaks into another stage sharing the same model."""

    def test_stage_override_is_used_when_configured(self):
        model = _openai_model(max_output_tokens=16_384)
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)

        self.assertEqual(adapter.effective_max_output_tokens, 8_192)

    def test_stage_without_override_uses_the_model_s_own_capability(self):
        model = _openai_model(max_output_tokens=16_384)
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK, model=model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_RANK)

        self.assertEqual(adapter.effective_max_output_tokens, 16_384)

    def test_stage_without_override_and_model_without_capability_uses_the_conservative_default(self):
        model = _openai_model(max_output_tokens=None)
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)

        self.assertEqual(adapter.effective_max_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS)

    def test_one_stage_s_higher_budget_does_not_affect_another_stage_on_the_same_model(self):
        """The exact scenario this decision targets: AJ_ANALYZE raised to 8,192 must never change
        AC_NORMALIZE/AC_RANK/AC_MATCH's own (still 4,096) budget, even though every stage here
        shares the same underlying LLMModel/capability."""
        model = _openai_model(max_output_tokens=16_384)
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_NORMALIZE, model=model, max_output_tokens=4_096
        )
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_RANK, model=model, max_output_tokens=4_096
        )
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_MATCH, model=model, max_output_tokens=4_096
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AB_BUILD, model=model)

        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE).effective_max_output_tokens, 8_192
        )
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AC_NORMALIZE).effective_max_output_tokens, 4_096
        )
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AC_RANK).effective_max_output_tokens, 4_096
        )
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH).effective_max_output_tokens, 4_096
        )
        # AB_BUILD has no override -- falls through to the shared model's own capability, not any
        # other stage's override.
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD).effective_max_output_tokens, 16_384
        )

    def test_reassigning_one_stage_s_model_never_changes_another_stage_s_resolved_budget(self):
        shared_model = _openai_model(max_output_tokens=16_384)
        other_model = _openai_model(max_output_tokens=2_048)
        aj = make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=shared_model, max_output_tokens=8_192
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK, model=shared_model)

        aj.model = other_model
        aj.max_output_tokens = None
        aj.full_clean()
        aj.save()

        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE).effective_max_output_tokens, 2_048
        )
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AC_RANK).effective_max_output_tokens, 16_384
        )


class RejectedConfigurationTests(TestCase):
    """Item 4: zero/negative/over-cap values are rejected before any provider call could happen --
    both at model-validation time (`full_clean`, what the admin form calls) and, as defense in
    depth, at `get_adapter_for_stage` resolution time for a row that reached the database without
    validation."""

    def test_zero_stage_budget_is_rejected_by_full_clean(self):
        model = _openai_model(max_output_tokens=16_384)
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=0
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_negative_stage_budget_is_rejected_by_full_clean(self):
        model = _openai_model(max_output_tokens=16_384)
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=-1
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_over_cap_stage_budget_is_rejected_by_full_clean(self):
        model = _openai_model(max_output_tokens=4_096)
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_zero_model_capability_is_rejected_by_full_clean(self):
        model = _openai_model()
        model.max_output_tokens = 0
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_over_cap_row_that_bypassed_full_clean_is_rejected_at_resolution_time_not_silently_used(self):
        """Defense in depth: a misconfigured row created via `.objects.create()` (bypassing
        `full_clean()`, e.g. a fixture or script) must still fail closed at
        `get_adapter_for_stage` -- before any provider call -- rather than silently sending an
        over-cap budget to the provider or silently clamping it."""
        model = _openai_model(max_output_tokens=4_096)
        StageModelAssignment.objects.create(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )

        with self.assertRaises(InvalidStageBudgetError):
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)

    def test_valid_configuration_at_the_intended_post_audit_values_passes_full_clean(self):
        """The exact intended post-audit configuration (not applied live by this change): model
        capability 16,384, AJ_ANALYZE raised to 8,192, every other stage unchanged/unconfigured."""
        model = _openai_model(max_output_tokens=16_384)
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )
        assignment.full_clean()  # must not raise
        assignment.save()
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE).effective_max_output_tokens, 8_192
        )


class AdminValidationTests(TestCase):
    """Item 7: the admin form (which every operator configuration change goes through) enforces
    the same cross-field rule as `StageModelAssignment.clean()` -- Django's `ModelForm` calls
    `instance.full_clean()` during validation, so this is the same code path, exercised the way
    the admin actually exercises it."""

    def _admin_form_class(self):
        import uuid

        from django.contrib import admin
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        superuser = User.objects.create_superuser(username=f"admin-test-{uuid.uuid4().hex}", password="x")
        request = RequestFactory().get("/")
        request.user = superuser
        model_admin = admin.site._registry[StageModelAssignment]
        return model_admin.get_form(request)

    def test_admin_form_rejects_an_over_cap_stage_budget(self):
        model = _openai_model(max_output_tokens=4_096)
        assignment = make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=model)
        form_class = self._admin_form_class()
        form = form_class(
            data={"stage": assignment.stage, "model": model.pk, "max_output_tokens": 8_192},
            instance=assignment,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("max_output_tokens", form.errors)

    def test_admin_form_accepts_a_valid_stage_budget(self):
        model = _openai_model(max_output_tokens=16_384)
        assignment = make_stage_assignment(stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model)
        form_class = self._admin_form_class()
        form = form_class(
            data={"stage": assignment.stage, "model": model.pk, "max_output_tokens": 8_192},
            instance=assignment,
        )
        self.assertTrue(form.is_valid(), form.errors)


class MigrationPreservesExistingBehaviorTests(TestCase):
    """Item 8: a `StageModelAssignment` created the old way (no `max_output_tokens` at all, as
    every row created before this migration was) resolves to exactly the same effective budget as
    before this change -- the new field is additive, opt-in, and defaults to inert."""

    def test_a_pre_existing_style_assignment_row_is_unaffected(self):
        model = _openai_model(max_output_tokens=4_096)
        # Mirrors exactly how every StageModelAssignment row was created before this field
        # existed -- no max_output_tokens kwarg at all.
        StageModelAssignment.objects.create(stage=StageModelAssignment.Stage.AB_BUILD, model=model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)

        self.assertIsNone(
            StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AB_BUILD).max_output_tokens
        )
        self.assertEqual(adapter.effective_max_output_tokens, 4_096)


class ProviderRequestReceivesEffectiveBudgetTests(TestCase):
    """Item 6: the actual `NormalizedLLMRequest` a pipeline service builds carries the resolved
    `effective_max_output_tokens` -- proven directly against Agent Jobber, the stage that
    motivated this change, using the real registry resolution path (not a hand-picked constant)."""

    def test_analyze_posting_builds_a_request_carrying_the_resolved_stage_budget(self):
        from job_intake.services.analyze import analyze_posting

        model = _openai_model(max_output_tokens=16_384)
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, max_output_tokens=8_192
        )

        captured = {}

        class _CapturingAdapter:
            llm_model = model
            effective_max_output_tokens = 8_192

            def generate(self, request):
                captured["request"] = request
                return NormalizedLLMResult(content=None)

        with mock.patch(
            "job_intake.services.analyze.get_adapter_for_stage", return_value=_CapturingAdapter()
        ):
            analyze_posting("A short posting.")

        self.assertEqual(captured["request"].max_output_tokens, 8_192)

    def test_effective_budget_reaches_the_actual_provider_request_body(self):
        """One level deeper: `NormalizedLLMRequest.max_output_tokens` (whatever value a stage
        resolved to) is exactly what ends up in the OpenAI-style request body's `max_tokens`
        field -- the translation this whole feature depends on was not touched."""
        request = NormalizedLLMRequest(
            stage=StageModelAssignment.Stage.AJ_ANALYZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_TinyOutput,
            max_output_tokens=8_192,
        )
        body = build_chat_completion_body(request, "some-model", schema={})
        self.assertEqual(body["max_tokens"], 8_192)


class TruncationRemainsNonRetryableTests(TestCase):
    """Item 5: a `finish_reason=length` truncation (JobApplication 9's actual failure) is still
    classified CONFIGURATION, not a transient error -- stage-specific budgets change *what budget
    is requested*, never how a truncation response is classified or retried."""

    def test_finish_reason_length_is_configuration_and_non_retryable(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

        result = parse_openai_style_chat_completion(response)

        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertFalse(is_retryable(result))
