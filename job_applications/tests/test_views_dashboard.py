from __future__ import annotations

from django.test import Client, TestCase

from candidate_matching.models import FitAssessment
from job_intake.models import JobRequirementAnalysis
from llm_provider.models import LLMCallLog
from resume_builder.models import ResumeDraft

from ..models import JobApplication


def _make_jra(
    application: JobApplication,
    version: int = 1,
    employer: str = "Acme",
    role_title: str = "Solutions Architect",
):
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text", extracted_text="posting text",
        extracted_text_sha256="0" * 64, posting_language="en",
        employer=employer, role_title=role_title,
    )


def _ready_application() -> JobApplication:
    """Builds, entirely from deterministic fixtures (never the operational database), a
    JobApplication in the exact same shape as the real JobApplication 9's accepted state: READY,
    Gate 1 and Gate 2 both approved, a confirmed, current ResumeDraft (Phase G #19)."""
    application = JobApplication.objects.create()
    jra = _make_jra(application)
    application.advance_to_analysis(jra=jra)
    fa = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
    application.record_fit_assessment(fa)
    application.approve_gate1()
    draft = ResumeDraft.objects.create(
        job_application=application, version=1, based_on_fit_assessment=fa,
        recommended_title="Generative AI Solutions Architect",
        rendered_markdown=(
            "# Generative AI Solutions Architect\n\n"
            "## Professional Summary\n\n"
            "- Summary line.\n"
        ),
    )
    application.record_resume_draft(draft)
    application.approve_gate2()
    application.refresh_from_db()
    return application


class DashboardViewTests(TestCase):
    def test_dashboard_lists_applications(self):
        app = _ready_application()
        response = self.client.get("/applications/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/applications/{app.pk}/")
        self.assertContains(response, "Ready")

    def test_dashboard_get_only(self):
        response = self.client.post("/applications/")
        self.assertEqual(response.status_code, 405)

    def test_dashboard_shows_summary_counts(self):
        _ready_application()
        response = self.client.get("/applications/")
        self.assertContains(response, "Total applications")
        self.assertContains(response, "Final resume ready")

    def test_root_serves_the_real_dashboard_not_a_bare_redirect(self):
        """M7 UX follow-up: `/` must render the dashboard content itself (status 200), not merely
        302-redirect to it, per the Product Owner's rejection of a bare-redirect homepage."""
        app = _ready_application()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/applications/{app.pk}/")
        self.assertContains(response, "New application")

    def test_empty_state_when_no_applications(self):
        response = self.client.get("/applications/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No job applications yet")
        self.assertContains(response, "/job-intake/")


class DetailViewTests(TestCase):
    def test_detail_shows_next_action_and_gate_status(self):
        app = _ready_application()
        response = self.client.get(f"/applications/{app.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "approved")

    def test_detail_404_for_unknown_application(self):
        response = self.client.get("/applications/999999/")
        self.assertEqual(response.status_code, 404)

    def test_detail_links_to_every_persisted_artifact(self):
        """M7 UX follow-up: the detail page must expose direct, readable links to each persisted
        artifact (JRA, Gate 1/fit assessment, Gate 2/resume draft, final resume preview) rather
        than only admin pages or raw JSON."""
        app = _ready_application()
        response = self.client.get(f"/applications/{app.pk}/")
        self.assertContains(response, f"/job-intake/{app.pk}/")
        self.assertContains(response, f"/reviews/gate1/{app.pk}/")
        self.assertContains(response, f"/reviews/gate2/{app.pk}/")
        self.assertContains(response, f"/resume/{app.pk}/preview/")

    def test_detail_explains_unavailable_artifacts_before_jra_exists(self):
        application = JobApplication.objects.create()
        response = self.client.get(f"/applications/{application.pk}/")
        self.assertContains(response, "Not started yet")
        self.assertContains(response, "Unavailable until")


class OutcomeActionCsrfTests(TestCase):
    def setUp(self):
        self.csrf_client = Client(enforce_csrf_checks=True)

    def test_post_without_csrf_token_rejected(self):
        app = _ready_application()
        response = self.csrf_client.post(f"/applications/{app.pk}/", {"application_outcome": "APPLIED"})
        self.assertEqual(response.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)

    def test_post_with_csrf_token_sets_outcome(self):
        app = _ready_application()
        self.csrf_client.get(f"/applications/{app.pk}/")  # obtain a CSRF cookie
        token = self.csrf_client.cookies["csrftoken"].value
        response = self.csrf_client.post(
            f"/applications/{app.pk}/",
            {"application_outcome": "APPLIED", "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.application_outcome, JobApplication.ApplicationOutcome.APPLIED)

    def test_invalid_outcome_value_rejected_with_no_state_change(self):
        app = _ready_application()
        response = self.client.post(f"/applications/{app.pk}/", {"application_outcome": "BOGUS"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a recognized application outcome")
        app.refresh_from_db()
        self.assertEqual(app.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)

    def test_outcome_change_before_ready_is_rejected(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        response = self.client.post(
            f"/applications/{application.pk}/", {"application_outcome": "APPLIED"}
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)


class NoProviderCallTests(TestCase):
    """No provider call from dashboard/render/download tests (Phase G #18)."""

    def test_dashboard_and_detail_and_download_make_zero_llm_calls(self):
        app = _ready_application()
        before = LLMCallLog.objects.count()
        self.client.get("/applications/")
        self.client.get(f"/applications/{app.pk}/")
        self.client.get(f"/resume/{app.pk}/preview/")
        self.client.get(f"/resume/{app.pk}/download/")
        after = LLMCallLog.objects.count()
        self.assertEqual(before, after)
