from __future__ import annotations

from django.urls import path

from . import views

app_name = "job_applications"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("<int:pk>/", views.detail_view, name="detail"),
]
