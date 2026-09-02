"""Deterministic `subject_scope` normalization (extraction-quality repair, 2026-09-02).

`MemoryClaim.subject_scope` is a **stable, normalized subject identifier** -- what entity a claim
is about, used to group duplicate/corroborating expressions of the same fact
(`storage.duplicate_group_key`) and to group comparable claims for conflict detection
(`services/conflicts.py`). It is **not** free-form prose describing the claim.

Canonical convention (reuse this everywhere; do not introduce a competing format):

    organization:<normalized client organization>   -- employment_dates/employment_location (or
                                                        an organization-scoped achievement) when a
                                                        client assignment is explicitly identified
    organization:<normalized legal employer>          -- same claim types with no distinct client
                                                        assignment (ordinary direct employment)
    language:<normalized language name>               -- language_proficiency claims
    skill:<normalized skill name>                     -- skill/capability claims
    career                                            -- career-wide evidence, only when no
                                                        narrower, explicitly supported subject
                                                        exists

Normalization here never invents an employer, client, language, or skill that isn't already
present in the extracted item's own fields -- it only reshapes what was already extracted into
the canonical form. If nothing safe can be derived, it returns `None`, and the caller
(`services/storage.py`) must fail closed exactly as it already does for a genuinely missing
subject_scope. This module never consults any external knowledge base -- only the one
`ExtractedItem` it's given.
"""

from __future__ import annotations

import re

from ..schemas import ExtractedItem

_MAX_IDENTIFIER_LENGTH = 80
# A period/!/? followed by a space and a capital letter is a strong, cheap signal of multi-clause
# prose rather than a short identifier -- deliberately conservative (a few false negatives on
# legitimate short identifiers are safer here than false positives that admit prose as a "scope").
_SENTENCE_LIKE = re.compile(r"[.!?]\s+[A-Z]")


def _normalize_token(raw: str) -> str:
    return " ".join(raw.strip().lower().split())


def is_malformed_subject_scope(value: str | None) -> bool:
    """True if `value` is never safe to store as a subject identifier, regardless of
    convention: empty, prose-shaped, too long, or multi-line."""
    if not value or not value.strip():
        return True
    value = value.strip()
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        return True
    if "\n" in value:
        return True
    if _SENTENCE_LIKE.search(value):
        return True
    return False


def normalize_subject_scope(item: ExtractedItem) -> str | None:
    """Returns the subject_scope to use for storage, or `None` if nothing safe could be
    determined -- the caller must then treat this exactly like a missing subject_scope and fail
    closed.

    A model-provided value that is already well-formed (canonical, or a short plain identifier)
    is always preserved exactly as given -- this function never second-guesses or reshapes a
    valid model-provided value, only fills a genuine gap (missing or malformed subject_scope)
    from the item's own structured/extracted fields. This is deliberately conservative: real
    corpora already contain plain, non-canonical identifiers (e.g. "Continental",
    "German language") predating this convention, and those must keep meaning exactly what they
    already mean rather than being silently rewritten.
    """
    raw = (item.subject_scope or "").strip()

    if raw and not is_malformed_subject_scope(raw):
        return raw

    # raw is missing or malformed from here -- attempt derivation from structured/extracted data.
    if item.claim_type in ("employment_dates", "employment_location"):
        org = (item.client_organization or item.legal_employer or "").strip()
        if org:
            return f"organization:{_normalize_token(org)}"
        return None  # no organization identity anywhere in the item -- cannot derive

    if item.claim_type == "language_proficiency" and item.language_proficiency is not None:
        language = (item.language_proficiency.language or "").strip()
        if language:
            return f"language:{_normalize_token(language)}"
        return None

    # Any other EVIDENCE claim_type (e.g. an organization-scoped achievement): derive from
    # whichever organization identity the item itself supplied, if any. There is no dedicated
    # structured field carrying a skill/technology name today, so a "skill" claim with a missing
    # or malformed subject_scope cannot be safely derived from anything else in the item -- it
    # correctly falls through to `None` (fail closed) rather than being guessed.
    org = (item.client_organization or item.legal_employer or "").strip()
    if org:
        return f"organization:{_normalize_token(org)}"

    return None
