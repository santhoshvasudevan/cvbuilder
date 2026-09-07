"""Intake orchestration: the one place job_intake's view calls into. Ties together source
resolution (URL fetch-with-fallback or pasted text), the Agent Jobber analysis call, and atomic
persistence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Max

from job_applications.models import JobApplication

from ..models import JobRequirement, JobRequirementAnalysis
from ..validators.integrity import find_integrity_violations
from .analyze import analyze_posting
from .fetch import fetch_job_posting

MIN_PASTED_TEXT_CHARS = 20
MAX_PASTED_TEXT_CHARS = 50_000


class IntakeValidationError(Exception):
    pass


class AnalysisFailedError(Exception):
    pass


class AnalysisIntegrityError(AnalysisFailedError):
    """The AJ response is schema-valid but fails a deterministic *integrity* check (2026-09-04 AJ
    hardening, D-022; course-corrected D-023): zero requirements, a duplicate requirement, or a
    requirement/screening-risk whose claimed source_context is not actually in the posting. Never
    a judgment about whether a classification is semantically correct -- that boundary belongs to
    the AJ LLM and the operator (D-023), not to this exception's callers. A subclass of
    `AnalysisFailedError` so every existing caller that already handles that (the intake view,
    Gate-1 feedback re-run) handles this too, without needing its own branch. Raised strictly
    before the persistence transaction begins: nothing -- no JobApplication, no
    JobRequirementAnalysis, no JobRequirement, no pointer/phase advancement -- is ever created for
    a rejected analysis. The LLMCallLog row from the underlying (successful, schema-valid) call
    still exists, per LLM-010's "every call is logged regardless of what happens next" -- it
    stores only token counts/latency/error metadata, never the raw posting or response content, so
    nothing sensitive is retained by this failure path either."""


class ConcurrentModificationError(Exception):
    """See `candidate_matching.services.fit_assessment.ConcurrentModificationError` -- same
    rationale, same (effectively unreachable given the `select_for_update()` lock) safety net."""


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


def run_intake(resolved: ResolvedSource, *, requested_model_id: int | None = None) -> JobApplication:
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
    result = analyze_posting(resolved.extracted_text, requested_model_id=requested_model_id)
    if result.is_error:
        raise AnalysisFailedError(result.error.message)
    analysis = result.content

    violations = find_integrity_violations(analysis, resolved.extracted_text)
    if violations:
        raise AnalysisIntegrityError(
            "Agent Jobber's analysis failed an integrity check and was not saved: "
            + " | ".join(violations)
        )

    with transaction.atomic():
        application = JobApplication.objects.create()
        jra = _persist_jra_version(application, version=1, resolved=resolved, analysis=analysis)
        application.advance_to_analysis(jra=jra)

    return application


def _persist_jra_version(application: JobApplication, *, version: int, resolved: ResolvedSource, analysis):
    jra = JobRequirementAnalysis.objects.create(
        job_application=application,
        version=version,
        source_type=resolved.source_type,
        source_url=resolved.source_url,
        original_input=resolved.original_input,
        extracted_text=resolved.extracted_text,
        extracted_text_sha256=hashlib.sha256(resolved.extracted_text.encode("utf-8")).hexdigest(),
        posting_language=analysis.posting_language,
        employer=analysis.employer,
        role_title=analysis.role_title,
        location=analysis.location,
        work_arrangement=analysis.work_arrangement,
        screening_risks=[
            {"text": risk.text, "source_context": risk.source_context} for risk in analysis.screening_risks
        ],
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
    return jra


def rerun_analysis(
    application: JobApplication,
    *,
    url: str = "",
    pasted_text: str = "",
    requested_model_id: int | None = None,
) -> JobRequirementAnalysis:
    """Gate-1 feedback re-run targeting Agent Jobber (M5): creates a new, append-only
    JobRequirementAnalysis version for an application that already has one, and repoints
    `JobApplication.current_jra` at it (`record_jra` -- a plain pointer update, no phase
    transition; the application is already past NEW).

    If the operator supplies neither a new URL nor new pasted text, this reruns analysis against
    exactly the same original input the current version used (a plain retry, e.g. to get a second
    opinion from the model without changing anything). Otherwise it resolves a fresh source exactly
    like the initial intake path, including the same URL-fetch-failure-propagates-uncaught contract
    (`resolve_posting_source`'s docstring) so the caller can offer the pasted-text fallback.
    """
    current = application.current_jra
    if current is None:
        raise IntakeValidationError("JobApplication has no existing JobRequirementAnalysis to rerun.")

    if url or pasted_text:
        resolved = resolve_posting_source(url=url, pasted_text=pasted_text)
    elif current.source_type == JobRequirementAnalysis.SourceType.URL:
        resolved = resolve_posting_source(url=current.original_input, pasted_text="")
    else:
        resolved = resolve_posting_source(url="", pasted_text=current.original_input)

    result = analyze_posting(resolved.extracted_text, requested_model_id=requested_model_id)
    if result.is_error:
        raise AnalysisFailedError(result.error.message)
    analysis = result.content

    violations = find_integrity_violations(analysis, resolved.extracted_text)
    if violations:
        raise AnalysisIntegrityError(
            "Agent Jobber's re-analysis failed an integrity check and was not saved: "
            + " | ".join(violations)
        )

    try:
        with transaction.atomic():
            locked_application = JobApplication.objects.select_for_update().get(pk=application.pk)
            next_version = (
                locked_application.job_requirement_analyses.aggregate(Max("version"))["version__max"] or 0
            ) + 1
            jra = _persist_jra_version(
                locked_application, version=next_version, resolved=resolved, analysis=analysis
            )
            locked_application.record_jra(jra)
    except IntegrityError as exc:
        raise ConcurrentModificationError(
            "A concurrent Agent Jobber re-run for this application raced this one -- retry."
        ) from exc

    application.current_jra = jra
    application.current_jra_id = jra.pk

    return jra
