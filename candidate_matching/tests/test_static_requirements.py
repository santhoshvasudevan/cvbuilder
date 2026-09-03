from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CareerEngagement

from ..models import RequirementAssessment
from ..services.static_requirements import StaticRequirementKind, assess, classify
from .factories import make_engagement


class ClassifyTests(TestCase):
    def test_total_experience(self):
        self.assertEqual(
            classify("5+ years of total experience in backend development"),
            StaticRequirementKind.TOTAL_EXPERIENCE,
        )

    def test_tenure(self):
        self.assertEqual(
            classify("3+ years of experience in a similar role"), StaticRequirementKind.TENURE
        )

    def test_current_status(self):
        self.assertEqual(
            classify("Candidate must be currently employed"), StaticRequirementKind.CURRENT_PAST_STATUS
        )

    def test_location(self):
        self.assertEqual(classify("Must be based in Berlin, Germany"), StaticRequirementKind.LOCATION)

    def test_employer_relationship(self):
        self.assertEqual(
            classify("Direct employment only, no third-party candidates"),
            StaticRequirementKind.EMPLOYER_CLIENT_RELATIONSHIP,
        )

    def test_narrative_requirement_is_not_classified(self):
        self.assertIsNone(classify("Strong experience designing REST APIs"))


class AssessTotalExperienceTests(TestCase):
    def test_match_when_total_meets_requirement(self):
        engagements = [
            make_engagement(
                start_year=2015, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
            )
        ]
        result = assess(StaticRequirementKind.TOTAL_EXPERIENCE, "5 years of total experience", engagements)
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)
        self.assertEqual(result.supporting_engagement_ids, [engagements[0].engagement_id])

    def test_gap_when_total_falls_short(self):
        engagements = [
            make_engagement(
                start_year=2019, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
            )
        ]
        result = assess(StaticRequirementKind.TOTAL_EXPERIENCE, "5 years of total experience", engagements)
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.GAP)
        self.assertTrue(result.gap_or_limitation)

    def test_never_double_counts_overlapping_engagements(self):
        engagements = [
            make_engagement(
                engagement_id="", start_year=2015, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2018, end_month=1,
            ),
            make_engagement(
                engagement_id="", start_year=2016, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2019, end_month=1,
            ),
        ]
        result = assess(StaticRequirementKind.TOTAL_EXPERIENCE, "4 years total experience", engagements)
        # Non-overlapping span is 2015-01 .. 2019-01 == 48 months, not 36+36.
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)


class AssessTenureTests(TestCase):
    def test_match_when_a_single_engagement_meets_tenure(self):
        engagement = make_engagement(
            start_year=2010, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
        )
        result = assess(
            StaticRequirementKind.TENURE, "5+ years of experience in a similar role", [engagement]
        )
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)
        self.assertEqual(result.supporting_engagement_ids, [engagement.engagement_id])

    def test_partial_when_only_combined_tenure_meets_requirement(self):
        engagements = [
            make_engagement(
                engagement_id="", start_year=2015, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2017, end_month=1,
            ),
            make_engagement(
                engagement_id="", start_year=2017, start_month=1,
                end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
            ),
        ]
        result = assess(StaticRequirementKind.TENURE, "4+ years of experience in a similar role", engagements)
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.PARTIAL)

    def test_gap_when_no_engagement_reaches_tenure(self):
        engagement = make_engagement(
            start_year=2019, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
        )
        result = assess(
            StaticRequirementKind.TENURE, "5+ years of experience in a similar role", [engagement]
        )
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.GAP)


class AssessCurrentStatusTests(TestCase):
    def test_match_when_currently_employed_and_required(self):
        engagement = make_engagement(end_status=CareerEngagement.EndStatus.PRESENT)
        result = assess(StaticRequirementKind.CURRENT_PAST_STATUS, "Must be currently employed", [engagement])
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)

    def test_gap_when_not_currently_employed_but_required(self):
        engagement = make_engagement(
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1
        )
        result = assess(StaticRequirementKind.CURRENT_PAST_STATUS, "Must be currently employed", [engagement])
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.GAP)

    def test_match_when_no_longer_employed_and_that_is_required(self):
        engagement = make_engagement(
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1
        )
        result = assess(
            StaticRequirementKind.CURRENT_PAST_STATUS,
            "Candidate must no longer be employed there",
            [engagement],
        )
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)


class AssessLocationTests(TestCase):
    def test_match_on_exact_normalized_location(self):
        engagement = make_engagement(location="Berlin, Germany")
        result = assess(StaticRequirementKind.LOCATION, "Must be based in Berlin, Germany", [engagement])
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)
        self.assertEqual(result.supporting_engagement_ids, [engagement.engagement_id])

    def test_gap_when_no_location_matches(self):
        engagement = make_engagement(location="Cologne, Germany")
        result = assess(StaticRequirementKind.LOCATION, "Must be based in Berlin, Germany", [engagement])
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.GAP)


class AssessEmployerRelationshipTests(TestCase):
    def test_match_when_a_direct_employment_engagement_exists(self):
        engagement = make_engagement(client_organization="")
        result = assess(
            StaticRequirementKind.EMPLOYER_CLIENT_RELATIONSHIP,
            "Direct employment only, no agencies",
            [engagement],
        )
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.MATCH)

    def test_gap_when_only_consulting_engagements_exist(self):
        engagement = make_engagement(client_organization="Ford Motor Company")
        result = assess(
            StaticRequirementKind.EMPLOYER_CLIENT_RELATIONSHIP,
            "Direct employment only, no agencies",
            [engagement],
        )
        self.assertEqual(result.disposition, RequirementAssessment.Disposition.GAP)
