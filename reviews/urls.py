from __future__ import annotations

from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("gate1/<int:application_id>/", views.gate1_view, name="gate1"),
    path("gate2/<int:application_id>/", views.gate2_view, name="gate2"),
]
