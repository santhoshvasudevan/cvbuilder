"""Deterministic candidate source ingestion with provenance, idempotency, and conflict preservation.

Never treats docs/CANDIDATE_MEMORY_SNAPSHOT.md (or any GENERATED_REFERENCE_ONLY document) as
evidence. No LLM / provider calls occur.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from candidate_memory.models import (
    CandidateMemory,
    CandidateProfile,
    MemoryClaim,
    MemoryClaimSupport,
    MemoryConflict,
    MemorySourceDocument,
    StaticResumeProfile,
)

GENERATED_MARKER = "GENERATED_REFERENCE_ONLY: true"
EXCLUDED_PATH_SUFFIXES = (
    "docs/CANDIDATE_MEMORY_SNAPSHOT.md",
    "CANDIDATE_MEMORY_SNAPSHOT.md",
)

SOURCE_KIND_BY_NAME = {
    "AC-MEMORY_PROFILE.md": (
        MemorySourceDocument.SourceKind.MEMORY_PROFILE,
        1,
    ),
    "AC-profile_english.md": (
        MemorySourceDocument.SourceKind.PROFILE_ENGLISH,
        2,
    ),
    "AC-profile_german.md": (
        MemorySourceDocument.SourceKind.PROFILE_GERMAN,
        3,
    ),
}

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class IngestResult:
    document: MemorySourceDocument | None
    created: bool
    skipped_reason: str = ""
    claims_created: int = 0
    conflicts_created: int = 0


def _repo_root() -> Path:
    return Path(settings.BASE_DIR)


def is_excluded_source(path: Path, text: str) -> str:
    normalized = path.as_posix()
    for suffix in EXCLUDED_PATH_SUFFIXES:
        if normalized.endswith(suffix):
            return "generated_snapshot_path"
    header = "\n".join(text.splitlines()[:20])
    if GENERATED_MARKER in header or "CANDIDATE_MEMORY_EVIDENCE_SOURCE: false" in header:
        return "generated_reference_marker"
    if "REINGESTION_ALLOWED: false" in header and "GENERATED_REFERENCE_ONLY" in header:
        return "generated_reference_marker"
    return ""


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim_key(category: str, text: str) -> str:
    digest = hashlib.sha256(f"{category}|{text.strip()}".encode("utf-8")).hexdigest()[:16]
    return f"{category.lower()}:{digest}"


def _iter_section_claims(text: str) -> list[tuple[str, str, str, int, int]]:
    """Return (category, claim_text, location_hint, start, end) tuples from markdown structure."""
    claims: list[tuple[str, str, str, int, int]] = []
    for match in HEADING_RE.finditer(text):
        heading = match.group(2).strip()
        category = _category_for_heading(heading)
        start, end = match.span()
        claims.append((category, heading, f"heading:{heading[:80]}", start, end))
    for match in BULLET_RE.finditer(text):
        bullet = match.group(1).strip()
        if len(bullet) < 12:
            continue
        category = _category_for_bullet(bullet)
        start, end = match.span()
        claims.append((category, bullet, f"bullet@{start}", start, end))
    return claims


def _category_for_heading(heading: str) -> str:
    lower = heading.lower()
    if "certif" in lower:
        return MemoryClaim.Category.CERTIFICATION
    if "language" in lower or "sprach" in lower:
        return MemoryClaim.Category.LANGUAGE
    if "educat" in lower or "ausbildung" in lower:
        return MemoryClaim.Category.EDUCATION
    if "career" in lower or "experience" in lower or "employment" in lower:
        return MemoryClaim.Category.CAREER_HISTORY
    if "gap" in lower or "constraint" in lower or "limitation" in lower:
        return MemoryClaim.Category.GAP_OR_CONSTRAINT
    if "skill" in lower or "capabilit" in lower:
        return MemoryClaim.Category.SKILL
    if "achieve" in lower or "metric" in lower:
        return MemoryClaim.Category.ACHIEVEMENT
    return MemoryClaim.Category.OTHER


def _category_for_bullet(bullet: str) -> str:
    lower = bullet.lower()
    if any(token in lower for token in ("gap", "do not claim", "awareness only", "currently learning")):
        return MemoryClaim.Category.GAP_OR_CONSTRAINT
    if any(token in lower for token in ("certificate", "certified", "certification")):
        return MemoryClaim.Category.CERTIFICATION
    if any(token in lower for token in ("english", "german", "tamil", "cefr", "b1", "b2", "c1")):
        return MemoryClaim.Category.LANGUAGE
    if any(token in lower for token in ("gmbh", "ltd", "inc", "university", "20")):
        return MemoryClaim.Category.CAREER_HISTORY
    return MemoryClaim.Category.OTHER


def _detect_conflicts(memory: CandidateMemory) -> int:
    """Preserve unresolved contradictions across ingested sources for operator review."""
    created = 0
    language_claims = list(
        MemoryClaim.objects.filter(memory=memory, category=MemoryClaim.Category.LANGUAGE)
    )
    german_variants = {
        claim.text.strip()
        for claim in language_claims
        if "german" in claim.text.lower() or "deutsch" in claim.text.lower()
    }
    if len(german_variants) >= 2:
        conflict, was_created = MemoryConflict.objects.get_or_create(
            memory=memory,
            topic="german_language_level",
            defaults={
                "description": (
                    "Sources disagree on German language proficiency level. "
                    "Preserved unresolved for operator review."
                ),
                "status": MemoryConflict.Status.UNRESOLVED,
            },
        )
        if was_created:
            created += 1
        conflict.related_claims.add(*language_claims)

    employment_markers = list(
        MemoryClaim.objects.filter(memory=memory).filter(
            text__icontains="Ambigai"
        )
    )
    direct_markers = list(
        MemoryClaim.objects.filter(memory=memory).filter(text__icontains="Ford Motor")
    )
    if employment_markers and direct_markers:
        conflict, was_created = MemoryConflict.objects.get_or_create(
            memory=memory,
            topic="employment_structure_and_dates",
            defaults={
                "description": (
                    "Sources disagree on direct employment versus agency contracting "
                    "(Ambigai) structure/dates/location. Preserved unresolved."
                ),
                "status": MemoryConflict.Status.UNRESOLVED,
            },
        )
        if was_created:
            created += 1
        conflict.related_claims.add(*(employment_markers + direct_markers))
    return created


def ensure_profile_shell(memory: CandidateMemory) -> tuple[CandidateProfile, StaticResumeProfile]:
    profile, _ = CandidateProfile.objects.get_or_create(memory=memory)
    static_profile, _ = StaticResumeProfile.objects.get_or_create(memory=memory)
    return profile, static_profile


@transaction.atomic
def ingest_source_file(memory: CandidateMemory, path: Path) -> IngestResult:
    path = path.resolve()
    text = path.read_text(encoding="utf-8")
    skip_reason = is_excluded_source(path, text)
    if skip_reason:
        return IngestResult(document=None, created=False, skipped_reason=skip_reason)

    relative = path
    try:
        relative = path.relative_to(_repo_root())
    except ValueError:
        relative = Path(path.name)

    source_name = path.name
    kind, precedence = SOURCE_KIND_BY_NAME.get(
        source_name,
        (MemorySourceDocument.SourceKind.OTHER, 100),
    )
    digest = _content_sha256(text)
    existing = MemorySourceDocument.objects.filter(memory=memory, content_sha256=digest).first()
    if existing is not None:
        return IngestResult(document=existing, created=False, skipped_reason="idempotent_hash_match")

    path_existing = MemorySourceDocument.objects.filter(
        memory=memory, source_path=relative.as_posix()
    ).first()
    if path_existing is not None and path_existing.content_sha256 != digest:
        # Same logical path, changed content: keep prior provenance rows; store as revised path key.
        relative = Path(f"{relative.as_posix()}#sha256:{digest[:12]}")

    document = MemorySourceDocument.objects.create(
        memory=memory,
        source_path=relative.as_posix(),
        source_kind=kind,
        precedence_rank=precedence,
        content_sha256=digest,
        content_text=text,
        byte_size=len(text.encode("utf-8")),
    )

    claims_created = 0
    for category, claim_text, location_hint, start, end in _iter_section_claims(text):
        key = _claim_key(category, claim_text)
        claim, created = MemoryClaim.objects.get_or_create(
            memory=memory,
            claim_key=key,
            defaults={
                "text": claim_text,
                "category": category,
            },
        )
        if created:
            claims_created += 1
        MemoryClaimSupport.objects.get_or_create(
            claim=claim,
            source_document=document,
            defaults={
                "excerpt": claim_text[:1000],
                "location_hint": location_hint,
                "char_start": start,
                "char_end": end,
            },
        )

    ensure_profile_shell(memory)
    conflicts_created = _detect_conflicts(memory)
    return IngestResult(
        document=document,
        created=True,
        claims_created=claims_created,
        conflicts_created=conflicts_created,
    )


@transaction.atomic
def ingest_default_candidate_sources(memory: CandidateMemory | None = None) -> list[IngestResult]:
    """Ingest the three authoritative AC sources; never the generated snapshot."""
    if memory is None:
        memory = CandidateMemory.objects.create(label="default", is_active=True)
    ensure_profile_shell(memory)
    root = _repo_root()
    sources = [
        root / "docs" / "AC" / "AC-MEMORY_PROFILE.md",
        root / "docs" / "AC" / "AC-profile_english.md",
        root / "docs" / "AC" / "AC-profile_german.md",
        root / "docs" / "CANDIDATE_MEMORY_SNAPSHOT.md",  # must be excluded
    ]
    return [ingest_source_file(memory, path) for path in sources if path.exists()]
