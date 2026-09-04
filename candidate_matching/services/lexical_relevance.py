"""Deterministic, rarity-aware relevance scoring (audit hardening, 2026-09-03; corrected
2026-09-04 following an independent re-audit's recall finding) -- the first, non-LLM stage of
D-015's "structured retrieval plus a bounded relevance-ranking step" pipeline. This narrows a
large eligible pool down to a per-requirement candidate list *before* anything is sent to a
provider; it is a plain statistical/lexical scorer over the actual claim corpus passed in at call
time, not an embedding/semantic-similarity system and not an LLM (matching the project-wide
convention of deterministic, explainable narrowing -- see
`candidate_matching.services.static_requirements`'s own regex-heuristic precedent).

**2026-09-04 correction**: the original version of this module scored a claim by plain shared-
token *count*, which let generic, corpus-wide-common words ("vehicle", "connected", "cloud" --
each appearing in hundreds of this candidate's own claims) outweigh rare, highly diagnostic terms
("Kubernetes", appearing in only ~30) -- an independent re-audit measured real critical-evidence
loss from this before a claim ever reached the AC_RANK candidate pool. The scorer below replaces
raw overlap counting with three deterministic, additive components, all computed from the actual
eligible-claim corpus given at call time (never a hard-coded claim ID or keyword list).

Two dead ends were tried and rejected the same day before landing on the design below, both
diagnosed by directly comparing a Kubernetes-mentioning claim's score against a generic claim
sharing only common words:

- A **flat** bonus for any exact phrase match reintroduced the same problem one level up -- a
  *common* two-word phrase (e.g. "connected vehicle") got the same flat reward as a genuinely rare
  one (e.g. "adaptive cruise control"), letting a generic claim outscore a specific one again.
- Weighting that phrase bonus by the **exact phrase's own document frequency** (mirroring the
  unigram approach exactly) still failed, for a subtler reason: an exact multi-word phrase is
  inherently sparse as a string regardless of how common its individual words are -- "connected
  vehicle" as an adjacent bigram occurs in relatively few claims even though "connected" and
  "vehicle" individually occur in hundreds, so phrase-level document frequency alone couldn't tell
  a common phrase from a rare one.

The fix: a matched phrase's bonus is weighted by the **average of its constituent words' own
(already-dampened) unigram IDF** -- the same rarity signal unigram scoring already trusts, applied
one level up. A phrase built from corpus-common words stays weighted low; a phrase built from
corpus-rare words is weighted high -- consistent by construction, with no separate phrase-frequency
statistic to get wrong.

1. **BM25 unigram scoring** (Robertson/Sparck-Jones IDF + term-frequency saturation) -- a term's
   weight is inversely proportional to how many claims in *this run's own corpus* contain it, so a
   term nearly every claim shares contributes almost nothing, while a term only a handful of
   claims contain contributes heavily. This is the standard, decades-old deterministic IR
   technique BM25 -- no embeddings, no learned model, no external service. Terms whose document
   frequency exceeds `_COMMON_TERM_DF_RATIO_THRESHOLD` (a corpus-relative ratio, recomputed every
   run -- never a fixed word list) have their already-modest natural IDF further dampened, since
   plain log-IDF alone still lets several medium-rarity common-topic words sum to outweigh one
   genuinely rare term.
2. **Rarity-weighted exact-phrase bonus** -- every requirement bigram/trigram (e.g. "adaptive
   cruise control") that appears verbatim (case-insensitive) in a claim's text adds a bonus scaled
   by the *average unigram IDF of its own words* (see above) -- a phrase made of rare, diagnostic
   words is rewarded strongly; a phrase made of words that are themselves common in this
   candidate's own corpus is not, regardless of how rare the exact adjacent phrase string happens
   to be.
3. **Rarity-weighted acronym bonus** -- a short (2-6 character), mixed/uppercase-heavy token in
   the *original-case* requirement text (e.g. "ADAS", "GCP", "HiL") that appears as an exact,
   case-sensitive whole word in a claim's original text adds a bonus scaled by that term's own
   (lowercased) unigram document frequency -- belt-and-suspenders on top of BM25's own unigram
   scoring for the same term, not a flat reward independent of rarity.

It is still explicitly a v1 approximation: a genuinely relevant claim phrased with no shared
vocabulary can still be missed by this step alone, which is exactly why
`MIN_CANDIDATES_PER_REQUIREMENT` (see `candidate_generation.py`) guarantees a floor of candidates
even when scoring finds nothing, and why the LLM ranking step downstream still exercises judgment
over the resulting pool rather than trusting this score as a final relevance verdict.
"""

from __future__ import annotations

import dataclasses
import math
import re

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with", "at", "by",
        "from", "as", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
        "those", "it", "its", "you", "your", "we", "our", "they", "their", "will", "shall", "must",
        "should", "can", "could", "would", "may", "might", "not", "no", "have", "has", "had",
        "do", "does", "did", "if", "than", "then", "so", "such", "into", "about", "across", "per",
        # Generic resume/job-posting boilerplate -- these discriminate nothing about *which*
        # requirement or claim is relevant (nearly every requirement says "experience with X",
        # nearly every claim covers some number of "years") and were found, during the 2026-09-04
        # recall correction, to accumulate enough IDF weight on their own to compete with genuinely
        # rare, diagnostic domain terms.
        "experience", "experienced", "year", "years",
        # Common German connector/function words -- found necessary during the 2026-09-04
        # requirement-normalization work: the AC_NORMALIZE architecture requires a German
        # requirement's *original* text to stay part of the scored search text alongside its
        # English canonical translation (never replaced), and the real CandidateMemory corpus
        # itself contains some German-language claim text. Left unfiltered, a German requirement's
        # grammatical filler words ("mit", "und", "für") spuriously matched *any* German-language
        # claim regardless of topical relevance, drowning out genuinely relevant English technical
        # claims that should also surface. These carry no more topical signal in German than "and"/
        # "with"/"for" do in English -- not a translation of the pipeline's working language, just
        # the same class of noise word this list already filters for English.
        "mit", "und", "für", "fuer", "sowie", "durch", "bei", "aus", "im", "zu", "von", "der",
        "die", "das", "den", "dem", "des", "ist", "sind", "oder", "auf", "nach", "über", "ueber",
    }
)

_WORD_RE = re.compile(r"[a-zA-Z0-9+#.]+")

# BM25's conventional constants (Robertson et al.) -- k1 controls term-frequency saturation, b
# controls document-length normalization strength. Neither is corpus-specific; these are the
# standard defaults used across BM25 implementations generally.
_BM25_K1 = 1.5
_BM25_B = 0.75

# A term (or, via `_rarity_weight`, a phrase/acronym) appearing in more than this fraction of the
# corpus is "common" for this run's own vocabulary (never a fixed keyword list) and has its
# already-modest natural IDF weight further dampened.
_COMMON_TERM_DF_RATIO_THRESHOLD = 0.08
_COMMON_TERM_DAMPENING = 0.15

# Scales applied to the rarity weight (see `_rarity_weight`) to produce the phrase/acronym bonus --
# calibrated so a rare, diagnostic phrase/acronym match is comparable to or stronger than a rare
# unigram's own BM25 contribution, while a common phrase/acronym contributes little to nothing,
# exactly like a common unigram.
PHRASE_BONUS_SCALE = 1.0
ACRONYM_BONUS_SCALE = 2.5

_ACRONYM_RE = re.compile(r"^[a-zA-Z0-9]{2,6}$")


def _findall_words(text: str) -> list[str]:
    """`_WORD_RE` deliberately includes "." so genuine decimal/version tokens ("3.5", "K8s.io")
    are not split apart -- but that means an ordinary sentence-final word immediately followed by
    a period (with no space, as almost every claim/requirement sentence is) is matched *with* that
    trailing period glued on ("platforms." as one token, distinct from "platforms"). Found during
    the 2026-09-04 requirement-normalization work to silently fragment a claim's own final word
    away from every other occurrence of that same word elsewhere in the corpus, corrupting its
    document frequency. Stripping a trailing "." here (but no other punctuation, and no interior
    dots) fixes exactly that sentence-boundary artifact without touching genuine decimal/version
    tokens, which never end in a bare trailing dot."""
    return [stripped for word in _WORD_RE.findall(text) if (stripped := word.rstrip("."))]


def tokenize(text: str) -> frozenset[str]:
    """Unordered, deduplicated lowercase tokens -- used where only set membership matters (e.g.
    `services/rule_selection.py`'s simpler positioning-rule relevance filter, which this
    correction leaves unchanged)."""
    words = _findall_words(text.lower())
    return frozenset(word for word in words if len(word) > 1 and word not in _STOPWORDS)


def overlap_score(requirement_tokens: frozenset[str], claim_tokens: frozenset[str]) -> int:
    return len(requirement_tokens & claim_tokens)


def tokenize_ordered(text: str) -> list[str]:
    """Lowercase tokens, order and repetition preserved, stopwords removed -- the shape BM25 term
    frequency and phrase n-grams both need."""
    words = _findall_words(text.lower())
    return [word for word in words if len(word) > 1 and word not in _STOPWORDS]


def extract_acronyms(original_text: str) -> frozenset[str]:
    """Case-preserved technical/proper-noun tokens from the *original* (non-lowercased) text --
    the signal `_acronym_bonus` uses on top of BM25's own unigram scoring. Two deterministic,
    capitalization-based patterns, neither a hard-coded word list:

    - a short (2-6 character) token with at least two uppercase letters -- conventional acronyms
      ("ADAS", "GCP") and mixed-case domain abbreviations ("HiL") alike.
    - any other capitalized token that is *not the first word of the text* and is not itself an
      ordinary stopword -- ordinary English only capitalizes mid-sentence for a proper noun or
      technology/product name ("Kubernetes", "Terraform", "BigQuery"), so this catches exactly
      that class without needing a name list, while excluding a token's sentence-initial
      capitalization, which carries no such signal (any sentence starts capitalized regardless of
      whether its first word is diagnostic). The stopword exclusion matters because
      `services/candidate_generation.py` concatenates a requirement's original text with its
      normalization-stage restatement into one string (`normalize.build_search_text`) -- the
      restatement's own first word ("Build...", "Experience...") is capitalized but is no longer
      at index 0 of the *combined* string, and a stopword's document frequency is never measured
      (stopwords are filtered out of `build_corpus_stats` entirely), so an un-guarded lookup would
      read that absence as "the rarest possible term" and award a large, spurious bonus -- found
      during the 2026-09-04 requirement-normalization work.
    """
    words = _findall_words(original_text)
    found = set()
    for index, word in enumerate(words):
        if not word or not word[0].isupper():
            continue
        if _ACRONYM_RE.match(word) and sum(1 for ch in word if ch.isupper()) >= 2:
            found.add(word)
        elif index > 0 and len(word) >= 3 and word.lower() not in _STOPWORDS:
            found.add(word)
    return frozenset(found)


def _ngrams(terms: list[str], n: int) -> list[str]:
    if len(terms) < n:
        return []
    return [" ".join(terms[i : i + n]) for i in range(len(terms) - n + 1)]


@dataclasses.dataclass(frozen=True)
class CorpusStats:
    document_frequency: dict[str, int]
    total_documents: int
    avg_document_length: float
    vocabulary: frozenset[str]


def _normalize_term(term: str, vocabulary: frozenset[str]) -> str:
    """Deterministic, corpus-driven singular/plural merge: a trailing "s" is stripped only when
    the resulting singular form is *itself a real token appearing elsewhere in this corpus* --
    found necessary during the 2026-09-04 recall correction, where "platforms" (rare as an exact
    surface form -- most claims happen to say "platform") scored as if it were a highly diagnostic
    term purely because of this morphological fragmentation, not because the underlying concept
    was actually rare. Requiring the singular form to already exist in the corpus's own vocabulary
    (rather than blind suffix-stripping) is what keeps this safe for domain proper nouns that
    happen to end in "s" -- "Kubernetes" is never touched, because "kubernete" is not a token any
    claim in the corpus actually contains."""
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss") and term[:-1] in vocabulary:
        return term[:-1]
    return term


def normalize_terms(terms: list[str], stats: CorpusStats) -> list[str]:
    """Apply `_normalize_term` using the corpus vocabulary captured in `stats` -- callers must
    normalize both requirement and claim terms through the *same* `stats` before scoring, so a
    plural in the requirement and a singular in a claim (or vice versa) are compared on equal
    footing."""
    return [_normalize_term(term, stats.vocabulary) for term in terms]


def build_corpus_stats(tokenized_documents: list[list[str]]) -> CorpusStats:
    """`tokenized_documents` is one ordered token list (from `tokenize_ordered`) per claim in the
    pool being scored -- unigram document frequency and average length are computed fresh from
    exactly this corpus every time, never cached or hard-coded, so scoring always reflects the
    actual claims available in this run. Only unigram frequency is tracked; phrase rarity is
    derived from it (see `_phrase_weight`) rather than counted separately, since an exact
    multi-word phrase is inherently sparse as a string regardless of how common its individual
    words are and so cannot itself distinguish a common phrase from a rare one. Document frequency
    is computed over *normalized* tokens (see `_normalize_term`) so plural/singular variants of the
    same word are not spuriously counted as separate, differently-rare terms."""
    vocabulary = frozenset(term for document in tokenized_documents for term in document)
    document_frequency: dict[str, int] = {}
    total_length = 0
    for document in tokenized_documents:
        normalized_document = [_normalize_term(term, vocabulary) for term in document]
        total_length += len(normalized_document)
        for term in set(normalized_document):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    total_documents = len(tokenized_documents)
    avg_document_length = (total_length / total_documents) if total_documents else 0.0
    return CorpusStats(
        document_frequency=document_frequency,
        total_documents=total_documents,
        avg_document_length=avg_document_length,
        vocabulary=vocabulary,
    )


def _rarity_weight(df: int, n: int) -> float:
    """The shared rarity transform used for unigram IDF, phrase bonus, and acronym bonus alike:
    standard BM25 log-IDF, further dampened once document frequency exceeds the common-term
    threshold (a corpus-relative ratio, not a fixed count)."""
    raw = math.log((n - df + 0.5) / (df + 0.5) + 1)
    if n and (df / n) > _COMMON_TERM_DF_RATIO_THRESHOLD:
        return raw * _COMMON_TERM_DAMPENING
    return raw


def _idf(term: str, stats: CorpusStats) -> float:
    return _rarity_weight(stats.document_frequency.get(term, 0), stats.total_documents)


def _bm25_term_score(term: str, term_frequency: int, doc_length: int, stats: CorpusStats) -> float:
    if term_frequency == 0:
        return 0.0
    idf = _idf(term, stats)
    length_norm = 1.0 if not stats.avg_document_length else doc_length / stats.avg_document_length
    denominator = term_frequency + _BM25_K1 * (1 - _BM25_B + _BM25_B * length_norm)
    return idf * (term_frequency * (_BM25_K1 + 1)) / denominator


def _phrase_weight(phrase: str, stats: CorpusStats) -> float:
    """A matched phrase's rarity, derived from the average unigram IDF of its own words -- not
    from how often the exact phrase string itself recurs (see module docstring for why that
    alternative was tried and rejected)."""
    words = phrase.split(" ")
    idfs = [_idf(word, stats) for word in words]
    return sum(idfs) / len(idfs) if idfs else 0.0


def _phrase_bonus(requirement_terms: list[str], claim_terms: list[str], stats: CorpusStats) -> float:
    claim_phrase_set = set(_ngrams(claim_terms, 2)) | set(_ngrams(claim_terms, 3))
    if not claim_phrase_set:
        return 0.0
    requirement_phrases = set(_ngrams(requirement_terms, 2)) | set(_ngrams(requirement_terms, 3))
    matched = requirement_phrases & claim_phrase_set
    return sum(PHRASE_BONUS_SCALE * _phrase_weight(phrase, stats) for phrase in matched)


def _acronym_bonus(requirement_text: str, claim_text: str, stats: CorpusStats) -> float:
    requirement_acronyms = extract_acronyms(requirement_text)
    if not requirement_acronyms:
        return 0.0
    claim_original_words = set(_findall_words(claim_text))
    matched = requirement_acronyms & claim_original_words
    total = 0.0
    for acronym in matched:
        df = stats.document_frequency.get(acronym.lower(), 0)
        total += ACRONYM_BONUS_SCALE * _rarity_weight(df, stats.total_documents)
    return total


def score_relevance(
    requirement_text: str,
    requirement_terms: list[str],
    claim_text: str,
    claim_terms: list[str],
    claim_term_frequency: dict[str, int],
    stats: CorpusStats,
) -> float:
    """The single relevance score `candidate_generation.py` sorts candidates by -- BM25 unigram
    weighting plus rarity-weighted exact-phrase and acronym bonuses (see module docstring).
    Callers precompute `requirement_terms`/`claim_terms`/`claim_term_frequency` once per
    requirement/claim rather than repeating tokenization inside a hot loop."""
    bm25_total = sum(
        _bm25_term_score(term, claim_term_frequency.get(term, 0), len(claim_terms), stats)
        for term in set(requirement_terms)
    )
    phrase_bonus = _phrase_bonus(requirement_terms, claim_terms, stats)
    acronym_bonus = _acronym_bonus(requirement_text, claim_text, stats)
    return bm25_total + phrase_bonus + acronym_bonus
