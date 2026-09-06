from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from job_applications.models import JobApplication

from .services.delivery import DraftNotDownloadableError, get_final_markdown


@require_GET
def preview_view(request, application_id: int):
    """Read-only rendered preview of the current v1 final deliverable, plus a copyable markdown
    source and a download link. Never mutates anything; never available unless the current draft
    is confirmed, current, and non-stale end to end (see `services/delivery.py`)."""
    application = get_object_or_404(JobApplication, pk=application_id)
    error = None
    final_markdown = None
    try:
        final_markdown = get_final_markdown(application)
    except DraftNotDownloadableError as exc:
        error = str(exc)

    return render(
        request,
        "resume_builder/preview.html",
        {"application": application, "final_markdown": final_markdown, "error": error},
    )


@require_GET
def download_view(request, application_id: int):
    """Serves the exact same immutable `rendered_markdown` the preview shows, as a `text/markdown`
    attachment with a deterministic, sanitized filename. A GET request, read-only -- repeated
    downloads are byte-identical and never mutate `ResumeDraft`."""
    application = get_object_or_404(JobApplication, pk=application_id)
    try:
        final_markdown = get_final_markdown(application)
    except DraftNotDownloadableError as exc:
        return HttpResponse(str(exc), status=409, content_type="text/plain; charset=utf-8")

    response = HttpResponse(final_markdown.content, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{final_markdown.filename}"'
    return response
