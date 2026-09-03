from __future__ import annotations

import socket
from unittest import mock

import requests
from django.test import Client, TestCase
from django.urls import reverse

from job_applications.models import JobApplication

from .factories import scripted_analysis, valid_analysis_response


class IntakeViewGetTests(TestCase):
    def test_get_renders_intake_form(self):
        response = self.client.get(reverse("job_intake:intake"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analyze a job posting")


class IntakeViewPastedTextPostTests(TestCase):
    def test_successful_pasted_submission_redirects_to_detail(self):
        with scripted_analysis(valid_analysis_response()):
            response = self.client.post(
                reverse("job_intake:intake"), {"url": "", "pasted_text": "Some job posting text."}
            )
        application = JobApplication.objects.get()
        self.assertRedirects(
            response, reverse("job_intake:analysis_detail", kwargs={"application_id": application.pk})
        )

    def test_detail_page_shows_requirements_with_stable_ids(self):
        with scripted_analysis(valid_analysis_response()):
            self.client.post(
                reverse("job_intake:intake"), {"url": "", "pasted_text": "Some job posting text."}
            )
        application = JobApplication.objects.get()
        response = self.client.get(
            reverse("job_intake:analysis_detail", kwargs={"application_id": application.pk})
        )
        self.assertContains(response, "JR-001")
        self.assertContains(response, "Globex Corporation")

    def test_html_special_characters_in_posting_output_are_escaped(self):
        response_body = valid_analysis_response(employer="<script>alert(1)</script>")
        with scripted_analysis(response_body):
            self.client.post(
                reverse("job_intake:intake"), {"url": "", "pasted_text": "Some job posting text."}
            )
        application = JobApplication.objects.get()
        response = self.client.get(
            reverse("job_intake:analysis_detail", kwargs={"application_id": application.pk})
        )
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_both_url_and_pasted_text_shows_validation_error_and_makes_no_llm_call(self):
        response = self.client.post(
            reverse("job_intake:intake"),
            {"url": "https://example.com/job", "pasted_text": "Some text."},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not both")
        self.assertEqual(JobApplication.objects.count(), 0)


class IntakeViewUrlFetchFailureTests(TestCase):
    @mock.patch("job_intake.services.fetch.requests.get")
    @mock.patch("socket.getaddrinfo")
    def test_fetch_failure_shows_pasted_fallback_and_preserves_url_with_zero_llm_calls(
        self, mock_dns, mock_get
    ):
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        mock_get.side_effect = requests.ConnectionError()
        response = self.client.post(
            reverse("job_intake:intake"), {"url": "https://example.com/job", "pasted_text": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "example.com/job")
        self.assertContains(response, "Paste the posting text")
        self.assertEqual(JobApplication.objects.count(), 0)

    @mock.patch("job_intake.services.fetch.requests.get")
    @mock.patch("socket.getaddrinfo")
    def test_unsafe_url_message_is_sanitized(self, mock_dns, mock_get):
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        response = self.client.post(
            reverse("job_intake:intake"), {"url": "http://internal.example/job", "pasted_text": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "private or internal network")


class CsrfProtectionTests(TestCase):
    def test_post_without_csrf_token_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("job_intake:intake"), {"url": "", "pasted_text": "Some job posting text."}
        )
        self.assertEqual(response.status_code, 403)


class NoLaterMilestoneControlsTests(TestCase):
    def test_detail_page_shows_no_gate1_or_matching_controls(self):
        with scripted_analysis(valid_analysis_response()):
            self.client.post(
                reverse("job_intake:intake"), {"url": "", "pasted_text": "Some job posting text."}
            )
        application = JobApplication.objects.get()
        response = self.client.get(
            reverse("job_intake:analysis_detail", kwargs={"application_id": application.pk})
        )
        for forbidden in ("Fit Assessment", "Approve", "Gate 1", "Resume Draft"):
            self.assertNotContains(response, forbidden)
