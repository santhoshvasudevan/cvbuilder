"""Lifecycle transitions and review actions (docs/ARCHITECTURE.md Sec 4, M0.1 audit fix).

Activation is transactional and concurrency-safe: it locks both the revision being activated and
the currently-active revision (if any) for the duration of one transaction, and the DB itself
enforces "at most one ACTIVE revision" via a partial unique index (`CandidateMemory.Meta`), so
even a locking bug cannot violate the invariant.
"""

from __future__ import annotations

from django.db import transaction

from ..exceptions import InvalidActivationError, RevisionNotEditableError
from ..models import CandidateMemory, MemoryClaim, MemoryConflict
from .comparable_values import COMPARABLE_CLAIM_TYPES, has_valid_structured_value


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


def activation_blockers(candidate_memory: CandidateMemory) -> list[str]:
    """Reasons activation must be refused outright. An unresolved conflict is *not* necessarily
    a blocker (see `activation_warnings`) -- but a conflict whose involved claims are not all
    safely ineligible *is* a blocker, since that would let unsupported content become eligible."""
    blockers: list[str] = []

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

    for conflict in candidate_memory.conflicts.filter(status=MemoryConflict.Status.OPEN):
        for claim in conflict.involved_claims.all():
            if claim.confirmation_status not in (
                MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT,
                MemoryClaim.ConfirmationStatus.RETIRED,
                MemoryClaim.ConfirmationStatus.UNCONFIRMED,
            ):
                blockers.append(
                    f"Conflict {conflict.conflict_key} is OPEN but {claim.claim_id} is "
                    f"{claim.confirmation_status}, not blocked/ineligible."
                )
    return blockers


def activation_warnings(candidate_memory: CandidateMemory) -> list[str]:
    """Non-blocking warnings to show the operator before they activate -- primarily: unresolved
    conflicts being excluded from this activation."""
    warnings: list[str] = []
    for conflict in candidate_memory.conflicts.filter(status=MemoryConflict.Status.OPEN):
        claim_ids = ", ".join(c.claim_id for c in conflict.involved_claims.all())
        warnings.append(
            f"Unresolved conflict '{conflict.conflict_key}' remains open; affected claims "
            f"({claim_ids}) are excluded from this activation and will stay ineligible."
        )
    return warnings


@transaction.atomic
def activate_revision(candidate_memory: CandidateMemory) -> CandidateMemory:
    locked = CandidateMemory.objects.select_for_update().get(pk=candidate_memory.pk)
    if locked.status != CandidateMemory.Status.NEEDS_REVIEW:
        raise InvalidActivationError(
            f"CandidateMemory {locked.pk} is {locked.status}; only a NEEDS_REVIEW revision can be activated."
        )

    blockers = activation_blockers(locked)
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
