from __future__ import annotations

from django.urls import path

from . import views

app_name = "job_intake"

urlpatterns = [
    path("", views.intake_view, name="intake"),
    path("<int:application_id>/", views.analysis_detail_view, name="analysis_detail"),
]
