"""Unit-level coverage of the pure D-017 recovery function -- no database, no ExtractedItem."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..services.quote_recovery import recover_quote


class ExactMatchUnaffectedTests(SimpleTestCase):
    """Recovery is a fallback -- when the exact match already succeeds, storage.py never calls
    this function at all (see test_storage.py's existing provenance tests, unchanged). These
    tests just confirm recovery itself also succeeds trivially on an already-exact single-line
    quote, as a baseline sanity check of the function in isolation."""

    def test_single_line_exact_quote_recovers_identically(self):
        source = "Alice worked at Acme Corp.\nSecond line.\n"
        recovered = recover_quote(source, "Alice worked at Acme Corp.")
        self.assertEqual(recovered.text, "Alice worked at Acme Corp.")
        self.assertEqual(recovered.start_line, 1)
        self.assertEqual(recovered.end_line, 1)


class WrappedSentenceRecoveryTests(SimpleTestCase):
    def test_sentence_wrapped_across_two_physical_lines_recovers_exact_original(self):
        source = (
            "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to\n"
            "client Globex Corporation, from March 2018 to September 2021.\n"
        )
        # The model joins the wrapped halves with a single space, as observed live.
        model_quote = (
            "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to "
            "client Globex Corporation, from March 2018 to September 2021."
        )
        recovered = recover_quote(source, model_quote)
        self.assertIsNotNone(recovered)
        self.assertEqual(
            recovered.text,
            "Alex Doe worked as Senior Systems Analyst for Fictional Consulting Group, assigned to\n"
            "client Globex Corporation, from March 2018 to September 2021.",
        )
        self.assertEqual(recovered.start_line, 1)
        self.assertEqual(recovered.end_line, 2)

    def test_recovered_text_contains_the_real_newline_not_a_space(self):
        source = "First half of the\nsecond half of the sentence.\n"
        model_quote = "First half of the second half of the sentence."
        recovered = recover_quote(source, model_quote)
        self.assertIn("\n", recovered.text)
        self.assertNotIn("the second", recovered.text)  # never the normalized/joined form

    def test_wrapped_across_three_physical_lines(self):
        source = "One\ntwo\nthree.\nUnrelated line.\n"
        recovered = recover_quote(source, "One two three.")
        self.assertEqual(recovered.text, "One\ntwo\nthree.")
        self.assertEqual(recovered.start_line, 1)
        self.assertEqual(recovered.end_line, 3)


class WhitespaceVarietyTests(SimpleTestCase):
    def test_multiple_spaces_tabs_and_newlines_all_normalize_to_a_single_space(self):
        source = "Alpha   beta\t\tgamma\n\n\ndelta.\n"
        recovered = recover_quote(source, "Alpha beta gamma delta.")
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.text, "Alpha   beta\t\tgamma\n\n\ndelta.")

    def test_leading_and_trailing_whitespace_in_quote_is_ignored(self):
        source = "Clean sentence here.\n"
        recovered = recover_quote(source, "   Clean sentence here.   ")
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.text, "Clean sentence here.")


class AmbiguousAndMissingMatchTests(SimpleTestCase):
    def test_two_normalized_matches_reject_as_ambiguous(self):
        source = "Repeated phrase here.\nSomething else.\nRepeated phrase here.\n"
        recovered = recover_quote(source, "Repeated phrase here.")
        self.assertIsNone(recovered)

    def test_no_match_anywhere_returns_none(self):
        source = "Nothing relevant in this document.\n"
        recovered = recover_quote(source, "This text does not appear at all.")
        self.assertIsNone(recovered)

    def test_blank_quote_returns_none(self):
        source = "Some content.\n"
        recovered = recover_quote(source, "   ")
        self.assertIsNone(recovered)


class OperatorResolutionsStyleWrappingTests(SimpleTestCase):
    """Synthetic content mimicking docs/AC/AC-OPERATOR_FACT_RESOLUTIONS.md's own hard-wrapped-at-
    ~90-columns style (confirmed via the read-only D-017 audit) -- never the real file content."""

    def test_conventionally_wrapped_paragraph_recovers_correctly(self):
        source = (
            "Ford client work began November 2017; no end date should be invented since the\n"
            "engagement's conclusion was never explicitly stated by any operator-approved source\n"
            "document reviewed during this bootstrap.\n"
        )
        model_quote = (
            "Ford client work began November 2017; no end date should be invented since the "
            "engagement's conclusion was never explicitly stated by any operator-approved source "
            "document reviewed during this bootstrap."
        )
        recovered = recover_quote(source, model_quote)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.start_line, 1)
        self.assertEqual(recovered.end_line, 3)
        self.assertEqual(recovered.text.count("\n"), 2)
