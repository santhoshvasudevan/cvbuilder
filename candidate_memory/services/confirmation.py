"""Auto-confirmation policy for the initial operator-approved bootstrap (requirements.md Sec 7).

A factual claim may begin CONFIRMED only when every one of these holds:

- its source is operator-approved (`MemorySourceDocument.trust_status == OPERATOR_APPROVED`);
- extraction schema was valid (guaranteed by construction: `classification.validate_item` already
  ran before a MemoryClaim row could exist);
- content is classified EVIDENCE (also guaranteed by construction -- CandidateRule holds the
  constraint/positioning planes instead);
- it is resume_eligible;
- exact source support resolves successfully, and content hash + line range validate
  (`MemoryClaimSupport.verify_against_source` / `MemorySourceDocument.verify_content_hash`);
- no unresolved contradiction affects it (`confirmation_status` is not already BLOCKED_CONFLICT --
  conflict detection must run *before* this function);
- no stronger operator resolution supersedes it (claims retired by conflict resolution are
  already RETIRED, and are skipped by the UNCONFIRMED-only filter below).

Source approval alone is not extraction approval: every condition above must hold, never just the
source's trust status.
"""

from __future__ import annotations

from ..models import CandidateMemory, MemoryClaim
from .comparable_values import COMPARABLE_CLAIM_TYPES, has_valid_structured_value


def evaluate_auto_confirmation(candidate_memory: CandidateMemory) -> int:
    """Confirm every eligible, currently-UNCONFIRMED claim on this revision. Must run *after*
    conflict detection so BLOCKED_CONFLICT/RETIRED claims are correctly excluded. Returns the
    count of claims confirmed."""
    confirmed_count = 0
    claims = candidate_memory.claims.filter(
        confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED
    ).prefetch_related("supports__memory_source_document")

    for claim in claims:
        if not claim.resume_eligible:
            continue
        # Audit repair: a comparable-type claim (employment_dates/employment_location/
        # language_proficiency) whose structured_value is missing or fails to validate must fail
        # closed -- it must never auto-confirm merely because its source was approved.
        if claim.claim_type in COMPARABLE_CLAIM_TYPES and not has_valid_structured_value(claim):
            continue
        supports = list(claim.supports.all())
        if not supports:
            continue
        all_supports_valid = all(
            support.memory_source_document.trust_status == "OPERATOR_APPROVED"
            and support.memory_source_document.verify_content_hash()
            and support.verify_against_source()
            for support in supports
        )
        if not all_supports_valid:
            continue
        claim.confirmation_status = MemoryClaim.ConfirmationStatus.CONFIRMED
        claim.save()
        confirmed_count += 1
    return confirmed_count
