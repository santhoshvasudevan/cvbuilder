from __future__ import annotations

from django.urls import path

from . import views

app_name = "job_applications"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("<int:pk>/", views.detail_view, name="detail"),
    path("<int:pk>/begin-revision/", views.begin_revision_view, name="begin_revision"),
]
