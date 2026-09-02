"""Deterministic snapshot generation from the active PostgreSQL Candidate Memory revision
(requirements.md Sec 4/16, D-015).

The snapshot is a read-only export: generating it never mutates the CandidateMemory revision it
reads from. It remains a human reference only -- never evidence, never re-ingested, never default
LLM context (see the header markers this function writes and CLAUDE.md).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.conf import settings

from ..models import CandidateMemory, MemoryClaim, MemoryConflict

DEFAULT_SNAPSHOT_PATH = Path(settings.BASE_DIR) / "docs" / "CANDIDATE_MEMORY_SNAPSHOT.md"

_HEADER = """GENERATED_REFERENCE_ONLY: true
RUNTIME_PROMPT_INPUT: false
CANDIDATE_MEMORY_EVIDENCE_SOURCE: false
REINGESTION_ALLOWED: false

# Candidate Memory Snapshot (Generated)

**This snapshot was generated from the active PostgreSQL Candidate Memory revision.** It is not
an evidence source, not authoritative runtime state, and not default LLM context. It must never
be re-ingested as Candidate Memory evidence, and it must not be loaded by default or passed to a
pipeline prompt -- see `CLAUDE.md`. The authoritative operational memory is the PostgreSQL
`CandidateMemory` revision this file was generated from, not this file.
"""


class NoActiveRevisionError(Exception):
    pass


def _employer_presentation_line(claim: MemoryClaim) -> str | None:
    """Audit repair: `presentation_mode` actually changes what's *displayed* here -- it never
    touches the underlying `legal_employer`/`client_organization` facts on the `MemoryClaim` row,
    which stay in PostgreSQL exactly as extracted/confirmed regardless of which mode is active.
    Only rendered when the source itself distinguished a legal employer from a client
    organization (operator resolution 2026-09-02, item 1/2) -- an ordinary direct-employment claim
    has neither field set and is unaffected."""
    if not (claim.legal_employer and claim.client_organization):
        return None
    mode = claim.presentation_mode
    if mode == MemoryClaim.PresentationMode.LEGAL_EMPLOYER_EXPLICIT:
        return f"  (legal employer: {claim.legal_employer}; client assignment: {claim.client_organization})"
    if mode == MemoryClaim.PresentationMode.COMBINED:
        return f"  ({claim.client_organization} -- client assignment; legal employer: {claim.legal_employer})"
    # CLIENT_CENTRIC (default): the client organization is already the section heading
    # (subject_scope) -- the legal employer is deliberately not named here.
    return "  (client assignment)"


def _render_claim(claim: MemoryClaim) -> str:
    lines = [f"- **[{claim.claim_id}]** {claim.canonical_text_en}"]
    employer_line = _employer_presentation_line(claim)
    if employer_line:
        lines.append(employer_line)
    sources = ", ".join(
        f"{s.memory_source_document.filename} L{s.start_line}-{s.end_line}" for s in claim.supports.all()
    )
    if sources:
        lines.append(f"  (source: {sources})")
    return "\n".join(lines)


def generate_snapshot_markdown(candidate_memory: CandidateMemory) -> str:
    if candidate_memory.status != CandidateMemory.Status.ACTIVE:
        raise NoActiveRevisionError(
            f"CandidateMemory {candidate_memory.pk} is {candidate_memory.status}, not ACTIVE; "
            "the snapshot may only be generated from the active revision."
        )

    parts = [_HEADER, f"\nGenerated from CandidateMemory version {candidate_memory.version} "
             f"(activated {candidate_memory.activated_at}).\n"]

    parts.append("\n## Source manifest\n")
    for source in candidate_memory.source_documents.all().order_by("precedence"):
        parts.append(
            f"- `{source.filename}` ({source.source_role}, {source.language}, "
            f"precedence {source.precedence}) -- sha256 `{source.content_sha256}`"
        )

    parts.append("\n\n## Confirmed, resume-eligible claims\n")
    confirmed = candidate_memory.claims.filter(
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True
    ).prefetch_related("supports__memory_source_document")
    by_scope: dict[str, list[MemoryClaim]] = {}
    for claim in confirmed:
        by_scope.setdefault(claim.subject_scope, []).append(claim)
    for scope, claims in sorted(by_scope.items()):
        parts.append(f"\n### {scope}\n")
        for claim in claims:
            parts.append(_render_claim(claim))

    parts.append("\n\n## Candidate rules (constraints / positioning)\n")
    for rule in candidate_memory.rules.all():
        scope_note = f" (scope: {rule.scope})" if rule.scope else ""
        parts.append(f"- **{rule.rule_type}**{scope_note}: {rule.text}")

    parts.append("\n\n## Unresolved conflicts\n")
    open_conflicts = candidate_memory.conflicts.filter(status=MemoryConflict.Status.OPEN)
    if not open_conflicts.exists():
        parts.append("None.")
    for conflict in open_conflicts:
        claim_ids = ", ".join(c.claim_id for c in conflict.involved_claims.all())
        parts.append(f"- **{conflict.conflict_key}**: {conflict.description} (claims: {claim_ids})")

    return "\n".join(parts) + "\n"


def export_snapshot(path: Path | str | None = None) -> Path:
    """Write the snapshot for the current ACTIVE revision to `path` (default:
    docs/CANDIDATE_MEMORY_SNAPSHOT.md). Read-only with respect to Candidate Memory -- this is an
    export, never a mutation.

    Audit repair: the write is atomic. Content goes to a temporary file in the same directory
    first, flushed and fsync'd, then swapped into place with `os.replace()` -- a single atomic
    rename on POSIX and Windows alike -- so a crash or interruption mid-write can never leave a
    truncated/corrupt snapshot in `target`'s place; the previous snapshot (if any) stays intact
    until the moment the new one is fully written. The temporary file is cleaned up on failure.
    """
    active = CandidateMemory.objects.filter(status=CandidateMemory.Status.ACTIVE).first()
    if active is None:
        raise NoActiveRevisionError("No ACTIVE CandidateMemory revision exists to export from.")

    content = generate_snapshot_markdown(active)
    target = Path(path) if path is not None else DEFAULT_SNAPSHOT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target
