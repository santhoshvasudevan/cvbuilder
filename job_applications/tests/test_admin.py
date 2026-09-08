from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from job_applications.models import JobApplication, JobApplicationStageState, StageRun


class AdminRegistrationTests(TestCase):
    def test_job_application_models_are_registered(self):
        self.assertIn(JobApplication, admin.site._registry)
        self.assertIn(StageRun, admin.site._registry)
        self.assertIn(JobApplicationStageState, admin.site._registry)

    def test_pipeline_phase_is_readonly_in_admin(self):
        # Workflow-controlled field, not admin-editable -- matches the reuse-audit-confirmed
        # V1 pattern (docs/V2_REUSE_AUDIT.md) of readonly-for-orchestration-owned-fields.
        model_admin = admin.site._registry[JobApplication]
        self.assertIn("pipeline_phase", model_admin.readonly_fields)

    def test_application_outcome_is_editable_in_admin(self):
        model_admin = admin.site._registry[JobApplication]
        self.assertNotIn("application_outcome", model_admin.readonly_fields)


class AdminSiteReachableTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="test-pass-12345"
        )

    def test_admin_index_reachable_when_authenticated(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

    def test_admin_requires_authentication(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

    def test_job_application_changelist_reachable(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:job_applications_jobapplication_changelist"))
        self.assertEqual(response.status_code, 200)
