"""Intake orchestration: the one place job_intake's view calls into. Ties together source
resolution (URL fetch-with-fallback or pasted text), the Agent Jobber analysis call, and atomic
persistence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db import transaction

from job_applications.models import JobApplication

from ..models import JobRequirement, JobRequirementAnalysis
from .analyze import analyze_posting
from .fetch import fetch_job_posting

MIN_PASTED_TEXT_CHARS = 20
MAX_PASTED_TEXT_CHARS = 50_000


class IntakeValidationError(Exception):
    pass


class AnalysisFailedError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedSource:
    source_type: str
    source_url: str
    original_input: str
    extracted_text: str


def resolve_posting_source(*, url: str, pasted_text: str) -> ResolvedSource:
    """Enforces "exactly one usable source" (point 40): an explicit validation error if both a
    URL and pasted text are supplied, or if neither is. Pasted text is always a first-class,
    independent path (point 39) -- it never depends on the fetch service succeeding.

    Raises `job_intake.services.fetch.FetchError` (never caught here) for a URL that fails to
    fetch/extract -- the caller (the view) is responsible for showing the pasted-text fallback
    when that happens, per requirements.md AJ-006.
    """
    url = (url or "").strip()
    pasted_text = (pasted_text or "").strip()

    if url and pasted_text:
        raise IntakeValidationError(
            "Provide either a job posting URL or pasted text, not both -- clear one of the two fields."
        )
    if not url and not pasted_text:
        raise IntakeValidationError("Provide a job posting URL or paste the posting text.")

    if pasted_text:
        if len(pasted_text) < MIN_PASTED_TEXT_CHARS:
            raise IntakeValidationError(
                f"Pasted text is too short to be a job posting (minimum "
                f"{MIN_PASTED_TEXT_CHARS} characters)."
            )
        if len(pasted_text) > MAX_PASTED_TEXT_CHARS:
            raise IntakeValidationError(
                f"Pasted text is too long (limit {MAX_PASTED_TEXT_CHARS:,} characters)."
            )
        return ResolvedSource(
            source_type=JobRequirementAnalysis.SourceType.PASTED,
            source_url="",
            original_input=pasted_text,
            extracted_text=pasted_text,
        )

    result = fetch_job_posting(url)  # FetchError propagates uncaught -- see docstring above
    return ResolvedSource(
        source_type=JobRequirementAnalysis.SourceType.URL,
        source_url=result.final_url,
        original_input=url,
        extracted_text=result.text,
    )


def run_intake(resolved: ResolvedSource) -> JobApplication:
    """Runs the AJ analysis call first, entirely outside any transaction, so its `LLMCallLog`
    audit row (written inside `BaseLLMAdapter.generate()`) commits independently of whatever
    happens afterward -- every provider call must be logged regardless of whether the surrounding
    business transaction later succeeds or rolls back.

    Only once a valid structured result exists does persistence begin, as one all-or-nothing
    transaction: `JobApplication` + `JobRequirementAnalysis` + its `JobRequirement` children +
    the `NEW` -> `ANALYSIS` phase transition either all commit together, or none of them do. A
    failed analysis, a storage error, or an invalid phase transition therefore can never leave a
    half-current analysis or falsely advance the application -- there is nothing partial to leave
    behind, because nothing in this block is visible to any other transaction until it all
    succeeds.
    """
    result = analyze_posting(resolved.extracted_text)
    if result.is_error:
        raise AnalysisFailedError(result.error.message)
    analysis = result.content

    with transaction.atomic():
        application = JobApplication.objects.create()
        jra = JobRequirementAnalysis.objects.create(
            job_application=application,
            version=1,
            source_type=resolved.source_type,
            source_url=resolved.source_url,
            original_input=resolved.original_input,
            extracted_text=resolved.extracted_text,
            extracted_text_sha256=hashlib.sha256(
                resolved.extracted_text.encode("utf-8")
            ).hexdigest(),
            posting_language=analysis.posting_language,
            employer=analysis.employer,
            role_title=analysis.role_title,
            location=analysis.location,
            work_arrangement=analysis.work_arrangement,
            screening_risks=list(analysis.screening_risks),
        )
        for order, requirement in enumerate(analysis.requirements, start=1):
            JobRequirement.objects.create(
                job_requirement_analysis=jra,
                requirement_id=f"JR-{order:03d}",
                order=order,
                category=requirement.category.value,
                text=requirement.text,
                source_context=requirement.source_context,
            )
        application.advance_to_analysis(jra=jra)

    return application
