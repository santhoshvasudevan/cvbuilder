"""Content-plane classification validation: evidence/constraint/positioning, title-vs-fact,
and experience-level non-inflation (requirements.md Sec 16, docs/ARCHITECTURE.md MemoryClaim
invariants)."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..schemas import ContentPlane, ExperienceLevel, ExtractedItem, RuleType, SourcePassage
from ..services.classification import ClassificationError, validate_item


def _support(quote="some quote", start=1, end=1, language="en"):
    return SourcePassage(quote=quote, start_line=start, end_line=end, language=language)


class EvidencePlaneValidationTests(SimpleTestCase):
    def test_valid_evidence_item_passes(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="Built a data pipeline.",
            support=_support(),
            claim_type="skill",
            subject_scope="Acme Corp",
            resume_eligible=True,
        )
        validate_item(item)  # must not raise

    def test_evidence_missing_claim_type_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE, canonical_text_en="x", support=_support(), subject_scope="Acme"
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_evidence_missing_subject_scope_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE, canonical_text_en="x", support=_support(), claim_type="skill"
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_evidence_empty_text_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="   ",
            support=_support(),
            claim_type="skill",
            subject_scope="Acme",
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)


class ConstraintPositioningPlaneValidationTests(SimpleTestCase):
    def test_constraint_requires_rule_type(self):
        item = ExtractedItem(
            plane=ContentPlane.CONSTRAINT, canonical_text_en="Careful wording.", support=_support()
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_constraint_with_rule_type_passes(self):
        item = ExtractedItem(
            plane=ContentPlane.CONSTRAINT,
            canonical_text_en="Currently learning Go.",
            support=_support(),
            rule_type=RuleType.LEARNING_STATUS,
        )
        validate_item(item)

    def test_positioning_title_never_stored_as_evidence(self):
        """A suggested/target job title is POSITIONING, never EVIDENCE -- this is enforced by the
        extraction contract's discriminated `plane` field itself: a positioning item simply has no
        path into `store_extracted_item`'s EVIDENCE branch (see test_storage.py)."""
        item = ExtractedItem(
            plane=ContentPlane.POSITIONING,
            canonical_text_en="Target title: Senior Solutions Architect",
            support=_support(),
            rule_type=RuleType.POSITIONING,
        )
        validate_item(item)
        self.assertEqual(item.plane, ContentPlane.POSITIONING)


class ProvenanceShapeValidationTests(SimpleTestCase):
    def test_empty_quote_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="x",
            support=_support(quote="   "),
            claim_type="skill",
            subject_scope="Acme",
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_inverted_line_range_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="x",
            support=_support(start=5, end=2),
            claim_type="skill",
            subject_scope="Acme",
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)


class ExperienceLevelInflationTests(SimpleTestCase):
    def test_awareness_level_with_delivery_language_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="Delivered a production-grade Go microservice.",
            support=_support(),
            claim_type="skill",
            subject_scope="Acme",
            experience_level=ExperienceLevel.AWARENESS,
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_learning_level_with_shipped_to_production_language_rejected(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="Shipped to production a small Go tool.",
            support=_support(),
            claim_type="skill",
            subject_scope="Acme",
            experience_level=ExperienceLevel.LEARNING,
        )
        with self.assertRaises(ClassificationError):
            validate_item(item)

    def test_awareness_level_with_honest_phrasing_accepted(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="Aware of Go through self-study; no professional delivery experience.",
            support=_support(),
            claim_type="skill",
            subject_scope="Acme",
            experience_level=ExperienceLevel.AWARENESS,
        )
        validate_item(item)  # must not raise

    def test_professional_delivery_level_may_use_delivery_language(self):
        item = ExtractedItem(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en="Delivered the Ford integration project as Solutions Architect.",
            support=_support(),
            claim_type="employment",
            subject_scope="Ford Motor Company",
            experience_level=ExperienceLevel.PROFESSIONAL_DELIVERY,
        )
        validate_item(item)  # must not raise
