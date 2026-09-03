"""The no-fabrication validator (NFR-001, D-007/D-014/D-019, `docs/RESUME_OUTPUT_STRUCTURE.md`
Sec 3). Runs against Agent Builder's *structured* output, before any markdown is ever rendered.

Unlike `candidate_matching.validators.disposition_coverage` (M5), which downgrades an individual
unverifiable item to UNKNOWN and keeps going, this validator fails the **entire** build closed:
`docs/RESUME_OUTPUT_STRUCTURE.md` Sec 3/6 specifies a structured draft with any invalid element is
"rejected... before any markdown is ever rendered" -- a resume silently missing bullets because
some of them failed evidence checks would look thin and unexplained to the operator with no
indication anything went wrong, whereas a clear, whole-build rejection naming exactly what failed
gives the operator (or a re-run) something actionable. Nothing is ever persisted for a build that
fails this check.

This is an evidence-attachment/eligibility check -- existence, confirmation, resume-eligibility,
and retrieval-context-membership of every cited ID -- never a text/embedding-similarity check
(D-014 explicitly rules that out as the fabrication test). Whether generated wording *fairly
represents* the cited evidence remains Gate 2's human-review responsibility.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from candidate_matching.services.retrieve import RetrievalContext
from candidate_memory.services.static_profile_boundary import (
    UnknownOrUnapprovedEngagementError,
    resolve_approved_engagement,
)

from ..models import ResumeElement
from ..schemas import AgentBuilderOutput


class NoFabricationError(Exception):
    def __init__(self, failures: list[str]):
        self.failures = list(failures)
        super().__init__(
            "Agent Builder output failed no-fabrication validation "
            f"({len(self.failures)} issue(s)): {'; '.join(self.failures)}"
        )


@dataclasses.dataclass(frozen=True)
class ValidatedElement:
    section: str
    engagement_id: str
    order: int
    text: str
    supporting_memory_claim_ids: list[str]
    matched_job_requirement_ids: list[str]


def validate_and_flatten(
    ab_output: AgentBuilderOutput, *, retrieval: RetrievalContext
) -> list[ValidatedElement]:
    valid_claim_ids = set(retrieval.claim_ids)
    valid_engagement_ids = set(retrieval.engagement_ids)
    failures: list[str] = []
    elements: list[ValidatedElement] = []
    order_counters: dict[tuple[str, str], int] = defaultdict(int)

    def add(section: str, text: str, claim_ids: list[str], jr_ids: list[str], engagement_id: str = ""):
        if not claim_ids:
            failures.append(f"{section} element {text[:60]!r} has no supporting_memory_claim_ids.")
            return
        unknown = [claim_id for claim_id in claim_ids if claim_id not in valid_claim_ids]
        if unknown:
            failures.append(
                f"{section} element {text[:60]!r} cites claim id(s) not in the retrieved context: {unknown}"
            )
            return
        key = (section, engagement_id)
        order_counters[key] += 1
        elements.append(
            ValidatedElement(
                section=section,
                engagement_id=engagement_id,
                order=order_counters[key],
                text=text,
                supporting_memory_claim_ids=list(claim_ids),
                matched_job_requirement_ids=list(jr_ids),
            )
        )

    for item in ab_output.summary_elements:
        add(
            ResumeElement.Section.SUMMARY,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )

    for section in ab_output.experience_sections:
        try:
            resolve_approved_engagement(section.engagement_id)
        except UnknownOrUnapprovedEngagementError as exc:
            failures.append(str(exc))
            continue
        if section.engagement_id not in valid_engagement_ids:
            failures.append(
                f"Engagement {section.engagement_id!r} was not part of the retrieved context for this run."
            )
            continue
        for bullet in section.bullets:
            add(
                ResumeElement.Section.EXPERIENCE_BULLET,
                bullet.text,
                bullet.supporting_memory_claim_ids,
                bullet.matched_job_requirement_ids,
                engagement_id=section.engagement_id,
            )

    for item in ab_output.positioning_themes:
        add(
            ResumeElement.Section.POSITIONING_THEME,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )
    for item in ab_output.achievements:
        add(
            ResumeElement.Section.ACHIEVEMENT,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )
    for item in ab_output.selected_skills:
        add(
            ResumeElement.Section.SKILL,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )
    for item in ab_output.certifications:
        add(
            ResumeElement.Section.CERTIFICATION,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )
    for item in ab_output.languages:
        add(
            ResumeElement.Section.LANGUAGE,
            item.text, item.supporting_memory_claim_ids, item.matched_job_requirement_ids,
        )

    if failures:
        raise NoFabricationError(failures)
    return elements
