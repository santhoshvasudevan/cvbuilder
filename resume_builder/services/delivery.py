"""The final v1 deliverable (M7): serving the already-persisted, immutable `ResumeDraft.
rendered_markdown` for preview/copy/download. This module never calls the generator or the
renderer again -- `rendered_markdown` was produced exactly once, by `services/build.py`'s call to
`rendering/markdown.py::render_resume_markdown`, strictly after the no-fabrication validator
passed, and is never touched again by anything except that one build (`ResumeDraft` is append-only
per its own `save()` guard). Re-deriving markdown from `ResumeElement` rows at read time would
require exactly reproducing `_place_achievements`' original retrieval context to get the same
result (achievement rows are persisted in their pre-placement form -- see
`docs/CURRENT_STATE.md`'s M7 section for this observation) -- reading the one immutable stored
field is the only representation guaranteed to match what Gate 2 actually approved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from job_applications.models import JobApplication

from ..models import ResumeDraft


class DraftNotDownloadableError(Exception):
    pass


def _sanitize_filename_component(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:60]


def build_filename(application: JobApplication, draft: ResumeDraft) -> str:
    """Deterministic and sanitized: a pure function of already-persisted fields, so repeated
    calls for the same draft always produce the exact same name. Uses the JRA's own role title
    (never anything Agent Builder produced) with a safe fallback so a blank/missing title can
    never produce an unsafe or empty filename."""
    jra = application.current_jra
    role_slug = _sanitize_filename_component(jra.role_title) if jra and jra.role_title else ""
    slug = role_slug or "resume"
    return f"resume-app{application.pk}-v{draft.version}-{slug}.md"


@dataclass(frozen=True)
class FinalMarkdown:
    draft: ResumeDraft
    filename: str
    content: str


def get_final_markdown(application: JobApplication) -> FinalMarkdown:
    """The one guard for the v1 final deliverable: only a draft that is (a) the application's
    current draft, (b) confirmed (Gate 2 approved it), and (c) non-stale anywhere in the chain
    (HITL-007, chain-wide -- not just draft-vs-fit-assessment) is ever returned. Read-only: never
    mutates `application` or `draft`.
    """
    draft = application.current_resume_draft
    if draft is None:
        raise DraftNotDownloadableError("No resume draft exists yet for this application.")
    if application.pipeline_phase != JobApplication.PipelinePhase.READY:
        raise DraftNotDownloadableError("Gate 2 has not been approved yet for this application.")
    if not draft.is_confirmed:
        raise DraftNotDownloadableError("The current resume draft has not been confirmed.")
    if draft.based_on_fit_assessment_id != application.current_fit_assessment_id:
        raise DraftNotDownloadableError(
            "The current resume draft is stale relative to the current fit assessment."
        )
    fit_assessment = application.current_fit_assessment
    if fit_assessment is not None and fit_assessment.based_on_jra_id != application.current_jra_id:
        raise DraftNotDownloadableError(
            "The current fit assessment is stale relative to the current job requirement "
            "analysis -- the whole chain must be current before the final resume is downloadable."
        )

    return FinalMarkdown(
        draft=draft,
        filename=build_filename(application, draft),
        content=draft.rendered_markdown,
    )
