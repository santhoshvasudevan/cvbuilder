"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from job_applications.views import dashboard_view

urlpatterns = [
    # `/` renders the real dashboard directly (status 200), not a redirect to it -- the Product
    # Owner rejected a bare-redirect homepage as insufficient for the M7 UX follow-up. `/applications/`
    # (job_applications.urls) shares this exact same view/template, so the two can never disagree.
    path('', dashboard_view, name='home'),
    path('admin/', admin.site.urls),
    path('candidate-memory/', include('candidate_memory.urls')),
    path('job-intake/', include('job_intake.urls')),
    path('reviews/', include('reviews.urls')),
    path('resume/', include('resume_builder.urls')),
    path('applications/', include('job_applications.urls')),
]
