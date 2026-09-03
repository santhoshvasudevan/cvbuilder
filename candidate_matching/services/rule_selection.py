"""Bounded, deterministic CandidateRule selection (audit hardening, 2026-09-03) -- shared by M5
(`services/bounded_retrieval.py`) and M6 (`resume_builder.services.context`), so both stages apply
the exact same rule budget/precedence rather than two independently-drifting implementations.

Rules are never resume evidence (D-015) -- they only ever shape wording/judgment. Genuine
constraints (`PROHIBITION`/`CAUTION`/`LEARNING_STATUS` -- "never say X", "still learning Y", the
kinds of statement that directly prevent an overstatement) are always preserved in full, up to
`MAX_RULES`; failing to fit them is a configuration problem worth surfacing loudly
(`RetrievalBudgetExceededError`), never a silent drop of a safety constraint. Pure positioning
guidance (`PREFERENCE`/`POSITIONING`) is relevance-filtered against the job's own requirement text
(same deterministic lexical scoring as claims) and truncated to whatever budget remains.
"""

from __future__ import annotations

import dataclasses

from candidate_memory.models import CandidateMemory, CandidateRule

from .lexical_relevance import overlap_score, tokenize
from .retrieval_limits import MAX_RULES, RetrievalBudgetExceededError
from .retrieve import RetrievedRule

_MANDATORY_RULE_TYPES = frozenset(
    {
        CandidateRule.RuleType.PROHIBITION,
        CandidateRule.RuleType.CAUTION,
        CandidateRule.RuleType.LEARNING_STATUS,
    }
)


@dataclasses.dataclass(frozen=True)
class RuleSelectionResult:
    selected: list[RetrievedRule]
    duplicate_count: int
    excluded_positioning_count: int


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def select_bounded_rules(
    candidate_memory: CandidateMemory, requirement_texts: list[str]
) -> RuleSelectionResult:
    raw_rules = list(CandidateRule.objects.filter(candidate_memory=candidate_memory).order_by("id"))

    seen_text: set[str] = set()
    deduped: list[CandidateRule] = []
    duplicate_count = 0
    for rule in raw_rules:
        key = _normalize(rule.text)
        if key in seen_text:
            duplicate_count += 1
            continue
        seen_text.add(key)
        deduped.append(rule)

    mandatory = [rule for rule in deduped if rule.rule_type in _MANDATORY_RULE_TYPES]
    positioning = [rule for rule in deduped if rule.rule_type not in _MANDATORY_RULE_TYPES]

    if len(mandatory) > MAX_RULES:
        raise RetrievalBudgetExceededError(
            f"{len(mandatory)} mandatory CandidateRules (PROHIBITION/CAUTION/LEARNING_STATUS) "
            f"alone exceed MAX_RULES={MAX_RULES} -- these can never be silently dropped. Raise "
            "MAX_RULES or reduce the number of mandatory rules on the ACTIVE CandidateMemory."
        )

    remaining_budget = MAX_RULES - len(mandatory)
    if requirement_texts:
        combined_requirement_tokens = frozenset().union(*(tokenize(text) for text in requirement_texts))
    else:
        combined_requirement_tokens = frozenset()
    scored_positioning = sorted(
        positioning,
        key=lambda rule: (-overlap_score(combined_requirement_tokens, tokenize(rule.text)), rule.pk),
    )
    selected_positioning = scored_positioning[:remaining_budget]
    excluded_positioning_count = len(positioning) - len(selected_positioning)

    selected = mandatory + selected_positioning
    selected.sort(key=lambda rule: rule.pk)

    return RuleSelectionResult(
        selected=[
            RetrievedRule(rule_id=rule.pk, rule_type=rule.rule_type, text=rule.text, scope=rule.scope)
            for rule in selected
        ],
        duplicate_count=duplicate_count,
        excluded_positioning_count=excluded_positioning_count,
    )
