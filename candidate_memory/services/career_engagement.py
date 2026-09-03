"""Deterministic calculations spanning multiple `CareerEngagement` records (D-019, the
deterministic static-profile boundary). Single-engagement derivations (`duration_months`,
`is_current`, `displayed_organization`, `title_for_language`) live on the model itself, since they
need no query beyond `self`; this module holds the one calculation that genuinely needs a
collection of engagements: total non-overlapping experience.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Iterable

from ..models import CareerEngagement


@dataclasses.dataclass
class NonOverlappingExperienceResult:
    total_months: int
    included_engagement_ids: list[str]
    excluded_engagement_ids: list[str]


def total_non_overlapping_experience_months(
    engagements: Iterable[CareerEngagement], *, as_of: datetime.date | None = None
) -> NonOverlappingExperienceResult:
    """Total months of experience across `engagements`, merging overlapping date ranges so a
    period covered by two concurrent engagements (e.g. two overlapping consulting assignments) is
    never double-counted. An engagement whose `end_status` is `UNKNOWN` has no determinable end
    point and is excluded outright -- reported in `excluded_engagement_ids`, never silently
    guessed at or silently dropped."""
    intervals: list[tuple[int, int]] = []
    included: list[str] = []
    excluded: list[str] = []

    for engagement in engagements:
        if engagement.end_status == CareerEngagement.EndStatus.UNKNOWN:
            excluded.append(engagement.engagement_id)
            continue
        start_index = engagement.start_year * 12 + (engagement.start_month or 1)
        if engagement.end_status == CareerEngagement.EndStatus.PRESENT:
            reference = as_of or datetime.date.today()
            end_index = reference.year * 12 + reference.month
        else:
            end_index = engagement.end_year * 12 + (engagement.end_month or 12)
        intervals.append((start_index, end_index))
        included.append(engagement.engagement_id)

    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    total_months = sum(end - start for start, end in merged)
    return NonOverlappingExperienceResult(
        total_months=total_months,
        included_engagement_ids=included,
        excluded_engagement_ids=excluded,
    )
