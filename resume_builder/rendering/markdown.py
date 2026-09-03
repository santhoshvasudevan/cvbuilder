"""Deterministic v1 markdown rendering (`docs/RESUME_OUTPUT_STRUCTURE.md` Sec 4), run only after
`validators/no_fabrication.py` has passed. Every employment header comes exclusively from
`services/static_profile_boundary.render_engagement_header`, resolved fresh from the database by
`engagement_id` -- never anything Agent Builder produced (D-019).

Achievement placement (Sec 4's "avoid unnecessary duplication... left open for a future
iteration"): a deterministic v1 policy, not a semantic/fuzzy dedup. An achievement is treated as
"naturally covered" when its own supporting claim IDs overlap with any already-placed summary or
experience bullet's claim IDs, and is skipped. An uncovered achievement is placed as an extra
bullet under the one engagement every one of its claims maps to (via that claim's own approved
engagement mapping, from the same retrieval context used to build this draft); an achievement
whose claims map to zero or more than one engagement is placed under Professional Summary instead
of being silently dropped -- content is never lost, even when the "ideal" placement is ambiguous.
"""

from __future__ import annotations

from candidate_matching.services.retrieve import RetrievalContext
from candidate_memory.models import CareerEngagement
from candidate_memory.services.static_profile_boundary import render_engagement_header

from ..models import ResumeElement
from ..validators.no_fabrication import ValidatedElement


def _engagement_sort_key(engagement: CareerEngagement) -> tuple[int, int]:
    if engagement.end_status == CareerEngagement.EndStatus.PRESENT:
        end_index = 10**9
    elif engagement.end_status == CareerEngagement.EndStatus.KNOWN:
        end_index = engagement.end_year * 12 + (engagement.end_month or 12)
    else:
        end_index = -1
    start_index = engagement.start_year * 12 + (engagement.start_month or 1)
    return (-end_index, -start_index)


def _place_achievements(
    elements: list[ValidatedElement], retrieval: RetrievalContext
) -> list[ValidatedElement]:
    covered_claim_ids: set[str] = set()
    kept: list[ValidatedElement] = []
    achievements: list[ValidatedElement] = []
    for element in elements:
        if element.section == ResumeElement.Section.ACHIEVEMENT:
            achievements.append(element)
            continue
        if element.section in (ResumeElement.Section.SUMMARY, ResumeElement.Section.EXPERIENCE_BULLET):
            covered_claim_ids.update(element.supporting_memory_claim_ids)
        kept.append(element)

    claim_engagement = {claim.claim_id: claim.engagement_id for claim in retrieval.claims}
    order_counters: dict[tuple[str, str], int] = {}
    for element in kept:
        order_counters[(element.section, element.engagement_id)] = max(
            order_counters.get((element.section, element.engagement_id), 0), element.order
        )

    for achievement in achievements:
        if covered_claim_ids.intersection(achievement.supporting_memory_claim_ids):
            continue
        mapped_engagements = {
            claim_engagement.get(claim_id) for claim_id in achievement.supporting_memory_claim_ids
        }
        mapped_engagements.discard(None)
        if len(mapped_engagements) == 1:
            section, engagement_id = ResumeElement.Section.EXPERIENCE_BULLET, next(iter(mapped_engagements))
        else:
            section, engagement_id = ResumeElement.Section.SUMMARY, ""
        key = (section, engagement_id)
        order_counters[key] = order_counters.get(key, 0) + 1
        kept.append(
            ValidatedElement(
                section=section,
                engagement_id=engagement_id,
                order=order_counters[key],
                text=achievement.text,
                supporting_memory_claim_ids=achievement.supporting_memory_claim_ids,
                matched_job_requirement_ids=achievement.matched_job_requirement_ids,
            )
        )
    return kept


def render_resume_markdown(
    elements: list[ValidatedElement],
    *,
    recommended_title: str,
    retrieval: RetrievalContext,
    language: str = "en",
) -> str:
    elements = _place_achievements(elements, retrieval)

    def section_items(section: str, engagement_id: str = "") -> list[ValidatedElement]:
        return sorted(
            (e for e in elements if e.section == section and e.engagement_id == engagement_id),
            key=lambda e: e.order,
        )

    lines = [f"# {recommended_title}", "", "## Professional Summary", ""]
    for item in section_items(ResumeElement.Section.SUMMARY):
        lines.append(f"- {item.text}")
    lines.append("")

    lines.append("## Professional Experience")
    lines.append("")
    used_engagement_ids = {
        e.engagement_id for e in elements
        if e.section == ResumeElement.Section.EXPERIENCE_BULLET and e.engagement_id
    }
    engagements = list(CareerEngagement.objects.filter(engagement_id__in=used_engagement_ids))
    for engagement in sorted(engagements, key=_engagement_sort_key):
        header = render_engagement_header(engagement.engagement_id, language=language)
        lines.append(f"### {header}")
        for item in section_items(ResumeElement.Section.EXPERIENCE_BULLET, engagement.engagement_id):
            lines.append(f"- {item.text}")
        lines.append("")

    lines.append("## Key Skills")
    lines.append("")
    skills = [item.text for item in section_items(ResumeElement.Section.SKILL)]
    if skills:
        lines.append(", ".join(skills))
    lines.append("")

    certifications = section_items(ResumeElement.Section.CERTIFICATION)
    if certifications:
        lines.append("## Certifications")
        lines.append("")
        for item in certifications:
            lines.append(f"- {item.text}")
        lines.append("")

    languages = section_items(ResumeElement.Section.LANGUAGE)
    if languages:
        lines.append("## Languages")
        lines.append("")
        for item in languages:
            lines.append(f"- {item.text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
