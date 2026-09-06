from __future__ import annotations

from django.urls import path

from . import views

app_name = "resume_builder"

urlpatterns = [
    path("<int:application_id>/preview/", views.preview_view, name="preview"),
    path("<int:application_id>/download/", views.download_view, name="download"),
]
