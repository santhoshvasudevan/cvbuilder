from __future__ import annotations

from django.test import SimpleTestCase

from ..validators.disposition_coverage import AssessmentItemData, ensure_full_coverage, sanitize_items


def _item(**kwargs) -> AssessmentItemData:
    defaults = dict(
        requirement_id="JR-001",
        disposition="MATCH",
        explanation="Because.",
        gap_or_limitation="",
        supporting_memory_claim_ids=[],
        supporting_engagement_ids=[],
    )
    defaults.update(kwargs)
    return AssessmentItemData(**defaults)


class SanitizeItemsTests(SimpleTestCase):
    def test_valid_match_with_real_evidence_passes_through_unchanged(self):
        item = _item(supporting_memory_claim_ids=["MC-1-0001"])
        result = sanitize_items([item], valid_claim_ids={"MC-1-0001"}, valid_engagement_ids=set())
        self.assertEqual(result[0].disposition, "MATCH")
        self.assertEqual(result[0].supporting_memory_claim_ids, ["MC-1-0001"])
        self.assertEqual(result[0].explanation, "Because.")

    def test_match_with_no_real_evidence_is_downgraded_to_unknown(self):
        item = _item(supporting_memory_claim_ids=["MC-fabricated"])
        result = sanitize_items([item], valid_claim_ids=set(), valid_engagement_ids=set())
        self.assertEqual(result[0].disposition, "UNKNOWN")
        self.assertIn("downgraded to UNKNOWN", result[0].explanation)
        self.assertEqual(result[0].supporting_memory_claim_ids, [])

    def test_fabricated_id_alongside_a_real_one_is_dropped_not_trusted(self):
        item = _item(supporting_memory_claim_ids=["MC-1-0001", "MC-fabricated"])
        result = sanitize_items([item], valid_claim_ids={"MC-1-0001"}, valid_engagement_ids=set())
        self.assertEqual(result[0].disposition, "MATCH")
        self.assertEqual(result[0].supporting_memory_claim_ids, ["MC-1-0001"])
        self.assertIn("discarded", result[0].explanation)

    def test_gap_never_needs_evidence(self):
        item = _item(disposition="GAP")
        result = sanitize_items([item], valid_claim_ids=set(), valid_engagement_ids=set())
        self.assertEqual(result[0].disposition, "GAP")

    def test_unrecognized_disposition_is_downgraded_to_unknown(self):
        item = _item(disposition="STRONG_MATCH")
        result = sanitize_items([item], valid_claim_ids=set(), valid_engagement_ids=set())
        self.assertEqual(result[0].disposition, "UNKNOWN")
        self.assertIn("not a recognized value", result[0].explanation)

    def test_engagement_evidence_alone_is_sufficient_for_match(self):
        item = _item(supporting_engagement_ids=["CE-0001"])
        result = sanitize_items([item], valid_claim_ids=set(), valid_engagement_ids={"CE-0001"})
        self.assertEqual(result[0].disposition, "MATCH")


class EnsureFullCoverageTests(SimpleTestCase):
    def test_missing_requirement_becomes_explicit_unknown(self):
        result = ensure_full_coverage([], ["JR-001"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].disposition, "UNKNOWN")
        self.assertEqual(result[0].requirement_id, "JR-001")

    def test_exactly_one_row_per_requirement_even_with_duplicates(self):
        items = [
            _item(requirement_id="JR-001", disposition="MATCH"),
            _item(requirement_id="JR-001", disposition="GAP"),
        ]
        result = ensure_full_coverage(items, ["JR-001"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].disposition, "MATCH")

    def test_a_known_gap_is_never_dropped(self):
        items = [_item(requirement_id="JR-001", disposition="GAP")]
        result = ensure_full_coverage(items, ["JR-001", "JR-002"])
        by_id = {item.requirement_id: item for item in result}
        self.assertEqual(by_id["JR-001"].disposition, "GAP")
        self.assertEqual(by_id["JR-002"].disposition, "UNKNOWN")

    def test_ignores_an_item_for_a_requirement_not_in_the_ordered_list(self):
        items = [_item(requirement_id="JR-999")]
        result = ensure_full_coverage(items, ["JR-001"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].requirement_id, "JR-001")
