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

# Month/year tokens used for same-engagement start/end date contradiction detection.
_MONTH_NAME_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_DATE_TOKEN = (
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|"
    r"\d{1,2}/\d{4}"
    r")"
)
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*[\u2013\u2014~–—-]+\s*"
    rf"(?P<end>Present|present|{_DATE_TOKEN})",
    re.IGNORECASE,
)

# Stable engagement keys for cross-source date comparison (generic, not Maruti-only).
ENGAGEMENT_ALIAS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmaruti\b", re.IGNORECASE), "maruti_suzuki"),
    (re.compile(r"\bcontinental\b", re.IGNORECASE), "continental"),
    (re.compile(r"\bford\b", re.IGNORECASE), "ford"),
    (re.compile(r"\bambigai\b", re.IGNORECASE), "ambigai"),
)


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


def _normalize_date_token(token: str) -> str | None:
    """Normalize a month/year token to YYYY-MM (or 'present')."""
    cleaned = token.strip()
    if cleaned.lower() == "present":
        return "present"
    numeric = re.fullmatch(r"(\d{1,2})/(\d{4})", cleaned)
    if numeric:
        month = int(numeric.group(1))
        year = int(numeric.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
        return None
    named = re.fullmatch(
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s+(\d{4})",
        cleaned,
        re.IGNORECASE,
    )
    if named:
        key = named.group(1).lower().rstrip(".")
        if key.startswith("sept"):
            month = 9
        else:
            month = _MONTH_NAME_TO_NUM.get(key) or _MONTH_NAME_TO_NUM.get(key[:3])
        year = int(named.group(2))
        if month is None:
            return None
        return f"{year:04d}-{month:02d}"
    return None


def _engagement_key_in_text(text: str) -> str | None:
    for pattern, key in ENGAGEMENT_ALIAS_PATTERNS:
        if pattern.search(text):
            return key
    return None


def _slug_company_phrase(text: str) -> str | None:
    """Derive a stable engagement slug from a company-like phrase preceding a date range."""
    without_dates = DATE_RANGE_RE.sub(" ", text)
    without_dates = re.sub(r"[*_`#]", "", without_dates)
    parts = re.split(r"\s*[\u2013\u2014|,—-]\s*", without_dates)
    if not parts:
        return None
    company = parts[0].strip()
    # Drop leading labels such as "Unternehmen:" / "Zeitraum:".
    company = re.sub(r"^(?:unternehmen|company|zeitraum|role|rolle)\s*:\s*", "", company, flags=re.I)
    if len(company) < 3:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_")
    if len(slug) < 3:
        return None
    return slug[:80]


def _nearest_heading_engagement_key(content: str, offset: int) -> str | None:
    """Resolve engagement from the nearest preceding markdown heading (section context)."""
    before = content[: max(0, offset)]
    headings = list(HEADING_RE.finditer(before))
    if not headings:
        return None
    heading_text = headings[-1].group(2)
    known = _engagement_key_in_text(heading_text)
    if known:
        return known
    return _slug_company_phrase(heading_text)


def _extract_date_range(text: str) -> tuple[str | None, str | None]:
    match = DATE_RANGE_RE.search(text)
    if not match:
        return None, None
    return _normalize_date_token(match.group("start")), _normalize_date_token(match.group("end"))


def _company_context_for_support(support: MemoryClaimSupport) -> str | None:
    """Resolve engagement key from claim text, then nearest source heading (not a wide window)."""
    for text in (support.claim.text, support.excerpt or ""):
        known = _engagement_key_in_text(text)
        if known:
            return known
        slug = _slug_company_phrase(text)
        # Only accept in-claim slugs when the claim also carries a date range (same-line company+dates).
        if slug is not None and _extract_date_range(text)[0] is not None:
            return slug

    document = support.source_document
    content = document.content_text or ""
    offset = support.char_start
    if offset is None:
        needle = (support.excerpt or support.claim.text or "")[:80]
        offset = content.find(needle) if needle else 0
    if offset < 0:
        offset = 0
    heading_key = _nearest_heading_engagement_key(content, offset)
    if heading_key:
        return heading_key
    # Last resort: known aliases in a tight backward window only.
    window = content[max(0, offset - 240) : offset]
    return _engagement_key_in_text(window)


def _detect_engagement_date_conflicts(memory: CandidateMemory) -> int:
    """Preserve unresolved same-engagement start/end date contradictions across sources.

    Date contradictions only — other contradiction categories remain out of scope here.
    """
    # engagement_key -> {"start": {norm: set[claim_id]}, "end": {...}, "claims": set}
    by_engagement: dict[str, dict[str, object]] = {}

    supports = (
        MemoryClaimSupport.objects.filter(claim__memory=memory)
        .select_related("claim", "source_document")
        .order_by("id")
    )
    for support in supports:
        start, end = _extract_date_range(support.claim.text)
        if start is None and end is None:
            start, end = _extract_date_range(support.excerpt or "")
        if start is None and end is None:
            continue
        engagement_key = _company_context_for_support(support)
        if engagement_key is None:
            continue
        bucket = by_engagement.setdefault(
            engagement_key,
            {"start": {}, "end": {}, "claims": set()},
        )
        claim = support.claim
        claims: set = bucket["claims"]  # type: ignore[assignment]
        claims.add(claim)
        if start is not None:
            starts: dict = bucket["start"]  # type: ignore[assignment]
            starts.setdefault(start, set()).add(claim)
        if end is not None and end != "present":
            ends: dict = bucket["end"]  # type: ignore[assignment]
            ends.setdefault(end, set()).add(claim)
        elif end == "present":
            ends = bucket["end"]  # type: ignore[assignment]
            ends.setdefault("present", set()).add(claim)

    created = 0
    for engagement_key, bucket in by_engagement.items():
        starts = bucket["start"]  # type: ignore[assignment]
        ends = bucket["end"]  # type: ignore[assignment]
        related = list(bucket["claims"])  # type: ignore[arg-type]
        if len(starts) >= 2:
            variants = ", ".join(sorted(starts.keys()))
            topic = f"engagement_start_date:{engagement_key}"
            conflict, was_created = MemoryConflict.objects.get_or_create(
                memory=memory,
                topic=topic,
                defaults={
                    "description": (
                        f"Sources disagree on start date for engagement '{engagement_key}' "
                        f"({variants}). Preserved unresolved for operator review."
                    ),
                    "status": MemoryConflict.Status.UNRESOLVED,
                },
            )
            if was_created:
                created += 1
            conflict.related_claims.add(*related)
        if len(ends) >= 2:
            variants = ", ".join(sorted(ends.keys()))
            topic = f"engagement_end_date:{engagement_key}"
            conflict, was_created = MemoryConflict.objects.get_or_create(
                memory=memory,
                topic=topic,
                defaults={
                    "description": (
                        f"Sources disagree on end date for engagement '{engagement_key}' "
                        f"({variants}). Preserved unresolved for operator review."
                    ),
                    "status": MemoryConflict.Status.UNRESOLVED,
                },
            )
            if was_created:
                created += 1
            conflict.related_claims.add(*related)
    return created


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

    created += _detect_engagement_date_conflicts(memory)
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
