"""Conservative, deterministic claim deduplication (audit hardening, 2026-09-03).

Groups claims that are demonstrably the same underlying content -- never a fuzzy/semantic
similarity test (matching the project-wide convention set by `candidate_memory.services.
engagement_mapping`/`storage.duplicate_group_key`): two claims are merged only if they share an
explicit, non-blank `duplicate_group_key` (the model's own "same fact across passages" signal from
extraction), or, failing that, if their `canonical_text_en` is identical after whitespace/case
normalization. Anything else -- two claims that merely *resemble* each other -- is kept separate,
consistent with D-018's "more, smaller claims, never a fuzzy identity test" precedent.
"""

from __future__ import annotations

import dataclasses

from .retrieve import RetrievedClaim


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


@dataclasses.dataclass(frozen=True)
class DedupedClaim:
    """One representative claim standing in for its whole duplicate group. `grouped_claim_ids`
    is the full, sorted audit trail of every claim merged into this representative -- never
    discarded, so a reviewer can always see which real rows a given piece of retrieved evidence
    actually came from."""

    claim_id: str
    text: str
    claim_type: str
    subject_scope: str
    approved_engagement_ids: tuple[str, ...]
    grouped_claim_ids: tuple[str, ...]


def deduplicate_claims(claims: list[RetrievedClaim]) -> list[DedupedClaim]:
    """Grouping key precedence: a claim's own non-blank `duplicate_group_key` first, else
    normalized `canonical_text_en`. The representative of each group is its lexicographically
    smallest `claim_id` -- a stable, deterministic choice independent of iteration order. Claims
    within a group are merged by union of `approved_engagement_ids` -- disagreement on
    `subject_scope`/engagement is resolved by keeping the representative's own value, since the
    group was already identity-matched by key/text, not by scope.
    """
    groups: dict[str, list[RetrievedClaim]] = {}
    for claim in claims:
        key = claim.duplicate_group_key or ""
        group_key = f"dgk:{key}" if key else f"text:{normalize_text(claim.text)}"
        groups.setdefault(group_key, []).append(claim)

    deduped: list[DedupedClaim] = []
    for members in groups.values():
        members_sorted = sorted(members, key=lambda c: c.claim_id)
        representative = members_sorted[0]
        merged_engagements: set[str] = set()
        for member in members_sorted:
            merged_engagements.update(member.approved_engagement_ids)
        deduped.append(
            DedupedClaim(
                claim_id=representative.claim_id,
                text=representative.text,
                claim_type=representative.claim_type,
                subject_scope=representative.subject_scope,
                approved_engagement_ids=tuple(sorted(merged_engagements)),
                grouped_claim_ids=tuple(sorted(c.claim_id for c in members_sorted)),
            )
        )
    deduped.sort(key=lambda d: d.claim_id)
    return deduped
