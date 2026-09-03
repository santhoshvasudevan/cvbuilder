"""D-017 fix: end-to-end proof that store_extracted_item recovers a wrapped-line quote through
the real production path (never a hand-built MemoryClaim), and that the pre-existing exact-match
and content-hash checks are completely unaffected."""

from __future__ import annotations

from django.test import TestCase

from ..schemas import ContentPlane, ExtractedItem, SourcePassage
from ..services.storage import ProvenanceError, store_extracted_item
from .factories import make_revision, make_source


def _item(quote, start_line, end_line, **overrides):
    defaults = dict(
        plane=ContentPlane.EVIDENCE,
        canonical_text_en="Built the Ford integration.",
        support=SourcePassage(quote=quote, start_line=start_line, end_line=end_line, language="en"),
        claim_type="employment",
        subject_scope="Ford Motor Company",
        resume_eligible=True,
    )
    defaults.update(overrides)
    return ExtractedItem(**defaults)


class WrappedLineRecoveryIntegrationTests(TestCase):
    def test_wrapped_sentence_with_wrong_line_range_and_space_joined_quote_is_stored(self):
        rev = make_revision()
        source = make_source(
            rev,
            raw_content=(
                "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to\n"
                "client Globex Corporation, from March 2018 to September 2021.\n"
            ),
        )
        # Model reconstructs the quote with a space (not the source's real newline) and reports
        # the wrong (single-line) end_line, exactly as observed in the live qualification call.
        item = _item(
            quote=(
                "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to "
                "client Globex Corporation, from March 2018 to September 2021."
            ),
            start_line=1,
            end_line=1,
        )
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        support = claim.supports.get()
        self.assertEqual(support.start_line, 1)
        self.assertEqual(support.end_line, 2)
        self.assertIn("\n", support.quotation)
        self.assertNotIn("assigned to client", support.quotation)  # never the space-joined form

    def test_recovered_quotation_is_an_exact_substring_of_the_immutable_source(self):
        rev = make_revision()
        raw_content = (
            "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to\n"
            "client Globex Corporation, from March 2018 to September 2021.\n"
        )
        source = make_source(rev, raw_content=raw_content)
        item = _item(
            quote=(
                "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to "
                "client Globex Corporation, from March 2018 to September 2021."
            ),
            start_line=1,
            end_line=1,
        )
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        support = claim.supports.get()
        self.assertIn(support.quotation, raw_content)  # the exact-substring invariant, restated


class UnwrappedBehaviorUnchangedTests(TestCase):
    def test_single_line_quote_still_stores_via_the_fast_exact_path(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\nline2\n")
        item = _item(quote="Built the Ford integration.", start_line=1, end_line=1)
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        support = claim.supports.get()
        self.assertEqual(support.quotation, "Built the Ford integration.")
        self.assertEqual(support.start_line, 1)
        self.assertEqual(support.end_line, 1)

    def test_unwrapped_long_single_line_ac_style_content_stores_unchanged(self):
        """Mirrors the bulk AC corpus's own style (confirmed during the D-017 audit): one long
        logical paragraph per physical line, never hard-wrapped -- the fast exact path should
        handle this with no recovery needed."""
        rev = make_revision()
        long_line = (
            "Delivered a multi-year client engagement covering requirements analysis, solution "
            "design, stakeholder communication, and delivery oversight across several concurrent "
            "workstreams for a large automotive client.\n"
        )
        source = make_source(rev, raw_content=long_line)
        item = _item(quote=long_line.strip(), start_line=1, end_line=1)
        claim = store_extracted_item(item, candidate_memory=rev, source_document=source)
        support = claim.supports.get()
        self.assertEqual(support.quotation, long_line.strip())


class FailClosedTests(TestCase):
    def test_ambiguous_duplicate_normalized_match_is_rejected(self):
        rev = make_revision()
        source = make_source(
            rev,
            raw_content="Repeated phrase here.\nSomething else.\nRepeated phrase here.\n",
        )
        item = _item(quote="Repeated phrase here.", start_line=2, end_line=2)  # wrong range forces fallback
        with self.assertRaises(ProvenanceError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(rev.claims.count(), 0)

    def test_missing_quotation_anywhere_is_rejected(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Nothing relevant in this document.\n")
        item = _item(quote="This text does not appear at all.", start_line=1, end_line=1)
        with self.assertRaises(ProvenanceError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(rev.claims.count(), 0)

    def test_content_hash_tampering_is_still_rejected_before_recovery_is_even_attempted(self):
        rev = make_revision()
        source = make_source(
            rev,
            raw_content=(
                "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to\n"
                "client Globex Corporation.\n"
            ),
        )
        # Simulate tampering after the hash was computed (mirrors the existing hash-mismatch test).
        source.raw_content = "tampered content that would otherwise recover just fine"
        item = _item(
            quote=(
                "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to "
                "client Globex Corporation."
            ),
            start_line=1,
            end_line=1,
        )
        with self.assertRaises(ProvenanceError):
            store_extracted_item(item, candidate_memory=rev, source_document=source)
        self.assertEqual(rev.claims.count(), 0)
