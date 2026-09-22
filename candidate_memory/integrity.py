"""Deterministic HARD_INTEGRITY checks (V2-D026) including provenance immutability (FACT-002)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from candidate_memory.models import ExperienceSlot, StaticResumeProfile

REQUIRED_ACTIVE_PRIMARY_SEQUENCES = (1, 2, 3)
STATIC_METADATA_FIELDS = (
    "company_name",
    "role_title",
    "location",
    "start_date",
    "end_date_or_present",
)


@dataclass(frozen=True)
class IntegrityFinding:
    code: str
    message: str


class HardIntegrityError(Exception):
    """Raised when a HARD_INTEGRITY check fails closed."""

    def __init__(self, findings: list[IntegrityFinding]):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


def provenance_immutability_findings(
    *,
    model_label: str,
    operation: str,
) -> list[IntegrityFinding]:
    """Fail-closed findings for post-creation provenance mutation or deletion (AUDIT-002)."""
    return [
        IntegrityFinding(
            code="PROVENANCE_IMMUTABLE",
            message=(
                f"{model_label} provenance is append-only after creation; "
                f"rejected {operation}."
            ),
        )
    ]


def reject_provenance_mutation(*, model_label: str, operation: str) -> None:
    """Raise HardIntegrityError for forbidden provenance write/delete paths."""
    raise HardIntegrityError(
        provenance_immutability_findings(model_label=model_label, operation=operation)
    )


def active_primary_slots(profile: StaticResumeProfile):
    from candidate_memory.models import ExperienceSlot

    return ExperienceSlot.objects.filter(
        static_resume_profile=profile,
        is_primary=True,
        is_active=True,
    ).order_by("sequence", "id")


def validate_experience_slot_cardinality(profile: StaticResumeProfile) -> list[IntegrityFinding]:
    """Reject any state other than exactly three unique active primary slots sequenced 1/2/3."""
    findings: list[IntegrityFinding] = []
    slots = list(active_primary_slots(profile))
    sequences = [slot.sequence for slot in slots]
    engagement_ids = [slot.career_engagement_id for slot in slots]

    if len(slots) != 3:
        findings.append(
            IntegrityFinding(
                code="SLOT_CARDINALITY",
                message=(
                    f"Expected exactly 3 active primary ExperienceSlots, found {len(slots)}."
                ),
            )
        )
    if sequences != list(REQUIRED_ACTIVE_PRIMARY_SEQUENCES):
        findings.append(
            IntegrityFinding(
                code="SLOT_SEQUENCE",
                message=(
                    "Active primary ExperienceSlots must use sequences 1, 2, and 3; "
                    f"found {sequences}."
                ),
            )
        )
    if len(set(engagement_ids)) != len(engagement_ids):
        findings.append(
            IntegrityFinding(
                code="SLOT_ENGAGEMENT_UNIQUE",
                message="Active primary ExperienceSlots must reference unique CareerEngagements.",
            )
        )
    return findings


def validate_static_resume_profile(profile: StaticResumeProfile) -> list[IntegrityFinding]:
    return validate_experience_slot_cardinality(profile)


def assert_hard_integrity(profile: StaticResumeProfile) -> None:
    findings = validate_static_resume_profile(profile)
    if findings:
        raise HardIntegrityError(findings)


def reject_model_static_metadata_write(
    slot: ExperienceSlot,
    proposed_fields: dict[str, str],
) -> list[IntegrityFinding]:
    """Model output must never overwrite operator-owned static metadata (STATIC-002, FACT-005)."""
    findings: list[IntegrityFinding] = []
    for field in STATIC_METADATA_FIELDS:
        if field not in proposed_fields:
            continue
        proposed = proposed_fields[field]
        current = getattr(slot, field)
        if proposed != current:
            findings.append(
                IntegrityFinding(
                    code="STATIC_METADATA_REPLACEMENT",
                    message=(
                        f"Rejected model attempt to replace ExperienceSlot.{field} "
                        f"from {current!r} to {proposed!r}."
                    ),
                )
            )
    return findings
