"""The disposition-coverage validator (M5 point 5/6/7, D-014, NFR-002).

Two responsibilities, deliberately kept together because they are two halves of the same
guarantee -- "every relevant requirement gets exactly one honest disposition, backed by real
evidence or none at all":

1. `sanitize_items` -- an evidence-attachment/fabrication check, never a text/embedding-similarity
   check (D-014): any cited claim_id/engagement_id that was not actually part of the bounded
   context retrieved for this run is dropped (an LLM cannot manufacture evidence by citing an ID
   it was never given); a MATCH/PARTIAL left with no surviving evidence is downgraded to UNKNOWN,
   never silently kept as if it were still supported, and never silently deleted either -- the
   downgrade and its reason are recorded in the row's own explanation text so a human reviewer at
   Gate 1 sees exactly what happened.
2. `ensure_full_coverage` -- guarantees exactly one row per relevant JobRequirement. A requirement
   the model never addressed becomes an explicit UNKNOWN row rather than silently having no row at
   all (GAP/UNKNOWN must never simply disappear); a requirement addressed more than once keeps only
   its first occurrence.
"""

from __future__ import annotations

import dataclasses

VALID_DISPOSITIONS = {"MATCH", "PARTIAL", "GAP", "UNKNOWN"}


@dataclasses.dataclass
class AssessmentItemData:
    requirement_id: str
    disposition: str
    explanation: str
    gap_or_limitation: str
    supporting_memory_claim_ids: list[str]
    supporting_engagement_ids: list[str]


def sanitize_items(
    items: list[AssessmentItemData],
    *,
    valid_claim_ids: set[str],
    valid_engagement_ids: set[str],
) -> list[AssessmentItemData]:
    sanitized: list[AssessmentItemData] = []
    for item in items:
        claim_ids = [c for c in item.supporting_memory_claim_ids if c in valid_claim_ids]
        engagement_ids = [e for e in item.supporting_engagement_ids if e in valid_engagement_ids]
        dropped_evidence = (
            len(claim_ids) != len(item.supporting_memory_claim_ids)
            or len(engagement_ids) != len(item.supporting_engagement_ids)
        )

        disposition = item.disposition
        note = ""
        if disposition not in VALID_DISPOSITIONS:
            note = f" (disposition {disposition!r} was not a recognized value; downgraded to UNKNOWN)"
            disposition = "UNKNOWN"
        elif disposition in ("MATCH", "PARTIAL") and not claim_ids and not engagement_ids:
            note = (
                " (disposition downgraded to UNKNOWN: no cited evidence could be verified "
                "against the retrieved context)"
            )
            disposition = "UNKNOWN"
        elif dropped_evidence:
            note = " (some cited evidence IDs were not part of the retrieved context and were discarded)"

        sanitized.append(
            AssessmentItemData(
                requirement_id=item.requirement_id,
                disposition=disposition,
                explanation=(item.explanation or "") + note,
                gap_or_limitation=item.gap_or_limitation,
                supporting_memory_claim_ids=claim_ids,
                supporting_engagement_ids=engagement_ids,
            )
        )
    return sanitized


def ensure_full_coverage(
    items: list[AssessmentItemData], ordered_requirement_ids: list[str]
) -> list[AssessmentItemData]:
    by_id: dict[str, AssessmentItemData] = {}
    for item in items:
        if item.requirement_id in ordered_requirement_ids and item.requirement_id not in by_id:
            by_id[item.requirement_id] = item

    result: list[AssessmentItemData] = []
    for requirement_id in ordered_requirement_ids:
        if requirement_id in by_id:
            result.append(by_id[requirement_id])
        else:
            result.append(
                AssessmentItemData(
                    requirement_id=requirement_id,
                    disposition="UNKNOWN",
                    explanation="Agent Candidate did not produce an assessment for this requirement.",
                    gap_or_limitation="No assessment was produced for this requirement.",
                    supporting_memory_claim_ids=[],
                    supporting_engagement_ids=[],
                )
            )
    return result
