"""Bootstrap orchestration: ties chunking, extraction, classification, storage, carry-forward,
conflict detection, and auto-confirmation together into one CandidateMemory revision build.

Two entry points:
- `build_revision` -- file-based, used by the `bootstrap_candidate_memory` management command.
- `build_revision_from_operator_text` -- used by the "Add experience or update profile" UI
  workflow: one new OPERATOR_UPDATE source, everything else carried forward unchanged.

Both ultimately call `build_revision_from_sources`, which is the single place carry-forward
semantics are decided: any of the *previous* active revision's sources not re-supplied this round
are carried forward unchanged automatically (the operator-text workflow only ever supplies the
one new source); any re-supplied source is compared by content hash and only reprocessed if it
actually changed.

Never invoked automatically (never on migrate/runserver/test/deploy). The revision this produces
is always left in NEEDS_REVIEW; activation is a separate, explicit operator action
(services/lifecycle.py).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
from pathlib import Path

from llm_provider.errors import LLMErrorCategory
from llm_provider.models import LLMCallLog, StageModelAssignment

from ..models import CandidateMemory, ChunkExtractionAttempt, MemoryClaim, MemorySourceDocument
from . import revision as revision_service
from . import storage as storage_service
from .chunking import SourceChunk, chunk_source, split_chunk_in_half
from .comparable_values import COMPARABLE_CLAIM_TYPES, has_valid_structured_value
from .confirmation import evaluate_auto_confirmation
from .conflicts import detect_and_resolve_conflicts
from .extraction import extract_chunk

SNAPSHOT_FILENAME = "CANDIDATE_MEMORY_SNAPSHOT.md"

# Candidate Memory recovery (2026-09-03): how many times a single top-level chunk may be
# recursively halved in response to a finish_reason=length truncation before giving up and
# failing closed (see chunking.MIN_SPLIT_CHUNK_LINES for the companion size bound -- whichever
# bound is hit first stops the recursion).
MAX_SPLIT_DEPTH = 4


class SnapshotImportRefusedError(Exception):
    pass


@dataclasses.dataclass
class SourceSpec:
    """A source identified by filesystem path (bootstrap command)."""

    path: Path
    logical_source_key: str
    source_role: str
    language: str
    precedence: int
    trust_status: str = "OPERATOR_APPROVED"


@dataclasses.dataclass
class ReadSource:
    """A source whose content has already been read into memory (shared core representation)."""

    content: str
    sha256: str
    logical_source_key: str
    filename: str
    source_role: str
    language: str
    precedence: int
    trust_status: str = "OPERATOR_APPROVED"


def _read_source_from_spec(spec: SourceSpec) -> ReadSource:
    raw = spec.path.read_text(encoding="utf-8")
    return ReadSource(
        content=raw,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        logical_source_key=spec.logical_source_key,
        filename=spec.path.name,
        source_role=spec.source_role,
        language=spec.language,
        precedence=spec.precedence,
        trust_status=spec.trust_status,
    )


def build_revision(
    source_specs: list[SourceSpec], *, abandon_existing: bool = False, force_reextract: bool = False
) -> CandidateMemory:
    for spec in source_specs:
        if spec.path.name == SNAPSHOT_FILENAME:
            raise SnapshotImportRefusedError(
                f"Refusing to import {SNAPSHOT_FILENAME} as a source -- the snapshot is a "
                "generated reference, never evidence (D-015)."
            )
    return build_revision_from_sources(
        [_read_source_from_spec(spec) for spec in source_specs],
        abandon_existing=abandon_existing,
        force_reextract=force_reextract,
    )


def build_revision_from_operator_text(
    *,
    text: str,
    context: str = "",
    employer: str = "",
    experience_level: str = "",
    dates: str = "",
    actions: str = "",
    results: str = "",
    metrics: str = "",
    abandon_existing: bool = False,
) -> CandidateMemory:
    """"Add experience or update profile" (requirements.md Sec 4/16): the operator's submission
    becomes one new, immutable OPERATOR_UPDATE source. Everything else the current active
    revision has is carried forward unchanged automatically."""
    composed = "\n".join(
        line
        for line in [
            f"Context: {context}" if context else "",
            f"Employer/Client/Project: {employer}" if employer else "",
            f"Experience level: {experience_level}" if experience_level else "",
            f"Dates: {dates}" if dates else "",
            f"Actions: {actions}" if actions else "",
            f"Results: {results}" if results else "",
            f"Metrics: {metrics}" if metrics else "",
            "",
            text,
        ]
        if line
    )
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    read_source = ReadSource(
        content=composed,
        sha256=hashlib.sha256(composed.encode("utf-8")).hexdigest(),
        logical_source_key=f"operator_update__ui_{timestamp}",
        filename=f"operator_update_{timestamp}.md",
        source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        language="en",
        precedence=0,
    )
    return build_revision_from_sources([read_source], abandon_existing=abandon_existing)


def _latest_memory_build_call_log_id() -> int:
    last = (
        LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.MEMORY_BUILD)
        .order_by("-id")
        .first()
    )
    return last.id if last is not None else 0


def _record_chunk_attempt(
    *,
    new_revision: CandidateMemory,
    source_document: MemorySourceDocument,
    chunk: SourceChunk,
    status: str,
    error_category: str = "",
    llm_call_log: LLMCallLog | None,
) -> None:
    ChunkExtractionAttempt.objects.create(
        candidate_memory=new_revision,
        source_document=source_document,
        source_content_sha256=source_document.content_sha256,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        status=status,
        error_category=error_category,
        llm_call_log=llm_call_log,
    )


def _process_chunk_with_recovery(
    chunk: SourceChunk,
    *,
    source_role: str,
    language: str,
    source_document: MemorySourceDocument,
    new_revision: CandidateMemory,
    build_summary: dict,
    depth: int = 0,
) -> None:
    """Extracts one chunk, recursively halving and retrying *only* on a `finish_reason=length`
    truncation, bounded by `MAX_SPLIT_DEPTH`/`chunking.MIN_SPLIT_CHUNK_LINES` (Candidate Memory
    recovery, 2026-09-03) -- reasoning stays disabled and `max_output_tokens` stays at its
    configured value throughout; only the chunk's own size shrinks. A truncated response never
    has any parseable content (JSON parsing fails on incomplete output, which is exactly what
    makes it classify as `finish_reason=length` in the first place -- see
    `llm_provider/adapters/openai.py`), so a chunk that gets split was never able to store
    anything in the first place; splitting can never duplicate an already-stored claim. Every
    attempt -- success, unresolved failure, or superseded-by-split -- gets exactly one
    `ChunkExtractionAttempt` row.
    """
    before_id = _latest_memory_build_call_log_id()
    result = extract_chunk(chunk, source_role=source_role, language=language)
    call_log = (
        LLMCallLog.objects.filter(stage=StageModelAssignment.Stage.MEMORY_BUILD, id__gt=before_id)
        .order_by("id")
        .first()
    )

    is_truncation = (
        result.is_error
        and result.error.category == LLMErrorCategory.CONFIGURATION
        and "finish_reason=length" in result.error.message
    )
    split = split_chunk_in_half(chunk) if (is_truncation and depth < MAX_SPLIT_DEPTH) else None

    if is_truncation and split is not None:
        _record_chunk_attempt(
            new_revision=new_revision,
            source_document=source_document,
            chunk=chunk,
            status=ChunkExtractionAttempt.Status.SUPERSEDED,
            error_category=result.error.category.value,
            llm_call_log=call_log,
        )
        for sub_chunk in split:
            _process_chunk_with_recovery(
                sub_chunk,
                source_role=source_role,
                language=language,
                source_document=source_document,
                new_revision=new_revision,
                build_summary=build_summary,
                depth=depth + 1,
            )
        return

    if result.is_error:
        # Either not a truncation, or a truncation that has already hit the split/size bound --
        # fail closed rather than split forever or silently drop the coverage gap.
        _record_chunk_attempt(
            new_revision=new_revision,
            source_document=source_document,
            chunk=chunk,
            status=ChunkExtractionAttempt.Status.FAILED,
            error_category=result.error.category.value,
            llm_call_log=call_log,
        )
        build_summary["extraction_errors"] += 1
        return

    _record_chunk_attempt(
        new_revision=new_revision,
        source_document=source_document,
        chunk=chunk,
        status=ChunkExtractionAttempt.Status.SUCCESS,
        llm_call_log=call_log,
    )
    for item in result.content.items:
        try:
            stored = storage_service.store_extracted_item(
                item, candidate_memory=new_revision, source_document=source_document
            )
        except Exception:
            build_summary["extraction_errors"] += 1
            continue
        if isinstance(stored, MemoryClaim):
            build_summary["claims_extracted"] += 1
        else:
            build_summary["rules_extracted"] += 1


def build_revision_from_sources(
    read_sources: list[ReadSource], *, abandon_existing: bool = False, force_reextract: bool = False
) -> CandidateMemory:
    for rs in read_sources:
        if rs.filename == SNAPSHOT_FILENAME:
            raise SnapshotImportRefusedError(
                f"Refusing to import {SNAPSHOT_FILENAME} as a source -- the snapshot is a "
                "generated reference, never evidence (D-015)."
            )

    if force_reextract:
        # Candidate Memory recovery (2026-09-03): deliberately build a completely independent
        # revision, bypassing the existing-working-revision guard *without* touching whatever
        # BUILDING/NEEDS_REVIEW/ACTIVE revision already exists -- never abandons it, never edits
        # it, never reads its sources or claims for reuse. Every supplied source is fully
        # reprocessed from scratch, so a prior revision's defects (e.g. incorrectly-merged
        # claims) can never leak into this new one via carry-forward.
        old_revision = None
    else:
        # Audit repair: never silently create a second working revision. A caller that actually
        # wants to discard a stuck/unwanted one must say so explicitly (abandon_existing=True),
        # which is a deliberate, auditable action (services/revision.py::abandon_revision), not a
        # side effect of just running the build again.
        existing_working = revision_service.current_working_revision()
        if existing_working is not None:
            if not abandon_existing:
                revision_service.require_no_working_revision()  # raises ExistingWorkingRevisionError
            revision_service.abandon_revision(
                existing_working, reason="Abandoned automatically: a new build was explicitly requested."
            )
        old_revision = revision_service.current_active_revision()

    new_revision = revision_service.start_new_revision(base_revision=old_revision)

    supplied_by_key = {rs.logical_source_key: rs for rs in read_sources}
    old_by_key: dict[str, MemorySourceDocument] = {}
    if old_revision is not None:
        old_by_key = {s.logical_source_key: s for s in old_revision.source_documents.all()}

    unchanged: dict[str, MemorySourceDocument] = {}
    to_process: list[ReadSource] = []

    # Any previous source not re-supplied this round is carried forward unchanged automatically
    # (this is what makes the operator-text UI workflow only need to supply the one new source).
    for key, old_source in old_by_key.items():
        if key not in supplied_by_key:
            unchanged[key] = old_source

    for key, rs in supplied_by_key.items():
        old_source = old_by_key.get(key)
        if old_source is not None and old_source.content_sha256 == rs.sha256:
            unchanged[key] = old_source
        else:
            to_process.append(rs)

    build_summary = {
        "sources_reused": len(unchanged),
        "sources_processed": len(to_process),
        "claims_extracted": 0,
        "rules_extracted": 0,
        "extraction_errors": 0,
    }

    # Audit repair: bounded state/recovery, not one giant transaction. Dozens of chunk-level
    # provider calls happen below across possibly-many sources; wrapping all of that in one
    # long-held `transaction.atomic()` would hold DB locks for the whole build. Instead, each
    # write commits as it happens (autocommit, same as before), and only an unexpected failure
    # (not the already-tallied per-chunk/per-item errors below) marks the revision FAILED --
    # explicit and visible, never a `BUILDING` revision stuck forever looking in-progress.
    try:
        if unchanged:
            revision_service.carry_forward_unchanged(new_revision, old_revision, unchanged)
        build_summary["claims_carried_forward"] = MemoryClaim.objects.filter(
            candidate_memory=new_revision
        ).count()

        for rs in to_process:
            source_document = MemorySourceDocument.objects.create(
                candidate_memory=new_revision,
                logical_source_key=rs.logical_source_key,
                filename=rs.filename,
                source_role=rs.source_role,
                language=rs.language,
                trust_status=rs.trust_status,
                precedence=rs.precedence,
                raw_content=rs.content,
            )
            for chunk in chunk_source(rs.content):
                _process_chunk_with_recovery(
                    chunk,
                    source_role=rs.source_role,
                    language=rs.language,
                    source_document=source_document,
                    new_revision=new_revision,
                    build_summary=build_summary,
                )

        detect_and_resolve_conflicts(new_revision)
        build_summary["conflicts_detected"] = new_revision.conflicts.count()
        build_summary["claims_auto_confirmed"] = evaluate_auto_confirmation(new_revision)
        # Audit repair: a comparable-type claim (employment_dates/employment_location/
        # language_proficiency) whose structured_value failed to validate is never silently
        # invisible -- it stays UNCONFIRMED/excluded from conflict grouping, and this count makes
        # that fact a visible build issue in the revision-detail UI rather than a hole nobody
        # notices.
        build_summary["structured_value_issues"] = sum(
            1
            for claim in new_revision.claims.filter(claim_type__in=COMPARABLE_CLAIM_TYPES)
            if not has_valid_structured_value(claim)
        )
        # Candidate Memory recovery (2026-09-03): visible, revision-level counts of the durable
        # per-chunk attempt trail -- FAILED is exactly what blocks activation (services/
        # lifecycle.py); SUPERSEDED chunks were truncated but their content was fully recovered by
        # smaller sub-chunk attempts, so they are informational only, never a blocker.
        build_summary["chunk_attempts_failed"] = new_revision.chunk_attempts.filter(
            status=ChunkExtractionAttempt.Status.FAILED
        ).count()
        build_summary["chunk_attempts_superseded"] = new_revision.chunk_attempts.filter(
            status=ChunkExtractionAttempt.Status.SUPERSEDED
        ).count()
    except Exception as exc:
        # A genuinely unexpected failure (DB error, missing MEMORY_BUILD stage assignment, etc.)
        # -- never leave this looking review-ready. build_summary reflects whatever progress was
        # made before the failure.
        build_summary["failure_reason"] = str(exc)[:500]
        new_revision.build_summary = build_summary
        new_revision.status = CandidateMemory.Status.FAILED
        new_revision.save()
        raise

    new_revision.build_summary = build_summary
    new_revision.status = CandidateMemory.Status.NEEDS_REVIEW
    new_revision.save()
    return new_revision
