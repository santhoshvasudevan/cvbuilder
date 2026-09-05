"""Operator-facing OpenRouter diagnostics admin view (2026-09-05, D-029): authenticated-only,
explicit refresh action (never queried automatically), shows only non-secret key-status data and
sanitized RATE_LIMIT call-log entries -- never Candidate Memory/job/resume content (this app's
models don't reference any of that), never the credential value.
"""

from __future__ import annotations

import uuid
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from ..models import LLMCallLog, LLMProvider, OpenRouterKeyStatus
from ..openrouter_key_status import OpenRouterKeyStatusResult
from .factories import make_model, make_provider

_CREDENTIAL_ENV_VAR = "TEST_OPENROUTER_DIAG_KEY"
_CREDENTIAL_VALUE = "dummy-test-value-should-never-render"


def _diagnostics_url():
    return reverse("admin:llm_provider_openrouter_diagnostics")


def _refresh_url(provider_id):
    return reverse("admin:llm_provider_openrouter_diagnostics_refresh", args=[provider_id])


class AuthenticationRequiredTests(TestCase):
    def test_anonymous_request_is_redirected_to_login(self):
        client = Client()
        response = client.get(_diagnostics_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_non_staff_user_is_denied(self):
        User.objects.create_user(username="not-staff", password="x", is_staff=False)
        client = Client()
        client.login(username="not-staff", password="x")
        response = client.get(_diagnostics_url())
        self.assertEqual(response.status_code, 302)


class _StaffTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username=f"admin-{uuid.uuid4().hex}", password="x")
        self.client = Client()
        self.client.login(username=self.user.username, password="x")
        self.provider = make_provider(
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            name="OpenRouter diagnostics view test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(provider=self.provider, model_id="z-ai/glm-5.2:free")


class ViewContentTests(_StaffTestCase):
    def test_page_loads_for_staff_user(self):
        response = self.client.get(_diagnostics_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OpenRouter diagnostics")
        self.assertContains(response, self.provider.name)

    def test_never_refreshed_state_is_shown(self):
        response = self.client.get(_diagnostics_url())
        self.assertContains(response, "Never refreshed yet")

    def test_key_status_fields_render_after_a_stored_status(self):
        OpenRouterKeyStatus.objects.create(
            provider=self.provider,
            success=True,
            label="my-label",
            is_free_tier=True,
            limit=10.0,
            limit_remaining=4.0,
            limit_reset="daily",
            usage=6.0,
            usage_daily=1.0,
            usage_weekly=2.0,
            usage_monthly=3.0,
        )
        response = self.client.get(_diagnostics_url())
        self.assertContains(response, "my-label")
        self.assertContains(response, "daily")

    def test_credential_value_never_appears_in_the_rendered_page(self):
        with mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: _CREDENTIAL_VALUE}):
            response = self.client.get(_diagnostics_url())
        self.assertNotContains(response, _CREDENTIAL_VALUE)

    def test_recent_rate_limit_entries_are_shown_with_diagnostics(self):
        LLMCallLog.objects.create(
            provider=self.provider,
            model=self.model,
            stage="AC_MATCH",
            error_category="RATE_LIMIT",
            error_message="Rate limited.",
            rate_limit_diagnostics={
                "retry_after_seconds": 30,
                "limit": 100,
                "remaining": 0,
                "reset": "123456",
                "source": "upstream",
                "upstream_provider": "SomeProvider",
            },
        )
        response = self.client.get(_diagnostics_url())
        self.assertContains(response, "AC_MATCH")
        self.assertContains(response, "SomeProvider")
        self.assertContains(response, "30")

    def test_non_rate_limit_call_logs_do_not_appear_in_the_rate_limit_table(self):
        LLMCallLog.objects.create(
            provider=self.provider, model=self.model, stage="AC_MATCH", error_category=""
        )
        response = self.client.get(_diagnostics_url())
        self.assertContains(response, "No RATE_LIMIT entries recorded")

    def test_no_candidate_memory_or_job_content_field_names_appear(self):
        """This view only ever queries llm_provider's own models -- confirm no other app's
        template vocabulary (job posting, resume, candidate memory) leaks in, as a regression
        guard against someone later wiring in more context than this task authorizes."""
        response = self.client.get(_diagnostics_url())
        for forbidden in ("JobRequirementAnalysis", "ResumeDraft", "MemoryClaim", "FitAssessment"):
            self.assertNotContains(response, forbidden)


class RefreshActionTests(_StaffTestCase):
    def test_get_request_to_refresh_url_does_not_perform_a_refresh(self):
        with mock.patch(
            "llm_provider.admin.fetch_openrouter_key_status"
        ) as fetch_mock:
            self.client.get(_refresh_url(self.provider.id))
        fetch_mock.assert_not_called()

    def test_post_triggers_exactly_one_fetch_and_upserts_the_status_row(self):
        result = OpenRouterKeyStatusResult(success=True, label="fresh-label", limit=5.0)
        with mock.patch(
            "llm_provider.admin.fetch_openrouter_key_status", return_value=result
        ) as fetch_mock:
            response = self.client.post(_refresh_url(self.provider.id), follow=True)
        fetch_mock.assert_called_once_with(self.provider)
        self.assertEqual(response.status_code, 200)
        status = OpenRouterKeyStatus.objects.get(provider=self.provider)
        self.assertTrue(status.success)
        self.assertEqual(status.label, "fresh-label")

    def test_refresh_never_happens_automatically_on_page_load(self):
        """Loading the diagnostics page itself must never call the OpenRouter key-status
        endpoint -- only the explicit POST refresh action may."""
        with mock.patch("llm_provider.admin.fetch_openrouter_key_status") as fetch_mock:
            self.client.get(_diagnostics_url())
        fetch_mock.assert_not_called()

    def test_failed_refresh_is_recorded_and_reported_without_raising(self):
        result = OpenRouterKeyStatusResult(success=False, error_message="Authentication failed.")
        with mock.patch("llm_provider.admin.fetch_openrouter_key_status", return_value=result):
            response = self.client.post(_refresh_url(self.provider.id), follow=True)
        self.assertEqual(response.status_code, 200)
        status = OpenRouterKeyStatus.objects.get(provider=self.provider)
        self.assertFalse(status.success)
        self.assertEqual(status.error_message, "Authentication failed.")

    def test_refresh_for_non_openrouter_provider_is_rejected(self):
        other = make_provider(provider_type=LLMProvider.ProviderType.OPENAI, name="Not OpenRouter")
        with mock.patch("llm_provider.admin.fetch_openrouter_key_status") as fetch_mock:
            response = self.client.post(_refresh_url(other.id), follow=True)
        fetch_mock.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OpenRouterKeyStatus.objects.filter(provider=other).exists())

    def test_refresh_requires_csrf_protection(self):
        enforced_client = Client(enforce_csrf_checks=True)
        enforced_client.login(username=self.user.username, password="x")
        response = enforced_client.post(_refresh_url(self.provider.id))
        self.assertEqual(response.status_code, 403)
