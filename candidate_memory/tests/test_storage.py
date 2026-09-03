"""store_extracted_item: provenance validation (quote/hash/line), duplicate/group identification,
multi-support claims, English/German grouping, and evidence-vs-constraint routing."""

from __future__ import annotations

from django.test import TestCase

from ..models import CandidateRule, MemoryClaim, MemoryClaimSupport
from ..schemas import ContentPlane, EmploymentDatesValue, ExtractedItem, RuleType, SourcePassage
from ..services.classification import ClassificationError
from ..services.storage import ProvenanceError, duplicate_group_key, store_extracted_item
from .factories import make_revision, make_source


def _evidence_item(**overrides):
    defaults = dict(
        plane=ContentPlane.EVIDENCE,
        canonical_text_en="Built the Ford integration.",
        support=SourcePassage(
            quote="Built the Ford integration.", start_line=1, end_line=1, language="en"
        ),
        claim_type="employment",
        subject_scope="Ford Motor Company",
        resume_eligible=True,
    )
    defaults.update(overrides)
    return ExtractedItem(**defaults)


class ProvenanceValidationTests(TestCase):
    def test_quote_must_match_exact_source_lines(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\nline2\n")
        item = _evidence_item()
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertIsInstance(claim, MemoryClaim)

    def test_quote_not_matching_source_line_range_raises(self):
        rev = make_revision()
        source = make_source(rev, raw_content="something else entirely\nline2\n")
        item = _evidence_item()
        with self.assertRaises(ProvenanceError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)

    def test_tampered_source_content_hash_mismatch_raises(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        # Simulate content having been altered after the hash was computed (should never happen
        # via the ORM since MemorySourceDocument is immutable, but this is the defense-in-depth
        # check store_extracted_item performs regardless).
        source.raw_content = "tampered"
        item = _evidence_item()
        with self.assertRaises(ProvenanceError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)


class DuplicateGroupKeyTests(TestCase):
    def test_hint_is_normalized(self):
        item = _evidence_item(duplicate_group_hint="  Ford Solutions Architect Role  ")
        self.assertEqual(duplicate_group_key(item), "ford_solutions_architect_role")

    def test_no_hint_never_produces_a_stable_coarse_fallback_key(self):
        """Candidate Memory recovery (2026-09-03): without an explicit duplicate_group_hint, two
        items sharing subject_scope+claim_type must never be treated as the same fact -- each
        call gets its own unique key, so distinct facts sharing a scope/type are never merged."""
        item = _evidence_item(
            duplicate_group_hint=None, subject_scope="Ford Motor Company", claim_type="Employment"
        )
        key_a = duplicate_group_key(item)
        key_b = duplicate_group_key(item)
        self.assertNotEqual(key_a, key_b)
        self.assertNotEqual(key_a, "ford motor company::employment")


class ClaimStorageRoutingTests(TestCase):
    def test_evidence_item_creates_memory_claim(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        result = store_extracted_item(_evidence_item(), candidate_memory=rev, source_document=source)
        self.assertIsInstance(result, MemoryClaim)
        self.assertEqual(result.supports.count(), 1)

    def test_constraint_item_creates_candidate_rule_not_claim(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Currently learning Kubernetes.\n")
        item = ExtractedItem(
            plane=ContentPlane.CONSTRAINT,
            canonical_text_en="Currently learning Kubernetes.",
            support=SourcePassage(
                quote="Currently learning Kubernetes.", start_line=1, end_line=1, language="en"
            ),
            rule_type=RuleType.LEARNING_STATUS,
        )
        result = store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertIsInstance(result, CandidateRule)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 0)

    def test_second_item_with_same_duplicate_group_key_adds_support_not_new_claim(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        store_extracted_item(
            _evidence_item(duplicate_group_hint="ford_role"), candidate_memory=rev, source_document=source
        )

        source2 = make_source(
            rev, logical_source_key="german", filename="german.md", language="de", precedence=3,
            raw_content="Baute die Ford-Integration.\n",
        )
        item2 = _evidence_item(
            duplicate_group_hint="ford_role",
            canonical_text_en="Built the Ford integration.",
            support=SourcePassage(
                quote="Baute die Ford-Integration.", start_line=1, end_line=1, language="de"
            ),
        )
        store_extracted_item(item2, candidate_memory=rev, source_document=source2)

        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 1)
        claim = MemoryClaim.objects.get(candidate_memory=rev)
        self.assertEqual(claim.supports.count(), 2)

    def test_english_support_is_primary_german_support_is_german_expression(self):
        rev = make_revision()
        source_en = make_source(rev, raw_content="Built the Ford integration.\n")
        store_extracted_item(
            _evidence_item(duplicate_group_hint="ford_role"),
            candidate_memory=rev, source_document=source_en,
        )

        source_de = make_source(
            rev, logical_source_key="german", filename="german.md", language="de", precedence=3,
            raw_content="Baute die Ford-Integration.\n",
        )
        item2 = _evidence_item(
            duplicate_group_hint="ford_role",
            support=SourcePassage(
                quote="Baute die Ford-Integration.", start_line=1, end_line=1, language="de"
            ),
        )
        store_extracted_item(item2, candidate_memory=rev, source_document=source_de)

        claim = MemoryClaim.objects.get(candidate_memory=rev)
        roles = set(claim.supports.values_list("support_role", flat=True))
        self.assertEqual(
            roles,
            {MemoryClaimSupport.SupportRole.PRIMARY, MemoryClaimSupport.SupportRole.GERMAN_EXPRESSION},
        )

    def test_third_english_support_for_same_group_is_corroborating(self):
        rev = make_revision()
        source1 = make_source(rev, raw_content="Built the Ford integration.\n")
        store_extracted_item(
            _evidence_item(duplicate_group_hint="ford_role"), candidate_memory=rev, source_document=source1
        )

        source2 = make_source(
            rev, logical_source_key="second", filename="second.md", precedence=1,
            raw_content="Built the Ford integration.\n",
        )
        store_extracted_item(
            _evidence_item(duplicate_group_hint="ford_role"), candidate_memory=rev, source_document=source2
        )

        claim = MemoryClaim.objects.get(candidate_memory=rev)
        self.assertEqual(claim.supports.count(), 2)
        self.assertTrue(
            claim.supports.filter(support_role=MemoryClaimSupport.SupportRole.CORROBORATING).exists()
        )


class LegalEmployerClientSeparationTests(TestCase):
    """Operator resolution 2026-09-02 item 1/2: Ambigai Consultancy Services was the legal
    employer for the Ford/Continental client assignments; both must be stored separately, and the
    default presentation is client-centric."""

    def test_legal_employer_and_client_organization_stored_separately(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Employed by Ambigai, assigned to Ford client project.\n")
        item = _evidence_item(
            canonical_text_en="Employed by Ambigai, assigned to Ford client project.",
            support=SourcePassage(
                quote="Employed by Ambigai, assigned to Ford client project.",
                start_line=1, end_line=1, language="en",
            ),
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
        )
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(claim.legal_employer, "Ambigai Consultancy Services")
        self.assertEqual(claim.client_organization, "Ford Motor Company")
        self.assertEqual(claim.presentation_mode, MemoryClaim.PresentationMode.CLIENT_CENTRIC)

    def test_ordinary_claim_leaves_legal_employer_and_client_blank(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = store_extracted_item(_evidence_item(), candidate_memory=rev, source_document=source)
        self.assertEqual(claim.legal_employer, "")
        self.assertEqual(claim.client_organization, "")


class SubjectScopeNormalizationIntegrationTests(TestCase):
    """Extraction-quality repair: `store_extracted_item` fills a genuinely missing subject_scope
    from the item's own structured fields before validation/storage -- proven here through the
    real production entry point, not just the pure `normalize_subject_scope` unit tests."""

    def test_missing_scope_is_derived_from_client_organization_before_storage(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Assigned to Globex Corporation, starting 2018.\n")
        item = _evidence_item(
            canonical_text_en="Assigned to Globex Corporation, starting 2018.",
            support=SourcePassage(
                quote="Assigned to Globex Corporation, starting 2018.",
                start_line=1, end_line=1, language="en",
            ),
            claim_type="employment_dates",
            subject_scope=None,
            legal_employer="Fictional Consulting Group",
            client_organization="Globex Corporation",
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(claim.subject_scope, "organization:globex corporation")

    def test_missing_scope_with_nothing_derivable_fails_closed(self):
        """A skill claim with no subject_scope and no organization/language field to derive from
        is genuinely ambiguous -- it must never silently become a claim with a fabricated or
        empty scope."""
        rev = make_revision()
        source = make_source(rev, raw_content="Uses Python daily.\n")
        item = _evidence_item(
            canonical_text_en="Uses Python daily.",
            support=SourcePassage(quote="Uses Python daily.", start_line=1, end_line=1, language="en"),
            claim_type="skill",
            subject_scope=None,
        )
        with self.assertRaises(ClassificationError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 0)

    def test_already_valid_scope_is_never_overwritten(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Continental client work, starting 2015.\n")
        item = _evidence_item(
            canonical_text_en="Continental client work, starting 2015.",
            support=SourcePassage(
                quote="Continental client work, starting 2015.", start_line=1, end_line=1, language="en"
            ),
            claim_type="employment_dates",
            subject_scope="Continental",
            employment_dates=EmploymentDatesValue(start_year=2015),
        )
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(claim.subject_scope, "Continental")
