from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from job_applications.models import JobApplication
from job_intake.models import JobRequirementAnalysis

from ..models import ResumeDraft, ResumeElement
from ..services.delivery import DraftNotDownloadableError, build_filename, get_final_markdown

MARKDOWN = (
    "# Solutions Architect\n\n"
    "## Professional Summary\n\n"
    "- Led cloud transformation initiatives across multiple engagements.\n\n"
    "## Professional Experience\n\n"
    "### Solutions Architect, Example Corp (2019 - Present)\n"
    "- Delivered a connected-vehicle platform.\n\n"
    "## Key Skills\n\n"
    "Python, Kubernetes, AWS\n"
)


def _make_jra(application: JobApplication, version: int = 1, role_title: str = "Solutions Architect"):
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text", extracted_text="posting text",
        extracted_text_sha256="0" * 64, posting_language="en",
        employer="Example Corp", role_title=role_title,
    )


def _ready_application_with_draft() -> tuple[JobApplication, ResumeDraft]:
    application = JobApplication.objects.create()
    jra = _make_jra(application)
    application.advance_to_analysis(jra=jra)
    fa = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
    application.record_fit_assessment(fa)
    application.approve_gate1()
    draft = ResumeDraft.objects.create(
        job_application=application, version=1, based_on_fit_assessment=fa,
        recommended_title="Solutions Architect", rendered_markdown=MARKDOWN,
    )
    ResumeElement.objects.create(
        resume_draft=draft, section=ResumeElement.Section.SUMMARY, order=1,
        text="Led cloud transformation initiatives across multiple engagements.",
        supporting_memory_claim_ids=["MC-1-0001"],
    )
    application.record_resume_draft(draft)
    application.approve_gate2()
    application.refresh_from_db()
    draft.refresh_from_db()
    return application, draft


class BuildFilenameTests(TestCase):
    def test_deterministic_sanitized_filename(self):
        application, draft = _ready_application_with_draft()
        name = build_filename(application, draft)
        self.assertEqual(name, f"resume-app{application.pk}-v1-solutions-architect.md")
        # Determinism: calling again yields byte-identical output.
        self.assertEqual(name, build_filename(application, draft))

    def test_filename_falls_back_safely_when_role_title_blank(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application, role_title="")
        application.advance_to_analysis(jra=jra)
        fa = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
        application.record_fit_assessment(fa)
        draft = ResumeDraft.objects.create(
            job_application=application, version=1, based_on_fit_assessment=fa,
            recommended_title="X", rendered_markdown="# X\n",
        )
        name = build_filename(application, draft)
        self.assertEqual(name, f"resume-app{application.pk}-v1-resume.md")


class GetFinalMarkdownTests(TestCase):
    def test_returns_content_for_ready_confirmed_current_draft(self):
        application, draft = _ready_application_with_draft()
        result = get_final_markdown(application)
        self.assertEqual(result.content, MARKDOWN)
        self.assertEqual(result.draft.pk, draft.pk)

    def test_no_evidence_ids_or_planning_metadata_leak_into_markdown(self):
        application, draft = _ready_application_with_draft()
        result = get_final_markdown(application)
        self.assertNotIn("MC-1-0001", result.content)
        self.assertNotIn("supporting_memory_claim_ids", result.content)
        self.assertNotIn("matched_job_requirement_ids", result.content)

    def test_raises_when_no_draft(self):
        application = JobApplication.objects.create()
        with self.assertRaises(DraftNotDownloadableError):
            get_final_markdown(application)

    def test_raises_when_not_ready(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
        application.record_fit_assessment(fa)
        application.approve_gate1()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1, based_on_fit_assessment=fa,
            recommended_title="X", rendered_markdown="# X\n",
        )
        application.record_resume_draft(draft)
        # Not yet approved at Gate 2 -- phase is still PREPARATION.
        with self.assertRaises(DraftNotDownloadableError):
            get_final_markdown(application)

    def test_raises_when_draft_stale_relative_to_fit_assessment(self):
        application, draft = _ready_application_with_draft()
        new_fa = FitAssessment.objects.create(
            job_application=application, version=2, based_on_jra=application.current_jra
        )
        application.record_fit_assessment(new_fa)
        with self.assertRaises(DraftNotDownloadableError):
            get_final_markdown(application)

    def test_raises_when_chain_transitively_stale(self):
        application, draft = _ready_application_with_draft()
        new_jra = _make_jra(application, version=2)
        application.record_jra(new_jra)
        with self.assertRaises(DraftNotDownloadableError):
            get_final_markdown(application)


class DownloadViewTests(TestCase):
    def test_download_returns_markdown_with_expected_headers(self):
        application, draft = _ready_application_with_draft()
        response = self.client.get(f"/resume/{application.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".md", response["Content-Disposition"])
        self.assertEqual(response.content.decode("utf-8"), MARKDOWN)

    def test_repeated_downloads_are_byte_identical(self):
        application, draft = _ready_application_with_draft()
        first = self.client.get(f"/resume/{application.pk}/download/").content
        second = self.client.get(f"/resume/{application.pk}/download/").content
        self.assertEqual(first, second)

    def test_download_does_not_mutate_resume_draft(self):
        application, draft = _ready_application_with_draft()
        before = (draft.rendered_markdown, draft.confirmed_at, draft.created_at)
        self.client.get(f"/resume/{application.pk}/download/")
        draft.refresh_from_db()
        after = (draft.rendered_markdown, draft.confirmed_at, draft.created_at)
        self.assertEqual(before, after)

    def test_download_blocked_when_not_confirmed_current(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
        application.record_fit_assessment(fa)
        application.approve_gate1()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1, based_on_fit_assessment=fa,
            recommended_title="X", rendered_markdown="# X\n",
        )
        application.record_resume_draft(draft)
        response = self.client.get(f"/resume/{application.pk}/download/")
        self.assertEqual(response.status_code, 409)

    def test_download_blocked_when_chain_stale(self):
        application, draft = _ready_application_with_draft()
        new_jra = _make_jra(application, version=2)
        application.record_jra(new_jra)
        response = self.client.get(f"/resume/{application.pk}/download/")
        self.assertEqual(response.status_code, 409)


class PreviewViewTests(TestCase):
    def test_preview_shows_markdown_and_copy_source(self):
        application, draft = _ready_application_with_draft()
        response = self.client.get(f"/resume/{application.pk}/preview/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solutions Architect")
        self.assertContains(response, "resume-markdown-source")
        self.assertNotContains(response, "MC-1-0001")

    def test_preview_shows_error_when_not_downloadable(self):
        application = JobApplication.objects.create()
        response = self.client.get(f"/resume/{application.pk}/preview/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not available")
