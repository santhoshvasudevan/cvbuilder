from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from job_applications.models import JobApplication

from ..models import ImmutableJobRequirementAnalysisError, JobRequirement, JobRequirementAnalysis


def _make_jra(application: JobApplication, version: int = 1) -> JobRequirementAnalysis:
    return JobRequirementAnalysis.objects.create(
        job_application=application,
        version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text",
        extracted_text="posting text",
        extracted_text_sha256="0" * 64,
        posting_language="en",
    )


class JraVersionUniquenessTests(TestCase):
    def test_duplicate_version_for_same_application_rejected(self):
        application = JobApplication.objects.create()
        _make_jra(application, version=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _make_jra(application, version=1)

    def test_same_version_number_allowed_across_different_applications(self):
        app_a = JobApplication.objects.create()
        app_b = JobApplication.objects.create()
        _make_jra(app_a, version=1)
        _make_jra(app_b, version=1)  # must not raise
        self.assertEqual(JobRequirementAnalysis.objects.count(), 2)


class JraAppendOnlyTests(TestCase):
    def test_saving_an_existing_jra_instance_raises(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        jra.employer = "Changed Corp"
        with self.assertRaises(ImmutableJobRequirementAnalysisError):
            jra.save()

    def test_deleting_a_jra_instance_directly_raises(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        with self.assertRaises(ImmutableJobRequirementAnalysisError):
            jra.delete()

    def test_historical_version_unchanged_after_a_new_version_is_created(self):
        application = JobApplication.objects.create()
        jra1 = _make_jra(application, version=1)
        _make_jra(application, version=2)
        jra1.refresh_from_db()
        self.assertEqual(jra1.version, 1)
        self.assertEqual(jra1.extracted_text, "posting text")


class JobRequirementIdAndOrderingTests(TestCase):
    def test_stable_ids_and_deterministic_ordering(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        for order, category in enumerate(
            ["MANDATORY", "PREFERRED", "RESPONSIBILITY"], start=1
        ):
            JobRequirement.objects.create(
                job_requirement_analysis=jra, requirement_id=f"JR-{order:03d}", order=order,
                category=category, text=f"requirement {order}",
            )
        ids = list(jra.requirements.values_list("requirement_id", flat=True))
        self.assertEqual(ids, ["JR-001", "JR-002", "JR-003"])

    def test_duplicate_requirement_id_within_same_jra_rejected(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        JobRequirement.objects.create(
            job_requirement_analysis=jra, requirement_id="JR-001", order=1,
            category="MANDATORY", text="a",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobRequirement.objects.create(
                job_requirement_analysis=jra, requirement_id="JR-001", order=2,
                category="PREFERRED", text="b",
            )

    def test_duplicate_order_within_same_jra_rejected(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        JobRequirement.objects.create(
            job_requirement_analysis=jra, requirement_id="JR-001", order=1,
            category="MANDATORY", text="a",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobRequirement.objects.create(
                job_requirement_analysis=jra, requirement_id="JR-002", order=1,
                category="PREFERRED", text="b",
            )

    def test_same_requirement_id_allowed_across_different_jra_versions(self):
        application = JobApplication.objects.create()
        jra1 = _make_jra(application, version=1)
        jra2 = _make_jra(application, version=2)
        JobRequirement.objects.create(
            job_requirement_analysis=jra1, requirement_id="JR-001", order=1,
            category="MANDATORY", text="a",
        )
        JobRequirement.objects.create(
            job_requirement_analysis=jra2, requirement_id="JR-001", order=1,
            category="MANDATORY", text="a-revised",
        )  # must not raise

    def test_editing_an_existing_requirement_instance_raises(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        requirement = JobRequirement.objects.create(
            job_requirement_analysis=jra, requirement_id="JR-001", order=1,
            category="MANDATORY", text="a",
        )
        requirement.text = "changed"
        with self.assertRaises(ImmutableJobRequirementAnalysisError):
            requirement.save()
