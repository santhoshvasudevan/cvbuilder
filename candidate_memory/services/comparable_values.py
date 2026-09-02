"""Canonical, validated comparison payloads for deterministic conflict detection (audit repair:
the extraction-to-conflict pipeline was previously disconnected -- `MemoryClaim.structured_value`
was never populated from real extraction, so `services/conflicts.py` could never actually detect
a real contradiction; it only worked against hand-built test fixtures).

This module is the single source of truth for:
- which `claim_type` values are "comparable" (subject to deterministic conflict detection) and
  what typed Pydantic shape (from `schemas.py`) each one's `structured_value` must validate as;
- turning a validated `ExtractedItem`'s typed field into the plain dict persisted in
  `MemoryClaim.structured_value` (`comparison_payload_for_item`);
- fail-closed structural comparison (`structural_key`) -- a comparable-type claim whose
  `structured_value` is missing or does not validate gets a key that can never spuriously match
  any other claim's key (`("__INVALID__", claim.pk)`), rather than a hole like `(None, None)` that
  would silently agree with every other equally-unpopulated claim;
- `has_valid_structured_value`, used by `services/confirmation.py` to refuse auto-confirmation of
  a comparable-type claim whose comparison data is missing/malformed.

`scope_key` (the audit's term for "which employer/client this comparison data is about") is
deliberately *not* duplicated inside `structured_value` -- `MemoryClaim.subject_scope` is already
that scope key (a real column `services/conflicts.py` groups by), so echoing it a second time
inside the JSON payload would just be a second, independently-LLM-supplied copy that could drift
out of sync with the first. One source of truth is safer than two that are supposed to agree.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..models import MemoryClaim
from ..schemas import EmploymentDatesValue, EmploymentLocationValue, ExtractedItem, LanguageProficiencyValue

COMPARABLE_SCHEMAS: dict[str, type] = {
    "employment_dates": EmploymentDatesValue,
    "employment_location": EmploymentLocationValue,
    "language_proficiency": LanguageProficiencyValue,
}

COMPARABLE_CLAIM_TYPES = frozenset(COMPARABLE_SCHEMAS)

_INVALID_SENTINEL = "__INVALID_STRUCTURED_VALUE__"


def comparison_payload_for_item(item: ExtractedItem) -> dict:
    """The plain dict to persist as `MemoryClaim.structured_value` for a newly-created claim.
    Returns `{}` (deliberately empty, never a guessed/defaulted shape) when `item.claim_type` is a
    comparable type but the matching typed field was left null by extraction -- callers must not
    interpret `{}` as "no conflict data needed," only as "this failed closed and needs review."
    """
    if item.claim_type == "employment_dates" and item.employment_dates is not None:
        return item.employment_dates.model_dump()
    if item.claim_type == "employment_location" and item.employment_location is not None:
        return item.employment_location.model_dump()
    if item.claim_type == "language_proficiency" and item.language_proficiency is not None:
        return item.language_proficiency.model_dump()
    return {}


def has_valid_structured_value(claim: MemoryClaim) -> bool:
    """True if `claim.claim_type` is not a comparable type (vacuously fine -- this check does not
    apply), or if it is and `claim.structured_value` validates against the matching schema."""
    schema = COMPARABLE_SCHEMAS.get(claim.claim_type)
    if schema is None:
        return True
    try:
        schema.model_validate(claim.structured_value)
    except ValidationError:
        return False
    return True


def structural_key(claim: MemoryClaim) -> tuple:
    """Deterministic comparison key consumed only by validated data (audit repair). A comparable
    claim with missing/malformed `structured_value` gets a key unique to that claim's own primary
    key -- it can never silently equal another claim's key, so it is never treated as "agreeing"
    with anything, and it is excluded from auto-confirmation by `has_valid_structured_value`."""
    schema = COMPARABLE_SCHEMAS.get(claim.claim_type)
    if schema is None:
        return ()
    try:
        payload = schema.model_validate(claim.structured_value)
    except ValidationError:
        return (_INVALID_SENTINEL, claim.pk)

    if schema is EmploymentDatesValue:
        return (
            payload.start_year,
            payload.start_month,
            payload.end_status,
            payload.end_year,
            payload.end_month,
        )
    if schema is EmploymentLocationValue:
        return (payload.city.strip().lower(),)
    if schema is LanguageProficiencyValue:
        # Conflict comparison is scoped to the *attained* level only -- an in-progress target
        # level pursued alongside a stable attained level is not a contradiction (operator
        # resolution 2026-09-02, item 5/6: B1 attained + B2 in progress is one consistent fact).
        return (payload.language.strip().lower(), payload.attained_level)
    return ()
