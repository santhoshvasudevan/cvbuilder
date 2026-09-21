from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from candidate_memory.forms import ExperienceSlotSelectionForm
from candidate_memory.integrity import (
    HardIntegrityError,
    assert_hard_integrity,
    validate_static_resume_profile,
)
from candidate_memory.models import CandidateMemory, CareerEngagement, ExperienceSlot
from candidate_memory.services.experience_slots import (
    ExperienceSlotServiceError,
    ensure_static_profile,
    set_slot_order,
)


@require_http_methods(["GET", "POST"])
def experience_slot_workspace(request, memory_id: int):
    """Server-rendered operator workflow: select → create/activate → order → validate."""
    memory = get_object_or_404(CandidateMemory, pk=memory_id)
    profile = ensure_static_profile(memory)
    engagements = CareerEngagement.objects.filter(memory=memory).order_by("sort_hint", "id")
    form = ExperienceSlotSelectionForm(request.POST or None, memory=memory)

    if request.method == "POST" and form.is_valid():
        try:
            set_slot_order(profile, form.ordered_engagement_ids())
            assert_hard_integrity(profile)
            messages.success(
                request,
                "Exact three active primary ExperienceSlots saved and validated.",
            )
            return redirect("candidate_memory:experience_slots", memory_id=memory.pk)
        except (ExperienceSlotServiceError, HardIntegrityError) as exc:
            messages.error(request, str(exc))

    slots = ExperienceSlot.objects.filter(static_resume_profile=profile).order_by("sequence", "id")
    findings = validate_static_resume_profile(profile)
    return render(
        request,
        "candidate_memory/experience_slots.html",
        {
            "memory": memory,
            "profile": profile,
            "engagements": engagements,
            "slots": slots,
            "form": form,
            "integrity_findings": findings,
            "integrity_ok": not findings,
        },
    )


@require_http_methods(["GET"])
def memory_overview(request, memory_id: int):
    memory = get_object_or_404(CandidateMemory, pk=memory_id)
    return render(
        request,
        "candidate_memory/overview.html",
        {
            "memory": memory,
            "profile": getattr(memory, "profile", None),
            "static_profile": getattr(memory, "static_resume_profile", None),
            "conflicts": memory.conflicts.filter(status="UNRESOLVED"),
            "corrections": memory.operator_corrections.all()[:50],
            "positioning_history": memory.positioning_history.all()[:50],
            "source_documents": memory.source_documents.all(),
        },
    )
