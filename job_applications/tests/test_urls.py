from django.test import TestCase
from django.urls import resolve, reverse

from job_applications.views import home


class UrlRoutingTests(TestCase):
    def test_home_url_resolves_by_name(self):
        url = reverse("job_applications:home")
        self.assertEqual(url, "/")

    def test_root_path_resolves_to_home_view(self):
        match = resolve("/")
        self.assertEqual(match.func, home)

    def test_admin_url_resolves(self):
        url = reverse("admin:index")
        self.assertEqual(url, "/admin/")
