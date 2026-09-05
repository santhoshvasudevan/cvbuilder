"""OpenRouter provider integration (2026-09-04): transport, capability/configuration correctness,
structured output, privacy routing, reasoning, and safety/auditability for `OpenRouterAdapter`.
All network calls are mocked (`requests.post`) -- these are deterministic, no live credential/
network required, and this repo's `NetworkGuardedTestRunner` structurally guarantees no real call
can reach `openrouter.ai` even if a test forgot to mock. Credentials used here are dummy values
that never leave the test process.

This file does not assert anything about resume/recruiter semantic quality -- see CLAUDE.md/
docs/TEST_STRATEGY.md: that is explicitly out of scope for provider-transport tests.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

import requests
from django.test import TestCase
from pydantic import BaseModel

from ..adapters import get_adapter_for_stage
from ..adapters.openai import build_chat_completion_body
from ..adapters.openrouter import OpenRouterAdapter
from ..errors import LLMErrorCategory
from ..models import LLMCallLog, LLMProvider, StageModelAssignment
from ..retry import is_retryable
from ..testing import BlockedNetworkCallError
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider, make_stage_assignment

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_KEY"
_MODEL_ID = "z-ai/glm-5.2:free"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = (
            payload
            if payload is not None
            else {
                "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }
        )

    def json(self):
        return self._payload


def _request(**overrides) -> NormalizedLLMRequest:
    defaults = dict(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=2048,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class _OpenRouterTestCase(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(
            provider=self.provider,
            model_id=_MODEL_ID,
            supports_structured_output=True,
            supports_reasoning=True,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)


# 1. Provider registry selects the OpenRouter adapter.
class RegistrySelectionTests(_OpenRouterTestCase):
    def test_get_adapter_for_stage_returns_openrouter_adapter(self):
        make_stage_assignment(stage=StageModelAssignment.Stage.AC_MATCH, model=self.model)
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AC_MATCH)
        self.assertIsInstance(adapter, OpenRouterAdapter)
        self.assertEqual(adapter.llm_model, self.model)


# 2. Correct endpoint and Bearer authentication.
class EndpointAndAuthTests(_OpenRouterTestCase):
    def test_posts_to_the_documented_chat_completions_endpoint_with_bearer_auth(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer dummy-test-value")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_provider_base_url_override_is_respected(self):
        self.provider.base_url = "https://example-openrouter-proxy.invalid/v1"
        self.provider.save(update_fields=["base_url"])
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(
            post_mock.call_args.args[0],
            "https://example-openrouter-proxy.invalid/v1/chat/completions",
        )


# 3. Missing credential fails before HTTP.
class MissingCredentialTests(TestCase):
    def test_missing_credential_is_configuration_error_and_makes_no_http_call(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER, credential_env_var="DOES_NOT_EXIST_ENV_VAR"
        )
        model = make_model(
            provider=provider, model_id=_MODEL_ID, supports_structured_output=True
        )
        with mock.patch("requests.post") as post_mock:
            result = OpenRouterAdapter(model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        post_mock.assert_not_called()


# 4. Optional attribution headers included only when configured.
class AttributionHeaderTests(_OpenRouterTestCase):
    def test_headers_present_when_env_vars_configured(self):
        with mock.patch.dict(
            "os.environ",
            {"OPENROUTER_HTTP_REFERER": "https://example.invalid", "OPENROUTER_APP_TITLE": "cvbuilder"},
        ):
            with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
                OpenRouterAdapter(self.model).generate(_request())
        headers = post_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["HTTP-Referer"], "https://example.invalid")
        self.assertEqual(headers["X-OpenRouter-Title"], "cvbuilder")

    def test_headers_absent_when_env_vars_unset(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("OPENROUTER_HTTP_REFERER", None)
            os.environ.pop("OPENROUTER_APP_TITLE", None)
            with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
                OpenRouterAdapter(self.model).generate(_request())
        headers = post_mock.call_args.kwargs["headers"]
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-OpenRouter-Title", headers)


# 5. Exact free model slug is sent without fallback.
class ExactModelSlugTests(_OpenRouterTestCase):
    def test_exact_configured_model_id_is_sent(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(post_mock.call_args.kwargs["json"]["model"], "z-ai/glm-5.2:free")

    def test_model_slug_is_identical_across_a_failed_then_retried_attempt(self):
        """No fallback to a paid model or a different slug ever happens mid-retry."""
        responses = [_FakeHttpResponse(status_code=503), _FakeHttpResponse()]
        with mock.patch("requests.post", side_effect=responses) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        sent_models = {call.kwargs["json"]["model"] for call in post_mock.call_args_list}
        self.assertEqual(sent_models, {"z-ai/glm-5.2:free"})


# 6. Normalized messages, sampling values, effective max_tokens, and stream=false.
class RequestBodyConstructionTests(_OpenRouterTestCase):
    def test_messages_temperature_max_tokens_and_stream_false(self):
        request = _request(
            messages=[{"role": "user", "content": "normalized message"}],
            temperature=0.3,
            max_output_tokens=1234,
        )
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(request)
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["messages"], [{"role": "user", "content": "normalized message"}])
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["max_tokens"], 1234)
        self.assertIs(body["stream"], False)

    def test_top_p_reaches_the_body_only_when_set(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request(top_p=0.9))
        self.assertEqual(post_mock.call_args.kwargs["json"]["top_p"], 0.9)

        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertNotIn("top_p", post_mock.call_args.kwargs["json"])


# 7. Structured JSON-schema request construction.
class StructuredOutputRequestTests(_OpenRouterTestCase):
    def test_response_format_uses_the_existing_schema_generation_contract(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        body = post_mock.call_args.kwargs["json"]
        expected = build_chat_completion_body(
            _request(), self.model.model_id, OpenRouterAdapter.translate_schema(_TinyOutput)
        )["response_format"]
        self.assertEqual(body["response_format"], expected)
        self.assertEqual(body["response_format"]["json_schema"]["name"], "_TinyOutput")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_rejects_when_model_not_marked_structured_output_capable(self):
        model = make_model(
            provider=self.provider,
            model_id="some-other-model",
            supports_structured_output=False,
        )
        with mock.patch("requests.post") as post_mock:
            result = OpenRouterAdapter(model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        post_mock.assert_not_called()


# 8/9/10. provider.require_parameters / data_collection default+fail-closed.
class ProviderRoutingObjectTests(_OpenRouterTestCase):
    def test_require_parameters_is_true(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertIs(post_mock.call_args.kwargs["json"]["provider"]["require_parameters"], True)

    def test_default_data_collection_is_deny(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(post_mock.call_args.kwargs["json"]["provider"]["data_collection"], "deny")

    def test_explicit_allow_policy_is_respected(self):
        self.provider.data_collection_policy = LLMProvider.DataCollectionPolicy.ALLOW
        self.provider.save(update_fields=["data_collection_policy"])
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(post_mock.call_args.kwargs["json"]["provider"]["data_collection"], "allow")

    def test_invalid_data_collection_policy_fails_closed_before_any_http_call(self):
        # Bypasses full_clean()/the admin form's choices validation, mirroring exactly how the
        # existing stage-budget defense-in-depth tests construct an invalid row (fixture/script).
        LLMProvider.objects.filter(pk=self.provider.pk).update(data_collection_policy="MAYBE")
        self.provider.refresh_from_db()
        with mock.patch("requests.post") as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        post_mock.assert_not_called()


# 11/12. Reasoning enabled/disabled/unsupported.
class ReasoningTests(_OpenRouterTestCase):
    def test_reasoning_enabled_maps_to_unified_reasoning_parameter(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request(reasoning_enabled=True))
        self.assertEqual(post_mock.call_args.kwargs["json"]["reasoning"], {"enabled": True})

    def test_reasoning_omitted_when_not_explicitly_enabled(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())  # reasoning_enabled=None
        self.assertNotIn("reasoning", post_mock.call_args.kwargs["json"])

    def test_reasoning_omitted_when_explicitly_disabled(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenRouterAdapter(self.model).generate(_request(reasoning_enabled=False))
        self.assertNotIn("reasoning", post_mock.call_args.kwargs["json"])

    def test_reasoning_enabled_against_unsupported_model_is_rejected_before_http(self):
        model = make_model(
            provider=self.provider,
            model_id="no-reasoning-model",
            supports_structured_output=True,
            supports_reasoning=False,
        )
        with mock.patch("requests.post") as post_mock:
            result = OpenRouterAdapter(model).generate(_request(reasoning_enabled=True))
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        post_mock.assert_not_called()


# 13. reasoning_details/reasoning_content can never substitute for missing final content.
class ReasoningNeverSubstitutesForContentTests(_OpenRouterTestCase):
    def test_reasoning_fields_present_but_content_missing_still_errors(self):
        payload = {
            "choices": [
                {
                    "message": {"reasoning_content": "internal chain-of-thought"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_reasoning_fields_alongside_valid_content_are_never_parsed_as_the_answer(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"ok": "yes"}',
                        "reasoning": "internal chain-of-thought, never trusted",
                        "reasoning_details": [{"type": "reasoning.text", "text": "secret"}],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.ok, "yes")


# 14. Raw reasoning is never stored in LLMCallLog.
class NoRawReasoningInLogsTests(_OpenRouterTestCase):
    def test_llm_call_log_never_contains_reasoning_text(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"ok": "yes"}',
                        "reasoning": "super secret internal reasoning trace",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            OpenRouterAdapter(self.model).generate(_request(reasoning_enabled=True))
        log = LLMCallLog.objects.get()
        for field in LLMCallLog._meta.fields:
            value = getattr(log, field.name)
            self.assertNotIn("super secret internal reasoning trace", str(value))


# 15/16. Successful structured response normalization + usage/finish_reason extraction.
class ResponseNormalizationTests(_OpenRouterTestCase):
    def test_successful_response_validates_against_the_pydantic_schema(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertFalse(result.is_error)
        self.assertIsInstance(result.content, _TinyOutput)
        self.assertEqual(result.content.ok, "yes")

    def test_usage_is_extracted(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(result.usage.input_tokens, 11)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertEqual(result.usage.total_tokens, 18)


# 17. Empty/missing choices and content.
class EmptyOrMissingContentTests(_OpenRouterTestCase):
    def test_empty_choices_list_is_an_error(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload={"choices": []})):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_missing_message_content_key_is_an_error(self):
        payload = {"choices": [{"message": {}, "finish_reason": "stop"}]}
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)


# 18. Malformed JSON content.
class MalformedContentTests(_OpenRouterTestCase):
    def test_non_json_content_is_schema_validation_error_without_leaking_content(self):
        payload = {
            "choices": [{"message": {"content": "not json, candidate secret"}, "finish_reason": "stop"}]
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)
        self.assertNotIn("candidate secret", result.error.message)


# 19. Status-code classification, including 402, 429, 524, and 529.
class StatusCodeClassificationTests(_OpenRouterTestCase):
    def _classify(self, status_code):
        with mock.patch("requests.post", return_value=_FakeHttpResponse(status_code=status_code)):
            return OpenRouterAdapter(self.model).generate(_request())

    def test_402_is_configuration_billing(self):
        result = self._classify(402)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)

    def test_429_is_rate_limit(self):
        result = self._classify(429)
        self.assertEqual(result.error.category, LLMErrorCategory.RATE_LIMIT)

    def test_524_is_provider_internal(self):
        result = self._classify(524)
        self.assertEqual(result.error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    def test_529_is_provider_internal(self):
        result = self._classify(529)
        self.assertEqual(result.error.category, LLMErrorCategory.PROVIDER_INTERNAL)

    def test_408_is_timeout(self):
        result = self._classify(408)
        self.assertEqual(result.error.category, LLMErrorCategory.TIMEOUT)

    def test_400_413_422_are_non_transient(self):
        from ..errors import TRANSIENT_ERROR_CATEGORIES

        for status_code in (400, 413, 422):
            result = self._classify(status_code)
            self.assertTrue(result.is_error)
            self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)

    def test_404_410_are_configuration(self):
        for status_code in (404, 410):
            result = self._classify(status_code)
            self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)


# 20. finish_reason=length remains non-retryable.
class TruncationNonRetryableTests(_OpenRouterTestCase):
    def test_finish_reason_length_is_configuration_and_never_retried(self):
        payload = {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 2048, "total_tokens": 2148},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertFalse(is_retryable(result))
        self.assertEqual(post_mock.call_count, 1)


# 21/22. Bounded retry for transient failures; no retry for non-transient ones.
class RetryBehaviorTests(_OpenRouterTestCase):
    def test_transient_failure_then_success_is_retried_and_succeeds(self):
        responses = [_FakeHttpResponse(status_code=503), _FakeHttpResponse()]
        with mock.patch("requests.post", side_effect=responses) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertFalse(result.is_error)
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(result.retry_count, 1)

    def test_retries_are_bounded_and_eventually_fail(self):
        with mock.patch(
            "requests.post", return_value=_FakeHttpResponse(status_code=503)
        ) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertTrue(result.is_error)
        self.assertEqual(post_mock.call_count, OpenRouterAdapter(self.model).retry_policy.max_attempts)

    def test_auth_failure_is_never_retried(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse(status_code=401)) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(result.error.category, LLMErrorCategory.AUTH)
        self.assertEqual(post_mock.call_count, 1)

    def test_billing_failure_is_never_retried(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse(status_code=402)) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertEqual(post_mock.call_count, 1)

    def test_schema_validation_failure_is_never_retried(self):
        payload = {"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]}
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)) as post_mock:
            result = OpenRouterAdapter(self.model).generate(_request())
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)
        self.assertEqual(post_mock.call_count, 1)

    def test_configuration_failure_missing_credential_is_never_retried(self):
        provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER, credential_env_var="STILL_MISSING"
        )
        model = make_model(provider=provider, model_id=_MODEL_ID, supports_structured_output=True)
        with mock.patch("requests.post") as post_mock:
            OpenRouterAdapter(model).generate(_request())
        post_mock.assert_not_called()


# 23. No fallback to paid or different models even across a retried sequence of failures.
class NoModelFallbackTests(_OpenRouterTestCase):
    def test_repeated_transient_failures_never_change_the_requested_model(self):
        with mock.patch(
            "requests.post", return_value=_FakeHttpResponse(status_code=502)
        ) as post_mock:
            OpenRouterAdapter(self.model).generate(_request())
        sent_models = {call.kwargs["json"]["model"] for call in post_mock.call_args_list}
        self.assertEqual(sent_models, {_MODEL_ID})


# 24. Stage-specific effective token budgets reach the OpenRouter request body.
class StageBudgetReachesRequestBodyTests(_OpenRouterTestCase):
    def test_stage_override_budget_is_used_in_the_actual_request(self):
        make_stage_assignment(
            stage=StageModelAssignment.Stage.AB_BUILD, model=self.model, max_output_tokens=777
        )
        adapter = get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)
        self.assertEqual(adapter.effective_max_output_tokens, 777)

        request = _request(
            stage=StageModelAssignment.Stage.AB_BUILD, max_output_tokens=adapter.effective_max_output_tokens
        )
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            adapter.generate(request)
        self.assertEqual(post_mock.call_args.kwargs["json"]["max_tokens"], 777)


# 26. Full-suite network guard blocks an attempted real OpenRouter call.
class NetworkGuardBlocksRealCallTests(_OpenRouterTestCase):
    def test_generate_never_reaches_the_network_under_the_guard(self):
        with self.assertRaises(BlockedNetworkCallError):
            OpenRouterAdapter(self.model).generate(_request())

    def test_requests_post_itself_is_blocked(self):
        with self.assertRaises(BlockedNetworkCallError):
            requests.post("https://openrouter.ai/api/v1/chat/completions")
