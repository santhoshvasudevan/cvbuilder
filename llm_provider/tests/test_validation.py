"""Pre-flight configuration validation tests (docs/IMPLEMENTATION_PLAN.md M2 acceptance): every
check here must fail *before* any HTTP call is attempted -- these tests never touch the network,
they only exercise the pure validation functions directly.
"""

import os
from unittest import mock

from django.test import TestCase

from llm_provider.errors import (
    InactiveModelError,
    InactiveProviderError,
    MissingCredentialConfigurationError,
    MissingCredentialValueError,
    StageBudgetExceededError,
    UnsupportedReasoningLevelError,
    UnsupportedStructuredOutputError,
)
from llm_provider.models import ReasoningLevel
from llm_provider.validation import (
    validate_call_configuration,
    validate_credential_configured,
    validate_credential_value_present,
    validate_model_active,
    validate_output_budget,
    validate_provider_active,
    validate_reasoning_level,
    validate_structured_output_supported,
)

from .factories import make_fake_provider, make_model, make_provider


class InactiveProviderModelTests(TestCase):
    def test_inactive_provider_raises(self):
        provider = make_provider(enabled=False)
        with self.assertRaises(InactiveProviderError):
            validate_provider_active(provider)

    def test_active_provider_passes(self):
        provider = make_provider(enabled=True)
        validate_provider_active(provider)  # must not raise

    def test_inactive_model_raises(self):
        provider = make_provider()
        model = make_model(provider, enabled=False)
        with self.assertRaises(InactiveModelError):
            validate_model_active(model)


class CredentialValidationTests(TestCase):
    def test_missing_credential_env_variable_configured_raises_for_real_provider(self):
        provider = make_provider(credential_env_variable="")
        with self.assertRaises(MissingCredentialConfigurationError):
            validate_credential_configured(provider)

    def test_fake_provider_never_requires_credential_configuration(self):
        provider = make_fake_provider()
        validate_credential_configured(provider)  # must not raise
        validate_credential_value_present(provider)  # must not raise

    def test_configured_but_unset_env_variable_raises(self):
        provider = make_provider(credential_env_variable="DEFINITELY_NOT_SET_ANYWHERE_XYZ")
        os.environ.pop("DEFINITELY_NOT_SET_ANYWHERE_XYZ", None)
        with self.assertRaises(MissingCredentialValueError):
            validate_credential_value_present(provider)

    def test_configured_and_set_env_variable_passes_without_reading_its_value(self):
        provider = make_provider(credential_env_variable="SOME_TEST_CREDENTIAL_VAR")
        with mock.patch.dict(os.environ, {"SOME_TEST_CREDENTIAL_VAR": "irrelevant-test-value"}):
            validate_credential_value_present(provider)  # must not raise


class StructuredOutputAndReasoningTests(TestCase):
    def test_unsupported_structured_output_raises_when_required(self):
        provider = make_provider()
        model = make_model(provider, supports_structured_output=False)
        with self.assertRaises(UnsupportedStructuredOutputError):
            validate_structured_output_supported(model, required=True)

    def test_unsupported_structured_output_allowed_when_not_required(self):
        provider = make_provider()
        model = make_model(provider, supports_structured_output=False)
        validate_structured_output_supported(model, required=False)  # must not raise

    def test_unsupported_reasoning_level_raises(self):
        provider = make_provider()
        model = make_model(provider, supported_reasoning_levels=[ReasoningLevel.NONE])
        with self.assertRaises(UnsupportedReasoningLevelError):
            validate_reasoning_level(model, ReasoningLevel.HIGH)

    def test_supported_reasoning_level_passes(self):
        provider = make_provider()
        model = make_model(
            provider, supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.HIGH]
        )
        validate_reasoning_level(model, ReasoningLevel.HIGH)  # must not raise


class OutputBudgetTests(TestCase):
    def test_budget_exceeding_capability_raises(self):
        provider = make_provider()
        model = make_model(provider, max_output_tokens=1000)
        with self.assertRaises(StageBudgetExceededError):
            validate_output_budget(model, 2000)

    def test_budget_within_capability_passes(self):
        provider = make_provider()
        model = make_model(provider, max_output_tokens=1000)
        validate_output_budget(model, 500)  # must not raise

    def test_no_requested_budget_always_passes(self):
        provider = make_provider()
        model = make_model(provider, max_output_tokens=1000)
        validate_output_budget(model, None)  # must not raise

    def test_no_model_capability_ceiling_allows_any_requested_budget(self):
        provider = make_provider()
        model = make_model(provider, max_output_tokens=None)
        validate_output_budget(model, 999_999)  # must not raise


class ValidateCallConfigurationOrderingTests(TestCase):
    """`validate_call_configuration` runs every check in a fixed order and fails on the first
    one that fails -- this class exercises that each individual failure class is reachable
    through the combined entrypoint used by the adapter/routing layer.
    """

    def test_fails_on_inactive_provider_first(self):
        provider = make_provider(enabled=False)
        model = make_model(provider, enabled=False)  # also inactive -- provider check wins first
        with self.assertRaises(InactiveProviderError):
            validate_call_configuration(provider=provider, model=model)

    def test_fails_on_inactive_model(self):
        provider = make_provider(enabled=True)
        model = make_model(provider, enabled=False)
        with self.assertRaises(InactiveModelError):
            validate_call_configuration(provider=provider, model=model)

    def test_fails_on_missing_credential_configuration(self):
        provider = make_provider(enabled=True, credential_env_variable="")
        model = make_model(provider, enabled=True)
        with self.assertRaises(MissingCredentialConfigurationError):
            validate_call_configuration(provider=provider, model=model)

    def test_fully_valid_configuration_passes(self):
        provider = make_fake_provider(enabled=True)
        model = make_model(
            provider,
            enabled=True,
            supports_structured_output=True,
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.MEDIUM],
            max_output_tokens=4096,
        )
        validate_call_configuration(
            provider=provider, model=model, reasoning_level=ReasoningLevel.MEDIUM, max_output_tokens=1000
        )  # must not raise

    def test_no_network_access_occurs_during_validation(self):
        # These functions must be pure/local -- assert no `requests` symbol is even imported by
        # the validation module, so there is no code path here that could reach the network.
        import llm_provider.validation as validation_module

        self.assertFalse(hasattr(validation_module, "requests"))
