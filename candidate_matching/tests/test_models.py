from __future__ import annotations

from django.test import TestCase

from ..models import FitAssessment, ImmutableFitAssessmentError, RequirementAssessment
from .factories import make_job_application_with_jra


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.application = make_job_application_with_jra()
        self.fit_assessment = FitAssessment.objects.create(
            job_application=self.application, version=1, based_on_jra=self.application.current_jra
        )

    def test_fit_assessment_cannot_be_edited_in_place(self):
        self.fit_assessment.retrieved_claim_ids = ["x"]
        with self.assertRaises(ImmutableFitAssessmentError):
            self.fit_assessment.save()

    def test_fit_assessment_cannot_be_deleted(self):
        with self.assertRaises(ImmutableFitAssessmentError):
            self.fit_assessment.delete()

    def test_requirement_assessment_cannot_be_edited_in_place(self):
        assessment = RequirementAssessment.objects.create(
            fit_assessment=self.fit_assessment, requirement_id="JR-001", disposition="GAP"
        )
        assessment.disposition = "MATCH"
        with self.assertRaises(ImmutableFitAssessmentError):
            assessment.save()

    def test_requirement_assessment_cannot_be_deleted(self):
        assessment = RequirementAssessment.objects.create(
            fit_assessment=self.fit_assessment, requirement_id="JR-001", disposition="GAP"
        )
        with self.assertRaises(ImmutableFitAssessmentError):
            assessment.delete()

    def test_duplicate_version_rejected(self):
        with self.assertRaises(Exception):
            FitAssessment.objects.create(
                job_application=self.application, version=1, based_on_jra=self.application.current_jra
            )
