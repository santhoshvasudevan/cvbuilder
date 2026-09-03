"""The deterministic static-profile boundary (D-019): static fields cannot be supplied through
planned M6 LLM output; only APPROVED CareerEngagement records can be rendered; headers exactly
match stored values; dates/organisation names/titles are never translated on the fly; future M5/M6
contracts support both claim and engagement evidence. No LLM call anywhere in this file."""

from __future__ import annotations

import datetime

from django.test import TestCase
from pydantic import ValidationError

from ..models import CareerEngagement
from ..services.static_profile_boundary import (
    EngagementBullet,
    EngagementNarrativeOutput,
    RequirementEvidenceReference,
    UnknownOrUnapprovedEngagementError,
    assess_location_requirement_locally,
    assess_tenure_requirement_locally,
    render_engagement_header,
    resolve_approved_engagement,
)


def _make_engagement(**kwargs) -> CareerEngagement:
    defaults = dict(
        legal_employer="Ambigai Consultancy Services",
        client_organization="Ford Motor Company",
        approved_role_title="Senior Cloud Engineer",
        location="Cologne, Germany",
        start_year=2017,
        start_month=7,
        end_status=CareerEngagement.EndStatus.PRESENT,
        approval_status=CareerEngagement.ApprovalStatus.APPROVED,
    )
    defaults.update(kwargs)
    return CareerEngagement.objects.create(**defaults)


class StaticFieldsCannotBeSuppliedThroughLLMOutputTests(TestCase):
    """EngagementNarrativeOutput/EngagementBullet is the planned M6 LLM output schema -- it must
    have no field for employer/title/location/dates at all, and must reject any attempt to smuggle
    one in via extra="forbid"."""

    def test_engagement_bullet_has_no_static_fields(self):
        field_names = set(EngagementBullet.model_fields)
        self.assertEqual(field_names, {"text", "supporting_memory_claim_ids"})

    def test_engagement_narrative_output_has_no_static_fields(self):
        field_names = set(EngagementNarrativeOutput.model_fields)
        self.assertEqual(field_names, {"engagement_id", "bullets"})

    def test_supplying_an_employer_field_fails_validation(self):
        with self.assertRaises(ValidationError):
            EngagementNarrativeOutput.model_validate(
                {
                    "engagement_id": "CE-0001",
                    "employer": "Fabricated Employer Inc",
                    "bullets": [{"text": "Did something.", "supporting_memory_claim_ids": ["MC-1"]}],
                }
            )

    def test_supplying_a_start_date_field_on_a_bullet_fails_validation(self):
        with self.assertRaises(ValidationError):
            EngagementBullet.model_validate(
                {
                    "text": "Did something.",
                    "supporting_memory_claim_ids": ["MC-1"],
                    "start_date": "2020-01",
                }
            )

    def test_bullet_without_any_supporting_claim_fails_validation(self):
        with self.assertRaises(ValidationError):
            EngagementBullet.model_validate({"text": "Did something.", "supporting_memory_claim_ids": []})


class OnlyApprovedEngagementsCanBeRenderedTests(TestCase):
    def test_unknown_engagement_id_fails_validation(self):
        with self.assertRaises(UnknownOrUnapprovedEngagementError):
            resolve_approved_engagement("CE-9999")

    def test_draft_engagement_fails_validation(self):
        engagement = _make_engagement(approval_status=CareerEngagement.ApprovalStatus.DRAFT)
        with self.assertRaises(UnknownOrUnapprovedEngagementError):
            resolve_approved_engagement(engagement.engagement_id)

    def test_rejected_engagement_fails_validation(self):
        engagement = _make_engagement(approval_status=CareerEngagement.ApprovalStatus.REJECTED)
        with self.assertRaises(UnknownOrUnapprovedEngagementError):
            resolve_approved_engagement(engagement.engagement_id)

    def test_approved_engagement_resolves(self):
        engagement = _make_engagement()
        resolved = resolve_approved_engagement(engagement.engagement_id)
        self.assertEqual(resolved.pk, engagement.pk)

    def test_render_engagement_header_refuses_an_unapproved_id(self):
        engagement = _make_engagement(approval_status=CareerEngagement.ApprovalStatus.DRAFT)
        with self.assertRaises(UnknownOrUnapprovedEngagementError):
            render_engagement_header(engagement.engagement_id)


class HeaderExactlyMatchesStoredValuesTests(TestCase):
    def test_header_contains_exact_stored_title_organization_dates_and_location(self):
        engagement = _make_engagement(
            approved_role_title="Senior Cloud Engineer",
            client_organization="Ford Motor Company",
            location="Cologne, Germany",
            start_year=2017, start_month=7,
            end_status=CareerEngagement.EndStatus.PRESENT,
        )
        header = render_engagement_header(engagement.engagement_id)
        self.assertIn("Senior Cloud Engineer", header)
        self.assertIn("Ford Motor Company", header)
        self.assertIn("Jul 2017", header)
        self.assertIn("Present", header)
        self.assertIn("Cologne, Germany", header)

    def test_header_uses_exact_end_dates_when_known(self):
        engagement = _make_engagement(
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2017, end_month=10,
        )
        header = render_engagement_header(engagement.engagement_id)
        self.assertIn("Oct 2017", header)

    def test_header_reflects_the_selected_presentation_mode(self):
        engagement = _make_engagement(
            presentation_mode=CareerEngagement.PresentationMode.LEGAL_EMPLOYER_EXPLICIT,
        )
        header = render_engagement_header(engagement.engagement_id)
        self.assertIn("Ambigai Consultancy Services", header)
        self.assertNotIn("Ford Motor Company", header)


class DatesAndOrganisationNamesAreNeverTranslatedTests(TestCase):
    def test_organisation_name_is_verbatim_regardless_of_requested_language(self):
        engagement = _make_engagement(client_organization="Ford Motor Company")
        header_en = render_engagement_header(engagement.engagement_id, language="en")
        header_de = render_engagement_header(engagement.engagement_id, language="de")
        self.assertIn("Ford Motor Company", header_en)
        self.assertIn("Ford Motor Company", header_de)

    def test_title_falls_back_to_english_when_no_stored_localized_alternative(self):
        engagement = _make_engagement(approved_role_title="Senior Cloud Engineer", localized_titles={})
        header = render_engagement_header(engagement.engagement_id, language="de")
        self.assertIn("Senior Cloud Engineer", header)

    def test_title_uses_the_stored_localized_alternative_when_present(self):
        engagement = _make_engagement(
            approved_role_title="Senior Cloud Engineer",
            localized_titles={"de": "Senior Cloud-Ingenieur"},
        )
        header = render_engagement_header(engagement.engagement_id, language="de")
        self.assertIn("Senior Cloud-Ingenieur", header)
        self.assertNotIn("Senior Cloud Engineer", header)

    def test_dates_are_never_reformatted_by_locale(self):
        engagement = _make_engagement(start_year=2017, start_month=7)
        header_de = render_engagement_header(engagement.engagement_id, language="de")
        self.assertIn("Jul 2017", header_de)  # never "Jul." or a locale month name


class StaticRequirementAssessmentIsLocalTests(TestCase):
    def test_tenure_requirement_met(self):
        engagement = _make_engagement(
            start_year=2017, start_month=7, end_status=CareerEngagement.EndStatus.PRESENT
        )
        self.assertTrue(
            assess_tenure_requirement_locally(
                engagement, minimum_months=36, as_of=datetime.date(2026, 7, 1)
            )
        )

    def test_tenure_requirement_not_met(self):
        engagement = _make_engagement(
            start_year=2024, start_month=7, end_status=CareerEngagement.EndStatus.PRESENT
        )
        self.assertFalse(
            assess_tenure_requirement_locally(
                engagement, minimum_months=36, as_of=datetime.date(2026, 7, 1)
            )
        )

    def test_tenure_requirement_cannot_be_assessed_when_end_status_is_unknown(self):
        engagement = _make_engagement(end_status=CareerEngagement.EndStatus.UNKNOWN)
        self.assertFalse(assess_tenure_requirement_locally(engagement, minimum_months=1))

    def test_location_requirement_matches_normalized_stored_value(self):
        engagement = _make_engagement(location="Cologne, Germany")
        self.assertTrue(
            assess_location_requirement_locally(engagement, required_location="  cologne, germany ")
        )
        self.assertFalse(assess_location_requirement_locally(engagement, required_location="Berlin"))


class FutureContractsSupportBothEngagementAndClaimEvidenceTests(TestCase):
    def test_requirement_evidence_reference_accepts_both_kinds_of_evidence(self):
        evidence = RequirementEvidenceReference(
            supporting_memory_claim_ids=["MC-1", "MC-2"],
            supporting_engagement_ids=["CE-0001"],
        )
        self.assertEqual(evidence.supporting_memory_claim_ids, ["MC-1", "MC-2"])
        self.assertEqual(evidence.supporting_engagement_ids, ["CE-0001"])

    def test_requirement_evidence_reference_allows_engagement_only_evidence(self):
        evidence = RequirementEvidenceReference(supporting_engagement_ids=["CE-0001"])
        self.assertEqual(evidence.supporting_memory_claim_ids, [])

    def test_requirement_evidence_reference_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            RequirementEvidenceReference.model_validate(
                {"supporting_memory_claim_ids": [], "supporting_engagement_ids": [], "extra_field": 1}
            )
