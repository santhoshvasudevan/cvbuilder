"""Deterministic lexical relevance scoring (audit hardening, 2026-09-03) -- the first,
non-LLM stage of D-015's "structured retrieval plus a bounded relevance-ranking step" pipeline.
This narrows a large eligible pool down to a per-requirement candidate list *before* anything is
sent to a provider; it is a plain word-overlap heuristic, not an embedding/semantic-similarity
system (matching the project-wide convention of deterministic, explainable narrowing -- see
`candidate_matching.services.static_requirements`'s own regex-heuristic precedent). It is
explicitly a v1 approximation: a genuinely relevant claim phrased with no shared vocabulary can
still be missed by this step alone, which is exactly why `MIN_CANDIDATES_PER_REQUIREMENT` below
guarantees a floor of candidates even when lexical scoring finds nothing, and why the LLM ranking
step downstream still exercises judgment over the resulting pool rather than trusting this score
as a final relevance verdict.
"""

from __future__ import annotations

import re

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with", "at", "by",
        "from", "as", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
        "those", "it", "its", "you", "your", "we", "our", "they", "their", "will", "shall", "must",
        "should", "can", "could", "would", "may", "might", "not", "no", "have", "has", "had",
        "do", "does", "did", "if", "than", "then", "so", "such", "into", "about", "across", "per",
    }
)

_WORD_RE = re.compile(r"[a-zA-Z0-9+#.]+")


def tokenize(text: str) -> frozenset[str]:
    words = _WORD_RE.findall(text.lower())
    return frozenset(word for word in words if len(word) > 1 and word not in _STOPWORDS)


def overlap_score(requirement_tokens: frozenset[str], claim_tokens: frozenset[str]) -> int:
    return len(requirement_tokens & claim_tokens)
