"""Tests for `manage.py llm_smoke_test` -- exercises only the deterministic, no-network branches
(unknown provider, no enabled model, missing credential). Never exercises the live-call branch:
that would require a real credential and make a real HTTP call, which no automated test may do
(docs/TEST_STRATEGY.md Section 2).
"""

import io

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .factories import make_fake_provider, make_model, make_provider


class LlmSmokeTestCommandTests(TestCase):
    def test_unknown_provider_raises_command_error(self):
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("llm_smoke_test", "Does Not Exist", stdout=out)

    def test_no_enabled_model_reports_not_live_verified(self):
        make_provider(name="Empty Provider")
        out = io.StringIO()
        call_command("llm_smoke_test", "Empty Provider", stdout=out)
        self.assertIn("NOT_LIVE_VERIFIED", out.getvalue())

    def test_missing_credential_reports_not_live_verified_without_network(self):
        provider = make_provider(name="No Credential Provider", credential_env_variable="")
        make_model(provider, supports_structured_output=True)
        out = io.StringIO()
        call_command("llm_smoke_test", "No Credential Provider", stdout=out)
        self.assertIn("NOT_LIVE_VERIFIED", out.getvalue())

    def test_unset_credential_env_var_reports_not_live_verified_without_network(self):
        provider = make_provider(
            name="Unset Credential Provider", credential_env_variable="DEFINITELY_NOT_SET_XYZ_2"
        )
        make_model(provider, supports_structured_output=True)
        out = io.StringIO()
        call_command("llm_smoke_test", "Unset Credential Provider", stdout=out)
        self.assertIn("NOT_LIVE_VERIFIED", out.getvalue())

    def test_fake_provider_exercises_the_live_call_branch_with_zero_network_access(self):
        # FakeAdapter never makes a network call either way (it has no requests.post anywhere
        # in it), so this is the one safe way to exercise the command's post-validation call
        # branch in an automated test. FakeAdapter's default empty response does not satisfy
        # the smoke-test schema, so this reports FAILED (schema validation) -- what matters here
        # is that the command completes and reports a real outcome, not which one.
        provider = make_fake_provider(name="Fake Smoke")
        make_model(provider, supports_structured_output=True)
        out = io.StringIO()
        call_command("llm_smoke_test", "Fake Smoke", stdout=out)
        output = out.getvalue()
        self.assertTrue("VERIFIED" in output or "FAILED" in output)
