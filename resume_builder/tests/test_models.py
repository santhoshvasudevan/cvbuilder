from __future__ import annotations

from django.test import TestCase

from ..models import ImmutableResumeDraftError, ResumeDraft, ResumeElement
from .factories import make_ready_for_gate2_application


class ResumeDraftImmutabilityTests(TestCase):
    def setUp(self):
        self.application, self.claim_id, self.engagement_id = make_ready_for_gate2_application()
        self.draft = ResumeDraft.objects.create(
            job_application=self.application,
            version=1,
            based_on_fit_assessment=self.application.current_fit_assessment,
            recommended_title="Engineer",
            rendered_markdown="# Engineer\n",
        )

    def test_cannot_edit_content_fields_in_place(self):
        self.draft.recommended_title = "Different Title"
        with self.assertRaises(ImmutableResumeDraftError):
            self.draft.save()

    def test_cannot_be_deleted(self):
        with self.assertRaises(ImmutableResumeDraftError):
            self.draft.delete()

    def test_confirm_sets_confirmed_at_exactly_once(self):
        self.assertIsNone(self.draft.confirmed_at)
        self.draft.confirm()
        self.assertIsNotNone(self.draft.confirmed_at)
        with self.assertRaises(ImmutableResumeDraftError):
            self.draft.confirm()

    def test_element_cannot_be_edited_or_deleted(self):
        element = ResumeElement.objects.create(
            resume_draft=self.draft, section=ResumeElement.Section.SUMMARY, order=1,
            text="Summary.", supporting_memory_claim_ids=[self.claim_id],
        )
        element.text = "Changed."
        with self.assertRaises(ImmutableResumeDraftError):
            element.save()
        with self.assertRaises(ImmutableResumeDraftError):
            element.delete()

    def test_duplicate_version_rejected(self):
        with self.assertRaises(Exception):
            ResumeDraft.objects.create(
                job_application=self.application, version=1,
                based_on_fit_assessment=self.application.current_fit_assessment,
                recommended_title="X", rendered_markdown="# X\n",
            )
