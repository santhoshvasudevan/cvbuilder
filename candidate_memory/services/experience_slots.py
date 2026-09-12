"""Operator-controlled ExperienceSlot creation, activation, and ordering (V2-D031)."""

from __future__ import annotations

from django.db import transaction

from candidate_memory.integrity import HardIntegrityError, assert_hard_integrity
from candidate_memory.models import CareerEngagement, ExperienceSlot, StaticResumeProfile


class ExperienceSlotServiceError(ValueError):
    """Invalid operator action against ExperienceSlot workflow rules."""


def ensure_static_profile(memory) -> StaticResumeProfile:
    profile, _ = StaticResumeProfile.objects.get_or_create(memory=memory)
    return profile


@transaction.atomic
def create_or_activate_slot(
    *,
    profile: StaticResumeProfile,
    engagement: CareerEngagement,
    sequence: int,
    is_primary: bool = True,
    is_active: bool = True,
) -> ExperienceSlot:
    """Explicit operator path only — copies static metadata and retains source linkage."""
    if engagement.memory_id != profile.memory_id:
        raise ExperienceSlotServiceError(
            "CareerEngagement and StaticResumeProfile must belong to the same CandidateMemory."
        )
    if sequence < 1:
        raise ExperienceSlotServiceError("ExperienceSlot sequence must be >= 1.")

    existing = (
        ExperienceSlot.objects.select_for_update()
        .filter(static_resume_profile=profile, career_engagement=engagement)
        .first()
    )
    if existing is not None:
        existing.sequence = sequence
        existing.is_primary = is_primary
        existing.is_active = is_active
        # Refresh copied metadata from the operator-owned engagement source.
        existing.company_name = engagement.company_name
        existing.role_title = engagement.role_title
        existing.location = engagement.location
        existing.start_date = engagement.start_date
        existing.end_date_or_present = engagement.end_date_or_present
        existing.save()
        return existing

    return ExperienceSlot.objects.create(
        static_resume_profile=profile,
        career_engagement=engagement,
        sequence=sequence,
        is_primary=is_primary,
        is_active=is_active,
        company_name=engagement.company_name,
        role_title=engagement.role_title,
        location=engagement.location,
        start_date=engagement.start_date,
        end_date_or_present=engagement.end_date_or_present,
    )


@transaction.atomic
def set_slot_order(profile: StaticResumeProfile, ordered_engagement_ids: list[int]) -> list[ExperienceSlot]:
    """Set explicit 1..N ordering for active primary slots from operator-selected engagements.

    Collision-safe under PostgreSQL's conditional unique active-primary sequence constraint:
    deactivate affected active-primary slots before assigning final sequences 1..N, then
    reactivate the operator-selected engagements in the requested order.
    """
    if len(ordered_engagement_ids) != len(set(ordered_engagement_ids)):
        raise ExperienceSlotServiceError("Ordered engagement IDs must be unique.")

    engagements: list[CareerEngagement] = []
    for engagement_id in ordered_engagement_ids:
        try:
            engagements.append(
                CareerEngagement.objects.get(pk=engagement_id, memory=profile.memory)
            )
        except CareerEngagement.DoesNotExist as exc:
            raise ExperienceSlotServiceError(
                f"CareerEngagement #{engagement_id} is not available for this candidate."
            ) from exc

    # Free sequence/engagement uniqueness before reassignment (reorder and replace cases).
    ExperienceSlot.objects.select_for_update().filter(
        static_resume_profile=profile,
        is_primary=True,
        is_active=True,
    ).update(is_active=False)

    slots: list[ExperienceSlot] = []
    for index, engagement in enumerate(engagements, start=1):
        slots.append(
            create_or_activate_slot(
                profile=profile,
                engagement=engagement,
                sequence=index,
                is_primary=True,
                is_active=True,
            )
        )

    # Keep only the operator-selected primary slots active.
    ExperienceSlot.objects.filter(
        static_resume_profile=profile,
        is_primary=True,
        is_active=True,
    ).exclude(pk__in=[slot.pk for slot in slots]).update(is_active=False)

    return slots


def validate_or_raise(profile: StaticResumeProfile) -> None:
    assert_hard_integrity(profile)


def require_valid_primary_slots(profile: StaticResumeProfile) -> list[ExperienceSlot]:
    try:
        assert_hard_integrity(profile)
    except HardIntegrityError:
        raise
    return list(
        ExperienceSlot.objects.filter(
            static_resume_profile=profile, is_primary=True, is_active=True
        ).order_by("sequence")
    )
