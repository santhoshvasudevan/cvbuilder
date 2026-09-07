"""D-037 completeness enforcement: corrects two audited output gaps left by D-035/D-036.

1. **Engagement completeness** -- `rendering/markdown.py` used to show the same "no evidence
   available" diagnostic line whenever an engagement ended up with zero rendered bullets,
   regardless of *why*. That conflated two entirely different situations: `NO_ELIGIBLE_EVIDENCE`
   (the pinned manifest recorded no eligible anchor claims for that engagement at all -- an honest
   diagnostic) and `MODEL_OMITTED_CONTENT` (eligible evidence genuinely existed, but Agent
   Builder's output simply didn't use it) -- the latter must never be displayed as if no evidence
   existed. This module enforces that distinction structurally: an engagement classified
   `MODEL_OMITTED_CONTENT` fails the whole build closed (`CompletenessError`) rather than reaching
   the renderer at all, so `rendering/markdown.py`'s diagnostic line is only ever reachable for a
   genuine `NO_ELIGIBLE_EVIDENCE` engagement. Fail-closed (over "render an explicit review
   diagnostic instead") is the deliberate v1 choice: an approved engagement with real, available
   evidence but zero rendered content is a build that should be retried, not shipped for human
   review with a hole in it -- there is no existing Product Owner decision permitting a
   header-only chronology for an engagement that actually had evidence.
2. **Language completeness** -- confirmed, pinned language evidence
   (`RetrievalContext.pinned_language_claim_ids`, D-037) must never silently disappear because
   Agent Builder's output simply omitted a `LANGUAGE` element for it. Checked by claim_id
   citation, never by parsing rendered prose -- this generalizes to any confirmed language claim,
   never hard-codes a specific language/proficiency value.
"""

from __future__ import annotations

from candidate_matching.services.retrieve import RetrievalContext

from ..models import ResumeElement
from ..validators.no_fabrication import ValidatedElement


class CompletenessError(Exception):
    def __init__(self, failures: list[str]):
        self.failures = list(failures)
        super().__init__(
            f"Resume completeness check failed ({len(self.failures)} issue(s)): "
            f"{'; '.join(self.failures)}"
        )


def check_engagement_completeness(
    placed_elements: list[ValidatedElement], retrieval: RetrievalContext
) -> list[str]:
    """Returns one failure message per `MODEL_OMITTED_CONTENT` engagement: an engagement the
    pinned manifest did *not* flag `NO_ELIGIBLE_EVIDENCE` (i.e. eligible anchor and/or
    job-relevant evidence was available), but which has zero placed `EXPERIENCE_BULLET` elements
    (bullets Agent Builder wrote directly, and any achievement placed under it -- `placed_elements`
    must already be post-achievement-placement, see `rendering.markdown.place_achievements`)."""
    engagement_ids_with_content = {
        element.engagement_id
        for element in placed_elements
        if element.section == ResumeElement.Section.EXPERIENCE_BULLET and element.engagement_id
    }
    no_eligible_evidence = set(retrieval.engagements_without_eligible_evidence)
    failures = []
    for engagement in retrieval.engagements:
        if engagement.engagement_id in no_eligible_evidence:
            continue
        if engagement.engagement_id not in engagement_ids_with_content:
            failures.append(
                f"Engagement {engagement.engagement_id} had eligible evidence available in the "
                "pinned baseline-chronology manifest (job-relevant and/or engagement-anchor "
                "claims), but Agent Builder's output produced zero valid experience bullets for "
                "it (MODEL_OMITTED_CONTENT) -- re-run Agent Builder rather than rendering a resume "
                "that looks like no evidence exists for this engagement."
            )
    return failures


def check_language_completeness(
    placed_elements: list[ValidatedElement], retrieval: RetrievalContext
) -> list[str]:
    """Returns a failure message if any of `RetrievalContext.pinned_language_claim_ids` is not
    cited by at least one `LANGUAGE` element's `supporting_memory_claim_ids`."""
    pinned = set(retrieval.pinned_language_claim_ids)
    if not pinned:
        return []
    cited = {
        claim_id
        for element in placed_elements
        if element.section == ResumeElement.Section.LANGUAGE
        for claim_id in element.supporting_memory_claim_ids
    }
    missing = sorted(pinned - cited)
    if not missing:
        return []
    return [
        f"Confirmed language evidence {missing} was pinned in the baseline-chronology manifest as "
        "always-included, but Agent Builder's output did not include a LANGUAGE element citing "
        "it -- confirmed language evidence must never be silently omitted."
    ]


def ensure_completeness(
    placed_elements: list[ValidatedElement], retrieval: RetrievalContext
) -> None:
    failures = check_engagement_completeness(placed_elements, retrieval) + check_language_completeness(
        placed_elements, retrieval
    )
    if failures:
        raise CompletenessError(failures)
