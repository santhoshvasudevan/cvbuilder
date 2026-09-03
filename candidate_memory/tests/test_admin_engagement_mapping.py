"""ClaimEngagementMapping admin review-experience fix (2026-09-03): the change/changelist pages
must display the underlying MemoryClaim/MemoryClaimSupport evidence read-only, so an operator can
approve/reject safely without database-shell access. No LLM call anywhere in this file."""

from __future__ import annotations

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from ..models import CareerEngagement, ClaimEngagementMapping, MemoryClaim, MemoryClaimSupport
from .factories import make_claim, make_revision, make_source


def _make_engagement(**kwargs) -> CareerEngagement:
    defaults = dict(
        legal_employer="Ambigai Consultancy Services",
        client_organization="Ford Motor Company",
        approved_role_title="Senior Cloud Engineer",
        start_year=2017,
        start_month=7,
        end_status=CareerEngagement.EndStatus.PRESENT,
        approval_status=CareerEngagement.ApprovalStatus.APPROVED,
    )
    defaults.update(kwargs)
    return CareerEngagement.objects.create(**defaults)


class ClaimEngagementMappingAdminReviewTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin-reviewer", email="admin@example.com", password="pw"
        )
        self.client.force_login(self.superuser)

        self.rev = make_revision()
        self.source = make_source(
            self.rev,
            filename="AC-profile_english.md",
            raw_content="\n".join([f"line {i}" for i in range(1, 5)])
            + "\nWorked at Ford Motor Company from 2017 to present.\n",
        )
        self.claim = make_claim(
            self.rev,
            claim_type="employment_dates",
            canonical_text_en="Employed by Ford Motor Company as Senior Cloud Engineer since 2017.",
            subject_scope="organization:ford motor company",
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            resume_eligible=True,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=self.claim,
            memory_source_document=self.source,
            quotation="Worked at Ford Motor Company from 2017 to present.",
            start_line=5,
            end_line=5,
            source_language="en",
            support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        self.engagement = _make_engagement()
        self.mapping = ClaimEngagementMapping.objects.create(
            memory_claim=self.claim,
            career_engagement=self.engagement,
            proposed_reason="exact normalized legal_employer/client_organization match",
        )

    def _change_url(self):
        return reverse("admin:candidate_memory_claimengagementmapping_change", args=[self.mapping.pk])

    def _changelist_url(self):
        return reverse("admin:candidate_memory_claimengagementmapping_changelist")

    def test_changelist_shows_concise_previews(self):
        response = self.client.get(self._changelist_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.claim.claim_id, body)
        self.assertIn("employment_dates", body)
        self.assertIn(self.engagement.engagement_id, body)
        self.assertIn("Senior Cloud Engineer", body)
        self.assertIn("AC-profile_english.md", body)
        self.assertIn("L5-5", body)

    def test_change_page_shows_complete_claim_and_quotation_evidence(self):
        response = self.client.get(self._change_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # MemoryClaim ID, complete canonical text, claim type, subject scope.
        self.assertIn(self.claim.claim_id, body)
        self.assertIn("Employed by Ford Motor Company as Senior Cloud Engineer since 2017.", body)
        self.assertIn("employment_dates", body)
        self.assertIn("organization:ford motor company", body)

        # Confirmation and resume-eligibility status.
        self.assertIn("Confirmed", body)
        self.assertIn("Resume-eligible", body)

        # Exact supporting source quotation, source document name, and line range.
        self.assertIn("Worked at Ford Motor Company from 2017 to present.", body)
        self.assertIn("AC-profile_english.md", body)
        self.assertIn("5", body)

        # Proposed CareerEngagement, proposal/matching basis, mapping status.
        self.assertIn(self.engagement.engagement_id, body)
        self.assertIn("exact normalized legal_employer/client_organization match", body)
        self.assertIn("Proposed", body)

    def test_change_page_shows_every_support_when_a_claim_has_more_than_one(self):
        MemoryClaimSupport.objects.create(
            memory_claim=self.claim,
            memory_source_document=self.source,
            quotation="Second corroborating quotation about Ford Motor Company.",
            start_line=3,
            end_line=3,
            source_language="en",
            support_role=MemoryClaimSupport.SupportRole.CORROBORATING,
        )
        response = self.client.get(self._change_url())
        body = response.content.decode()
        self.assertIn("Worked at Ford Motor Company from 2017 to present.", body)
        self.assertIn("Second corroborating quotation about Ford Motor Company.", body)

    def test_claim_text_and_quotation_are_html_escaped(self):
        self.claim.canonical_text_en = "Owned <script>alert(1)</script> the migration."
        self.claim.save()
        response = self.client.get(self._change_url())
        body = response.content.decode()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_admin_never_alters_the_underlying_claim_or_quotation(self):
        original_text = self.claim.canonical_text_en
        original_quotation = MemoryClaimSupport.objects.get(memory_claim=self.claim).quotation
        self.client.get(self._change_url())
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.canonical_text_en, original_text)
        self.assertEqual(
            MemoryClaimSupport.objects.get(memory_claim=self.claim).quotation, original_quotation
        )

    def test_viewing_the_page_never_approves_or_rejects_the_mapping(self):
        self.client.get(self._changelist_url())
        self.client.get(self._change_url())
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.status, ClaimEngagementMapping.Status.PROPOSED)
        self.assertIsNone(self.mapping.reviewed_at)


class ClaimEngagementMappingAdminPermissionTests(TestCase):
    """The review page must respect normal Django admin permissions -- it introduces no new
    unauthenticated or unprivileged access path."""

    def setUp(self):
        self.rev = make_revision()
        self.engagement = _make_engagement()
        self.claim = make_claim(self.rev, claim_type="employment_dates")
        self.mapping = ClaimEngagementMapping.objects.create(
            memory_claim=self.claim, career_engagement=self.engagement
        )

    def _change_url(self):
        return reverse("admin:candidate_memory_claimengagementmapping_change", args=[self.mapping.pk])

    def _changelist_url(self):
        return reverse("admin:candidate_memory_claimengagementmapping_changelist")

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self._changelist_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_staff_user_without_view_permission_is_refused(self):
        staff_user = User.objects.create_user(username="no-perms", password="pw", is_staff=True)
        self.client.force_login(staff_user)
        response = self.client.get(self._changelist_url())
        self.assertEqual(response.status_code, 403)

    def test_staff_user_with_view_permission_can_read_but_the_page_still_shows_evidence(self):
        staff_user = User.objects.create_user(username="viewer", password="pw", is_staff=True)
        permission = Permission.objects.get(
            content_type__app_label="candidate_memory", codename="view_claimengagementmapping"
        )
        staff_user.user_permissions.add(permission)
        self.client.force_login(staff_user)

        response = self.client.get(self._change_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.claim.claim_id, response.content.decode())
