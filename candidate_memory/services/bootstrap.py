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
from .chunking import (
    SourceChunk,
    chunk_source,
    split_chunk_in_half,
    split_chunk_into_individual_lines,
    split_line_into_sentences,
)
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
    attempt_number: int = 1,
) -> None:
    ChunkExtractionAttempt.objects.create(
        candidate_memory=new_revision,
        source_document=source_document,
        source_content_sha256=source_document.content_sha256,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        start_char=chunk.start_char,
        end_char=chunk.end_char,
        status=status,
        error_category=error_category,
        llm_call_log=llm_call_log,
        attempt_number=attempt_number,
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
    attempt_number: int = 1,
    call_budget: dict | None = None,
    max_output_tokens_override: int | None = None,
) -> None:
    """Extracts one chunk, recursively halving and retrying *only* on a `finish_reason=length`
    truncation, bounded by `MAX_SPLIT_DEPTH`/`chunking.MIN_SPLIT_CHUNK_LINES` (Candidate Memory
    recovery, 2026-09-03) -- reasoning stays disabled and `max_output_tokens` stays at its
    configured value throughout; only the chunk's own size shrinks. Once a chunk is down to a
    single whole physical line and still truncates, the deterministic (LLM-free)
    `chunking.split_line_into_sentences` is tried as a further, final tier before failing closed
    (2026-09-03) -- see that function's docstring for its own fail-closed conditions.
    `max_output_tokens_override`, when given, is threaded to every call this invocation makes
    (including any split children) and never changes the registry's configured default -- see
    `retry_chunk_lineage_with_output_override` below, the only caller that ever sets it.

    A truncated response never has any parseable content (JSON parsing fails on incomplete output,
    which is exactly what makes it classify as `finish_reason=length` in the first place -- see
    `llm_provider/adapters/openai.py`), so a chunk that gets split was never able to store
    anything in the first place; splitting can never duplicate an already-stored claim. Every
    attempt -- success, unresolved failure, or superseded-by-split -- gets exactly one
    `ChunkExtractionAttempt` row.

    `attempt_number` distinguishes a genuine retry of the *same* line range (incremented by the
    caller -- see `retry_failed_chunks`) from a range newly created by splitting (always 1, since
    it has never been attempted before). `call_budget`, when given, is a shared, mutable
    `{"remaining": N}` dict enforcing a hard cap on live provider calls across an entire retry
    pass (targeted recovery, 2026-09-03) -- once exhausted, remaining chunks are recorded FAILED
    with `error_category="BUDGET_EXHAUSTED"` *without* ever calling the provider again; `None`
    means unlimited, which is what a normal full build still uses.
    """
    if call_budget is not None and call_budget["remaining"] <= 0:
        _record_chunk_attempt(
            new_revision=new_revision,
            source_document=source_document,
            chunk=chunk,
            status=ChunkExtractionAttempt.Status.FAILED,
            error_category="BUDGET_EXHAUSTED",
            llm_call_log=None,
            attempt_number=attempt_number,
        )
        build_summary["extraction_errors"] += 1
        return
    if call_budget is not None:
        call_budget["remaining"] -= 1

    before_id = _latest_memory_build_call_log_id()
    result = extract_chunk(
        chunk,
        source_role=source_role,
        language=language,
        max_output_tokens_override=max_output_tokens_override,
    )
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
    split: list[SourceChunk] | tuple[SourceChunk, SourceChunk] | None = None
    if is_truncation and depth < MAX_SPLIT_DEPTH:
        split = split_chunk_in_half(chunk)
        if split is None and len(chunk.lines) > 1:
            # Already at the line-count floor (MIN_SPLIT_CHUNK_LINES) but still more than one
            # physical line -- fall back to one-line-per-chunk granularity (recovery, 2026-09-03):
            # a "minimum-size" chunk can still be too large in *characters* when each line is its
            # own long Markdown bullet. An empty list here (chunk is already a single line) keeps
            # `split` falsy, same as the original None case.
            split = split_chunk_into_individual_lines(chunk) or None
        if split is None and len(chunk.lines) == 1 and chunk.start_char is None:
            # Already a single whole physical line and still truncating -- deterministic,
            # LLM-free sentence-boundary split (recovery, 2026-09-03): some lines are themselves
            # dense enough (several complete sentences in one Markdown bullet) to overflow the
            # output budget even alone. `[] or None` fails closed exactly like the tiers above when
            # the line doesn't tokenize into at least two trustworthy sentence fragments.
            split = split_line_into_sentences(chunk) or None

    if is_truncation and split:
        _record_chunk_attempt(
            new_revision=new_revision,
            source_document=source_document,
            chunk=chunk,
            status=ChunkExtractionAttempt.Status.SUPERSEDED,
            error_category=result.error.category.value,
            llm_call_log=call_log,
            attempt_number=attempt_number,
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
                call_budget=call_budget,
                max_output_tokens_override=max_output_tokens_override,
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
            attempt_number=attempt_number,
        )
        build_summary["extraction_errors"] += 1
        return

    _record_chunk_attempt(
        new_revision=new_revision,
        source_document=source_document,
        chunk=chunk,
        status=ChunkExtractionAttempt.Status.SUCCESS,
        llm_call_log=call_log,
        attempt_number=attempt_number,
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


@dataclasses.dataclass
class ChunkRetrySummary:
    failed_before: int
    live_calls_made: int
    recovered: int
    still_failed: int
    claims_extracted: int
    rules_extracted: int
    extraction_errors: int


def retry_failed_chunks(candidate_memory: CandidateMemory, *, max_live_calls: int) -> ChunkRetrySummary:
    """Targeted recovery (2026-09-03): retries only the currently-`FAILED`
    `ChunkExtractionAttempt` rows for one *existing* revision, reconstructing each exact chunk
    from the source document's own immutable content at the attempt's stored line range -- never
    a full bootstrap re-run, never touching any other revision. Each retry reuses the same bounded
    recursive-split recovery as a real build (`_process_chunk_with_recovery`), so reasoning stays
    disabled and `max_output_tokens` stays at its configured value throughout.

    Bounded by `max_live_calls`, a hard cap on real provider calls across this entire pass --
    enforced via a shared `call_budget` dict threaded through every (possibly recursive) chunk
    attempt, so a single retry's own splitting can never blow through the cap either. Once
    exhausted, remaining un-retried `FAILED` attempts are left exactly as they already are.

    A retried attempt's row is marked `SUPERSEDED` only once every new attempt its retry produced
    (including any further splits) reaches a terminal state with zero remaining `FAILED` leaves --
    i.e. only after a genuinely successful replacement. If the retry itself also fails (with or
    without splitting), the original row is left untouched, still `FAILED` -- never deleted, never
    silently reinterpreted, so attempt lineage stays fully auditable.
    """
    failed_attempts = list(
        candidate_memory.chunk_attempts.filter(status=ChunkExtractionAttempt.Status.FAILED)
        .select_related("source_document")
    )

    # Deduplicate by (source_document, start_line, end_line): a range that has already been
    # retried once and failed again has *two* FAILED rows (the original attempt_number=1 and the
    # prior retry's attempt_number=2, etc.) -- retrying each independently would redundantly repeat
    # the same call. Only the highest attempt_number per distinct range is actually retried; every
    # row for that range (old and new) is updated together from the one outcome, since they all
    # describe the exact same not-yet-covered source content.
    by_range: dict[tuple[int, int, int], list[ChunkExtractionAttempt]] = {}
    for attempt in failed_attempts:
        key = (attempt.source_document_id, attempt.start_line, attempt.end_line)
        by_range.setdefault(key, []).append(attempt)

    call_budget = {"remaining": max_live_calls}
    build_summary = {"extraction_errors": 0, "claims_extracted": 0, "rules_extracted": 0}
    recovered = 0
    still_failed = 0

    for (_source_id, _start, _end), attempts_for_range in by_range.items():
        latest_attempt = max(attempts_for_range, key=lambda a: a.attempt_number)

        if call_budget["remaining"] <= 0:
            still_failed += len(attempts_for_range)
            continue

        source_document = latest_attempt.source_document
        if source_document.content_sha256 != latest_attempt.source_content_sha256:
            # The source changed since this attempt was recorded -- never safe to retry against
            # different content under the same attempt's stored provenance; leave it FAILED.
            still_failed += len(attempts_for_range)
            continue

        lines = source_document.raw_content.splitlines()
        chunk = SourceChunk(
            start_line=latest_attempt.start_line,
            end_line=latest_attempt.end_line,
            lines=tuple(lines[latest_attempt.start_line - 1 : latest_attempt.end_line]),
        )
        before_ids = set(
            ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory)
            .values_list("id", flat=True)
        )
        _process_chunk_with_recovery(
            chunk,
            source_role=source_document.source_role,
            language=source_document.language,
            source_document=source_document,
            new_revision=candidate_memory,
            build_summary=build_summary,
            attempt_number=latest_attempt.attempt_number + 1,
            call_budget=call_budget,
        )
        after_ids = set(
            ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory)
            .values_list("id", flat=True)
        )
        new_attempts = ChunkExtractionAttempt.objects.filter(id__in=after_ids - before_ids)
        if new_attempts.exists() and not new_attempts.filter(
            status=ChunkExtractionAttempt.Status.FAILED
        ).exists():
            for attempt in attempts_for_range:
                attempt.status = ChunkExtractionAttempt.Status.SUPERSEDED
                attempt.save()
            recovered += len(attempts_for_range)
        else:
            still_failed += len(attempts_for_range)

    candidate_memory.refresh_from_db()
    summary = dict(candidate_memory.build_summary)
    summary["claims_extracted"] = summary.get("claims_extracted", 0) + build_summary["claims_extracted"]
    summary["rules_extracted"] = summary.get("rules_extracted", 0) + build_summary["rules_extracted"]
    summary["extraction_errors"] = summary.get("extraction_errors", 0) + build_summary["extraction_errors"]
    summary["chunk_attempts_failed"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.FAILED
    ).count()
    summary["chunk_attempts_superseded"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.SUPERSEDED
    ).count()
    candidate_memory.build_summary = summary
    candidate_memory.save()

    return ChunkRetrySummary(
        failed_before=len(failed_attempts),
        live_calls_made=max_live_calls - call_budget["remaining"],
        recovered=recovered,
        still_failed=still_failed,
        claims_extracted=build_summary["claims_extracted"],
        rules_extracted=build_summary["rules_extracted"],
        extraction_errors=build_summary["extraction_errors"],
    )


def retry_chunk_lineage_with_output_override(
    candidate_memory: CandidateMemory,
    *,
    source_document: MemorySourceDocument,
    line_number: int,
    max_output_tokens: int,
) -> ChunkRetrySummary:
    """Last-resort, single-call recovery (2026-09-03) for one specific physical line whose every
    `ChunkExtractionAttempt` -- at any ancestor granularity, not just its own direct single-line
    attempt -- is currently `FAILED`. This only ever matters once the normal bounded-split recovery
    (`retry_failed_chunks` / `split_chunk_into_individual_lines`) has already reached the finest
    possible granularity (a single line) and that line *still* truncates at the model's configured
    default `max_output_tokens` -- i.e. the line itself, not the chunk size, is the bottleneck.

    Retries with an explicit, higher `max_output_tokens` for this one call only; the registry's
    configured `LLMModel.max_output_tokens` default is never read from or written to here, so every
    other call (including any future normal build or `retry_failed_chunks` pass) is entirely
    unaffected. Matches every currently-`FAILED` attempt on this source document whose stored line
    range *contains* `line_number` -- both its own direct single-line attempt and any coarser
    ancestor chunk that also covered it and was never superseded (because that ancestor's retry
    left this exact line as its one remaining failure) -- so the whole lineage is resolved together
    rather than leaving stale ancestor rows referring to now-covered content.

    Fails closed, exactly like `retry_failed_chunks`, if the source document's current content hash
    no longer matches any matched attempt's stored `source_content_sha256` (never retries against
    changed content under stale provenance), and if there are no matching `FAILED` attempts at all
    (nothing to do -- never a partial no-op treated as success). Since the chunk being retried is
    always exactly one physical line, `_process_chunk_with_recovery` can never split it further
    (`split_chunk_into_individual_lines` returns `[]` for a single-line chunk), so this function
    always makes at most one live provider call. All matched attempts are marked `SUPERSEDED`
    together only if that one call succeeds without truncating; otherwise every one of them is left
    exactly as `FAILED` as it already was -- this function never retries a second time itself.
    """
    matched_attempts = list(
        candidate_memory.chunk_attempts.filter(
            status=ChunkExtractionAttempt.Status.FAILED,
            source_document=source_document,
            start_line__lte=line_number,
            end_line__gte=line_number,
        )
    )
    if not matched_attempts:
        return ChunkRetrySummary(0, 0, 0, 0, 0, 0, 0)

    if any(
        source_document.content_sha256 != attempt.source_content_sha256 for attempt in matched_attempts
    ):
        return ChunkRetrySummary(len(matched_attempts), 0, 0, len(matched_attempts), 0, 0, 0)

    lines = source_document.raw_content.splitlines()
    chunk = SourceChunk(start_line=line_number, end_line=line_number, lines=(lines[line_number - 1],))
    next_attempt_number = max(attempt.attempt_number for attempt in matched_attempts) + 1
    build_summary = {"extraction_errors": 0, "claims_extracted": 0, "rules_extracted": 0}

    before_ids = set(
        ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory).values_list(
            "id", flat=True
        )
    )
    _process_chunk_with_recovery(
        chunk,
        source_role=source_document.source_role,
        language=source_document.language,
        source_document=source_document,
        new_revision=candidate_memory,
        build_summary=build_summary,
        attempt_number=next_attempt_number,
        max_output_tokens_override=max_output_tokens,
    )
    after_ids = set(
        ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory).values_list(
            "id", flat=True
        )
    )
    new_attempts = ChunkExtractionAttempt.objects.filter(id__in=after_ids - before_ids)
    recovered_ok = new_attempts.exists() and not new_attempts.filter(
        status=ChunkExtractionAttempt.Status.FAILED
    ).exists()
    if recovered_ok:
        for attempt in matched_attempts:
            attempt.status = ChunkExtractionAttempt.Status.SUPERSEDED
            attempt.save()

    candidate_memory.refresh_from_db()
    summary = dict(candidate_memory.build_summary)
    summary["claims_extracted"] = summary.get("claims_extracted", 0) + build_summary["claims_extracted"]
    summary["rules_extracted"] = summary.get("rules_extracted", 0) + build_summary["rules_extracted"]
    summary["extraction_errors"] = summary.get("extraction_errors", 0) + build_summary["extraction_errors"]
    summary["chunk_attempts_failed"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.FAILED
    ).count()
    summary["chunk_attempts_superseded"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.SUPERSEDED
    ).count()
    candidate_memory.build_summary = summary
    candidate_memory.save()

    return ChunkRetrySummary(
        failed_before=len(matched_attempts),
        live_calls_made=1,
        recovered=len(matched_attempts) if recovered_ok else 0,
        still_failed=0 if recovered_ok else len(matched_attempts),
        claims_extracted=build_summary["claims_extracted"],
        rules_extracted=build_summary["rules_extracted"],
        extraction_errors=build_summary["extraction_errors"],
    )


@dataclasses.dataclass
class SentenceSplitPlan:
    """Result of pre-calculating a sentence-level split before any provider call is made."""

    fragments: list[SourceChunk]
    required_calls: int
    within_budget: bool


def plan_sentence_split_recovery(
    source_document: MemorySourceDocument, *, line_number: int, max_live_calls: int
) -> SentenceSplitPlan:
    """Pure, no-provider-call pre-calculation (Candidate Memory recovery, 2026-09-03): splits
    `line_number` deterministically via `chunking.split_line_into_sentences` and reports how many
    live calls that plan would require, without making any of them. Callers must check
    `within_budget` and refuse to proceed (never silently truncate the plan to fit) if it is
    `False`."""
    lines = source_document.raw_content.splitlines()
    whole_line = SourceChunk(start_line=line_number, end_line=line_number, lines=(lines[line_number - 1],))
    fragments = split_line_into_sentences(whole_line)
    return SentenceSplitPlan(
        fragments=fragments,
        required_calls=len(fragments),
        within_budget=len(fragments) >= 2 and len(fragments) <= max_live_calls,
    )


def retry_line_via_sentence_split(
    candidate_memory: CandidateMemory,
    *,
    source_document: MemorySourceDocument,
    line_number: int,
    max_live_calls: int,
) -> ChunkRetrySummary:
    """Deterministic, LLM-free sentence-level last-resort recovery (2026-09-03) for one physical
    line whose every currently-`FAILED` `ChunkExtractionAttempt` -- at any ancestor granularity,
    including any prior per-call `max_output_tokens` override attempt -- still traces back to it.
    This only ever matters once line-level splitting and a raised per-call output-token ceiling
    have both already been exhausted and the line still truncates: the sentence boundaries
    themselves (never the provider) decide how to subdivide it, via
    `plan_sentence_split_recovery`/`chunking.split_line_into_sentences`.

    Pre-calculates the full plan via `plan_sentence_split_recovery` *before* making any call.
    Returns immediately, having made zero calls, if: no currently-`FAILED` attempt covers this
    line (a true no-op, never reinterpreted as success); the source document's current content
    hash no longer matches any matched attempt's stored `source_content_sha256` (fail closed,
    never retry under stale provenance); the line does not split into at least two trustworthy
    sentence fragments; or the plan would require more calls than `max_live_calls` allows.

    Fragments are attempted strictly in order, one live call each, at `depth=MAX_SPLIT_DEPTH` so a
    fragment that itself still truncates fails closed immediately with no further split attempt.
    Processing stops at the first fragment failure -- every fragment after it is left entirely
    unattempted (no call spent, no `ChunkExtractionAttempt` row created for it) rather than
    continuing or retrying. All matched historical `FAILED` attempts are marked `SUPERSEDED`
    together only if every fragment succeeds; on any failure, or on any of the zero-call fail-closed
    conditions above, they are left exactly as they already were.
    """
    matched_attempts = list(
        candidate_memory.chunk_attempts.filter(
            status=ChunkExtractionAttempt.Status.FAILED,
            source_document=source_document,
            start_line__lte=line_number,
            end_line__gte=line_number,
        )
    )
    if not matched_attempts:
        return ChunkRetrySummary(0, 0, 0, 0, 0, 0, 0)

    if any(
        source_document.content_sha256 != attempt.source_content_sha256 for attempt in matched_attempts
    ):
        return ChunkRetrySummary(len(matched_attempts), 0, 0, len(matched_attempts), 0, 0, 0)

    plan = plan_sentence_split_recovery(
        source_document, line_number=line_number, max_live_calls=max_live_calls
    )
    if not plan.within_budget:
        return ChunkRetrySummary(len(matched_attempts), 0, 0, len(matched_attempts), 0, 0, 0)

    build_summary = {"extraction_errors": 0, "claims_extracted": 0, "rules_extracted": 0}
    live_calls_made = 0
    all_succeeded = True

    for fragment in plan.fragments:
        before_ids = set(
            ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory).values_list(
                "id", flat=True
            )
        )
        _process_chunk_with_recovery(
            fragment,
            source_role=source_document.source_role,
            language=source_document.language,
            source_document=source_document,
            new_revision=candidate_memory,
            build_summary=build_summary,
            depth=MAX_SPLIT_DEPTH,
        )
        live_calls_made += 1
        after_ids = set(
            ChunkExtractionAttempt.objects.filter(candidate_memory=candidate_memory).values_list(
                "id", flat=True
            )
        )
        new_attempts = ChunkExtractionAttempt.objects.filter(id__in=after_ids - before_ids)
        if new_attempts.filter(status=ChunkExtractionAttempt.Status.FAILED).exists():
            all_succeeded = False
            break

    if all_succeeded:
        for attempt in matched_attempts:
            attempt.status = ChunkExtractionAttempt.Status.SUPERSEDED
            attempt.save()

    candidate_memory.refresh_from_db()
    summary = dict(candidate_memory.build_summary)
    summary["claims_extracted"] = summary.get("claims_extracted", 0) + build_summary["claims_extracted"]
    summary["rules_extracted"] = summary.get("rules_extracted", 0) + build_summary["rules_extracted"]
    summary["extraction_errors"] = summary.get("extraction_errors", 0) + build_summary["extraction_errors"]
    summary["chunk_attempts_failed"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.FAILED
    ).count()
    summary["chunk_attempts_superseded"] = candidate_memory.chunk_attempts.filter(
        status=ChunkExtractionAttempt.Status.SUPERSEDED
    ).count()
    candidate_memory.build_summary = summary
    candidate_memory.save()

    return ChunkRetrySummary(
        failed_before=len(matched_attempts),
        live_calls_made=live_calls_made,
        recovered=len(matched_attempts) if all_succeeded else 0,
        still_failed=0 if all_succeeded else len(matched_attempts),
        claims_extracted=build_summary["claims_extracted"],
        rules_extracted=build_summary["rules_extracted"],
        extraction_errors=build_summary["extraction_errors"],
    )


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
