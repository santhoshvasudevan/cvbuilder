"""Post-extraction classification validation (requirements.md Sec 16).

Schema validity (an `ExtractedItem` that parses) is necessary but never sufficient -- source
approval is not extraction approval, and the LLM's own plane assignment is not trusted blindly.
This module is the one place that decides whether an item is actually well-formed enough to
become a MemoryClaim or a CandidateRule.
"""

from __future__ import annotations

from ..schemas import ContentPlane, ExperienceLevel, ExtractedItem

# ARCHITECTURE.md's MemoryClaim invariant: "A resume_eligible claim with experience_level of
# AWARENESS or LEARNING must not be presented as PROFESSIONAL_DELIVERY or higher" -- enforced here
# as a schema-level fact (not prompting alone) by rejecting delivery/production-grade phrasing on
# a claim the model itself only classified as AWARENESS/LEARNING.
_INFLATION_MARKERS = (
    "delivered",
    "led the",
    "owned the",
    "in production",
    "shipped to production",
    "architected",
    "production-grade",
)


class ClassificationError(Exception):
    pass


def validate_item(item: ExtractedItem) -> None:
    if item.plane == ContentPlane.EVIDENCE:
        if not item.claim_type or not item.subject_scope:
            raise ClassificationError(
                "EVIDENCE-plane item is missing claim_type/subject_scope; cannot store as a "
                "MemoryClaim."
            )
        if not item.canonical_text_en.strip():
            raise ClassificationError("EVIDENCE-plane item has empty canonical_text_en.")
        if item.experience_level in (ExperienceLevel.AWARENESS, ExperienceLevel.LEARNING):
            lowered = item.canonical_text_en.lower()
            if any(marker in lowered for marker in _INFLATION_MARKERS):
                raise ClassificationError(
                    f"Experience level inflation: item is classified {item.experience_level.value} "
                    "but canonical_text_en uses professional-delivery phrasing."
                )
        # Extraction-quality repair (2026-09-02): a staffing/consultancy split is only ever
        # legitimate when the source distinguishes BOTH the legal employer and the client it
        # assigned the candidate to -- an item with exactly one of the two set is an inconsistent
        # extraction (either a half-completed split or an invented client/employer), never
        # silently completed from the other field. Fail closed rather than guess.
        if bool(item.legal_employer) != bool(item.client_organization):
            raise ClassificationError(
                "legal_employer and client_organization must both be set or both left unset -- "
                "the source must explicitly distinguish a staffing/consultancy split before "
                "either field is populated; never invent one from the other."
            )
    else:
        if item.rule_type is None:
            raise ClassificationError(
                f"{item.plane.value}-plane item is missing rule_type; cannot store as a "
                "CandidateRule."
            )
        if item.resume_eligible:
            raise ClassificationError(
                f"{item.plane.value}-plane item must never be resume_eligible=True -- only an "
                "EVIDENCE item can become a resume-eligible MemoryClaim; a CONSTRAINT/POSITIONING "
                "item becomes a CandidateRule and is never itself resume content."
            )

    if not item.support.quote.strip():
        raise ClassificationError("Item has an empty support quote -- no provenance to validate.")
    if item.support.start_line > item.support.end_line:
        raise ClassificationError("Item support has start_line > end_line.")
