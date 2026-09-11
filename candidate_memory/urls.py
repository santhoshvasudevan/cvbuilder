from django.urls import path

from candidate_memory import views

app_name = "candidate_memory"

urlpatterns = [
    path(
        "candidate-memory/<int:memory_id>/",
        views.memory_overview,
        name="overview",
    ),
    path(
        "candidate-memory/<int:memory_id>/experience-slots/",
        views.experience_slot_workspace,
        name="experience_slots",
    ),
]
