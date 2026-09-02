"""Auto-confirmation policy (requirements.md Sec 7): a claim may become CONFIRMED only when every
condition holds -- source approval alone is never sufficient."""

from __future__ import annotations

from django.test import TestCase

from ..models import MemoryClaim, MemoryClaimSupport, MemorySourceDocument
from ..services.confirmation import evaluate_auto_confirmation
from .factories import make_claim, make_revision, make_source


def _support_for(claim, source, **overrides):
    defaults = dict(
        memory_claim=claim, memory_source_document=source, quotation=claim.canonical_text_en,
        start_line=1, end_line=1, source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
    )
    defaults.update(overrides)
    return MemoryClaimSupport.objects.create(**defaults)


class AutoConfirmationTests(TestCase):
    def test_eligible_claim_with_valid_operator_approved_support_is_confirmed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(rev, canonical_text_en="Built the Ford integration.", resume_eligible=True)
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)

        self.assertEqual(count, 1)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)

    def test_not_resume_eligible_claim_is_not_confirmed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(rev, canonical_text_en="Built the Ford integration.", resume_eligible=False)
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)

    def test_claim_with_no_supports_is_not_confirmed(self):
        rev = make_revision()
        claim = make_claim(rev, resume_eligible=True)
        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)

    def test_claim_supported_only_by_unverified_source_is_not_confirmed(self):
        rev = make_revision()
        source = make_source(
            rev, raw_content="Built the Ford integration.\n",
            trust_status=MemorySourceDocument.TrustStatus.UNVERIFIED,
        )
        claim = make_claim(rev, canonical_text_en="Built the Ford integration.", resume_eligible=True)
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)

    def test_claim_with_quote_that_does_not_resolve_against_source_is_not_confirmed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Something unrelated entirely.\n")
        claim = make_claim(rev, canonical_text_en="Built the Ford integration.", resume_eligible=True)
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)

    def test_blocked_conflict_claim_is_never_auto_confirmed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(
            rev, canonical_text_en="Built the Ford integration.", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT,
        )
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)

    def test_retired_claim_is_never_auto_confirmed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(
            rev, canonical_text_en="Built the Ford integration.", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED,
        )
        _support_for(claim, source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)

    def test_multi_support_claim_requires_every_support_to_validate(self):
        rev = make_revision()
        good_source = make_source(rev, logical_source_key="good", raw_content="Built the Ford integration.\n")
        bad_source = make_source(
            rev, logical_source_key="bad", raw_content="Unrelated content.\n", precedence=3,
        )
        claim = make_claim(rev, canonical_text_en="Built the Ford integration.", resume_eligible=True)
        _support_for(claim, good_source, quotation="Built the Ford integration.")
        _support_for(claim, bad_source, quotation="Built the Ford integration.")

        count = evaluate_auto_confirmation(rev)
        self.assertEqual(count, 0)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)
