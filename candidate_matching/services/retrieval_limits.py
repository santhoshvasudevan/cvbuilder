"""Configurable hard limits for the bounded retrieval pipeline (audit hardening, 2026-09-03).

Defaults are chosen against the real, activated CandidateMemory revision 2 (1,122 eligible
narrative claims, 359 CandidateRules, 3 APPROVED CareerEngagements as of 2026-09-03) -- large
enough that "send everything" produced a ~203,000-character (~50,785-token) request per stage in
the pre-hardening audit. These defaults keep a realistic per-job request in the low thousands of
tokens while still giving every JobRequirement a genuine, non-trivial pool of candidate evidence
to judge, and are deliberately generous relative to that measured baseline rather than tuned to a
guessed "ideal" -- revisit them with real operator feedback once this has been used for actual job
applications, not before.

`CHARS_PER_TOKEN_ESTIMATE` is a conservative (i.e. slightly overestimates token count) plain
character-count heuristic -- not an actual tokenizer -- used only to decide whether a request stays
inside `MAX_ESTIMATED_REQUEST_TOKENS`, never to decide what to cut. Exceeding it after all count
caps have already been applied is treated as a configuration/data problem worth surfacing loudly
(`RetrievalBudgetExceededError`), not something to silently truncate away.
"""

from __future__ import annotations

CHARS_PER_TOKEN_ESTIMATE = 4

# Per-JobRequirement lexical candidate generation (services/candidate_generation.py).
MAX_CANDIDATES_PER_REQUIREMENT = 40
MIN_CANDIDATES_PER_REQUIREMENT = 10

# The union candidate pool sent to the D-015 relevance-ranking adapter (services/rank.py).
MAX_RANKING_CANDIDATES = 150

# The final claim set selected (across all requirements combined) after ranking, actually placed
# into the AC_MATCH/AB_BUILD request.
MAX_SELECTED_CLAIMS = 80

# CandidateRule budget (services/rule_selection.py) -- shared by M5 and M6.
MAX_RULES = 30

# Hard ceiling on the estimated token size of the final AC_MATCH/AB_BUILD request (context +
# requirements/JRA text, not counting the system prompt). Exceeding this after every other cap has
# already been applied raises an actionable error rather than truncating mid-request.
MAX_ESTIMATED_REQUEST_TOKENS = 12_000


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE


class RetrievalBudgetExceededError(Exception):
    """Raised when the final, already-capped request content still exceeds
    `MAX_ESTIMATED_REQUEST_TOKENS`, or when mandatory CandidateRules alone exceed `MAX_RULES` --
    both are configuration/data problems for the operator to address, never silently absorbed by
    truncating request content underneath a validator that expects it to be complete."""
