from __future__ import annotations

from django.urls import path

from . import views, views_m5, views_m6

app_name = "reviews"

urlpatterns = [
    path("gate1/<int:application_id>/", views.gate1_view, name="gate1"),
    path("gate2/<int:application_id>/", views.gate2_view, name="gate2"),
    # Operator-controlled, persistent, resumable M5 staged workflow (2026-09-08, D-041): three
    # individually-authorized pages, each requiring its own explicit "Run" click.
    path("m5/<int:application_id>/start/", views_m5.m5_start_view, name="m5_start"),
    path("m5/<int:application_id>/<int:run_id>/cancel/", views_m5.m5_cancel_view, name="m5_cancel"),
    path("m5/<int:application_id>/<int:run_id>/normalize/", views_m5.m5_normalize_view, name="m5_normalize"),
    path("m5/<int:application_id>/<int:run_id>/rank/", views_m5.m5_rank_view, name="m5_rank"),
    path("m5/<int:application_id>/<int:run_id>/match/", views_m5.m5_match_view, name="m5_match"),
    # Operator-controlled M6 (AB_BUILD) inspect/edit/approve review (2026-09-08, D-041).
    path("m6/<int:application_id>/start/", views_m6.m6_start_view, name="m6_start"),
    path("m6/<int:application_id>/<int:run_id>/", views_m6.m6_review_view, name="m6_review"),
]
