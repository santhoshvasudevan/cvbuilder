from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from ..models import LLMModel, LLMProvider, StageModelAssignment
from .factories import make_model, make_provider, make_stage_assignment


class LLMProviderModelTests(TestCase):
    def test_credential_value_is_never_a_field(self):
        provider = make_provider(credential_env_var="OPENAI_API_KEY")
        field_names = {f.name for f in LLMProvider._meta.fields}
        self.assertIn("credential_env_var", field_names)
        self.assertNotIn("credential", field_names)
        self.assertNotIn("api_key", field_names)
        self.assertEqual(provider.credential_env_var, "OPENAI_API_KEY")

    def test_provider_name_unique(self):
        make_provider(name="Dup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_provider(name="Dup")


class LLMModelTests(TestCase):
    def test_model_unique_per_provider(self):
        provider = make_provider()
        make_model(provider=provider, model_id="same-id")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_model(provider=provider, model_id="same-id")

    def test_same_model_id_allowed_under_different_providers(self):
        make_model(provider=make_provider(name="P1"), model_id="shared-id")
        make_model(provider=make_provider(name="P2"), model_id="shared-id")
        self.assertEqual(LLMModel.objects.filter(model_id="shared-id").count(), 2)


class StageModelAssignmentTests(TestCase):
    def test_one_assignment_per_stage(self):
        make_stage_assignment(stage=StageModelAssignment.Stage.MEMORY_BUILD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_stage_assignment(stage=StageModelAssignment.Stage.MEMORY_BUILD)

    def test_reassigning_stage_is_an_update_not_a_new_row(self):
        model_a = make_model(model_id="a")
        model_b = make_model(provider=make_provider(name="Other"), model_id="b")
        assignment = make_stage_assignment(stage=StageModelAssignment.Stage.AJ_ANALYZE, model=model_a)

        assignment.model = model_b
        assignment.save()

        self.assertEqual(StageModelAssignment.objects.filter(stage="AJ_ANALYZE").count(), 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.model, model_b)


class AdminRegistryReachabilityTests(TestCase):
    """'Admin screenshot/manual check of registry CRUD' (M2 completion evidence) --
    automated as: every registered model's admin changelist renders for a staff user."""

    def setUp(self):
        User = get_user_model()
        self.staff_user = User.objects.create_superuser(
            username="admin_test", email="admin_test@example.com", password="not-a-real-password"
        )
        self.client.force_login(self.staff_user)

    def test_all_registry_admin_changelists_reachable(self):
        for model_name in ("llmprovider", "llmmodel", "stagemodelassignment", "llmcalllog"):
            url = reverse(f"admin:llm_provider_{model_name}_changelist")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"admin changelist for {model_name} did not render")

    def test_provider_and_model_creatable_through_admin(self):
        add_provider_url = reverse("admin:llm_provider_llmprovider_add")
        response = self.client.post(
            add_provider_url,
            data={
                "name": "Admin-created provider",
                "provider_type": LLMProvider.ProviderType.OPENAI,
                "base_url": "",
                "credential_env_var": "OPENAI_API_KEY",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LLMProvider.objects.filter(name="Admin-created provider").exists())
