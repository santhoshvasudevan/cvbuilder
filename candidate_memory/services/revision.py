"""Revision creation and carry-forward (D-002/D-015): snapshot + incremental, not full reprocess.

A claim is carried forward (confirmation preserved) only if *every* one of its supports comes
from a source document that is unchanged this round. If even one support comes from a new or
changed source, the claim is not carried forward automatically -- conservative trust behavior is
preferred over convenience (D-002's explicit instruction).
"""

from __future__ import annotations

import hashlib

from ..exceptions import ExistingWorkingRevisionError
from ..models import CandidateMemory, MemoryClaim, MemoryClaimSupport, MemorySourceDocument

_WORKING_STATUSES = (CandidateMemory.Status.BUILDING, CandidateMemory.Status.NEEDS_REVIEW)


def next_version() -> int:
    latest = CandidateMemory.objects.order_by("-version").values_list("version", flat=True).first()
    return (latest or 0) + 1


def current_active_revision() -> CandidateMemory | None:
    return CandidateMemory.objects.filter(status=CandidateMemory.Status.ACTIVE).first()


def current_working_revision() -> CandidateMemory | None:
    """The one BUILDING/NEEDS_REVIEW revision, if any (audit repair: bootstrap idempotency).
    There should only ever be zero or one of these at a time -- `require_no_working_revision`
    enforces that before a new build is allowed to start."""
    return CandidateMemory.objects.filter(status__in=_WORKING_STATUSES).order_by("-version").first()


def require_no_working_revision() -> None:
    """Refuse to proceed if a working revision already exists, rather than silently creating a
    second, orphaned one (audit repair). Callers that want to discard the existing revision must
    call `abandon_revision` explicitly first -- this function never does that on its own."""
    existing = current_working_revision()
    if existing is not None:
        raise ExistingWorkingRevisionError(
            f"CandidateMemory v{existing.version} is already {existing.status} -- refusing to "
            "start a second working revision. Safe next actions: review and activate it through "
            "the Candidate Memory UI, or call abandon_revision() / pass --abandon-existing to "
            "mark it FAILED and retry."
        )


def abandon_revision(revision: CandidateMemory, reason: str = "") -> CandidateMemory:
    """Explicit operator-controlled recovery path (audit repair): mark a stuck/unwanted
    BUILDING/NEEDS_REVIEW revision FAILED so a new build can start cleanly. Never touches the
    ACTIVE revision (this function only ever operates on the revision it's given, and the model
    layer refuses this transition for any revision that isn't currently mutable, e.g. an already-
    FAILED or ACTIVE/SUPERSEDED one)."""
    summary = dict(revision.build_summary)
    summary["abandoned_reason"] = reason or "Abandoned by operator without a stated reason."
    revision.build_summary = summary
    revision.status = CandidateMemory.Status.FAILED
    revision.save()
    return revision


def source_manifest_fingerprint(sources) -> str:
    """Deterministic fingerprint of a set of sources' identity (audit repair), from source role +
    logical key + content hash only -- never raw content. `sources` is any iterable of objects
    exposing `.source_role`, `.logical_source_key`, and a content hash attribute named either
    `content_sha256` (MemorySourceDocument) or `sha256` (bootstrap.ReadSource)."""
    def _hash_of(s):
        return getattr(s, "content_sha256", None) or getattr(s, "sha256", "")

    parts = sorted(f"{s.source_role}:{s.logical_source_key}:{_hash_of(s)}" for s in sources)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def start_new_revision(base_revision: CandidateMemory | None = None) -> CandidateMemory:
    base_revision = base_revision if base_revision is not None else current_active_revision()
    return CandidateMemory.objects.create(
        version=next_version(), status=CandidateMemory.Status.BUILDING, base_revision=base_revision
    )


def carry_forward_unchanged(
    new_revision: CandidateMemory,
    old_revision: CandidateMemory,
    unchanged_sources: dict[str, MemorySourceDocument],
) -> dict[int, MemorySourceDocument]:
    """`unchanged_sources`: logical_source_key -> the OLD revision's MemorySourceDocument row for
    every source confirmed unchanged this round (matched by content_sha256).

    Returns a mapping of old source-document id -> new (this revision's) source-document copy.
    """
    new_source_by_old_id: dict[int, MemorySourceDocument] = {}
    for old_source in unchanged_sources.values():
        new_source = MemorySourceDocument.objects.create(
            candidate_memory=new_revision,
            logical_source_key=old_source.logical_source_key,
            filename=old_source.filename,
            source_role=old_source.source_role,
            language=old_source.language,
            trust_status=old_source.trust_status,
            precedence=old_source.precedence,
            raw_content=old_source.raw_content,
            unchanged_from=old_source,
        )
        new_source_by_old_id[old_source.id] = new_source

    unchanged_source_ids = set(new_source_by_old_id.keys())
    old_claims = MemoryClaim.objects.filter(candidate_memory=old_revision).prefetch_related("supports")
    for old_claim in old_claims:
        support_source_ids = {s.memory_source_document_id for s in old_claim.supports.all()}
        if not support_source_ids or not support_source_ids.issubset(unchanged_source_ids):
            # At least one support comes from a new/changed/removed source this round -- do not
            # silently carry confirmation forward; it will be re-extracted (or dropped) instead.
            continue
        new_claim = MemoryClaim.objects.create(
            candidate_memory=new_revision,
            stable_key=old_claim.stable_key,
            canonical_text_en=old_claim.canonical_text_en,
            claim_type=old_claim.claim_type,
            subject_scope=old_claim.subject_scope,
            experience_level=old_claim.experience_level,
            resume_eligible=old_claim.resume_eligible,
            confirmation_status=old_claim.confirmation_status,
            duplicate_group_key=old_claim.duplicate_group_key,
            valid_from=old_claim.valid_from,
            valid_to=old_claim.valid_to,
            structured_value=old_claim.structured_value,
            legal_employer=old_claim.legal_employer,
            client_organization=old_claim.client_organization,
            presentation_mode=old_claim.presentation_mode,
        )
        for old_support in old_claim.supports.all():
            new_source = new_source_by_old_id[old_support.memory_source_document_id]
            MemoryClaimSupport.objects.create(
                memory_claim=new_claim,
                memory_source_document=new_source,
                quotation=old_support.quotation,
                start_line=old_support.start_line,
                end_line=old_support.end_line,
                source_language=old_support.source_language,
                support_role=old_support.support_role,
            )
    return new_source_by_old_id
