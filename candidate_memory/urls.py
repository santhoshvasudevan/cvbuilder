from django.urls import path

from . import views

app_name = "candidate_memory"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("update/", views.update_profile_form, name="update_profile_form"),
    path("snapshot/export/", views.snapshot_export, name="snapshot_export"),
    path("revisions/<int:version>/", views.revision_detail, name="revision_detail"),
    path("revisions/<int:version>/claims/", views.claims_list, name="claims_list"),
    path("revisions/<int:version>/claims/<str:claim_id>/", views.claim_detail, name="claim_detail"),
    path(
        "revisions/<int:version>/claims/<str:claim_id>/confirm/",
        views.claim_confirm,
        name="claim_confirm",
    ),
    path(
        "revisions/<int:version>/claims/<str:claim_id>/retire/", views.claim_retire, name="claim_retire"
    ),
    path(
        "revisions/<int:version>/claims/<str:claim_id>/restore/",
        views.claim_restore,
        name="claim_restore",
    ),
    path(
        "revisions/<int:version>/claims/<str:claim_id>/correct/",
        views.claim_correct,
        name="claim_correct",
    ),
    path("revisions/<int:version>/rules/", views.rules_list, name="rules_list"),
    path("revisions/<int:version>/conflicts/", views.conflicts_inbox, name="conflicts_inbox"),
    path(
        "revisions/<int:version>/conflicts/<int:conflict_id>/resolve/",
        views.conflict_resolve,
        name="conflict_resolve",
    ),
    path(
        "revisions/<int:version>/conflicts/<int:conflict_id>/dismiss/",
        views.conflict_dismiss,
        name="conflict_dismiss",
    ),
    path("revisions/<int:version>/activate/", views.activate_confirm, name="activate_confirm"),
    path("revisions/<int:version>/activate/confirm/", views.activate, name="activate"),
    path("revisions/<int:version>/abandon/", views.abandon, name="abandon"),
]
