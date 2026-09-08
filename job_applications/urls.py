from django.urls import path

from . import views

app_name = "job_applications"

urlpatterns = [
    path("", views.home, name="home"),
]
