from django.test import TestCase


class HomeViewTests(TestCase):
    def test_home_page_renders_200(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_home_page_uses_base_template(self):
        response = self.client.get("/")
        template_names = [t.name for t in response.templates if t.name]
        self.assertIn("home.html", template_names)
        self.assertIn("base.html", template_names)

    def test_home_page_contains_expected_content(self):
        response = self.client.get("/")
        self.assertContains(response, "CVBuilder V2")
