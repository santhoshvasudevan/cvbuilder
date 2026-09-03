"""Lifecycle transitions and review actions (docs/ARCHITECTURE.md Sec 4, M0.1 audit fix).

Activation is transactional and concurrency-safe: it locks both the revision being activated and
the currently-active revision (if any) for the duration of one transaction, and the DB itself
enforces "at most one ACTIVE revision" via a partial unique index (`CandidateMemory.Meta`), so
even a locking bug cannot violate the invariant.
"""

from __future__ import annotations

from django.db import transaction

from ..exceptions import InvalidActivationError, RevisionNotEditableError
from ..models import CandidateMemory, ChunkExtractionAttempt, MemoryClaim, MemoryConflict
from .comparable_values import COMPARABLE_CLAIM_TYPES, has_valid_structured_value

# Candidate Memory recovery (2026-09-03): claim types whose presence (or absence) determines
# "employment coverage" -- see activation_blockers' zero-coverage check below.
_EMPLOYMENT_CLAIM_TYPES = ("employment_dates", "employment_location")


def _require_mutable(candidate_memory: CandidateMemory) -> None:
    if not candidate_memory.is_mutable:
        raise RevisionNotEditableError(
            f"CandidateMemory {candidate_memory.pk} is {candidate_memory.status}; not editable."
        )


def confirm_claim(claim: MemoryClaim) -> MemoryClaim:
    _require_mutable(claim.candidate_memory)
    claim.confirmation_status = MemoryClaim.ConfirmationStatus.CONFIRMED
    claim.save()
    return claim


def retire_claim(claim: MemoryClaim) -> MemoryClaim:
    _require_mutable(claim.candidate_memory)
    claim.confirmation_status = MemoryClaim.ConfirmationStatus.RETIRED
    claim.save()
    return claim


def restore_claim(claim: MemoryClaim) -> MemoryClaim:
    """Undo a retirement -- back to UNCONFIRMED (not straight back to CONFIRMED), so it goes
    through review again."""
    _require_mutable(claim.candidate_memory)
    claim.confirmation_status = MemoryClaim.ConfirmationStatus.UNCONFIRMED
    claim.save()
    return claim


def correct_claim(claim: MemoryClaim, **fields) -> MemoryClaim:
    _require_mutable(claim.candidate_memory)
    for field, value in fields.items():
        setattr(claim, field, value)
    claim.save()
    return claim


def resolve_conflict(
    conflict: MemoryConflict, *, resolved_claim: MemoryClaim | None, resolution_note: str
) -> MemoryConflict:
    _require_mutable(conflict.candidate_memory)
    from django.utils import timezone

    conflict.status = MemoryConflict.Status.RESOLVED
    conflict.resolved_claim = resolved_claim
    conflict.operator_resolution = resolution_note
    conflict.resolved_at = timezone.now()
    conflict.save()

    for claim in conflict.involved_claims.all():
        if resolved_claim is not None and claim.pk == resolved_claim.pk:
            continue
        if claim.confirmation_status == MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT:
            claim.confirmation_status = MemoryClaim.ConfirmationStatus.RETIRED
            claim.save()
    return conflict


def dismiss_conflict(conflict: MemoryConflict, *, resolution_note: str) -> MemoryConflict:
    _require_mutable(conflict.candidate_memory)
    from django.utils import timezone

    conflict.status = MemoryConflict.Status.DISMISSED
    conflict.operator_resolution = resolution_note
    conflict.resolved_at = timezone.now()
    conflict.save()
    return conflict


def activation_blockers(
    candidate_memory: CandidateMemory, *, acknowledge_zero_employment_coverage: bool = False
) -> list[str]:
    """Reasons activation must be refused outright.

    Candidate Memory recovery (2026-09-03) strengthens this in three ways, all directly motivated
    by the real revision-1 bootstrap review: (1) any unresolved (`FAILED`) `ChunkExtractionAttempt`
    blocks -- a truncated/failed chunk means real source content was never even attempted/covered,
    which is worse than a resolved conflict and must not be silently activated past; `SUCCESS` and
    `SUPERSEDED` attempts never block, since their content was either stored directly or fully
    recovered by smaller sub-chunk attempts. (2) *every* `OPEN` `MemoryConflict` now blocks
    outright, superseding the earlier D-015 allowance that let an open conflict through as long as
    its claims stayed ineligible -- recorded as a new decision (D-018) rather than silently
    changing an approved design. (3) zero `CONFIRMED` employment-history coverage (no
    `employment_dates`/`employment_location` claim at all) blocks unless the caller explicitly
    passes `acknowledge_zero_employment_coverage=True` -- an intentional, visible operator
    override, never a silent default.
    """
    blockers: list[str] = []

    failed_attempts = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.FAILED
    ).select_related("source_document")
    if failed_attempts.exists():
        examples = ", ".join(
            f"{a.source_document.filename} L{a.start_line}-{a.end_line}" for a in failed_attempts[:5]
        )
        blockers.append(
            f"{failed_attempts.count()} chunk extraction attempt(s) remain FAILED and unresolved "
            f"(e.g. {examples}) -- this source content was never successfully extracted."
        )

    for claim in candidate_memory.claims.filter(
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED
    ).prefetch_related("supports__memory_source_document"):
        supports = list(claim.supports.all())
        if not supports:
            blockers.append(f"{claim.claim_id} is CONFIRMED but has no MemoryClaimSupport.")
            continue
        for support in supports:
            if not support.memory_source_document.verify_content_hash():
                blockers.append(f"{claim.claim_id}: source document content hash mismatch.")
            if not support.verify_against_source():
                blockers.append(f"{claim.claim_id}: support quote does not resolve against source.")
        if claim.claim_type in COMPARABLE_CLAIM_TYPES and not has_valid_structured_value(claim):
            blockers.append(
                f"{claim.claim_id} is CONFIRMED but its comparable-type structured_value is "
                "missing or invalid -- confirming it manually does not bypass this."
            )

    open_conflicts = candidate_memory.conflicts.filter(status=MemoryConflict.Status.OPEN)
    if open_conflicts.exists():
        blockers.append(
            f"{open_conflicts.count()} unresolved conflict(s) remain OPEN "
            f"({', '.join(c.conflict_key for c in open_conflicts)}) -- resolve or dismiss each one "
            "before activating (D-018)."
        )

    if not acknowledge_zero_employment_coverage:
        has_employment_coverage = candidate_memory.claims.filter(
            claim_type__in=_EMPLOYMENT_CLAIM_TYPES,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        ).exists()
        if not has_employment_coverage:
            blockers.append(
                "Zero CONFIRMED employment_dates/employment_location claims exist in this "
                "revision -- no employment-history coverage was extracted. Pass "
                "acknowledge_zero_employment_coverage=True to activate anyway as an explicit "
                "operator override."
            )
    return blockers


def activation_warnings(candidate_memory: CandidateMemory) -> list[str]:
    """Non-blocking, informational notes to show the operator before they activate. Since
    Candidate Memory recovery (2026-09-03) an OPEN conflict is a hard blocker (see
    `activation_blockers`), so this function no longer needs to warn about one -- it now surfaces
    the informational `SUPERSEDED` chunk-attempt count instead (truncated chunks whose content was
    fully recovered by smaller sub-chunk attempts -- never a blocker, but worth the operator
    knowing the original chunking needed automatic recovery)."""
    warnings: list[str] = []
    superseded = candidate_memory.chunk_attempts.filter(status=ChunkExtractionAttempt.Status.SUPERSEDED)
    if superseded.exists():
        warnings.append(
            f"{superseded.count()} chunk(s) were truncated and automatically recovered via "
            "smaller sub-chunk attempts during this build."
        )
    return warnings


@transaction.atomic
def activate_revision(
    candidate_memory: CandidateMemory, *, acknowledge_zero_employment_coverage: bool = False
) -> CandidateMemory:
    locked = CandidateMemory.objects.select_for_update().get(pk=candidate_memory.pk)
    if locked.status != CandidateMemory.Status.NEEDS_REVIEW:
        raise InvalidActivationError(
            f"CandidateMemory {locked.pk} is {locked.status}; only a NEEDS_REVIEW revision can be activated."
        )

    blockers = activation_blockers(
        locked, acknowledge_zero_employment_coverage=acknowledge_zero_employment_coverage
    )
    if blockers:
        raise InvalidActivationError("Activation blocked: " + "; ".join(blockers))

    previous_active = (
        CandidateMemory.objects.select_for_update()
        .filter(status=CandidateMemory.Status.ACTIVE)
        .exclude(pk=locked.pk)
        .first()
    )
    if previous_active is not None:
        previous_active.status = CandidateMemory.Status.SUPERSEDED
        previous_active.save()

    from django.utils import timezone

    locked.status = CandidateMemory.Status.ACTIVE
    locked.activated_at = timezone.now()
    locked.save()
    return locked
