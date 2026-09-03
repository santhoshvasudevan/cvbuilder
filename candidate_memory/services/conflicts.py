"""Deterministic conflict detection for well-defined, comparable fact categories (employment
dates, employment location, language proficiency level), plus operator-resolution precedence.

This is deliberately deterministic rather than an LLM pass, so automated tests can assert exact
behavior without live credentials. The `ConflictCandidate` Pydantic contract in
`candidate_memory.schemas` remains available for a future, optional LLM-assisted second pass over
ambiguous free-text cases this detector does not cover -- it is not required for the categories
below, which is what the current operator-resolution scenarios need (requirements.md Sec 7).
"""

from __future__ import annotations

from collections import defaultdict

from django.utils import timezone

from ..models import CandidateMemory, MemoryClaim, MemoryConflict
from .comparable_values import COMPARABLE_CLAIM_TYPES, has_valid_structured_value
from .comparable_values import structural_key as _structural_key

# Convention shared with the bootstrap command: 0 = OPERATOR_UPDATE (highest precedence).
OPERATOR_UPDATE_PRECEDENCE = 0


def _min_source_precedence(claim: MemoryClaim) -> int:
    precedences = [s.memory_source_document.precedence for s in claim.supports.all()]
    return min(precedences) if precedences else 10_000


def detect_and_resolve_conflicts(candidate_memory: CandidateMemory) -> list[MemoryConflict]:
    # Audit repair (fail-closed): a claim whose structured_value is missing/malformed is excluded
    # from grouping entirely, not given a key that could spuriously agree *or* disagree with a
    # sibling claim's genuinely valid data. It stays UNCONFIRMED (services/confirmation.py refuses
    # to auto-confirm it) and is counted separately (build_summary["structured_value_issues"]) --
    # it must never silently affect a different, validly-supported claim's eligibility.
    claims = [
        claim
        for claim in candidate_memory.claims.filter(
            claim_type__in=COMPARABLE_CLAIM_TYPES
        ).prefetch_related("supports__memory_source_document")
        if has_valid_structured_value(claim)
    ]
    groups: dict[tuple, list[MemoryClaim]] = defaultdict(list)
    for claim in claims:
        groups[(claim.subject_scope.strip().lower(), claim.claim_type)].append(claim)

    created: list[MemoryConflict] = []
    for (subject_scope, claim_type), group_claims in groups.items():
        if len(group_claims) < 2:
            continue
        by_structural_key: dict[tuple, list[MemoryClaim]] = defaultdict(list)
        for claim in group_claims:
            by_structural_key[_structural_key(claim)].append(claim)
        if len(by_structural_key) < 2:
            continue  # every claim in this group agrees structurally -- no conflict

        conflict_key = f"{claim_type}:{subject_scope}"
        # Idempotency (Candidate Memory recovery, 2026-09-03): a conflict_key that already has a
        # MemoryConflict row -- of any status, including an already-RESOLVED or DISMISSED one --
        # is never re-created. Re-running detection after new content is added (e.g. a targeted
        # chunk retry) must never duplicate a conflict for the same revision and competing claims,
        # and must never re-litigate/reopen a decision the operator (or an earlier auto-resolution)
        # already made -- new claims that happen to join this same structural group are left
        # exactly as they already are (fail closed: unconfirmed, not silently retired or reopened).
        if candidate_memory.conflicts.filter(conflict_key=conflict_key).exists():
            continue

        winner = min(group_claims, key=_min_source_precedence)
        winner_precedence = _min_source_precedence(winner)
        losers = [c for c in group_claims if c is not winner]

        conflict = MemoryConflict.objects.create(
            candidate_memory=candidate_memory,
            conflict_key=conflict_key,
            description=(
                f"Multiple conflicting '{claim_type}' facts for '{subject_scope}': "
                + "; ".join(f"{c.claim_id}={_structural_key(c)!r}" for c in group_claims)
            ),
        )
        conflict.involved_claims.set(group_claims)

        if winner_precedence == OPERATOR_UPDATE_PRECEDENCE and any(
            _min_source_precedence(c) > OPERATOR_UPDATE_PRECEDENCE for c in losers
        ):
            # The operator already made this call explicitly -- record it as resolved (visible,
            # not silent), rather than leaving it open for a decision that has already been made.
            conflict.status = MemoryConflict.Status.RESOLVED
            conflict.resolved_claim = winner
            conflict.operator_resolution = (
                f"Auto-resolved by dated operator resolution: '{winner.canonical_text_en}' "
                f"(claim {winner.claim_id}) supersedes conflicting statement(s) from "
                + ", ".join(c.claim_id for c in losers) + "."
            )
            conflict.resolved_at = timezone.now()
            conflict.save()
            for loser in losers:
                loser.confirmation_status = MemoryClaim.ConfirmationStatus.RETIRED
                loser.save()
        else:
            for claim in group_claims:
                claim.confirmation_status = MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT
                claim.save()

        created.append(conflict)
    return created
