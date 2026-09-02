"""Server-rendered Candidate Memory operator UI (requirements.md Sec 4/16, D-015).

No SPA, no API endpoints -- plain Django views + templates. Every state-changing action is a
POST-only view guarded by Django's CSRF middleware, and every one of them delegates to
`services/lifecycle.py` rather than touching models directly, so "read-only through services,
views, forms, and admin" (for ACTIVE/SUPERSEDED revisions) holds in exactly one place.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .exceptions import ExistingWorkingRevisionError, InvalidActivationError, RevisionNotEditableError
from .models import CandidateMemory, MemoryClaim, MemoryConflict
from .services import lifecycle as lifecycle_service
from .services import revision as revision_service
from .services.bootstrap import build_revision_from_operator_text
from .services.snapshot_export import NoActiveRevisionError, export_snapshot

_CORRECTABLE_FIELDS = (
    "canonical_text_en",
    "claim_type",
    "subject_scope",
    "legal_employer",
    "client_organization",
    "presentation_mode",
)


def _get_revision(version: int) -> CandidateMemory:
    return get_object_or_404(CandidateMemory, version=version)


def overview(request):
    revisions = CandidateMemory.objects.all()
    active = revisions.filter(status=CandidateMemory.Status.ACTIVE).first()
    working = revisions.filter(
        status__in=[CandidateMemory.Status.BUILDING, CandidateMemory.Status.NEEDS_REVIEW]
    ).first()
    return render(
        request,
        "candidate_memory/overview.html",
        {"revisions": revisions, "active": active, "working": working},
    )


def revision_detail(request, version: int):
    revision = _get_revision(version)
    sources = revision.source_documents.all()
    claim_counts = {
        "total": revision.claims.count(),
        "confirmed": revision.claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED
        ).count(),
        "unconfirmed": revision.claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED
        ).count(),
        "blocked": revision.claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT
        ).count(),
        "retired": revision.claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED
        ).count(),
    }
    open_conflicts = revision.conflicts.filter(status=MemoryConflict.Status.OPEN).count()
    return render(
        request,
        "candidate_memory/revision_detail.html",
        {
            "revision": revision,
            "sources": sources,
            "claim_counts": claim_counts,
            "rule_count": revision.rules.count(),
            "open_conflicts": open_conflicts,
        },
    )


def claims_list(request, version: int):
    revision = _get_revision(version)
    claims = revision.claims.all().prefetch_related("supports")

    q = request.GET.get("q", "").strip()
    claim_type = request.GET.get("claim_type", "").strip()
    experience_level = request.GET.get("experience_level", "").strip()
    confirmation_status = request.GET.get("confirmation_status", "").strip()
    eligible = request.GET.get("eligible", "").strip()

    if q:
        claims = claims.filter(subject_scope__icontains=q)
    if claim_type:
        claims = claims.filter(claim_type=claim_type)
    if experience_level:
        claims = claims.filter(experience_level=experience_level)
    if confirmation_status:
        claims = claims.filter(confirmation_status=confirmation_status)
    if eligible == "yes":
        claims = claims.filter(resume_eligible=True)
    elif eligible == "no":
        claims = claims.filter(resume_eligible=False)

    return render(
        request,
        "candidate_memory/claims_list.html",
        {
            "revision": revision,
            "claims": claims,
            "confirmation_choices": MemoryClaim.ConfirmationStatus.choices,
            "experience_choices": MemoryClaim.ExperienceLevel.choices,
            "claim_type_choices": revision.claims.order_by().values_list("claim_type", flat=True).distinct(),
            "filters": {
                "q": q,
                "claim_type": claim_type,
                "experience_level": experience_level,
                "confirmation_status": confirmation_status,
                "eligible": eligible,
            },
        },
    )


def claim_detail(request, version: int, claim_id: str):
    revision = _get_revision(version)
    claim = get_object_or_404(MemoryClaim, candidate_memory=revision, claim_id=claim_id)
    supports = claim.supports.all().select_related("memory_source_document")
    conflicts = claim.conflicts.all().prefetch_related("involved_claims")
    return render(
        request,
        "candidate_memory/claim_detail.html",
        {
            "revision": revision,
            "claim": claim,
            "supports": supports,
            "conflicts": conflicts,
            "presentation_choices": MemoryClaim.PresentationMode.choices,
        },
    )


@require_POST
def claim_confirm(request, version: int, claim_id: str):
    revision = _get_revision(version)
    claim = get_object_or_404(MemoryClaim, candidate_memory=revision, claim_id=claim_id)
    try:
        lifecycle_service.confirm_claim(claim)
        messages.success(request, f"{claim.claim_id} confirmed.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:claim_detail", version=version, claim_id=claim_id)


@require_POST
def claim_retire(request, version: int, claim_id: str):
    revision = _get_revision(version)
    claim = get_object_or_404(MemoryClaim, candidate_memory=revision, claim_id=claim_id)
    try:
        lifecycle_service.retire_claim(claim)
        messages.success(request, f"{claim.claim_id} retired.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:claim_detail", version=version, claim_id=claim_id)


@require_POST
def claim_restore(request, version: int, claim_id: str):
    revision = _get_revision(version)
    claim = get_object_or_404(MemoryClaim, candidate_memory=revision, claim_id=claim_id)
    try:
        lifecycle_service.restore_claim(claim)
        messages.success(request, f"{claim.claim_id} restored to unconfirmed for re-review.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:claim_detail", version=version, claim_id=claim_id)


@require_POST
def claim_correct(request, version: int, claim_id: str):
    revision = _get_revision(version)
    claim = get_object_or_404(MemoryClaim, candidate_memory=revision, claim_id=claim_id)
    fields = {name: request.POST[name] for name in _CORRECTABLE_FIELDS if name in request.POST}
    try:
        lifecycle_service.correct_claim(claim, **fields)
        messages.success(request, f"{claim.claim_id} updated.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:claim_detail", version=version, claim_id=claim_id)


def rules_list(request, version: int):
    revision = _get_revision(version)
    rules = revision.rules.all()
    return render(request, "candidate_memory/rules_list.html", {"revision": revision, "rules": rules})


def conflicts_inbox(request, version: int):
    revision = _get_revision(version)
    conflicts = revision.conflicts.all().prefetch_related("involved_claims")
    return render(
        request, "candidate_memory/conflicts_inbox.html", {"revision": revision, "conflicts": conflicts}
    )


@require_POST
def conflict_resolve(request, version: int, conflict_id: int):
    revision = _get_revision(version)
    conflict = get_object_or_404(MemoryConflict, candidate_memory=revision, pk=conflict_id)
    resolved_claim = None
    resolved_claim_id = request.POST.get("resolved_claim_id", "").strip()
    if resolved_claim_id:
        resolved_claim = get_object_or_404(
            MemoryClaim, candidate_memory=revision, claim_id=resolved_claim_id
        )
    note = request.POST.get("resolution_note", "")
    try:
        lifecycle_service.resolve_conflict(conflict, resolved_claim=resolved_claim, resolution_note=note)
        messages.success(request, f"Conflict {conflict.conflict_key} resolved.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:conflicts_inbox", version=version)


@require_POST
def conflict_dismiss(request, version: int, conflict_id: int):
    revision = _get_revision(version)
    conflict = get_object_or_404(MemoryConflict, candidate_memory=revision, pk=conflict_id)
    note = request.POST.get("resolution_note", "")
    try:
        lifecycle_service.dismiss_conflict(conflict, resolution_note=note)
        messages.success(request, f"Conflict {conflict.conflict_key} dismissed.")
    except RevisionNotEditableError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:conflicts_inbox", version=version)


def activate_confirm(request, version: int):
    revision = _get_revision(version)
    blockers = lifecycle_service.activation_blockers(revision)
    warnings = lifecycle_service.activation_warnings(revision)
    return render(
        request,
        "candidate_memory/activate_confirm.html",
        {"revision": revision, "blockers": blockers, "warnings": warnings},
    )


@require_POST
def activate(request, version: int):
    revision = _get_revision(version)
    try:
        lifecycle_service.activate_revision(revision)
        messages.success(request, f"CandidateMemory v{revision.version} is now ACTIVE.")
        return redirect("candidate_memory:revision_detail", version=version)
    except InvalidActivationError as exc:
        messages.error(request, str(exc))
        return redirect("candidate_memory:activate_confirm", version=version)


@require_POST
def abandon(request, version: int):
    """Explicit operator-controlled recovery path (audit repair): mark a stuck/unwanted
    BUILDING/NEEDS_REVIEW revision FAILED so a new build can start. Never touches ACTIVE."""
    revision = _get_revision(version)
    if revision.status not in (CandidateMemory.Status.BUILDING, CandidateMemory.Status.NEEDS_REVIEW):
        messages.error(request, f"CandidateMemory v{version} is {revision.status}; nothing to abandon.")
        return redirect("candidate_memory:revision_detail", version=version)
    reason = request.POST.get("reason", "")
    revision_service.abandon_revision(revision, reason=reason)
    messages.success(request, f"CandidateMemory v{version} marked FAILED.")
    return redirect("candidate_memory:overview")


def update_profile_form(request):
    if request.method == "POST":
        try:
            revision = build_revision_from_operator_text(
                text=request.POST.get("text", ""),
                context=request.POST.get("context", ""),
                employer=request.POST.get("employer", ""),
                experience_level=request.POST.get("experience_level", ""),
                dates=request.POST.get("dates", ""),
                actions=request.POST.get("actions", ""),
                results=request.POST.get("results", ""),
                metrics=request.POST.get("metrics", ""),
            )
        except ExistingWorkingRevisionError as exc:
            messages.error(request, str(exc))
            return redirect("candidate_memory:overview")
        messages.success(
            request,
            f"New CandidateMemory v{revision.version} created (NEEDS_REVIEW). Review claims and "
            "conflicts before activating.",
        )
        return redirect("candidate_memory:revision_detail", version=revision.version)
    return render(request, "candidate_memory/update_profile_form.html", {})


@require_POST
def snapshot_export(request):
    try:
        path = export_snapshot()
        messages.success(request, f"Snapshot regenerated at {path}.")
    except NoActiveRevisionError as exc:
        messages.error(request, str(exc))
    return redirect("candidate_memory:overview")
