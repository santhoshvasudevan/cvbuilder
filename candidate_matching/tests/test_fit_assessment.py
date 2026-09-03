from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, CareerEngagement, ClaimEngagementMapping

from ..models import RequirementAssessment
from ..services.fit_assessment import AgentCandidateError, build_fit_assessment
from ..services.retrieve import NoActiveCandidateMemoryError
from .factories import (
    freeze_revision,
    make_engagement,
    make_job_application_with_jra,
    make_narrative_claim,
    make_revision,
    scripted_assessment,
    valid_assessment_response,
)


class BuildFitAssessmentTests(TestCase):
    def test_raises_without_an_active_candidate_memory(self):
        application = make_job_application_with_jra()
        with self.assertRaises(NoActiveCandidateMemoryError):
            build_fit_assessment(application)

    def test_raises_without_a_current_jra(self):
        from job_applications.models import JobApplication

        application = JobApplication.objects.create()
        with self.assertRaises(AgentCandidateError):
            build_fit_assessment(application)

    def test_every_relevant_requirement_gets_exactly_one_assessment(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        application = make_job_application_with_jra(
            requirements=[
                {"category": "MANDATORY", "text": "Own the payments service end to end."},
                {"category": "MANDATORY", "text": "5+ years of total experience in software"},
            ]
        )

        with scripted_assessment(valid_assessment_response()):
            fit_assessment = build_fit_assessment(application)

        assessments = fit_assessment.requirement_assessments.all()
        self.assertEqual(assessments.count(), 2)
        self.assertEqual({a.requirement_id for a in assessments}, {"JR-001", "JR-002"})

    def test_static_requirement_is_never_sent_to_the_llm(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        engagement = make_engagement(
            start_year=2010, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
        )
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "5+ years of total experience"}]
        )

        # An LLM response that has nothing for JR-001 would leave it uncovered if it were routed
        # to the LLM -- since it's static, the LLM is never called for it at all.
        with scripted_assessment({"requirement_assessments": []}):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.MATCH)
        self.assertEqual(assessment.supporting_engagement_ids, [engagement.engagement_id])

    def test_a_known_gap_is_never_softened_by_sanitization(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Experience with Kubernetes at scale"}]
        )
        response = valid_assessment_response(
            requirement_assessments=[
                {
                    "requirement_id": "JR-001",
                    "disposition": "GAP",
                    "explanation": "No evidence of Kubernetes experience.",
                    "gap_or_limitation": "No Kubernetes claims found.",
                    "supporting_memory_claim_ids": [],
                    "supporting_engagement_ids": [],
                }
            ]
        )
        with scripted_assessment(response):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.GAP)

    def test_match_with_a_fabricated_claim_id_is_downgraded_to_unknown(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Experience with Kubernetes at scale"}]
        )
        response = valid_assessment_response(
            requirement_assessments=[
                {
                    "requirement_id": "JR-001",
                    "disposition": "MATCH",
                    "explanation": "Strong Kubernetes background.",
                    "gap_or_limitation": "",
                    "supporting_memory_claim_ids": ["MC-9999-9999"],
                    "supporting_engagement_ids": [],
                }
            ]
        )
        with scripted_assessment(response):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.UNKNOWN)

    def test_records_exactly_which_claims_were_retrieved(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Some narrative requirement"}]
        )
        with scripted_assessment(valid_assessment_response()):
            fit_assessment = build_fit_assessment(application)

        self.assertEqual(fit_assessment.retrieved_claim_ids, [claim.claim_id])

    def test_creates_a_new_version_and_updates_the_current_pointer_on_rerun(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Some narrative requirement"}]
        )
        with scripted_assessment(valid_assessment_response()):
            first = build_fit_assessment(application)
            second = build_fit_assessment(application)

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        application.refresh_from_db()
        self.assertEqual(application.current_fit_assessment_id, second.pk)
