"""Turns validated ExtractedItems into stored MemoryClaim/MemoryClaimSupport/CandidateRule rows,
including exact provenance validation (D-003) and English/German duplicate grouping (D-015)."""

from __future__ import annotations

import uuid

from ..models import CandidateMemory, CandidateRule, MemoryClaim, MemoryClaimSupport, MemorySourceDocument
from ..schemas import ContentPlane, ExtractedItem
from .classification import validate_item
from .comparable_values import COMPARABLE_CLAIM_TYPES, comparison_payload_for_item
from .quote_recovery import recover_quote
from .subject_scope import normalize_subject_scope


class ProvenanceError(Exception):
    pass


def duplicate_group_key(item: ExtractedItem) -> str:
    if item.duplicate_group_hint:
        return item.duplicate_group_hint.strip().lower().replace(" ", "_")
    if item.claim_type in COMPARABLE_CLAIM_TYPES:
        # Audit repair: two employment_dates/employment_location/language_proficiency items
        # about the same subject_scope are not necessarily the same fact restated -- they may be
        # genuinely different candidate values (e.g. a stale corpus date range vs. a corrected
        # operator-update date range) that the conflict detector needs as *separate* MemoryClaim
        # rows to compare. Falling back to the coarse scope+type key here would silently merge
        # them into one claim's supports before conflict detection ever runs, which is exactly the
        # kind of hidden bug the fixture-only tests never caught. Only an explicit
        # duplicate_group_hint (checked above) may say "these are the same underlying fact."
        return f"__no_fallback_merge__::{uuid.uuid4()}"
    return f"{(item.subject_scope or '').strip().lower()}::{(item.claim_type or '').strip().lower()}"


def _verify_quote_at_lines(
    source_document: MemorySourceDocument, quote: str, start_line: int, end_line: int
) -> bool:
    lines = source_document.raw_content.splitlines()
    excerpt = "\n".join(lines[start_line - 1 : end_line])
    return quote.strip() in excerpt


def store_extracted_item(
    item: ExtractedItem,
    *,
    candidate_memory: CandidateMemory,
    source_document: MemorySourceDocument,
) -> MemoryClaim | CandidateRule:
    if item.plane == ContentPlane.EVIDENCE:
        # Extraction-quality repair (2026-09-02): fill in or reshape subject_scope from the
        # item's own structured/extracted fields when it is missing or non-canonical, before
        # validation checks for its presence. Never invents a value -- see subject_scope.py.
        normalized_scope = normalize_subject_scope(item)
        if normalized_scope is not None:
            item.subject_scope = normalized_scope

    validate_item(item)

    if not source_document.verify_content_hash():
        raise ProvenanceError(
            f"MemorySourceDocument {source_document.pk} content hash mismatch -- refusing to "
            "store an item against it."
        )
    if not _verify_quote_at_lines(
        source_document, item.support.quote, item.support.start_line, item.support.end_line
    ):
        # D-017 fix: the model-supplied quote/line-range didn't resolve exactly -- a known real
        # failure mode when a supporting sentence word-wraps across two physical source lines and
        # the model reconstructs its quote with a space where the source has an actual newline.
        # Attempt a whitespace-normalized *location* of the quote (never a fuzzy/semantic match,
        # and only accepted if it locates a *unique* position); recover the real original slice at
        # that position and re-run the exact same validator against it before trusting anything.
        recovered = recover_quote(source_document.raw_content, item.support.quote)
        if recovered is None:
            raise ProvenanceError(
                f"Quote does not resolve to an exact substring of source lines "
                f"{item.support.start_line}-{item.support.end_line} in {source_document.filename}, "
                "and no unique whitespace-normalized match exists elsewhere in the source either."
            )
        if not _verify_quote_at_lines(
            source_document, recovered.text, recovered.start_line, recovered.end_line
        ):
            # Defensive-in-depth: recovery is expected to always re-validate by construction: it
            # never fires. If it somehow didn't, fail closed rather than trust an unverified
            # recovery -- this is not a code path any test should be able to reach.
            raise ProvenanceError(
                f"Recovered quote failed re-validation against source lines "
                f"{recovered.start_line}-{recovered.end_line} in {source_document.filename}."
            )
        # Use the recovered, verified-exact original text/line-range for everything stored below
        # -- never the model's own possibly-inaccurate quote/line values, and never the
        # whitespace-normalized string itself.
        item.support.quote = recovered.text
        item.support.start_line = recovered.start_line
        item.support.end_line = recovered.end_line

    if item.plane == ContentPlane.EVIDENCE:
        return _store_claim(item, candidate_memory=candidate_memory, source_document=source_document)

    return CandidateRule.objects.create(
        candidate_memory=candidate_memory,
        rule_type=item.rule_type.value,
        text=item.canonical_text_en,
        scope=item.scope or "",
        source_document=source_document,
        source_quote=item.support.quote,
        start_line=item.support.start_line,
        end_line=item.support.end_line,
    )


def _store_claim(
    item: ExtractedItem, *, candidate_memory: CandidateMemory, source_document: MemorySourceDocument
) -> MemoryClaim:
    group_key = duplicate_group_key(item)
    claim = MemoryClaim.objects.filter(
        candidate_memory=candidate_memory, duplicate_group_key=group_key
    ).first()
    is_new_claim = claim is None
    if is_new_claim:
        claim = MemoryClaim.objects.create(
            candidate_memory=candidate_memory,
            stable_key=group_key,
            canonical_text_en=item.canonical_text_en,
            claim_type=item.claim_type,
            subject_scope=item.subject_scope,
            experience_level=item.experience_level.value if item.experience_level else None,
            resume_eligible=item.resume_eligible,
            duplicate_group_key=group_key,
            # Legal-employer-vs-client separation (operator resolution 2026-09-02, item 1/2):
            # only ever set when the source itself distinguishes them; presentation defaults to
            # CLIENT_CENTRIC (the model's default) and is never inferred beyond what item states.
            legal_employer=item.legal_employer or "",
            client_organization=item.client_organization or "",
            # Comparable-claim-type structured payload (audit repair): validated typed data for
            # employment_dates/employment_location/language_proficiency, or {} when a comparable
            # claim_type's matching field was left null -- deliberately not defaulted/guessed, so
            # conflict detection and confirmation both fail closed on it (see comparable_values.py).
            structured_value=comparison_payload_for_item(item),
        )

    if item.support.language == "en":
        support_role = (
            MemoryClaimSupport.SupportRole.PRIMARY
            if is_new_claim
            else MemoryClaimSupport.SupportRole.CORROBORATING
        )
    else:
        support_role = MemoryClaimSupport.SupportRole.GERMAN_EXPRESSION

    MemoryClaimSupport.objects.create(
        memory_claim=claim,
        memory_source_document=source_document,
        quotation=item.support.quote,
        start_line=item.support.start_line,
        end_line=item.support.end_line,
        source_language=item.support.language,
        support_role=support_role,
    )
    return claim
