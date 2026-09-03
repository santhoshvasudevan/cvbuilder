"""Deterministic static-profile boundary (D-019): CareerEngagement model behavior and
cross-engagement calculations. No LLM call anywhere in this file."""

from __future__ import annotations

import datetime

from django.test import TestCase

from ..models import CareerEngagement
from ..services.career_engagement import total_non_overlapping_experience_months


def _make_engagement(**kwargs) -> CareerEngagement:
    defaults = dict(
        legal_employer="Ambigai Consultancy Services",
        client_organization="Ford Motor Company",
        approved_role_title="Senior Cloud Engineer",
        location="Cologne, Germany",
        start_year=2017,
        start_month=7,
        end_status=CareerEngagement.EndStatus.PRESENT,
    )
    defaults.update(kwargs)
    return CareerEngagement.objects.create(**defaults)


class EngagementIdAssignmentTests(TestCase):
    def test_engagement_id_is_assigned_automatically_and_is_stable(self):
        first = _make_engagement()
        second = _make_engagement(client_organization="Continental Automotive")
        self.assertTrue(first.engagement_id)
        self.assertTrue(second.engagement_id)
        self.assertNotEqual(first.engagement_id, second.engagement_id)
        original_id = first.engagement_id
        first.approved_role_title = "Updated title"
        first.save()
        first.refresh_from_db()
        self.assertEqual(first.engagement_id, original_id)  # unchanged across a later save


class DefaultDisplayedOrganizationTests(TestCase):
    def test_defaults_to_client_organization_when_present(self):
        engagement = _make_engagement(client_organization="Ford Motor Company")
        self.assertEqual(engagement.default_displayed_organization, "Ford Motor Company")

    def test_defaults_to_legal_employer_when_no_client(self):
        engagement = _make_engagement(client_organization="", legal_employer="Direct Employer Inc")
        self.assertEqual(engagement.default_displayed_organization, "Direct Employer Inc")

    def test_explicit_value_is_never_overwritten(self):
        engagement = _make_engagement(
            client_organization="Ford Motor Company",
            default_displayed_organization="Custom Display Name",
        )
        self.assertEqual(engagement.default_displayed_organization, "Custom Display Name")


class DisplayedOrganizationPresentationModeTests(TestCase):
    """Presentation-mode selection uses stored alternatives only -- never a computed/rewritten
    third value."""

    def test_client_centric_uses_default_displayed_organization(self):
        engagement = _make_engagement(
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
            presentation_mode=CareerEngagement.PresentationMode.CLIENT_CENTRIC,
        )
        self.assertEqual(engagement.displayed_organization, "Ford Motor Company")

    def test_legal_employer_explicit_uses_legal_employer(self):
        engagement = _make_engagement(
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
            presentation_mode=CareerEngagement.PresentationMode.LEGAL_EMPLOYER_EXPLICIT,
        )
        self.assertEqual(engagement.displayed_organization, "Ambigai Consultancy Services")

    def test_combined_uses_both_stored_values_verbatim(self):
        engagement = _make_engagement(
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
            presentation_mode=CareerEngagement.PresentationMode.COMBINED,
        )
        self.assertEqual(
            engagement.displayed_organization, "Ford Motor Company (via Ambigai Consultancy Services)"
        )

    def test_combined_without_a_client_falls_back_to_default(self):
        engagement = _make_engagement(
            legal_employer="Direct Employer Inc",
            client_organization="",
            presentation_mode=CareerEngagement.PresentationMode.COMBINED,
        )
        self.assertEqual(engagement.displayed_organization, "Direct Employer Inc")


class TitleForLanguageTests(TestCase):
    """Dates and organisation names -- and titles -- are never translated on the fly; only a
    stored operator-approved alternative is ever used."""

    def test_falls_back_to_approved_role_title_when_no_localized_entry(self):
        engagement = _make_engagement(approved_role_title="Senior Cloud Engineer", localized_titles={})
        self.assertEqual(engagement.title_for_language("de"), "Senior Cloud Engineer")

    def test_uses_the_stored_localized_title_when_present(self):
        engagement = _make_engagement(
            approved_role_title="Senior Cloud Engineer",
            localized_titles={"de": "Senior Cloud-Ingenieur"},
        )
        self.assertEqual(engagement.title_for_language("de"), "Senior Cloud-Ingenieur")
        self.assertEqual(engagement.title_for_language("en"), "Senior Cloud Engineer")


class IsCurrentTests(TestCase):
    def test_present_end_status_is_current(self):
        engagement = _make_engagement(end_status=CareerEngagement.EndStatus.PRESENT)
        self.assertTrue(engagement.is_current)

    def test_known_end_status_is_not_current(self):
        engagement = _make_engagement(
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1
        )
        self.assertFalse(engagement.is_current)

    def test_unknown_end_status_is_not_current(self):
        engagement = _make_engagement(end_status=CareerEngagement.EndStatus.UNKNOWN)
        self.assertFalse(engagement.is_current)


class DurationMonthsTests(TestCase):
    def test_known_full_precision_dates(self):
        engagement = _make_engagement(
            start_year=2015, start_month=7,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2017, end_month=7,
        )
        self.assertEqual(engagement.duration_months(), 24)

    def test_present_uses_as_of_reference_date(self):
        engagement = _make_engagement(
            start_year=2017, start_month=7, end_status=CareerEngagement.EndStatus.PRESENT
        )
        self.assertEqual(engagement.duration_months(as_of=datetime.date(2026, 7, 1)), 108)

    def test_unknown_end_status_returns_none_never_zero(self):
        engagement = _make_engagement(end_status=CareerEngagement.EndStatus.UNKNOWN)
        self.assertIsNone(engagement.duration_months())

    def test_missing_month_uses_disclosed_january_december_convention(self):
        engagement = _make_engagement(
            start_year=2015, start_month=None,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2017, end_month=None,
        )
        # Jan 2015 -> Dec 2017 = 35 months.
        self.assertEqual(engagement.duration_months(), 35)


class TotalNonOverlappingExperienceTests(TestCase):
    def test_non_overlapping_engagements_sum_additively(self):
        first = _make_engagement(
            start_year=2012, start_month=8,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2015, end_month=6,
        )
        second = _make_engagement(
            start_year=2017, start_month=7, end_status=CareerEngagement.EndStatus.PRESENT
        )
        result = total_non_overlapping_experience_months(
            [first, second], as_of=datetime.date(2026, 9, 1)
        )
        # first: 2012-08 -> 2015-06 = 34 months. second: 2017-07 -> 2026-09 = 110 months.
        self.assertEqual(result.total_months, 34 + 110)
        self.assertCountEqual(result.included_engagement_ids, [first.engagement_id, second.engagement_id])
        self.assertEqual(result.excluded_engagement_ids, [])

    def test_overlapping_engagements_are_not_double_counted(self):
        first = _make_engagement(
            start_year=2015, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2018, end_month=1,
        )
        second = _make_engagement(
            start_year=2017, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2019, end_month=1,
        )
        result = total_non_overlapping_experience_months([first, second])
        # Merged interval: 2015-01 -> 2019-01 = 48 months, not 36+24=60.
        self.assertEqual(result.total_months, 48)

    def test_unknown_end_status_engagements_are_excluded_and_reported(self):
        determinable = _make_engagement(
            start_year=2015, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2017, end_month=1,
        )
        indeterminable = _make_engagement(end_status=CareerEngagement.EndStatus.UNKNOWN)
        result = total_non_overlapping_experience_months([determinable, indeterminable])
        self.assertEqual(result.total_months, 24)
        self.assertEqual(result.included_engagement_ids, [determinable.engagement_id])
        self.assertEqual(result.excluded_engagement_ids, [indeterminable.engagement_id])
