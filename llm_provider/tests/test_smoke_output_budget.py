"""Reasoning-enabled smoke-test output-token budget (2026-09-04, D-026): a plain 64-token smoke
budget is not a valid qualification budget for a reasoning-enabled request, since reasoning
tokens consume the same completion-token allowance as the final answer -- this is exactly the
condition that produced the real OpenRouter `message.content=None` incident covered in
`test_null_content_handling.py`. `resolve_smoke_max_output_tokens` and `run_smoke_test`'s wiring
of it are exercised here; none of this ever touches `LLMModel.max_output_tokens`,
`StageModelAssignment`, or any application-stage request budget.
"""

from __future__ import annotations

import contextlib
import io
from unittest import mock

from django.test import TestCase

from ..models import LLMProvider, StageModelAssignment
from ..smoke.common import (
    DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS,
    DEFAULT_SMOKE_MAX_OUTPUT_TOKENS,
    SMOKE_MAX_OUTPUT_TOKENS_CEILING,
    InvalidSmokeOutputBudgetError,
    resolve_smoke_max_output_tokens,
    run_smoke_test,
)
from .factories import make_model, make_provider


def _run_smoke_test_quietly(*args, **kwargs) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        run_smoke_test(*args, **kwargs)
    return buffer.getvalue()


class ResolveSmokeMaxOutputTokensTests(TestCase):
    def test_non_reasoning_default_is_64(self):
        self.assertEqual(DEFAULT_SMOKE_MAX_OUTPUT_TOKENS, 64)
        self.assertEqual(
            resolve_smoke_max_output_tokens(None, reasoning_enabled=False, model_capability=None),
            64,
        )

    def test_non_reasoning_default_when_reasoning_flag_is_none(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(None, reasoning_enabled=None, model_capability=None),
            DEFAULT_SMOKE_MAX_OUTPUT_TOKENS,
        )

    def test_reasoning_default_is_4096(self):
        self.assertEqual(DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS, 4096)
        self.assertEqual(
            resolve_smoke_max_output_tokens(None, reasoning_enabled=True, model_capability=None),
            4096,
        )

    def test_explicit_value_overrides_reasoning_default(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(256, reasoning_enabled=True, model_capability=None), 256
        )

    def test_explicit_value_overrides_non_reasoning_default(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(1000, reasoning_enabled=False, model_capability=None),
            1000,
        )

    def test_zero_is_invalid(self):
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(0, reasoning_enabled=False, model_capability=None)

    def test_negative_is_invalid(self):
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(-10, reasoning_enabled=True, model_capability=None)

    def test_non_integer_is_invalid(self):
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(3.5, reasoning_enabled=False, model_capability=None)

    def test_bool_is_invalid(self):
        # bool is a subclass of int in Python -- must never silently pass through as 1/0.
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(True, reasoning_enabled=False, model_capability=None)

    def test_above_ceiling_is_invalid(self):
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(
                SMOKE_MAX_OUTPUT_TOKENS_CEILING + 1, reasoning_enabled=True, model_capability=None
            )

    def test_at_ceiling_is_valid(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(
                SMOKE_MAX_OUTPUT_TOKENS_CEILING, reasoning_enabled=True, model_capability=None
            ),
            SMOKE_MAX_OUTPUT_TOKENS_CEILING,
        )

    def test_above_model_capability_is_invalid(self):
        with self.assertRaises(InvalidSmokeOutputBudgetError):
            resolve_smoke_max_output_tokens(2000, reasoning_enabled=True, model_capability=1000)

    def test_at_model_capability_is_valid(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(1000, reasoning_enabled=True, model_capability=1000),
            1000,
        )

    def test_unknown_model_capability_does_not_block_a_valid_value(self):
        self.assertEqual(
            resolve_smoke_max_output_tokens(2000, reasoning_enabled=True, model_capability=None),
            2000,
        )


class _FakeSmokeResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"content": '{"acknowledged": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class RunSmokeTestBudgetIntegrationTests(TestCase):
    """`run_smoke_test`'s actual wiring: the resolved budget reaches the real request body, and
    an invalid value is rejected before `requests.post` is ever called. Selection deliberately
    never passes `--model` (i.e. never passes `model_id` to `run_smoke_test`) -- `--model` routes
    through `_resolve_explicit_model`, which get-or-creates a *separate* "<provider> (smoke test)"
    registry row rather than reusing `self.model`; omitting it lets `select_default_model` resolve
    to the single registered candidate instead, exactly the pattern a real qualification run must
    use to exercise the actual registry row (see D-026 / the OpenRouter continuation report)."""

    def setUp(self):
        self.env_var = "TEST_OPENROUTER_BUDGET_KEY"
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter budget test",
            credential_env_var=self.env_var,
        )
        self.model = make_model(
            provider=provider,
            model_id="z-ai/glm-5.2:free",
            supports_reasoning=True,
            max_output_tokens=230400,
        )
        self.env_patch = mock.patch.dict("os.environ", {self.env_var: "dummy-nonempty-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_non_reasoning_default_reaches_request_body(self):
        with mock.patch("requests.post", return_value=_FakeSmokeResponse()) as post_mock:
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER)
        self.assertEqual(post_mock.call_args.kwargs["json"]["max_tokens"], 64)

    def test_reasoning_default_reaches_request_body(self):
        with mock.patch("requests.post", return_value=_FakeSmokeResponse()) as post_mock:
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER, reasoning_enabled=True)
        self.assertEqual(post_mock.call_args.kwargs["json"]["max_tokens"], 4096)

    def test_explicit_override_reaches_request_body(self):
        with mock.patch("requests.post", return_value=_FakeSmokeResponse()) as post_mock:
            _run_smoke_test_quietly(
                LLMProvider.ProviderType.OPENROUTER, reasoning_enabled=True, max_output_tokens=2048
            )
        self.assertEqual(post_mock.call_args.kwargs["json"]["max_tokens"], 2048)

    def test_invalid_explicit_value_never_calls_the_provider(self):
        with mock.patch("requests.post") as post_mock:
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER, max_output_tokens=-5)
        post_mock.assert_not_called()

    def test_non_integer_like_zero_never_calls_the_provider(self):
        with mock.patch("requests.post") as post_mock:
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER, max_output_tokens=0)
        post_mock.assert_not_called()

    def test_value_above_ceiling_never_calls_the_provider(self):
        with mock.patch("requests.post") as post_mock:
            _run_smoke_test_quietly(
                LLMProvider.ProviderType.OPENROUTER,
                max_output_tokens=SMOKE_MAX_OUTPUT_TOKENS_CEILING + 1,
            )
        post_mock.assert_not_called()

    def test_no_model_or_stage_configuration_is_mutated(self):
        with mock.patch("requests.post", return_value=_FakeSmokeResponse()):
            _run_smoke_test_quietly(
                LLMProvider.ProviderType.OPENROUTER, reasoning_enabled=True, max_output_tokens=2048
            )
        self.model.refresh_from_db()
        self.assertEqual(self.model.max_output_tokens, 230400)
        self.assertEqual(StageModelAssignment.objects.count(), 0)

    def test_network_guard_still_blocks_a_real_call_if_reached(self):
        """Sanity check that this test file's success depends on the mock, not on the guard
        happening to also block things -- and that the guard is still active for this path."""
        from ..testing import BlockedNetworkCallError

        with self.assertRaises(BlockedNetworkCallError):
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER)


class ValueAboveModelCapabilityTests(TestCase):
    """Isolated from `RunSmokeTestBudgetIntegrationTests` so the model's own capability (well
    below the smoke ceiling) is what triggers the rejection, not the ceiling."""

    def setUp(self):
        self.env_var = "TEST_OPENROUTER_LOW_CAPABILITY_KEY"
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter low-capability test",
            credential_env_var=self.env_var,
        )
        self.model = make_model(
            provider=provider,
            model_id="low-capability-model",
            supports_reasoning=True,
            max_output_tokens=500,
        )
        self.env_patch = mock.patch.dict("os.environ", {self.env_var: "dummy-nonempty-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_value_above_model_capability_never_calls_the_provider(self):
        with mock.patch("requests.post") as post_mock:
            _run_smoke_test_quietly(LLMProvider.ProviderType.OPENROUTER, max_output_tokens=1000)
        post_mock.assert_not_called()
