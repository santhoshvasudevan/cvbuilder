from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, CandidateRule

from ..services.retrieval_limits import MAX_RULES, RetrievalBudgetExceededError
from ..services.rule_selection import select_bounded_rules
from .factories import freeze_revision, make_revision


def _rule(rev, rule_type, text, scope=""):
    return CandidateRule.objects.create(candidate_memory=rev, rule_type=rule_type, text=text, scope=scope)


class SelectBoundedRulesTests(TestCase):
    def test_mandatory_rules_are_always_preserved(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        caution = _rule(rev, CandidateRule.RuleType.CAUTION, "Still learning Kubernetes.")
        prohibition = _rule(rev, CandidateRule.RuleType.PROHIBITION, "Never claim security clearance.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        result = select_bounded_rules(rev, ["Some job requirement."])
        selected_texts = {r.text for r in result.selected}
        self.assertIn(caution.text, selected_texts)
        self.assertIn(prohibition.text, selected_texts)

    def test_exact_duplicate_rules_are_deduplicated(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        _rule(rev, CandidateRule.RuleType.PREFERENCE, "Prefers backend roles.")
        _rule(rev, CandidateRule.RuleType.PREFERENCE, "  prefers   BACKEND roles.  ")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        result = select_bounded_rules(rev, [])
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(len(result.selected), 1)

    def test_positioning_rules_are_relevance_filtered_when_budget_is_tight(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        relevant = _rule(
            rev, CandidateRule.RuleType.POSITIONING, "Emphasize Kubernetes orchestration experience."
        )
        irrelevant = _rule(rev, CandidateRule.RuleType.POSITIONING, "Mention hobby woodworking projects.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        import candidate_matching.services.rule_selection as rule_selection_module

        original_max = rule_selection_module.MAX_RULES
        rule_selection_module.MAX_RULES = 1
        try:
            result = select_bounded_rules(rev, ["Kubernetes orchestration required."])
        finally:
            rule_selection_module.MAX_RULES = original_max

        selected_texts = {r.text for r in result.selected}
        self.assertIn(relevant.text, selected_texts)
        self.assertNotIn(irrelevant.text, selected_texts)
        self.assertEqual(result.excluded_positioning_count, 1)

    def test_mandatory_rules_exceeding_the_budget_fail_closed(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        for i in range(MAX_RULES + 1):
            _rule(rev, CandidateRule.RuleType.CAUTION, f"Caution number {i}.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        with self.assertRaises(RetrievalBudgetExceededError):
            select_bounded_rules(rev, [])

    def test_no_rules_returns_empty_selection(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        result = select_bounded_rules(rev, [])
        self.assertEqual(result.selected, [])
