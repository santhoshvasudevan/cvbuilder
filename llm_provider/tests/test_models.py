from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from job_applications.models import LLM_CAPABLE_STAGES, StageIdentifier
from llm_provider.models import (
    LLM_CAPABLE_STAGE_CHOICES,
    LLMCallLog,
    LLMModel,
    LLMProvider,
    ReasoningLevel,
    StageModelAssignment,
)

from .factories import make_fake_provider, make_model, make_provider, make_stage_assignment


class LLMProviderTests(TestCase):
    def test_create_with_defaults(self):
        provider = make_provider()
        self.assertTrue(provider.enabled)
        self.assertEqual(provider.adapter_type, LLMProvider.AdapterType.OPENAI)

    def test_name_must_be_unique(self):
        make_provider(name="Dup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_provider(name="Dup")

    def test_credential_value_never_stored(self):
        # Only the environment variable *name* is a field on this model -- there is no field
        # anywhere on LLMProvider that could hold a credential value (LLM-012).
        field_names = {f.name for f in LLMProvider._meta.get_fields()}
        self.assertIn("credential_env_variable", field_names)
        for suspicious in ("api_key", "secret", "credential_value", "token", "password"):
            self.assertNotIn(suspicious, field_names)


class LLMModelTests(TestCase):
    def setUp(self):
        self.provider = make_provider()

    def test_create_with_defaults(self):
        model = make_model(self.provider, supported_reasoning_levels=None)
        self.assertEqual(model.supported_reasoning_levels, [ReasoningLevel.NONE])
        self.assertFalse(model.supports_reasoning)

    def test_temperature_capability_fields_default_to_false(self):
        # LLMModel.objects.create bypasses the factory's explicit defaults -- assert the model
        # field defaults themselves (V2-D044: fail-closed, explicit opt-in required), not just
        # what the factory happens to pass.
        model = LLMModel.objects.create(
            provider=self.provider,
            model_identifier="defaults-check",
            supported_reasoning_levels=[ReasoningLevel.NONE],
        )
        self.assertFalse(model.supports_temperature)
        self.assertFalse(model.supports_temperature_with_reasoning)

    def test_clean_rejects_temperature_with_reasoning_when_temperature_itself_unsupported(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="inconsistent-temp-flags",
            supported_reasoning_levels=[ReasoningLevel.NONE],
            supports_temperature=False,
            supports_temperature_with_reasoning=True,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_clean_allows_both_temperature_flags_true(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="both-temp-flags-true",
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.HIGH],
            supports_temperature=True,
            supports_temperature_with_reasoning=True,
        )
        model.full_clean()  # must not raise

    def test_clean_allows_temperature_true_reasoning_combo_false(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="temp-true-combo-false",
            supported_reasoning_levels=[ReasoningLevel.NONE],
            supports_temperature=True,
            supports_temperature_with_reasoning=False,
        )
        model.full_clean()  # must not raise -- temperature alone is fine without the combo flag

    def test_clean_allows_both_temperature_flags_false(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="both-temp-flags-false",
            supported_reasoning_levels=[ReasoningLevel.NONE],
            supports_temperature=False,
            supports_temperature_with_reasoning=False,
        )
        model.full_clean()  # must not raise -- the fail-closed default itself is valid

    def test_supports_reasoning_is_derived_from_levels(self):
        model = make_model(
            self.provider, supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.HIGH]
        )
        self.assertTrue(model.supports_reasoning)

    def test_supports_reasoning_false_when_only_none(self):
        model = make_model(self.provider, supported_reasoning_levels=[ReasoningLevel.NONE])
        self.assertFalse(model.supports_reasoning)

    def test_unique_provider_model_identifier(self):
        make_model(self.provider, model_identifier="dup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_model(self.provider, model_identifier="dup")

    def test_same_model_identifier_allowed_across_providers(self):
        other_provider = make_provider(name="Other")
        make_model(self.provider, model_identifier="shared-id")
        # must not raise
        make_model(other_provider, model_identifier="shared-id")

    def test_clean_rejects_reasoning_levels_missing_none(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="bad",
            supported_reasoning_levels=[ReasoningLevel.HIGH],
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_clean_rejects_invalid_reasoning_level_value(self):
        model = LLMModel(
            provider=self.provider,
            model_identifier="bad",
            supported_reasoning_levels=[ReasoningLevel.NONE, "NOT_A_REAL_LEVEL"],
        )
        with self.assertRaises(ValidationError):
            model.full_clean()


class StageModelAssignmentTests(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.model = make_model(
            self.provider,
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.MEDIUM],
            max_output_tokens=2000,
        )

    def test_create_with_valid_defaults(self):
        assignment = make_stage_assignment(
            self.model, stage="AJ_ANALYZE", default_reasoning_level=ReasoningLevel.MEDIUM
        )
        self.assertEqual(assignment.stage, "AJ_ANALYZE")

    def test_stage_is_unique(self):
        make_stage_assignment(self.model, stage="AJ_ANALYZE")
        other_model = make_model(self.provider, model_identifier="other")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_stage_assignment(other_model, stage="AJ_ANALYZE")

    def test_stage_choices_are_exactly_the_llm_capable_subset(self):
        choice_values = {value for value, _ in LLM_CAPABLE_STAGE_CHOICES}
        self.assertEqual(choice_values, {stage.value for stage in LLM_CAPABLE_STAGES})

    def test_deterministic_stages_are_not_valid_choices(self):
        choice_values = {value for value, _ in LLM_CAPABLE_STAGE_CHOICES}
        for stage in StageIdentifier:
            if stage not in LLM_CAPABLE_STAGES:
                self.assertNotIn(stage.value, choice_values)

    def test_clean_rejects_reasoning_level_not_supported_by_model(self):
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE", model=self.model, default_reasoning_level=ReasoningLevel.XHIGH
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_clean_rejects_budget_exceeding_model_capability(self):
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=self.model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_max_output_tokens=5000,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_clean_allows_budget_within_model_capability(self):
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=self.model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_max_output_tokens=1000,
        )
        assignment.full_clean()  # must not raise

    def test_deleting_model_with_assignment_is_protected(self):
        make_stage_assignment(self.model, stage="AJ_ANALYZE")
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.model.delete()

    def test_clean_allows_valid_temperature(self):
        temperature_model = make_model(
            self.provider, model_identifier="temp-ok", supports_temperature=True
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_temperature=0.7,
        )
        assignment.full_clean()  # must not raise

    def test_clean_rejects_temperature_when_model_does_not_support_it(self):
        temperature_model = make_model(
            self.provider, model_identifier="temp-off", supports_temperature=False
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_temperature=0.7,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_clean_rejects_temperature_combined_with_forbidden_reasoning(self):
        temperature_model = make_model(
            self.provider,
            model_identifier="temp-no-reasoning-combo",
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.MEDIUM],
            supports_temperature=True,
            supports_temperature_with_reasoning=False,
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.MEDIUM,
            default_temperature=0.7,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_clean_allows_temperature_combined_with_permitted_reasoning(self):
        temperature_model = make_model(
            self.provider,
            model_identifier="temp-reasoning-combo-ok",
            supported_reasoning_levels=[ReasoningLevel.NONE, ReasoningLevel.MEDIUM],
            supports_temperature=True,
            supports_temperature_with_reasoning=True,
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.MEDIUM,
            default_temperature=0.7,
        )
        assignment.full_clean()  # must not raise

    def test_clean_rejects_out_of_range_temperature(self):
        temperature_model = make_model(
            self.provider, model_identifier="temp-range", supports_temperature=True
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_temperature=2.5,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_clean_allows_null_temperature_regardless_of_capability(self):
        temperature_model = make_model(
            self.provider, model_identifier="temp-null", supports_temperature=False
        )
        assignment = StageModelAssignment(
            stage="AJ_ANALYZE",
            model=temperature_model,
            default_reasoning_level=ReasoningLevel.NONE,
            default_temperature=None,
        )
        assignment.full_clean()  # must not raise


class LLMCallLogTests(TestCase):
    def setUp(self):
        self.provider = make_fake_provider()
        self.model = make_model(self.provider, model_identifier="fake-1")

    def test_create_standalone_call_log_without_stage_run(self):
        log = LLMCallLog.objects.create(
            stage="AJ_ANALYZE", requested_provider=self.provider, requested_model=self.model
        )
        self.assertIsNone(log.stage_run)

    def test_no_prompt_or_response_body_field_exists(self):
        field_names = {f.name for f in LLMCallLog._meta.get_fields()}
        for forbidden in ("prompt", "response", "raw_response", "messages", "content", "body"):
            self.assertNotIn(forbidden, field_names)

    def test_deleting_provider_with_call_log_is_protected(self):
        LLMCallLog.objects.create(
            stage="AJ_ANALYZE", requested_provider=self.provider, requested_model=self.model
        )
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.provider.delete()

    def test_ordering_is_most_recent_first(self):
        first = LLMCallLog.objects.create(
            stage="AJ_ANALYZE", requested_provider=self.provider, requested_model=self.model
        )
        second = LLMCallLog.objects.create(
            stage="AC_ASSESS", requested_provider=self.provider, requested_model=self.model
        )
        logs = list(LLMCallLog.objects.all())
        self.assertEqual(logs, [second, first])
