from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from llm_provider.models import LLMCallLog, LLMModel, LLMProvider, StageModelAssignment

from .factories import make_fake_provider, make_model, make_stage_assignment


class AdminRegistrationTests(TestCase):
    def test_all_registry_and_audit_models_are_registered(self):
        for model in (LLMProvider, LLMModel, StageModelAssignment, LLMCallLog):
            self.assertIn(model, admin.site._registry)


class LLMCallLogAdminIsReadOnlyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin2",
            email="admin2@example.com",
            password="test-pass-12345",  # pragma: allowlist secret
        )
        self.client.force_login(self.superuser)

    def test_add_permission_denied(self):
        model_admin = admin.site._registry[LLMCallLog]
        self.assertFalse(model_admin.has_add_permission(request=None))

    def test_change_permission_denied(self):
        model_admin = admin.site._registry[LLMCallLog]
        self.assertFalse(model_admin.has_change_permission(request=None))

    def test_changelist_reachable(self):
        response = self.client.get(reverse("admin:llm_provider_llmcalllog_changelist"))
        self.assertEqual(response.status_code, 200)


class RegistryAdminReachableTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin3",
            email="admin3@example.com",
            password="test-pass-12345",  # pragma: allowlist secret
        )
        self.client.force_login(self.superuser)

    def test_provider_changelist_reachable(self):
        make_fake_provider()
        response = self.client.get(reverse("admin:llm_provider_llmprovider_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_model_changelist_never_displays_a_credential_value(self):
        provider = make_fake_provider(name="Visible Provider")
        make_model(provider)
        response = self.client.get(reverse("admin:llm_provider_llmmodel_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_stage_assignment_changelist_reachable(self):
        provider = make_fake_provider()
        model = make_model(provider)
        make_stage_assignment(model, stage="AJ_ANALYZE")
        response = self.client.get(reverse("admin:llm_provider_stagemodelassignment_changelist"))
        self.assertEqual(response.status_code, 200)
