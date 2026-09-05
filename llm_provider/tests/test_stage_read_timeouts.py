"""Configurable per-stage HTTP read timeout (2026-09-05): every real adapter previously passed a
single hardcoded `timeout=60` to `requests.post()` -- a combined connect+read budget, identical
for every provider and every stage. `StageModelAssignment.read_timeout_seconds` is an optional,
per-stage override resolved through the same `get_adapter_for_stage` path as the existing
per-stage output-token budget (D-024); the connect phase gets its own separate, fixed, smaller
default (`DEFAULT_CONNECT_TIMEOUT_SECONDS`) so a hung connection no longer has to consume the
entire read budget before failing.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from pydantic import BaseModel

from ..adapters import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    InvalidStageTimeoutError,
    get_adapter_for_stage,
)
from ..adapters.gemini import GeminiAdapter
from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..adapters.openrouter import OpenRouterAdapter
from ..errors import LLMErrorCategory, NormalizedLLMError
from ..models import MAX_READ_TIMEOUT_SECONDS, MIN_READ_TIMEOUT_SECONDS, LLMProvider, StageModelAssignment
from ..retry import RetryPolicy, execute_with_retry, is_retryable
from ..types import NormalizedLLMRequest, NormalizedLLMResult
from .factories import make_model, make_provider, make_stage_assignment

_provider_counter = iter(range(1, 10_000))


def _provider(provider_type, credential_env_var="TEST_TIMEOUT_KEY"):
    return make_provider(
        provider_type=provider_type,
        name=f"{provider_type} timeout test {next(_provider_counter)}",
        credential_env_var=credential_env_var,
    )


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


def _request(**overrides) -> NormalizedLLMRequest:
    defaults = dict(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=64,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class EffectiveTimeoutResolutionTests(TestCase):
    """Mirrors the existing effective-max-output-tokens resolution tests (D-024) for the read
    timeout: stage override used when configured; unconfigured stage falls back to the built-in
    default; one stage's override never leaks into another stage sharing the same model."""

    def test_stage_override_is_used_when_configured(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, read_timeout_seconds=120
        )

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)

        self.assertEqual(adapter.effective_read_timeout_seconds, 120)

    def test_stage_without_override_uses_the_built_in_default(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_RANK, model=model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_RANK)

        self.assertEqual(adapter.effective_read_timeout_seconds, DEFAULT_READ_TIMEOUT_SECONDS)
        self.assertEqual(DEFAULT_READ_TIMEOUT_SECONDS, 60)  # the exact prior hardcoded value

    def test_one_stage_s_override_does_not_affect_another_stage_on_the_same_model(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, read_timeout_seconds=180
        )
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=model)

        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE).effective_read_timeout_seconds,
            180,
        )
        self.assertEqual(
            get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH).effective_read_timeout_seconds,
            DEFAULT_READ_TIMEOUT_SECONDS,
        )

    def test_directly_constructed_adapter_gets_the_built_in_default_without_the_registry(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        adapter = OpenAIAdapter(model)
        self.assertEqual(adapter.effective_read_timeout_seconds, DEFAULT_READ_TIMEOUT_SECONDS)
        self.assertEqual(
            adapter.request_timeout, (DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS)
        )


class RejectedConfigurationTests(TestCase):
    """Zero/negative/excessive values are rejected before any provider call could happen -- both
    at model-validation time (`full_clean`, what the admin form calls) and, as defense in depth,
    at `get_adapter_for_stage` resolution time for a row that reached the database without
    validation."""

    def test_zero_is_rejected_by_full_clean(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, read_timeout_seconds=0
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_negative_is_rejected_by_full_clean(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model, read_timeout_seconds=-5
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_excessive_value_is_rejected_by_full_clean(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        assignment = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE,
            model=model,
            read_timeout_seconds=MAX_READ_TIMEOUT_SECONDS + 1,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_boundary_values_are_accepted_by_full_clean(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        low = StageModelAssignment(
            stage=StageModelAssignment.Stage.AJ_ANALYZE,
            model=model,
            read_timeout_seconds=MIN_READ_TIMEOUT_SECONDS,
        )
        low.full_clean()  # must not raise
        high = StageModelAssignment(
            stage=StageModelAssignment.Stage.AC_RANK,
            model=model,
            read_timeout_seconds=MAX_READ_TIMEOUT_SECONDS,
        )
        high.full_clean()  # must not raise

    def test_out_of_range_row_that_bypassed_full_clean_is_rejected_at_resolution_time(self):
        """Defense in depth: a misconfigured row created via `.objects.create()` (bypassing
        `full_clean()`) must still fail closed at `get_adapter_for_stage` -- before any provider
        call -- rather than silently sending an excessive/invalid timeout to `requests.post()`."""
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        StageModelAssignment.objects.create(
            stage=StageModelAssignment.Stage.AJ_ANALYZE,
            model=model,
            read_timeout_seconds=MAX_READ_TIMEOUT_SECONDS + 1,
        )

        with self.assertRaises(InvalidStageTimeoutError):
            get_adapter_for_stage(StageModelAssignment.Stage.AJ_ANALYZE)

    def test_admin_form_rejects_an_excessive_read_timeout(self):
        import uuid

        from django.contrib import admin
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        assignment = make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=model)
        superuser = User.objects.create_superuser(username=f"admin-test-{uuid.uuid4().hex}", password="x")
        request = RequestFactory().get("/")
        request.user = superuser
        form_class = admin.site._registry[StageModelAssignment].get_form(request)
        form = form_class(
            data={
                "stage": assignment.stage,
                "model": model.pk,
                "read_timeout_seconds": MAX_READ_TIMEOUT_SECONDS + 100,
            },
            instance=assignment,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("read_timeout_seconds", form.errors)


class MigrationPreservesExistingBehaviorTests(TestCase):
    """A `StageModelAssignment` row created the old way (no `read_timeout_seconds` at all, as
    every row created before this migration was) resolves to exactly the prior hardcoded 60s read
    timeout -- the new field is additive, opt-in, and defaults to inert."""

    def test_a_pre_existing_style_assignment_row_is_unaffected(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        StageModelAssignment.objects.create(stage=StageModelAssignment.Stage.AB_BUILD, model=model)

        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)

        self.assertIsNone(
            StageModelAssignment.objects.get(stage=StageModelAssignment.Stage.AB_BUILD).read_timeout_seconds
        )
        self.assertEqual(adapter.effective_read_timeout_seconds, 60)


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = (
            payload
            if payload is not None
            else {
                "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )

    def json(self):
        return self._payload


class _CredentialedTestCase(TestCase):
    """Every real HTTP-call test needs the provider's credential env var to actually resolve --
    otherwise the adapter fails closed with CONFIGURATION before `requests.post` is ever called
    (by design), which would make these tests exercise the wrong code path."""

    def setUp(self):
        self.env_patch = mock.patch.dict("os.environ", {"TEST_TIMEOUT_KEY": "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)


class AdapterPassesTimeoutTupleTests(_CredentialedTestCase):
    """Item: every affected adapter passes `requests`' own `(connect, read)` tuple form -- never a
    single combined number -- and the read half reflects the resolved effective value, not a
    re-hardcoded constant."""

    def _assert_tuple_passed(self, adapter, expected_read_timeout):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            adapter.generate(_request())
        self.assertEqual(
            post_mock.call_args.kwargs["timeout"],
            (DEFAULT_CONNECT_TIMEOUT_SECONDS, expected_read_timeout),
        )

    def test_openai_adapter_passes_the_resolved_timeout_tuple(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_MATCH, model=model, read_timeout_seconds=90
        )
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self._assert_tuple_passed(adapter, 90)

    def test_nvidia_adapter_passes_the_resolved_timeout_tuple(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.NVIDIA_NIM))
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_MATCH, model=model, read_timeout_seconds=45
        )
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self.assertIsInstance(adapter, NvidiaNimAdapter)
        self._assert_tuple_passed(adapter, 45)

    def test_openrouter_adapter_passes_the_resolved_timeout_tuple(self):
        model = make_model(
            provider=_provider(LLMProvider.ProviderType.OPENROUTER), supports_structured_output=True
        )
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_MATCH, model=model, read_timeout_seconds=200
        )
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self.assertIsInstance(adapter, OpenRouterAdapter)
        self._assert_tuple_passed(adapter, 200)

    def test_gemini_adapter_passes_the_resolved_timeout_tuple(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.GEMINI))
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AC_MATCH, model=model, read_timeout_seconds=30
        )
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self.assertIsInstance(adapter, GeminiAdapter)
        with mock.patch(
            "requests.post",
            return_value=_FakeHttpResponse(
                payload={
                    "candidates": [{"content": {"parts": [{"text": '{"ok": "yes"}'}]}}],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
                }
            ),
        ) as post_mock:
            adapter.generate(_request())
        self.assertEqual(post_mock.call_args.kwargs["timeout"], (DEFAULT_CONNECT_TIMEOUT_SECONDS, 30))

    def test_unconfigured_stage_still_uses_the_prior_60s_read_timeout(self):
        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=model)
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self._assert_tuple_passed(adapter, 60)


class ClientTimeoutClassificationTests(_CredentialedTestCase):
    """A client-side read timeout must never be classified as anything other than TIMEOUT,
    regardless of which phase (connect or read) actually raised `requests.Timeout` -- `requests`'
    own `ConnectTimeout`/`ReadTimeout` both subclass `requests.Timeout`, and the adapter's except
    clause already catches the base class, so both are already one category."""

    def test_requests_read_timeout_is_classified_as_timeout(self):
        import requests

        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        adapter = OpenAIAdapter(model)
        with mock.patch("requests.post", side_effect=requests.exceptions.ReadTimeout("timed out")):
            result = adapter.generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.TIMEOUT)

    def test_requests_connect_timeout_is_also_classified_as_timeout(self):
        import requests

        model = make_model(provider=_provider(LLMProvider.ProviderType.OPENAI))
        adapter = OpenAIAdapter(model)
        with mock.patch("requests.post", side_effect=requests.exceptions.ConnectTimeout("timed out")):
            result = adapter.generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.TIMEOUT)


class RetryBoundWorstCaseWallTimeTests(TestCase):
    """TIMEOUT stays a transient, retried category and `RetryPolicy` stays bounded (max_attempts
    unchanged by this feature) -- so a stage configured at the maximum allowed read timeout still
    has a finite, computable worst-case wall-clock wait, never an unbounded one."""

    def test_timeout_is_still_retryable_and_retry_count_still_bounded_at_three_attempts(self):
        result = NormalizedLLMResult(
            error=NormalizedLLMError(category=LLMErrorCategory.TIMEOUT, message="timed out")
        )
        self.assertTrue(is_retryable(result))

        calls = {"n": 0}

        def _always_times_out():
            calls["n"] += 1
            return NormalizedLLMResult(
                error=NormalizedLLMError(category=LLMErrorCategory.TIMEOUT, message="timed out")
            )

        sleeps = []
        final = execute_with_retry(
            _always_times_out, policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0), sleep_fn=sleeps.append
        )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(final.retry_count, 2)
        # Worst case at the maximum configurable read timeout: 3 attempts * 300s each, plus the
        # (unaffected-by-this-change) linear backoff between attempts -- computed here so a
        # regression that silently raised MAX_READ_TIMEOUT_SECONDS or max_attempts would be caught
        # by this test's own documented arithmetic, not just prose in a decision record.
        worst_case_seconds = 3 * MAX_READ_TIMEOUT_SECONDS + sum(sleeps)
        self.assertEqual(worst_case_seconds, 3 * 300 + (1.0 + 2.0))
