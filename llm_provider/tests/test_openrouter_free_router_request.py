"""Regression coverage specific to the OpenRouter Free Models Router migration (D-038): the
`openrouter/free` model id reaching the wire exactly, with no prefix manipulation, plus the new
requested-vs-resolved-model / finish_reason audit fields.

`llm_provider/tests/test_openrouter_adapter.py` already covers the OpenRouterAdapter's general
transport/structured-output/privacy/retry/error-classification behavior against a generic model id
fixture (`z-ai/glm-5.2:free`) -- that coverage is provider-behavior-generic and applies unchanged
to any model id, including this one. This file adds only what's new or id-specific: the exact
`openrouter/free` wire value, absence of double-prefixing, and resolved-model/finish_reason
capture. All network calls are mocked (`requests.post`); no live credential/network required.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import TestCase
from pydantic import BaseModel

from ..adapters.openrouter import OpenRouterAdapter
from ..models import LLMCallLog, LLMProvider, StageModelAssignment
from ..services.openrouter_free_router import FREE_ROUTER_MODEL_ID
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_FREE_KEY"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload

    def json(self):
        return self._payload


class _FreeRouterTestCase(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(
            provider=self.provider,
            model_id=FREE_ROUTER_MODEL_ID,
            supports_structured_output=True,
            supports_reasoning=False,
            max_output_tokens=8192,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _request(self, **overrides) -> NormalizedLLMRequest:
        defaults = dict(
            stage=StageModelAssignment.Stage.AC_NORMALIZE,
            messages=[{"role": "user", "content": "hi"}],
            output_schema=_TinyOutput,
            max_output_tokens=64,
        )
        defaults.update(overrides)
        return NormalizedLLMRequest(**defaults)


class ExactModelIdOnTheWireTests(_FreeRouterTestCase):
    def test_exact_openrouter_free_id_reaches_the_serialized_request(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "model": FREE_ROUTER_MODEL_ID,
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                },
            )
            adapter.generate(self._request())
        sent_body = post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_body["model"], "openrouter/free")

    def test_never_double_prefixed_or_reconstructed(self):
        """The model id must reach the wire byte-for-byte -- never 'openrouter/openrouter/free',
        never 'openrouter:openrouter/free', never split-and-rejoined in any form."""
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        sent_model = post_mock.call_args.kwargs["json"]["model"]
        self.assertNotIn("openrouter/openrouter", sent_model)
        self.assertNotIn("openrouter:openrouter", sent_model)
        self.assertNotIn("z-ai", sent_model)
        self.assertEqual(sent_model, FREE_ROUTER_MODEL_ID)
        self.assertEqual(sent_model.count("/"), 1)

    def test_slash_containing_id_is_not_split_by_any_registry_or_adapter_code(self):
        # A model id with more than one slash-delimited segment must still reach the wire intact
        # -- generalizes the single-slash 'openrouter/free' case to guard against any parsing
        # logic that assumes a fixed prefix depth.
        weird_model = make_model(
            provider=self.provider,
            model_id="vendor/sub/weird-model:free",
            supports_structured_output=True,
        )
        adapter = OpenRouterAdapter(weird_model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["model"], "vendor/sub/weird-model:free"
        )


class ToolsAbsentTests(_FreeRouterTestCase):
    def test_tools_and_tool_choice_are_never_sent(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        sent_body = post_mock.call_args.kwargs["json"]
        self.assertNotIn("tools", sent_body)
        self.assertNotIn("tool_choice", sent_body)


class StructuredOutputContractTests(_FreeRouterTestCase):
    def test_response_format_and_require_parameters_present(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        sent_body = post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_body["response_format"]["type"], "json_schema")
        self.assertTrue(sent_body["response_format"]["json_schema"]["strict"])
        self.assertEqual(sent_body["response_format"]["json_schema"]["name"], "_TinyOutput")
        self.assertIn("schema", sent_body["response_format"]["json_schema"])
        self.assertTrue(sent_body["provider"]["require_parameters"])

    def test_reasoning_field_absent_for_the_non_reasoning_free_router_model(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        self.assertNotIn("reasoning", post_mock.call_args.kwargs["json"])

    def test_reasoning_request_against_free_router_model_is_rejected_before_any_http_call(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            result = adapter.generate(self._request(reasoning_enabled=True))
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category.value, "CONFIGURATION")


class ResolvedModelAndFinishReasonAuditTests(_FreeRouterTestCase):
    def test_resolved_model_captured_and_distinct_from_requested(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "model": "some-vendor/actually-routed-model-7b",
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                },
            )
            result = adapter.generate(self._request())

        self.assertFalse(result.is_error)
        self.assertEqual(result.resolved_model, "some-vendor/actually-routed-model-7b")
        self.assertEqual(result.finish_reason, "stop")

        log = LLMCallLog.objects.latest("id")
        # The requested registry model is untouched -- still exactly 'openrouter/free'.
        self.assertEqual(log.model.model_id, FREE_ROUTER_MODEL_ID)
        # The resolved model is recorded separately, distinct from the requested one.
        self.assertEqual(log.resolved_model_id, "some-vendor/actually-routed-model-7b")
        self.assertEqual(log.finish_reason, "stop")
        self.assertNotEqual(log.resolved_model_id, log.model.model_id)

    def test_resolved_model_blank_when_provider_does_not_report_one(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            result = adapter.generate(self._request())
        self.assertIsNone(result.resolved_model)
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.resolved_model_id, "")

    def test_resolved_model_never_guessed_on_error(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(401, {})
            result = adapter.generate(self._request())
        self.assertTrue(result.is_error)
        self.assertIsNone(result.resolved_model)
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.resolved_model_id, "")

    def test_correlation_id_carried_through_when_supplied(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request(correlation_id="job-application-9"))
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.correlation_id, "job-application-9")

    def test_correlation_id_blank_when_not_supplied(self):
        adapter = OpenRouterAdapter(self.model)
        with mock.patch("requests.post") as post_mock:
            post_mock.return_value = _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                    "usage": {},
                },
            )
            adapter.generate(self._request())
        log = LLMCallLog.objects.latest("id")
        self.assertEqual(log.correlation_id, "")
