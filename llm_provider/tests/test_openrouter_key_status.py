"""Explicit OpenRouter key-status lookup (2026-09-05, D-029): `GET /api/v1/key`, using the
configured inference credential (never a management key), returning only documented non-secret
quota/limit fields. All network calls are mocked -- no live credential/network required.
"""

from __future__ import annotations

from unittest import mock

import requests
from django.test import TestCase

from ..models import LLMProvider
from ..openrouter_key_status import fetch_openrouter_key_status
from .factories import make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_KEY_STATUS"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class SuccessTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter key-status success test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_parses_documented_fields_on_success(self):
        payload = {
            "data": {
                "label": "my-key",
                "is_free_tier": True,
                "limit": 10.0,
                "limit_remaining": 3.5,
                "limit_reset": "daily",
                "usage": 6.5,
                "usage_daily": 1.0,
                "usage_weekly": 2.0,
                "usage_monthly": 3.0,
            }
        }
        with mock.patch("requests.get", return_value=_FakeResponse(payload=payload)) as get_mock:
            result = fetch_openrouter_key_status(self.provider)

        self.assertTrue(result.success)
        self.assertEqual(result.label, "my-key")
        self.assertTrue(result.is_free_tier)
        self.assertEqual(result.limit, 10.0)
        self.assertEqual(result.limit_remaining, 3.5)
        self.assertEqual(result.limit_reset, "daily")
        self.assertEqual(result.usage, 6.5)
        self.assertEqual(result.usage_daily, 1.0)
        self.assertEqual(result.usage_weekly, 2.0)
        self.assertEqual(result.usage_monthly, 3.0)
        self.assertEqual(result.error_message, "")

        args, kwargs = get_mock.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/key")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer dummy-test-value")

    def test_never_uses_the_credits_endpoint(self):
        with mock.patch("requests.get", return_value=_FakeResponse(payload={"data": {}})) as get_mock:
            fetch_openrouter_key_status(self.provider)
        called_url = get_mock.call_args.args[0]
        self.assertNotIn("credits", called_url)
        self.assertTrue(called_url.endswith("/key"))

    def test_credential_value_never_appears_on_the_result_object(self):
        with mock.patch("requests.get", return_value=_FakeResponse(payload={"data": {}})):
            result = fetch_openrouter_key_status(self.provider)
        for value in dataclasses_asdict_values(result):
            if isinstance(value, str):
                self.assertNotIn("dummy-test-value", value)


def dataclasses_asdict_values(obj):
    import dataclasses

    return dataclasses.asdict(obj).values()


class FailureModeTests(TestCase):
    def setUp(self):
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter key-status failure test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )

    def test_missing_credential_fails_closed_without_any_http_call(self):
        with mock.patch("requests.get") as get_mock:
            result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertIn(_CREDENTIAL_ENV_VAR, result.error_message)
        get_mock.assert_not_called()

    def test_non_openrouter_provider_is_rejected_without_any_http_call(self):
        other = make_provider(
            provider_type=LLMProvider.ProviderType.OPENAI, name="Non-OpenRouter key-status test"
        )
        with mock.patch("requests.get") as get_mock:
            result = fetch_openrouter_key_status(other)
        self.assertFalse(result.success)
        get_mock.assert_not_called()

    def test_authentication_error_is_reported_sanitized(self):
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "bad-key"}):
            with mock.patch("requests.get", return_value=_FakeResponse(status_code=401)):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Authentication failed.")

    def test_timeout_is_reported_sanitized(self):
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy"}):
            with mock.patch("requests.get", side_effect=requests.Timeout("read timed out after 10s")):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertIn("Timed out", result.error_message)
        self.assertNotIn("10s", result.error_message)

    def test_network_error_message_is_sanitized_not_raw(self):
        raw = 'ConnectionError({"secret": "should-not-leak", "nested": {"a": 1}})'
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy"}):
            with mock.patch("requests.get", side_effect=requests.ConnectionError(raw)):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertNotIn("should-not-leak", result.error_message)
        self.assertIn("[redacted body]", result.error_message)

    def test_malformed_non_json_payload_fails_closed(self):
        response = _FakeResponse(status_code=200)
        response.json = mock.Mock(side_effect=ValueError("no JSON"))
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy"}):
            with mock.patch("requests.get", return_value=response):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertIn("Malformed", result.error_message)

    def test_unexpected_response_shape_fails_closed(self):
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy"}):
            with mock.patch("requests.get", return_value=_FakeResponse(payload={"unexpected": True})):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)

    def test_server_error_status_fails_closed(self):
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy"}):
            with mock.patch("requests.get", return_value=_FakeResponse(status_code=503)):
                result = fetch_openrouter_key_status(self.provider)
        self.assertFalse(result.success)
        self.assertIn("503", result.error_message)
